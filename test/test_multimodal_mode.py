"""阶段 I1：显式 MULTIMODAL_ENABLED / MULTIMODAL_MODE 模式控制测试。

覆盖 CLAUDE_PRODUCTION_RELEASE_MODIFICATION_REQUIREMENTS.md §10 I1：
- I1.1 显式开关与 remote|local 模式；生产 remote，local 仅调试；
- I1.5 启动/日志脱敏：sanitize_base_url_for_log 只保留 scheme://host[:port]。
"""

import os
import unittest
from pathlib import Path
from unittest import mock

from server.utils import multimodal_remote as mm


class MultimodalModeTests(unittest.TestCase):
    def test_mode_defaults_to_remote(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(mm.get_multimodal_mode(), "remote")

    def test_mode_local_and_remote(self):
        with mock.patch.dict(os.environ, {"MULTIMODAL_MODE": "local"}, clear=True):
            self.assertEqual(mm.get_multimodal_mode(), "local")
        with mock.patch.dict(os.environ, {"MULTIMODAL_MODE": "REMOTE"}, clear=True):
            self.assertEqual(mm.get_multimodal_mode(), "remote")

    def test_mode_invalid_falls_back_to_remote(self):
        with mock.patch.dict(os.environ, {"MULTIMODAL_MODE": "bogus"}, clear=True):
            self.assertEqual(mm.get_multimodal_mode(), "remote")

    def test_enabled_explicit_values(self):
        for truthy in ("1", "true", "yes", "on", "TRUE"):
            with mock.patch.dict(os.environ, {"MULTIMODAL_ENABLED": truthy}, clear=True):
                self.assertTrue(mm.is_multimodal_enabled(), truthy)
        for falsy in ("0", "false", "no", "off", "FALSE"):
            with mock.patch.dict(os.environ, {"MULTIMODAL_ENABLED": falsy}, clear=True):
                self.assertFalse(mm.is_multimodal_enabled(), falsy)

    def test_disabled_when_unset_defaults_off(self):
        # 4.1.1：未设置/为空 → 默认关闭，不得无条件解释为 True
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(mm.is_multimodal_enabled())
        with mock.patch.dict(os.environ, {"MULTIMODAL_ENABLED": ""}, clear=True):
            self.assertFalse(mm.is_multimodal_enabled())

    def test_unset_enabled_returns_none_even_with_base(self):
        # 4.1.1/4.1.2：未显式启用时即使配置了 Base URL 也不读取/校验/连接远端
        env = {"MULTIMODAL_KB_API_BASE": "https://mm.example.com/api/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(mm.get_multimodal_api_base())

    def test_remote_mode_uses_env_base(self):
        env = {
            "MULTIMODAL_ENABLED": "true",
            "MULTIMODAL_MODE": "remote",
            "MULTIMODAL_KB_API_BASE": "https://mm.example.com/api/v1",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(mm.get_multimodal_api_base(), "https://mm.example.com/api/v1")

    def test_explicit_disabled_returns_none_even_with_base(self):
        env = {
            "MULTIMODAL_ENABLED": "false",
            "MULTIMODAL_KB_API_BASE": "https://mm.example.com/api/v1",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(mm.get_multimodal_api_base(), "显式关闭时即使配了 Base URL 也不生效")

    def test_local_mode_uses_local_base(self):
        env = {"MULTIMODAL_ENABLED": "true", "MULTIMODAL_MODE": "local"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(mm.get_multimodal_api_base(), "http://127.0.0.1:8002/api/v1")

    def test_local_mode_custom_base_url(self):
        env = {
            "MULTIMODAL_ENABLED": "true",
            "MULTIMODAL_MODE": "local",
            "MULTIMODAL_LOCAL_BASE_URL": "http://127.0.0.1:9000/api/v1",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(mm.get_multimodal_api_base(), "http://127.0.0.1:9000/api/v1")

    def test_local_mode_accepts_compose_service_name(self):
        """local-multimodal profile 下 API 容器经 compose 网络访问 rag-backend。"""
        env = {
            "MULTIMODAL_ENABLED": "true",
            "MULTIMODAL_MODE": "local",
            "MULTIMODAL_LOCAL_BASE_URL": "http://rag-backend:8002/api/v1",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(mm.get_multimodal_api_base(), "http://rag-backend:8002/api/v1")

    def test_remote_http_still_requires_allow_flag(self):
        """SSRF 边界不被 I1 削弱：remote 模式 http 仍须显式 MULTIMODAL_ALLOW_HTTP=1。"""
        env = {
            "MULTIMODAL_ENABLED": "true",
            "MULTIMODAL_MODE": "remote",
            "MULTIMODAL_KB_API_BASE": "http://10.0.0.5/api/v1",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(mm.MultimodalConfigError):
                mm.get_multimodal_api_base()
        env["MULTIMODAL_ALLOW_HTTP"] = "1"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(mm.get_multimodal_api_base(), "http://10.0.0.5/api/v1")

    def test_local_mode_rejects_non_http_scheme(self):
        env = {
            "MULTIMODAL_ENABLED": "true",
            "MULTIMODAL_MODE": "local",
            "MULTIMODAL_LOCAL_BASE_URL": "ftp://127.0.0.1:8002/api/v1",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(mm.MultimodalConfigError):
                mm.get_multimodal_api_base()

    def test_local_mode_rejects_userinfo_and_bad_path(self):
        for bad in (
            "http://user:pass@127.0.0.1:8002/api/v1",
            "http://127.0.0.1:8002/other/prefix",
            "http://127.0.0.1:8002/api/v1?x=1",
        ):
            env = {
                "MULTIMODAL_ENABLED": "true",
                "MULTIMODAL_MODE": "local",
                "MULTIMODAL_LOCAL_BASE_URL": bad,
            }
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(mm.MultimodalConfigError, msg=bad):
                    mm.get_multimodal_api_base()

    def test_remote_without_base_returns_none(self):
        env = {"MULTIMODAL_ENABLED": "true", "MULTIMODAL_MODE": "remote"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsNone(mm.get_multimodal_api_base())

    def test_sanitize_log_only_scheme_host_port(self):
        cases = [
            ("https://mm.example.com/api/v1", "https://mm.example.com"),
            ("http://10.0.0.5:8002/api/v1", "http://10.0.0.5:8002"),
            ("", "(未配置)"),
        ]
        for raw, expected in cases:
            self.assertEqual(mm.sanitize_base_url_for_log(raw), expected)

    def test_sanitize_log_never_leaks_token(self):
        self.assertEqual(
            mm.sanitize_base_url_for_log("https://user:secret@mm.example.com:8443/api/v1?x=1"),
            "https://mm.example.com:8443",
        )


class MainStartupBannerSourceTest(unittest.TestCase):
    """I1.5 启动横幅 source-level 断言（沿用 test_concurrency 对 main.py 的既定模式）。

    启动日志只允许出现「多模态已启用 mode=… target=<脱敏标识>」，绝不允许把完整
    Base URL（含路径/Token）写进日志。
    """

    @classmethod
    def setUpClass(cls):
        cls.source = (Path(__file__).resolve().parents[1] / "server" / "main.py").read_text(
            encoding="utf-8"
        )

    def test_banner_uses_sanitized_target_only(self):
        self.assertIn('"多模态已启用 mode=%s target=%s"', self.source)
        self.assertIn("sanitize_base_url_for_log(_multimodal_base)", self.source)

    def test_banner_never_logs_raw_base_url(self):
        # 不允许出现把原始 Base URL 直接传入日志变量的写法（漏打裸 URL 到启动日志）
        self.assertNotIn("logger.info(_multimodal_base", self.source)
        self.assertNotIn("logger.warning(_multimodal_base", self.source)


if __name__ == "__main__":
    unittest.main()
