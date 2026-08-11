"""文档上传加固（P1-3）：白名单、大小上限、分块流式、原子写、无绝对路径泄露。

设计约束：
- 纯服务层，不 import `src` / `server.db_manager`，可在单元测试中注入临时目录验证。
- 扩展名白名单统一由环境变量 `UPLOAD_ALLOWED_EXTENSIONS`（逗号分隔）配置，
  默认覆盖前端提示的常见文本/文档类型。
- 大小上限由 `MAX_UPLOAD_SIZE_MB` 配置，默认 100MB；超限立即删除临时文件并抛 413。
- 先写临时文件（同目录），写完后 `os.replace` 原子改名，避免半文件。
- 任何失败（写入中断/超限/空文件）都删除临时文件，不留下半成品。
- 只返回存储文件名（file_id）与大小，绝不返回服务器绝对路径。
- 后续解析只接受裸 file_id（单层存储文件名）：绝对路径、盘符、UNC、
  目录、`..`、路径分隔符、上传目录外软链接一律拒绝（P1-1）。
"""

import hashlib
import os
import re
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

# 分块读取大小：1 MiB，保证大文件不会一次性进入内存
CHUNK_SIZE = 1024 * 1024
DEFAULT_MAX_SIZE_MB = 100

# 默认扩展名白名单（覆盖前端上传提示 .pdf/.txt/.md/.html/.json/.csv 及常见文档）
_DEFAULT_ALLOWED_EXTENSIONS = (
    ".pdf,.txt,.md,.html,.htm,.json,.csv,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.yaml,.yml,.xml"
)

# 存储文件名中保留的字符集，其余一律替换为 _
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]")


class UploadError(Exception):
    """上传业务错误。status_code 对应当前上传场景的 HTTP 状态码。"""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def allowed_extensions() -> set[str]:
    """读取环境变量配置的扩展名白名单（统一入口，P2-5 会写入 .env.example）。

    空值/空白视为“使用默认白名单”——Docker Compose 用 ${VAR:-} 透传时，
    未设置的环境变量会变成空字符串，不能被当成“空白名单（全部拒绝）”。
    """
    raw = os.getenv("UPLOAD_ALLOWED_EXTENSIONS", _DEFAULT_ALLOWED_EXTENSIONS)
    if not raw or not str(raw).strip():
        raw = _DEFAULT_ALLOWED_EXTENSIONS
    exts = set()
    for part in str(raw).split(","):
        part = part.strip().lower()
        if not part:
            continue
        if not part.startswith("."):
            part = "." + part
        exts.add(part)
    return exts


def max_upload_bytes() -> int:
    """读取环境变量配置的大小上限（MB → 字节）；非法/非正数回退默认 100MB。"""
    try:
        mb = float(os.getenv("MAX_UPLOAD_SIZE_MB", str(DEFAULT_MAX_SIZE_MB)))
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_SIZE_MB
    if not (mb > 0):
        mb = DEFAULT_MAX_SIZE_MB
    return int(mb * 1024 * 1024)


def _short_hash(seed: str, length: int = 6) -> str:
    """md5 + 时间盐，生成存储名的短哈希片段（行为与 src.utils.hashstr 一致）。"""
    salted = f"{seed}|{time.time()}".encode("utf-8")
    return hashlib.md5(salted).hexdigest()[:length]


def _check_original_filename(filename) -> str:
    """校验原始文件名：非空、纯裸文件名（无路径分隔符 / 遍历）。"""
    name = filename if isinstance(filename, str) else ""
    name = name.strip()
    if not name:
        raise UploadError("未选择文件", 400)
    if "/" in name or "\\" in name or ".." in name or name in (".", ".."):
        raise UploadError("文件名不合法，禁止包含路径", 400)
    return name


def build_stored_filename(original: str) -> str:
    """把原始文件名转成服务器端存储名（<安全stem>_<短哈希><ext>，小写）。"""
    name = _check_original_filename(original)
    ext = Path(name).suffix.lower()
    if not ext:
        raise UploadError("文件缺少扩展名", 415)
    if ext not in allowed_extensions():
        raise UploadError(f"不支持的文件类型：{ext}", 415)
    stem = _SAFE_STEM.sub("_", Path(name).stem)
    stem = stem.strip("._") or "file"
    stored = f"{stem}_{_short_hash(name)}{ext}".lower()
    return stored, ext


async def save_upload_stream_async(file, upload_dir):
    """分块流式保存上传文件，边写边计数，超限/失败立即清理临时文件。

    返回 (stored_filename, size_bytes)。file 只需提供 filename 与 async read(size)。
    """
    original = getattr(file, "filename", "") or ""
    stored, ext = build_stored_filename(original)
    upload_dir = Path(upload_dir)
    os.makedirs(upload_dir, exist_ok=True)

    limit = max_upload_bytes()
    tmp = upload_dir / f".{stored}.upload.{os.getpid()}.{os.urandom(4).hex()}.part"
    size = 0
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise UploadError(
                        f"上传文件超过大小限制（最大 {limit // (1024 * 1024)}MB）",
                        413,
                    )
                out.write(chunk)
        if size == 0:
            raise UploadError("上传文件为空", 400)
        os.replace(tmp, upload_dir / stored)
    except UploadError:
        _remove_quiet(tmp)
        raise
    except Exception:
        _remove_quiet(tmp)
        raise UploadError("写入上传文件失败", 500)
    return stored, size


def _remove_quiet(path):
    try:
        os.remove(path)
    except OSError:
        pass


_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_UNC_RE = re.compile(r"^(\\\\|//)")


def _validate_bare_filename(name: str) -> str:
    """裸文件名校验：只接受单层存储文件名（file_id）。

    拒绝绝对路径（POSIX/Windows）、盘符、UNC、目录、`..`、路径分隔符。
    返回去空白后的裸文件名；任何越界输入都抛 400。
    """
    if not isinstance(name, str) or not name.strip():
        raise UploadError("文件名为空", 400)
    name = name.strip()
    if PurePosixPath(name).is_absolute() or PureWindowsPath(name).is_absolute():
        raise UploadError("文件名不合法：禁止绝对路径", 400)
    if _WIN_DRIVE_RE.match(name) or _UNC_RE.match(name):
        raise UploadError("文件名不合法：禁止盘符/UNC 路径", 400)
    p = Path(name)
    if len(p.parts) != 1:
        raise UploadError("文件名不合法：禁止路径分隔符", 400)
    if p.name in (".", "..") or ".." in name:
        raise UploadError("文件名不合法：禁止目录穿越", 400)
    return name


def _resolve_bare_in(root, name):
    """在受控根目录内解析裸文件名。

    越界（含软链接逃逸）、目标为目录、软链接一律抛 400；文件不存在返回 None。
    """
    base = Path(root).resolve()
    candidate = base / name
    if candidate.is_symlink():
        raise UploadError("不允许通过软链接引用上传文件", 400)
    real = candidate.resolve()
    if real.is_dir():
        raise UploadError(f"目标是目录，不是文件：{name}", 400)
    if not real.is_relative_to(base):
        raise UploadError(f"文件越出受控上传目录：{name}", 400)
    if not real.is_file():
        return None
    return str(real)


def resolve_upload_path(upload_dir, name: str, general_dir=None) -> str:
    """把前端提交的 file_id（裸存储名）解析为受控目录内的真实磁盘路径。

    - 只接受裸 file_id；绝对路径、盘符、UNC、目录、`..`、路径分隔符一律 400。
    - 优先在 upload_dir（当前知识库上传目录）解析；其次兼容通用上传目录 general_dir。
    - 解析后的路径必须位于任一受控根目录内，且为常规文件（软链接/目录拒绝）。
    - 两个受控目录都不存在该文件时返回 404。
    """
    name = _validate_bare_filename(name)
    roots = []
    for root in (upload_dir, general_dir):
        if root and root not in roots:
            roots.append(root)
    for root in roots:
        got = _resolve_bare_in(root, name)
        if got is not None:
            return got
    raise UploadError(f"上传文件不存在：{name}", 404)


def resolve_upload_paths(upload_dir, names, general_dir=None) -> list[str]:
    """批量解析；任一非法即整体失败（与 file-to-chunk 的整批语义一致）。"""
    if not isinstance(names, (list, tuple)):
        raise UploadError("文件列表必须是数组", 400)
    return [resolve_upload_path(upload_dir, n, general_dir=general_dir) for n in names]
