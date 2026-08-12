"""阶段 B1：消除 SSRF 与客户端配置注入的回归测试。

每个测试先用可复现的攻击输入（meta 伪造 base_url / 内网地址 / 异常超时 /
超大 top_k / 恶意 kbId）验证服务端只访问配置中的固定远端、非法字段被拒绝
或忽略。改实现前这些测试必须能复现问题（即当前实现下失败）。
"""

import json
import os
import unittest
from unittest.mock import Mock, patch

from server.utils.multimodal_remote import (
    MultimodalConfigError,
    get_multimodal_api_base,
    search_multimodal_remote,
)

FIXED_BASE = "https://fixed-remote.example/api/v1"
METADATA_URL = "http://169.254.169.254/latest/meta-data/"
LOOPBACK_URL = "http://127.0.0.1:8002/api/v1"


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
    r.json.return_value = {
        "ok": True,
        "results": [
            {
                "id": 1,
                "score": 0.8,
                "entity_key": "file-1",
                "source": json.dumps({"file_id": "file-1", "page": 3}),
                "chunk_text": "井身结构设计关键内容",
            }
        ],
    }
    return r


class BaseUrlInjectionTests(unittest.TestCase):
    def test_meta_multimodal_api_base_is_ignored(self):
        """用户伪造 multimodal_api_base 指向云元数据地址，请求仍只发往固定远端。"""
        with patch.dict(
            os.environ, {"MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE}, clear=False
        ):
            with patch("server.utils.multimodal_remote.requests.get") as mock_get, patch(
                "server.utils.multimodal_remote.requests.post"
            ) as mock_post:
                mock_get.return_value = _kb_list_response()
                mock_post.return_value = _search_ok_response()

                result = search_multimodal_remote(
                    "井身结构设计",
                    {
                        "multimodal_api_base": METADATA_URL,
                        "multimodal_kb_id": "kb-1",
                    },
                )

        # kb_id 由用户提供，不触发 kb/list 探测；只有 index/search 发往固定远端
        mock_get.assert_not_called()
        mock_post.assert_called_once()
        post_url = mock_post.call_args.args[0]
        self.assertTrue(post_url.startswith(FIXED_BASE))
        self.assertNotIn("169.254.169.254", post_url)
        self.assertEqual(result["status"], "ok")

    def test_meta_loopback_base_url_is_ignored(self):
        """伪造 base_url 指向本机回环地址也不影响请求目标。"""
        with patch.dict(os.environ, {"MULTIMODAL_KB_API_BASE": FIXED_BASE}, clear=False):
            with patch("server.utils.multimodal_remote.requests.get") as mock_get, patch(
                "server.utils.multimodal_remote.requests.post"
            ) as mock_post:
                mock_get.return_value = _kb_list_response()
                mock_post.return_value = _search_ok_response()

                search_multimodal_remote("q", {"multimodal_api_base": LOOPBACK_URL, "multimodal_kb_id": "kb-1"})

        post_url = mock_post.call_args.args[0]
        self.assertTrue(post_url.startswith(FIXED_BASE))
        self.assertNotIn("127.0.0.1", post_url)


class ConfigInjectionTests(unittest.TestCase):
    def test_meta_cannot_override_timeout(self):
        """meta 里的异常超时（-1 / 9999 / 非数字）不能覆盖服务端超时。"""
        with patch.dict(
            os.environ,
            {"MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE, "MULTIMODAL_KB_TIMEOUT": "30"},
            clear=False,
        ):
            with patch("server.utils.multimodal_remote.requests.get") as mock_get, patch(
                "server.utils.multimodal_remote.requests.post"
            ) as mock_post:
                mock_get.return_value = _kb_list_response()
                mock_post.return_value = _search_ok_response()

                search_multimodal_remote(
                    "q",
                    {
                        "multimodal_kb_id": "kb-1",
                        "multimodal_timeout": -1,
                    },
                )

        mock_get.assert_not_called()
        self.assertEqual(mock_post.call_args.kwargs["timeout"], 30)

    def test_meta_top_k_clamped_to_1_20(self):
        """meta 超大/超小 top_k 被钳制到 1..20。"""
        with patch.dict(os.environ, {"MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE}, clear=False):
            with patch("server.utils.multimodal_remote.requests.get") as mock_get, patch(
                "server.utils.multimodal_remote.requests.post"
            ) as mock_post:
                mock_get.return_value = _kb_list_response()
                mock_post.return_value = _search_ok_response()

                search_multimodal_remote("q", {"multimodal_kb_id": "kb-1", "multimodal_top_k": 99})
                body = mock_post.call_args.kwargs["json"]
                self.assertEqual(body["k"], 20)

        with patch.dict(os.environ, {"MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE}, clear=False):
            with patch("server.utils.multimodal_remote.requests.get") as mock_get, patch(
                "server.utils.multimodal_remote.requests.post"
            ) as mock_post:
                mock_get.return_value = _kb_list_response()
                mock_post.return_value = _search_ok_response()

                search_multimodal_remote("q", {"multimodal_kb_id": "kb-1", "multimodal_top_k": -5})
                body = mock_post.call_args.kwargs["json"]
                self.assertEqual(body["k"], 1)

    def test_malicious_kb_id_rejected_without_network(self):
        """kbId 带绝对路径/控制字符/URL 时拒绝检索，不发网络请求。"""
        for bad_kb in ("/etc/passwd", "https://evil.example/api", "..\\..\\secret", "kb\x00id"):
            with patch.dict(os.environ, {"MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE}, clear=False):
                with patch("server.utils.multimodal_remote.requests.get") as mock_get, patch(
                    "server.utils.multimodal_remote.requests.post"
                ) as mock_post:
                    result = search_multimodal_remote("q", {"multimodal_kb_id": bad_kb})
            mock_get.assert_not_called()
            mock_post.assert_not_called()
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["results"], [])


class ResultLeakTests(unittest.TestCase):
    def test_result_does_not_expose_base_url_or_raw(self):
        """检索结果不包含 base_url、远端绝对路径或原始上游响应 raw。"""
        with patch.dict(os.environ, {"MULTIMODAL_REMOTE_BASE_URL": FIXED_BASE}, clear=False):
            with patch("server.utils.multimodal_remote.requests.get") as mock_get, patch(
                "server.utils.multimodal_remote.requests.post"
            ) as mock_post:
                mock_get.return_value = _kb_list_response()
                mock_post.return_value = _search_ok_response()

                result = search_multimodal_remote("q", {"multimodal_kb_id": "kb-1"})

        self.assertNotIn("base_url", result)
        self.assertNotIn("raw", result)
        for item in result["results"]:
            self.assertNotIn("raw", item)


class BaseUrlValidationTests(unittest.TestCase):
    def _clear_env(self):
        return patch.dict(
            os.environ,
            {
                "MULTIMODAL_REMOTE_BASE_URL": "",
                "MULTIMODAL_KB_API_BASE": "",
                "MULTIMODAL_ALLOW_HTTP": "",
            },
            clear=True,
        )

    def test_http_rejected_unless_explicit_allow(self):
        with self._clear_env():
            os.environ["MULTIMODAL_REMOTE_BASE_URL"] = "http://10.0.0.5:8002/api/v1"
            with self.assertRaises(MultimodalConfigError):
                get_multimodal_api_base()

    def test_https_accepted_by_default(self):
        with self._clear_env():
            os.environ["MULTIMODAL_REMOTE_BASE_URL"] = "https://10.0.0.5:8002/api/v1"
            self.assertEqual(get_multimodal_api_base(), "https://10.0.0.5:8002/api/v1")

    def test_http_accepted_when_explicitly_allowed(self):
        with self._clear_env():
            os.environ["MULTIMODAL_REMOTE_BASE_URL"] = "http://10.0.0.5:8002/api/v1"
            os.environ["MULTIMODAL_ALLOW_HTTP"] = "1"
            self.assertEqual(get_multimodal_api_base(), "http://10.0.0.5:8002/api/v1")

    def test_wrong_prefix_rejected(self):
        with self._clear_env():
            os.environ["MULTIMODAL_REMOTE_BASE_URL"] = "https://10.0.0.5:8002/other"
            with self.assertRaises(MultimodalConfigError):
                get_multimodal_api_base()
        with self._clear_env():
            os.environ["MULTIMODAL_REMOTE_BASE_URL"] = "https://10.0.0.5:8002/v1"
            with self.assertRaises(MultimodalConfigError):
                get_multimodal_api_base()

    def test_credentials_rejected(self):
        with self._clear_env():
            os.environ["MULTIMODAL_REMOTE_BASE_URL"] = "https://user:pass@10.0.0.5:8002/api/v1"
            with self.assertRaises(MultimodalConfigError):
                get_multimodal_api_base()

    def test_query_fragment_rejected(self):
        with self._clear_env():
            os.environ["MULTIMODAL_REMOTE_BASE_URL"] = "https://10.0.0.5:8002/api/v1?x=1"
            with self.assertRaises(MultimodalConfigError):
                get_multimodal_api_base()

    def test_invalid_port_rejected(self):
        with self._clear_env():
            os.environ["MULTIMODAL_REMOTE_BASE_URL"] = "https://10.0.0.5:99999/api/v1"
            with self.assertRaises(MultimodalConfigError):
                get_multimodal_api_base()

    def test_unset_returns_none(self):
        with self._clear_env():
            self.assertIsNone(get_multimodal_api_base())


if __name__ == "__main__":
    unittest.main()
