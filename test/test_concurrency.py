import ast
import asyncio
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

import server.services.concurrency as concurrency
from server.services.concurrency import BoundedGate


ROOT = Path(__file__).resolve().parents[1]


def _install_chat_src_shim() -> None:
    """让被测路由可在无 Milvus/Neo4j/MySQL 的主机导入（同 test_chat_stream_route）。

    server.routers 包 __init__ 会连锁导入 college_router → db_manager_college
    （要求 MYSQL_PASSWORD）以及真实 src → KnowledgeBase（Milvus ConnectionError），
    因此直接 spec 加载单个路由文件，并用轻量 src 桩满足其 import 期名称。
    """
    if "src" in sys.modules and getattr(sys.modules["src"], "_sage_chat_shim", False):
        return

    class _StubLogger:
        def info(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass
        def warning(self, *args, **kwargs): pass
        def debug(self, *args, **kwargs): pass

    save_dir = tempfile.mkdtemp(prefix="sage-test-save-")
    src = types.ModuleType("src")
    src.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-blocking")
    src.config = types.SimpleNamespace(
        save_dir=save_dir,
        default_agent_id="default",
        model_names=[],
        save=lambda *args, **kwargs: None,
        _save_models_to_file=lambda *args, **kwargs: None,
        get=lambda key, default=None: default,
    )
    src.retriever = types.SimpleNamespace()
    src.knowledge_base = types.SimpleNamespace()
    src.graph_base = types.SimpleNamespace(close=lambda: None)
    src.shutdown_runtime = lambda: None
    src._sage_chat_shim = True
    sys.modules["src"] = src

    utils = types.ModuleType("src.utils")
    utils.logger = _StubLogger()
    sys.modules["src.utils"] = utils
    logging_config = types.ModuleType("src.utils.logging_config")
    logging_config.logger = _StubLogger()
    sys.modules["src.utils.logging_config"] = logging_config
    prompts = types.ModuleType("src.utils.prompts")
    prompts.get_system_prompt = lambda *args, **kwargs: None
    sys.modules["src.utils.prompts"] = prompts

    history_spec = importlib.util.spec_from_file_location(
        "src.core.history", ROOT / "src" / "core" / "history.py"
    )
    history_mod = importlib.util.module_from_spec(history_spec)
    sys.modules["src.core.history"] = history_mod
    history_spec.loader.exec_module(history_mod)
    core = types.ModuleType("src.core")
    core.HistoryManager = history_mod.HistoryManager
    sys.modules["src.core"] = core

    agents = types.ModuleType("src.agents")
    agents.agent_manager = types.SimpleNamespace()
    sys.modules["src.agents"] = agents
    tools_factory = types.ModuleType("src.agents.tools_factory")
    tools_factory.get_all_tools = lambda *args, **kwargs: []
    sys.modules["src.agents.tools_factory"] = tools_factory
    models = types.ModuleType("src.models")
    models.select_model = lambda *args, **kwargs: None
    sys.modules["src.models"] = models

    os.environ["SAGE_DB_PATH"] = os.path.join(
        tempfile.mkdtemp(prefix="sage-test-db-"), "srv.db"
    )


_install_chat_src_shim()


def _spec_load(module_name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(
        module_name, ROOT / relative_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


chat_router = _spec_load("chat_router_under_test", "server/routers/chat_router.py")
multimodal_proxy_router = _spec_load(
    "multimodal_proxy_router_under_test", "server/routers/multimodal_proxy_router.py"
)
data_router = _spec_load("data_router_under_test", "server/routers/data_router.py")


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_gate_rejects_when_capacity_does_not_free(self):
        gate = BoundedGate("model", limit=1, acquire_timeout=0.01)

        async with gate:
            with self.assertRaises(HTTPException) as context:
                async with gate:
                    pass

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.headers, {"Retry-After": "2"})

    async def test_capacity_is_released_after_exception(self):
        gate = BoundedGate("model", limit=1, acquire_timeout=0.1)

        with self.assertRaises(RuntimeError):
            async with gate:
                raise RuntimeError("boom")

        async with gate:
            self.assertEqual(gate.in_use, 1)

    async def test_named_gates_have_independent_default_limits(self):
        expected = {
            "chat_gate": 2,
            "retrieval_gate": 4,
            "graph_import_gate": 1,
            "upstream_proxy_gate": 16,
        }

        for name, limit in expected.items():
            gate = getattr(concurrency, name)
            self.assertIsInstance(gate, BoundedGate)
            self.assertEqual(gate.limit, limit)


class StreamingGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_proxy_holds_gate_until_stream_finishes(self):

        gate = BoundedGate("image-stream", limit=1, acquire_timeout=0.1)

        class FakeResponse:
            status_code = 200
            is_redirect = False
            headers = {"content-type": "image/png"}

            def __init__(self):
                self.closed = False

            async def aiter_bytes(self, chunk_size):
                self.chunk_size = chunk_size
                yield b"png"

            async def aclose(self):
                self.closed = True

        response = FakeResponse()

        class FakeClient:
            def build_request(self, *args, **kwargs):
                return object()

            async def send(self, request, stream):
                self.request = request
                self.stream = stream
                return response

        with (
            patch.object(chat_router, "upstream_proxy_gate", gate),
            patch.object(chat_router, "get_multimodal_client", return_value=FakeClient()),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream"),
        ):
            proxy_response = await chat_router.get_multimodal_image(
                kbId="kb",
                fileId="file",
                imagePath="image.png",
                current_user=object(),
            )

            self.assertEqual(gate.in_use, 1)
            body = b"".join([chunk async for chunk in proxy_response.body_iterator])

        self.assertEqual(body, b"png")
        self.assertTrue(response.closed)
        self.assertEqual(gate.in_use, 0)

    async def test_image_proxy_releases_gate_when_stream_is_cancelled(self):

        gate = BoundedGate("image-stream", limit=1, acquire_timeout=0.1)

        class FakeResponse:
            status_code = 200
            is_redirect = False
            headers = {"content-type": "image/png"}

            def __init__(self):
                self.closed = False

            async def aiter_bytes(self, chunk_size):
                yield b"first"
                yield b"second"

            async def aclose(self):
                self.closed = True

        response = FakeResponse()

        class FakeClient:
            def build_request(self, *args, **kwargs):
                return object()

            async def send(self, request, stream):
                return response

        with (
            patch.object(chat_router, "upstream_proxy_gate", gate),
            patch.object(chat_router, "get_multimodal_client", return_value=FakeClient()),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream"),
        ):
            proxy_response = await chat_router.get_multimodal_image(
                kbId="kb",
                fileId="file",
                imagePath="image.png",
                current_user=object(),
            )
            iterator = proxy_response.body_iterator
            self.assertEqual(await anext(iterator), b"first")
            self.assertEqual(gate.in_use, 1)
            await iterator.aclose()

        self.assertTrue(response.closed)
        self.assertEqual(gate.in_use, 0)

    async def test_proxy_stream_releases_gates_when_request_build_fails(self):
        # Stage J（B3）后不再有 proxy_multimodal_request 通配入口；构建/发送/释放
        # 边界收敛在 _proxy_stream（_Gates = category gate + upstream_proxy_gate）。
        gate = BoundedGate("generic-proxy", limit=1, acquire_timeout=0.1)

        class FakeQueryParams:
            def multi_items(self):
                return []

        class FakeRequest:
            method = "GET"
            headers = {}
            query_params = FakeQueryParams()

            async def stream(self):
                if False:
                    yield b""

        class FakeClient:
            def build_request(self, *args, **kwargs):
                raise ValueError("invalid upstream request")

        spec = SimpleNamespace(
            method="GET",
            path="kb/list",
            body=multimodal_proxy_router.BODY_NONE,
        )
        with (
            patch.object(multimodal_proxy_router, "upstream_proxy_gate", gate),
            patch.object(
                multimodal_proxy_router,
                "get_multimodal_client",
                return_value=FakeClient(),
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await multimodal_proxy_router._proxy_stream(
                    request=FakeRequest(),
                    remote_url="http://upstream/kb/list",
                    spec=spec,
                    headers={},
                    trace_id="test-trace",
                    timeout=object(),
                )
            self.assertEqual(ctx.exception.status_code, 502)

        self.assertEqual(gate.in_use, 0)

    async def test_graph_download_does_not_buffer_before_streaming(self):

        gate = BoundedGate("graph-download", limit=1, acquire_timeout=0.1)

        class FakeResponse:
            status_code = 200
            headers = {"content-type": "application/octet-stream"}

            def __init__(self):
                self.closed = False

            async def aiter_bytes(self, chunk_size):
                yield b"part-one"
                yield b"part-two"

            async def aclose(self):
                self.closed = True

        upstream_response = FakeResponse()

        class FakeClient:
            def build_request(self, method, path):
                self.request = (method, path)
                return object()

            async def send(self, request, stream):
                self.stream = stream
                return upstream_response

        client = FakeClient()
        with (
            patch.object(data_router, "upstream_proxy_gate", gate),
            patch.object(data_router, "get_graph_worker_client", return_value=client),
        ):
            response = await data_router.api_download_file(
                graph_type="ground",
                file_name="graph.csv",
                current_user=object(),
            )
            self.assertTrue(client.stream)
            self.assertEqual(gate.in_use, 1)
            body = b"".join([chunk async for chunk in response.body_iterator])

        self.assertEqual(body, b"part-onepart-two")
        self.assertTrue(upstream_response.closed)
        self.assertEqual(gate.in_use, 0)


class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_multimodal_image_build_request_runtime_error_releases_gate(self):

        gate = BoundedGate("image-cancel-build", limit=1, acquire_timeout=0.1)

        class FakeClient:
            def build_request(self, *args, **kwargs):
                raise RuntimeError("bad params")

        with (
            patch.object(chat_router, "upstream_proxy_gate", gate),
            patch.object(chat_router, "get_multimodal_client", return_value=FakeClient()),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream"),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await chat_router.get_multimodal_image(
                    kbId="kb",
                    fileId="file",
                    imagePath="page/image.png",
                    current_user=object(),
                )
            self.assertEqual(ctx.exception.status_code, 502)

        self.assertEqual(gate.in_use, 0)

    async def test_get_multimodal_image_send_cancelled_releases_gate(self):

        gate = BoundedGate("image-cancel-send", limit=1, acquire_timeout=0.1)

        class FakeClient:
            def build_request(self, *args, **kwargs):
                return object()

            async def send(self, request, stream):
                raise asyncio.CancelledError()

        with (
            patch.object(chat_router, "upstream_proxy_gate", gate),
            patch.object(chat_router, "get_multimodal_client", return_value=FakeClient()),
            patch.object(chat_router, "get_multimodal_api_base", return_value="http://upstream"),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await chat_router.get_multimodal_image(
                    kbId="kb",
                    fileId="file",
                    imagePath="page/image.png",
                    current_user=object(),
                )

        self.assertEqual(gate.in_use, 0)

    async def test_proxy_stream_send_cancelled_releases_gates(self):
        # Stage J（B3）后白名单入口由 _proxy_stream 统一持有 _Gates，取消必须释放。
        gate = BoundedGate("generic-cancel", limit=1, acquire_timeout=0.1)

        class FakeQueryParams:
            def multi_items(self):
                return []

        class FakeRequest:
            method = "GET"
            headers = {}
            query_params = FakeQueryParams()

            async def stream(self):
                if False:
                    yield b""

        class FakeClient:
            def build_request(self, *args, **kwargs):
                return object()

            async def send(self, request, stream):
                raise asyncio.CancelledError()

        spec = SimpleNamespace(
            method="GET",
            path="kb/list",
            body=multimodal_proxy_router.BODY_NONE,
        )
        with (
            patch.object(multimodal_proxy_router, "upstream_proxy_gate", gate),
            patch.object(
                multimodal_proxy_router,
                "get_multimodal_client",
                return_value=FakeClient(),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await multimodal_proxy_router._proxy_stream(
                    request=FakeRequest(),
                    remote_url="http://upstream/kb/list",
                    spec=spec,
                    headers={},
                    trace_id="test-trace",
                    timeout=object(),
                )

        self.assertEqual(gate.in_use, 0)

    async def test_api_download_file_send_cancelled_releases_gate(self):

        gate = BoundedGate("download-cancel", limit=1, acquire_timeout=0.1)

        class FakeClient:
            def build_request(self, method, path):
                return object()

            async def send(self, request, stream):
                raise asyncio.CancelledError()

        with (
            patch.object(data_router, "upstream_proxy_gate", gate),
            patch.object(data_router, "get_graph_worker_client", return_value=FakeClient()),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await data_router.api_download_file(
                    graph_type="ground",
                    file_name="graph.csv",
                    current_user=object(),
                )

        self.assertEqual(gate.in_use, 0)


class RuntimeWiringTests(unittest.TestCase):
    @staticmethod
    def _blocking_calls_in_async(path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        blocked = {"requests.get", "requests.post", "requests.put",
                   "requests.patch", "requests.delete", "time.sleep",
                   "subprocess.run"}
        matches = []

        for function in ast.walk(tree):
            if not isinstance(function, ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                name = None
                if isinstance(node.func, ast.Attribute) and isinstance(
                    node.func.value, ast.Name
                ):
                    name = f"{node.func.value.id}.{node.func.attr}"
                if name in blocked:
                    matches.append((function.name, name, node.lineno))

        return matches

    def test_blocking_executor_is_explicitly_bounded(self):
        source = (ROOT / "src" / "__init__.py").read_text(encoding="utf-8")

        self.assertIn("BLOCKING_WORKERS", source)
        self.assertIn("max_workers=", source)
        self.assertIn("thread_name_prefix=\"sage-blocking\"", source)
        self.assertIn("wait=True", source)
        self.assertIn("cancel_futures=True", source)
        self.assertIn("graph_base.close", source)
        self.assertNotIn(
            'int(os.getenv("BLOCKING_WORKERS"',
            source,
        )

    def test_application_lifespan_closes_clients_and_runtime(self):
        source = (ROOT / "server" / "main.py").read_text(encoding="utf-8")

        self.assertIn("close_multimodal_client", source)
        self.assertIn("close_graph_worker_client", source)
        self.assertIn("shutdown_runtime", source)
        self.assertIn("lifespan=app_lifespan", source)
        self.assertNotIn("threads=10", source)
        self.assertNotIn("workers=10", source)
        self.assertNotIn("reload=True", source)

    def test_sqlite_engine_uses_wal_busy_timeout_and_foreign_keys(self):
        source = (ROOT / "server" / "db_manager.py").read_text(encoding="utf-8")

        self.assertIn('"timeout": 30', source)
        self.assertIn('"check_same_thread": False', source)
        self.assertIn("pool_pre_ping=True", source)
        self.assertIn("PRAGMA journal_mode=WAL", source)
        self.assertIn("PRAGMA busy_timeout=30000", source)
        self.assertIn("PRAGMA foreign_keys=ON", source)
        self.assertRegex(source, r"finally:\s+cursor\.close\(\)")

    def test_async_routes_have_no_blocking_http_sleep_or_subprocess_calls(self):
        route_paths = [
            ROOT / "server" / "routers" / "chat_router.py",
            ROOT / "server" / "routers" / "data_router.py",
            ROOT / "server" / "routers" / "multimodal_proxy_router.py",
        ]
        matches = []
        for path in route_paths:
            matches.extend(
                (path.name, *match)
                for match in self._blocking_calls_in_async(path)
            )

        self.assertEqual(matches, [])

    def test_heavy_route_boundaries_use_separate_gates(self):
        chat_source = (
            ROOT / "server" / "routers" / "chat_router.py"
        ).read_text(encoding="utf-8")
        data_source = (
            ROOT / "server" / "routers" / "data_router.py"
        ).read_text(encoding="utf-8")
        multimodal_source = (
            ROOT / "server" / "routers" / "multimodal_proxy_router.py"
        ).read_text(encoding="utf-8")

        self.assertIn("async with chat_gate", chat_source)
        self.assertGreaterEqual(chat_source.count("await chat_gate.__aenter__()"), 2)
        self.assertGreaterEqual(chat_source.count("await chat_gate.__aexit__"), 2)
        self.assertIn("async with retrieval_gate", chat_source)
        self.assertIn("run_in_executor(executor", chat_source)
        self.assertIn("async def generate_response", chat_source)
        self.assertIn("await upstream_proxy_gate.__aenter__()", chat_source)
        self.assertIn("await upstream_proxy_gate.__aexit__", chat_source)
        self.assertIn("async with retrieval_gate", data_source)
        self.assertIn("async with graph_import_gate", data_source)
        self.assertIn("async with upstream_proxy_gate", data_source)
        self.assertIn(
            "self._gates = (category_gate(category), upstream_proxy_gate)",
            multimodal_source,
        )
        self.assertIn("await self._gates[1].__aenter__()", multimodal_source)

    def test_question_normalization_removes_curly_quotes_and_period(self):
        """process_question_stats must strip straight, left, and right
        double quotes plus the Chinese full stop -- not three copies of
        the straight quote."""
        chat_source = (
            ROOT / "server" / "routers" / "chat_router.py"
        ).read_text(encoding="utf-8")

        # Must contain all four distinct replacement targets.
        self.assertIn("“", chat_source, "Missing left double quote replacement")
        self.assertIn("”", chat_source, "Missing right double quote replacement")
        self.assertIn("。", chat_source, "Missing Chinese full stop replacement")

        # The straight-quote replacement must appear exactly once in the
        # cleaning line (not three duplicate calls).
        cleaning_lines = [
            line for line in chat_source.splitlines()
            if "llm_title" in line and ".replace(" in line and "。" in line
        ]
        self.assertEqual(len(cleaning_lines), 1, "Expected exactly one cleaning line")
        # Count how many times straight double quote U+0022 appears as a
        # replacement target in that line.
        straight_count = cleaning_lines[0].count("'\"'")
        self.assertEqual(
            straight_count, 1,
            f"Straight double quote should appear once, found {straight_count}",
        )

    def test_production_paths_and_graph_download_are_deployable(self):
        data_source = (
            ROOT / "server" / "routers" / "data_router.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("host.docker.internal", data_source)
        self.assertNotRegex(data_source, r"""Path\(["'][A-Za-z]:\\""")

        download_source = data_source[
            data_source.index("async def api_download_file"):
            data_source.index("async def run_graphrag_index")
        ]
        self.assertIn("client.build_request", download_source)
        self.assertIn("client.send", download_source)
        self.assertIn("stream=True", download_source)

    def test_index_nodes_uses_graph_import_gate_for_embedding(self):
        """index_nodes performs graph writes/embedding -- it must use
        graph_import_gate, not retrieval_gate, for the heavy operation."""
        data_source = (
            ROOT / "server" / "routers" / "data_router.py"
        ).read_text(encoding="utf-8")

        # Find the index_nodes function body
        idx = data_source.index("async def index_nodes")
        # End at the next route decorator or end of file
        next_route = data_source.find("\n@data.", idx + 1)
        if next_route == -1:
            body = data_source[idx:]
        else:
            body = data_source[idx:next_route]

        self.assertIn("graph_import_gate", body)
        self.assertNotIn("retrieval_gate", body)
        self.assertIn("add_embedding_to_nodes", body)


if __name__ == "__main__":
    unittest.main()
