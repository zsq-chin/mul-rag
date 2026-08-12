"""远端图片目录：服务端分页 + 轻量元数据 + 缩略图生命周期（阶段 D1 远端实现）。

生产上线要求 D1（CLAUDE_PRODUCTION_RELEASE_MODIFICATION_REQUIREMENTS.md §5）：
`/kb/images` 必须在数据源所在进程（远端后端）完成分页与稳定排序，网络与浏览器
只拿到当前页；SAGE 不得再对全量列表本地切片（“假分页”）。

本模块只依赖标准库；Pillow 在生成缩略图时惰性导入，因此可以脱离 FastAPI 与重型
OCR/向量依赖，直接在 SAGE 的 unittest 中单测。目录布局与 app.py 保持一致：
    <DATA_ROOT>/<kbId>/files/<fileId>/{images, thumbs, image_summaries.json}

约定：
- 列表条目只含轻量元数据（img_name / fileName / fileId / page_num / summary 预览），
  不含原图 Base64、完整 OCR/全文、Markdown、向量或内部绝对路径；
- 分页响应固定为 `items/page/pageSize/total`，pageSize 最大 100、默认 24；
- 排序稳定：fileName → page_num → img_name，翻页不重复、不遗漏；
- 缩略图按需生成 320px 宽 JPEG 并缓存到 `thumbs/`；源图更新（mtime 前进）即重新
  生成，ETag 基于“被服务文件”的 stat 指纹，源更新后缓存键必然变化。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import unquote

# 与 app.py 相同的默认数据根：mul_rag/backend/data
DATA_ROOT_DEFAULT = Path(__file__).resolve().parent.parent / "data"

SUMMARY_PREVIEW_CHARS = 160  # 列表条目 summary 预览长度（轻量元数据）
THUMB_MAX_WIDTH = 320        # 缩略图目标宽度（px）
THUMB_SUBDIR = "thumbs"      # 缩略图缓存子目录名
PAGE_SIZE_DEFAULT = 24
PAGE_SIZE_MAX = 100

_SAFE_IMG_RE = re.compile(r"^[^\\/]+$")  # 图片名不允许任何路径分隔符


def coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """把查询参数安全地转换为受限整数，非法值回落默认。"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def file_display_name(file_workdir: Path, file_id: str) -> str:
    """从文件工作目录中取主文件显示名；找不到则回落 file_id。"""
    for pattern in ("*.pdf", "*.xlsx", "*.xls", "*.csv"):
        matches = sorted(file_workdir.glob(pattern))
        if matches:
            return matches[0].name
    return file_id


def image_summary_preview(summary: Any, limit: int = SUMMARY_PREVIEW_CHARS) -> str:
    """截断 summary 到轻量预览长度，避免列表传输大段 OCR/全文。"""
    text = str(summary or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _read_json_quietly(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _parse_page_from_name(img_name: str) -> int:
    """从 `page1_img2.png` 这类命名解析页码，失败回落 0。"""
    match = re.search(r"page(\d+)", img_name)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def file_image_items(data_root: Path, kb_id: str, file_id: str, file_workdir: Path) -> List[Dict[str, Any]]:
    """读取单个文件下的图片目录条目（轻量元数据）。

    优先读 image_summaries.json；缺失时扫描 images/*.png 结合 image_captions.json
    构建基础列表。返回条目固定为：
        {img_name, summary(预览), page_num}，另保留 source_page_num/original_img_name
    若远端摘要中存在（可选字段，用于缩略图/翻页稳定性）。
    """
    summaries = _read_json_quietly(file_workdir / "image_summaries.json")
    if isinstance(summaries, list):
        items: List[Dict[str, Any]] = []
        for raw in summaries:
            if not isinstance(raw, dict):
                continue
            img_name = str(raw.get("img_name") or raw.get("name") or "").strip()
            if not img_name:
                continue
            items.append(
                {
                    "img_name": img_name,
                    "summary": image_summary_preview(raw.get("summary")),
                    "page_num": raw.get("page_num", 0),
                    "source_page_num": raw.get("source_page_num"),
                    "original_img_name": str(raw.get("original_img_name") or ""),
                }
            )
        return items

    img_dir = file_workdir / "images"
    if not img_dir.is_dir():
        return []
    captions = _read_json_quietly(file_workdir / "image_captions.json")
    if not isinstance(captions, dict):
        captions = {}
    items = []
    for img_file in sorted(img_dir.glob("*.png")):
        name = img_file.name
        items.append(
            {
                "img_name": name,
                "summary": image_summary_preview(captions.get(name, "(未生成详细摘要)")),
                "page_num": _parse_page_from_name(name),
                "source_page_num": None,
                "original_img_name": "",
            }
        )
    return items


def iter_kb_image_items(data_root: Path, kb_id: str) -> Iterator[Dict[str, Any]]:
    """枚举知识库下全部文件的图片目录条目，注入 fileId/fileName。"""
    safe_kb = safe_kb_id(kb_id)
    if safe_kb is None:
        return
    files_root = data_root / safe_kb / "files"
    if not files_root.is_dir():
        return
    for file_workdir in sorted(files_root.iterdir()):
        if not file_workdir.is_dir():
            continue
        file_id = file_workdir.name
        display = file_display_name(file_workdir, file_id)
        for item in file_image_items(data_root, kb_id, file_id, file_workdir):
            item["fileId"] = file_id
            item["fileName"] = display
            yield item


def _catalog_sort_key(item: Dict[str, Any]) -> Tuple[str, int, str]:
    return (
        str(item.get("fileName") or ""),
        coerce_int(item.get("page_num"), 0, 0, 10**9),
        str(item.get("img_name") or ""),
    )


def paginate_kb_images(
    data_root: Path,
    kb_id: str,
    page: Any = 1,
    page_size: Any = PAGE_SIZE_DEFAULT,
) -> Dict[str, Any]:
    """数据源层分页：稳定排序后只返回当前页，响应固定 items/page/pageSize/total。"""
    safe_page = coerce_int(page, 1, 1, 10**9)
    safe_page_size = coerce_int(page_size, PAGE_SIZE_DEFAULT, 1, PAGE_SIZE_MAX)

    items = list(iter_kb_image_items(data_root, kb_id))
    items.sort(key=_catalog_sort_key)
    total = len(items)
    start = (safe_page - 1) * safe_page_size
    current = items[start : start + safe_page_size]
    return {
        "items": current,
        "page": safe_page,
        "pageSize": safe_page_size,
        "total": total,
    }


def safe_kb_id(kb_id: Any) -> Optional[str]:
    """知识库 ID 必须是不含路径分隔符的安全目录名，拒绝穿越/盘符/UNC/NUL。"""
    if kb_id is None:
        return None
    raw = unquote(str(kb_id).strip()).replace("\\", "/")
    if not raw or "\x00" in raw or raw in (".", ".."):
        return None
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    if "/" in raw:
        return None
    return raw


def _safe_image_name(image_path: Any) -> Optional[str]:
    """图片名必须是不含分隔符的安全文件名；拒绝路径、绝对路径、穿越、NUL。

    先做 URL 解码（%2F → /），与 SAGE 侧 normalize_multimodal_image_path 一致，
    防止 `..%2Fsecret.png` 这类编码穿越在拼路径时被当作分隔符。
    """
    if image_path is None:
        return None
    raw = str(image_path).strip()
    if not raw or "\x00" in raw or raw in (".", ".."):
        return None
    decoded = unquote(raw)
    if "\x00" in decoded:
        return None
    decoded = decoded.replace("\\", "/")
    if decoded.startswith("/") or re.match(r"^[A-Za-z]:", decoded):
        return None
    if not _SAFE_IMG_RE.match(decoded):
        return None
    return decoded


def source_image_path(data_root: Path, kb_id: str, file_id: str, image_path: Any) -> Optional[Path]:
    """解析并校验源图绝对路径；不安全返回 None，绝不把内部绝对路径传给外部。"""
    safe_kb = safe_kb_id(kb_id)
    name = _safe_image_name(image_path)
    safe_file = _safe_image_name(file_id)
    if safe_kb is None or name is None or safe_file is None:
        return None
    return data_root / safe_kb / "files" / safe_file / "images" / name


def thumb_dir(data_root: Path, kb_id: str, file_id: str) -> Path:
    safe_kb = safe_kb_id(kb_id)
    safe_file = _safe_image_name(file_id)
    if safe_kb is None or safe_file is None:
        return data_root / "__invalid__" / "thumbs"
    return data_root / safe_kb / "files" / safe_file / THUMB_SUBDIR


def make_image_etag(path: Path, *, thumb: bool = False) -> str:
    """基于被服务文件 stat 指纹（mtime_ns:size）的强 ETag。

    源图更新 → 缩略图重新生成（新 mtime/size）→ ETag 变化，满足 D1.5“原图更新后
    缓存键必须变化”。文件缺失时返回 `""`（空校验器）。
    """
    try:
        st = path.stat()
    except OSError:
        return '""'
    digest = str(st.st_mtime_ns) + ":" + str(st.st_size)
    tag = "thumb:" if thumb else "orig:"
    return f'"{tag}{digest}"'


def ensure_thumbnail(
    data_root: Path, kb_id: str, file_id: str, image_path: Any
) -> Optional[Path]:
    """按需生成并缓存 320px 宽缩略图（Pillow 惰性导入）。

    源图 mtime 不晚于缩略图且缓存非空时直接复用；否则重新生成。生成失败返回
    None（由路由回落原图或 404），绝不把损坏的中间态当缩略图返回。
    """
    source = source_image_path(data_root, kb_id, file_id, image_path)
    if source is None or not source.is_file():
        return None
    try:
        src_st = source.stat()
    except OSError:
        return None
    tdir = thumb_dir(data_root, kb_id, file_id)
    tdir.mkdir(parents=True, exist_ok=True)
    thumb = tdir / (source.stem + ".jpg")

    if thumb.is_file():
        try:
            if thumb.stat().st_size > 0 and thumb.stat().st_mtime_ns >= src_st.st_mtime_ns:
                return thumb
        except OSError:
            pass

    try:
        from PIL import Image, ImageOps

        with Image.open(source) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((THUMB_MAX_WIDTH, THUMB_MAX_WIDTH))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(thumb, "JPEG", quality=82)
    except Exception:
        return None
    return thumb


def resolve_image_for_serve(
    data_root: Path, kb_id: str, file_id: str, image_path: Any, thumb: bool = False
) -> Tuple[Optional[Path], bool]:
    """解析应服务的图片文件：缩略图优先（生成失败回落原图），否则源图。

    返回 (path, is_thumbnail)；不安全/不存在时 (None, False)。
    """
    source = source_image_path(data_root, kb_id, file_id, image_path)
    if source is None or not source.is_file():
        return None, False
    if thumb:
        cached = ensure_thumbnail(data_root, kb_id, file_id, image_path)
        if cached is not None:
            return cached, True
    return source, False
