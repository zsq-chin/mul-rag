"""CORS 配置解析回归测试（§9.3.1）。

覆盖 server/utils/cors_config.py 的 resolve_cors_config：
1. 未设置/空值/纯空白 → 回退本机开发来源，允许 credentials；
2. 显式来源（逗号分隔）→ 按来源列表返回，允许 credentials；
3. 含 `*` → 不允许 credentials（浏览器 CORS 规范拒绝 "*"+credentials）；
4. 分段去除前后空白、丢弃空段；
5. 逗号分隔后无有效来源 → 回退 ["http://localhost:5173"]。

本模块纯函数直导，无框架依赖。
"""

import unittest

from server.utils.cors_config import resolve_cors_config

DEFAULT_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost",
    "http://127.0.0.1",
]


class CorsConfigTests(unittest.TestCase):
    def test_empty_env_falls_back_to_local_dev_origins_with_credentials(self):
        origins, allow_credentials = resolve_cors_config("")
        self.assertEqual(origins, DEFAULT_ORIGINS)
        self.assertTrue(allow_credentials)

    def test_whitespace_only_env_falls_back_to_defaults(self):
        origins, allow_credentials = resolve_cors_config("   ")
        self.assertEqual(origins, DEFAULT_ORIGINS)
        self.assertTrue(allow_credentials)

    def test_explicit_origins_are_returned_with_credentials(self):
        origins, allow_credentials = resolve_cors_config(
            "https://app.example.com,https://admin.example.com"
        )
        self.assertEqual(
            origins, ["https://app.example.com", "https://admin.example.com"]
        )
        self.assertTrue(allow_credentials)

    def test_origins_trim_whitespace_and_drop_empty_segments(self):
        origins, allow_credentials = resolve_cors_config(
            " https://a.example.com , , https://b.example.com "
        )
        self.assertEqual(origins, ["https://a.example.com", "https://b.example.com"])
        self.assertTrue(allow_credentials)

    def test_wildcard_origin_disables_credentials(self):
        origins, allow_credentials = resolve_cors_config("*")
        self.assertEqual(origins, ["*"])
        self.assertFalse(allow_credentials)

    def test_wildcard_among_explicit_origins_still_disables_credentials(self):
        origins, allow_credentials = resolve_cors_config(
            "https://app.example.com,*,http://localhost:5173"
        )
        self.assertIn("*", origins)
        self.assertFalse(allow_credentials)

    def test_split_with_no_valid_origin_falls_back_to_localhost(self):
        origins, allow_credentials = resolve_cors_config(", ,")
        self.assertEqual(origins, ["http://localhost:5173"])
        self.assertTrue(allow_credentials)


if __name__ == "__main__":
    unittest.main()
