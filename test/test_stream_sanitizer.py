"""
TDD RED 阶段测试 — 流式内容清理器 & 推荐问题解析器

目标 API：
  - ChatStreamSanitizer(feed / finish 逐字符流式接口)
  - parse_related_questions(raw: str) -> list[str]

运行命令：
  docker compose run --rm --no-deps -v ${PWD}:/workspace -w /workspace \
    api /app/.venv/bin/python -m unittest test.test_stream_sanitizer -v
"""

from __future__ import annotations

import unittest

import server.utils.stream_sanitizer as stream_sanitizer
from server.utils.stream_sanitizer import ChatStreamSanitizer, parse_related_questions


# ── 泄漏样本 ──────────────────────────────────────────────────────────────

_LEAKAGE_TAIL = "\n\n1\nuser\n钻井工程具体包括哪些内容？"
_MULTI_TURN_LEAKAGE = (
    "\n\n1\nuser\n问题一\n2\nassistant\n回答二\n3\nuser\n问题三"
)


def _feed_chunks(sanitizer: ChatStreamSanitizer, text: str, chunk_size: int = 3) -> str:
    """按 chunk_size 逐块 feed，拼接每次输出 + finish 输出。"""
    parts: list[str] = []
    for i in range(0, len(text), chunk_size):
        out = sanitizer.feed(text[i : i + chunk_size])
        if out:
            parts.append(out)
    tail = sanitizer.finish()
    if tail:
        parts.append(tail)
    return "".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# 流式清理器测试
# ═══════════════════════════════════════════════════════════════════════════

class TestChatStreamSanitizerLeakage(unittest.TestCase):
    """ChatStreamSanitizer 在流式 feed 中不应泄漏模板标记。"""

    def _assert_no_leakage(self, full_text: str, expected_clean: str, chunk_size: int = 3):
        san = ChatStreamSanitizer()
        result = _feed_chunks(san, full_text, chunk_size=chunk_size)
        self.assertEqual(result, expected_clean,
                         f"chunk_size={chunk_size}: 泄漏未清理，得到 {result!r}")

    # ── 标准泄漏：\n\n1\nuser\n... ────────────────────────────────────────

    def test_standard_leakage_char_by_char(self):
        """逐字符 feed，泄漏标记跨 chunk 边界时必须全部清除。"""
        raw = "钻井工程主要包括：\n1. 设计\n2. 施工" + _LEAKAGE_TAIL
        self._assert_no_leakage(raw, "钻井工程主要包括：\n1. 设计\n2. 施工", chunk_size=1)

    def test_standard_leakage_small_chunks(self):
        raw = "回答内容。" + _LEAKAGE_TAIL
        self._assert_no_leakage(raw, "回答内容。", chunk_size=3)

    def test_standard_leakage_single_chunk(self):
        """一次性收到全部内容也应清理。"""
        raw = "完整回答。" + _LEAKAGE_TAIL
        self._assert_no_leakage(raw, "完整回答。", chunk_size=9999)

    # ── 多轮对话泄漏 ──────────────────────────────────────────────────────

    def test_multi_turn_leakage_streaming(self):
        raw = "回答。" + _MULTI_TURN_LEAKAGE
        self._assert_no_leakage(raw, "回答。", chunk_size=2)

    # ── 无泄漏时内容完整保留 ──────────────────────────────────────────────

    def test_no_leakage_preserves_every_char(self):
        """正常回答逐字 feed，每字都必须出现在输出中。"""
        original = "钻井工程主要包括以下内容：\n\n1. 钻井设计\n2. 钻井液技术\n3. 固井作业。"
        san = ChatStreamSanitizer()
        parts = [san.feed(c) for c in original]
        parts.append(san.finish())
        result = "".join(parts)
        self.assertEqual(result, original,
                         f"正常内容被错误截断: {result!r}")

    # ── 泄漏 \n\n 恰好在 chunk 边界 ──────────────────────────────────────

    def test_leakage_split_at_boundary(self):
        """\\n\\n 恰好被切成两个 chunk，清理器必须跨边界识别。"""
        raw = "回答正文。\n\n1\nuser\n追问？"
        # chunk 边界恰好在 \n 和 \n 之间
        san = ChatStreamSanitizer()
        out1 = san.feed("回答正文。\n")
        out2 = san.feed("\n1\n")
        out3 = san.feed("user\n")
        out4 = san.feed("追问？")
        tail = san.finish()
        result = "".join(filter(None, [out1, out2, out3, out4, tail]))
        self.assertEqual(result, "回答正文。",
                         f"跨边界泄漏未清理: {result!r}")

    # ── 正常列表中的数字不应被误杀 ────────────────────────────────────────

    def test_numbered_list_preserved(self):
        """正文含 '1.' 开头的列表项，不得被当作泄漏移除。"""
        original = "步骤如下：\n1. 准备材料\n2. 开始施工\n3. 验收。"
        self._assert_no_leakage(original, original, chunk_size=5)

    def test_long_whitespace_inside_marker_is_not_emitted(self):
        raw = "回答。" + "\n\n1\n" + (" " * 80) + "user\n追问？"
        self._assert_no_leakage(raw, "回答。", chunk_size=7)

    def test_arbitrarily_long_whitespace_does_not_bypass_marker(self):
        san = ChatStreamSanitizer()
        parts = [san.feed("回答。\n\n1\n")]
        for _ in range(100):
            parts.append(san.feed(" " * 100))
            self.assertLessEqual(san.buffered_size, 512)
        parts.append(san.feed("user\n追问？"))
        parts.append(san.finish())
        self.assertEqual("".join(parts), "回答。")

    def test_crlf_marker_is_not_emitted(self):
        raw = "回答。\r\n\r\n1\r\nuser\r\n追问？"
        self._assert_no_leakage(raw, "回答。", chunk_size=2)

    def test_fullwidth_role_colon_is_not_emitted(self):
        raw = "回答。\n\n1\nuser：\n追问？"
        self._assert_no_leakage(raw, "回答。", chunk_size=2)

    # ── 中文冒号 / ASCII 冒号在数字后 ────────────────────────────────────

    def test_fullwidth_colon_after_number_is_not_emitted(self):
        """\\n\\n1：\\nuser\\n下一问题 — 中文冒号分隔符必须识别并清除。"""
        raw = "回答。\n\n1：\nuser\n下一问题"
        self._assert_no_leakage(raw, "回答。", chunk_size=2)

    def test_ascii_colon_after_number_is_not_emitted(self):
        """\\n\\n1:\\nuser\\n下一问题 — ASCII 冒号分隔符必须识别并清除。"""
        raw = "回答。\n\n1:\nuser\n下一问题"
        self._assert_no_leakage(raw, "回答。", chunk_size=2)

    def test_fullwidth_colon_after_number_single_chunk(self):
        """一次性收到含中文冒号的泄漏标记。"""
        raw = "完整回答。\n\n1：\nuser\n下一问题"
        self._assert_no_leakage(raw, "完整回答。", chunk_size=9999)

    def test_fused_role_artifact_with_colon_separator(self):
        """\\n\\n1：useredm通信技术 — 中文冒号 + fused artifact。"""
        raw = "回答。\n\n1：useredm通信钻井技术。"
        self._assert_no_leakage(
            raw,
            "回答。\n\n1. 通信钻井技术。",
            chunk_size=2,
        )

    def test_normal_numbered_list_with_colon_preserved(self):
        """正文含 '1: 第一点\\n2: 第二点' 的列表不得被误杀。"""
        original = "步骤如下：\n1: 准备材料\n2: 开始施工\n3: 验收。"
        self._assert_no_leakage(original, original, chunk_size=5)

    def test_fused_role_artifact_is_removed_but_answer_text_is_kept(self):
        raw = "回答。\n\n1\nuseredm通信钻井、完井和测井技术服务。"
        self._assert_no_leakage(
            raw,
            "回答。\n\n1. 通信钻井、完井和测井技术服务。",
            chunk_size=2,
        )

    def test_role_word_at_chunk_end_does_not_truncate_normal_content(self):
        san = ChatStreamSanitizer()
        parts = [
            san.feed("正文。\n\n1\nuser"),
            san.feed(" experience 是一个术语。"),
            san.finish(),
        ]
        self.assertEqual("".join(parts), "正文。\n\n1\nuser experience 是一个术语。")

    def test_normal_short_chunk_is_emitted_without_holdback(self):
        san = ChatStreamSanitizer()
        self.assertEqual(san.feed("正常回答。"), "正常回答。")
        self.assertEqual(san.finish(), "")

    def test_abort_discards_an_incomplete_leaked_marker(self):
        san = ChatStreamSanitizer()
        parts = [
            san.feed("回答。\n\n1\nus"),
            san.abort(),
        ]
        self.assertEqual("".join(parts), "回答。")

    def test_abort_keeps_a_single_trailing_newline(self):
        san = ChatStreamSanitizer()
        parts = [
            san.feed("短回答。\n"),
            san.abort(),
        ]
        self.assertEqual("".join(parts), "短回答。\n")


# ═══════════════════════════════════════════════════════════════════════════
# 推荐问题解析器测试
# ═══════════════════════════════════════════════════════════════════════════

class TestParseRelatedQuestions(unittest.TestCase):
    """parse_related_questions 应解析多种格式，最多返回 3 个去重问题。"""

    def test_numbered_dot_format(self):
        raw = "1. 钻井成本如何计算？\n2. 钻井有哪些风险？\n3. 钻井周期多长？"
        result = parse_related_questions(raw)
        self.assertEqual(result, ["钻井成本如何计算？", "钻井有哪些风险？", "钻井周期多长？"])

    def test_chinese_comma_numbering(self):
        """中文顿号序号：1、2、"""
        raw = "1、钻井成本如何计算？\n2、钻井有哪些风险？\n3、钻井周期多长？"
        result = parse_related_questions(raw)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "钻井成本如何计算？")

    def test_markdown_unordered_list(self):
        raw = "- 钻井成本如何计算？\n- 钻井有哪些风险？\n- 钻井周期多长？"
        result = parse_related_questions(raw)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], "钻井成本如何计算？")

    def test_short_topic_list_becomes_questions(self):
        raw = "-钻井工程\n -井下作业\n -井筒技术\n -完井与修井技术"
        result = parse_related_questions(raw)
        self.assertEqual(
            result,
            [
                "钻井工程包括哪些内容？",
                "井下作业包括哪些内容？",
                "井筒技术包括哪些内容？",
            ],
        )

    def test_topic_list_with_punctuation_becomes_questions(self):
        raw = "-钻井工程：设计、施工与安全\n-井控风险，处置流程"
        result = parse_related_questions(raw)
        self.assertEqual(
            result,
            [
                "钻井工程包括哪些内容？",
                "井控风险包括哪些内容？",
            ],
        )

    def test_max_three_dedup(self):
        """超过 3 个只取前 3 个，重复项去重。"""
        raw = "1. 钻井成本？\n2. 钻井风险？\n3. 钻井周期？\n4. 钻井成本？\n5. 额外问题？"
        result = parse_related_questions(raw)
        self.assertEqual(result, ["钻井成本？", "钻井风险？", "钻井周期？"])

    def test_standalone_number_and_role_lines_filtered(self):
        """单独的数字行 '1' 和角色行 'user'/'assistant' 不应出现在结果中。"""
        raw = "1\n2\nuser\nassistant\n1. 钻井有哪些风险？"
        result = parse_related_questions(raw)
        self.assertEqual(result, ["钻井有哪些风险？"])

    def test_role_line_with_fullwidth_colon_filtered(self):
        """'user：' (全角冒号) 应被识别为角色行并过滤。"""
        raw = "user：\nassistant：\nsystem：\n1. 钻井有哪些风险？"
        result = parse_related_questions(raw)
        self.assertEqual(result, ["钻井有哪些风险？"])

    def test_empty_input(self):
        self.assertEqual(parse_related_questions(""), [])

    def test_whitespace_only(self):
        self.assertEqual(parse_related_questions("   \n  \n  "), [])

    def test_mixed_format_dedup(self):
        """混合格式（1. 、2、 、-）且含重复。"""
        raw = "1. 钻井成本？\n2、钻井成本？\n- 钻井周期？"
        result = parse_related_questions(raw)
        self.assertEqual(result, ["钻井成本？", "钻井周期？"])


class TestCompleteRelatedQuestions(unittest.TestCase):
    def test_empty_model_result_gets_three_fallback_questions(self):
        complete = getattr(
            stream_sanitizer,
            "complete_related_questions",
            lambda questions: questions,
        )
        self.assertEqual(
            complete([]),
            [
                "这个问题还涉及哪些关键内容？",
                "相关技术有哪些典型应用？",
                "实际应用中需要注意哪些问题？",
            ],
        )

    def test_existing_questions_are_preserved_and_padded(self):
        complete = getattr(
            stream_sanitizer,
            "complete_related_questions",
            lambda questions: questions,
        )
        self.assertEqual(
            complete(["钻井工程包括哪些内容？"]),
            [
                "钻井工程包括哪些内容？",
                "这个问题还涉及哪些关键内容？",
                "相关技术有哪些典型应用？",
            ],
        )


if __name__ == "__main__":
    unittest.main()
