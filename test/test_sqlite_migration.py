"""I2.2/I2.3 SQLite 迁移与持久化验证脚本测试。

在临时目录复刻「saves/data/server.db → 命名卷 /app/db/server.db」迁移链路：
- 迁移前自动备份源库；
- 校验大小 / integrity / 关键表行数；
- 用 sqlite backup API 一致性复制；
- 目标已有数据时拒绝覆盖（除非 --force）；
- verify_sqlite_persistence.py 对迁移结果输出 OK。

只 stdlib，可在任意环境运行。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATE = ROOT / "scripts" / "migrate_sqlite_to_named_volume.py"
VERIFY = ROOT / "scripts" / "verify_sqlite_persistence.py"

KEY_TABLES = (
    "users",
    "chat_records",
    "user_model_credentials",
    "config_change_history",
    "knowledge_governance",
    "knowledge_document_versions",
)


def _make_source_db(db_path, marker="u1"):
    """建一个带关键表与数据的源库。"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE chat_records(id INTEGER PRIMARY KEY, question TEXT);
            CREATE TABLE user_model_credentials(id INTEGER PRIMARY KEY, provider TEXT);
            CREATE TABLE config_change_history(id INTEGER PRIMARY KEY, field TEXT);
            CREATE TABLE knowledge_governance(id INTEGER PRIMARY KEY, db_id TEXT);
            CREATE TABLE knowledge_document_versions(id INTEGER PRIMARY KEY, note TEXT);
            """
        )
        conn.execute("INSERT INTO users(name) VALUES (?)", (marker,))
        conn.execute("INSERT INTO chat_records(question) VALUES ('q')")
        conn.execute("INSERT INTO user_model_credentials(provider) VALUES ('openai')")
        conn.execute("INSERT INTO config_change_history(field) VALUES ('f')")
        conn.execute("INSERT INTO knowledge_governance(db_id) VALUES ('kb')")
        conn.execute("INSERT INTO knowledge_document_versions(note) VALUES ('n')")
        conn.commit()
    finally:
        conn.close()


def _counts(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in KEY_TABLES}
    finally:
        conn.close()


def _run(script, *args):
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        cwd=ROOT,
    )


class SqliteMigrationTests(unittest.TestCase):
    def test_migrate_backups_verifies_and_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "saves" / "data" / "server.db"
            dst = Path(tmp) / "app" / "db" / "server.db"
            _make_source_db(src, marker="persist-me")
            r = _run(MIGRATE, "--src", str(src), "--dst", str(dst))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("[done] 迁移完成", r.stdout)
            self.assertIn("[backup]", r.stdout)
            # 源库保留，备份存在
            self.assertTrue(src.exists())
            backups = list(Path(tmp).rglob("server.db.backup-*"))
            self.assertEqual(len(backups), 1, "必须生成源库备份")
            # 目标校验一致
            self.assertEqual(_counts(dst), _counts(src))
            self.assertEqual(_counts(dst)["users"], 1)

    def test_migrate_refuses_overwrite_existing_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "server_src.db"
            dst = Path(tmp) / "server_dst.db"
            _make_source_db(src, marker="old")
            _make_source_db(dst, marker="already-has-data")
            r = _run(MIGRATE, "--src", str(src), "--dst", str(dst))
            self.assertEqual(r.returncode, 2, "已有数据的目标必须拒绝覆盖")
            self.assertIn("[abort]", r.stdout)
            # 目标数据未被破坏
            self.assertEqual(_counts(dst)["users"], 1)

    def test_migrate_force_overwrites_after_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "server_src.db"
            dst = Path(tmp) / "server_dst.db"
            _make_source_db(src, marker="new-data")
            _make_source_db(dst, marker="old-data")
            r = _run(MIGRATE, "--src", str(src), "--dst", str(dst), "--force")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            conn = sqlite3.connect(dst)
            try:
                marker = conn.execute("SELECT name FROM users LIMIT 1").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(marker, "new-data", "--force 应以源库覆盖目标")

    def test_migrate_skips_when_src_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run(MIGRATE, "--src", str(Path(tmp) / "nope.db"), "--dst", str(Path(tmp) / "x.db"))
            self.assertEqual(r.returncode, 0)
            self.assertIn("[skip]", r.stdout)

    def test_verify_persistence_pass_and_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "server.db"
            _make_source_db(db)
            ok = _run(VERIFY, "--db", str(db))
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertIn("[done]", ok.stdout)
            # 删除一个关键表 → 必须失败
            conn = sqlite3.connect(db)
            try:
                conn.execute("DROP TABLE user_model_credentials")
                conn.commit()
            finally:
                conn.close()
            bad = _run(VERIFY, "--db", str(db))
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("[缺失] 模型凭据", bad.stdout)


if __name__ == "__main__":
    unittest.main()
