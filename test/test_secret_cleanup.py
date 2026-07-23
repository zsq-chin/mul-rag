"""Tests for Task 13 secret cleanup.

Verifies:
  1. Hardcoded credential literals are absent from production source.
  2. DBManagerCollege builds MySQL URL from environment variables.
  3. docker-compose.yml passes MYSQL_* env vars to the API container.
"""

import importlib
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from sqlalchemy.engine import URL

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Sentinel values that must NOT appear as hardcoded defaults in production code.
FORBIDDEN_SECRETS = ["CUPer123456", "0123456789", "defaultkey", "app_password", "minioadmin"]


# =========================================================================
# 1. Source-level: no hardcoded secrets in production Python
# =========================================================================


class TestNoHardcodedSecretsInSource(unittest.TestCase):
    """Scan production Python files for forbidden credential literals."""

    _SCAN_FILES = [
        "server/db_manager_college.py",
        "src/core/graphbase.py",
    ]

    def test_forbidden_literals_absent(self):
        for rel_path in self._SCAN_FILES:
            source = (_PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
            for secret in FORBIDDEN_SECRETS:
                self.assertNotIn(
                    secret,
                    source,
                    f"{rel_path} still contains forbidden literal '{secret}'",
                )


# =========================================================================
# 2. DBManagerCollege: URL built from env vars, no secret fallback
# =========================================================================


class TestDBManagerCollegeSecretCleanup(unittest.TestCase):
    """Verify that DBManagerCollege reads credentials from the environment."""

    _MODULE_PATH = _PROJECT_ROOT / "server" / "db_manager_college.py"

    def _load_module(self, env_overrides: dict):
        """Reload db_manager_college with a patched environment.

        Returns (module, mock_engine, mock_url_create).
        """
        clean_env = {k: v for k, v in os.environ.items()
                     if not k.startswith("MYSQL_")}
        clean_env.update(env_overrides)

        # Stub heavy transitive imports that are not needed for this test.
        stub_logger = MagicMock()
        stub_config = types.ModuleType("src.config")
        stub_config.Config = type("Config", (), {})

        stub_src = types.ModuleType("src")
        stub_src.__path__ = [str(_PROJECT_ROOT / "src")]
        stub_src.config = MagicMock()

        stub_src_utils = types.ModuleType("src.utils")
        stub_src_utils.__path__ = []
        stub_src_utils.logger = stub_logger

        stub_log_cfg = types.ModuleType("src.utils.logging_config")
        stub_log_cfg.logger = stub_logger

        stub_server = types.ModuleType("server")
        stub_server.__path__ = []
        stub_models = types.ModuleType("server.models_college")
        stub_models.Base = MagicMock()
        stub_user = types.ModuleType("server.models.user_model")
        stub_user.User = MagicMock()

        stub_map = {
            "src": stub_src,
            "src.config": stub_config,
            "src.utils": stub_src_utils,
            "src.utils.logging_config": stub_log_cfg,
            "server": stub_server,
            "server.models_college": stub_models,
            "server.models.user_model": stub_user,
        }
        saved = {}
        for name, mod in stub_map.items():
            if name in sys.modules:
                saved[name] = sys.modules[name]
            sys.modules[name] = mod

        try:
            with patch.dict(os.environ, clean_env, clear=True):
                spec = importlib.util.spec_from_file_location(
                    "server.db_manager_college_test", self._MODULE_PATH
                )
                mod = importlib.util.module_from_spec(spec)
                # Patch create_engine so we don't actually connect to MySQL.
                with patch("sqlalchemy.create_engine") as mock_engine, \
                     patch("sqlalchemy.engine.URL.create", wraps=URL.create) as mock_url_create:
                    mock_engine.return_value = MagicMock()
                    spec.loader.exec_module(mod)
        finally:
            for name in stub_map:
                if name in saved:
                    sys.modules[name] = saved[name]
                else:
                    sys.modules.pop(name, None)
        return mod, mock_engine, mock_url_create

    def test_mysql_password_read_from_env(self):
        """The engine URL must contain the password from MYSQL_PASSWORD."""
        mod, mock_engine, mock_url_create = self._load_module({
            "MYSQL_PASSWORD": "s3cret_test_pw",
            "MYSQL_HOST": "dbhost",
            "MYSQL_PORT": "3307",
            "MYSQL_DATABASE": "mydb",
            "MYSQL_USER": "myuser",
        })
        # Verify create_engine was called with a URL object (not a string).
        call_args = mock_engine.call_args
        url_obj = call_args[0][0]
        self.assertIsInstance(url_obj, URL,
                              "create_engine must receive a URL object, not a string")
        # Assert all fields including the password.
        self.assertEqual(url_obj.password, "s3cret_test_pw")
        self.assertEqual(url_obj.username, "myuser")
        self.assertEqual(url_obj.host, "dbhost")
        self.assertEqual(url_obj.port, 3307)
        self.assertEqual(url_obj.database, "mydb")

    def test_missing_password_raises_config_error(self):
        """Missing MYSQL_PASSWORD must raise a generic RuntimeError with no secret."""
        with self.assertRaises(RuntimeError) as ctx:
            self._load_module({})
        msg = str(ctx.exception)
        for secret in FORBIDDEN_SECRETS:
            self.assertNotIn(secret, msg,
                             f"Exception message must not contain secret '{secret}'")

    def test_no_hardcoded_password_in_source(self):
        """The source must not contain CUPer123456."""
        source = self._MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("CUPer123456", source)

    def test_url_uses_sqlalchemy_url_create(self):
        """The module should use sqlalchemy.engine.URL.create, not string
        interpolation, to build the connection URL."""
        source = self._MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("URL.create", source,
                       "db_manager_college should use sqlalchemy.engine.URL.create")


# =========================================================================
# 3. docker-compose.yml: API receives MYSQL_* env vars
# =========================================================================


class TestDockerComposeMySQL(unittest.TestCase):
    """Verify docker-compose.yml passes MySQL connection vars to the API service."""

    _COMPOSE_PATH = _PROJECT_ROOT / "docker-compose.yml"

    @classmethod
    def setUpClass(cls):
        with cls._COMPOSE_PATH.open(encoding="utf-8") as compose_file:
            compose = yaml.safe_load(compose_file)
        cls._api_env_lines = compose["services"]["api"]["environment"]

    def test_api_mysql_env_vars_present(self):
        """API service must receive MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE,
        MYSQL_USER, MYSQL_PASSWORD."""
        expected_vars = ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE",
                         "MYSQL_USER", "MYSQL_PASSWORD")
        api_env_text = "\n".join(self._api_env_lines)
        for var in expected_vars:
            self.assertIn(
                f"{var}=",
                api_env_text,
                f"docker-compose.yml API service missing {var} in environment",
            )

    def test_mysql_password_required(self):
        """MYSQL_PASSWORD must be required (no default) in compose."""
        api_env_text = "\n".join(self._api_env_lines)
        self.assertIn(
            "${MYSQL_PASSWORD:",
            api_env_text,
            "MYSQL_PASSWORD must use required-variable syntax in docker-compose.yml",
        )


if __name__ == "__main__":
    unittest.main()
