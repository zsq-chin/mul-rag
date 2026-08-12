"""阶段 H1：知识图谱错误语义回归测试。

覆盖 CLAUDE_PRODUCTION_RELEASE_MODIFICATION_REQUIREMENTS.md §8 H1：
1. `/graph/add-by-jsonl` 不再返回 HTTP 200 的 `status=failed` 字典——
   图谱未启用 503、文件格式错误 400、UploadError 400、导入失败 500；
2. `/graph/handle` 提交失败/任务失败/上游连接失败 502、超时 504、
   其他异常 500，成功保留 200；
3. 严格请求模型拒绝未知字段（422），file_path 拒绝绝对路径/盘符/UNC/穿越（400）；
4. 全部通过真实 FastAPI TestClient 调 HTTP 路由验证状态码和响应，
   不再直接调用 resolver 当作接口测试；
5. 图谱禁用/不可用时返回可恢复的 503（前端据此显示「未启用/服务不可用」）。

与 test_image_proxy 相同，本模块以「轻量 src 桩 + 直载 data_router 模块文件」
方式加载路由：被测路由在运行期不触碰真实图数据库/知识库设施，桩只提供
import 期名称，不伪造任何路由行为；路由逻辑本身照常经真实 HTTP 执行。
"""

import asyncio
import importlib.util
import logging
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
UPLOADS_DIR = ROOT / "saves" / "data" / "uploads"
FIXTURE_FILE = "h1_fixture.csv"


def _install_src_shim() -> None:
    """让 data_router 可在无 Milvus/模型栈的主机导入。"""
    if "src" in sys.modules and getattr(sys.modules.get("src"), "_sage_graph_router_shim", False):
        return

    fake_src = types.ModuleType("src")
    fake_src.__path__ = [str(ROOT / "src")]
    fake_src._sage_graph_router_shim = True
    fake_src.executor = __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=2)
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

    # src.utils 提供 logger（db_manager / data_router 均引用）
    fake_utils = types.ModuleType("src.utils")
    fake_utils.logger = logging.getLogger("test_graph_router_h1")
    sys.modules["src.utils"] = fake_utils


_install_src_shim()

_spec = importlib.util.spec_from_file_location(
    "data_router_under_test",
    ROOT / "server" / "routers" / "data_router.py",
)
data_router = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(data_router)

from server.utils.auth_middleware import get_required_user, get_superadmin_user  # noqa: E402


def _build_app():
    app = FastAPI()
    app.include_router(data_router.data, prefix="/api")
    app.dependency_overrides[get_superadmin_user] = lambda: object()
    app.dependency_overrides[get_required_user] = lambda: object()
    return app


class _FakeTianshuResponse:
    """极简 httpx 响应替身：json() / raise_for_status()（>=400 抛 HTTPStatusError）。"""

    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://tianshu/tasks/fake")
            raise httpx.HTTPStatusError(
                f"upstream {self.status_code}",
                request=request,
                response=httpx.Response(self.status_code, request=request),
            )


class GraphRouterH1TestBase(unittest.TestCase):
    """公共夹具：真实 HTTP TestClient + 通用上传目录内的裸 file_id 文件。"""

    @classmethod
    def setUpClass(cls):
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        (UPLOADS_DIR / FIXTURE_FILE).write_text("kbId,relation,content\n", encoding="utf-8")
        # 桩里 graph_base=None；替换成共享 Mock，patch.object 才能挂方法
        data_router.graph_base = Mock()
        cls.client = TestClient(_build_app())

    @classmethod
    def tearDownClass(cls):
        (UPLOADS_DIR / FIXTURE_FILE).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# /graph/add-by-jsonl —— 不再返回 HTTP 200 的 status=failed
# ---------------------------------------------------------------------------
class GraphAddByJsonlHttpTests(GraphRouterH1TestBase):
    URL = "/api/data/graph/add-by-jsonl"

    def test_graph_disabled_returns_503(self):
        with patch.object(data_router.config, "enable_knowledge_graph", False):
            resp = self.client.post(self.URL, json={"file_path": FIXTURE_FILE})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "知识图谱未启用")

    def test_non_csv_returns_400(self):
        resp = self.client.post(self.URL, json={"file_path": "h1_fixture.txt"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("文件格式错误", resp.json()["detail"])

    def test_traversal_file_path_returns_400(self):
        resp = self.client.post(self.URL, json={"file_path": "../../etc/passwd"})
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn("etc/passwd", resp.json()["detail"])

    def test_absolute_windows_path_returns_400(self):
        resp = self.client.post(self.URL, json={"file_path": "C:\\windows\\secret.csv"})
        self.assertEqual(resp.status_code, 400)

    def test_missing_file_returns_404(self):
        resp = self.client.post(self.URL, json={"file_path": "does-not-exist.csv"})
        self.assertEqual(resp.status_code, 404)

    def test_unknown_field_rejected_with_422(self):
        resp = self.client.post(self.URL, json={"file_path": FIXTURE_FILE, "evil": 1})
        self.assertEqual(resp.status_code, 422)

    def test_import_failure_returns_500(self):
        with patch.object(data_router.graph_base, "jsonl_file_add_entity", new=AsyncMock(side_effect=RuntimeError("boom"))):
            resp = self.client.post(self.URL, json={"file_path": FIXTURE_FILE})
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.json()["detail"], "添加实体失败")
        # 不把内部异常细节回显给浏览器
        self.assertNotIn("boom", resp.json()["detail"])

    def test_success_returns_200_status_success(self):
        with patch.object(data_router.graph_base, "jsonl_file_add_entity", new=AsyncMock()):
            resp = self.client.post(self.URL, json={"file_path": FIXTURE_FILE})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")


# ---------------------------------------------------------------------------
# /graph/handle —— 4xx/5xx 语义
# ---------------------------------------------------------------------------
class GraphFileHandleHttpTests(GraphRouterH1TestBase):
    URL = "/api/data/graph/handle"

    def _mock_graph_base(self, file_handle_return, copy_output_return=None):
        gb = Mock()
        gb.file_Handle = Mock(return_value=file_handle_return)
        if copy_output_return is not None:
            gb.copy_output = Mock(return_value=copy_output_return)
        return gb

    def _mock_tianshu(self, responses, connect_error=False):
        client = Mock()
        if connect_error:
            client.get = AsyncMock(side_effect=httpx.ConnectError("upstream down"))
            return client
        client.get = AsyncMock(side_effect=responses)
        return client

    def test_traversal_file_path_returns_400(self):
        resp = self.client.post(self.URL, json={"file_path": "../secret.csv"})
        self.assertEqual(resp.status_code, 400)

    def test_unknown_field_rejected_with_422(self):
        resp = self.client.post(self.URL, json={"file_path": FIXTURE_FILE, "evil": 1})
        self.assertEqual(resp.status_code, 422)

    def test_submit_failure_returns_502(self):
        gb = self._mock_graph_base({"success": False, "message": "no task_id"})
        with patch.object(data_router, "graph_base", gb):
            resp = self.client.post(self.URL, json={"file_path": FIXTURE_FILE})
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["detail"], "文件提交失败")

    def test_task_failed_returns_502(self):
        gb = self._mock_graph_base({"task_id": "t1"})
        client = self._mock_tianshu([_FakeTianshuResponse(json_data={"status": "failed"})])
        with (
            patch.object(data_router, "graph_base", gb),
            patch.object(data_router, "get_tianshu_client", return_value=client),
        ):
            resp = self.client.post(self.URL, json={"file_path": FIXTURE_FILE})
        self.assertEqual(resp.status_code, 502)
        self.assertIn("文件处理失败", resp.json()["detail"])

    def test_poll_timeout_returns_504(self):
        gb = self._mock_graph_base({"task_id": "t1"})
        client = self._mock_tianshu([_FakeTianshuResponse(json_data={"status": "processing"})])
        with (
            patch.object(data_router, "graph_base", gb),
            patch.object(data_router, "get_tianshu_client", return_value=client),
            # 负超时保证 loop.time() - start_time 恒大于它，确定性触发 504
            patch.object(data_router, "GRAPH_FILE_HANDLE_TIMEOUT", -1),
        ):
            resp = self.client.post(self.URL, json={"file_path": FIXTURE_FILE})
        self.assertEqual(resp.status_code, 504)
        self.assertIn("处理超时", resp.json()["detail"])

    def test_upstream_connect_error_returns_502(self):
        gb = self._mock_graph_base({"task_id": "t1"})
        client = self._mock_tianshu([], connect_error=True)
        with (
            patch.object(data_router, "graph_base", gb),
            patch.object(data_router, "get_tianshu_client", return_value=client),
        ):
            resp = self.client.post(self.URL, json={"file_path": FIXTURE_FILE})
        self.assertEqual(resp.status_code, 502)
        self.assertIn("图谱服务连接失败", resp.json()["detail"])

    def test_upstream_error_status_returns_502(self):
        gb = self._mock_graph_base({"task_id": "t1"})
        client = self._mock_tianshu([_FakeTianshuResponse(status_code=503)])
        with (
            patch.object(data_router, "graph_base", gb),
            patch.object(data_router, "get_tianshu_client", return_value=client),
        ):
            resp = self.client.post(self.URL, json={"file_path": FIXTURE_FILE})
        self.assertEqual(resp.status_code, 502)
        self.assertIn("图谱服务返回错误", resp.json()["detail"])

    def test_success_returns_200_with_bare_output_name(self):
        gb = self._mock_graph_base(
            {"task_id": "t1"},
            copy_output_return=Path("/app/output/t1/h1_fixture.pdf"),
        )
        client = self._mock_tianshu([_FakeTianshuResponse(json_data={"status": "completed", "result": {"ok": 1}})])
        with (
            patch.object(data_router, "graph_base", gb),
            patch.object(data_router, "get_tianshu_client", return_value=client),
        ):
            resp = self.client.post(self.URL, json={"file_path": FIXTURE_FILE})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "success")
        # 绝不把远端文件系统绝对路径暴露给浏览器
        self.assertEqual(body["output_file"], "h1_fixture.pdf")
        self.assertNotIn("/app/output", body["output_file"])


# ---------------------------------------------------------------------------
# /graph、/graph/index-nodes、/graph/nodes —— 服务不可用语义
# ---------------------------------------------------------------------------
class GraphServiceStateHttpTests(GraphRouterH1TestBase):
    def test_graph_info_unavailable_returns_503(self):
        with patch.object(data_router.graph_base, "get_graph_info", return_value=None):
            resp = self.client.get("/api/data/graph")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "图数据库服务不可用")

    def test_graph_info_success_returns_200(self):
        with patch.object(data_router.graph_base, "get_graph_info", return_value={"node_count": 3}):
            resp = self.client.get("/api/data/graph")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["node_count"], 3)

    def test_index_nodes_not_running_returns_503(self):
        with patch.object(data_router.graph_base, "is_running", return_value=False):
            resp = self.client.post("/api/data/graph/index-nodes", json={"kgdb_name": "neo4j"})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["detail"], "图数据库未启动")

    def test_index_nodes_unknown_field_rejected_with_422(self):
        resp = self.client.post("/api/data/graph/index-nodes", json={"kgdb_name": "neo4j", "evil": 1})
        self.assertEqual(resp.status_code, 422)

    def test_index_nodes_success_returns_200(self):
        with (
            patch.object(data_router.graph_base, "is_running", return_value=True),
            patch.object(data_router.graph_base, "add_embedding_to_nodes", return_value=5),
        ):
            resp = self.client.post("/api/data/graph/index-nodes", json={"kgdb_name": "neo4j"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["indexed_count"], 5)

    def test_graph_nodes_disabled_returns_503(self):
        with patch.object(data_router.config, "enable_knowledge_graph", False):
            resp = self.client.get("/api/data/graph/nodes?kgdb_name=neo4j&num=5")
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
