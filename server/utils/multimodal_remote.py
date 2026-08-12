import json
import logging
import os
import re
import time
import uuid
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
