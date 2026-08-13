"""阶段 B2：服务间认证、trace 透传与上游错误脱敏的回归测试。

验证：
- 服务 Token 仅由服务端环境注入，且不进入返回给浏览器的结果；
- 浏览器 Authorization / Cookie 不会被转发到远端；
- 上游错误日志只含 trace/接口/状态码/耗时/异常类型，不含正文、查询、
  Token、API Key 或响应全文。
"""

import json
import os
import sys
import types
import unittest
from unittest.mock import Mock, patch


def _install_src_shim() -> None:
    """让 server.utils.multimodal_remote 可在无 Milvus/Neo4j/MySQL 的主机导入。

    multimodal_remote 模块级 ``from src.utils.logging_config import logger`` 会触发
    真实 src/__init__.py（实例化 KnowledgeBase → Milvus 连接失败），既拖慢测试，
    又在 runner 输出里留下 Milvus 错误标记（这些模块曾被误判为 env-missing，掩盖了
    真实断言失败）。这里用最小 src 桩屏蔽真实 src；被测逻辑仍是真实 multimodal_remote。
    """
    if "src" in sys.modules and getattr(sys.modules["src"], "_sage_multimodal_shim", False):
        return

    class _StubLogger:
        def info(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass
        def warning(self, *args, **kwargs): pass
        def debug(self, *args, **kwargs): pass

    src = types.ModuleType("src")
    src._sage_multimodal_shim = True
    sys.modules["src"] = src
    utils = types.ModuleType("src.utils")
    utils.logger = _StubLogger()
    sys.modules["src.utils"] = utils
    logging_config = types.ModuleType("src.utils.logging_config")
    logging_config.logger = _StubLogger()
    sys.modules["src.utils.logging_config"] = logging_config


_install_src_shim()

from server.utils.multimodal_remote import (
    build_service_auth_headers,
    filter_multimodal_proxy_headers,
    format_redacted_upstream_error,
    search_multimodal_remote,
)

FIXED_BASE = "https://fixed-remote.example/api/v1"


def _kb_list_response():
    r = Mock()
    r.ok = True
    r.status_code = 200
    r.json.return_value = {"kbs": [{"kbId": "kb-1", "kbName": "库1"}]}
    return r


def _search_ok_response():
    r = Mock()
    r.ok = True
    r.status_code = 200
    r.json.return_value = {"ok": True, "results": [{"id": 1, "entity_key": "f", "chunk_text": "x"}]}
    return r


class ServiceTokenTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            # I1.1 显式开关：未显式启用即视为关闭，检索路径需要显式 true
            "MULTIMODAL_ENABLED": "true",
            "MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE,
            "MULTIMODAL_SERVICE_TOKEN": "svc-secret-token",
        },
        clear=False,
    )
    @patch("server.utils.multimodal_remote.get_multimodal_sync_session")
    def test_service_token_injected_and_not_leaked(self, mock_gs):
        session = mock_gs.return_value
        session.post.return_value = _search_ok_response()

        result = search_multimodal_remote("q", {"multimodal_kb_id": "kb-1"})

        auth = session.post.call_args.kwargs["headers"].get("Authorization")
        self.assertEqual(auth, "Bearer svc-secret-token")
        self.assertTrue(session.post.call_args.kwargs["headers"].get("X-Sage-Trace-Id"))
        # Token 不进入返回给浏览器的结果
        dumped = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("svc-secret-token", dumped)

    @patch.dict(
        os.environ,
        {"MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE, "MULTIMODAL_SERVICE_TOKEN": "svc-secret-token"},
        clear=False,
    )
    def test_service_headers_never_echo_browser_credentials(self):
        headers = build_service_auth_headers(trace_id="abc123")
        # 服务头只含注入的 Token 与 trace，绝不包含调用方传入的任何凭据
        self.assertEqual(headers["Authorization"], "Bearer svc-secret-token")
        self.assertEqual(headers["X-Sage-Trace-Id"], "abc123")
        self.assertNotIn("Cookie", headers)

    @patch.dict(os.environ, {"MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE}, clear=False)
    def test_no_authorization_header_when_token_unset(self):
        headers = build_service_auth_headers(trace_id="t1")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["X-Sage-Trace-Id"], "t1")

    def test_custom_token_header_name_supported(self):
        with patch.dict(
            os.environ,
            {
                "MULTIMODAL_SERVICE_TOKEN": "tok",
                "MULTIMODAL_SERVICE_TOKEN_HEADER": "X-Service-Token",
            },
            clear=False,
        ):
            headers = build_service_auth_headers()
        self.assertEqual(headers["X-Service-Token"], "tok")
        self.assertNotIn("Authorization", headers)


class BrowserHeaderForwardingTests(unittest.TestCase):
    def test_browser_auth_and_cookie_never_forwarded(self):
        filtered = filter_multimodal_proxy_headers(
            {
                "Authorization": "Bearer browser-token",
                "Cookie": "session=abc",
                "Host": "sage-app",
                "X-Trace-Id": "client-trace",
                "Content-Type": "application/json",
            }
        )
        self.assertNotIn("Authorization", filtered)
        self.assertNotIn("Cookie", filtered)
        self.assertNotIn("Host", filtered)
        self.assertIn("Content-Type", filtered)
        self.assertIn("X-Trace-Id", filtered)


class RedactedLogTests(unittest.TestCase):
    def test_redacted_error_format_contains_no_secrets(self):
        message = format_redacted_upstream_error(
            trace_id="aabbccdd",
            endpoint="index/search",
            status=502,
            duration_ms=123.45,
            exc_type="ConnectTimeout",
        )
        self.assertIn("trace=aabbccdd", message)
        self.assertIn("endpoint=index/search", message)
        self.assertIn("status=502", message)
        self.assertIn("type=ConnectTimeout", message)
        # 不包含正文/查询/Tokey/API Key 的占位内容
        self.assertNotIn("svc-secret-token", message)
        self.assertNotIn("井身结构", message)


if __name__ == "__main__":
    unittest.main()
