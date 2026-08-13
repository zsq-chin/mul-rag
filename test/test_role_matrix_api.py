"""阶段 7：三角色真实 HTTP 权限矩阵回归测试（TestClient）。

7.1/7.2.3：所有用户管理接口后端独立鉴权，不能只依赖前端菜单隐藏——
普通用户直连 /api/auth/users 系列必须 403；管理员只能管理普通用户、
不能创建/提升超级管理员或管理员；超级管理员全权但受"最后一个超级管理员"
保护（禁止删除或降级）。全部用例经由真实 FastAPI TestClient 调 HTTP 路由，
不 import 路由包名、不伪造响应（7.2.5）。

路由模块以轻量 spec 载入（与 test_user_model_router_api 一致），
src/Milvus 由 _install_src_shim 屏蔽；DB 用 StaticPool 内存 SQLite 共享连接。
"""

import importlib.util
import logging
import os
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
    """让 auth_middleware/db_manager 可在无 Milvus/模型栈的主机导入。"""
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
    fake_utils.logger = logging.getLogger("test_role_matrix_api")
    sys.modules["src.utils"] = fake_utils


_install_src_shim()

from server.models import Base  # noqa: E402
from server.models.cas_session_model import CASSession  # noqa: E402
from server.models.user_model import OperationLog, User  # noqa: E402
from server.services.audit_service import AuditService  # noqa: E402
from server.utils.auth_middleware import (  # noqa: E402
    get_current_user,
    get_db,
)

_spec = importlib.util.spec_from_file_location(
    "auth_router_under_test",
    ROOT / "server" / "routers" / "auth_router.py",
)
router_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(router_mod)


class _Ctx:
    """当前请求用户持有者：override 闭包与各用例共享。"""

    user = None


class RoleMatrixHttpTests(unittest.TestCase):
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
        app.include_router(router_mod.auth, prefix="/api")
        app.dependency_overrides[get_db] = _override_db
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
        # 每个用例独立用户集：清掉审计/会话/用户行，重建三角色。
        self.db.query(OperationLog).delete()
        self.db.query(CASSession).delete()
        self.db.query(User).delete()
        self.db.add_all([
            User(username="root", password_hash="x", role="superadmin"),
            User(username="admin", password_hash="x", role="admin"),
            User(username="field", password_hash="x", role="user"),
            User(username="drill", password_hash="x", role="user"),
        ])
        self.db.commit()
        self._as("root")

    def _as(self, username):
        self.db.expire_all()  # 请求会话可能已改动角色，先清掉本地身份映射缓存
        user = self.db.query(User).filter(User.username == username).one()
        _Ctx.user = user
        return user

    def _by_id(self, user_id):
        self.db.expire_all()
        return self.db.query(User).filter(User.id == user_id).one()

    def _count(self, role):
        return self.db.query(User).filter(User.role == role).count()

    # ---- 7.2.3：普通用户直连管理 API 一律 403 ----
    def test_ordinary_user_gets_403_on_user_list(self):
        self._as("field")
        resp = self.client.get("/api/auth/users")
        self.assertEqual(resp.status_code, 403)

    def test_ordinary_user_gets_403_on_create_user(self):
        self._as("field")
        resp = self.client.post(
            "/api/auth/users",
            json={"username": "hacker", "password": "P@ssw0rd!", "role": "user"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_ordinary_user_gets_403_on_update_user(self):
        self._as("field")
        resp = self.client.put("/api/auth/users/3", json={"username": "hijack"})
        self.assertEqual(resp.status_code, 403)

    def test_ordinary_user_gets_403_on_delete_user(self):
        self._as("field")
        resp = self.client.delete("/api/auth/users/4")
        self.assertEqual(resp.status_code, 403)

    # ---- 7.1：列表可见性 ----
    def test_admin_sees_only_user_accounts(self):
        self._as("admin")
        resp = self.client.get("/api/auth/users")
        self.assertEqual(resp.status_code, 200)
        users = resp.json()
        self.assertEqual(len(users), 2)
        self.assertTrue(all(u["role"] == "user" for u in users))
        self.assertNotIn("root", [u["username"] for u in users])
        self.assertNotIn("admin", [u["username"] for u in users])

    def test_superadmin_sees_all_accounts(self):
        self._as("root")
        resp = self.client.get("/api/auth/users")
        self.assertEqual(resp.status_code, 200)
        users = resp.json()
        self.assertEqual(len(users), 4)
        roles = {u["role"] for u in users}
        self.assertEqual(roles, {"superadmin", "admin", "user"})

    # ---- 7.3.3：管理员不能创建同级/更高级账号 ----
    def test_admin_cannot_create_admin(self):
        self._as("admin")
        resp = self.client.post(
            "/api/auth/users",
            json={"username": "upgrade", "password": "P@ssw0rd!", "role": "admin"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_cannot_create_superadmin(self):
        self._as("admin")
        resp = self.client.post(
            "/api/auth/users",
            json={"username": "god", "password": "P@ssw0rd!", "role": "superadmin"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_create_ordinary_user(self):
        self._as("admin")
        resp = self.client.post(
            "/api/auth/users",
            json={"username": "newfield", "password": "P@ssw0rd!", "role": "user"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["role"], "user")
        self.assertEqual(self._count("user"), 3)

    def test_duplicate_username_creation_returns_400(self):
        self._as("admin")
        resp = self.client.post(
            "/api/auth/users",
            json={"username": "drill", "password": "P@ssw0rd!", "role": "user"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("已存在", resp.text)

    # ---- 7.3.3：管理员不能修改/删除同级或更高级账号 ----
    def test_admin_cannot_update_superadmin(self):
        self._as("admin")
        superadmin_id = self._by_id_by_username("root").id
        resp = self.client.put(
            f"/api/auth/users/{superadmin_id}", json={"username": "root2"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_admin_cannot_update_admin_account(self):
        self._as("admin")
        admin_id = self._by_id_by_username("admin").id
        resp = self.client.put(f"/api/auth/users/{admin_id}", json={"username": "admin2"})
        self.assertEqual(resp.status_code, 403)

    def test_admin_cannot_promote_user_to_admin(self):
        self._as("admin")
        resp = self.client.put("/api/auth/users/3", json={"role": "admin"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self._by_id(3).role, "user")

    def test_admin_cannot_delete_admin_account(self):
        self._as("admin")
        admin_id = self._by_id_by_username("admin").id
        resp = self.client.delete(f"/api/auth/users/{admin_id}")
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_update_ordinary_user_username(self):
        self._as("admin")
        resp = self.client.put("/api/auth/users/3", json={"username": "field2"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["username"], "field2")

    # ---- 7.3.4：最后一个超级管理员禁止删除/降级 ----
    def test_last_superadmin_cannot_be_demoted(self):
        self._as("root")
        root_id = self._by_id_by_username("root").id
        resp = self.client.put(f"/api/auth/users/{root_id}", json={"role": "user"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("最后一个超级管理员", resp.text)
        self.assertEqual(self._count("superadmin"), 1)

    def test_last_superadmin_cannot_be_deleted(self):
        self._as("root")
        root_id = self._by_id_by_username("root").id
        resp = self.client.delete(f"/api/auth/users/{root_id}")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("最后一个超级管理员", resp.text)
        self.assertEqual(self._count("superadmin"), 1)

    def test_superadmin_self_delete_rejected(self):
        self._as("root")
        # 先补一个超级管理员，绕开"最后一个"保护，验证"不能删除自己"
        self.db.add(User(username="root2", password_hash="x", role="superadmin"))
        self.db.commit()
        root_id = self._by_id_by_username("root").id
        resp = self.client.delete(f"/api/auth/users/{root_id}")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("自己的账户", resp.text)

    def test_superadmin_can_demote_when_not_last(self):
        self._as("root")
        self.db.add(User(username="root2", password_hash="x", role="superadmin"))
        self.db.commit()
        root_id = self._by_id_by_username("root").id
        resp = self.client.put(f"/api/auth/users/{root_id}", json={"role": "admin"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._by_id(root_id).role, "admin")

    # ---- 7.3.3/7.3.4：超级管理员合法操作 ----
    def test_superadmin_can_promote_user_to_admin(self):
        self._as("root")
        resp = self.client.put("/api/auth/users/3", json={"role": "admin"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._by_id(3).role, "admin")

    def test_superadmin_can_delete_ordinary_user(self):
        self._as("root")
        resp = self.client.delete("/api/auth/users/3")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(
            self.db.query(User).filter(User.id == 3).first(),
            "超级管理员删除普通用户后该用户应不存在",
        )

    def test_admin_cannot_delete_self(self):
        self._as("admin")
        admin_id = self._by_id_by_username("admin").id
        # 管理员不能删除自己（can_manage_target 对自己为 False → 403）
        resp = self.client.delete(f"/api/auth/users/{admin_id}")
        self.assertEqual(resp.status_code, 403)

    # ---- 菜单/路由数据源：所有角色都能读取自身资料 ----
    def test_all_roles_can_read_own_profile(self):
        for username, role in (
            ("root", "superadmin"),
            ("admin", "admin"),
            ("field", "user"),
        ):
            with self.subTest(role=role):
                self._as(username)
                resp = self.client.get("/api/auth/me")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["role"], role)
                self.assertEqual(resp.json()["username"], username)

    def _by_id_by_username(self, username):
        self.db.expire_all()
        return self.db.query(User).filter(User.username == username).one()


if __name__ == "__main__":
    unittest.main()
