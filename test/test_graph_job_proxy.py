"""Tests for the main API graph job proxy endpoints (Task 11).

Coverage targets:
  - POST /api/data/graph/jobs (submit)
  - GET /api/data/graph/jobs/{task_id} (get)
  - POST /api/data/graph/jobs/{task_id}/cancel (cancel)
  - POST /api/data/graph/jobs/{task_id}/retry (retry)
  - Authorization: superadmin-only (403 for non-superadmin)
  - Upstream 404/409/5xx faithfully propagated
  - No success-shaped response on upstream failure
  - Connect error -> 502, timeout -> 504
  - task_id validation: 32 lowercase hex chars only; invalid IDs never forwarded
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import importlib.util
from pathlib import Path

# ---------------------------------------------------------------------------
# Isolated module loader for data_router.
#
# We load data_router in a separate fake-package namespace
# (_test_proxy_data_router.*) so its transitive mocks never pollute the real
# ``server.*`` namespace that other test modules need.
# ---------------------------------------------------------------------------

_SERVER_ROOT = str(Path(__file__).resolve().parent.parent / "server")

# Build a minimal fake package tree under _test_proxy_data_router so that
# data_router.py's relative imports (``from server.utils.auth_middleware
# import ...``) resolve against our stubs, not the real server tree.
_PKG = types.ModuleType("_test_proxy_data_router")
_PKG.__path__ = [_SERVER_ROOT]
sys.modules["_test_proxy_data_router"] = _PKG

# Stub heavy transitive deps that data_router pulls in via ``from src import ...``
for _mod_name in [
    'src', 'src.utils', 'src.utils.logging_config', 'src.utils.logger',
    'src.executor', 'src.retriever', 'src.config', 'src.knowledge_base',
    'src.graph_base', 'src.core', 'src.core.graphbase',
    'src.agents', 'src.agents.react',
    'langchain_core', 'langchain_core.messages',
]:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

# Build a fake server package tree for the isolated loader
def _make_fake_pkg(name, path):
    mod = types.ModuleType(name)
    mod.__path__ = [path]
    sys.modules[name] = mod
    return mod

_fake_server = _make_fake_pkg("_test_proxy_data_router.server", _SERVER_ROOT)
_fake_routers = _make_fake_pkg("_test_proxy_data_router.server.routers", _SERVER_ROOT + "/routers")
_fake_utils = _make_fake_pkg("_test_proxy_data_router.server.utils", _SERVER_ROOT + "/utils")
_fake_models = _make_fake_pkg("_test_proxy_data_router.server.models", _SERVER_ROOT + "/models")
_fake_services = _make_fake_pkg("_test_proxy_data_router.server.services", _SERVER_ROOT + "/services")

_fake_server.routers = _fake_routers
_fake_server.utils = _fake_utils
_fake_server.models = _fake_models
_fake_server.services = _fake_services

# Mock heavy leaf modules under the fake namespace
for _leaf in [
    'server.utils.auth_utils',
    'server.db_manager',
    'server.models.user_model',
    'server.services.graph_import',
    'server.services.http_clients',
]:
    _full = '_test_proxy_data_router.' + _leaf
    sys.modules[_full] = MagicMock()

# Stub auth_middleware
_auth_mod = types.ModuleType("_test_proxy_data_router.server.utils.auth_middleware")
_auth_mod.__path__ = []

async def _get_required_user():
    raise HTTPException(status_code=401, detail="Not authenticated")

async def _get_superadmin_user():
    raise HTTPException(status_code=401, detail="Not authenticated")

_auth_mod.get_required_user = _get_required_user
_auth_mod.get_superadmin_user = _get_superadmin_user
sys.modules["_test_proxy_data_router.server.utils.auth_middleware"] = _auth_mod
_fake_utils.auth_middleware = _auth_mod

# Now load the real data_router.py under our isolated namespace.
# We temporarily point ``server`` to our fake tree so the module's own
# ``from server.xxx import ...`` statements resolve against stubs.
_real_server = sys.modules.get("server")
_real_routers = sys.modules.get("server.routers")
sys.modules["server"] = _fake_server
sys.modules["server.routers"] = _fake_routers

try:
    _spec = importlib.util.spec_from_file_location(
        "_test_proxy_data_router.server.routers.data_router",
        str(Path(_SERVER_ROOT) / "routers" / "data_router.py"),
    )
    _data_router_mod = importlib.util.module_from_spec(_spec)
    sys.modules["_test_proxy_data_router.server.routers.data_router"] = _data_router_mod
    _fake_routers.data_router = _data_router_mod
    _spec.loader.exec_module(_data_router_mod)
finally:
    # Restore the real server package so other tests are unaffected
    if _real_server is not None:
        sys.modules["server"] = _real_server
    else:
        sys.modules.pop("server", None)
    if _real_routers is not None:
        sys.modules["server.routers"] = _real_routers
    else:
        sys.modules.pop("server.routers", None)

data = _data_router_mod.data
_proxy_graph_worker = _data_router_mod._proxy_graph_worker
GraphJobCreateRequest = _data_router_mod.GraphJobCreateRequest
_validate_task_id = _data_router_mod._validate_task_id

# Get the real auth dependency for dependency_overrides
from server.utils.auth_middleware import get_superadmin_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A valid 32-lowercase-hex-char task ID (matches worker real format).
VALID_TASK_ID = "a" * 32
VALID_TASK_ID_2 = "b" * 32


def _make_app():
    """Build a minimal FastAPI app mounting only the data router."""
    app = FastAPI()
    app.include_router(data)
    return app


def _mock_user(role: str = "superadmin"):
    user = MagicMock()
    user.id = 1
    user.role = role
    user.username = "testuser"
    return user


class _FakeResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code: int, json_body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_body
        self.text = text or str(json_body or "")

    def json(self):
        if self._json is None:
            raise ValueError("not json")
        return self._json


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


class GraphJobProxyAuthTests(unittest.TestCase):
    """Verify that only superadmin users can access graph job proxy endpoints."""

    def setUp(self):
        self.app = _make_app()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_submit_requires_superadmin(self):
        self.app.dependency_overrides[get_superadmin_user] = (
            lambda: (_ for _ in ()).throw(HTTPException(status_code=403, detail="Forbidden"))
        )
        try:
            resp = self.client.post("/data/graph/jobs", json={"graph_type": "ground"})
            self.assertEqual(resp.status_code, 403)
        finally:
            self.app.dependency_overrides.clear()

    def test_get_requires_superadmin(self):
        self.app.dependency_overrides[get_superadmin_user] = (
            lambda: (_ for _ in ()).throw(HTTPException(status_code=403, detail="Forbidden"))
        )
        try:
            resp = self.client.get(f"/data/graph/jobs/{VALID_TASK_ID}")
            self.assertEqual(resp.status_code, 403)
        finally:
            self.app.dependency_overrides.clear()

    def test_cancel_requires_superadmin(self):
        self.app.dependency_overrides[get_superadmin_user] = (
            lambda: (_ for _ in ()).throw(HTTPException(status_code=403, detail="Forbidden"))
        )
        try:
            resp = self.client.post(f"/data/graph/jobs/{VALID_TASK_ID}/cancel")
            self.assertEqual(resp.status_code, 403)
        finally:
            self.app.dependency_overrides.clear()

    def test_retry_requires_superadmin(self):
        self.app.dependency_overrides[get_superadmin_user] = (
            lambda: (_ for _ in ()).throw(HTTPException(status_code=403, detail="Forbidden"))
        )
        try:
            resp = self.client.post(f"/data/graph/jobs/{VALID_TASK_ID}/retry")
            self.assertEqual(resp.status_code, 403)
        finally:
            self.app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# task_id validation tests
# ---------------------------------------------------------------------------


class GraphJobTaskIdValidationTests(unittest.TestCase):
    """Verify that invalid task IDs are rejected at the API boundary
    and never forwarded to the graph worker."""

    def setUp(self):
        self.app = _make_app()
        self.app.dependency_overrides[get_superadmin_user] = lambda: _mock_user()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.app.dependency_overrides.clear()

    # -- Direct helper tests --

    def test_helper_accepts_valid_id(self):
        """32 lowercase hex chars pass validation."""
        result = _validate_task_id("a" * 32)
        self.assertEqual(result, "a" * 32)

    def test_helper_rejects_uppercase(self):
        with self.assertRaises(HTTPException) as ctx:
            _validate_task_id("A" * 32)
        self.assertEqual(ctx.exception.status_code, 422)

    def test_helper_rejects_too_short(self):
        with self.assertRaises(HTTPException):
            _validate_task_id("a" * 31)

    def test_helper_rejects_too_long(self):
        with self.assertRaises(HTTPException):
            _validate_task_id("a" * 33)

    def test_helper_rejects_dots_traversal(self):
        with self.assertRaises(HTTPException):
            _validate_task_id("..")

    def test_helper_rejects_slashes(self):
        with self.assertRaises(HTTPException):
            _validate_task_id("abc/def")
        with self.assertRaises(HTTPException):
            _validate_task_id("abc%2Fdef")

    def test_helper_rejects_punctuation(self):
        with self.assertRaises(HTTPException):
            _validate_task_id("a" * 31 + "!")

    def test_helper_rejects_empty(self):
        with self.assertRaises(HTTPException):
            _validate_task_id("")

    def test_helper_rejects_trailing_newline(self):
        """32-lowercase-hex ID followed by \\n must be rejected (fullmatch guard).

        Python ``re.match`` with ``$`` matches before a trailing newline,
        so the pattern must use ``fullmatch`` to block this edge case.
        """
        with self.assertRaises(HTTPException) as ctx:
            _validate_task_id("a" * 32 + "\n")
        self.assertEqual(ctx.exception.status_code, 422)

    # -- HTTP endpoint tests: invalid IDs return 422 and do NOT forward --

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_get_invalid_id_returns_422(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        resp = self.client.get("/data/graph/jobs/not-a-valid-id!")
        self.assertEqual(resp.status_code, 422)
        mock_client.request.assert_not_called()

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_cancel_invalid_id_returns_422(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        resp = self.client.post("/data/graph/jobs/not-a-valid-id!/cancel")
        self.assertEqual(resp.status_code, 422)
        mock_client.request.assert_not_called()

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_retry_invalid_id_returns_422(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        resp = self.client.post(f"/data/graph/jobs/{'A' * 32}/retry")
        self.assertEqual(resp.status_code, 422)
        mock_client.request.assert_not_called()

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_dot_dot_never_forwards_to_worker(self, mock_client_fn):
        """Starlette normalizes '..' out of the path before the handler runs.
        The key guarantee is that the worker is never called with a traversal."""
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        # '..' normalises to /data/graph/jobs which is a different route.
        # The response is 200 (submit endpoint) or 405 -- either way
        # the worker must NOT receive a request with '..' in the path.
        self.client.get("/data/graph/jobs/..")
        for call in mock_client.request.call_args_list:
            path = call[0][1] if len(call[0]) > 1 else call[1].get('path', '')
            self.assertNotIn('..', path, "Worker must never receive '..' in path")

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_encoded_slash_never_forwards_to_worker(self, mock_client_fn):
        """Starlette decodes %2F to '/' before routing, turning it into a
        different route.  The worker must never receive an encoded slash."""
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        # %2F is decoded to '/' making it /data/graph/jobs/abc/def
        # which does not match any endpoint -> 404.
        resp = self.client.get("/data/graph/jobs/abc%2Fdef")
        # Either 404 (no matching route) or 422 (our validation) --
        # never 200 with a proxied path containing the slash.
        self.assertIn(resp.status_code, [404, 422])
        for call in mock_client.request.call_args_list:
            path = call[0][1] if len(call[0]) > 1 else ''
            self.assertNotIn('abc/def', path)

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_get_uppercase_32_chars_returns_422(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client_fn.return_value = mock_client

        resp = self.client.get(f"/data/graph/jobs/{'AB' * 16}")
        self.assertEqual(resp.status_code, 422)
        mock_client.request.assert_not_called()

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_valid_id_forwards_to_worker(self, mock_client_fn):
        """A valid 32-hex-char ID IS forwarded to the worker."""
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_FakeResponse(200, {"id": VALID_TASK_ID, "status": "building"})
        )
        mock_client_fn.return_value = mock_client

        resp = self.client.get(f"/data/graph/jobs/{VALID_TASK_ID}")
        self.assertEqual(resp.status_code, 200)
        mock_client.request.assert_called_once()


# ---------------------------------------------------------------------------
# Upstream error propagation tests
# ---------------------------------------------------------------------------


class GraphJobProxyUpstreamTests(unittest.TestCase):
    """Verify faithful upstream error propagation."""

    def setUp(self):
        self.app = _make_app()
        self.app.dependency_overrides[get_superadmin_user] = lambda: _mock_user()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.app.dependency_overrides.clear()

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_upstream_404_propagated(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_FakeResponse(404, {"detail": "Job not found"})
        )
        mock_client_fn.return_value = mock_client

        resp = self.client.get(f"/data/graph/jobs/{VALID_TASK_ID}")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("Job not found", resp.json().get("detail", ""))

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_upstream_409_propagated_not_success_shaped(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_FakeResponse(409, {"detail": "Active job already exists"})
        )
        mock_client_fn.return_value = mock_client

        resp = self.client.post("/data/graph/jobs", json={"graph_type": "ground"})
        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertNotEqual(body.get("status"), "success",
                            "Error response must not use success shape")

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_upstream_500_propagated(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_FakeResponse(500, {"detail": "Internal server error"})
        )
        mock_client_fn.return_value = mock_client

        resp = self.client.get(f"/data/graph/jobs/{VALID_TASK_ID}")
        self.assertEqual(resp.status_code, 500)

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_connect_error_returns_502(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        mock_client_fn.return_value = mock_client

        resp = self.client.get(f"/data/graph/jobs/{VALID_TASK_ID}")
        self.assertEqual(resp.status_code, 502)

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_timeout_returns_504(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )
        mock_client_fn.return_value = mock_client

        resp = self.client.get(f"/data/graph/jobs/{VALID_TASK_ID}")
        self.assertEqual(resp.status_code, 504)


# ---------------------------------------------------------------------------
# Success pass-through tests
# ---------------------------------------------------------------------------


class GraphJobProxySuccessTests(unittest.TestCase):
    """Verify successful proxy pass-through."""

    def setUp(self):
        self.app = _make_app()
        self.app.dependency_overrides[get_superadmin_user] = lambda: _mock_user()
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.app.dependency_overrides.clear()

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_submit_returns_202_with_job_fields(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_FakeResponse(202, {
                "id": VALID_TASK_ID,
                "graph_type": "ground",
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "created_at": "2026-07-22T00:00:00",
                "started_at": None,
                "finished_at": None,
                "cancel_requested": False,
                "input_count": 0,
                "relationship_count": 0,
                "artifact_path": "",
                "artifact_sha256": "",
                "error_summary": "",
                "log_tail": "",
            })
        )
        mock_client_fn.return_value = mock_client

        resp = self.client.post("/data/graph/jobs", json={"graph_type": "ground"})
        self.assertEqual(resp.status_code, 202)
        body = resp.json()
        self.assertEqual(body["id"], VALID_TASK_ID)
        self.assertEqual(body["status"], "queued")

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_cancel_returns_updated_status(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_FakeResponse(200, {
                "id": VALID_TASK_ID,
                "status": "cancelling",
                "cancel_requested": True,
                "progress": 25,
            })
        )
        mock_client_fn.return_value = mock_client

        resp = self.client.post(f"/data/graph/jobs/{VALID_TASK_ID}/cancel")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["cancel_requested"])

    @patch.object(_data_router_mod, "get_graph_worker_client")
    def test_retry_returns_queued(self, mock_client_fn):
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(
            return_value=_FakeResponse(200, {
                "id": VALID_TASK_ID,
                "status": "queued",
                "progress": 0,
            })
        )
        mock_client_fn.return_value = mock_client

        resp = self.client.post(f"/data/graph/jobs/{VALID_TASK_ID}/retry")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "queued")


if __name__ == "__main__":
    unittest.main()
