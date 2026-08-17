"""回归测试：知识库详情接口必须返回用户存储的名称，而非 Milvus 内部集合标记。

根因：``KnowledgeBase.get_database_info`` 曾用 ``db_copy.update(milvus_info)`` 合并
Milvus 信息，而 ``get_collection_info`` 失败时返回 ``{"name": collection_name, ...}``，
``collection_name`` 是内部标记（如 ``kb_xxx``），会覆盖用户存储的知识库名称，
导致详情页头部标题显示 ``kb_xxx`` 而非「压裂工程知识库」。列表接口
``get_databases`` 已做逐键保护，详情接口此前遗漏了 ``name`` 的保护。

本测试直接 spec 加载 ``src/core/knowledgebase.py``，用桩屏蔽 Milvus/模型栈，
以 ``object.__new__`` 构造无连接实例并注入 ``get_database_by_id`` /
``get_collection_info``，验证 ``get_database_info`` 的真实合并逻辑。
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def _install_src_shim() -> None:
    """屏蔽 knowledgebase 的 Milvus/模型/langchain 依赖，只提供 import 期名称。"""
    if "src" in sys.modules and getattr(sys.modules.get("src"), "_sage_kb_shim", False):
        return

    class _StubLogger:
        def info(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass
        def warning(self, *args, **kwargs): pass
        def debug(self, *args, **kwargs): pass

    src = types.ModuleType("src")
    src.__path__ = [str(ROOT / "src")]
    src._sage_kb_shim = True
    src.config = types.SimpleNamespace(save_dir="saves")
    sys.modules["src"] = src

    utils = types.ModuleType("src.utils")
    utils.logger = _StubLogger()
    utils.hashstr = lambda s, length=None, with_salt=False: "kb_hash"
    sys.modules["src.utils"] = utils
    logging_config = types.ModuleType("src.utils.logging_config")
    logging_config.logger = _StubLogger()
    sys.modules["src.utils.logging_config"] = logging_config

    core = types.ModuleType("src.core")
    sys.modules["src.core"] = core
    indexing = types.ModuleType("src.core.indexing")
    indexing.chunk_with_parser = lambda *a, **k: []
    indexing.chunk_text = lambda *a, **k: []
    indexing.parse_pdf_async = lambda *a, **k: []
    sys.modules["src.core.indexing"] = indexing

    db_migration = types.ModuleType("src.utils.db_migration")
    db_migration.migrate_knowledge_db = lambda: False
    sys.modules["src.utils.db_migration"] = db_migration

    # server.db_manager 在 knowledgebase 中仅为 import 期名称，运行期不触碰
    db_manager_mod = types.ModuleType("server.db_manager")
    db_manager_mod.db_manager = types.SimpleNamespace()
    sys.modules["server.db_manager"] = db_manager_mod

    # kb_models 仅用于类型/记录名；用轻量桩避免 sqlalchemy 全量模型导入
    kb_models = types.ModuleType("server.models.kb_models")
    kb_models.KnowledgeDatabase = type("KnowledgeDatabase", (), {})
    kb_models.KnowledgeFile = type("KnowledgeFile", (), {})
    kb_models.KnowledgeNode = type("KnowledgeNode", (), {})
    sys.modules["server.models.kb_models"] = kb_models


_install_src_shim()

_spec = importlib.util.spec_from_file_location(
    "knowledgebase_under_test", ROOT / "src" / "core" / "knowledgebase.py"
)
knowledgebase = importlib.util.module_from_spec(_spec)
sys.modules["knowledgebase_under_test"] = knowledgebase
assert _spec.loader is not None
_spec.loader.exec_module(knowledgebase)


class _BareKnowledgeBase(knowledgebase.KnowledgeBase):
    """绕过 __init__（连接 Milvus / 加载模型），仅承载被注入的协作方法。"""

    def __init__(self):
        pass


class KnowledgeBaseInfoNameTests(unittest.TestCase):
    def _make(self, stored_name, milvus_info):
        kb = _BareKnowledgeBase()
        kb.get_database_by_id = lambda db_id: {
            "db_id": db_id,
            "name": stored_name,
            "description": "我存储的描述",
            "files": {},
        }
        kb.get_collection_info = lambda db_id: milvus_info
        return kb

    def test_name_preserved_when_milvus_returns_marker(self):
        # Milvus 失败路径返回 {"name": collection_name}，collection_name 是内部标记 kb_xxx
        kb = self._make(
            stored_name="压裂工程知识库",
            milvus_info={"name": "kb_71b40c8691c3f3ded268f8e0a6258cc6", "row_count": 0, "status": "错误"},
        )
        info = kb.get_database_info("kb_71b40c8691c3f3ded268f8e0a6258cc6")
        self.assertEqual(info["name"], "压裂工程知识库")
        self.assertEqual(info["description"], "我存储的描述")
        self.assertNotEqual(info["name"], "kb_71b40c8691c3f3ded268f8e0a6258cc6")

    def test_name_preserved_when_milvus_describe_uses_collection_name(self):
        # Milvus describe_collection 可能带 collection_name 键；同样不得覆盖 name
        kb = self._make(
            stored_name="钻采工程知识库",
            milvus_info={
                "collection_name": "kb_ac0cd1240f13934027ba8ae102fc4262",
                "row_count": 10,
                "status": "已连接",
            },
        )
        info = kb.get_database_info("kb_ac0cd1240f13934027ba8ae102fc4262")
        self.assertEqual(info["name"], "钻采工程知识库")

    def test_name_preserved_on_milvus_exception_path(self):
        # get_collection_info 抛异常时走 except 分支，也必须保留 name
        kb = _BareKnowledgeBase()
        kb.get_database_by_id = lambda db_id: {
            "db_id": db_id, "name": "测井知识库", "description": "", "files": {},
        }

        def _raise(db_id):
            raise RuntimeError("milvus down")

        kb.get_collection_info = _raise
        with patch.object(knowledgebase, "logger"):
            info = kb.get_database_info("kb_a61ebe7839f90195798dacf3934c41d6")
        self.assertEqual(info["name"], "测井知识库")

    def test_milvus_row_count_still_merged(self):
        # 名称保护不吞掉 Milvus 有用的行数/状态信息
        kb = self._make(
            stored_name="压裂工程知识库",
            milvus_info={"name": "kb_xxx", "row_count": 42, "status": "已连接"},
        )
        info = kb.get_database_info("kb_xxx")
        self.assertEqual(info["name"], "压裂工程知识库")
        self.assertEqual(info["row_count"], 42)


if __name__ == "__main__":
    unittest.main()
