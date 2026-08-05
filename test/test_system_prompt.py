from __future__ import annotations

import unittest

from src.utils.prompts import get_system_prompt


class SystemPromptTests(unittest.TestCase):
    def test_default_chat_prompt_forbids_role_and_next_turn_leakage(self):
        prompt = get_system_prompt()
        self.assertIn("不要输出 user、assistant、system 等角色标记", prompt)
        self.assertIn("不要模拟下一轮对话", prompt)
        self.assertIn("只输出回答正文", prompt)


if __name__ == "__main__":
    unittest.main()
