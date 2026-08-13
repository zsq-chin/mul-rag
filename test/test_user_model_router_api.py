"""阶段 6.2.4：用户自定义模型（UserModelCredential）真实 HTTP 回归测试。

覆盖（全部通过真实 FastAPI TestClient 调 HTTP 路由）：
1. 创建 / 列表 / 更新 / 选择 / 删除 全生命周期；
2. 更新时 api_key 传空串（留空）＝ 保留现有密钥——不覆盖、不清空；
   此前空串会走到 cipher.encrypt("") 抛 ValueError → 400，破坏「留空保留」契约；
3. 创建时 api_key 为空 → 400（创建必须提供密钥）；
4. 响应体绝不回传加密串 / 明文 Key（仅 has_api_key + key_hint）；
5. 跨用户访问他人模型 → 404，不存在的模型 → 404；
6. /validate：公开端点校验通过；回环/内网地址（SSRF）→ 400；上游失败 → 400。

路由模块以「轻量 spec 载入模块文件」方式加载（与 test_graph_router_h1 一致），
不 import 路由包名；业务经由真实 HTTP 执行。validate_api_base 的 DNS 用
socket.getaddrinfo 打桩返回公开/私网 IP，避免外部解析。
"""

import importlib.util
import logging
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]


def _install_src_shim() -> None:
    """让 auth_middleware/db_manager 可在无 Milvus/模型栈的主机导入。

    与 test_graph_router_h1 相同的模式：以假 src 模块占位 sys.modules，
    只提供 import 期名称（config.save_dir、utils.logger），不伪造任何路由行为。
    """
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
    fake_utils.logger = logging.getLogger("test_user_model_router_api")
    sys.modules["src.utils"] = fake_utils


_install_src_shim()

from server.models import Base  # noqa: E402
from server.models.user_model import User  # noqa: E402
from server.models.user_model_credential import UserModelCredential  # noqa: E402
from server.services.audit_service import AuditService  # noqa: E402
from server.services.model_credentials import CredentialCipher  # noqa: E402
from server.utils.auth_middleware import get_db, get_required_user  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "user_model_router_under_test",
    ROOT / "server" / "routers" / "user_model_router.py",
)
router_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(router_mod)

PUBLIC_RESOLUTION = [(2, 1, 6, "", ("8.8.8.8", 443))]
PRIVATE_RESOLUTION = [(2, 1, 6, "", ("127.0.0.1", 443))]


class _FakeValidateResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeAsyncClient:
    """httpx.AsyncClient 替身：只实现 validate 端点用到的 async with + get。"""

    status_code = 200

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        return _FakeValidateResponse(self.status_code)


class _Ctx:
    """当前请求用户持有者：override 闭包与各用例共享，避免继承自类属性混淆。"""

    user = None


class UserModelRouterTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.master_key = Fernet.generate_key().decode()
        os.environ["MODEL_CREDENTIAL_MASTER_KEY"] = cls.master_key

        # StaticPool：强制所有线程（含 TestClient 的 ASGI 线程）共用同一连接，
        # 避免 sqlite :memory: 每连接一个独立空库。
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

        def _override_user():
            if _Ctx.user is None:
                raise HTTPException(status_code=401, detail="请登录后再访问")
            return _Ctx.user

        app = FastAPI()
        app.include_router(router_mod.user_models, prefix="/api")
        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_required_user] = _override_user
        cls.app = app
        cls.client = TestClient(app, raise_server_exceptions=False)

        cls.owner = User(username="owner", password_hash="x", role="user")
        cls.other = User(username="other", password_hash="x", role="user")
        cls.db.add_all([cls.owner, cls.other])
        cls.db.commit()

    @classmethod
    def tearDownClass(cls):
        AuditService.session_factory = None
        _Ctx.user = None
        cls.db.close()
        cls.engine.dispose()

    def setUp(self):
        _Ctx.user = self.owner
        # 每个用例独立：清空上一用例创建的模型，避免 (user_id, display_name)
        # 唯一约束跨用例冲突（同库复用时 create 相同名字会 409）。
        self.db.query(UserModelCredential).delete()
        self.db.commit()

    @classmethod
    def _create_model(cls, name="现场模型", api_key="sk-original-secret"):
        with patch(
            "server.services.model_credentials.socket.getaddrinfo",
            return_value=PUBLIC_RESOLUTION,
        ):
            return cls.client.post(
                "/api/chat/user-models",
                json={
                    "display_name": name,
                    "provider": "openai-compatible",
                    "model_name": "field-model",
                    "api_base": "https://models.example.com/v1",
                    "api_key": api_key,
                },
            )

    @classmethod
    def _stored_key(cls, model_id):
        row = cls.db.query(UserModelCredential).filter_by(id=model_id).one()
        return CredentialCipher(cls.master_key).decrypt(row.encrypted_api_key)


class UserModelCrudHttpTests(UserModelRouterTestBase):
    def test_create_returns_has_api_key_and_key_hint_only(self):
        resp = self._create_model()
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertTrue(body["has_api_key"])
        self.assertEqual(body["key_hint"], "cret")
        encoded = repr(body)
        self.assertNotIn("sk-original-secret", encoded)
        self.assertNotIn("encrypted_api_key", encoded)

    def test_create_with_empty_api_key_rejected_400(self):
        resp = self.client.post(
            "/api/chat/user-models",
            json={
                "display_name": "空密钥",
                "provider": "openai-compatible",
                "model_name": "no-key-model",
                "api_base": "https://models.example.com/v1",
                "api_key": "",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("sk-", resp.text)

    def test_list_returns_owned_models_without_key_material(self):
        created = self._create_model().json()
        resp = self.client.get("/api/chat/user-models")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], created["id"])
        self.assertNotIn("sk-original-secret", repr(body))

    def test_update_empty_api_key_keeps_existing_key(self):
        created = self._create_model().json()
        resp = self.client.patch(
            f"/api/chat/user-models/{created['id']}", json={"api_key": ""}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["has_api_key"])
        self.assertEqual(body["key_hint"], "cret")
        # 数据库中存储的密钥必须保持不变
        self.assertEqual(self._stored_key(created["id"]), "sk-original-secret")

    def test_update_new_api_key_replaces_existing_key(self):
        created = self._create_model().json()
        resp = self.client.patch(
            f"/api/chat/user-models/{created['id']}",
            json={"api_key": "sk-brand-new-7777"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["key_hint"], "7777")
        self.assertEqual(self._stored_key(created["id"]), "sk-brand-new-7777")

    def test_update_other_fields_does_not_touch_key(self):
        created = self._create_model().json()
        with patch(
            "server.services.model_credentials.socket.getaddrinfo",
            return_value=PUBLIC_RESOLUTION,
        ):
            resp = self.client.patch(
                f"/api/chat/user-models/{created['id']}",
                json={"display_name": "改名后"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["display_name"], "改名后")
        self.assertEqual(self._stored_key(created["id"]), "sk-original-secret")

    def test_delete_removes_model(self):
        created = self._create_model().json()
        resp = self.client.delete(f"/api/chat/user-models/{created['id']}")
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self.client.get("/api/chat/user-models").json(), [])

    def test_select_marks_usage(self):
        created = self._create_model().json()
        resp = self.client.post(f"/api/chat/user-models/{created['id']}/select")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.json()["last_used_at"])

    def test_patch_nonexistent_model_returns_404(self):
        resp = self.client.patch(
            "/api/chat/user-models/99999", json={"display_name": "x"}
        )
        self.assertEqual(resp.status_code, 404)


class UserModelIsolationTests(UserModelRouterTestBase):
    def setUp(self):
        super().setUp()  # 重置当前用户并清空上一用例的模型
        created = self._create_model().json()
        self.foreign_id = created["id"]

    def _as_other(self):
        _Ctx.user = self.other

    def test_other_user_cannot_list_foreign_models(self):
        self._as_other()
        resp = self.client.get("/api/chat/user-models")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_other_user_patch_foreign_model_returns_404(self):
        self._as_other()
        resp = self.client.patch(
            f"/api/chat/user-models/{self.foreign_id}", json={"display_name": "偷改"}
        )
        self.assertEqual(resp.status_code, 404)

    def test_other_user_delete_foreign_model_returns_404(self):
        self._as_other()
        resp = self.client.delete(f"/api/chat/user-models/{self.foreign_id}")
        self.assertEqual(resp.status_code, 404)

    def test_other_user_select_foreign_model_returns_404(self):
        self._as_other()
        resp = self.client.post(f"/api/chat/user-models/{self.foreign_id}/select")
        self.assertEqual(resp.status_code, 404)


class UserModelValidateTests(UserModelRouterTestBase):
    def test_validate_public_endpoint_returns_valid(self):
        with patch.object(router_mod.httpx, "AsyncClient", _FakeAsyncClient):
            _FakeAsyncClient.status_code = 200
            with patch(
                "server.services.model_credentials.socket.getaddrinfo",
                return_value=PUBLIC_RESOLUTION,
            ):
                resp = self.client.post(
                    "/api/chat/user-models/validate",
                    json={
                        "api_base": "https://models.example.com/v1",
                        "api_key": "sk-check",
                    },
                )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"valid": True})

    def test_validate_loopback_endpoint_returns_400(self):
        with patch(
            "server.services.model_credentials.socket.getaddrinfo",
            return_value=PRIVATE_RESOLUTION,
        ):
            resp = self.client.post(
                "/api/chat/user-models/validate",
                json={
                    "api_base": "https://127.0.0.1/v1",
                    "api_key": "sk-check",
                },
            )
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("sk-check", resp.text)

    def test_validate_upstream_error_returns_400(self):
        with patch.object(router_mod.httpx, "AsyncClient", _FakeAsyncClient):
            _FakeAsyncClient.status_code = 500
            with patch(
                "server.services.model_credentials.socket.getaddrinfo",
                return_value=PUBLIC_RESOLUTION,
            ):
                resp = self.client.post(
                    "/api/chat/user-models/validate",
                    json={
                        "api_base": "https://models.example.com/v1",
                        "api_key": "sk-check",
                    },
                )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
