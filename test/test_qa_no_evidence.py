"""问答“无有效证据时明确说明证据不足”回归测试（P1-2）。

src 包因 Milvus 依赖无法整体导入，但 src/utils/prompts.py 是纯标准库模块
（仅 datetime），可以按文件路径隔离加载，直接对生产使用的纯函数
（build_qa_prompt / retrieval_mode_enabled / build_chat_prompt）做行为测试。

回归场景（第二轮评审 P1-2）：
- 普通聊天（未启用任何检索）→ construct_query 返回原始 query，
  不受“无证据”模板影响；
- 启用知识库/图谱/联网/多模态检索且证据为空 → 注入无证据占位模板；
- 有资料 → 正常引用资料回答；
- 出题请求 → 始终走出题模板，自带“信息不足”处理。

按评审要求，不在此处做源码字符串断言，只验证运行时行为。
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS_PATH = ROOT / "src" / "utils" / "prompts.py"


def _load_prompts():
    """按文件路径隔离加载 prompts.py，避免触发 src/__init__ 的 Milvus 连接。"""
    spec = importlib.util.spec_from_file_location("sage_qa_prompts", PROMPTS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QaNoEvidenceBehaviorTests(unittest.TestCase):
    """对生产使用的 build_qa_prompt 做行为测试。"""

    @classmethod
    def setUpClass(cls):
        cls.prompts = _load_prompts()

    def test_template_instructs_insufficient_evidence(self):
        text = self.prompts.knowbase_qa_template
        self.assertIn("证据不足", text)
        self.assertIn("不要编造", text)

    def test_empty_external_uses_no_evidence_marker(self):
        prompt = self.prompts.build_qa_prompt("什么是RAG？", "")
        self.assertIn(self.prompts.NO_EVIDENCE_MARKER, prompt)
        self.assertIn("什么是RAG？", prompt)
        self.assertIn("证据不足", prompt)

    def test_none_external_uses_no_evidence_marker(self):
        prompt = self.prompts.build_qa_prompt("问题", None)
        self.assertIn(self.prompts.NO_EVIDENCE_MARKER, prompt)
        self.assertIn("证据不足", prompt)

    def test_whitespace_external_treated_as_no_evidence(self):
        prompt = self.prompts.build_qa_prompt("问题", "   \n\t ")
        self.assertIn(self.prompts.NO_EVIDENCE_MARKER, prompt)

    def test_real_external_keeps_evidence(self):
        prompt = self.prompts.build_qa_prompt(
            "问题", "知识库信息:\nRAG是检索增强生成"
        )
        self.assertIn("知识库信息:", prompt)
        self.assertNotIn(self.prompts.NO_EVIDENCE_MARKER, prompt)
        self.assertIn("RAG是检索增强生成", prompt)

    def test_item_request_keeps_item_template_and_no_marker(self):
        prompt = self.prompts.build_qa_prompt(
            "根据资料出题", "", params={"count": 2}, is_item_request=True
        )
        self.assertIn("专业的中文出题助手", prompt)
        self.assertNotIn(self.prompts.NO_EVIDENCE_MARKER, prompt)


class RetrievalModeEnabledTests(unittest.TestCase):
    """retrieval_mode_enabled：只有 meta 明确启用检索源才判定为检索模式。"""

    @classmethod
    def setUpClass(cls):
        cls.prompts = _load_prompts()

    def test_plain_chat_without_meta_is_not_retrieval(self):
        self.assertFalse(self.prompts.retrieval_mode_enabled(None))
        self.assertFalse(self.prompts.retrieval_mode_enabled({}))
        self.assertFalse(self.prompts.retrieval_mode_enabled("not-a-dict"))

    def test_each_retrieval_source_enables_mode(self):
        for key in ("db_id", "use_graph", "use_web", "use_multimodal_kb"):
            self.assertTrue(
                self.prompts.retrieval_mode_enabled({key: True}),
                f"{key}=True 应判定为检索模式",
            )

    def test_false_meta_keys_do_not_enable_mode(self):
        self.assertFalse(
            self.prompts.retrieval_mode_enabled(
                {"db_id": None, "use_graph": False, "use_web": 0, "use_multimodal_kb": ""}
            )
        )


class BuildChatPromptTests(unittest.TestCase):
    """build_chat_prompt：普通聊天回归 + 检索无证据 + 出题优先级。"""

    @classmethod
    def setUpClass(cls):
        cls.prompts = _load_prompts()

    def test_plain_chat_returns_raw_query_even_with_empty_evidence(self):
        """普通聊天：即使外部证据为空，也必须返回原始 query，不得注入无证据模板。"""
        result = self.prompts.build_chat_prompt("今天天气怎么样", "", {})
        self.assertEqual(result, "今天天气怎么样")
        self.assertNotIn(self.prompts.NO_EVIDENCE_MARKER, result)

    def test_plain_chat_returns_raw_query_even_with_evidence(self):
        """普通聊天：即便附带资料也保持原样，不套问答模板（行为不变量）。"""
        result = self.prompts.build_chat_prompt(
            "你好", "知识库信息:\n某段资料", {}
        )
        self.assertEqual(result, "你好")

    def test_kb_retrieval_with_evidence_answers_normally(self):
        result = self.prompts.build_chat_prompt(
            "什么是RAG？", "知识库信息:\nRAG是检索增强生成", {"db_id": 1}
        )
        self.assertIn("RAG是检索增强生成", result)
        self.assertNotIn(self.prompts.NO_EVIDENCE_MARKER, result)

    def test_kb_retrieval_empty_evidence_marks_no_evidence(self):
        result = self.prompts.build_chat_prompt(
            "什么是RAG？", "", {"db_id": 1}
        )
        self.assertIn(self.prompts.NO_EVIDENCE_MARKER, result)
        self.assertIn("证据不足", result)

    def test_multimodal_retrieval_empty_evidence_marks_no_evidence(self):
        result = self.prompts.build_chat_prompt(
            "这段视频讲了什么", "", {"use_multimodal_kb": True}
        )
        self.assertIn(self.prompts.NO_EVIDENCE_MARKER, result)
        self.assertIn("证据不足", result)

    def test_multimodal_retrieval_with_evidence_answers_normally(self):
        result = self.prompts.build_chat_prompt(
            "这段视频讲了什么",
            "多模态知识库信息:\n视频第 1 帧文字",
            {"use_multimodal_kb": True},
        )
        self.assertIn("多模态知识库信息:", result)
        self.assertNotIn(self.prompts.NO_EVIDENCE_MARKER, result)

    def test_graph_retrieval_empty_evidence_marks_no_evidence(self):
        result = self.prompts.build_chat_prompt(
            "井筒和套管的关系", "", {"use_graph": True}
        )
        self.assertIn(self.prompts.NO_EVIDENCE_MARKER, result)

    def test_item_request_uses_item_template_even_without_retrieval(self):
        """出题模式优先级最高：未启用检索也走出题模板，而不是原样返回。"""
        result = self.prompts.build_chat_prompt(
            "根据资料出题",
            "",
            {"isItemRequest": True},
            params={"count": 2},
        )
        self.assertIn("专业的中文出题助手", result)
        self.assertNotIn(self.prompts.NO_EVIDENCE_MARKER, result)

    def test_item_request_with_retrieval_also_uses_item_template(self):
        result = self.prompts.build_chat_prompt(
            "根据资料出题",
            "知识库信息:\n一些资料",
            {"db_id": 1, "isItemRequest": True},
            params={"count": 2},
        )
        self.assertIn("专业的中文出题助手", result)


if __name__ == "__main__":
    unittest.main()
