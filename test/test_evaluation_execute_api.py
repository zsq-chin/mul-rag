"""问答测试集"执行"验收测试（真实 FastAPI TestClient 走 HTTP 路由）。

8.2：测试集除 CRUD/导入/导出外还需支持"执行"。
本文件覆盖 execute 端点：判分统计、无 key_points 未判分、单条模型失败透出、
无启用用例 400、测试集不存在 404、非 superadmin 403、禁用用例跳过。
执行器（router 模块级 _default_eval_answerer）在各用例中 patch 为 canned 应答，
不伪造响应、不依赖真实模型/远端知识库（黑盒）；远端 kb_id 仅透传不查询。

路由模块以轻量 spec 载入（同 test_role_matrix_api），src 由 shim 屏蔽。
"""

import importlib.util
import logging
import sys
import types
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]


def _install_src_shim() -> None:
    """让 db_manager/auth_middleware 可在无 Milvus/模型栈的主机导入。"""
    if "src" in sys.modules and getattr(sys.modules.get("src"), "_sage_router_shim", False):
        return

    fake_src = types.ModuleType("src")
    fake_src.__path__ = [str(ROOT / "src")]
    fake_src._sage_router_shim = True
    fake_src.executor = None
    fake_src.config = types.SimpleNamespace(
        save_dir="saves",
        enable_knowledge_graph=True,
    )
    fake_src.retriever = None
    fake_src.knowledge_base = None
    fake_src.graph_base = None
    fake_src.BLOCKING_WORKERS = 2
    fake_src.shutdown_runtime = lambda: None
    sys.modules["src"] = fake_src

    fake_utils = types.ModuleType("src.utils")
    fake_utils.logger = logging.getLogger("test_evaluation_execute_api")
    sys.modules["src.utils"] = fake_utils


_install_src_shim()

from server.models import Base  # noqa: E402
from server.models.evaluation_model import EvaluationCase  # noqa: E402
from server.services import evaluation_service  # noqa: E402
from server.services.audit_service import AuditService  # noqa: E402
from server.utils.auth_middleware import get_current_user  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "evaluation_router_under_test",
    ROOT / "server" / "routers" / "evaluation_router.py",
)
router_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(router_mod)


class _Ctx:
    """当前请求用户持有者：override 闭包与各用例共享。"""

    user = None


class EvaluationExecuteHttpTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)
        cls.db = cls.Session()
        AuditService.session_factory = cls.Session

        def _override_db():
            session = cls.Session()
            try:
                yield session
            finally:
                session.close()

        async def _override_current_user():
            return _Ctx.user

        app = FastAPI()
        app.include_router(router_mod.router, prefix="/api")
        app.dependency_overrides[router_mod.get_db] = _override_db
        app.dependency_overrides[get_current_user] = _override_current_user
        cls.app = app
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        AuditService.session_factory = None
        _Ctx.user = None
        cls.db.close()
        cls.engine.dispose()

    def setUp(self):
        from server.models.user_model import User

        self.db.query(User).delete()
        self.db.query(EvaluationCase).delete()
        from server.models.evaluation_model import EvaluationSuite

        self.db.query(EvaluationSuite).delete()
        self.db.commit()
        root = User(username="root", password_hash="x", role="superadmin")
        user = User(username="field", password_hash="x", role="user")
        self.db.add_all([root, user])
        self.db.commit()
        self.superadmin = root
        self.normal_user = user

        self.suite = evaluation_service.create_suite(
            self.db, "安全测试集", "描述", "安全", creator="root"
        )
        self._add_case("如何防止原油泄漏？", "定期巡检管道并安装报警装置。", ["巡检", "报警装置"])
        self._add_case("火灾处置第一步是什么？", "切断气源并立即报警。", ["切断气源", "报警"])

    def _add_case(self, question, answer, key_points, enabled=True):
        return evaluation_service.create_case(
            self.db,
            self.suite["id"],
            {
                "question": question,
                "answer": answer,
                "key_points": key_points,
                "kb_id": "kb_test",
                "enabled": enabled,
            },
        )

    def _patch_answerer(self, fn):
        self._orig_answerer = router_mod._default_eval_answerer
        router_mod._default_eval_answerer = fn

    def tearDown(self):
        orig = getattr(self, "_orig_answerer", None)
        if orig is not None:
            router_mod._default_eval_answerer = orig
            self._orig_answerer = None

    def _as(self, user):
        self.db.expire_all()
        self.db.flush()
        _Ctx.user = user

    def test_execute_runs_and_judges_all_cases(self):
        """全部要点命中 → 全部 passed，matched=True（真实 HTTP）。"""
        self._as(self.superadmin)

        def fake(question, kb_id):
            if "火灾" in question:
                return "应切断气源并立即报警。", None
            return "需定期巡检管道并安装泄漏报警装置。", None

        self._patch_answerer(fake)
        resp = self.client.post(f"/api/evaluation/suites/{self.suite['id']}/execute")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "success")
        data = body["data"]
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["passed"], 2)
        self.assertEqual(data["failed"], 0)
        self.assertEqual(data["errored"], 0)
        self.assertEqual(data["unjudged"], 0)
        for case in data["cases"]:
            self.assertTrue(case["judged"])
            self.assertTrue(case["matched"])

    def test_execute_marks_missing_key_point_as_failed(self):
        """答案缺少某个要点 → 该条 failed（要点子串判分）。"""
        self._as(self.superadmin)

        def fake(question, kb_id):
            if "火灾" in question:
                return "立即报警。", None  # 缺"切断气源"
            return "定期巡检管道并安装泄漏报警装置。", None

        self._patch_answerer(fake)
        resp = self.client.post(f"/api/evaluation/suites/{self.suite['id']}/execute")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["passed"], 1)
        self.assertEqual(data["failed"], 1)
        failed_case = next(c for c in data["cases"] if not c["matched"])
        self.assertFalse(failed_case["matched"])

    def test_execute_without_key_points_counts_unjudged(self):
        """无 key_points 的用例执行成功但不判分（unjudged，matched=None）。"""
        self._as(self.superadmin)
        self._add_case("自由问题", "任意回答", None, enabled=True)

        def fake(question, kb_id):
            return "这是一个回答。", None

        self._patch_answerer(fake)
        resp = self.client.post(f"/api/evaluation/suites/{self.suite['id']}/execute")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["unjudged"], 1)
        unjudged_case = next(c for c in data["cases"] if c["key_points"] == [])
        self.assertFalse(unjudged_case["judged"])
        self.assertIsNone(unjudged_case["matched"])
        self.assertIsNone(unjudged_case["error"])

    def test_execute_model_error_reported_per_case(self):
        """模型调用失败逐条透出 error，不整批失败也不掩盖（8.1.2）。"""
        self._as(self.superadmin)

        def fake(question, kb_id):
            return None, "模型调用失败：provider 不可用"

        self._patch_answerer(fake)
        resp = self.client.post(f"/api/evaluation/suites/{self.suite['id']}/execute")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["errored"], 2)
        self.assertEqual(data["passed"], 0)
        for case in data["cases"]:
            self.assertIsNone(case["response"])
            self.assertIn("模型调用失败", case["error"])

    def test_execute_skips_disabled_cases(self):
        """禁用用例不计入执行。"""
        self._as(self.superadmin)
        self._add_case("禁用问题", "不该被问到", ["不该"], enabled=False)

        def fake(question, kb_id):
            return "命中全部要点。", None

        self._patch_answerer(fake)
        resp = self.client.post(f"/api/evaluation/suites/{self.suite['id']}/execute")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["data"]
        self.assertEqual(data["total"], 2)
        questions = [c["question"] for c in data["cases"]]
        self.assertNotIn("禁用问题", questions)

    def test_execute_empty_suite_returns_400(self):
        """测试集没有启用用例 → 400（业务参数错误）。"""
        self._as(self.superadmin)
        empty = evaluation_service.create_suite(self.db, "空测试集")
        self._patch_answerer(lambda q, kb: ("x", None))
        resp = self.client.post(f"/api/evaluation/suites/{empty['id']}/execute")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("没有启用的用例", resp.json()["detail"])

    def test_execute_missing_suite_returns_404(self):
        self._as(self.superadmin)
        self._patch_answerer(lambda q, kb: ("x", None))
        resp = self.client.post("/api/evaluation/suites/99999/execute")
        self.assertEqual(resp.status_code, 404)

    def test_execute_requires_superadmin(self):
        """非 superadmin（含 admin/user）→ 403，真实依赖链校验角色。"""
        self._as(self.normal_user)
        self._patch_answerer(lambda q, kb: ("x", None))
        resp = self.client.post(f"/api/evaluation/suites/{self.suite['id']}/execute")
        self.assertEqual(resp.status_code, 403)

    def test_execute_answerer_is_invoked_with_question_and_kb(self):
        """执行器收到问题与 kb_id（kb_id 仅透传，不查询远端——黑盒不接入）。"""
        self._as(self.superadmin)
        seen = []

        def fake(question, kb_id):
            seen.append((question, kb_id))
            return "定期巡检管道并安装泄漏报警装置。", None

        self._patch_answerer(fake)
        resp = self.client.post(f"/api/evaluation/suites/{self.suite['id']}/execute")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0][1], "kb_test")
        self.assertTrue(all(q for q, _ in seen))


if __name__ == "__main__":
    unittest.main()
