"""知识字典向量索引与检索测试（设计文档 §10 / §15.2）：独立集合、
增量 upsert/删除、一致性校验、发布门禁与检索权限。

使用内存假 Milvus 客户端 + 假 embedding，不加载真实 Milvus / src。
"""

from __future__ import annotations

import re
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.models import Base
import server.models.kb_models  # noqa: F401
import server.models.user_model  # noqa: F401
from server.models.knowledge_dictionary_models import (  # noqa: F401
    KnowledgeDictionary,
    KnowledgeDictionaryEntry,
    KnowledgeDictionaryEvidence,
    KnowledgeDictionaryVersion,
)

from server.services.knowledge_dictionary import (
    service as svc,
    vector_indexer,
)
from server.services.knowledge_dictionary.errors import (
    Forbidden,
    PublishBlocked,
    ValidationError,
)


class User:
    def __init__(self, user_id=1, role="admin"):
        self.id = user_id
        self.role = role
        self.username = f"u{user_id}"


class FakeMilvusClient:
    """最小内存 Milvus 客户端：支持 ensure_collection/reindex/search 所用子集。"""

    def __init__(self):
        self.rows = {}  # id -> row dict
        self.collections = set()
        self.dim = None
        self.indexes = []
        self.queries = 0

    def list_collections(self):
        return sorted(self.collections)

    def has_collection(self, collection_name):
        return collection_name in self.collections

    def create_collection(self, collection_name=None, schema=None, dimension=None):
        self.collections.add(collection_name or vector_indexer.COLLECTION_NAME)
        if schema is not None:
            for field in schema.fields:
                if field.dtype.name == "FLOAT_VECTOR":
                    self.dim = field.params.get("dim")
        elif dimension:
            self.dim = dimension

    def prepare_index_params(self):
        return _FakeIndexParams(self)

    def create_index(self, collection_name, index_params):
        self.indexes.append(collection_name)

    def load_collection(self, collection_name):
        pass

    def upsert(self, collection_name, data):
        for row in data:
            self.rows[int(row["id"])] = {k: v for k, v in row.items() if v is not None}

    def delete(self, collection_name, filter=None):
        ids = _parse_filter_ids(filter)
        for rid in ids:
            self.rows.pop(rid, None)

    def query(self, collection_name, filter=None, output_fields=None, limit=10, offset=0):
        rows = [r for r in self.rows.values() if _match_filter(r, filter)]
        return [
            {k: v for k, v in r.items() if k in (output_fields or []) or k == "entry_id"}
            for r in rows[offset : offset + limit]
        ]

    def search(self, collection_name, data, limit=10, filter=None, output_fields=None):
        rows = [r for r in self.rows.values() if _match_filter(r, filter)]
        hits = []
        for r in rows[:limit]:
            entity = {k: v for k, v in r.items() if k in (output_fields or [])}
            hits.append({"distance": 0.9, "entity": entity})
        return [hits]


class _FakeIndexParams:
    def __init__(self, client):
        self.client = client

    def add_index(self, field_name=None, **kwargs):
        return self


class _LossyClient(FakeMilvusClient):
    """模拟 Milvus 部分写入失败：查询时按数量丢行，触发一致性校验失败。"""

    def __init__(self, drop_on_query=0):
        super().__init__()
        self.drop_on_query = drop_on_query

    def query(self, collection_name, filter=None, output_fields=None, limit=10, offset=0):
        rows = [r for r in self.rows.values() if _match_filter(r, filter)]
        if self.drop_on_query:
            rows = rows[: max(0, len(rows) - self.drop_on_query)]
        return [
            {k: v for k, v in r.items() if k in (output_fields or []) or k == "entry_id"}
            for r in rows[offset : offset + limit]
        ]


def _parse_filter_ids(filter_expr):
    match = re.search(r"in \[([\d,\s]+)\]", filter_expr or "")
    if match:
        return [int(x) for x in match.group(1).split(",") if x.strip()]
    match = re.search(r"== (\d+)", filter_expr or "")
    if match:
        return [int(match.group(1))]
    return []


def _match_filter(row, filter_expr):
    if not filter_expr:
        return True
    expr = str(filter_expr)
    for cond in expr.split(" and "):
        cond = cond.strip(" ()")
        m = re.match(r"(\w+) == \"?([\w-]+)\"?$", cond)
        if m:
            key, value = m.group(1), m.group(2)
            if str(row.get(key)) != value:
                return False
        m = re.match(r"(\w+) in \[([^\]]*)\]$", cond)
        if m:
            key = m.group(1)
            values = [v.strip().strip('"') for v in m.group(2).split(",")]
            if str(row.get(key)) not in values:
                return False
        m = re.match(r"(\w+) == (-?\d+)$", cond)
        if m:
            if int(row.get(m.group(1)) or 0) != int(m.group(2)):
                return False
    return True


def _fake_embed(dim=8):
    def embed(texts):
        out = []
        for i, text in enumerate(texts):
            vec = [0.0] * dim
            vec[i % dim] = 1.0
            out.append(vec)
        return out

    return embed


@contextmanager
def _env():
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(f"sqlite:///{Path(tmp) / 'server.db'}")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        try:
            yield {"db": db, "root": Path(tmp)}
        finally:
            db.close()
            engine.dispose()


def _setup(db, role="admin", user_id=1):
    data = svc.create_dictionary(db, User(user_id, role), name="压裂字典", domain="石油工程")
    version = (
        db.query(KnowledgeDictionaryVersion)
        .filter(KnowledgeDictionaryVersion.dictionary_id == data["id"])
        .first()
    )
    return data, version


def _entry(db, version_id, name, review="pending", **extra):
    payload = {
        "standard_name": name,
        "definition": extra.pop("definition", f"{name}的定义"),
        "unit": extra.pop("unit", "%"),
        "data_type": extra.pop("data_type", "number"),
        "evidences": [{"quote": name, "field_path": "standard_name"}],
    }
    payload.update(extra)
    entry = svc.create_entry(db, User(1, "admin"), 1, version_id, payload)
    if review != "pending":
        entry_row = db.query(KnowledgeDictionaryEntry).get(entry["id"])
        entry_row.review_status = review
        db.commit()
    return entry


class ReindexTest(unittest.TestCase):
    def test_reindex_draft_scope_and_ready(self):
        with _env() as env:
            db = env["db"]
            client = FakeMilvusClient()
            data, version = _setup(db)
            _entry(db, version.id, "孔隙度", "approved")
            _entry(db, version.id, "渗透率", "pending")
            _entry(db, version.id, "含水率", "rejected")
            vector_indexer.reindex_version(
                db, version, embed=_fake_embed(), client=client, config_hash="test-hash"
            )
            version = db.query(KnowledgeDictionaryVersion).get(version.id)
            self.assertEqual(version.index_status, "ready")
            self.assertEqual(version.vector_count, 2)  # rejected 不入库
            self.assertEqual(version.embedding_config_hash, "test-hash")
            # 全部 upsert 行都带元数据
            for row in client.rows.values():
                self.assertEqual(row["embedding_config_hash"], "test-hash")
                self.assertEqual(row["version_status"], "draft")

    def test_consistency_failure_blocks_ready(self):
        with _env() as env:
            db = env["db"]
            client = _LossyClient(drop_on_query=1)  # Milvus 丢行：查询比写入少一条
            data, version = _setup(db)
            _entry(db, version.id, "孔隙度", "approved")
            _entry(db, version.id, "渗透率", "approved")
            with self.assertRaises(ValidationError):
                vector_indexer.reindex_version(db, version, embed=_fake_embed(), client=client, config_hash="h1")

    def test_reindex_is_incremental_and_removes_stale(self):
        with _env() as env:
            db = env["db"]
            client = FakeMilvusClient()
            data, version = _setup(db)
            e1 = _entry(db, version.id, "孔隙度", "approved")
            e2 = _entry(db, version.id, "渗透率", "approved")
            vector_indexer.reindex_version(db, version, embed=_fake_embed(), client=client, config_hash="h1")
            self.assertEqual(len(client.rows), 2)
            # 删除条目后重建：悬挂向量被清除
            svc.delete_entry(db, User(1, "admin"), data["id"], version.id, e2["id"])
            vector_indexer.reindex_version(db, version, embed=_fake_embed(), client=client, config_hash="h1")
            self.assertEqual(len(client.rows), 1)
            self.assertIn(e1["id"], client.rows)


class SearchTest(unittest.TestCase):
    def _published(self, db, version):
        """构造已发布且索引 ready 的版本。"""
        version.status = "reviewing"
        version.index_status = "ready"
        version.vector_count = 1
        db.commit()

    def test_user_search_only_published_approved(self):
        with _env() as env:
            db = env["db"]
            client = FakeMilvusClient()
            data, version = _setup(db)
            _entry(db, version.id, "孔隙度", "approved")
            _entry(db, version.id, "渗透率", "pending")
            vector_indexer.reindex_version(db, version, embed=_fake_embed(), client=client, config_hash="h1")
            dictionary = db.query(KnowledgeDictionary).get(data["id"])
            version = db.query(KnowledgeDictionaryVersion).get(version.id)
            version.status = "published"
            dictionary.status = "published"
            dictionary.active_version_id = version.id
            db.commit()
            # 普通用户：只能召回 approved
            result = vector_indexer.search_entries(
                db, User(2, "user"), query="孔隙度", top_k=10, client=client, embed=_fake_embed()
            )
            names = {item["standard_name"] for item in result["items"]}
            self.assertIn("孔隙度", names)
            self.assertNotIn("渗透率", names)
            self.assertNotIn("confidence", result["items"][0])  # 内部字段不回显

    def test_user_cannot_search_draft(self):
        with _env() as env:
            db = env["db"]
            client = FakeMilvusClient()
            data, version = _setup(db)
            _entry(db, version.id, "孔隙度", "approved")
            vector_indexer.reindex_version(db, version, embed=_fake_embed(), client=client, config_hash="h1")
            with self.assertRaises(Forbidden):
                vector_indexer.search_entries(
                    db, User(2, "user"), query="孔隙度", version_id=version.id, include_draft=True, client=client, embed=_fake_embed()
                )

    def test_manager_draft_search_excludes_rejected(self):
        with _env() as env:
            db = env["db"]
            client = FakeMilvusClient()
            data, version = _setup(db)
            _entry(db, version.id, "孔隙度", "pending")
            _entry(db, version.id, "被驳回", "rejected")
            vector_indexer.reindex_version(db, version, embed=_fake_embed(), client=client, config_hash="h1")
            result = vector_indexer.search_entries(
                db, User(1, "admin"), query="孔隙度", version_id=version.id, include_draft=True, client=client, embed=_fake_embed()
            )
            names = {item["standard_name"] for item in result["items"]}
            self.assertIn("孔隙度", names)
            self.assertNotIn("被驳回", names)

    def test_search_empty_query_rejected(self):
        with _env() as env:
            db = env["db"]
            client = FakeMilvusClient()
            with self.assertRaises(ValidationError):
                vector_indexer.search_entries(db, User(1, "admin"), query="   ", client=client, embed=_fake_embed())


class PublishVectorGateTest(unittest.TestCase):
    def test_publish_requires_matching_vector_count(self):
        with _env() as env:
            db = env["db"]
            client = FakeMilvusClient()
            data, version = _setup(db)
            entry = _entry(db, version.id, "孔隙度", "approved")
            vector_indexer.reindex_version(db, version, embed=_fake_embed(), client=client, config_hash="h1")
            version = db.query(KnowledgeDictionaryVersion).get(version.id)
            self.assertEqual(version.index_status, "ready")
            # 数据库 approved=1，向量=1 → 可发布（embedding_config_hash 已设置且一致时需注入当前哈希）
            version.embedding_config_hash = None
            db.commit()
            published = svc.publish_version(db, User(1, "admin"), data["id"], version.id)
            self.assertEqual(published["status"], "published")
            dictionary = db.query(KnowledgeDictionary).get(data["id"])
            self.assertEqual(dictionary.active_version_id, version.id)


if __name__ == "__main__":
    unittest.main()
