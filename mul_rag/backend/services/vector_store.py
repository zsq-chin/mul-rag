from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any, Protocol

import sqlite3

from services.pdf_service import read_kb_metadata


MetaTuple = Tuple[str, str, str]  # (entity_key, source, chunk_text)


class VectorStore(Protocol):
    def load(self) -> bool:
        """Load underlying index/collection if it exists. Return True if exists."""

    def add_embeddings(self, vectors: List[List[float]], metas: List[MetaTuple]) -> None:
        """Insert metas into SQLite to allocate ids, then add vectors to vector store."""

    def search(
        self, vector: List[float], top_k: int = 5, entity_keys: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Return list of {id, score, entity_key, source, chunk_text}."""

    def remove_by_ids(self, ids: List[int]) -> int:
        """Remove vectors by ids. Return removed count (best-effort)."""


def _sanitize_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe or "default"


@dataclass
class FaissVectorStore:
    index_path: str
    conn: sqlite3.Connection

    def _store(self):
        from services.faiss_store import FaissStore

        return FaissStore(index_path=self.index_path, conn=self.conn)

    def load(self) -> bool:
        if os.path.exists(self.index_path):
            self._store().load()
            # load() mutates store instance; we recreate store each call,
            # so we need to persist it.
        return os.path.exists(self.index_path)

    def add_embeddings(self, vectors: List[List[float]], metas: List[MetaTuple]) -> None:
        store = self._store()
        if os.path.exists(self.index_path):
            store.load()
        store.add_embeddings(vectors, metas)

    def search(
        self, vector: List[float], top_k: int = 5, entity_keys: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        store = self._store()
        if not os.path.exists(self.index_path):
            return []
        store.load()
        return store.search(vector, top_k=top_k, entity_keys=entity_keys)

    def remove_by_ids(self, ids: List[int]) -> int:
        store = self._store()
        if not os.path.exists(self.index_path):
            return 0
        store.load()
        return store.remove_by_ids(ids)


@dataclass
class MilvusVectorStore:
    kb_id: str
    conn: sqlite3.Connection
    uri: Optional[str] = None

    @property
    def collection_name(self) -> str:
        return f"kb_{_sanitize_name(self.kb_id)}"

    def _connect(self):
        from pymilvus import connections

        uri = self.uri or os.getenv("MILVUS_URI") or os.getenv("MILVUS_HOST")
        if not uri:
            # Support legacy MILVUS_HOST/MILVUS_PORT
            host = os.getenv("MILVUS_HOST", "127.0.0.1")
            port = os.getenv("MILVUS_PORT", "19530")
            uri = f"http://{host}:{port}"
        connections.connect(alias="default", uri=uri)

    def _ensure_collection(self, dim: int):
        from pymilvus import (
            FieldSchema,
            CollectionSchema,
            DataType,
            Collection,
            utility,
        )

        self._connect()
        if not utility.has_collection(self.collection_name):
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=False),
                FieldSchema(
                    name="entity_key", dtype=DataType.VARCHAR, max_length=256, is_primary=False
                ),
                FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=dim),
            ]
            schema = CollectionSchema(fields, description=f"KB vectors for {self.kb_id}")
            col = Collection(self.collection_name, schema)
            # Create index
            try:
                col.create_index(
                    field_name="vector",
                    index_params={"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 1024}},
                )
            except Exception:
                # Index creation can fail on small dims / older versions; still usable.
                pass
            col.load()
        else:
            col = Collection(self.collection_name)
            col.load()
        return col

    def load(self) -> bool:
        try:
            from pymilvus import utility

            self._connect()
            exists = utility.has_collection(self.collection_name)
            if exists:
                from pymilvus import Collection

                Collection(self.collection_name).load()
            return bool(exists)
        except Exception:
            return False

    def add_embeddings(self, vectors: List[List[float]], metas: List[MetaTuple]) -> None:
        import numpy as np

        vectors_np = np.array(vectors, dtype="float32")
        if vectors_np.ndim != 2 or vectors_np.shape[0] == 0:
            return
        dim = int(vectors_np.shape[1])

        # 1) allocate ids in sqlite
        cur = self.conn.cursor()
        ids: List[int] = []
        entity_keys: List[str] = []
        for key, source, chunk_text in metas:
            cur.execute(
                "INSERT INTO embeddings (entity_key, source, chunk_text) VALUES (?, ?, ?)",
                (key, source, chunk_text),
            )
            ids.append(int(cur.lastrowid))
            entity_keys.append(key)
        self.conn.commit()

        # 2) insert vectors to milvus
        col = self._ensure_collection(dim)
        # normalize like FAISS (IP)
        try:
            import faiss

            faiss.normalize_L2(vectors_np)
        except Exception:
            pass
        col.insert([ids, entity_keys, vectors_np.tolist()])
        try:
            col.flush()
        except Exception:
            pass

    def search(
        self, vector: List[float], top_k: int = 5, entity_keys: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        import numpy as np

        if not self.load():
            return []
        from pymilvus import Collection

        col = Collection(self.collection_name)

        v = np.array(vector, dtype="float32").reshape(1, -1)
        try:
            import faiss

            faiss.normalize_L2(v)
        except Exception:
            pass

        expr = None
        if entity_keys:
            escaped = [k.replace("\\", "\\\\").replace("'", "\\'") for k in entity_keys]
            expr = "entity_key in [" + ", ".join(["'" + k + "'" for k in escaped]) + "]"

        res = col.search(
            data=v.tolist(),
            anns_field="vector",
            param={"metric_type": "IP", "params": {"nprobe": 10}},
            limit=top_k,
            expr=expr,
            output_fields=["entity_key"],
        )

        ids: List[int] = []
        scores: List[float] = []
        for hit in (res[0] if res else []):
            ids.append(int(hit.id))
            scores.append(float(hit.distance))

        if not ids:
            return []

        cur = self.conn.cursor()
        results: List[Dict[str, Any]] = []
        for i, id_ in enumerate(ids):
            cur.execute("SELECT entity_key, source, chunk_text FROM embeddings WHERE id=?", (id_,))
            row = cur.fetchone()
            if row:
                results.append(
                    {
                        "id": id_,
                        "score": scores[i],
                        "entity_key": row[0],
                        "source": row[1],
                        "chunk_text": row[2],
                    }
                )
        return results

    def remove_by_ids(self, ids: List[int]) -> int:
        if not ids:
            return 0
        if not self.load():
            return 0
        try:
            from pymilvus import Collection

            col = Collection(self.collection_name)
            expr = f"id in [{', '.join(str(int(i)) for i in ids)}]"
            col.delete(expr)
            try:
                col.flush()
            except Exception:
                pass
            return len(ids)
        except Exception:
            return 0


@dataclass
class ElasticsearchVectorStore:
    kb_id: str
    conn: sqlite3.Connection
    url: Optional[str] = None

    @property
    def index_name(self) -> str:
        return f"kb-{_sanitize_name(self.kb_id).lower()}"

    def _client(self):
        from elasticsearch import Elasticsearch

        url = self.url or os.getenv("ES_URL") or os.getenv("ELASTICSEARCH_URL") or "http://127.0.0.1:9200"
        user = os.getenv("ES_USER")
        password = os.getenv("ES_PASSWORD")
        if user and password:
            return Elasticsearch(url, basic_auth=(user, password))
        return Elasticsearch(url)

    def _ensure_index(self, dim: int):
        es = self._client()
        if not es.indices.exists(index=self.index_name):
            mapping = {
                "mappings": {
                    "properties": {
                        "entity_key": {"type": "keyword"},
                        "chunk_text": {"type": "text"},
                        # script_score can work without vector indexing; keep mapping minimal
                        "vector": {"type": "dense_vector", "dims": dim, "index": False},
                    }
                }
            }
            es.indices.create(index=self.index_name, **mapping)
        return es

    def load(self) -> bool:
        try:
            es = self._client()
            return bool(es.indices.exists(index=self.index_name))
        except Exception:
            return False

    def add_embeddings(self, vectors: List[List[float]], metas: List[MetaTuple]) -> None:
        if not vectors:
            return
        dim = len(vectors[0])
        es = self._ensure_index(dim)

        # allocate ids in sqlite
        cur = self.conn.cursor()
        ids: List[int] = []
        entity_keys: List[str] = []
        chunk_texts: List[str] = []
        for key, source, chunk_text in metas:
            cur.execute(
                "INSERT INTO embeddings (entity_key, source, chunk_text) VALUES (?, ?, ?)",
                (key, source, chunk_text),
            )
            ids.append(int(cur.lastrowid))
            entity_keys.append(key)
            chunk_texts.append(chunk_text)
        self.conn.commit()

        # bulk index
        try:
            from elasticsearch.helpers import bulk

            actions = []
            for i, id_ in enumerate(ids):
                actions.append(
                    {
                        "_op_type": "index",
                        "_index": self.index_name,
                        "_id": str(id_),
                        "_source": {
                            "entity_key": entity_keys[i],
                            "chunk_text": chunk_texts[i],
                            "vector": vectors[i],
                        },
                    }
                )
            bulk(es, actions, refresh=True)
        except Exception as e:
            raise RuntimeError(f"Failed to index into Elasticsearch: {e}")

    def search(
        self, vector: List[float], top_k: int = 5, entity_keys: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        if not self.load():
            return []
        es = self._client()

        filters = []
        if entity_keys:
            filters.append({"terms": {"entity_key": entity_keys}})

        body = {
            "size": top_k,
            "query": {
                "script_score": {
                    "query": {"bool": {"filter": filters}} if filters else {"match_all": {}},
                    "script": {
                        "source": "cosineSimilarity(params.query_vector, 'vector') + 1.0",
                        "params": {"query_vector": vector},
                    },
                }
            },
        }

        resp = es.search(index=self.index_name, body=body)
        hits = (resp or {}).get("hits", {}).get("hits", [])
        ids: List[int] = []
        scores: List[float] = []
        for h in hits:
            try:
                ids.append(int(h.get("_id")))
                scores.append(float(h.get("_score", 0.0)))
            except Exception:
                continue

        if not ids:
            return []

        cur = self.conn.cursor()
        results: List[Dict[str, Any]] = []
        for i, id_ in enumerate(ids):
            cur.execute("SELECT entity_key, source, chunk_text FROM embeddings WHERE id=?", (id_,))
            row = cur.fetchone()
            if row:
                results.append(
                    {
                        "id": id_,
                        "score": scores[i],
                        "entity_key": row[0],
                        "source": row[1],
                        "chunk_text": row[2],
                    }
                )
        return results

    def hybrid_search(
        self,
        query_text: str,
        query_vector: List[float],
        top_k: int = 10,
        entity_keys: Optional[List[str]] = None,
        bm25_weight: float = 0.05,
    ) -> List[Dict[str, Any]]:
        """Unified retrieval in ES: BM25(text) + vector cosine similarity.

        Returns hydrated results from SQLite to keep existing API shape.
        """
        if not self.load():
            return []

        es = self._client()

        filters = []
        if entity_keys:
            filters.append({"terms": {"entity_key": entity_keys}})

        # Use a bool query to compute BM25 score in _score, then combine in script_score.
        base_query: Dict[str, Any]
        if filters:
            base_query = {
                "bool": {
                    "filter": filters,
                    "should": [{"match": {"chunk_text": query_text}}],
                    "minimum_should_match": 0,
                }
            }
        else:
            base_query = {
                "bool": {
                    "should": [{"match": {"chunk_text": query_text}}],
                    "minimum_should_match": 0,
                }
            }

        body = {
            "size": top_k,
            "query": {
                "script_score": {
                    "query": base_query,
                    "script": {
                        "source": "cosineSimilarity(params.q, 'vector') + 1.0 + params.bw * _score",
                        "params": {"q": query_vector, "bw": float(bm25_weight)},
                    },
                }
            },
        }

        resp = es.search(index=self.index_name, body=body)
        hits = (resp or {}).get("hits", {}).get("hits", [])
        ids: List[int] = []
        scores: List[float] = []
        for h in hits:
            try:
                ids.append(int(h.get("_id")))
                scores.append(float(h.get("_score", 0.0)))
            except Exception:
                continue

        if not ids:
            return []

        cur = self.conn.cursor()
        results: List[Dict[str, Any]] = []
        for i, id_ in enumerate(ids):
            cur.execute("SELECT entity_key, source, chunk_text FROM embeddings WHERE id=?", (id_,))
            row = cur.fetchone()
            if row:
                results.append(
                    {
                        "id": id_,
                        "score": scores[i],
                        "entity_key": row[0],
                        "source": row[1],
                        "chunk_text": row[2],
                    }
                )
        return results

    def remove_by_ids(self, ids: List[int]) -> int:
        if not ids:
            return 0
        if not self.load():
            return 0
        es = self._client()
        try:
            from elasticsearch.helpers import bulk

            actions = [
                {"_op_type": "delete", "_index": self.index_name, "_id": str(int(i))}
                for i in ids
            ]
            bulk(es, actions, refresh=True, raise_on_error=False)
            return len(ids)
        except Exception:
            return 0


def get_vector_store(kb_id: str, conn: sqlite3.Connection) -> VectorStore:
    meta = {}
    try:
        meta = read_kb_metadata(kb_id) or {}
    except Exception:
        meta = {}

    vst = (meta.get("vectorStoreType") or "faiss").lower().strip()

    if vst == "faiss":
        idx_path = os.path.join("data", kb_id, "index", "index.faiss")
        return FaissVectorStore(index_path=idx_path, conn=conn)

    if vst == "milvus":
        try:
            import pymilvus  # noqa: F401
        except Exception as e:
            raise RuntimeError(
                "vectorStoreType=milvus requires package 'pymilvus'. Install it in backend env."
            ) from e
        return MilvusVectorStore(kb_id=kb_id, conn=conn)

    if vst in ("es", "elasticsearch"):
        try:
            import elasticsearch  # noqa: F401
        except Exception as e:
            raise RuntimeError(
                "vectorStoreType=es requires package 'elasticsearch'. Install it in backend env."
            ) from e
        return ElasticsearchVectorStore(kb_id=kb_id, conn=conn)

    raise ValueError(f"Unsupported vectorStoreType: {vst}")
