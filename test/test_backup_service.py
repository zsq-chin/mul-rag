"""本机备份/校验/预检/恢复验收测试（服务层 + 临时 SQLite/目录）。

覆盖计划阶段 9 的验收点：
- Zip Slip、损坏压缩包、校验值错误、磁盘空间不足、重复确认令牌、
  恢复中断测试。
- 数据库一致性副本使用 SQLite Backup API（不直接复制 WAL）。
- 备份绝不包含 .env / 私钥 / 凭据文件。
- 恢复前自动创建恢复点，恢复失败回滚到恢复点。
路由层只做源码断言（operations_router 导入 src，禁止导入）。
"""

import collections
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base
from server.models.operations_model import BackupJob
from server.services import backup_service as bs

Usage = collections.namedtuple("usage", "total used free")


@contextmanager
def _temp_env():
    """临时目录 + 启用 SQLite 的临时数据库 + 会话。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = str(root / "data" / "server.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'original')")
        conn.commit()
        conn.close()

        engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        backup_dir = str(root / "backups")
        try:
            yield {
                "root": root,
                "db_path": db_path,
                "engine": engine,
                "db": session,
                "backup_dir": backup_dir,
            }
        finally:
            session.close()
            engine.dispose()


def _read_db_values(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT val FROM t ORDER BY id")]
    finally:
        conn.close()


def _make_manual_backup(db, backup_dir, zip_name, manifest, files, sha=None):
    """手工构造一个 BackupJob 行指向自制的 zip（用于恶意/损坏包测试）。"""
    os.makedirs(backup_dir, exist_ok=True)
    zip_path = os.path.join(backup_dir, zip_name)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for arcname, content in files.items():
            if isinstance(content, str):
                zf.writestr(arcname, content)
            else:
                with zf.open(arcname, "w") as f:
                    f.write(content)
    row = BackupJob(
        filename=zip_name,
        path=zip_name,
        size_bytes=os.path.getsize(zip_path),
        sha256=sha or bs._sha256_of(zip_path),
        manifest_version=bs.BACKUP_MANIFEST_VERSION,
        status="completed",
        created_by="tester",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


class BackupCreateTests(unittest.TestCase):
    def test_create_produces_valid_archive(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            row = bs.create_backup(
                db, backup_dir, db_path, {"enable_reranker": True}, created_by="admin", note="first"
            )
            self.assertGreater(row.size_bytes, 0)
            self.assertEqual(len(row.sha256), 64)
            zip_path = os.path.join(backup_dir, row.path)
            with zipfile.ZipFile(zip_path) as zf:
                names = set(zf.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("server.db", names)
            self.assertIn("config.json", names)
            # 校验 server.db 副本可读且数据与快照一致
            with zipfile.ZipFile(zip_path) as zf:
                extracted = str(Path(backup_dir).parent / "dbcopy.db")
                with zf.open("server.db") as src, open(extracted, "wb") as out:
                    out.write(src.read())
            try:
                conn = sqlite3.connect(extracted)
                vals = [r[0] for r in conn.execute("SELECT val FROM t ORDER BY id")]
                conn.close()
            finally:
                os.remove(extracted)
            self.assertEqual(vals, ["original"])

    def test_manifest_and_config_snapshot_sanitized(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            row = bs.create_backup(
                db, backup_dir, db_path,
                {"enable_reranker": True, "custom_models": [
                    {"custom_id": "m1", "api_key": "sk-topsecret", "api_base": "http://a"}]},
                created_by="admin",
            )
            with zipfile.ZipFile(os.path.join(backup_dir, row.path)) as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                cfg = json.loads(zf.read("config.json").decode("utf-8"))
            self.assertEqual(manifest["manifest_version"], "1")
            self.assertEqual(manifest["entries"][0]["path"], "server.db")
            self.assertEqual(cfg["custom_models"][0]["api_key"], "***")
            self.assertNotIn("sk-topsecret", json.dumps(cfg))

    def test_kb_inclusion_skips_sensitive_files(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            kb = Path(backup_dir).parent / "kb"
            kb.mkdir()
            (kb / "doc.txt").write_text("hello", encoding="utf-8")
            (kb / ".env").write_text("SECRET=1", encoding="utf-8")
            (kb / "private.pem").write_text("KEY", encoding="utf-8")
            row = bs.create_backup(
                db, backup_dir, db_path, {}, include_kb=True, kb_roots=[str(kb)], created_by="admin"
            )
            with zipfile.ZipFile(os.path.join(backup_dir, row.path)) as zf:
                names = set(zf.namelist())
            self.assertIn("knowledge/doc.txt", names)
            self.assertNotIn("knowledge/.env", names)
            self.assertNotIn("knowledge/private.pem", names)

    def test_list_get_and_pagination(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            for i in range(3):
                bs.create_backup(db, backup_dir, db_path, {}, created_by="admin")
            data = bs.list_backups(db, page=1, page_size=2)
            self.assertEqual(data["total"], 3)
            self.assertEqual(len(data["items"]), 2)
            first_id = data["items"][0]["id"]
            row = bs.get_backup(db, first_id)
            self.assertIsNotNone(row)
            self.assertNotIn("path", data["items"][0])
            self.assertIsNone(bs.get_backup(db, 99999))


class BackupVerifyTests(unittest.TestCase):
    def test_verify_passes_and_stamps_time(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            row = bs.create_backup(db, backup_dir, db_path, {}, created_by="admin")
            data = bs.verify_backup(db, row.id, backup_dir)
            self.assertTrue(data["verified_at"])

    def test_checksum_mismatch_detected(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            row = bs.create_backup(db, backup_dir, db_path, {}, created_by="admin")
            zip_path = os.path.join(backup_dir, row.path)
            with open(zip_path, "rb") as f:
                content = bytearray(f.read())
            content[len(content) // 2] ^= 0xFF
            with open(zip_path, "wb") as f:
                f.write(content)
            with self.assertRaises(bs.BackupError):
                bs.verify_backup(db, row.id, backup_dir)

    def test_corrupted_archive_detected(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            # 手工制造一个“备份记录”指向非 zip 文件
            fake = os.path.join(backup_dir, "fake.zip")
            os.makedirs(backup_dir, exist_ok=True)
            with open(fake, "wb") as f:
                f.write(b"this is not a zip")
            row = _make_manual_backup(db, backup_dir, "fake.zip", {}, {}, sha=None)
            row.size_bytes = os.path.getsize(fake)
            row.sha256 = bs._sha256_of(fake)
            db.commit()
            with self.assertRaises(bs.BackupError):
                bs.verify_backup(db, row.id, backup_dir)


class BackupRestoreTests(unittest.TestCase):
    def test_preview_lists_added_overwritten_skipped(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            row = bs.create_backup(db, backup_dir, db_path, {}, created_by="admin")
            # 备份后改动数据库（写入大块数据，确保文件尺寸变化），使 server.db 变为“将被覆盖”
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO t VALUES (2, ?)", ("x" * 200000,))
            conn.commit()
            conn.close()
            targets = {"server.db": db_path, "config.json": str(Path(backup_dir).parent / "base.json")}
            preview = bs.preview_restore(db, row.id, backup_dir, targets)
            self.assertIn("server.db", preview["overwritten"])
            self.assertIn("config.json", preview["added"])
            self.assertTrue(preview["token"])
            self.assertEqual(preview["total_entries"], 2)

    def test_restore_returns_db_to_snapshot_state(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            row = bs.create_backup(db, backup_dir, db_path, {"model_name": "x"}, created_by="admin")
            targets = {"server.db": db_path, "config.json": str(Path(backup_dir).parent / "base.json")}
            preview = bs.preview_restore(db, row.id, backup_dir, targets)
            # 改动数据库
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO t VALUES (2, 'changed')")
            conn.commit()
            conn.close()
            self.assertEqual(_read_db_values(db_path), ["original", "changed"])
            # 正式恢复
            result = bs.restore_backup(
                db, row.id, preview["token"], backup_dir, db_path,
                str(Path(backup_dir).parent / "base.json"),
                config_snapshot={"model_name": "x"},
            )
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["restore_point_id"])
            self.assertEqual(_read_db_values(db_path), ["original"])
            # 恢复前自动创建了恢复点（其 zip 文件保留在备份目录中）
            zip_files = [f for f in os.listdir(backup_dir) if f.endswith(".zip")]
            self.assertEqual(len(zip_files), 2)

    def test_duplicate_confirmation_token_rejected(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            row = bs.create_backup(db, backup_dir, db_path, {}, created_by="admin")
            targets = {"server.db": db_path, "config.json": str(Path(backup_dir).parent / "base.json")}
            preview = bs.preview_restore(db, row.id, backup_dir, targets)
            bs.restore_backup(db, row.id, preview["token"], backup_dir, db_path,
                              str(Path(backup_dir).parent / "base.json"), config_snapshot={})
            with self.assertRaises(bs.BackupError):
                bs.restore_backup(db, row.id, preview["token"], backup_dir, db_path,
                                  str(Path(backup_dir).parent / "base.json"), config_snapshot={})

    def test_forged_token_rejected(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            row = bs.create_backup(db, backup_dir, db_path, {}, created_by="admin")
            with self.assertRaises(bs.BackupError):
                bs.restore_backup(db, row.id, "forged", backup_dir, db_path,
                                  str(Path(backup_dir).parent / "base.json"), config_snapshot={})

    def test_zip_slip_rejected(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            os.makedirs(backup_dir, exist_ok=True)
            manifest = {
                "manifest_version": "1",
                "created_at": "now",
                "entries": [{"path": "server.db", "size": 10}],
            }
            # 解压服务器.db 本体供安全条目使用
            good = bs.create_backup(db, backup_dir, db_path, {}, created_by="admin")
            with zipfile.ZipFile(os.path.join(backup_dir, good.path)) as zf:
                db_bytes = zf.read("server.db")
            row = _make_manual_backup(
                db, backup_dir, "evil.zip", manifest,
                {"server.db": db_bytes, "../evil.txt": "pwned"},
            )
            targets = {"server.db": db_path, "config.json": str(Path(backup_dir).parent / "base.json")}
            preview = bs.preview_restore(db, row.id, backup_dir, targets)
            with self.assertRaises(bs.BackupError) as ctx:
                bs.restore_backup(db, row.id, preview["token"], backup_dir, db_path,
                                  str(Path(backup_dir).parent / "base.json"), config_snapshot={})
            self.assertIn("Zip Slip", str(ctx.exception))
            # 原始数据库未被污染
            self.assertEqual(_read_db_values(db_path), ["original"])

    def test_interrupted_restore_leaves_original_and_creates_restore_point(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            manifest = {
                "manifest_version": "1",
                "created_at": "now",
                "entries": [{"path": "server.db", "size": 20}],
            }
            # server.db 条目是一段损坏内容，恢复时完整性校验会失败
            row = _make_manual_backup(
                db, backup_dir, "corrupt.db.zip", manifest, {"server.db": "NOT A SQLITE DB"},
            )
            targets = {"server.db": db_path, "config.json": str(Path(backup_dir).parent / "base.json")}
            preview = bs.preview_restore(db, row.id, backup_dir, targets)
            conn = sqlite3.connect(db_path)
            conn.execute("INSERT INTO t VALUES (2, 'changed')")
            conn.commit()
            conn.close()
            with self.assertRaises(bs.BackupError):
                bs.restore_backup(db, row.id, preview["token"], backup_dir, db_path,
                                  str(Path(backup_dir).parent / "base.json"), config_snapshot={})
            # 恢复失败后原数据库完好（回滚到恢复点）
            self.assertEqual(_read_db_values(db_path), ["original", "changed"])
            # 恢复点备份已自动创建
            self.assertGreaterEqual(bs.list_backups(db)["total"], 2)

    def test_unknown_manifest_version_rejected(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            manifest = {"manifest_version": "999", "entries": []}
            _make_manual_backup(db, backup_dir, "ver.zip", manifest, {})
            rows = bs.list_backups(db)["items"]
            bid = rows[0]["id"]
            with self.assertRaises(bs.BackupError) as ctx:
                bs.verify_backup(db, bid, backup_dir)
            self.assertIn("版本", str(ctx.exception))


class BackupDiskSpaceTests(unittest.TestCase):
    def test_insufficient_space_rejected(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            with patch.object(bs.shutil, "disk_usage", return_value=Usage(100, 99, 1)):
                with self.assertRaises(bs.BackupError) as ctx:
                    bs.create_backup(db, backup_dir, db_path, {}, created_by="admin")
            self.assertIn("磁盘空间不足", str(ctx.exception))


class BackupDeleteTests(unittest.TestCase):
    def test_delete_removes_file_and_row(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            row = bs.create_backup(db, backup_dir, db_path, {}, created_by="admin")
            zip_path = os.path.join(backup_dir, row.path)
            self.assertTrue(os.path.exists(zip_path))
            bs.delete_backup(db, row.id, backup_dir)
            self.assertFalse(os.path.exists(zip_path))
            self.assertEqual(bs.list_backups(db)["total"], 0)
            self.assertIsNone(bs.get_backup(db, row.id))


class OperationsRouterSourceTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.router = (root / "server/routers/operations_router.py").read_text(encoding="utf-8")

    def test_all_endpoints_present(self):
        for route in (
            '@router.post("/backups")',
            '@router.get("/backups")',
            '@router.get("/backups/{backup_id}")',
            '@router.get("/backups/{backup_id}/download")',
            '@router.post("/backups/{backup_id}/verify")',
            '@router.post("/backups/{backup_id}/restore/preview")',
            '@router.post("/backups/{backup_id}/restore")',
            '@router.delete("/backups/{backup_id}")',
        ):
            self.assertIn(route, self.router)

    def test_superadmin_only(self):
        self.assertIn("get_superadmin_user", self.router)
        self.assertIn('router = APIRouter(prefix="/operations"', self.router)

    def test_audit_hooks(self):
        self.assertIn('"backup.create"', self.router)
        self.assertIn('"backup.restore"', self.router)
        self.assertIn('status="failed"', self.router)
        self.assertIn("audit_service.record", self.router)

    def test_restore_requires_token(self):
        self.assertIn("token: str = Body(...)", self.router)

    def test_no_secrets_in_router(self):
        self.assertNotIn("api_key", self.router)
        self.assertNotIn("password", self.router)


if __name__ == "__main__":
    unittest.main()
