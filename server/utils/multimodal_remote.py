import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit

import requests

try:
    from src.utils.logging_config import logger
except Exception:  # 隔离测试环境不加载 src 时回退标准日志
    logger = logging.getLogger(__name__)


MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((?:\.?/)?images/([^)]+)\)")
MULTIMODAL_PROXY_BLOCKED_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "cookie",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class MultimodalConfigError(Exception):
    """多模态远端配置错误（Base URL 缺失、格式非法或安全校验不通过）。"""


def _multimodal_allow_http() -> bool:
    return os.getenv("MULTIMODAL_ALLOW_HTTP", "").strip().lower() in {"1", "true", "yes", "on"}


def _validate_multimodal_base_url(raw: str) -> str:
    """校验并规范化远端 Base URL（B1.3/B1.4）。

    要求：
    - scheme 只能是 https（默认）或显式放行 MULTIMODAL_ALLOW_HTTP=1 的 http；
    - host 非空、不含用户名/密码与控制字符；
    - port 合法（1..65535）；
    - path 必须以 /api/v1 前缀开头；
    - 不允许 query / fragment。
    校验失败抛 MultimodalConfigError，由启动/调用方明确报错。
    """
    value = str(raw or "").strip().rstrip("/")
    if not value:
        raise MultimodalConfigError("多模态远端 Base URL 为空")
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise MultimodalConfigError("多模态远端 Base URL 含控制字符")

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise MultimodalConfigError(f"多模态远端 Base URL 解析失败: {exc}") from exc

    scheme = (parsed.scheme or "").lower()
    if scheme == "https":
        pass
    elif scheme == "http":
        if not _multimodal_allow_http():
            raise MultimodalConfigError(
                "多模态远端 Base URL 使用 http 但未显式设置 MULTIMODAL_ALLOW_HTTP=1；"
                "内网阶段允许 http 必须同时满足固定内网地址、防火墙白名单和服务间认证"
            )
    else:
        raise MultimodalConfigError(f"多模态远端 Base URL scheme 非法: {scheme or '(空)'}")

    if "@" in parsed.netloc:
        raise MultimodalConfigError("多模态远端 Base URL 不允许内嵌用户名/密码")
    if not parsed.hostname:
        raise MultimodalConfigError("多模态远端 Base URL 缺少固定 host")
    if parsed.query or parsed.fragment:
        raise MultimodalConfigError("多模态远端 Base URL 不允许携带 query 或 fragment")

    try:
        port = parsed.port
    except ValueError as exc:
        raise MultimodalConfigError(f"多模态远端 Base URL 端口非法: {exc}") from exc
    if port is not None and not (1 <= port <= 65535):
        raise MultimodalConfigError("多模态远端 Base URL 端口超出 1..65535")

    path = parsed.path or ""
    if not (path == "/api/v1" or path.startswith("/api/v1/")):
        raise MultimodalConfigError("多模态远端 Base URL 必须以 /api/v1 前缀开头")

    return value


def get_multimodal_api_base() -> str | None:
    """返回经校验的远端 Base URL，仅来自服务端环境配置。

    - 浏览器/聊天 meta 无法覆盖（消除 SSRF 与客户端配置注入）；
    - 未配置时返回 None（多模态未启用）；
    - 配置非法时抛 MultimodalConfigError。
    """
    raw = (
        os.getenv("MULTIMODAL_REMOTE_BASE_URL")
        or os.getenv("MULTIMODAL_KB_API_BASE")
        or ""
    ).strip()
    if not raw:
        return None
    return _validate_multimodal_base_url(raw)


def get_multimodal_service_token() -> str | None:
    """远端服务间认证 Token（B2.2）：仅由服务端环境注入，绝不由浏览器提供。"""
    return os.getenv("MULTIMODAL_SERVICE_TOKEN") or None


def new_multimodal_trace_id() -> str:
    """为检索 / 图片 / 管理操作生成统一 trace ID（B2.5）。"""
    return uuid.uuid4().hex


def build_service_auth_headers(trace_id: str | None = None) -> dict[str, str]:
    """生成发往远端的服务间认证与 trace 透传头（B2.2/B2.5）。

    返回的头只含服务端注入的 Token 与 trace ID，绝不包含浏览器
    Authorization / Cookie。未配置 Token 时只带 trace 头。
    """
    headers: dict[str, str] = {}
    token = get_multimodal_service_token()
    if token:
        header_name = os.getenv("MULTIMODAL_SERVICE_TOKEN_HEADER", "Authorization").strip() or "Authorization"
        if header_name.lower() == "authorization":
            headers["Authorization"] = f"Bearer {token}"
        else:
            headers[header_name] = token
    if trace_id:
        headers["X-Sage-Trace-Id"] = trace_id
    return headers


def format_redacted_upstream_error(
    trace_id: str,
    endpoint: str,
    status: int | None,
    duration_ms: float,
    exc_type: str,
) -> str:
    """上游错误日志（B2.4）：只记录 trace / 接口 / 状态码 / 耗时 / 异常类型。

    绝不记录请求正文、查询全文、Cookie、Token、API Key 或远端响应全文。
    """
    status_part = f" status={status}" if status is not None else ""
    return (
        f"multimodal upstream error trace={trace_id} endpoint={endpoint}"
        f"{status_part} duration_ms={duration_ms:.1f} type={exc_type}"
    )


def build_multimodal_remote_url(path: str, base_url: str | None = None) -> str:
    remote_path = str(path or "").strip()
    parsed = urlsplit(remote_path)
    if not remote_path:
        raise ValueError("empty multimodal proxy path")
    if parsed.scheme or parsed.netloc:
        raise ValueError("absolute multimodal proxy paths are not allowed")

    normalized_path = remote_path.lstrip("/")
    if any(part == ".." for part in normalized_path.split("/")):
        raise ValueError("parent path segments are not allowed")

    resolved_base = base_url or get_multimodal_api_base()
    if not resolved_base:
        raise MultimodalConfigError("多模态远端 Base URL 未配置")
    return f"{resolved_base.rstrip('/')}/{normalized_path}"


def filter_multimodal_proxy_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in MULTIMODAL_PROXY_BLOCKED_HEADERS
    }


def pick_first_kb_id(payload: dict[str, Any]) -> str | None:
    raw_kbs = payload.get("kbs") or payload.get("data") or payload.get("results") or []
    if not isinstance(raw_kbs, list):
        return None

    for item in raw_kbs:
        if not isinstance(item, dict):
            continue
        kb_id = item.get("kbId") or item.get("id") or item.get("kb_id")
        if kb_id:
            return str(kb_id).strip()
    return None


def normalize_multimodal_kbs(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []

    raw_kbs = payload.get("kbs") or payload.get("data") or payload.get("results") or []
    if not isinstance(raw_kbs, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in raw_kbs:
        if not isinstance(item, dict):
            continue

        kb_id = item.get("kbId") or item.get("id") or item.get("kb_id")
        if not kb_id:
            continue

        kb_name = item.get("kbName") or item.get("name") or item.get("kb_name") or kb_id
        normalized.append(
            {
                "kbId": str(kb_id).strip(),
                "kbName": str(kb_name).strip(),
                "fileCount": item.get("fileCount") or item.get("file_count") or 0,
                "vectorStoreType": item.get("vectorStoreType") or item.get("vector_store_type") or "",
                "embedModel": item.get("embedModel") or item.get("embed_model") or "",
            }
        )

    return normalized


def _parse_source(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _extract_raw_results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("results", "data", "citations"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_raw_results(value)
            if nested:
                return nested
    return []


def _first_text(item: dict[str, Any]) -> str:
    for key in ("text", "content", "chunk_text", "snippet"):
        value = item.get(key)
        if value:
            return str(value).strip()
    return ""


def _image_proxy_url(kb_id: str, file_id: str, image_path: str) -> str:
    query = urlencode({"kbId": kb_id, "fileId": file_id, "imagePath": image_path})
    return f"/api/chat/multimodal/image?{query}"


def _normalize_image_path(value: Any) -> str | None:
    raw_path = str(value or "").strip()
    if not raw_path or "\x00" in raw_path:
        return None

    decoded_path = unquote(raw_path).replace("\\", "/")
    if "\x00" in decoded_path:
        return None
    # URL-encode '#' before parsing so filenames like "fig#1.png" are not
    # misinterpreted as having a URL fragment by urlsplit.
    parsed = urlsplit(decoded_path.replace("#", "%23"))
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None
    if decoded_path.startswith("/") or re.match(r"^[A-Za-z]:", decoded_path):
        return None

    parts = [part for part in decoded_path.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None

    return parts[-1]


def normalize_multimodal_image_path(value: Any) -> str | None:
    return _normalize_image_path(value)


def _iter_image_candidates(value: Any, inherited_alt: str = ""):
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_image_candidates(item, inherited_alt)
        return

    if isinstance(value, dict):
        alt = str(value.get("alt") or value.get("caption") or value.get("label") or inherited_alt or "")
        for key in ("image_path", "imagePath", "img_name", "path", "name"):
            if value.get(key):
                yield value[key], alt
                return
        for key in ("images", "referenced_images"):
            if value.get(key):
                yield from _iter_image_candidates(value[key], alt)
        return

    if isinstance(value, str) and value.strip():
        yield value, inherited_alt


def _extract_images(
    item: dict[str, Any],
    text: str,
    kb_id: str | None,
    file_id: str | None,
    source_meta: dict[str, Any],
) -> list[dict[str, str]]:
    if not kb_id or not file_id:
        return []

    images: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_image(path: Any, alt: str = ""):
        image_path = _normalize_image_path(path)
        if not image_path or image_path in seen:
            return
        seen.add(image_path)
        images.append(
            {
                "name": image_path.split("/")[-1],
                "path": image_path,
                "alt": alt.strip(),
                "url": _image_proxy_url(kb_id, file_id, image_path),
            }
        )

    for match in MARKDOWN_IMAGE_RE.finditer(text or ""):
        add_image(match.group(2), match.group(1))

    for container in (source_meta, item):
        default_alt = str(container.get("caption") or container.get("alt") or "")
        for key in ("image_path", "imagePath", "img_name", "images", "referenced_images"):
            for path, alt in _iter_image_candidates(container.get(key), default_alt):
                add_image(path, alt)

    return images


def normalize_multimodal_image_page(payload: Any, page: int = 1, page_size: int = 24) -> dict[str, Any]:
    safe_page = max(1, int(page or 1))
    safe_page_size = min(100, max(1, int(page_size or 24)))

    containers = [payload]
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        containers.append(payload["data"])

    items: list[Any] = []
    metadata: dict[str, Any] = {}
    for container in containers:
        if isinstance(container, list):
            items = container
            break
        if not isinstance(container, dict):
            continue
        for key in ("items", "images", "results", "data"):
            value = container.get(key)
            if isinstance(value, list):
                items = value
                metadata = container
                break
        if items or metadata:
            break

    is_remote_page = bool(
        metadata
        and "total" in metadata
        and any(key in metadata for key in ("page", "pageSize", "page_size"))
    )
    if is_remote_page:
        total = max(0, int(metadata.get("total") or 0))
        remote_page = max(1, int(metadata.get("page") or safe_page))
        remote_page_size = min(
            100,
            max(1, int(metadata.get("pageSize") or metadata.get("page_size") or safe_page_size)),
        )
        if len(items) > remote_page_size:
            if len(items) == total:
                start = (remote_page - 1) * remote_page_size
                items = items[start : start + remote_page_size]
            else:
                items = items[:remote_page_size]
        return {
            "items": items,
            "page": remote_page,
            "pageSize": remote_page_size,
            "total": total,
        }

    total = len(items)
    start = (safe_page - 1) * safe_page_size
    return {
        "items": items[start : start + safe_page_size],
        "page": safe_page,
        "pageSize": safe_page_size,
        "total": total,
    }


def normalize_multimodal_results(payload: Any, kb_id: str | None = None) -> list[dict[str, Any]]:
    normalized = []
    for index, item in enumerate(_extract_raw_results(payload), start=1):
        source_meta = _parse_source(item.get("source") or item.get("metadata"))
        text = _first_text(item)
        file_id = item.get("fileId") or item.get("file_id") or source_meta.get("file_id") or item.get("entity_key")
        file_name = (
            item.get("fileName")
            or item.get("file_name")
            or source_meta.get("fileName")
            or source_meta.get("file_name")
            or source_meta.get("filename")
        )
        page = item.get("page") or item.get("page_number") or source_meta.get("page") or source_meta.get("page_number")

        images = _extract_images(item, text, kb_id, file_id, source_meta)
        remote_content_type = (
            item.get("contentType")
            or item.get("content_type")
            or item.get("type")
            or source_meta.get("type")
        )
        content_type = (
            "table"
            if re.search(r"<table\b", text, re.IGNORECASE)
            else str(remote_content_type or ("image" if images and not text else "text"))
        )

        normalized.append(
            {
                "id": item.get("id") or item.get("citation_id") or index,
                "rank": item.get("rank") or index,
                "fileId": file_id,
                "fileName": file_name or file_id or "unknown",
                "page": page,
                "score": item.get("score"),
                "source": item.get("source"),
                "metadata": source_meta,
                "previewUrl": item.get("previewUrl") or item.get("preview_url"),
                "contentType": content_type,
                "images": images,
                "text": text,
            }
        )
    return normalized


def format_multimodal_context(results: list[dict[str, Any]], max_items: int = 5, max_chars: int = 6000) -> str:
    if not results:
        return ""

    parts = ["多模态知识库检索结果:"]
    used_chars = len(parts[0])

    for index, item in enumerate(results[:max_items], start=1):
        text = str(item.get("text") or "").strip()
        if not text:
            continue

        source_name = item.get("fileName") or item.get("fileId") or "unknown"
        page_text = f" p.{item.get('page')}" if item.get("page") else ""
        score = item.get("score")
        score_text = f" score={score:.2f}" if isinstance(score, (int, float)) else ""
        header = f"[{index}] {source_name}{page_text}{score_text}"
        remaining = max_chars - used_chars - len(header) - 2
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[: max(0, remaining - 3)] + "..."
        entry = f"{header}\n{text}"
        parts.append(entry)
        used_chars += len(entry)

    return "\n\n".join(parts) if len(parts) > 1 else ""


def _safe_identifier(value: Any, field: str) -> str | None:
    """校验 kbId / fileId 等远端标识符（B1.6 / D2.5）。

    拒绝空值、超长、控制字符、URL 形态、绝对路径/盘符/UNC 与父路径穿越。
    """
    text = str(value or "").strip()
    if not text or len(text) > 256:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        return None
    if "://" in text:
        return None
    if text.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", text):
        return None
    if text.startswith("\\\\") or text.startswith("//"):
        return None
    for separator in ("/", "\\"):
        if ".." in text.split(separator):
            return None
    return text


def _clamp_top_k(value: Any) -> int:
    """top_k 限制在 1..20（B1.6）。非法值回落默认 5。"""
    try:
        k = int(value)
    except (TypeError, ValueError):
        return 5
    if k < 1:
        return 1
    return min(k, 20)


def _coerce_timeout(value: Any) -> float:
    """远端检索超时只来自服务端环境配置，限制在 [5, 30] 秒（J3.3）。"""
    try:
        t = float(value)
    except (TypeError, ValueError):
        return 30.0
    if t != t or t in (float("inf"), float("-inf")) or t <= 0:
        return 30.0
    return min(max(t, 5.0), 30.0)


def _resolve_kb_id(
    base_url: str, meta: dict[str, Any], timeout: float, headers: dict[str, str]
) -> str | None:
    kb_id = (
        meta.get("multimodal_kb_id")
        or os.getenv("MULTIMODAL_KB_DEFAULT_KB_ID")
        or os.getenv("MULTIMODAL_REMOTE_DEFAULT_KB_ID")
    )
    if kb_id:
        return _safe_identifier(kb_id, "kbId")

    response = requests.get(f"{base_url}/kb/list", timeout=timeout, headers=headers)
    response.raise_for_status()
    return pick_first_kb_id(response.json())


def search_multimodal_remote(query: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = meta or {}
    base_url = get_multimodal_api_base()
    if not base_url:
        return {
            "results": [],
            "message": "多模态知识库未配置（服务端未设置 MULTIMODAL_REMOTE_BASE_URL / MULTIMODAL_KB_API_BASE）",
            "kb_id": None,
            "kb_name": None,
            "file_id": None,
            "status": "disabled",
        }

    trace_id = new_multimodal_trace_id()
    headers = build_service_auth_headers(trace_id)

    # 连接/读取超时与 top_k 只允许来自服务端配置；meta 只能提供经过校验的检索选择
    timeout = _coerce_timeout(os.getenv("MULTIMODAL_KB_TIMEOUT") or os.getenv("MULTIMODAL_HTTP_READ_TIMEOUT"))
    top_k = _clamp_top_k(meta.get("multimodal_top_k") or os.getenv("MULTIMODAL_KB_TOP_K"))

    kb_id = None
    kb_name = meta.get("multimodal_kb_name")
    try:
        kb_id = _resolve_kb_id(base_url, meta, timeout, headers)
    except requests.RequestException as exc:
        logger.error(format_redacted_upstream_error(trace_id, "kb/list", None, 0.0, type(exc).__name__))
        return {
            "results": [],
            "message": "远端多模态知识库列表不可用（可重试）",
            "kb_id": None,
            "kb_name": kb_name,
            "file_id": None,
            "status": "error",
            "trace_id": trace_id,
        }
    if kb_id is None:
        kb_name = kb_name or kb_id

    if not kb_id:
        return {
            "results": [],
            "message": "未配置多模态知识库 kbId，且远程 /kb/list 没有可用知识库",
            "kb_id": None,
            "kb_name": kb_name,
            "file_id": None,
            "status": "error",
            "trace_id": trace_id,
        }

    query_text = str(query or "").strip()
    if not query_text:
        return {
            "results": [],
            "message": "检索 query 为空",
            "kb_id": kb_id,
            "kb_name": kb_name,
            "file_id": None,
            "status": "error",
            "trace_id": trace_id,
        }
    if len(query_text) > 5000:
        query_text = query_text[:5000]

    body: dict[str, Any] = {"kbId": kb_id, "query": query_text, "k": top_k}
    file_id = _safe_identifier(meta.get("multimodal_file_id"), "fileId")
    if file_id:
        body["fileId"] = file_id

    t0 = time.monotonic()
    try:
        response = requests.post(
            f"{base_url}/index/search", json=body, timeout=timeout, headers=headers
        )
    except requests.RequestException as exc:
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        logger.error(format_redacted_upstream_error(trace_id, "index/search", None, elapsed_ms, type(exc).__name__))
        return {
            "results": [],
            "message": "远端多模态检索失败（已记录，可重试）",
            "kb_id": kb_id,
            "kb_name": kb_name,
            "file_id": file_id,
            "status": "error",
            "trace_id": trace_id,
        }
    elapsed_ms = (time.monotonic() - t0) * 1000.0

    try:
        payload = response.json()
    except ValueError:
        payload = {"message": response.text}

    if not response.ok:
        logger.error(format_redacted_upstream_error(trace_id, "index/search", response.status_code, elapsed_ms, "HTTPError"))
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            error_code = error.get("code") or response.status_code
            error_message = error.get("message") or response.text
            message = f"{error_code}: {error_message}"
        elif isinstance(payload, dict):
            message = payload.get("message") or payload.get("detail") or response.text
        else:
            message = response.text

        return {
            "results": [],
            "message": message,
            "kb_id": kb_id,
            "kb_name": kb_name,
            "file_id": file_id,
            "status": "error",
            "trace_id": trace_id,
        }

    results = normalize_multimodal_results(payload, kb_id=kb_id)

    return {
        "results": results,
        "message": payload.get("message") if isinstance(payload, dict) else "",
        "kb_id": kb_id,
        "kb_name": kb_name,
        "file_id": file_id,
        "status": "ok" if results else "empty",
        "trace_id": trace_id,
    }


# ---------------------------------------------------------------------------
# B3. 固定代理白名单与请求/响应边界策略
#
# 远端 `/api/multimodal/**` 不再使用全路径通用代理，改为“HTTP 方法 + 固定
# 路径模板”白名单。每条接口显式定义权限、请求模型、超时、响应类型和大小限制。
# 这些纯策略函数可被契约测试直接导入，路由层只负责把它们接到 FastAPI 上。
# ---------------------------------------------------------------------------

# 权限级别：检索使用接口允许任意已登录用户；管理接口仅超级管理员。
PERMISSION_READ = "read"
PERMISSION_ADMIN = "admin"

# 请求体类型：无正文 / JSON / multipart 上传。
BODY_NONE = "none"
BODY_JSON = "json"
BODY_MULTIPART = "multipart"

# 响应类型：JSON（有体积上限） / 流式文件（图片/文档/下载）。
RESPONSE_JSON = "json"
RESPONSE_STREAM = "stream"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


MAX_JSON_BODY_BYTES = _env_int("MULTIMODAL_JSON_BODY_MAX_BYTES", 1 * 1024 * 1024)
MAX_JSON_RESPONSE_BYTES = _env_int("MULTIMODAL_JSON_RESPONSE_MAX_BYTES", 16 * 1024 * 1024)
MAX_STREAM_BYTES = _env_int("MULTIMODAL_STREAM_RESPONSE_MAX_BYTES", 512 * 1024 * 1024)
MAX_UPLOAD_FILES = _env_int("MULTIMODAL_UPLOAD_MAX_FILES", 5)
MAX_UPLOAD_FILE_BYTES = _env_int("MULTIMODAL_UPLOAD_MAX_FILE_BYTES", 50 * 1024 * 1024)
MAX_UPLOAD_TOTAL_BYTES = _env_int("MULTIMODAL_UPLOAD_MAX_TOTAL_BYTES", 100 * 1024 * 1024)

# 上传扩展名/MIME 白名单（对齐管理页目前允许的 PDF/Excel/CSV/TXT/MD/LAS 与图片）。
ALLOWED_UPLOAD_EXTENSIONS = frozenset({
    ".pdf", ".xlsx", ".xls", ".csv", ".txt", ".md", ".las",
    ".docx", ".doc", ".png", ".jpg", ".jpeg", ".bmp", ".gif",
    ".webp", ".tif", ".tiff",
})
ALLOWED_UPLOAD_MIMES = frozenset({
    "application/pdf",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/csv",
    "text/plain",
    "text/markdown",
    "text/x-las",
    "application/zip",
    "application/octet-stream",
    "image/png", "image/jpeg", "image/gif", "image/bmp",
    "image/webp", "image/tiff",
})

# 上传不允许的 MIME（防止把 HTML/脚本当文档入库）。
REJECTED_UPLOAD_MIMES = frozenset({
    "text/html",
    "application/xhtml+xml",
    "application/javascript",
    "text/javascript",
    "application/json",
})

# 流式（文件/图片）响应的合法 Content-Type。application/json / text/html 会被拒绝。
SAFE_STREAM_CONTENT_TYPES = frozenset({
    "application/pdf",
    "application/octet-stream",
    "application/zip",
    "application/gzip",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/csv",
    "text/plain",
    "text/markdown",
    "text/x-las",
})


class MultimodalUploadError(Exception):
    """上传校验失败（status_code + message，路由层据此返回 4xx）。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class MultimodalRouteSpec:
    """一条远端代理路由的完整边界定义（B3.2）。"""

    method: str
    path: str  # 相对 /api/v1 的路径模板，如 "kb/list"
    permission: str  # PERMISSION_READ / PERMISSION_ADMIN
    body: str  # BODY_NONE / BODY_JSON / BODY_MULTIPART
    response: str  # RESPONSE_JSON / RESPONSE_STREAM
    timeout_seconds: float
    max_response_bytes: int
    max_files: int = MAX_UPLOAD_FILES
    max_file_bytes: int = MAX_UPLOAD_FILE_BYTES
    max_total_bytes: int = MAX_UPLOAD_TOTAL_BYTES
    allowed_extensions: frozenset[str] = ALLOWED_UPLOAD_EXTENSIONS
    allowed_mimes: frozenset[str] = ALLOWED_UPLOAD_MIMES


def _make_route_spec(
    method: str,
    path: str,
    *,
    permission: str,
    body: str = BODY_NONE,
    response: str = RESPONSE_JSON,
    timeout: float = 30.0,
    max_response_bytes: int | None = None,
    **upload: Any,
) -> MultimodalRouteSpec:
    return MultimodalRouteSpec(
        method=method,
        path=path,
        permission=permission,
        body=body,
        response=response,
        timeout_seconds=timeout,
        max_response_bytes=max_response_bytes or MAX_JSON_RESPONSE_BYTES,
        **upload,
    )


# 白名单由管理页当前真实调用的远端接口构成（A2 契约核对后的子集）。
# 检索接口（POST /index/search）允许任意已登录用户；其余管理接口仅超级管理员。
MULTIMODAL_PROXY_WHITELIST: dict[tuple[str, str], MultimodalRouteSpec] = {
    # ---- 检索使用接口（已登录用户） ----
    ("POST", "index/search"): _make_route_spec(
        "POST", "index/search", permission=PERMISSION_READ, body=BODY_JSON, timeout=30.0,
    ),

    # ---- 健康与知识库（超级管理员） ----
    ("GET", "health"): _make_route_spec("GET", "health", permission=PERMISSION_ADMIN, timeout=10.0),
    ("GET", "kb/list"): _make_route_spec("GET", "kb/list", permission=PERMISSION_ADMIN, timeout=15.0),
    ("GET", "kb/files"): _make_route_spec("GET", "kb/files", permission=PERMISSION_ADMIN, timeout=30.0),
    ("GET", "kb/images"): _make_route_spec("GET", "kb/images", permission=PERMISSION_ADMIN, timeout=15.0),
    ("GET", "kb/file/dataframe"): _make_route_spec("GET", "kb/file/dataframe", permission=PERMISSION_ADMIN, timeout=60.0),
    ("GET", "kb/file/content"): _make_route_spec("GET", "kb/file/content", permission=PERMISSION_ADMIN, timeout=60.0),
    ("GET", "file-manager/wells"): _make_route_spec("GET", "file-manager/wells", permission=PERMISSION_ADMIN, timeout=30.0),

    # ---- 文件 / PDF / 解析状态（超级管理员） ----
    ("POST", "pdf/upload"): _make_route_spec(
        "POST", "pdf/upload", permission=PERMISSION_ADMIN, body=BODY_MULTIPART, timeout=120.0,
    ),
    ("POST", "pdf/parse"): _make_route_spec("POST", "pdf/parse", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=120.0),
    ("GET", "pdf/status"): _make_route_spec("GET", "pdf/status", permission=PERMISSION_ADMIN, timeout=15.0),
    ("GET", "pdf/images_list"): _make_route_spec("GET", "pdf/images_list", permission=PERMISSION_ADMIN, timeout=30.0),
    ("GET", "pdf/image_summaries"): _make_route_spec("GET", "pdf/image_summaries", permission=PERMISSION_ADMIN, timeout=30.0),
    ("POST", "pdf/image_summaries/update"): _make_route_spec(
        "POST", "pdf/image_summaries/update", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=120.0,
    ),
    ("GET", "pdf/chunk"): _make_route_spec("GET", "pdf/chunk", permission=PERMISSION_ADMIN, timeout=30.0),

    # ---- 文件/图片流式响应（超级管理员） ----
    ("GET", "kb/file/original"): _make_route_spec(
        "GET", "kb/file/original", permission=PERMISSION_ADMIN, response=RESPONSE_STREAM, timeout=60.0,
    ),
    ("GET", "pdf/images"): _make_route_spec(
        "GET", "pdf/images", permission=PERMISSION_ADMIN, response=RESPONSE_STREAM, timeout=60.0,
    ),
    ("GET", "pdf/page"): _make_route_spec(
        "GET", "pdf/page", permission=PERMISSION_ADMIN, response=RESPONSE_STREAM, timeout=60.0,
    ),
    ("GET", "extraction/image"): _make_route_spec(
        "GET", "extraction/image", permission=PERMISSION_ADMIN, response=RESPONSE_STREAM, timeout=60.0,
    ),

    # ---- 索引（超级管理员） ----
    ("POST", "index/build"): _make_route_spec("POST", "index/build", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=120.0),
    ("POST", "index/delete"): _make_route_spec("POST", "index/delete", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=60.0),
    ("GET", "index/chunks"): _make_route_spec("GET", "index/chunks", permission=PERMISSION_ADMIN, timeout=30.0),
    ("GET", "index/chunks/stats"): _make_route_spec("GET", "index/chunks/stats", permission=PERMISSION_ADMIN, timeout=30.0),

    # ---- 知识库文件与图片管理（超级管理员） ----
    ("POST", "kb/create"): _make_route_spec("POST", "kb/create", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=30.0),
    ("POST", "kb/delete"): _make_route_spec("POST", "kb/delete", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=30.0),
    ("POST", "kb/file/delete"): _make_route_spec("POST", "kb/file/delete", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=30.0),
    ("POST", "kb/image/update"): _make_route_spec("POST", "kb/image/update", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=30.0),
    ("POST", "kb/images/update"): _make_route_spec("POST", "kb/images/update", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=30.0),

    # ---- 提取（超级管理员） ----
    ("POST", "extraction/extract"): _make_route_spec(
        "POST", "extraction/extract", permission=PERMISSION_ADMIN, body=BODY_MULTIPART, timeout=120.0,
    ),
    ("GET", "extraction/status"): _make_route_spec("GET", "extraction/status", permission=PERMISSION_ADMIN, timeout=15.0),
    ("GET", "extraction/content"): _make_route_spec("GET", "extraction/content", permission=PERMISSION_ADMIN, timeout=60.0),
    ("GET", "extraction/check_filename"): _make_route_spec("GET", "extraction/check_filename", permission=PERMISSION_ADMIN, timeout=15.0),
    ("POST", "extraction/update_result"): _make_route_spec("POST", "extraction/update_result", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=60.0),

    # ---- 预处理（超级管理员） ----
    ("GET", "preprocess/methods"): _make_route_spec("GET", "preprocess/methods", permission=PERMISSION_ADMIN, timeout=15.0),
    ("POST", "preprocess/upload"): _make_route_spec(
        "POST", "preprocess/upload", permission=PERMISSION_ADMIN, body=BODY_MULTIPART, timeout=120.0,
    ),
    ("POST", "preprocess/run"): _make_route_spec("POST", "preprocess/run", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=120.0),
    ("POST", "preprocess/workbench/run"): _make_route_spec("POST", "preprocess/workbench/run", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=120.0),
    ("POST", "preprocess/workbench/grouped/run"): _make_route_spec("POST", "preprocess/workbench/grouped/run", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=120.0),
    ("POST", "preprocess/workbench/store"): _make_route_spec("POST", "preprocess/workbench/store", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=120.0),
    ("GET", "preprocess/workbench/dataframe"): _make_route_spec("GET", "preprocess/workbench/dataframe", permission=PERMISSION_ADMIN, timeout=60.0),
    ("GET", "preprocess/report"): _make_route_spec("GET", "preprocess/report", permission=PERMISSION_ADMIN, timeout=60.0),
    ("GET", "preprocess/dataframe"): _make_route_spec("GET", "preprocess/dataframe", permission=PERMISSION_ADMIN, timeout=60.0),
    ("GET", "preprocess/workbench/download"): _make_route_spec(
        "GET", "preprocess/workbench/download", permission=PERMISSION_ADMIN, response=RESPONSE_STREAM, timeout=120.0,
    ),
    ("GET", "preprocess/workbench/artifact/download"): _make_route_spec(
        "GET", "preprocess/workbench/artifact/download", permission=PERMISSION_ADMIN, response=RESPONSE_STREAM, timeout=120.0,
    ),

    # ---- 结构化数据库（超级管理员） ----
    ("GET", "structured-db/supported"): _make_route_spec("GET", "structured-db/supported", permission=PERMISSION_ADMIN, timeout=15.0),
    ("GET", "structured-db/connections"): _make_route_spec("GET", "structured-db/connections", permission=PERMISSION_ADMIN, timeout=15.0),
    ("POST", "structured-db/connect"): _make_route_spec("POST", "structured-db/connect", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=30.0),
    ("POST", "structured-db/disconnect"): _make_route_spec("POST", "structured-db/disconnect", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=30.0),
    ("GET", "structured-db/schema"): _make_route_spec("GET", "structured-db/schema", permission=PERMISSION_ADMIN, timeout=30.0),
    ("GET", "structured-db/table"): _make_route_spec("GET", "structured-db/table", permission=PERMISSION_ADMIN, timeout=30.0),
    ("POST", "structured-db/query"): _make_route_spec("POST", "structured-db/query", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=60.0),

    # ---- 统一查询（超级管理员；检索仍在 chat_router 走 /api/chat/multimodal） ----
    ("POST", "query"): _make_route_spec("POST", "query", permission=PERMISSION_ADMIN, body=BODY_JSON, timeout=60.0),
}


def route_spec_for(method: str, path: str) -> MultimodalRouteSpec | None:
    """按“HTTP 方法 + 固定路径”精确匹配白名单；不在白名单返回 None（路由层 404/405）。"""
    return MULTIMODAL_PROXY_WHITELIST.get((str(method or "").upper(), str(path or "").lstrip("/")))


def whitelisted_proxy_routes() -> list[tuple[str, str]]:
    """返回按方法/路径排序的白名单条目，用于显式注册路由（B3.1 删除通配代理）。"""
    return sorted(MULTIMODAL_PROXY_WHITELIST, key=lambda item: (item[0], item[1]))


def validate_upload_metadata(
    file_metas: list[tuple[str | None, str | None, int]],
    spec: MultimodalRouteSpec,
) -> None:
    """校验上传的文件数、单文件大小、总大小、扩展名与 MIME（B3.4）。

    file_metas 元素为 (filename, content_type, size_bytes)。校验失败抛
    MultimodalUploadError。路由层负责把上传流式解析（SpooledTemporaryFile，
    超过阈值落到磁盘），不在内存中读取完整文件。
    """
    if len(file_metas) > spec.max_files:
        raise MultimodalUploadError(
            413, f"上传文件数超过限制（最多 {spec.max_files} 个）",
        )

    total = 0
    for filename, content_type, size in file_metas:
        extension = Path(str(filename or "")).suffix.lower()
        if extension not in spec.allowed_extensions:
            raise MultimodalUploadError(
                400, f"文件类型不允许上传: {extension or '(无扩展名)'}",
            )
        mime = str(content_type or "").strip().lower()
        if mime in REJECTED_UPLOAD_MIMES:
            raise MultimodalUploadError(400, f"文件 MIME 类型不允许上传: {mime}")
        if (
            mime
            and mime != "application/octet-stream"
            and mime not in spec.allowed_mimes
            and not mime.startswith(("image/", "text/"))
        ):
            raise MultimodalUploadError(400, f"文件 MIME 类型不允许上传: {mime}")

        file_size = int(size or 0)
        if file_size > spec.max_file_bytes:
            raise MultimodalUploadError(
                413, f"单个文件超过大小限制（{spec.max_file_bytes} 字节）",
            )
        total += file_size

    if total > spec.max_total_bytes:
        raise MultimodalUploadError(
            413, f"上传总大小超过限制（{spec.max_total_bytes} 字节）",
        )


def map_upstream_proxy_status(status: int) -> tuple[int, str]:
    """按 B3.7 把上游状态映射为 SAGE 响应（status_code, 通用 detail）。"""
    if status in (400, 422):
        return 400, "多模态远端请求参数错误"
    if status == 404:
        return 404, "多模态远端接口不存在"
    if status == 429:
        return 429, "多模态远端请求过频"
    if status == 503:
        return 503, "多模态远端繁忙，请稍后重试"
    if status >= 500:
        return 502, "多模态远端服务不可用"
    return status, ""


def validate_stream_content_type(content_type: str | None) -> bool:
    """流式（文件/图片）响应的 Content-Type 白名单校验（B3.6）。

    拒绝 application/json / text/html 等“错误 Content-Type”，防止把上游
    错误页当文件返回。未知类型不阻塞（由流式转发兜底），但 JSON/HTML 一律拒绝。
    """
    ct = str(content_type or "").split(";", 1)[0].strip().lower()
    if not ct:
        return True
    if ct in ("application/json", "text/html", "application/xhtml+xml"):
        return False
    if ct in SAFE_STREAM_CONTENT_TYPES:
        return True
    if ct.startswith(("image/", "text/")):
        return True
    return False


# 允许透传给浏览器的响应头白名单。Set-Cookie / Location / content-length /
# content-type 等一律不透传（B3.6）。
SAFE_PROXY_RESPONSE_HEADERS = frozenset({
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-range",
    "etag",
    "last-modified",
})


def filter_multimodal_response_headers(headers: Any) -> dict[str, str]:
    """只转发白名单内的响应头（B3.6）。"""
    result: dict[str, str] = {}
    if headers is None:
        return result
    for key, value in headers.items():
        if str(key).lower() in SAFE_PROXY_RESPONSE_HEADERS:
            result[str(key)] = str(value)
    return result


async def accumulate_bounded_bytes(agen: Any, cap: int) -> bytes | None:
    """从异步字节迭代器累计读取，超过 *cap* 字节返回 None（B3.5 体积上限）。

    用于 JSON 响应体与 JSON 请求体：避免把超大响应整体读入内存。
    """
    total = 0
    chunks: list[bytes] = []
    async for chunk in agen:
        total += len(chunk)
        if total > cap:
            return None
        chunks.append(chunk)
    return b"".join(chunks)
