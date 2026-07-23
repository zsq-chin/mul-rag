"""Static configuration tests for graphrag-worker.

Verifies docker-compose.yml, docker/graphrag.Dockerfile, and
docker-compose.prod.yml (production overrides) contain the expected mounts,
environment, ports, healthcheck, and pinned dependencies without fragile
full-text matching.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
PROD_COMPOSE_PATH = REPO_ROOT / "docker-compose.prod.yml"
DOCKERFILE_PATH = REPO_ROOT / "docker" / "graphrag.Dockerfile"

# -- YAML custom tag support for Compose extensions (!override, !reset) ------

yaml.SafeLoader.add_constructor("!override", lambda l, n: l.construct_sequence(n))
yaml.SafeLoader.add_constructor("!reset", lambda l, n: [])


def _load_compose() -> dict:
    with open(COMPOSE_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_prod_compose() -> dict:
    """Load docker-compose.prod.yml as a standalone production override file."""
    with open(PROD_COMPOSE_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_dockerfile() -> str:
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


def _assert_env_present(testcase: unittest.TestCase, env_list: list[str], entry: str) -> None:
    testcase.assertIn(entry, env_list, f"environment missing: {entry!r}")


def _assert_no_hardcoded_token(testcase: unittest.TestCase, env_list: list[str]) -> None:
    """GRAPH_INTERNAL_TOKEN must use compose interpolation, not a literal."""
    for entry in env_list:
        if not entry.startswith("GRAPH_INTERNAL_TOKEN="):
            continue
        value = entry.split("=", 1)[1]
        testcase.assertRegex(
            value, r"^\$\{.+\}$",
            f"GRAPH_INTERNAL_TOKEN must be interpolation, got literal: {value!r}",
        )
        return
    testcase.fail("GRAPH_INTERNAL_TOKEN not found in environment")


# -- Expected base-API environment entries (data-driven) ---------------------

_BASE_ENV_ENTRIES: list[str] = [
    # required secrets
    "JWT_SECRET_KEY=${JWT_SECRET_KEY:?Set JWT_SECRET_KEY in .env}",
    # optional keys
    "BOCHA_API_KEY=${BOCHA_API_KEY:-}",
    # concurrency limits
    "BLOCKING_WORKERS=${BLOCKING_WORKERS:-8}",
    "CHAT_CONCURRENCY=${CHAT_CONCURRENCY:-2}",
    "RETRIEVAL_CONCURRENCY=${RETRIEVAL_CONCURRENCY:-4}",
    "GRAPH_IMPORT_CONCURRENCY=${GRAPH_IMPORT_CONCURRENCY:-1}",
    "UPSTREAM_PROXY_CONCURRENCY=${UPSTREAM_PROXY_CONCURRENCY:-16}",
    "CONCURRENCY_ACQUIRE_TIMEOUT=${CONCURRENCY_ACQUIRE_TIMEOUT:-30}",
    # multimodal HTTP client timeouts
    "MULTIMODAL_HTTP_CONNECT_TIMEOUT=${MULTIMODAL_HTTP_CONNECT_TIMEOUT:-10}",
    "MULTIMODAL_HTTP_READ_TIMEOUT=${MULTIMODAL_HTTP_READ_TIMEOUT:-600}",
    "MULTIMODAL_HTTP_WRITE_TIMEOUT=${MULTIMODAL_HTTP_WRITE_TIMEOUT:-60}",
    "MULTIMODAL_HTTP_POOL_TIMEOUT=${MULTIMODAL_HTTP_POOL_TIMEOUT:-10}",
    "MULTIMODAL_HTTP_MAX_CONNECTIONS=${MULTIMODAL_HTTP_MAX_CONNECTIONS:-40}",
    "MULTIMODAL_HTTP_MAX_KEEPALIVE=${MULTIMODAL_HTTP_MAX_KEEPALIVE:-20}",
    "MULTIMODAL_HTTP_KEEPALIVE_EXPIRY=${MULTIMODAL_HTTP_KEEPALIVE_EXPIRY:-30}",
    # graph worker HTTP client
    "GRAPH_WORKER_URL=${GRAPH_WORKER_URL:-http://graphrag-worker:8111}",
    "GRAPH_WORKER_CONNECT_TIMEOUT=${GRAPH_WORKER_CONNECT_TIMEOUT:-5}",
    "GRAPH_WORKER_READ_TIMEOUT=${GRAPH_WORKER_READ_TIMEOUT:-30}",
    "GRAPH_WORKER_WRITE_TIMEOUT=${GRAPH_WORKER_WRITE_TIMEOUT:-10}",
    "GRAPH_WORKER_POOL_TIMEOUT=${GRAPH_WORKER_POOL_TIMEOUT:-5}",
    "GRAPH_WORKER_MAX_CONNECTIONS=${GRAPH_WORKER_MAX_CONNECTIONS:-10}",
    "GRAPH_WORKER_MAX_KEEPALIVE=${GRAPH_WORKER_MAX_KEEPALIVE:-5}",
    # tianshu backend
    "TIANSHU_API_BASE=${TIANSHU_API_BASE:-http://tianshu-backend:8000/api/v1}",
    "TIANSHU_CONNECT_TIMEOUT=${TIANSHU_CONNECT_TIMEOUT:-10}",
    "TIANSHU_READ_TIMEOUT=${TIANSHU_READ_TIMEOUT:-60}",
]


class GraphWorkerComposeTests(unittest.TestCase):
    """Assert graphrag-worker service configuration in docker-compose.yml."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = _load_compose()
        cls.svc = cls.compose["services"]["graphrag-worker"]

    def test_volumes(self) -> None:
        volumes: list[str] = self.svc.get("volumes", [])
        for expected in (
            "./saves/data/graphrag-jobs:/app/jobs:rw",
            "./indexing:/app/indexing:rw",
            "./indexing_drill:/app/indexing_drill:rw",
            "./saves/data/graphragfile:/app/saves/data/graphragfile:rw",
            "./saves/data/copypath:/app/saves/data/copypath:rw",
            "./graphrag_api:/app/graphrag_api:rw",
        ):
            with self.subTest(volume=expected):
                self.assertIn(expected, volumes)

    def test_graph_job_db_env_present(self) -> None:
        self.assertIn("GRAPH_JOB_DB=/app/jobs/jobs.db", self.svc.get("environment", []))

    def test_no_host_ports(self) -> None:
        self.assertNotIn("ports", self.svc)

    def test_expose_8111(self) -> None:
        self.assertIn("8111", self.svc.get("expose", []))

    def test_healthcheck_present_and_configured(self) -> None:
        hc = self.svc.get("healthcheck")
        self.assertIsNotNone(hc, "healthcheck section missing")
        joined = " ".join(str(x) for x in hc.get("test", []))
        self.assertIn("urllib.request", joined)
        self.assertIn("http://127.0.0.1:8111/health", joined)
        self.assertEqual(hc.get("interval"), "10s")
        self.assertEqual(hc.get("timeout"), "5s")
        self.assertEqual(hc.get("retries"), 5)
        self.assertEqual(hc.get("start_period"), "20s")

    def test_networks_preserved(self) -> None:
        self.assertIn("app-network", self.svc.get("networks", []))

    def test_restart_preserved(self) -> None:
        self.assertEqual(self.svc.get("restart"), "unless-stopped")


class GraphApiComposeTests(unittest.TestCase):
    """Assert api service carries the graph-import wiring in docker-compose.yml."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = _load_compose()
        cls.svc = cls.compose["services"]["api"]

    def test_api_indexing_volumes_present(self) -> None:
        volumes: list[str] = self.svc.get("volumes", [])
        for expected in (
            "./indexing_drill:/app/indexing_drill:rw",
            "./indexing:/app/indexing:rw",
        ):
            with self.subTest(volume=expected):
                self.assertIn(expected, volumes)

    def test_api_graph_internal_token_present(self) -> None:
        env: list[str] = self.svc.get("environment", [])
        self.assertIn(
            "GRAPH_INTERNAL_TOKEN=${GRAPH_INTERNAL_TOKEN:?Set GRAPH_INTERNAL_TOKEN in .env}",
            env,
        )
        _assert_no_hardcoded_token(self, env)

    def test_api_graph_import_roots_present(self) -> None:
        env: list[str] = self.svc.get("environment", [])
        for entry in (
            "GRAPH_GROUND_IMPORT_ROOT=/app/indexing/ground_graph_fill",
            "GRAPH_DRILL_IMPORT_ROOT=/app/indexing_drill/drill_graph_fill",
        ):
            with self.subTest(entry=entry):
                self.assertIn(entry, env)


class ApiBaseEnvironmentContractTests(unittest.TestCase):
    """Assert base docker-compose.yml API receives all required runtime variables."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.svc = _load_compose()["services"]["api"]
        cls.env_list: list[str] = cls.svc.get("environment", [])

    def test_required_env_entries_present(self) -> None:
        for entry in _BASE_ENV_ENTRIES:
            with self.subTest(entry=entry):
                _assert_env_present(self, self.env_list, entry)


class GraphWorkerEnvironmentExtensionTests(unittest.TestCase):
    """Assert graphrag-worker env carries graph-import variables."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.svc = _load_compose()["services"]["graphrag-worker"]
        cls.env_list: list[str] = cls.svc.get("environment", [])

    def test_worker_graph_import_env(self) -> None:
        for entry in (
            "GRAPH_INTERNAL_TOKEN=${GRAPH_INTERNAL_TOKEN:?Set GRAPH_INTERNAL_TOKEN in .env}",
            "MAIN_API_INTERNAL_URL=http://api:5050/api/data/graph/internal/import",
            "GRAPH_IMPORT_TIMEOUT=${GRAPH_IMPORT_TIMEOUT:-1800}",
        ):
            with self.subTest(entry=entry):
                self.assertIn(entry, self.env_list)

    def test_worker_no_hardcoded_token_value(self) -> None:
        _assert_no_hardcoded_token(self, self.env_list)


class GraphWorkerDockerfileTests(unittest.TestCase):
    """Assert docker/graphrag.Dockerfile pins and startup command."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dockerfile = _load_dockerfile()

    def test_base_image(self) -> None:
        self.assertIn("FROM python:3.12-slim", self.dockerfile)

    def _assert_pinned(self, package: str, version: str) -> None:
        pattern = r"{0}=={1}\b".format(re.escape(package), re.escape(version))
        self.assertRegex(self.dockerfile, pattern, f"{package}=={version} not found")

    def test_pinned_packages(self) -> None:
        for pkg, ver in (
            ("graphrag", "0.1.1"),
            ("fastapi", "0.116.1"),
            ("uvicorn", "0.35.0"),
            ("pandas", "2.2.3"),
            ("pyarrow", "15.0.0"),
            ("httpx", "0.28.1"),
        ):
            with self.subTest(pkg=pkg):
                self._assert_pinned(pkg, ver)

    def test_uvicorn_startup_command(self) -> None:
        self.assertIn("graphrag_api.main:app", self.dockerfile)
        self.assertIn("--port", self.dockerfile)
        self.assertIn("8111", self.dockerfile)


class ProductionComposeTests(unittest.TestCase):
    """Assert production overrides in docker-compose.prod.yml.

    These tests parse the prod file as a standalone override (not merged).
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.prod = _load_prod_compose()
        cls.api = cls.prod["services"]["api"]
        cls.web = cls.prod["services"]["web"]

    def test_api_command_includes_one_worker(self) -> None:
        cmd = self.api.get("command")
        self.assertIsNotNone(cmd, "production API must have an explicit command")
        parts = cmd if isinstance(cmd, list) else str(cmd).split()
        self.assertIn("--workers", parts)
        self.assertEqual(parts[parts.index("--workers") + 1], "1")

    def test_api_override_volumes_no_source_mounts(self) -> None:
        source_mounts = {"./server", "./src", "./test", "./web"}
        for vol in self.api.get("volumes", []):
            host_part = vol.split(":")[0]
            self.assertNotIn(host_part, source_mounts, f"source mount in prod: {vol}")

    def test_web_volumes_reset_empty(self) -> None:
        self.assertEqual(self.web.get("volumes", []), [], "web volumes must be empty")

    def test_web_publishes_only_target_80(self) -> None:
        ports = self.web.get("ports", [])
        self.assertEqual(len(ports), 1, "web must publish exactly one port")
        entry = ports[0]
        if isinstance(entry, dict):
            self.assertEqual(entry.get("target"), 80)
        else:
            self.assertIn(":80", str(entry))

    def test_api_receives_required_runtime_mappings(self) -> None:
        env_list: list[str] = self.api.get("environment", [])
        required_prefixes = [
            "JWT_SECRET_KEY=", "BLOCKING_WORKERS=", "CHAT_CONCURRENCY=",
            "RETRIEVAL_CONCURRENCY=", "GRAPH_IMPORT_CONCURRENCY=",
            "UPSTREAM_PROXY_CONCURRENCY=", "CONCURRENCY_ACQUIRE_TIMEOUT=",
            "MULTIMODAL_HTTP_CONNECT_TIMEOUT=", "MULTIMODAL_HTTP_READ_TIMEOUT=",
            "MULTIMODAL_HTTP_WRITE_TIMEOUT=", "MULTIMODAL_HTTP_POOL_TIMEOUT=",
            "MULTIMODAL_HTTP_MAX_CONNECTIONS=", "MULTIMODAL_HTTP_MAX_KEEPALIVE=",
            "MULTIMODAL_HTTP_KEEPALIVE_EXPIRY=", "GRAPH_WORKER_URL=",
            "GRAPH_WORKER_CONNECT_TIMEOUT=", "GRAPH_WORKER_READ_TIMEOUT=",
            "GRAPH_WORKER_WRITE_TIMEOUT=", "GRAPH_WORKER_POOL_TIMEOUT=",
            "GRAPH_WORKER_MAX_CONNECTIONS=", "GRAPH_WORKER_MAX_KEEPALIVE=",
            "TIANSHU_API_BASE=", "TIANSHU_CONNECT_TIMEOUT=", "TIANSHU_READ_TIMEOUT=",
        ]
        for prefix in required_prefixes:
            with self.subTest(prefix=prefix):
                found = any(e.startswith(prefix) for e in env_list)
                self.assertTrue(found, f"production API env missing {prefix!r}")


if __name__ == "__main__":
    unittest.main()
