import json
import os
import re
from typing import Any
from urllib.parse import unquote, urlencode, urlsplit

import requests


DEFAULT_MULTIMODAL_API_BASE = "http://localhost:8002/api/v1"
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((?:\./)?images/([^)]+)\)")
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


def get_multimodal_api_base(meta: dict[str, Any] | None = None) -> str:
    meta = meta or {}
    return (
        meta.get("multimodal_api_base")
        or os.getenv("MULTIMODAL_KB_API_BASE")
        or os.getenv("MULTIMODAL_REMOTE_BASE_URL")
        or DEFAULT_MULTIMODAL_API_BASE
    ).rstrip("/")


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

    return f"{(base_url or get_multimodal_api_base()).rstrip('/')}/{normalized_path}"


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
    parsed = urlsplit(decoded_path)
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
                "raw": item,
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


def _resolve_kb_id(base_url: str, meta: dict[str, Any], timeout: float) -> str | None:
    kb_id = (
        meta.get("multimodal_kb_id")
        or os.getenv("MULTIMODAL_KB_DEFAULT_KB_ID")
        or os.getenv("MULTIMODAL_REMOTE_DEFAULT_KB_ID")
    )
    if kb_id:
        return str(kb_id).strip()

    response = requests.get(f"{base_url}/kb/list", timeout=timeout)
    response.raise_for_status()
    return pick_first_kb_id(response.json())


def search_multimodal_remote(query: str, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = meta or {}
    base_url = get_multimodal_api_base(meta)
    timeout = float(meta.get("multimodal_timeout") or os.getenv("MULTIMODAL_KB_TIMEOUT") or 30)
    top_k = int(meta.get("multimodal_top_k") or os.getenv("MULTIMODAL_KB_TOP_K") or 5)
    kb_id = _resolve_kb_id(base_url, meta, timeout)
    kb_name = meta.get("multimodal_kb_name") or kb_id

    if not kb_id:
        return {
            "results": [],
            "message": "未配置多模态知识库 kbId，且远程 /kb/list 没有可用知识库",
            "kb_id": None,
            "kb_name": None,
            "base_url": base_url,
            "status": "error",
        }

    body: dict[str, Any] = {"kbId": kb_id, "query": query, "k": top_k}
    file_id = meta.get("multimodal_file_id")
    if file_id:
        body["fileId"] = file_id

    response = requests.post(f"{base_url}/index/search", json=body, timeout=timeout)
    try:
        payload = response.json()
    except ValueError:
        payload = {"message": response.text}

    if not response.ok:
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
            "base_url": base_url,
            "status": "error",
            "raw": payload,
        }

    results = normalize_multimodal_results(payload, kb_id=kb_id)

    return {
        "results": results,
        "message": payload.get("message") if isinstance(payload, dict) else "",
        "kb_id": kb_id,
        "kb_name": kb_name,
        "file_id": file_id,
        "base_url": base_url,
        "status": "ok" if results else "empty",
        "raw": payload,
    }
