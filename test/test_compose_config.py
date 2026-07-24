"""Regression tests for docker-compose.yml configuration."""
import pathlib
import unittest

DOCKERFILE_PATH = pathlib.Path(__file__).resolve().parent.parent / "docker" / "api.Dockerfile"

try:
    import yaml
except ImportError:
    yaml = None

COMPOSE_PATH = pathlib.Path(__file__).resolve().parent.parent / "docker-compose.yml"


@unittest.skipIf(yaml is None, "PyYAML not installed")
class ComposeConfigTests(unittest.TestCase):
    """Validate docker-compose.yml service configurations."""

    @classmethod
    def setUpClass(cls):
        with open(COMPOSE_PATH, encoding="utf-8") as f:
            cls.compose = yaml.safe_load(f)
        cls.services = cls.compose.get("services", {})

    def _get_env_list(self, service_name):
        """Return environment as a list of 'KEY=value' strings."""
        svc = self.services.get(service_name, {})
        env = svc.get("environment", [])
        if isinstance(env, dict):
            return [f"{k}={v}" for k, v in env.items()]
        return list(env)

    def _get_env_keys(self, service_name):
        """Return set of environment variable names for a service."""
        env_list = self._get_env_list(service_name)
        keys = set()
        for entry in env_list:
            if "=" in str(entry):
                keys.add(str(entry).split("=", 1)[0])
            else:
                keys.add(str(entry))
        return keys

    # ---- Milvus must receive MinIO credentials ----

    def test_milvus_has_minio_access_key(self):
        """Milvus needs MINIO_ACCESS_KEY_ID to authenticate with MinIO."""
        env_keys = self._get_env_keys("milvus")
        self.assertIn("MINIO_ACCESS_KEY_ID", env_keys,
                       "Milvus service must have MINIO_ACCESS_KEY_ID environment variable")

    def test_milvus_has_minio_secret_key(self):
        """Milvus needs MINIO_SECRET_ACCESS_KEY to authenticate with MinIO."""
        env_keys = self._get_env_keys("milvus")
        self.assertIn("MINIO_SECRET_ACCESS_KEY", env_keys,
                       "Milvus service must have MINIO_SECRET_ACCESS_KEY environment variable")

    def test_milvus_minio_env_matches_minio_service(self):
        """Milvus MinIO env vars must reference the same .env vars as the MinIO service."""
        milvus_env = self._get_env_list("milvus")
        milvus_env_str = "\n".join(str(e) for e in milvus_env)
        # Both should pull from the same MINIO_ACCESS_KEY / MINIO_SECRET_KEY .env vars
        self.assertIn("MINIO_ACCESS_KEY", milvus_env_str,
                       "Milvus MINIO_ACCESS_KEY_ID must reference ${MINIO_ACCESS_KEY}")
        self.assertIn("MINIO_SECRET_KEY", milvus_env_str,
                       "Milvus MINIO_SECRET_ACCESS_KEY must reference ${MINIO_SECRET_KEY}")

    # ---- MinIO service has credentials ----

    def test_minio_has_access_key(self):
        """MinIO service must have MINIO_ACCESS_KEY."""
        env_keys = self._get_env_keys("minio")
        self.assertIn("MINIO_ACCESS_KEY", env_keys)

    def test_minio_has_secret_key(self):
        """MinIO service must have MINIO_SECRET_KEY."""
        env_keys = self._get_env_keys("minio")
        self.assertIn("MINIO_SECRET_KEY", env_keys)

    # ---- api Dockerfile build env ----

    def test_api_dockerfile_sets_uv_http_timeout_before_uv_sync(self):
        """api.Dockerfile must set UV_HTTP_TIMEOUT before uv sync to avoid
        build-time download failures when uv downloads large locked wheels."""
        content = DOCKERFILE_PATH.read_text(encoding="utf-8")
        self.assertIn("UV_HTTP_TIMEOUT=600", content,
                       "api.Dockerfile must set UV_HTTP_TIMEOUT=600 to prevent "
                       "uv sync timeouts during image build")
        timeout_pos = content.index("UV_HTTP_TIMEOUT=600")
        sync_pos = content.index("uv sync")
        self.assertLess(timeout_pos, sync_pos,
                        "UV_HTTP_TIMEOUT=600 must be declared before 'uv sync' "
                        "so the timeout applies to uv downloads")

    # ---- Milvus depends on etcd and minio ----

    def test_milvus_depends_on_etcd_and_minio(self):
        """Milvus must depend on both etcd and minio."""
        milvus = self.services.get("milvus", {})
        deps = milvus.get("depends_on", [])
        self.assertIn("etcd", deps, "Milvus must depend on etcd")
        self.assertIn("minio", deps, "Milvus must depend on minio")

    # ---- Tianshu backend healthcheck ----

    def test_tianshu_backend_healthcheck_uses_api_v1_health(self):
        """Backend healthcheck must probe /api/v1/health, not the image default /health."""
        backend = self.services.get("tianshu-backend", {})
        hc = backend.get("healthcheck", {})
        test_cmd = hc.get("test", [])
        test_str = " ".join(str(part) for part in test_cmd)
        self.assertIn("/api/v1/health", test_str,
                       "tianshu-backend healthcheck must probe /api/v1/health")

    def test_tianshu_backend_healthcheck_port_8000(self):
        """Backend healthcheck must probe port 8000."""
        backend = self.services.get("tianshu-backend", {})
        hc = backend.get("healthcheck", {})
        test_cmd = hc.get("test", [])
        test_str = " ".join(str(part) for part in test_cmd)
        self.assertIn("localhost:8000", test_str,
                       "tianshu-backend healthcheck must probe port 8000")

    # ---- Tianshu worker port wiring ----

    def test_tianshu_worker_command_passes_port_8001(self):
        """Worker command must pass --port 8001 to override the default 9000."""
        worker = self.services.get("tianshu-worker", {})
        cmd = worker.get("command", [])
        cmd_str = " ".join(str(part) for part in cmd) if isinstance(cmd, list) else str(cmd)
        self.assertIn("--port", cmd_str,
                       "tianshu-worker command must include --port flag")
        self.assertIn("8001", cmd_str,
                       "tianshu-worker command must pass port 8001")

    def test_tianshu_worker_published_port_is_8001(self):
        """Worker published port must be 8001."""
        worker = self.services.get("tianshu-worker", {})
        ports = worker.get("ports", [])
        port_strs = [str(p) for p in ports]
        self.assertTrue(any("8001" in p for p in port_strs),
                        "tianshu-worker must publish port 8001")

    def test_tianshu_worker_healthcheck_probes_health(self):
        """Worker healthcheck must probe /health on port 8001."""
        worker = self.services.get("tianshu-worker", {})
        hc = worker.get("healthcheck", {})
        test_cmd = hc.get("test", [])
        test_str = " ".join(str(part) for part in test_cmd)
        self.assertIn("/health", test_str,
                       "tianshu-worker healthcheck must probe /health, not /predict")
        self.assertIn("localhost:8001", test_str,
                       "tianshu-worker healthcheck must probe port 8001")

    def test_tianshu_backend_worker_url_points_to_port_8001(self):
        """Backend WORKER_URL must reference the worker on port 8001."""
        env_list = self._get_env_list("tianshu-backend")
        env_str = "\n".join(str(e) for e in env_list)
        self.assertIn("WORKER_URL", env_str,
                       "tianshu-backend must have WORKER_URL env var")
        self.assertIn("tianshu-worker:8001", env_str,
                       "WORKER_URL must point to tianshu-worker:8001")

    def test_tianshu_backend_depends_on_worker_healthy(self):
        """Backend must wait for worker to be healthy before starting."""
        backend = self.services.get("tianshu-backend", {})
        deps = backend.get("depends_on", {})
        worker_dep = deps.get("tianshu-worker", {})
        condition = worker_dep.get("condition", "")
        self.assertEqual(condition, "service_healthy",
                         "tianshu-backend must depend on tianshu-worker with condition service_healthy")

    # ---- No hard-coded secrets ----

    def test_no_hardcoded_api_key_defaults(self):
        """Compose must not contain hard-coded API key defaults (e.g. sk-...)."""
        import re
        raw = COMPOSE_PATH.read_text(encoding="utf-8")
        # Match patterns like ${VAR:-sk-...} that embed a real key as a default
        pattern = re.compile(r'\$\{[^}]+:-sk-[A-Za-z0-9]+}')
        matches = pattern.findall(raw)
        self.assertEqual(matches, [],
                         f"Found hard-coded API key defaults in Compose: {matches}. "
                         "Use ${{VAR?Set VAR in .env}} or ${{VAR:-}} instead.")

    # ---- No fixed host proxy URLs ----

    def test_no_fixed_host_proxy_urls(self):
        """Compose must not contain hard-coded host proxy URLs."""
        raw = COMPOSE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("host.docker.internal:7890", raw,
                         "Committed Compose must not contain hard-coded host proxy URLs "
                         "(host.docker.internal:7890). Use environment-driven ${VAR:-} instead.")


if __name__ == "__main__":
    unittest.main()
