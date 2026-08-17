"""知识字典路由 API 测试（设计文档 §13/§14/§15）：真实 FastAPI TestClient 调 HTTP 路由，
验证状态码、统一错误结构与权限矩阵。

以「轻量 src 桩 + 直载 router 模块文件」方式加载（与 test_graph_router_h1 同模式）：
被测路由在运行期不触碰真实 Milvus/模型栈，服务层照常经真实 HTTP 执行。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]


def _install_src_shim() -> None:
    if "src" in sys.modules and getattr(sys.modules.get("src"), "_sage_dict_router_shim", False):
        return
    fake_src = types.ModuleType("src")
    fake_src.__path__ = [str(ROOT / "src")]
    fake_src._sage_dict_router_shim = True
    fake_src.executor = __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=2)
    fake_src.config = types.SimpleNamespace(save_dir="saves")
    fake_src.shutdown_runtime = lambda: None
    sys.modules["src"] = fake_src
    fake_utils = types.ModuleType("src.utils")
    fake_utils.logger = logging.getLogger("test_dict_router")
    sys.modules["src.utils"] = fake_utils


_install_src_shim()

# ---- 测试数据库（真实 SQLite）与 db_manager 桩 ----
from server.models import Base  # noqa: E402
import server.models.kb_models  # noqa: E402, F401
import server.models.user_model  # noqa: E402, F401
import server.models.knowledge_dictionary_models  # noqa: E402, F401
from server.models.knowledge_dictionary_models import (  # noqa: E402
    KnowledgeDictionary,
    KnowledgeDictionaryJob,
    KnowledgeDictionaryVersion,
)

_engine = create_engine(f"sqlite:///{Path(tempfile.mkdtemp()) / 'server.db'}")
Base.metadata.create_all(_engine)
_TestSession = sessionmaker(bind=_engine)

import atexit  # noqa: E402

atexit.register(_engine.dispose)


def _make_session():
    return _TestSession()


_fake_db_manager = types.ModuleType("server.db_manager")
_fake_db_manager.db_manager = types.SimpleNamespace(get_session=_make_session, db_path=":memory:")
sys.modules["server.db_manager"] = _fake_db_manager

# ---- 直载被测路由 ----
_spec = importlib.util.spec_from_file_location(
    "knowledge_dictionary_router_under_test",
    ROOT / "server" / "routers" / "knowledge_dictionary_router.py",
)
router_module = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(router_module)

from server.utils.auth_middleware import get_required_user  # noqa: E402


class ApiUser:
    def __init__(self, role="admin", user_id=1):
        self.id = user_id
        self.role = role
        self.username = f"u{user_id}"


def _build_app(role="admin", user_id=1):
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api")
    app.dependency_overrides[get_required_user] = lambda: ApiUser(role, user_id)
    return app


def _client(role="admin", user_id=1):
    return TestClient(_build_app(role, user_id))


class RouterApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = _make_session()
        cls._cleanup()

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    @classmethod
    def _cleanup(cls):
        # 每轮清空业务表，保持用例独立
        from server.models.kb_models import KnowledgeDatabase, KnowledgeFile, KnowledgeNode
        from server.models.knowledge_dictionary_models import (
            KnowledgeDictionaryEntry,
            KnowledgeDictionaryEvidence,
            KnowledgeDictionaryJob,
            KnowledgeDictionarySource,
        )

        for model in (
            KnowledgeDictionaryEvidence,
            KnowledgeDictionaryEntry,
            KnowledgeDictionarySource,
            KnowledgeDictionaryJob,
            KnowledgeDictionaryVersion,
            KnowledgeDictionary,
            KnowledgeNode,
            KnowledgeFile,
            KnowledgeDatabase,
        ):
            cls.session.query(model).delete()
        cls.session.commit()

    def setUp(self):
        self._cleanup()

    def test_list_and_create_dictionary(self):
        with _client("admin") as c:
            resp = c.get("/api/knowledge-dictionaries")
            self.assertEqual(resp.status_code, 200)
            resp = c.post(
                "/api/knowledge-dictionaries",
                json={"name": "压裂字典", "description": "描述", "domain": "石油工程"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["data"]["status"], "draft")
            # 重名 409 + 统一错误结构
            resp = c.post("/api/knowledge-dictionaries", json={"name": "压裂字典"})
            self.assertEqual(resp.status_code, 409)
            body = resp.json()["detail"]
            self.assertIn("error_code", body)
            self.assertEqual(body["error_code"], "DICTIONARY_CONFLICT")

    def test_user_cannot_create(self):
        with _client("user", user_id=2) as c:
            resp = c.post("/api/knowledge-dictionaries", json={"name": "用户字典"})
            self.assertEqual(resp.status_code, 403)
            body = resp.json()["detail"]
            self.assertEqual(body["error_code"], "DICTIONARY_FORBIDDEN")

    def test_generate_job_and_lifecycle(self):
        # 准备知识库来源数据
        from server.models.kb_models import KnowledgeDatabase, KnowledgeFile, KnowledgeNode

        db = self.session
        kb = KnowledgeDatabase(db_id="kb1", name="库1")
        f = KnowledgeFile(file_id="f1", database_id="kb1", filename="a.pdf", path="p", file_type="pdf", status="done")
        db.add_all([kb, f])
        db.commit()
        db.add(KnowledgeNode(file_id="f1", text="孔隙度是描述储层的重要参数，单位为%。", hash="h1"))
        db.commit()

        with _client("admin") as c:
            resp = c.post(
                "/api/knowledge-dictionaries/generate",
                json={
                    "name": "压裂字典",
                    "domain": "石油工程",
                    "use_seed": False,
                    "source": {"kind": "kb_file", "db_id": "kb1", "file_id": "f1"},
                },
            )
            self.assertEqual(resp.status_code, 202, resp.text)
            job = resp.json()["data"]
            self.assertEqual(job["job_type"], "generate")
            # 任务查询
            resp = c.get(f"/api/knowledge-dictionaries/jobs/{job['id']}")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["data"]["status"], "queued")
            # 取消
            resp = c.post(f"/api/knowledge-dictionaries/jobs/{job['id']}/cancel")
            self.assertEqual(resp.status_code, 200)
            # 重试
            resp = c.post(f"/api/knowledge-dictionaries/jobs/{job['id']}/retry")
            self.assertEqual(resp.status_code, 200)

    def test_generate_mutually_exclusive_source(self):
        with _client("admin") as c:
            resp = c.post(
                "/api/knowledge-dictionaries/generate",
                json={"name": "x", "source": {"kind": "kb_file", "db_id": None, "file_id": None}},
            )
            self.assertEqual(resp.status_code, 422)  # Pydantic 校验
            resp = c.post(
                "/api/knowledge-dictionaries/generate",
                json={"name": "x", "source": {"kind": "kb_file", "db_id": "不存在的库", "file_id": "f1"}},
            )
            self.assertEqual(resp.status_code, 404)  # 知识库不存在
            body = resp.json()["detail"]
            self.assertEqual(body["error_code"], "DICTIONARY_NOT_FOUND")

    def test_upload_source_and_bad_extension(self):
        with _client("admin") as c:
            resp = c.post(
                "/api/knowledge-dictionaries/upload",
                files={"file": ("资料.txt", "孔隙度=10%".encode("utf-8"), "text/plain")},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertIn("storage_ref", resp.json()["data"])
            resp = c.post(
                "/api/knowledge-dictionaries/upload",
                files={"file": ("x.exe", b"MZ", "application/octet-stream")},
            )
            self.assertEqual(resp.status_code, 415)
            body = resp.json()["detail"]
            self.assertEqual(body["error_code"], "DICTIONARY_UNSUPPORTED_FILE")

    def test_user_cannot_read_draft_entries(self):
        with _client("admin") as c:
            resp = c.post("/api/knowledge-dictionaries", json={"name": "压裂字典"})
            data = resp.json()["data"]
            version = (
                self.session.query(KnowledgeDictionaryVersion)
                .filter(KnowledgeDictionaryVersion.dictionary_id == data["id"])
                .first()
            )
        with _client("user", user_id=2) as c:
            resp = c.get(f"/api/knowledge-dictionaries/{data['id']}/versions/{version.id}/entries")
            self.assertEqual(resp.status_code, 403)

    def test_publish_blocked_unified_error(self):
        with _client("admin") as c:
            resp = c.post("/api/knowledge-dictionaries", json={"name": "压裂字典"})
            data = resp.json()["data"]
            version = (
                self.session.query(KnowledgeDictionaryVersion)
                .filter(KnowledgeDictionaryVersion.dictionary_id == data["id"])
                .first()
            )
            resp = c.post(f"/api/knowledge-dictionaries/{data['id']}/versions/{version.id}/publish")
            self.assertEqual(resp.status_code, 409)
            body = resp.json()["detail"]
            self.assertEqual(body["error_code"], "DICTIONARY_PUBLISH_BLOCKED")

    def test_export_csv_chinese_filename(self):
        with _client("admin") as c:
            resp = c.post("/api/knowledge-dictionaries", json={"name": "压裂字典"})
            data = resp.json()["data"]
            version = (
                self.session.query(KnowledgeDictionaryVersion)
                .filter(KnowledgeDictionaryVersion.dictionary_id == data["id"])
                .first()
            )
            resp = c.get(f"/api/knowledge-dictionaries/{data['id']}/versions/{version.id}/export?format=csv")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("UTF-8", resp.headers.get("content-disposition", ""))
            self.assertIn("attachment", resp.headers.get("content-disposition", ""))

    def test_search_unavailable_without_milvus(self):
        with _client("user", user_id=2) as c:
            resp = c.post("/api/knowledge-dictionaries/search", json={"query": "孔隙度", "top_k": 5})
            # 本地无 Milvus：503 且业务数据不丢失
            self.assertEqual(resp.status_code, 503)
            body = resp.json()["detail"]
            self.assertEqual(body["error_code"], "DICTIONARY_SERVICE_UNAVAILABLE")

    def test_source_listing_endpoints(self):
        from server.models.kb_models import KnowledgeDatabase, KnowledgeFile, KnowledgeNode

        db = self.session
        kb = KnowledgeDatabase(db_id="kb1", name="库1")
        f = KnowledgeFile(file_id="f1", database_id="kb1", filename="a.pdf", path="p", file_type="pdf", status="done")
        db.add_all([kb, f])
        db.commit()
        db.add(KnowledgeNode(file_id="f1", text="节点文本", hash="h1"))
        db.commit()
        with _client("admin") as c:
            resp = c.get("/api/knowledge-dictionaries/sources/knowledge-bases")
            self.assertEqual(resp.status_code, 200, resp.text)
            kbs = resp.json()["data"]
            self.assertEqual(kbs[0]["db_id"], "kb1")
            self.assertEqual(kbs[0]["parsed_count"], 1)
            resp = c.get("/api/knowledge-dictionaries/sources/knowledge-bases/kb1/files?keyword=a")
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["data"]["total"], 1)
            # 静态路径不得被 /{dictionary_id} 吞掉（422 说明顺序错误）
            self.assertNotEqual(resp.status_code, 422)
        with _client("user", user_id=2) as c:
            resp = c.get("/api/knowledge-dictionaries/sources/knowledge-bases")
            self.assertEqual(resp.status_code, 403)

    def test_seed_import_job_created(self):
        with _client("admin") as c:
            resp = c.post("/api/knowledge-dictionaries/seed-import")
            self.assertEqual(resp.status_code, 202, resp.text)
            self.assertEqual(resp.json()["data"]["job_type"], "import_seed")
            # 普通用户禁止
        with _client("user", user_id=3) as c:
            resp = c.post("/api/knowledge-dictionaries/seed-import")
            self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
