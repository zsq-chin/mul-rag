"""阶段 D2：图片代理硬化回归测试。

覆盖：
- 标识符校验（kbId/fileId/imagePath）：拒绝绝对路径、穿越、URL、盘符、UNC、NUL；
- 通用代理查询参数标识符校验（validate_proxy_identifier_params）；
- 图片 Content-Type 白名单与响应头白名单（不把 JSON 错误当 PNG 返回，不泄漏
  Set-Cookie/Location 等头）；
- `/api/chat/multimodal/image` 真实 HTTP 路由行为（TestClient）：上游状态码/
  Content-Type 前置检查、304 透传、体积上限、条件请求头透传、标识符非法返回 400。

路由行为一律通过真实 FastAPI TestClient 验证（对应 H1.4「不再直接调用 resolver
当作接口测试」）。为使路由可在无 Milvus/模型栈的主机运行，本模块在 import 时
以「轻量 src 桩 + 直载 chat_router 模块文件」方式加载路由：被测路由在运行期
完全不触碰知识库/图谱/Agent 设施，桩只提供 import 期名称，不伪造任何路由行为。
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.utils.multimodal_remote import (
    MAX_IMAGE_RESPONSE_BYTES,
    filter_image_response_headers,
    is_image_content_type,
    validate_multimodal_image_params,
    validate_proxy_identifier_params,
)

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# 轻量 src 桩 + 直载 chat_router（不触发 server.routers 全量路由导入）
# ---------------------------------------------------------------------------
def _install_src_shim() -> None:
    """让 chat_router 可在无 Milvus/模型栈的主机导入。

    真实的 `src` 包在 import 时会实例化 KnowledgeBase()（需 Milvus 连接），并
    连带导入 torch/neo4j/langchain。被测的 /api/chat/multimodal/image 路由在
    运行期不触碰知识库/图谱/Agent 设施，因此这里只提供 import 期需要的名称；
    路由逻辑本身照常通过真实 FastAPI TestClient 执行。
    """
    if "src" in sys.modules and getattr(sys.modules.get("src"), "_sage_image_proxy_shim", False):
        return

    fake_src = types.ModuleType("src")
    fake_src.__path__ = [str(ROOT / "src")]
    fake_src._sage_image_proxy_shim = True
    fake_src.executor = __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=2)
    fake_src.config = types.SimpleNamespace(
        save_dir="saves",
        enable_knowledge_base=False,
        enable_reranker=False,
        model_provider="",
        model_name="",
        embed_model="",
    )
    fake_src.retriever = None
    fake_src.knowledge_base = None
    fake_src.graph_base = None
    fake_src.BLOCKING_WORKERS = 2
    fake_src.shutdown_runtime = lambda: None
    sys.modules["src"] = fake_src

    # src.core 连带 torch/neo4j —— 只取 import 期名称
    fake_core = types.ModuleType("src.core")
    fake_core.HistoryManager = type("HistoryManager", (), {})
    sys.modules["src.core"] = fake_core

    # src.agents / src.agents.tools_factory 连带 langchain agents
    fake_agents = types.ModuleType("src.agents")
    fake_agents.agent_manager = None
    sys.modules["src.agents"] = fake_agents
    fake_tools = types.ModuleType("src.agents.tools_factory")
    fake_tools.get_all_tools = lambda *a, **k: []
    sys.modules["src.agents.tools_factory"] = fake_tools

    # src.models 连带 langchain 模型工厂
    fake_models = types.ModuleType("src.models")
    fake_models.select_model = lambda *a, **k: None
    sys.modules["src.models"] = fake_models


_install_src_shim()

_spec = importlib.util.spec_from_file_location(
    "chat_router_under_test",
    ROOT / "server" / "routers" / "chat_router.py",
)
chat_router = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(chat_router)


# ---------------------------------------------------------------------------
# 纯 utils：标识符校验
# ---------------------------------------------------------------------------
class ImageIdentifierValidationTests(unittest.TestCase):
    def test_accepts_safe_identifiers(self):
        kb, file_id, img = validate_multimodal_image_params("kb-1", "file-A", "page1_img1.png")
        self.assertEqual((kb, file_id, img), ("kb-1", "file-A", "page1_img1.png"))

    def test_accepts_chinese_kb_id(self):
        kb, _, _ = validate_multimodal_image_params("钻井设计资料", "file", "a.png")
        self.assertEqual(kb, "钻井设计资料")

    def test_accepts_subdir_image_path_normalizes_to_basename(self):
        # 远端图片名形如 page1/image.png，SAGE 校验后转发 basename
        kb, file_id, img = validate_multimodal_image_params("kb", "file", "page1/image.png")
        self.assertEqual((kb, file_id, img), ("kb", "file", "image.png"))

    def test_rejects_traversal_image_path(self):
        bad = [
            "../secret.png",
            "..%2Fsecret.png",
            "sub/../evil.png",
            "/etc/passwd",
            "C:\\Windows\\evil.png",
            "C:/Windows/evil.png",
            "//server/share/evil.png",
            "\\\\server\\share\\evil.png",
            "http://evil.example/x.png",
            "a\x00b.png",
        ]
        for candidate in bad:
            with self.assertRaises(ValueError, msg=f"应拒绝图片名 {candidate!r}"):
                validate_multimodal_image_params("kb", "file", candidate)

    def test_rejects_traversal_kb_or_file_id(self):
        for bad_kb in ("../etc", "a/b", "a\\b", "C:kb", "//host/share", "/abs", "a\x00b", "..", "."):
            with self.assertRaises(ValueError, msg=f"应拒绝 kbId {bad_kb!r}"):
                validate_multimodal_image_params(bad_kb, "file", "a.png")
        for bad_file in ("../x", "a/b", "C:\\x", "/abs", "a\x00b", "..", "."):
            with self.assertRaises(ValueError, msg=f"应拒绝 fileId {bad_file!r}"):
                validate_multimodal_image_params("kb", bad_file, "a.png")

    def test_proxy_identifier_params_only_validate_identifiers(self):
        # 只校验标识符类参数；检索文本 query 允许任意字符
        validate_proxy_identifier_params([("query", "a/b/c ?=#"), ("topK", "3")])
        with self.assertRaises(ValueError):
            validate_proxy_identifier_params([("kbId", "../../etc")])
        with self.assertRaises(ValueError):
            validate_proxy_identifier_params([("fileId", "C:\\Windows")])
        with self.assertRaises(ValueError):
            validate_proxy_identifier_params([("imagePath", "http://evil/x.png")])
        with self.assertRaises(ValueError):
            validate_proxy_identifier_params([("path", "/abs/x.png")])
        with self.assertRaises(ValueError):
            validate_proxy_identifier_params([("kbId", "..")])


class ImageContentTypeTests(unittest.TestCase):
    def test_accepts_image_content_types(self):
        for ct in ("image/png", "image/jpeg", "image/webp", "application/octet-stream"):
            self.assertTrue(is_image_content_type(ct), ct)
        self.assertTrue(is_image_content_type("image/png; charset=binary"))

    def test_rejects_non_image_content_types(self):
        for ct in ("application/json", "text/html", "text/plain", "application/pdf", ""):
            self.assertFalse(is_image_content_type(ct), ct)


class ImageResponseHeaderFilterTests(unittest.TestCase):
    def test_only_allowed_headers_pass(self):
        source = {
            "ETag": '"abc"',
            "Cache-Control": "private, max-age=3600",
            "Accept-Ranges": "bytes",
            "Content-Range": "bytes 0-10/100",
            "Last-Modified": "Wed",
            "Set-Cookie": "session=evil",
            "Location": "http://evil/",
            "Content-Length": "99999",
            "Content-Type": "image/png",
            "Authorization": "Bearer secret",
        }
        filtered = filter_image_response_headers(source)
        self.assertIn("ETag", filtered)
        self.assertIn("Cache-Control", filtered)
        self.assertIn("Accept-Ranges", filtered)
        self.assertIn("Content-Range", filtered)
        self.assertIn("Last-Modified", filtered)
        for blocked in ("Set-Cookie", "Location", "Content-Length", "Content-Type", "Authorization"):
            self.assertNotIn(blocked, filtered)

    def test_max_image_bytes_constant_exists(self):
        self.assertGreater(MAX_IMAGE_RESPONSE_BYTES, 0)


# ---------------------------------------------------------------------------
# HTTP：/api/chat/multimodal/image 真实路由
# ---------------------------------------------------------------------------
class ScriptedResponse:
    def __init__(self, status_code=200, headers=None, body=b"", is_redirect=False):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self.is_redirect = is_redirect
        self.closed = False

    async def aiter_bytes(self, chunk_size=65536):
        if self._body:
            yield self._body

    async def aclose(self):
        self.closed = True


class ScriptedClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []

    def build_request(self, method, url, params=None, headers=None, **kwargs):
        self.requests.append({"method": method, "url": url, "params": params, "headers": headers or {}})
        return self.requests[-1]

    async def send(self, request, stream):
        self.requests[-1]["_sent"] = True
        return self._responses.pop(0)


class FakeUser:
    id = 1
    role = "user"


class ChatImageProxyHttpTests(unittest.TestCase):
    def setUp(self):
        from server.utils.auth_middleware import get_required_user

        app = FastAPI()
        app.include_router(chat_router.chat, prefix="/api")
        app.dependency_overrides[get_required_user] = lambda: FakeUser()
        self.app = app

    def _client_with_upstream(self, *responses):
        upstream = ScriptedClient(responses)
        return upstream, TestClient(self.app)

    def _request(self, client, url="/api/chat/multimodal/image?kbId=kb&fileId=file&imagePath=a.png", headers=None):
        return client.get(url, headers=headers or {})

    def test_streams_png_with_allowed_headers(self):
        upstream, client = self._client_with_upstream(
            ScriptedResponse(
                200,
                headers={"content-type": "image/png", "ETag": '"x"', "Cache-Control": "private, max-age=3600", "Set-Cookie": "evil=1"},
                body=b"\x89PNG-IMG",
            )
        )
        with (
            patch.object(chat_router, "get_multimodal_client", return_value=upstream),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream/api/v1"),
        ):
            resp = self._request(client)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"\x89PNG-IMG")
        self.assertEqual(resp.headers["content-type"], "image/png")
        self.assertEqual(resp.headers["etag"], '"x"')
        self.assertNotIn("set-cookie", resp.headers)

    def test_upstream_url_is_remote_pdf_images_with_safe_params(self):
        # D2a：代理指向远端 /pdf/images 并转发规范化后的标识符
        upstream, client = self._client_with_upstream(
            ScriptedResponse(200, headers={"content-type": "image/png"}, body=b"png")
        )
        with (
            patch.object(chat_router, "get_multimodal_client", return_value=upstream),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream/api/v1"),
        ):
            client.get(
                "/api/chat/multimodal/image",
                params={"kbId": "kb", "fileId": "f", "imagePath": "page1/a.png"},
            )
        req = upstream.requests[0]
        self.assertTrue(req["url"].endswith("/pdf/images"))
        self.assertEqual(req["params"]["kbId"], "kb")
        self.assertEqual(req["params"]["fileId"], "f")
        self.assertEqual(req["params"]["imagePath"], "a.png")
        self.assertEqual(req["params"]["thumb"], 0)

    def test_json_error_from_upstream_is_not_streamed_as_png(self):
        # D2.4：上游 200 但 Content-Type 是 JSON（如 JSON 错误被当图片返回）→ 502
        upstream, client = self._client_with_upstream(
            ScriptedResponse(200, headers={"content-type": "application/json"}, body=b'{"error": "oops"}')
        )
        with (
            patch.object(chat_router, "get_multimodal_client", return_value=upstream),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream/api/v1"),
        ):
            resp = self._request(client)
        self.assertEqual(resp.status_code, 502)

    def test_upstream_404_maps_and_never_streams_error_body(self):
        upstream, client = self._client_with_upstream(
            ScriptedResponse(404, headers={"content-type": "application/json"}, body=b'{"error": {"message": "nope"}}')
        )
        with (
            patch.object(chat_router, "get_multimodal_client", return_value=upstream),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream/api/v1"),
        ):
            resp = self._request(client)
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("image/png", resp.headers.get("content-type", ""))

    def test_304_is_passed_through(self):
        upstream, client = self._client_with_upstream(
            ScriptedResponse(304, headers={"etag": '"x"', "cache-control": "private, max-age=3600"})
        )
        with (
            patch.object(chat_router, "get_multimodal_client", return_value=upstream),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream/api/v1"),
        ):
            resp = self._request(client, headers={"If-None-Match": '"x"'})
        self.assertEqual(resp.status_code, 304)
        self.assertEqual(resp.headers["etag"], '"x"')

    def test_conditional_and_range_headers_forwarded_to_upstream(self):
        upstream, client = self._client_with_upstream(
            ScriptedResponse(200, headers={"content-type": "image/png"}, body=b"png")
        )
        with (
            patch.object(chat_router, "get_multimodal_client", return_value=upstream),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream/api/v1"),
        ):
            self._request(client, headers={"If-None-Match": '"x"', "Range": "bytes=0-10"})
        lower = {k.lower(): v for k, v in upstream.requests[0]["headers"].items()}
        self.assertEqual(lower.get("if-none-match"), '"x"')
        self.assertEqual(lower.get("range"), "bytes=0-10")

    def test_oversized_image_is_rejected(self):
        upstream, client = self._client_with_upstream(
            ScriptedResponse(
                200,
                headers={"content-type": "image/png", "content-length": str(MAX_IMAGE_RESPONSE_BYTES + 1)},
                body=b"png",
            )
        )
        with (
            patch.object(chat_router, "get_multimodal_client", return_value=upstream),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream/api/v1"),
        ):
            resp = self._request(client)
        self.assertEqual(resp.status_code, 502)

    def test_invalid_identifier_returns_400_before_upstream_call(self):
        upstream, client = self._client_with_upstream()
        with (
            patch.object(chat_router, "get_multimodal_client", return_value=upstream),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream/api/v1"),
        ):
            resp = client.get(
                "/api/chat/multimodal/image",
                params={"kbId": "kb", "fileId": "file", "imagePath": "../../etc/passwd"},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(len(upstream.requests), 0, "非法标识符不得发起上游请求")

    def test_missing_config_returns_503(self):
        upstream, client = self._client_with_upstream()
        with patch.object(chat_router, "get_multimodal_api_base", return_value=""):
            resp = self._request(client)
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(len(upstream.requests), 0)


if __name__ == "__main__":
    unittest.main()
