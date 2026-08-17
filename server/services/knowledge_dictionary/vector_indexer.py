"""字典向量索引（设计文档 §10）：独立 Milvus 集合、增量索引、一致性校验与语义检索。

- 关系数据库是唯一事实来源；Milvus 是可删除、可重建的派生索引；
- 独立物理集合 knowledge_dictionary_entries_v1（embedding 模型变化时新建 v2）；
- 向量文本只含字段语义，不拼入证据原文；
- 服务端构造过滤表达式，前端不能传任意 Milvus 表达式/集合名/模型名；
- 召回后回查关系数据库再次校验版本、条目状态与用户权限。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from server.models.knowledge_dictionary_models import (
    KnowledgeDictionary,
    KnowledgeDictionaryEntry,
    KnowledgeDictionaryVersion,
)

from . import repository as repo
from .errors import NotFound, ServiceUnavailable, ValidationError
from .permissions import is_manager

logger = logging.getLogger("sage.knowledge-dictionary.vector")

COLLECTION_NAME = "knowledge_dictionary_entries_v1"
VECTOR_TEXT_TEMPLATE = (
    "分类：{category}\n"
    "标准名称：{standard_name}\n"
    "定义：{definition}\n"
    "单位：{unit}\n"
    "数据类型：{data_type}\n"
    "同义词：{synonyms}\n"
    "取值规则：{value_rule}"
)

# 元数据字段（§10.2）
_META_FIELDS = [
    ("dictionary_id", "Int64"),
    ("version_id", "Int64"),
    ("entry_id", "Int64"),
    ("category", "VARCHAR", 255),
    ("standard_name", "VARCHAR", 255),
    ("data_type", "VARCHAR", 32),
    ("unit", "VARCHAR", 64),
    ("review_status", "VARCHAR", 20),
    ("version_status", "VARCHAR", 20),
    ("content_hash", "VARCHAR", 64),
    ("embedding_config_hash", "VARCHAR", 64),
    ("text", "VARCHAR", 8192),
]

_MAX_QUERY_LIMIT = 16384


def vector_index_enabled() -> bool:
    return os.getenv("DICTIONARY_VECTOR_ENABLED", "true").lower() not in ("0", "false", "no")


def milvus_uri() -> str:
    return os.getenv("MILVUS_URI", "http://milvus:19530")


def get_milvus_client():
    """惰性创建 Milvus 客户端（不加载任何模型）。"""
    from pymilvus import MilvusClient

    client = MilvusClient(uri=milvus_uri())
    client.list_collections()  # 连接探测
    return client


def current_embedding_config_hash() -> str:
    """系统固定 embedding 模型的配置哈希（模型名 + 维度 + 归一化）。"""
    from src import config
    from src.models.embedding import get_embedding_model

    model = get_embedding_model()
    if model is None:
        raise ServiceUnavailable("系统未配置 embedding 模型，无法建立字典向量索引")
    payload = {
        "model": config.embed_model,
        "dimension": int(model.get_dimension() or 0),
        "normalized": True,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _make_embed(embed: Optional[Callable[[List[str]], List[List[float]]]]) -> Callable[[List[str]], List[List[float]]]:
    if embed is not None:
        return embed
    from src.models.embedding import get_embedding_model

    model = get_embedding_model()
    if model is None:
        raise ServiceUnavailable("系统未配置 embedding 模型")

    def _embed(texts: List[str]) -> List[List[float]]:
        return model.batch_encode(texts, batch_size=16)

    return _embed


def embed_texts(texts: List[str], embed: Optional[Callable] = None) -> List[List[float]]:
    return _make_embed(embed)(texts)


def vector_text(entry: KnowledgeDictionaryEntry) -> str:
    return VECTOR_TEXT_TEMPLATE.format(
        category=entry.category or "",
        standard_name=entry.standard_name or "",
        definition=entry.definition or "",
        unit=entry.unit or "",
        data_type=entry.data_type or "",
        synonyms=", ".join(json.loads(entry.synonyms) if isinstance(entry.synonyms, str) else (entry.synonyms or [])),
        value_rule=entry.value_rule or "",
    )


# ---------------------------------------------------------------------------
# 集合 schema（v1；embedding 变化时创建新物理集合，绝不混写）
# ---------------------------------------------------------------------------


def ensure_collection(client, dimension: int) -> None:
    if client.has_collection(COLLECTION_NAME):
        # 集合已存在：校验维度一致，embedding 模型变化时禁止混写旧集合（§10.1）
        try:
            desc = client.describe_collection(COLLECTION_NAME)
            for field in desc.get("fields", []):
                if field.get("type") in (101, "FLOAT_VECTOR") or field.get("name") == "vector":
                    existing_dim = field.get("params", {}).get("dim")
                    if existing_dim and int(existing_dim) != int(dimension):
                        raise ServiceUnavailable(
                            f"集合 {COLLECTION_NAME} 维度({existing_dim})与当前 embedding 模型维度({dimension})不一致，"
                            "请创建新集合版本（如 knowledge_dictionary_entries_v2）或恢复原 embedding 配置"
                        )
                    break
        except ServiceUnavailable:
            raise
        except Exception:
            pass  # describe 不可用时交由后续操作报错
        return
    from pymilvus import CollectionSchema, DataType, FieldSchema

    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dimension),
    ]
    for name, dtype, *rest in _META_FIELDS:
        if dtype == "Int64":
            fields.append(FieldSchema(name=name, dtype=DataType.INT64))
        else:
            fields.append(FieldSchema(name=name, dtype=DataType.VARCHAR, max_length=rest[0]))
    schema = CollectionSchema(fields=fields, description="knowledge dictionary entries v1")
    client.create_collection(collection_name=COLLECTION_NAME, schema=schema)
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    index_params.add_index(field_name="entry_id")
    index_params.add_index(field_name="version_id")
    index_params.add_index(field_name="dictionary_id")
    client.create_index(collection_name=COLLECTION_NAME, index_params=index_params)
    client.load_collection(COLLECTION_NAME)


def _entry_upsert_row(entry: KnowledgeDictionaryEntry, version: KnowledgeDictionaryVersion, config_hash: str) -> Dict[str, Any]:
    return {
        "id": int(entry.id),
        "vector": None,  # 占位，由 embedding 填充
        "dictionary_id": int(version.dictionary_id),
        "version_id": int(version.id),
        "entry_id": int(entry.id),
        "category": (entry.category or "")[:250],
        "standard_name": (entry.standard_name or "")[:250],
        "data_type": (entry.data_type or "")[:30],
        "unit": (entry.unit or "")[:60],
        "review_status": (entry.review_status or "")[:20],
        "version_status": (version.status or "")[:20],
        "content_hash": (entry.content_hash or "")[:64],
        "embedding_config_hash": config_hash[:64],
        "text": vector_text(entry)[:8000],
    }


# ---------------------------------------------------------------------------
# 增量索引（§10.3）
# ---------------------------------------------------------------------------

_BATCH_SIZE = 32


def reindex_version(
    db: Session,
    version: KnowledgeDictionaryVersion,
    *,
    embed: Optional[Callable] = None,
    heartbeat: Optional[Callable[..., None]] = None,
    client=None,
    config_hash: Optional[str] = None,
) -> None:
    """重建/增量同步草稿版本索引：pending -> embedding -> indexed -> verified -> ready。

    草稿范围包含未被驳回的候选（pending/approved/conflict），拒绝条目删除对应向量。
    config_hash 仅测试注入；生产环境使用系统当前 embedding 配置哈希。
    """
    client = client or get_milvus_client()
    embed_fn = _make_embed(embed)
    config_hash = config_hash or current_embedding_config_hash()

    def _beat(**kwargs: Any) -> None:
        if heartbeat is not None:
            heartbeat(**kwargs)

    entries = (
        db.query(KnowledgeDictionaryEntry)
        .filter(
            KnowledgeDictionaryEntry.version_id == version.id,
            KnowledgeDictionaryEntry.review_status != "rejected",
        )
        .order_by(KnowledgeDictionaryEntry.id)
        .all()
    )
    dimension = _dimension_of(embed_fn)
    ensure_collection(client, dimension)
    version.index_status = "embedding"
    version.embedding_config_hash = config_hash
    db.commit()

    wanted_ids = {int(e.id) for e in entries}

    # 1) 删除集合中不属于当前版本的悬挂向量 + 被删除/驳回条目向量（§10.5）
    existing_ids = _collection_entry_ids(client, version.id)
    stale_ids = [eid for eid in existing_ids if eid not in wanted_ids]
    for i in range(0, len(stale_ids), 500):
        chunk = stale_ids[i : i + 500]
        client.delete(COLLECTION_NAME, filter=f"entry_id in {chunk}")

    # 2) 分批 embedding + upsert（§12.2 单批失败不得把整个版本标记为完成）
    indexable = [e for e in entries if (e.index_status or "pending") != "indexed"]
    total = len(indexable)
    failed_batches = 0
    last_error = ""
    for i in range(0, total, _BATCH_SIZE):
        chunk = indexable[i : i + _BATCH_SIZE]
        rows = [_entry_upsert_row(e, version, config_hash) for e in chunk]
        try:
            vectors = embed_fn([r["text"] for r in rows])
            for row, vector in zip(rows, vectors):
                row["vector"] = vector
            client.upsert(collection_name=COLLECTION_NAME, data=rows)
        except Exception as exc:  # 单批失败跳过，其余批次继续（§12.2）
            failed_batches += 1
            last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            logger.warning("字典向量批次 upsert 失败（跳过该批次）: %s", last_error)
            continue
        for e in chunk:
            e.index_status = "indexed"
            e.vector_id = f"{COLLECTION_NAME}:{e.id}"
        db.commit()
        _beat(
            stage=f"embedding:{min(i + _BATCH_SIZE, total)}/{total}",
            progress=round(10 + 70 * min(i + _BATCH_SIZE, total) / max(total, 1), 2),
            checkpoint={"phase": "embedding", "offset": i + len(chunk)},
        )

    if failed_batches:
        logger.warning("字典版本 %s 索引批次失败 %s/%s，最近错误: %s", version.id, failed_batches, total or 0, last_error)

    version.index_status = "indexed"
    # 精确重算：集合内属于该版本的唯一 entry_id 数
    version.vector_count = len(_collection_entry_ids(client, version.id))
    db.commit()

    # 3) 一致性校验（§10.5）
    _beat(stage="verifying", progress=92.0)
    _verify_consistency(db, version, client, config_hash)

    version.index_status = "ready"
    db.commit()
    _beat(stage="ready", progress=100.0)


def _dimension_of(embed_fn: Callable[[List[str]], List[List[float]]]) -> int:
    vectors = embed_fn(["维度探测"])
    if not vectors or not vectors[0]:
        raise ServiceUnavailable("embedding 模型返回空向量")
    return len(vectors[0])


def _collection_entry_ids(client, version_id: int) -> List[int]:
    ids: List[int] = []
    offset = 0
    while True:
        rows = client.query(
            collection_name=COLLECTION_NAME,
            filter=f"version_id == {int(version_id)}",
            output_fields=["entry_id"],
            limit=_MAX_QUERY_LIMIT,
            offset=offset,
        )
        ids.extend(int(r["entry_id"]) for r in rows)
        if len(rows) < _MAX_QUERY_LIMIT:
            break
        offset += len(rows)
    return sorted(set(ids))


def _verify_consistency(db: Session, version: KnowledgeDictionaryVersion, client, config_hash: str) -> None:
    """§10.5：条目数一致、无悬挂向量、抽样检索可回查、embedding 配置一致。"""
    expected = (
        db.query(KnowledgeDictionaryEntry)
        .filter(
            KnowledgeDictionaryEntry.version_id == version.id,
            KnowledgeDictionaryEntry.review_status != "rejected",
        )
        .count()
    )
    actual = len(_collection_entry_ids(client, version.id))
    if expected != actual:
        raise ValidationError(
            f"向量一致性校验失败：数据库条目数({expected})与向量数({actual})不一致"
        )
    if version.embedding_config_hash != config_hash:
        raise ValidationError("embedding 配置与集合 schema 不一致")
    # 抽样检索回查
    sample = (
        db.query(KnowledgeDictionaryEntry)
        .filter(
            KnowledgeDictionaryEntry.version_id == version.id,
            KnowledgeDictionaryEntry.review_status != "rejected",
        )
        .order_by(KnowledgeDictionaryEntry.id)
        .limit(1)
        .first()
    )
    if sample is not None:
        rows = client.query(
            collection_name=COLLECTION_NAME,
            filter=f"entry_id == {int(sample.id)}",
            output_fields=["content_hash"],
            limit=1,
        )
        if not rows or str(rows[0].get("content_hash")) != str(sample.content_hash):
            raise ValidationError("抽样检索内容哈希不一致，请重建索引")


def delete_entry_vector(client, entry_id: int) -> None:
    client.delete(COLLECTION_NAME, filter=f"entry_id == {int(entry_id)}")


# ---------------------------------------------------------------------------
# 检索（§10.4 / §13.4）
# ---------------------------------------------------------------------------


def search_entries(
    db: Session,
    user: Any,
    *,
    query: str,
    dictionary_ids: Optional[List[int]] = None,
    top_k: int = 5,
    version_id: Optional[int] = None,
    include_draft: bool = False,
    embed: Optional[Callable] = None,
    client=None,
) -> Dict[str, Any]:
    """语义检索：服务端构造过滤表达式；召回后回查数据库二次授权。"""
    query = (query or "").strip()
    if not query:
        raise ValidationError("检索 query 不能为空")
    top_k = max(1, min(int(top_k), 20))

    manager = is_manager(user)
    if (version_id is not None or include_draft) and not manager:
        from .errors import Forbidden

        raise Forbidden("只有管理员可以检索草稿版本")

    filters = _build_filter(user, dictionary_ids=dictionary_ids, version_id=version_id, include_draft=include_draft)
    try:
        client = client or get_milvus_client()
        embed_fn = _make_embed(embed)
        vector = embed_fn([query])[0]
    except ServiceUnavailable:
        raise
    except Exception as exc:  # Milvus / 模型不可用
        raise ServiceUnavailable(f"向量检索暂时不可用: {type(exc).__name__}") from exc
    try:
        results = client.search(
            collection_name=COLLECTION_NAME,
            data=[vector],
            limit=top_k * 3,
            filter=filters,
            output_fields=[
                "dictionary_id",
                "version_id",
                "entry_id",
                "standard_name",
                "category",
                "data_type",
                "unit",
                "review_status",
                "version_status",
                "content_hash",
            ],
        )
    except Exception as exc:  # Milvus 不可用
        raise ServiceUnavailable(f"向量检索暂时不可用: {type(exc).__name__}") from exc
    hits = results[0] if results else []

    items = []
    for hit in hits:
        meta = hit.get("entity", hit)
        entry_id = int(meta.get("entry_id") or 0)
        entry = (
            db.query(KnowledgeDictionaryEntry)
            .filter(KnowledgeDictionaryEntry.id == entry_id)
            .first()
        )
        if entry is None:
            continue
        version = (
            db.query(KnowledgeDictionaryVersion)
            .filter(KnowledgeDictionaryVersion.id == entry.version_id)
            .first()
        )
        dictionary = (
            db.query(KnowledgeDictionary)
            .filter(KnowledgeDictionary.id == version.dictionary_id, KnowledgeDictionary.is_deleted == 0)
            .first()
        )
        if version is None or dictionary is None:
            continue
        # 数据库二次授权（Milvus 元数据不能代替最终授权，§10.4）
        if not _entry_visible_to(user, entry, version, dictionary):
            continue
        if entry.review_status == "rejected":
            continue
        item = repo.serialize_entry(entry)
        item["similarity"] = round(float(hit.get("distance", 0.0)), 4)
        item["dictionary_id"] = dictionary.id
        item["dictionary_name"] = dictionary.name
        item["version_no"] = version.version_no
        item["evidence_summary"] = _evidence_summary(db, entry.id)
        if not manager:
            item.pop("confidence", None)
            item.pop("review_note", None)
        items.append(item)
        if len(items) >= top_k:
            break
    return {"items": items, "query": query, "top_k": top_k}


def _build_filter(
    user: Any,
    *,
    dictionary_ids: Optional[List[int]] = None,
    version_id: Optional[int] = None,
    include_draft: bool = False,
) -> str:
    """服务端构造 Milvus 过滤表达式（§10.4：不能信任前端任意表达式）。

    过滤只做粗筛；Milvus 元数据不能代替最终授权——召回后由
    `_entry_visible_to` 回查关系数据库再次校验版本/条目状态与用户权限。
    """
    manager = is_manager(user)
    parts = []
    if version_id is not None:
        parts.append(f"version_id == {int(version_id)}")
        if include_draft:
            parts.append('review_status in ["pending", "approved", "conflict"]')
        else:
            parts.append('review_status == "approved"')
    else:
        if include_draft:
            parts.append('review_status in ["pending", "approved", "conflict"]')
        else:
            parts.append('review_status == "approved"')
    if dictionary_ids:
        ids = ",".join(str(int(d)) for d in dictionary_ids[:50])
        parts.append(f"dictionary_id in [{ids}]")
    return " and ".join(f"({p})" for p in parts)


def _entry_visible_to(user: Any, entry: KnowledgeDictionaryEntry, version: KnowledgeDictionaryVersion, dictionary: KnowledgeDictionary) -> bool:
    if is_manager(user):
        return True
    return (
        dictionary.status == "published"
        and version.status == "published"
        and dictionary.active_version_id == version.id
        and entry.review_status == "approved"
    )


def _evidence_summary(db: Session, entry_id: int) -> List[Dict[str, Any]]:
    from server.models.knowledge_dictionary_models import KnowledgeDictionaryEvidence, KnowledgeDictionarySource

    rows = (
        db.query(KnowledgeDictionaryEvidence, KnowledgeDictionarySource)
        .join(KnowledgeDictionarySource, KnowledgeDictionaryEvidence.source_id == KnowledgeDictionarySource.id, isouter=True)
        .filter(KnowledgeDictionaryEvidence.entry_id == entry_id)
        .limit(5)
        .all()
    )
    out = []
    for ev, source in rows:
        out.append(
            {
                "id": ev.id,
                "quote": (ev.quote or "")[:200],
                "page_no": ev.page_no,
                "sheet_name": ev.sheet_name,
                "cell_range": ev.cell_range,
                "file_name": source.file_name if source is not None else None,
            }
        )
    return out


def version_index_status(db: Session, dictionary_id: int, version_id: int) -> Dict[str, Any]:
    repo.get_dictionary(db, dictionary_id)
    version = repo.get_version_of_dictionary(db, dictionary_id, version_id)
    return {"index_status": version.index_status, "vector_count": version.vector_count, "embedding_config_hash": version.embedding_config_hash}


# 供 publish 门禁使用：Milvus 当前向量数（数据库会话外验证）
def milvus_vector_count(version_id: int, client=None) -> Optional[int]:
    try:
        client = client or get_milvus_client()
        return len(_collection_entry_ids(client, version_id))
    except Exception:
        return None
