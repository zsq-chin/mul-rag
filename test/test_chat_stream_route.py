from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import BackgroundTasks

from server.services.concurrency import BoundedGate

ROOT = Path(__file__).resolve().parents[1]


def _install_chat_src_shim() -> None:
    """让 server.routers.chat_router 可在无 Milvus/Neo4j/MySQL 的主机导入。

    chat_router 的 import 链会触发真实 src/__init__.py（实例化 KnowledgeBase →
    Milvus ConnectionError）。这里以轻量 src 桩替换：真实 ThreadPoolExecutor +
    真实 HistoryManager（spec 加载 src/core/history.py），其余运行期不会被
    普通聊天路径触碰的组件（retriever/knowledge_base/graph_base/agent_manager/
    select_model/get_all_tools）给空桩。被测逻辑仍是真实 chat_router。
    """
    if "src" in sys.modules and getattr(sys.modules["src"], "_sage_chat_shim", False):
        return

    class _StubLogger:
        def info(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass
        def warning(self, *args, **kwargs): pass
        def debug(self, *args, **kwargs): pass

    save_dir = tempfile.mkdtemp(prefix="sage-test-save-")
    src = types.ModuleType("src")
    src.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-blocking")
    src.config = types.SimpleNamespace(
        save_dir=save_dir,
        default_agent_id="default",
        model_names=[],
        save=lambda *args, **kwargs: None,
        _save_models_to_file=lambda *args, **kwargs: None,
        get=lambda key, default=None: default,
    )
    src.retriever = types.SimpleNamespace()
    src.knowledge_base = types.SimpleNamespace()
    src.graph_base = types.SimpleNamespace(close=lambda: None)
    src.shutdown_runtime = lambda: None
    src._sage_chat_shim = True
    sys.modules["src"] = src

    utils = types.ModuleType("src.utils")
    utils.logger = _StubLogger()
    sys.modules["src.utils"] = utils
    logging_config = types.ModuleType("src.utils.logging_config")
    logging_config.logger = _StubLogger()
    sys.modules["src.utils.logging_config"] = logging_config
    prompts = types.ModuleType("src.utils.prompts")
    prompts.get_system_prompt = lambda *args, **kwargs: None
    sys.modules["src.utils.prompts"] = prompts

    history_spec = importlib.util.spec_from_file_location(
        "src.core.history", ROOT / "src" / "core" / "history.py"
    )
    history_mod = importlib.util.module_from_spec(history_spec)
    sys.modules["src.core.history"] = history_mod
    history_spec.loader.exec_module(history_mod)
    core = types.ModuleType("src.core")
    core.HistoryManager = history_mod.HistoryManager
    sys.modules["src.core"] = core

    agents = types.ModuleType("src.agents")
    agents.agent_manager = types.SimpleNamespace()
    sys.modules["src.agents"] = agents
    tools_factory = types.ModuleType("src.agents.tools_factory")
    tools_factory.get_all_tools = lambda *args, **kwargs: []
    sys.modules["src.agents.tools_factory"] = tools_factory
    models = types.ModuleType("src.models")
    models.select_model = lambda *args, **kwargs: None
    sys.modules["src.models"] = models

    # 隔离 SQLite 路径，绝不触碰 saves/data/server.db
    os.environ["SAGE_DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="sage-test-db-"), "srv.db"
    )


# 在导入被测路由之前安装桩；聊天流是被测真实逻辑，桩只满足 import 期名称。
_install_chat_src_shim()

chat_spec = importlib.util.spec_from_file_location(
    "chat_router_under_test", ROOT / "server" / "routers" / "chat_router.py"
)
chat_router = importlib.util.module_from_spec(chat_spec)
sys.modules["chat_router_under_test"] = chat_router
chat_spec.loader.exec_module(chat_router)


class _FakeModel:
    model_name = "test-model"

    def predict(self, message, stream=False):
        if stream:
            chunks = [
                "钻采工程的主要业务范围包括钻井工程和采油工程。",
                "\n\n1",
                "\nuser\n",
                "钻井工程具体包括哪些内容？",
            ]
            return iter(SimpleNamespace(content=chunk) for chunk in chunks)

        return SimpleNamespace(
            content=(
                "-钻井工程\n"
                "-井下作业\n"
                "-井筒技术\n"
                "-完井与修井技术"
            )
        )


class _FullSnapshotModel(_FakeModel):
    def predict(self, message, stream=False):
        if stream:
            prefix = "A" * 80
            return iter(
                [
                    SimpleNamespace(content=prefix + "old", is_full=True),
                    SimpleNamespace(content=prefix + "new", is_full=True),
                ]
            )
        return SimpleNamespace(content="1. 后续问题是什么？")


class _FailingStreamModel(_FakeModel):
    def predict(self, message, stream=False):
        if stream:
            def generate():
                yield SimpleNamespace(content="短回答。\n")
                raise RuntimeError("stream failed")

            return generate()
        return super().predict(message, stream=stream)


class _PartialMarkerFailingModel(_FakeModel):
    def predict(self, message, stream=False):
        if stream:
            def generate():
                yield SimpleNamespace(content="回答。\n\n1\nus")
                raise RuntimeError("stream failed")

            return generate()
        return super().predict(message, stream=stream)


class _MixedSnapshotModel(_FakeModel):
    def predict(self, message, stream=False):
        if stream:
            return iter(
                [
                    SimpleNamespace(content="abc"),
                    SimpleNamespace(content="abcdef", is_full=True),
                ]
            )
        return SimpleNamespace(content="1. 后续问题是什么？")


class _FusedArtifactModel(_FakeModel):
    def predict(self, message, stream=False):
        if stream:
            return iter(
                [
                    SimpleNamespace(content="回答。\n\n1\nuser"),
                    SimpleNamespace(content="edm通信钻井、完井和测井技术服务。"),
                ]
            )
        return SimpleNamespace(content="1. 通信钻井技术如何工作？")


class _ChineseColonLeakModel(_FakeModel):
    """模型在数字后使用中文冒号的泄漏模式：\\n\\n1：\\nuser\\n下一问题"""
    def predict(self, message, stream=False):
        if stream:
            chunks = [
                "钻采工程的主要业务范围包括钻井工程和采油工程。",
                "\n\n1：",
                "\nuser\n",
                "下一问题",
            ]
            return iter(SimpleNamespace(content=chunk) for chunk in chunks)
        return SimpleNamespace(content="-钻井工程\n-井下作业\n-井筒技术")


class _AsciiColonLeakModel(_FakeModel):
    """模型在数字后使用 ASCII 冒号的泄漏模式：\\n\\n1:\\nuser\\n下一问题"""
    def predict(self, message, stream=False):
        if stream:
            chunks = [
                "正文回答。",
                "\n\n1:",
                "\nuser\n",
                "钻井工程包括哪些内容？",
            ]
            return iter(SimpleNamespace(content=chunk) for chunk in chunks)
        return SimpleNamespace(content="-钻井工程\n-井下作业")


class ChatStreamRouteTests(unittest.IsolatedAsyncioTestCase):
    async def _collect_chunks(self, model):
        gate = BoundedGate("chat-output-test", limit=1, acquire_timeout=0.1)
        with (
            patch.object(chat_router, "chat_gate", gate),
            patch.object(chat_router, "resolve_model_for_user", return_value=model),
            patch.object(chat_router, "assert_chat_features_allowed"),
        ):
            response = await chat_router.chat_post(
                query="钻采工程业务范围？",
                meta={"history_round": 20},
                history=[],
                db=object(),
                current_user=SimpleNamespace(id=1),
                background_tasks=BackgroundTasks(),
            )
            chunks = [
                json.loads(chunk)
                async for chunk in response.body_iterator
            ]
        return chunks

    @staticmethod
    def _render_answer(chunks):
        answer = ""
        for chunk in chunks:
            response = chunk.get("response") or ""
            if chunk.get("replace_content"):
                answer = response
            else:
                answer += response
        return answer

    async def test_chat_stream_removes_leaked_turn_and_returns_recommendations(self):
        chunks = await self._collect_chunks(_FakeModel())
        answer = self._render_answer(chunks)
        self.assertEqual(
            answer,
            "钻采工程的主要业务范围包括钻井工程和采油工程。",
        )
        self.assertNotIn("\nuser\n", answer)
        self.assertNotIn("钻井工程具体包括哪些内容？", answer)

        finished = next(chunk for chunk in chunks if chunk.get("status") == "finished")
        self.assertEqual(
            finished["related_questions"],
            [
                "钻井工程包括哪些内容？",
                "井下作业包括哪些内容？",
                "井筒技术包括哪些内容？",
            ],
        )
        self.assertEqual(
            finished["history"][-1],
            {
                "role": "assistant",
                "content": "钻采工程的主要业务范围包括钻井工程和采油工程。",
            },
        )

    async def test_full_snapshot_stream_uses_only_latest_snapshot(self):
        chunks = await self._collect_chunks(_FullSnapshotModel())
        answer = self._render_answer(chunks)
        self.assertEqual(answer, ("A" * 80) + "new")
        loading = [
            chunk for chunk in chunks
            if chunk.get("status") == "loading" and chunk.get("response")
        ]
        self.assertGreaterEqual(len(loading), 2)
        self.assertEqual(loading[0]["response"], ("A" * 80) + "old")
        self.assertTrue(loading[1]["replace_content"])
        finished = next(chunk for chunk in chunks if chunk.get("status") == "finished")
        self.assertEqual(finished["history"][-1]["content"], ("A" * 80) + "new")

    async def test_stream_error_flushes_safe_pending_content(self):
        chunks = await self._collect_chunks(_FailingStreamModel())
        answer = self._render_answer(chunks)
        self.assertEqual(answer, "短回答。\n")
        self.assertEqual(chunks[-1]["status"], "error")

    async def test_stream_error_discards_partial_marker(self):
        chunks = await self._collect_chunks(_PartialMarkerFailingModel())
        answer = self._render_answer(chunks)
        self.assertEqual(answer, "回答。")
        self.assertNotIn("\n\n1\nus", answer)
        self.assertEqual(chunks[-1]["status"], "error")

    async def test_mixed_incremental_and_full_snapshot_does_not_duplicate(self):
        chunks = await self._collect_chunks(_MixedSnapshotModel())
        answer = self._render_answer(chunks)
        self.assertEqual(answer, "abcdef")
        finished = next(chunk for chunk in chunks if chunk.get("status") == "finished")
        self.assertEqual(finished["history"][-1]["content"], "abcdef")

    async def test_fused_role_artifact_is_cleaned_in_answer_and_history(self):
        chunks = await self._collect_chunks(_FusedArtifactModel())
        answer = self._render_answer(chunks)
        expected = "回答。\n\n1. 通信钻井、完井和测井技术服务。"
        self.assertEqual(answer, expected)
        finished = next(chunk for chunk in chunks if chunk.get("status") == "finished")
        self.assertEqual(finished["history"][-1]["content"], expected)

    async def test_chinese_colon_leak_is_removed(self):
        """中文冒号分隔的泄漏标记必须被清除。"""
        chunks = await self._collect_chunks(_ChineseColonLeakModel())
        answer = self._render_answer(chunks)
        self.assertEqual(
            answer,
            "钻采工程的主要业务范围包括钻井工程和采油工程。",
        )
        self.assertNotIn("user", answer)
        self.assertNotIn("下一问题", answer)

    async def test_ascii_colon_leak_is_removed(self):
        """ASCII 冒号分隔的泄漏标记必须被清除。"""
        chunks = await self._collect_chunks(_AsciiColonLeakModel())
        answer = self._render_answer(chunks)
        self.assertEqual(answer, "正文回答。")
        self.assertNotIn("user", answer)


if __name__ == "__main__":
    unittest.main()
