"""知识治理服务验收测试（服务层 + 临时 SQLite，不依赖 Milvus/docker）。

覆盖：sync 补建、列表/详情合并元数据、路径不泄露、PATCH 更新与非法值、
伪造 db_id/file_id、下载权限矩阵、路径穿越/软链接逃逸/URL 拒绝、
usage_count 服务端自增、JSON/XLSX 导出不含 path。
"""

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import server.models.governance_model  # noqa: F401
import server.models.kb_models  # noqa: F401
from server.models import Base
from server.models.governance_model import KnowledgeDocumentVersion, KnowledgeGovernance
from server.models.kb_models import KnowledgeDatabase, KnowledgeFile
from server.services import governance_service
from server.services.governance_service import (
    GovernanceError,
    GovernanceForbidden,
    GovernanceNotFound,
)

_ORIG_DATA_ROOT = governance_service.DATA_ROOT
_ORIG_PROJECT_ROOT = governance_service._PROJECT_ROOT


@contextmanager
def _temp_db():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        # 注意：不开启 foreign_keys=ON。
        # knowledge_nodes.file_id 引用非唯一的 knowledge_files.file_id，
        # 属于既有模型设计，SQLite 开启外键后会报 mismatch。
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            yield engine, session
        finally:
            session.close()
            engine.dispose()


def _set_roots(data_root):
    governance_service.DATA_ROOT = data_root
    governance_service._PROJECT_ROOT = data_root


def _restore_roots():
    governance_service.DATA_ROOT = _ORIG_DATA_ROOT
    governance_service._PROJECT_ROOT = _ORIG_PROJECT_ROOT


def _seed(session, data_root, db_id="kb_test", names=("a.pdf", "b.txt"), status="done"):
    db = KnowledgeDatabase(db_id=db_id, name="测试知识库")
    session.add(db)
    session.flush()
    for name in names:
        p = os.path.join(data_root, name)
        Path(p).write_bytes(("content " + name).encode("utf-8"))
        ext = os.path.splitext(name)[1].lstrip(".")
        session.add(
            KnowledgeFile(
                database_id=db_id,
                file_id="file_" + name.split(".")[0],
                filename=name,
                path=p,
                file_type=ext,
                status=status,
            )
        )
    session.commit()


def _seed_file(session, data_root, db_id, file_id, filename, path, file_type="pdf"):
    db = session.query(KnowledgeDatabase).filter_by(db_id=db_id).first()
    if db is None:
        db = KnowledgeDatabase(db_id=db_id, name=db_id)
        session.add(db)
        session.flush()
    session.add(
        KnowledgeFile(
            database_id=db_id,
            file_id=file_id,
            filename=filename,
            path=path,
            file_type=file_type,
            status="done",
        )
    )
    session.commit()


class _User:
    def __init__(self, role):
        self.id = 1
        self.role = role


class GovernanceServiceTests(unittest.TestCase):
    def test_sync_creates_default_governance_rows(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _seed(session, data_root)
                result = governance_service.sync_governance(session, "kb_test")
                self.assertEqual(result["total"], 2)
                self.assertEqual(result["created"], 2)
                self.assertEqual(result["updated"], 0)
                rows = (
                    session.query(KnowledgeGovernance)
                    .filter_by(db_id="kb_test")
                    .all()
                )
                self.assertEqual(len(rows), 2)
                for r in rows:
                    self.assertEqual(r.confidentiality, "internal")
                    self.assertEqual(r.download_allowed, 1)
                    self.assertIsNotNone(r.source_updated_at)

    def test_sync_idempotent(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _seed(session, data_root)
                governance_service.sync_governance(session, "kb_test")
                result = governance_service.sync_governance(session, "kb_test")
                self.assertEqual(result["created"], 0)

    def test_sync_missing_database_raises_not_found(self):
        with _temp_db() as (engine, session):
            with self.assertRaises(GovernanceNotFound):
                governance_service.sync_governance(session, "kb_missing")

    def test_list_documents_merges_metadata_without_path(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _seed(session, data_root)
                governance_service.sync_governance(session, "kb_test")
                data = governance_service.list_documents(session, "kb_test")
                self.assertEqual(data["total"], 2)
                self.assertEqual(len(data["items"]), 2)
                for it in data["items"]:
                    self.assertNotIn("path", it, "列表响应不得泄露文件路径")
                    self.assertIn("filename", it)
                    self.assertIn("confidentiality", it)
                    self.assertIn("download_allowed", it)
                    self.assertIn("usage_count", it)
                    self.assertIn("node_count", it)

    def test_list_filters_and_pagination(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _seed(session, data_root)
                governance_service.sync_governance(session, "kb_test")
                gov = (
                    session.query(KnowledgeGovernance)
                    .filter_by(db_id="kb_test", file_id="file_a")
                    .first()
                )
                gov.knowledge_type = "报告"
                gov.confidentiality = "restricted"
                session.commit()
                # 按类型筛选
                data = governance_service.list_documents(
                    session, "kb_test", knowledge_type="报告"
                )
                self.assertEqual(data["total"], 1)
                self.assertEqual(data["items"][0]["file_id"], "file_a")
                # 按关键字筛选
                data = governance_service.list_documents(session, "kb_test", keyword="b.txt")
                self.assertEqual(data["total"], 1)
                self.assertEqual(data["items"][0]["filename"], "b.txt")
                # 分页
                data = governance_service.list_documents(
                    session, "kb_test", page=1, page_size=1
                )
                self.assertEqual(len(data["items"]), 1)
                self.assertEqual(data["page"], 1)

    def test_get_document_increments_usage(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _seed(session, data_root)
                doc = governance_service.get_document(session, "kb_test", "file_a")
                self.assertEqual(doc["filename"], "a.pdf")
                self.assertNotIn("path", doc)
                self.assertEqual(doc["usage_count"], 1)
                doc2 = governance_service.get_document(session, "kb_test", "file_a")
                self.assertEqual(doc2["usage_count"], 2)

    def test_patch_updates_governance_fields(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _seed(session, data_root)
                doc = governance_service.update_governance(
                    session,
                    "kb_test",
                    "file_a",
                    {
                        "domain": "石油储运",
                        "knowledge_type": "标准",
                        "confidentiality": "restricted",
                        "tags": ["规范", "安全"],
                        "download_allowed": False,
                        "owner_department": "工程部",
                    },
                )
                self.assertEqual(doc["domain"], "石油储运")
                self.assertEqual(doc["knowledge_type"], "标准")
                self.assertEqual(doc["confidentiality"], "restricted")
                self.assertEqual(doc["tags"], ["规范", "安全"])
                self.assertIs(doc["download_allowed"], False)
                self.assertEqual(doc["owner_department"], "工程部")
                # 持久化
                got = governance_service.get_document(session, "kb_test", "file_a")
                self.assertEqual(got["confidentiality"], "restricted")

    def test_patch_rejects_invalid_values(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _seed(session, data_root)
                with self.assertRaises(GovernanceError):
                    governance_service.update_governance(
                        session, "kb_test", "file_a", {"confidentiality": "top_secret"}
                    )
                with self.assertRaises(GovernanceError):
                    governance_service.update_governance(
                        session, "kb_test", "file_a", {"knowledge_type": "其他类型"}
                    )
                with self.assertRaises(GovernanceError):
                    governance_service.update_governance(
                        session, "kb_test", "file_a", {"tags": "not-a-list"}
                    )
                with self.assertRaises(GovernanceError):
                    governance_service.update_governance(
                        session, "kb_test", "file_a", {"mystery_field": 1}
                    )

    def test_forged_ids_rejected(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _seed(session, data_root)
                with self.assertRaises(GovernanceNotFound):
                    governance_service.get_document(session, "kb_missing", "file_a")
                with self.assertRaises(GovernanceNotFound):
                    governance_service.get_document(session, "kb_test", "file_missing")
                with self.assertRaises(GovernanceError):
                    governance_service.get_document(session, "kb_test", "../etc/passwd")
                with self.assertRaises(GovernanceError):
                    governance_service.get_document(session, "kb_test\\evil", "file_a")

    def test_download_permission_matrix(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    _seed(session, data_root)
                    # 默认 internal + download_allowed=1 → 普通用户可下载
                    info = governance_service.resolve_download(
                        session, "kb_test", "file_a", _User("user")
                    )
                    self.assertEqual(info["filename"], "a.pdf")
                    self.assertGreater(info["size_bytes"], 0)
                    self.assertTrue(os.path.isfile(info["abs_path"]))
                    # restricted → 非 superadmin 拒绝
                    governance_service.update_governance(
                        session, "kb_test", "file_a", {"confidentiality": "restricted"}
                    )
                    with self.assertRaises(GovernanceForbidden):
                        governance_service.resolve_download(
                            session, "kb_test", "file_a", _User("admin")
                        )
                    info = governance_service.resolve_download(
                        session, "kb_test", "file_a", _User("superadmin")
                    )
                    self.assertEqual(info["filename"], "a.pdf")
                    # download_allowed=0 → 所有人（含 superadmin）拒绝
                    governance_service.update_governance(
                        session, "kb_test", "file_b", {"download_allowed": False}
                    )
                    with self.assertRaises(GovernanceForbidden):
                        governance_service.resolve_download(
                            session, "kb_test", "file_b", _User("user")
                        )
                    with self.assertRaises(GovernanceForbidden):
                        governance_service.resolve_download(
                            session, "kb_test", "file_b", _User("superadmin")
                        )
                finally:
                    _restore_roots()

    def test_download_increments_usage_count(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    _seed(session, data_root)
                    governance_service.resolve_download(
                        session, "kb_test", "file_a", _User("user")
                    )
                    governance_service.resolve_download(
                        session, "kb_test", "file_a", _User("user")
                    )
                    got = governance_service.get_document(
                        session, "kb_test", "file_a", increment_usage=False
                    )
                    self.assertEqual(got["usage_count"], 2)
                finally:
                    _restore_roots()

    def test_path_traversal_rejected(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    evil = os.path.join(data_root, "..", "outside.txt")
                    Path(evil).write_bytes(b"secret")
                    _seed_file(
                        session, data_root, "kb_test", "file_evil", "evil.txt", evil
                    )
                    with self.assertRaises(GovernanceNotFound):
                        governance_service.resolve_download(
                            session, "kb_test", "file_evil", _User("user")
                        )
                finally:
                    _restore_roots()

    def test_absolute_path_outside_root_rejected(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                with tempfile.TemporaryDirectory() as outside:
                    _set_roots(data_root)
                    try:
                        evil = os.path.join(outside, "secret.txt")
                        Path(evil).write_bytes(b"secret")
                        _seed_file(
                            session, data_root, "kb_test", "file_abs", "secret.txt", evil
                        )
                        with self.assertRaises(GovernanceNotFound):
                            governance_service.resolve_download(
                                session, "kb_test", "file_abs", _User("user")
                            )
                    finally:
                        _restore_roots()

    def test_url_path_rejected(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    _seed_file(
                        session,
                        data_root,
                        "kb_test",
                        "file_url",
                        "remote.pdf",
                        "http://10.16.33.2:8002/upload/remote.pdf",
                    )
                    with self.assertRaises(GovernanceNotFound):
                        governance_service.resolve_download(
                            session, "kb_test", "file_url", _User("user")
                        )
                finally:
                    _restore_roots()

    def test_symlink_escape_rejected(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                with tempfile.TemporaryDirectory() as outside:
                    _set_roots(data_root)
                    try:
                        target = os.path.join(outside, "real.txt")
                        Path(target).write_bytes(b"secret")
                        link = os.path.join(data_root, "link.txt")
                        try:
                            os.symlink(target, link)
                        except (OSError, NotImplementedError):
                            self.skipTest("当前环境不允许创建软链接")
                        _seed_file(
                            session, data_root, "kb_test", "file_link", "link.txt", link
                        )
                        with self.assertRaises(GovernanceNotFound):
                            governance_service.resolve_download(
                                session, "kb_test", "file_link", _User("user")
                            )
                    finally:
                        _restore_roots()

    def test_export_json_no_path_no_secrets(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _seed(session, data_root)
                governance_service.update_governance(
                    session,
                    "kb_test",
                    "file_a",
                    {"domain": "安全", "confidentiality": "restricted"},
                )
                data = governance_service.export_json(session, "kb_test")
                self.assertEqual(data["total"], 2)
                for it in data["items"]:
                    self.assertNotIn("path", it)
                joined = "".join(str(it) for it in data["items"])
                self.assertNotIn("data_root", joined.lower())

    def test_export_xlsx_bytes_contains_headers_no_path(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _seed(session, data_root)
                content = governance_service.export_xlsx_bytes(session, "kb_test")
                self.assertTrue(content.startswith(b"PK"), "xlsx 是 zip 容器")
                from io import BytesIO
                from openpyxl import load_workbook
                wb = load_workbook(BytesIO(content))
                ws = wb.active
                headers = [c.value for c in ws[1]]
                self.assertIn("filename", headers)
                self.assertNotIn("path", headers)
                filenames = [row[1].value for row in ws.iter_rows(min_row=2)]
                self.assertIn("a.pdf", filenames)

    def test_safe_id_rejects_traversal(self):
        for bad in ("..", "../x", "a/b", "a\\b", ".", "", None):
            with self.assertRaises(GovernanceError, msg=f"id={bad!r}"):
                governance_service._safe_id(bad)
        self.assertEqual(governance_service._safe_id("kb_abc123"), "kb_abc123")


class GovernanceVersionTests(unittest.TestCase):
    def test_snapshot_creates_version_with_hash_and_size(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    _seed(session, data_root)
                    v = governance_service.create_snapshot(
                        session, "kb_test", "file_a", creator="admin", note="首次快照"
                    )
                    self.assertEqual(v["version"], 1)
                    self.assertEqual(v["note"], "首次快照")
                    self.assertEqual(v["created_by"], "admin")
                    self.assertEqual(v["file_size"], len("content a.pdf"))
                    self.assertEqual(len(v["sha256"]), 64)
                    # 源文件副本位于受控目录
                    p = os.path.join(data_root, "knowledge_versions", "kb_test", "file_a", "1", "file")
                    self.assertTrue(os.path.isfile(p))
                finally:
                    _restore_roots()

    def test_versions_monotonic_no_duplicate(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    _seed(session, data_root)
                    v1 = governance_service.create_snapshot(session, "kb_test", "file_a")
                    # 改变内容后再次快照
                    p = os.path.join(data_root, "a.pdf")
                    Path(p).write_bytes(b"changed content")
                    v2 = governance_service.create_snapshot(session, "kb_test", "file_a")
                    v3 = governance_service.create_snapshot(session, "kb_test", "file_a")
                    self.assertEqual([v1["version"], v2["version"], v3["version"]], [1, 2, 3])
                    # 直接预插 version=1 已提交后再建快照 → 不重号
                    session.add(
                        KnowledgeDocumentVersion(
                            db_id="kb_test", file_id="file_b", version=1,
                            sha256="x", file_size=0, note="pre",
                        )
                    )
                    session.commit()
                    vb = governance_service.create_snapshot(session, "kb_test", "file_b")
                    self.assertEqual(vb["version"], 2)
                finally:
                    _restore_roots()

    def test_same_content_snapshot_dedups_copy(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    _seed(session, data_root)
                    v1 = governance_service.create_snapshot(session, "kb_test", "file_a")
                    v2 = governance_service.create_snapshot(session, "kb_test", "file_a")
                    self.assertTrue(v2["deduplicated"], "相同内容不重复复制")
                    self.assertFalse(v2["sha256"] != v1["sha256"])
                    # 版本 2 目录不应有副本文件
                    v2_dir = os.path.join(data_root, "knowledge_versions", "kb_test", "file_a", "2")
                    self.assertFalse(os.path.exists(os.path.join(v2_dir, "file")))
                    # v1 目录有副本
                    self.assertTrue(
                        os.path.isfile(os.path.join(
                            data_root, "knowledge_versions", "kb_test", "file_a", "1", "file"))
                    )
                finally:
                    _restore_roots()

    def test_list_versions_orders_and_counts(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    _seed(session, data_root)
                    governance_service.create_snapshot(session, "kb_test", "file_a", note="n1")
                    governance_service.create_snapshot(session, "kb_test", "file_a", note="n2")
                    data = governance_service.list_versions(session, "kb_test", "file_a")
                    self.assertEqual(data["total"], 2)
                    self.assertEqual([v["version"] for v in data["items"]], [2, 1])
                finally:
                    _restore_roots()

    def test_version_download_dedup_chain_and_permissions(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    _seed(session, data_root)
                    governance_service.create_snapshot(session, "kb_test", "file_a")
                    v2 = governance_service.create_snapshot(session, "kb_test", "file_a")  # dedup
                    info = governance_service.resolve_version_download(
                        session, "kb_test", "file_a", v2["version"], _User("user")
                    )
                    self.assertEqual(info["filename"], "a.pdf")
                    self.assertGreater(info["size_bytes"], 0)
                    # restricted → 非 superadmin 拒绝
                    governance_service.update_governance(
                        session, "kb_test", "file_a", {"confidentiality": "restricted"}
                    )
                    with self.assertRaises(GovernanceForbidden):
                        governance_service.resolve_version_download(
                            session, "kb_test", "file_a", v2["version"], _User("admin")
                        )
                    info = governance_service.resolve_version_download(
                        session, "kb_test", "file_a", v2["version"], _User("superadmin")
                    )
                    self.assertEqual(info["version"], v2["version"])
                    # download_allowed=0 → 一律拒绝
                    governance_service.update_governance(
                        session, "kb_test", "file_a",
                        {"confidentiality": "internal", "download_allowed": False},
                    )
                    with self.assertRaises(GovernanceForbidden):
                        governance_service.resolve_version_download(
                            session, "kb_test", "file_a", v2["version"], _User("superadmin")
                        )
                finally:
                    _restore_roots()

    def test_version_not_found_cases(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    _seed(session, data_root)
                    with self.assertRaises(GovernanceNotFound):
                        governance_service.list_versions(session, "kb_test", "file_missing")
                    governance_service.create_snapshot(session, "kb_test", "file_a")
                    with self.assertRaises(GovernanceNotFound):
                        governance_service.resolve_version_download(
                            session, "kb_test", "file_a", 99, _User("superadmin")
                        )
                finally:
                    _restore_roots()

    def test_snapshot_rejects_path_traversal_source(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    evil = os.path.join(data_root, "..", "outside.txt")
                    Path(evil).write_bytes(b"secret")
                    _seed_file(session, data_root, "kb_test", "file_evil", "evil.txt", evil)
                    with self.assertRaises(GovernanceNotFound):
                        governance_service.create_snapshot(session, "kb_test", "file_evil")
                finally:
                    _restore_roots()

    def test_snapshot_metadata_has_no_path(self):
        with _temp_db() as (engine, session):
            with tempfile.TemporaryDirectory() as data_root:
                _set_roots(data_root)
                try:
                    _seed(session, data_root)
                    v = governance_service.create_snapshot(session, "kb_test", "file_a")
                    self.assertNotIn("path", v)
                    self.assertNotIn("path", v["sha256"])
                    row = (
                        session.query(KnowledgeDocumentVersion)
                        .filter_by(db_id="kb_test", file_id="file_a", version=v["version"])
                        .first()
                    )
                    self.assertNotIn("path", row.metadata_snapshot)
                finally:
                    _restore_roots()


class GovernanceRouterSourceTests(unittest.TestCase):
    """router 源码级验证（避免引入 src/Milvus）。"""

    def setUp(self):
        self.src = (
            Path(__file__).resolve().parents[1]
            / "server"
            / "routers"
            / "governance_router.py"
        ).read_text(encoding="utf-8")

    def test_uses_streaming_response_for_download(self):
        self.assertIn("StreamingResponse", self.src)
        self.assertIn("_file_chunks(info[\"abs_path\"])", self.src)

    def test_content_disposition_rfc5987(self):
        self.assertIn("filename*=UTF-8''", self.src)
        self.assertIn("quote(filename", self.src)

    def test_permission_guard_on_management_endpoints(self):
        # 管理端点：PATCH / sync / export 仅 superadmin
        self.assertGreaterEqual(self.src.count("get_superadmin_user"), 3)
        # 列表/详情/下载允许已登录用户（受控下载在服务层鉴权）
        self.assertIn("get_required_user", self.src)

    def test_no_absolute_path_in_responses(self):
        # 路由不直接读取 KnowledgeFile.path 拼接响应
        self.assertNotIn("file_row.path", self.src)
        self.assertIn("resolve_download", self.src)

    def test_audit_records_download_success_and_failure(self):
        self.assertIn("\"knowledge.download\"", self.src)
        self.assertGreaterEqual(self.src.count("\"knowledge.download\""), 2)


if __name__ == "__main__":
    unittest.main()
