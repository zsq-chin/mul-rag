"""Static configuration tests for graphrag-worker (Task 9B-2B).

Verifies docker-compose.yml and docker/graphrag.Dockerfile contain the
expected mounts, environment, ports, healthcheck, and pinned dependencies
without fragile full-text matching.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
DOCKERFILE_PATH = REPO_ROOT / "docker" / "graphrag.Dockerfile"


def _load_compose() -> dict:
    with open(COMPOSE_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load_dockerfile() -> str:
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


class GraphWorkerComposeTests(unittest.TestCase):
    """Assert graphrag-worker service configuration in docker-compose.yml."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = _load_compose()
        cls.svc = cls.compose["services"]["graphrag-worker"]

    # -- volumes ---------------------------------------------------------------

    def test_jobs_volume_mount_present(self) -> None:
        volumes: list[str] = self.svc.get("volumes", [])
        self.assertIn("./saves/data/graphrag-jobs:/app/jobs:rw", volumes)

    def test_existing_volume_mounts_preserved(self) -> None:
        volumes: list[str] = self.svc.get("volumes", [])
        for expected in (
            "./indexing:/app/indexing:rw",
            "./indexing_drill:/app/indexing_drill:rw",
            "./saves/data/graphragfile:/app/saves/data/graphragfile:rw",
            "./saves/data/copypath:/app/saves/data/copypath:rw",
            "./graphrag_api:/app/graphrag_api:rw",
        ):
            self.assertIn(expected, volumes, "Missing volume: {0}".format(expected))

    # -- environment -----------------------------------------------------------

    def test_graph_job_db_env_present(self) -> None:
        env_list: list[str] = self.svc.get("environment", [])
        self.assertIn("GRAPH_JOB_DB=/app/jobs/jobs.db", env_list)

    # -- ports / expose --------------------------------------------------------

    def test_no_host_ports(self) -> None:
        self.assertNotIn("ports", self.svc)

    def test_expose_8111(self) -> None:
        expose: list[str] = self.svc.get("expose", [])
        self.assertIn("8111", expose)

    # -- healthcheck -----------------------------------------------------------

    def test_healthcheck_present(self) -> None:
        hc = self.svc.get("healthcheck")
        self.assertIsNotNone(hc, "healthcheck section missing")

    def test_healthcheck_uses_python_urllib(self) -> None:
        hc = self.svc["healthcheck"]
        test_cmd = hc.get("test", [])
        # test should be a list; join into one string for substring check
        joined = " ".join(str(x) for x in test_cmd)
        self.assertIn("urllib.request", joined)
        self.assertIn("http://127.0.0.1:8111/health", joined)

    def test_healthcheck_timing(self) -> None:
        hc = self.svc["healthcheck"]
        self.assertEqual(hc.get("interval"), "10s")
        self.assertEqual(hc.get("timeout"), "5s")
        self.assertEqual(hc.get("retries"), 5)
        self.assertEqual(hc.get("start_period"), "20s")

    # -- network / restart -----------------------------------------------------

    def test_networks_preserved(self) -> None:
        nets: list[str] = self.svc.get("networks", [])
        self.assertIn("app-network", nets)

    def test_restart_preserved(self) -> None:
        self.assertEqual(self.svc.get("restart"), "unless-stopped")


class GraphApiComposeTests(unittest.TestCase):
    """Assert api service carries the graph-import wiring in docker-compose.yml."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = _load_compose()
        cls.svc = cls.compose["services"]["api"]

    # -- volumes ---------------------------------------------------------------

    def test_api_indexing_drill_volume_present(self) -> None:
        volumes: list[str] = self.svc.get("volumes", [])
        self.assertIn(
            "./indexing_drill:/app/indexing_drill:rw",
            volumes,
            "api service is missing the indexing_drill volume mount",
        )

    def test_api_indexing_volume_preserved(self) -> None:
        """The original indexing mount must still be present."""
        volumes: list[str] = self.svc.get("volumes", [])
        self.assertIn("./indexing:/app/indexing:rw", volumes)

    # -- environment -----------------------------------------------------------

    def test_api_graph_internal_token_present(self) -> None:
        env_list: list[str] = self.svc.get("environment", [])
        self.assertIn("GRAPH_INTERNAL_TOKEN=${GRAPH_INTERNAL_TOKEN:-}", env_list)

    def test_api_graph_ground_import_root_present(self) -> None:
        env_list: list[str] = self.svc.get("environment", [])
        self.assertIn(
            "GRAPH_GROUND_IMPORT_ROOT=/app/indexing/ground_graph_fill", env_list,
        )

    def test_api_graph_drill_import_root_present(self) -> None:
        env_list: list[str] = self.svc.get("environment", [])
        self.assertIn(
            "GRAPH_DRILL_IMPORT_ROOT=/app/indexing_drill/drill_graph_fill", env_list,
        )

    # -- no hardcoded secret ---------------------------------------------------

    def test_api_no_hardcoded_token_value(self) -> None:
        """GRAPH_INTERNAL_TOKEN must use compose interpolation, not a literal."""
        env_list: list[str] = self.svc.get("environment", [])
        for entry in env_list:
            if not entry.startswith("GRAPH_INTERNAL_TOKEN="):
                continue
            value = entry.split("=", 1)[1]
            # The value must look like a compose variable interpolation
            # (${VAR:-...}), not a bare string secret.
            self.assertRegex(
                value,
                r"^\$\{.+\}$",
                "GRAPH_INTERNAL_TOKEN value must be compose interpolation, "
                "got literal: {!r}".format(value),
            )
            break
        else:
            self.fail("GRAPH_INTERNAL_TOKEN not found in api environment")


class GraphWorkerEnvironmentExtensionTests(unittest.TestCase):
    """Assert graphrag-worker env carries graph-import variables."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = _load_compose()
        cls.svc = cls.compose["services"]["graphrag-worker"]

    def test_worker_graph_internal_token_present(self) -> None:
        env_list: list[str] = self.svc.get("environment", [])
        self.assertIn("GRAPH_INTERNAL_TOKEN=${GRAPH_INTERNAL_TOKEN:-}", env_list)

    def test_worker_main_api_internal_url_present(self) -> None:
        env_list: list[str] = self.svc.get("environment", [])
        self.assertIn(
            "MAIN_API_INTERNAL_URL=http://api:5050/api/data/graph/internal/import",
            env_list,
        )

    def test_worker_graph_import_timeout_present(self) -> None:
        env_list: list[str] = self.svc.get("environment", [])
        self.assertIn("GRAPH_IMPORT_TIMEOUT=${GRAPH_IMPORT_TIMEOUT:-1800}", env_list)

    def test_worker_no_hardcoded_token_value(self) -> None:
        """GRAPH_INTERNAL_TOKEN must use compose interpolation, not a literal."""
        env_list: list[str] = self.svc.get("environment", [])
        for entry in env_list:
            if not entry.startswith("GRAPH_INTERNAL_TOKEN="):
                continue
            value = entry.split("=", 1)[1]
            self.assertRegex(
                value,
                r"^\$\{.+\}$",
                "GRAPH_INTERNAL_TOKEN value must be compose interpolation, "
                "got literal: {!r}".format(value),
            )
            break
        else:
            self.fail("GRAPH_INTERNAL_TOKEN not found in worker environment")


class GraphWorkerDockerfileTests(unittest.TestCase):
    """Assert docker/graphrag.Dockerfile pins and startup command."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dockerfile = _load_dockerfile()

    def test_base_image(self) -> None:
        self.assertIn("FROM python:3.12-slim", self.dockerfile)

    def _assert_pinned(self, package: str, version: str) -> None:
        pattern = r"{0}=={1}\b".format(re.escape(package), re.escape(version))
        self.assertRegex(
            self.dockerfile, pattern,
            "{0}=={1} not found in Dockerfile".format(package, version),
        )

    def test_graphrag_pinned(self) -> None:
        self._assert_pinned("graphrag", "0.1.1")

    def test_fastapi_pinned(self) -> None:
        self._assert_pinned("fastapi", "0.116.1")

    def test_uvicorn_pinned(self) -> None:
        self._assert_pinned("uvicorn", "0.35.0")

    def test_pandas_pinned(self) -> None:
        self._assert_pinned("pandas", "2.2.3")

    def test_pyarrow_pinned(self) -> None:
        self._assert_pinned("pyarrow", "15.0.0")

    def test_httpx_pinned(self) -> None:
        self._assert_pinned("httpx", "0.28.1")

    def test_uvicorn_startup_command(self) -> None:
        self.assertIn("graphrag_api.main:app", self.dockerfile)
        self.assertIn("--port", self.dockerfile)
        self.assertIn("8111", self.dockerfile)


if __name__ == "__main__":
    unittest.main()
