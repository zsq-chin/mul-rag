"""阶段 I：Compose 多模态模式 + SQLite 命名卷持久化验收测试。

覆盖 CLAUDE_PRODUCTION_RELEASE_MODIFICATION_REQUIREMENTS.md §10：
- I1.1 显式 MULTIMODAL_ENABLED / MULTIMODAL_MODE=remote|local；
- I1.2 rag-backend 与 olmocr 只随 local-multimodal profile 启动；
- I1.3 生产 MULTIMODAL_KB_API_BASE 必须显式配置，Compose 无硬编码私网 IP 静默默认值；
- I2.1 基础与生产 Compose 使用同一命名卷 sage_db:/app/db 持久化 SQLite，
      生产 !override 不得丢掉该卷，SAGE_DB_PATH 两处一致；
- I2.3 真实 docker compose 重建容器/升级镜像 后命名卷内数据仍存在
      （一次性命名卷复刻，不触碰现有 dev 栈）。

渲染用 `docker compose --env-file <受控临时文件> config` 隔离仓库 .env 的干扰；
依赖 docker compose；不可用时整体 skip（环境缺失，不伪装通过）。
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 除 MULTIMODAL_KB_API_BASE / MULTIMODAL_ENABLED 之外的必需变量，
# 用于让 `docker compose config` 走到目标断言而不被更早的 :? 卡住。
_BASE_REQUIRED_ENV = {
    "NEO4J_USERNAME": "neo4j",
    "NEO4J_PASSWORD": "p",
    "MYSQL_PASSWORD": "p",
    "MYSQL_ROOT_PASSWORD": "p",
    "MINIO_ACCESS_KEY": "a",
    "MINIO_SECRET_KEY": "s",
    "MODEL_CREDENTIAL_MASTER_KEY": "k",
    "GRAPH_INTERNAL_TOKEN": "t",
    "JWT_SECRET_KEY": "j",
}


def _clean_env(extra=None):
    """构造干净子进程环境：剔除会干扰断言的 MULTIMODAL_*/SAGE_DB_PATH 变量。"""
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("MULTIMODAL_") or key == "SAGE_DB_PATH":
            del env[key]
    env.update(extra or {})
    return env


def _compose_config(*files, env=None, profiles=()):
    """用受控 env-file 渲染 docker compose config；返回 CompletedProcess。

    --env-file 替换项目 .env，避免仓库本地 .env 注入私网 IP 干扰断言。
    env-file 只写受控变量（宿主环境含 (X86) 等非法 .env 键名，不能整体写入）；
    子进程环境剥离 MULTIMODAL_*/SAGE_DB_PATH，避免宿主环境覆盖 env-file。
    """
    clean = _clean_env()
    fd, envfile = tempfile.mkstemp(suffix=".env")
    try:
        with os.fdopen(fd, "w", newline="\n", encoding="utf-8") as f:
            for key, value in (env or {}).items():
                f.write(f"{key}={value}\n")
        cmd = ["docker", "compose", "--env-file", envfile, "--ansi", "never"]
        for p in profiles:
            cmd += ["--profile", p]
        for f in files:
            cmd += ["-f", str(f)]
        cmd += ["config"]
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=120,
            env=clean,
        )
    finally:
        os.unlink(envfile)


def _docker_available():
    if shutil.which("docker") is None:
        return False
    r = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True, text=True, timeout=30,
    )
    return r.returncode == 0 and bool(r.stdout.strip())


def _image_available(image):
    r = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True, text=True, timeout=60,
    )
    return r.returncode == 0


class ComposeConfigMultimodalTests(unittest.TestCase):
    """静态断言：docker compose config 对真实 compose 文件的渲染结果。"""

    @classmethod
    def setUpClass(cls):
        if not _docker_available():
            raise unittest.SkipTest("docker 不可用，跳过 Compose 配置断言")
        r = _compose_config(ROOT / "docker-compose.yml", env=_BASE_REQUIRED_ENV)
        cls.base_rc, cls.base_out, cls.base_err = r.returncode, r.stdout, r.stderr
        rp = _compose_config(
            ROOT / "docker-compose.yml", ROOT / "docker-compose.prod.yml",
            env={**_BASE_REQUIRED_ENV, "MULTIMODAL_ENABLED": "true",
                 "MULTIMODAL_KB_API_BASE": "https://mm.example.com/api/v1"},
        )
        cls.prod_rc, cls.prod_out, cls.prod_err = rp.returncode, rp.stdout, rp.stderr

    def test_base_config_renders_with_required_vars(self):
        self.assertEqual(self.base_rc, 0, f"基础 Compose 渲染失败: {self.base_err}")

    def test_base_no_hardcoded_private_ip_default(self):
        """I1.3：Base URL 不再有私网 IP 静默默认值。"""
        self.assertIn("MULTIMODAL_KB_API_BASE: \"\"", self.base_out)
        self.assertNotIn("10.16.33.2", self.base_out)
        self.assertNotIn("localhost:8002", self.base_out)

    def test_base_explicit_enabled_and_mode(self):
        """I1.1：基础 Compose 显式声明 MULTIMODAL_ENABLED 与 MULTIMODAL_MODE。"""
        self.assertIn("MULTIMODAL_ENABLED:", self.base_out)
        self.assertIn("MULTIMODAL_MODE:", self.base_out)

    def test_base_sqlite_named_volume_mounted(self):
        """I2.1：基础 Compose 挂载 sage_db:/app/db，且声明 SAGE_DB_PATH。"""
        self.assertIn("source: sage_db", self.base_out)
        self.assertIn("target: /app/db", self.base_out)
        self.assertIn("SAGE_DB_PATH: /app/db/server.db", self.base_out)

    def test_rag_backend_and_olmocr_profiled_local(self):
        """I1.2：默认配置不含本机 8002/8005 端口映射；启用 profile 后才有。"""
        self.assertNotIn("published: \"8002\"", self.base_out)
        self.assertNotIn("published: \"8005\"", self.base_out)
        profiled = _compose_config(
            ROOT / "docker-compose.yml", env=_BASE_REQUIRED_ENV,
            profiles=("local-multimodal",),
        )
        self.assertEqual(profiled.returncode, 0, profiled.stderr)
        self.assertIn("published: \"8002\"", profiled.stdout)
        self.assertIn("published: \"8005\"", profiled.stdout)

    def test_prod_config_renders(self):
        self.assertEqual(self.prod_rc, 0, f"生产 Compose 渲染失败: {self.prod_err}")

    def test_prod_sqlite_volume_survives_override(self):
        """I2.1 关键：生产 !override 后仍挂载 sage_db:/app/db，SAGE_DB_PATH 一致。"""
        self.assertIn("source: sage_db", self.prod_out)
        self.assertIn("target: /app/db", self.prod_out)
        self.assertIn("SAGE_DB_PATH: /app/db/server.db", self.prod_out)

    def test_prod_mode_remote_and_explicit_enabled(self):
        """I1.1：生产固定 remote 模式，MULTIMODAL_ENABLED 显式注入。"""
        self.assertIn("MULTIMODAL_MODE: remote", self.prod_out)
        self.assertIn("MULTIMODAL_ENABLED: \"true\"", self.prod_out)

    def test_prod_base_url_must_be_explicit(self):
        """I1.3：生产缺 MULTIMODAL_KB_API_BASE 时渲染必须失败。"""
        r = _compose_config(
            ROOT / "docker-compose.yml", ROOT / "docker-compose.prod.yml",
            env={**_BASE_REQUIRED_ENV, "MULTIMODAL_ENABLED": "true"},
        )
        self.assertNotEqual(r.returncode, 0, "缺 MULTIMODAL_KB_API_BASE 时生产配置不应渲染成功")
        self.assertIn("MULTIMODAL_KB_API_BASE", r.stderr)

    def test_prod_api_daemon_no_reload_with_grace_logrotate_limits(self):
        """I3.1/I3.2：生产 API 无 reload；健康检查/停止宽限/日志轮转/资源限制/重启策略齐备。"""
        api = self.prod_out.split("  api:\n", 1)[1].split("\n  web:", 1)[0]
        self.assertNotIn("--reload", api, "生产 API 不得使用 --reload")
        self.assertIn("--workers", api)
        self.assertIn("stop_grace_period: 30s", api)
        self.assertIn("max-size: 10m", api)
        self.assertIn('max-file: "3"', api)
        self.assertIn("restart: always", api)
        self.assertIn('memory: "42949672960"', api)  # 40g 资源上限
        self.assertIn("healthcheck:", api)

    def test_prod_web_production_build_no_dev_server(self):
        """I3.1：Web 使用生产构建，不跑 vite dev server。"""
        self.assertIn("target: production", self.prod_out)
        self.assertIn("NODE_ENV: production", self.prod_out)
        self.assertNotIn("pnpm run server", self.prod_out)


class RemoteDaemonTemplateTests(unittest.TestCase):
    """I3.4/I3.6：远端多模态后端守护模板（systemd + 启动脚本 + README）。"""

    def setUp(self):
        deploy = ROOT / "deploy" / "remote-multimodal"
        self.unit = (deploy / "multimodal-rag.service").read_text(encoding="utf-8")
        self.launcher = (deploy / "start_remote_multimodal.sh").read_text(encoding="utf-8")
        self.readme = (deploy / "README.md").read_text(encoding="utf-8")

    def test_launcher_binds_8002_without_reload_single_worker(self):
        self.assertIn("--workers 1", self.launcher)
        self.assertIn('BIND="${MULTIMODAL_BIND:-0.0.0.0}"', self.launcher)
        self.assertIn('PORT="${MULTIMODAL_PORT:-8002}"', self.launcher)
        exec_line = next(
            ln for ln in self.launcher.splitlines() if ln.strip().startswith("exec uvicorn")
        )
        self.assertNotIn("--reload", exec_line)

    def test_launcher_activates_conda_env_and_execs(self):
        self.assertIn("conda activate", self.launcher)
        self.assertIn("exec uvicorn", self.launcher)

    def test_systemd_unit_daemon_guarantees(self):
        self.assertIn("WorkingDirectory=", self.unit)
        self.assertIn("Restart=always", self.unit)
        self.assertIn("TimeoutStopSec=60", self.unit)
        self.assertIn("EnvironmentFile=", self.unit)
        self.assertIn("knowledge_base", self.unit)
        self.assertIn("RestartSec=5", self.unit)

    def test_no_real_secrets_in_templates(self):
        # 模板只允许占位符/文档说明；绝不出现真实私网 IP 或已填写的秘密值
        for text in (self.unit, self.launcher, self.readme):
            self.assertNotIn("10.16.33.2", text)
        # 启动脚本/单元不得出现已填写的 Token/密码；变量名说明（占位符）允许出现在 README
        self.assertNotIn("password=", self.launcher.lower())
        self.assertNotIn("MULTIMODAL_SERVICE_TOKEN=Bearer", self.unit + self.launcher + self.readme)
        self.assertNotIn("sk-", self.unit + self.launcher + self.readme)

    def test_readme_covers_persistence_backup_recovery(self):
        self.assertIn("knowledge_base", self.readme)
        self.assertIn("恢复演练", self.readme)
        self.assertIn("备份", self.readme)
        self.assertIn("healthy/degraded/down", self.readme)


class DockerVolumePersistenceTest(unittest.TestCase):
    """I2.3 实测：重建容器 + 升级镜像后命名卷内 SQLite 数据仍在。

    一次性命名卷 + 本地缓存镜像（python:3.11-slim → python:3.12 模拟升级），
    完全不触碰现有 dev 栈与真实 sage-master_sage_db 卷。
    """

    _marker = "alice-persists"

    def test_named_volume_survives_down_up_and_image_upgrade(self):
        if not _docker_available():
            self.skipTest("docker 不可用，跳过持久化实测")
        if not _image_available("python:3.11-slim") or not _image_available("python:3.12"):
            self.skipTest("本地缺少 python 镜像，跳过持久化实测（离线）")

        suffix = os.urandom(4).hex()
        volume = f"sage-master_verify_persist_{suffix}"
        with tempfile.TemporaryDirectory(prefix="sage_persist_") as tmp:
            phase1 = Path(tmp) / "phase1.yml"
            phase2 = Path(tmp) / "phase2.yml"

            phase1.write_text(
                f"""services:
  probe:
    image: python:3.11-slim
    volumes:
      - {volume}:/app/db
    command: python -c "import sqlite3;c=sqlite3.connect('/app/db/server.db');c.execute('CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT)');c.execute('INSERT INTO users(name) VALUES (?)',('{self._marker}',));c.commit();c.close();print('WRITER_OK')"
volumes:
  {volume}:
    name: {volume}
""",
                encoding="utf-8",
            )
            phase2.write_text(
                f"""services:
  probe:
    image: python:3.12
    volumes:
      - {volume}:/app/db
    command: python -c "import sqlite3,sys;c=sqlite3.connect('/app/db/server.db');n=c.execute('SELECT COUNT(*) FROM users WHERE name=?',('{self._marker}',)).fetchone()[0];c.close();print('rows=',n);sys.exit(0 if n>=1 else 1)"
volumes:
  {volume}:
    name: {volume}
""",
                encoding="utf-8",
            )

            def dc(file, *args):
                return subprocess.run(
                    ["docker", "compose", "--ansi", "never", "-f", str(file), *args],
                    capture_output=True, text=True, encoding="utf-8", timeout=300,
                )

            try:
                # 1) 写入：python:3.11-slim 在命名卷里创建带标记的 SQLite
                r1 = dc(phase1, "run", "--rm", "probe")
                self.assertEqual(r1.returncode, 0, f"phase1 写库失败: {r1.stderr}")
                self.assertIn("WRITER_OK", r1.stdout)
                # 2) down：容器与网络移除，卷保留
                r2 = dc(phase1, "down")
                self.assertEqual(r2.returncode, 0, f"phase1 down 失败: {r2.stderr}")
                # 3) 升级镜像（3.11-slim → 3.12）并以新容器校验数据仍在
                r3 = dc(phase2, "run", "--rm", "probe")
                self.assertEqual(
                    r3.returncode, 0,
                    f"重建/升级后数据丢失或校验失败: stdout={r3.stdout} stderr={r3.stderr}",
                )
                self.assertIn("rows= 1", r3.stdout)
            finally:
                dc(phase1, "down", "-v")  # 清理容器、网络与一次性卷


if __name__ == "__main__":
    unittest.main()
