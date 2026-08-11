from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# src.utils.prompts is pure stdlib, but importing it through the real ``src``
# package triggers Milvus initialisation (src/__init__.py), which is not
# available on the host. Load the module file in isolation under stub
# ``src`` / ``src.utils`` parents, then restore the global module table so
# this file cannot leak fakes into later test modules (review P2-2).


def _load_prompts_module():
    saved = {}
    for name in ("src", "src.utils"):
        saved[name] = sys.modules.get(name, None)
        pkg = types.ModuleType(name)
        pkg.__path__ = [] if name == "src.utils" else [_PROJECT_ROOT / "src"]
        sys.modules[name] = pkg
    spec = importlib.util.spec_from_file_location(
        "src.utils.prompts", _PROJECT_ROOT / "src" / "utils" / "prompts.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["src.utils.prompts"] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        for name in ("src", "src.utils"):
            if saved[name] is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = saved[name]
    return mod


_prompts = _load_prompts_module()
get_system_prompt = _prompts.get_system_prompt


class SystemPromptTests(unittest.TestCase):
    def test_default_chat_prompt_forbids_role_and_next_turn_leakage(self):
        prompt = get_system_prompt()
        self.assertIn("不要输出 user、assistant、system 等角色标记", prompt)
        self.assertIn("不要模拟下一轮对话", prompt)
        self.assertIn("只输出回答正文", prompt)


if __name__ == "__main__":
    unittest.main()
