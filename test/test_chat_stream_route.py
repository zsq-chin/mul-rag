from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import BackgroundTasks

from server.services.concurrency import BoundedGate


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
        from server.routers import chat_router

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
