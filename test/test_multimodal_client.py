"""阶段 C1：统一多模态远端客户端适配层（MultimodalRemoteClient）回归测试。

验证：
- 检索器侧的同步调用复用应用级连接池（requests.Session），不每次新建连接；
- 应用关闭时同步/异步连接池都被释放（lifespan 调用 close_*）；
- GET 一次带抖动的有限重试；搜索最多一次可控重试；4xx 不重试；
- 未选择知识库时不再自动取远端第一个知识库（无 kb/list 探测请求），
  必须由用户显式选择或使用服务端配置默认库；
- 4xx 上游错误正文中的业务 message（如 INDEX_NOT_FOUND）仍透传。

本测试不 import 任何 router；服务层（http_clients）只依赖 requests，不依赖 src。
"""

import json
import os
import unittest
from unittest.mock import Mock, patch

import requests

from server.services.http_clients import (
    close_multimodal_sync_session,
    get_multimodal_sync_session,
    multimodal_client_lifespan,
)
from server.utils.multimodal_remote import MultimodalRemoteClient, search_multimodal_remote

FIXED_BASE = "https://fixed-remote.example/api/v1"


def _search_ok_response():
    r = Mock()
    r.ok = True
    r.status_code = 200
    r.json.return_value = {
        "ok": True,
        "results": [{"id": 1, "entity_key": "f", "chunk_text": "井身结构设计关键内容"}],
    }
    r.text = '{"ok": true, "results": []}'
    return r


def _search_error_response(status, payload):
    r = Mock()
    r.ok = 200 <= status < 400
    r.status_code = status
    r.json.return_value = payload
    r.text = json.dumps(payload, ensure_ascii=False)
    return r


class _RecordingSession:
    """requests.Session 的最小替身：记录调用并依次弹出预置响应。"""

    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.calls = []
        self.get_calls = 0
        self.post_calls = 0
        self.closed = False

    def _next(self):
        if not self._responses:
            raise AssertionError("没有更多预置响应")
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        self.get_calls += 1
        self.calls.append(("GET", url, kwargs))
        return self._next()

    def post(self, url, **kwargs):
        self.post_calls += 1
        self.calls.append(("POST", url, kwargs))
        return self._next()

    def close(self):
        self.closed = True


class ConnectionRaisingSession(_RecordingSession):
    """前 n 次请求抛连接错误，之后正常返回预置响应。"""

    def __init__(self, responses=None, fail_count=1):
        super().__init__(responses)
        self.fail_count = fail_count

    def _try(self):
        if self.fail_count > 0:
            self.fail_count -= 1
            raise requests.exceptions.ConnectionError("connection refused")
        return self._next()

    def get(self, url, **kwargs):
        self.get_calls += 1
        self.calls.append(("GET", url, kwargs))
        return self._try()

    def post(self, url, **kwargs):
        self.post_calls += 1
        self.calls.append(("POST", url, kwargs))
        return self._try()


class SharedSessionTests(unittest.TestCase):
    def test_default_client_uses_app_level_shared_session(self):
        # C1.2：默认客户端必须复用应用级共享连接池，而不是每次新建连接
        client = MultimodalRemoteClient()
        self.assertIs(client._session_for(), get_multimodal_sync_session())

    def test_injected_session_is_used(self):
        # 测试可注入 session（隔离测试的既定 seams）
        session = _RecordingSession()
        client = MultimodalRemoteClient(session=session, sleep=lambda _: None)
        self.assertIs(client._session_for(), session)


class ShutdownReleaseTests(unittest.TestCase):
    def test_sync_session_closed_and_reset(self):
        # C1.3：close_multimodal_sync_session 关闭共享会话；再次获取得到新会话
        import server.services.http_clients as hc

        session = get_multimodal_sync_session()
        self.assertIsNotNone(session)
        close_multimodal_sync_session()
        self.assertIsNone(hc._multimodal_sync_session)  # 已复位
        fresh = get_multimodal_sync_session()
        self.assertIsNot(fresh, session)  # 原会话已释放，不复用
        close_multimodal_sync_session()

    def test_lifespan_closes_sync_session(self):
        # C1.3：应用关闭时同步/异步连接池都释放
        with patch("server.services.http_clients.close_multimodal_sync_session") as close_sync, patch(
            "server.services.http_clients.close_multimodal_client"
        ) as close_client:
            import asyncio

            async def _run():
                async with multimodal_client_lifespan(None):
                    pass

            asyncio.run(_run())
        close_sync.assert_called_once_with()
        close_client.assert_called_once_with()


class RetryPolicyTests(unittest.TestCase):
    def _env(self, **extra):
        env = {"MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE}
        env.update(extra)
        return patch.dict(os.environ, env, clear=False)

    def test_get_retried_once_with_jitter_on_5xx(self):
        # C1.4：GET 一次带抖动的有限重试（幂等）
        session = _RecordingSession([_search_error_response(503, {"message": "busy"}), _search_ok_response()])
        client = MultimodalRemoteClient(session=session, sleep=lambda _: None)
        with self._env():
            response = client.get(f"{FIXED_BASE}/kb/list")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.get_calls, 2)
        self.assertEqual(client.retries, 1)

    def test_search_retried_once_on_5xx_then_succeeds(self):
        # C1.4：搜索最多一次可控重试；503 后重试成功
        session = _RecordingSession([_search_error_response(503, {"message": "unavailable"}), _search_ok_response()])
        client = MultimodalRemoteClient(session=session, sleep=lambda _: None)
        with self._env():
            result = client.search("q", {"multimodal_kb_id": "kb-1"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(session.post_calls, 2)
        self.assertEqual(client.retries, 1)

    def test_search_retried_once_on_transport_error(self):
        # C1.4：连接错误同样重试一次
        session = ConnectionRaisingSession([_search_ok_response()], fail_count=1)
        client = MultimodalRemoteClient(session=session, sleep=lambda _: None)
        with self._env():
            result = client.search("q", {"multimodal_kb_id": "kb-1"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(session.post_calls, 2)
        self.assertEqual(client.retries, 1)

    def test_search_not_retried_on_4xx(self):
        # C1.4：4xx 是客户端错误，不重试
        session = _RecordingSession([_search_error_response(400, {"error": {"code": "INDEX_NOT_FOUND", "message": "请先构建索引"}})])
        client = MultimodalRemoteClient(session=session, sleep=lambda _: None)
        with self._env():
            result = client.search("q", {"multimodal_kb_id": "kb-1"})
        self.assertEqual(result["status"], "error")
        self.assertEqual(session.post_calls, 1)
        self.assertEqual(client.retries, 0)
        self.assertIn("请先构建索引", result["message"])


class NoAutoPickKbTests(unittest.TestCase):
    def _env(self, **extra):
        env = {"MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE}
        env.update(extra)
        return patch.dict(os.environ, env, clear=False)

    def test_no_kb_selected_returns_distinct_state_without_network(self):
        # C1.5：未选择知识库且无服务端默认库时，返回 no_kb_selected，
        # 不发任何网络请求（不自动取远端第一个知识库）
        session = _RecordingSession()
        client = MultimodalRemoteClient(session=session, sleep=lambda _: None)
        with self._env():
            result = client.search("q", {})
        self.assertEqual(result["status"], "no_kb_selected")
        self.assertEqual(result["results"], [])
        self.assertEqual(session.post_calls, 0)
        self.assertEqual(session.get_calls, 0)

    def test_server_default_kb_id_used(self):
        # C1.5：服务端配置的默认库仍然有效（前端须明确显示）
        session = _RecordingSession([_search_ok_response()])
        client = MultimodalRemoteClient(session=session, sleep=lambda _: None)
        with self._env(MULTIMODAL_KB_DEFAULT_KB_ID="kb-default"):
            result = client.search("q", {})
        self.assertEqual(result["status"], "ok")
        body = session.calls[0][2]["json"]
        self.assertEqual(body["kbId"], "kb-default")

    def test_meta_kb_id_wins_over_env_default(self):
        # 用户显式选择优先于服务端默认库
        session = _RecordingSession([_search_ok_response()])
        client = MultimodalRemoteClient(session=session, sleep=lambda _: None)
        with self._env(MULTIMODAL_KB_DEFAULT_KB_ID="kb-default"):
            result = client.search("q", {"multimodal_kb_id": "kb-meta"})
        self.assertEqual(result["status"], "ok")
        body = session.calls[0][2]["json"]
        self.assertEqual(body["kbId"], "kb-meta")

    def test_malicious_kb_id_rejected_without_network(self):
        # 恶意 kbId 仍被拒绝，不发网络请求
        for bad in ("/etc/passwd", "https://evil.example/api", "..\\..\\secret", "kb\x00id"):
            session = _RecordingSession()
            client = MultimodalRemoteClient(session=session, sleep=lambda _: None)
            with self._env():
                result = client.search("q", {"multimodal_kb_id": bad})
            self.assertEqual(result["status"], "error", bad)
            self.assertEqual(session.post_calls, 0, bad)


class PublicApiCompatTests(unittest.TestCase):
    def test_search_multimodal_remote_delegates_to_shared_client(self):
        # 公开函数仍然可用（检索器、既有测试的 patch 面不变）
        session = _RecordingSession([_search_ok_response()])
        with patch.dict(os.environ, {"MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE}, clear=False):
            with patch("server.utils.multimodal_remote.get_multimodal_sync_session", return_value=session):
                result = search_multimodal_remote("q", {"multimodal_kb_id": "kb-1"})
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
