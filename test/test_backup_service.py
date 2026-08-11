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
import server.models.kb_models  # noqa: F401  # 注册 knowledge_* 表
from server.models.kb_models import KnowledgeDatabase, KnowledgeFile
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


def _flaky_copyfile(dest_match, error=None, fail_on=1):
    """构造一个只在前 `fail_on` 次命中 `dest_match` 的 copyfile 调用上抛错的替身。

    用于故障注入：恢复中的应用步骤是首个命中调用，而回滚发生在后续（放行），
    从而能验证"应用失败 → 全量回滚"而不会误伤回滚自身。
    """
    real = bs.shutil.copyfile
    state = {"hits": 0}
    err = error or OSError("模拟复制失败")

    def flaky(src, dst, *a, **kw):
        if dest_match(os.path.abspath(dst)):
            state["hits"] += 1
            if state["hits"] == fail_on:
                raise err
        return real(src, dst, *a, **kw)

    return flaky


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

    def test_kb_inclusion_reads_real_sources_and_skips_sensitive(self):
        """P1-4：只打包 KnowledgeFile 登记的真实源文件；未被引用的文件不入包。"""
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            kb = Path(backup_dir).parent / "data"
            # 真实源文件：被 KnowledgeFile 引用
            (kb / "doc.txt").write_text("hello", encoding="utf-8")
            # 磁盘上存在但未被 KnowledgeFile 引用：不得进入备份（P1-4 核心）
            (kb / "orphan.txt").write_text("orphan", encoding="utf-8")
            # 被引用但属于敏感文件：仍必须跳过
            (kb / ".env").write_text("SECRET=1", encoding="utf-8")
            (kb / "private.pem").write_text("KEY", encoding="utf-8")
            db.add(KnowledgeDatabase(db_id="db1", name="kb1"))
            db.commit()
            for fn in ("doc.txt", ".env", "private.pem"):
                db.add(KnowledgeFile(
                    database_id="db1",
                    file_id="f-" + fn,
                    filename=fn,
                    path=str(kb / fn),
                    file_type="txt",
                    status="completed",
                ))
            db.commit()
            row = bs.create_backup(
                db, backup_dir, db_path, {}, include_kb=True, kb_root=str(kb), created_by="admin"
            )
            with zipfile.ZipFile(os.path.join(backup_dir, row.path)) as zf:
                names = set(zf.namelist())
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            self.assertIn("knowledge/doc.txt", names)
            self.assertNotIn("knowledge/orphan.txt", names)
            self.assertNotIn("knowledge/.env", names)
            self.assertNotIn("knowledge/private.pem", names)
            # manifest 记录数量与大小
            self.assertEqual(manifest["kb_sources"]["count"], 1)
            self.assertGreater(manifest["kb_sources"]["size_bytes"], 0)

    def _seed_kb_file(self, db, kb_root, path, filename="doc.txt"):
        db.add(KnowledgeDatabase(db_id="db1", name="kb1"))
        db.commit()
        db.add(KnowledgeFile(
            database_id="db1",
            file_id="f-" + filename,
            filename=filename,
            path=str(path),
            file_type="txt",
            status="completed",
        ))
        db.commit()
        return str(kb_root)

    def test_kb_forged_url_path_rejected(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            kb = Path(backup_dir).parent / "data"
            self._seed_kb_file(db, kb, "http://evil.example/x.txt", filename="evil.txt")
            with self.assertRaises(bs.BackupError) as ctx:
                bs.create_backup(
                    db, backup_dir, db_path, {}, include_kb=True, kb_root=str(kb), created_by="admin"
                )
            self.assertIn("URL", str(ctx.exception))

    def test_kb_path_outside_root_rejected(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            kb = Path(backup_dir).parent / "data"
            outside = Path(backup_dir).parent / "elsewhere.txt"
            outside.write_text("secret", encoding="utf-8")
            self._seed_kb_file(db, kb, outside, filename="escaped.txt")
            with self.assertRaises(bs.BackupError) as ctx:
                bs.create_backup(
                    db, backup_dir, db_path, {}, include_kb=True, kb_root=str(kb), created_by="admin"
                )
            self.assertIn("越界", str(ctx.exception))

    def test_kb_missing_file_skipped(self):
        """陈旧记录（文件已被删除）跳过，不阻断备份。"""
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            kb = Path(backup_dir).parent / "data"
            self._seed_kb_file(db, kb, kb / "gone.txt", filename="gone.txt")
            row = bs.create_backup(
                db, backup_dir, db_path, {}, include_kb=True, kb_root=str(kb), created_by="admin"
            )
            with zipfile.ZipFile(os.path.join(backup_dir, row.path)) as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                names = set(zf.namelist())
            self.assertEqual(manifest["kb_sources"]["count"], 0)
            self.assertNotIn("knowledge/gone.txt", names)

    def test_kb_symlink_rejected(self):
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            kb = Path(backup_dir).parent / "data"
            outside = Path(backup_dir).parent / "outside.txt"
            outside.write_text("x", encoding="utf-8")
            link = kb / "link.txt"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("当前环境不支持创建符号链接")
            self._seed_kb_file(db, kb, link, filename="link.txt")
            with self.assertRaises(bs.BackupError) as ctx:
                bs.create_backup(
                    db, backup_dir, db_path, {}, include_kb=True, kb_root=str(kb), created_by="admin"
                )
            self.assertIn("软链接", str(ctx.exception))

    def test_restore_preview_lists_kb_files(self):
        """P1-4 验收：恢复预检必须显示知识源文件。"""
        with _temp_env() as env:
            db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
            kb = Path(backup_dir).parent / "data"
            (kb / "doc.txt").write_text("hello", encoding="utf-8")
            self._seed_kb_file(db, kb, kb / "doc.txt", filename="doc.txt")
            row = bs.create_backup(
                db, backup_dir, db_path, {}, include_kb=True, kb_root=str(kb), created_by="admin"
            )
            targets = {"server.db": db_path, "config.json": str(Path(backup_dir).parent / "base.json")}
            restore_target = Path(backup_dir).parent / "restore_kb"
            preview = bs.preview_restore(db, row.id, backup_dir, targets, kb_target_root=str(restore_target))
            self.assertIn("knowledge/doc.txt", preview["added"])

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

    def test_restore_point_record_survives_overwrite(self):
        """P1-5 验收：恢复点记录必须穿过数据库覆盖写入恢复后的库文件，
        从而在恢复后（含重启后）可被列表/详情/校验接口读取。"""
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
            # 正式恢复
            result = bs.restore_backup(
                db, row.id, preview["token"], backup_dir, db_path,
                str(Path(backup_dir).parent / "base.json"),
                config_snapshot={"model_name": "x"},
                created_by="admin",
            )
            rpid = result["restore_point_id"]
            self.assertTrue(rpid)

            # 详情接口可读（get_backup 同时支撑列表/详情/恢复）
            got = bs.get_backup(db, rpid)
            self.assertIsNotNone(got)
            self.assertEqual(got.id, rpid)
            self.assertEqual(got.status, "completed")
            self.assertEqual(got.note, "restore point before restore #{}".format(row.id))

            # 列表接口包含恢复点
            ids = [item["id"] for item in bs.list_backups(db)["items"]]
            self.assertIn(rpid, ids)

            # 校验接口可读取并校验恢复点 zip
            data = bs.verify_backup(db, rpid, backup_dir)
            self.assertTrue(data["verified_at"])
            self.assertEqual(data["size_bytes"], got.size_bytes)

            # 模拟重启：全新 sqlite 连接读回数据库文件，恢复点记录仍在库中
            conn = sqlite3.connect(db_path)
            try:
                fetched = conn.execute(
                    "SELECT id, status, note FROM backup_jobs WHERE id=?", (rpid,)
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched[1], "completed")

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


class BackupRestoreRollbackTests(unittest.TestCase):
    """P1-4：恢复任一步失败必须全量回滚数据库、配置、日志和知识文件。

    故障注入点：配置替换、日志复制、知识目录应用到一半。每个故障点之后
    四类目标都与恢复前一致（不出现"旧数据库 + 新配置/部分新文件"的混合状态），
    且异常信息报告失败步骤与回滚是否完整。
    """

    def _prepare(self, env):
        """构造含 db/config/log/kb 四类目标的备份与差异化恢复目标。"""
        db, db_path, backup_dir = env["db"], env["db_path"], env["backup_dir"]
        root = env["root"]

        # 备份源：日志 + 两个知识源文件（登记到 KnowledgeFile）
        log_src = root / "log_src.log"
        log_src.write_text("backup log", encoding="utf-8")
        kb_src = root / "kb_src"
        kb_src.mkdir()
        (kb_src / "a.txt").write_text("source a", encoding="utf-8")
        (kb_src / "b.txt").write_text("source b", encoding="utf-8")
        db.add(KnowledgeDatabase(db_id="db1", name="kb1"))
        db.commit()
        for fn in ("a.txt", "b.txt"):
            db.add(KnowledgeFile(
                database_id="db1", file_id="f-" + fn, filename=fn,
                path=str(kb_src / fn), file_type="txt", status="completed",
            ))
        db.commit()

        row = bs.create_backup(
            db, backup_dir, db_path, {"foo": "from_backup"},
            log_path=str(log_src), include_logs=True,
            include_kb=True, kb_root=str(kb_src), created_by="admin",
        )

        # 备份后把数据库改成另一状态，让恢复/回滚可观测
        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO t VALUES (2, 'changed')")
        conn.commit()
        conn.close()

        # 恢复目标（恢复前的实时内容，均与备份内容不同）
        config_target = root / "live_config.json"
        config_target.write_text('{"live": "original"}', encoding="utf-8")
        log_target = root / "live_app.log"
        log_target.write_text("live original log", encoding="utf-8")
        kb_target = root / "live_kb"
        kb_target.mkdir()
        (kb_target / "a.txt").write_text("target a", encoding="utf-8")
        (kb_target / "keep.txt").write_text("keep original", encoding="utf-8")

        targets = {
            "server.db": db_path,
            "config.json": str(config_target),
            "logs/app.log": str(log_target),
        }
        preview = bs.preview_restore(db, row.id, backup_dir, targets, kb_target_root=str(kb_target))
        return {
            "db": db, "db_path": db_path, "backup_dir": backup_dir,
            "row": row, "preview": preview,
            "config_target": config_target, "log_target": log_target, "kb_target": kb_target,
            "log_src": log_src, "kb_src": kb_src,
        }

    def _restore(self, p):
        return bs.restore_backup(
            p["db"], p["row"].id, p["preview"]["token"], p["backup_dir"], p["db_path"],
            str(p["config_target"]),
            log_target=str(p["log_target"]),
            kb_target_root=str(p["kb_target"]),
            config_snapshot={"live": "original"},
            log_path=str(p["log_src"]), kb_root=str(p["kb_src"]),
            created_by="admin",
        )

    def _assert_pre_restore_state(self, p):
        """四类目标都必须与恢复前一致：数据库、配置、日志、知识目录。"""
        self.assertEqual(_read_db_values(p["db_path"]), ["original", "changed"])
        self.assertEqual(p["config_target"].read_text(encoding="utf-8"), '{"live": "original"}')
        self.assertEqual(p["log_target"].read_text(encoding="utf-8"), "live original log")
        self.assertEqual((p["kb_target"] / "a.txt").read_text(encoding="utf-8"), "target a")
        self.assertEqual((p["kb_target"] / "keep.txt").read_text(encoding="utf-8"), "keep original")
        self.assertFalse((p["kb_target"] / "b.txt").exists())

    def test_config_replace_failure_rolls_back_all_four(self):
        """配置替换失败：数据库/日志/知识目录未被修改，配置也必须保持恢复前内容。"""
        with _temp_env() as env:
            p = self._prepare(env)
            config_abs = os.path.abspath(str(p["config_target"]))
            with patch.object(
                bs.shutil, "copyfile",
                side_effect=_flaky_copyfile(lambda d: d == config_abs),
            ):
                with self.assertRaises(bs.BackupError) as ctx:
                    self._restore(p)
            self.assertIn("配置", str(ctx.exception), "必须报告失败步骤")
            self.assertIn("已回滚至恢复前状态", str(ctx.exception), "必须报告回滚完整")
            self._assert_pre_restore_state(p)

    def test_log_copy_failure_rolls_back_all_four(self):
        """日志复制失败：配置已先被替换，必须一并回滚（不得遗留"旧库+新配置"）。"""
        with _temp_env() as env:
            p = self._prepare(env)
            log_abs = os.path.abspath(str(p["log_target"]))
            with patch.object(
                bs.shutil, "copyfile",
                side_effect=_flaky_copyfile(lambda d: d == log_abs),
            ):
                with self.assertRaises(bs.BackupError) as ctx:
                    self._restore(p)
            self.assertIn("日志", str(ctx.exception))
            self.assertIn("已回滚至恢复前状态", str(ctx.exception))
            self._assert_pre_restore_state(p)

    def test_kb_partial_apply_failure_rolls_back_all_four(self):
        """知识目录应用到一半失败：a.txt 已覆盖、b.txt 复制中断，全部必须回滚。"""
        with _temp_env() as env:
            p = self._prepare(env)
            kb_abs = os.path.abspath(str(p["kb_target"])) + os.sep
            with patch.object(
                bs.shutil, "copyfile",
                side_effect=_flaky_copyfile(lambda d: d.startswith(kb_abs), fail_on=2),
            ):
                with self.assertRaises(bs.BackupError) as ctx:
                    self._restore(p)
            self.assertIn("知识目录", str(ctx.exception))
            self.assertIn("已回滚至恢复前状态", str(ctx.exception))
            self._assert_pre_restore_state(p)


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
