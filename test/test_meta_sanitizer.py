"""6.2.4#7：聊天 meta 日志脱敏（redact_meta_for_log）真实函数单元测试。

meta 由客户端提交，可能携带密钥字段；打日志前必须脱敏，日志/采集不得出现明文 Key。
本模块直接调用 server.utils.meta_sanitizer.redact_meta_for_log 真实实现。
"""

import unittest

from server.utils.meta_sanitizer import redact_meta_for_log


class MetaSanitizerTests(unittest.TestCase):
    def test_sensitive_keys_are_redacted_case_insensitively(self):
        meta = {
            "api_key": "sk-plain-1234",
            "apiKey": "sk-plain-5678",
            "api_base": "https://internal.example.com",
            "encrypted_api_key": "gAAAA...",
            "token": "abc",
            "user_model_id": 3,
            "history_round": 20,
        }
        out = redact_meta_for_log(meta)
        self.assertEqual(out["api_key"], "***")
        self.assertEqual(out["apiKey"], "***")
        self.assertEqual(out["api_base"], "***")
        self.assertEqual(out["encrypted_api_key"], "***")
        self.assertEqual(out["token"], "***")
        # 普通字段保持原值
        self.assertEqual(out["user_model_id"], 3)
        self.assertEqual(out["history_round"], 20)
        # 输出整体不含任何明文密钥
        self.assertNotIn("sk-plain-1234", repr(out))
        self.assertNotIn("sk-plain-5678", repr(out))
        self.assertNotIn("internal.example.com", repr(out))

    def test_ordinary_fields_are_untouched(self):
        out = redact_meta_for_log({"selectedKB": 1, "use_web": False, "db_id": "kb-1"})
        self.assertEqual(out, {"selectedKB": 1, "use_web": False, "db_id": "kb-1"})

    def test_non_dict_input_returns_empty_dict(self):
        self.assertEqual(redact_meta_for_log(None), {})
        self.assertEqual(redact_meta_for_log("nope"), {})
        self.assertEqual(redact_meta_for_log(42), {})

    def test_empty_meta_is_unchanged(self):
        self.assertEqual(redact_meta_for_log({}), {})


if __name__ == "__main__":
    unittest.main()
