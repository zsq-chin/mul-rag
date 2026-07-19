import os
import faiss
import numpy as np
import sqlite3
from typing import List, Tuple, Optional

class FaissStore:
    def __init__(self, index_path: str, conn: sqlite3.Connection):
        self.index_path = index_path
        self.conn = conn
        self.index = None
        self.dim = None
        os.makedirs(os.path.dirname(index_path) or '.', exist_ok=True)

    def _ensure_index(self, dim: int):
        if self.index is None:
            # 改用 IndexFlatIP (精确搜索)，它原生支持 remove_ids
            # 虽然 HNSW 更快，但不支持删除，且对于中小型知识库 Flat 性能足够
            index = faiss.IndexFlatIP(dim)
            
            # 使用 IndexIDMap2 以支持 add_with_ids 和 remove_ids
            self.index = faiss.IndexIDMap2(index)
            self.dim = dim

    def add_embeddings(self, vectors: List[List[float]], metas: List[Tuple[str, str, str]]):
        # metas: list of tuples (key, source, chunk_text)
        vectors = np.array(vectors).astype('float32')
        n, dim = vectors.shape
        self._ensure_index(dim)
        # insert rows into sqlite embeddings table to get ids
        cur = self.conn.cursor()
        ids = []
        for meta in metas:
            key, source, chunk_text = meta
            cur.execute('INSERT INTO embeddings (entity_key, source, chunk_text) VALUES (?, ?, ?)', (key, source, chunk_text))
            ids.append(cur.lastrowid)
        self.conn.commit()
        ids_np = np.array(ids).astype('int64')
        # normalize vectors for IP if needed
        faiss.normalize_L2(vectors)
        self.index.add_with_ids(vectors, ids_np)
        # persist
        faiss.write_index(self.index, self.index_path)

    def search(self, vector: List[float], top_k: int = 5, entity_keys: Optional[List[str]] = None):
        v = np.array(vector).astype('float32').reshape(1, -1)
        faiss.normalize_L2(v)

        cur = self.conn.cursor()
        search_params = None

        # If entity_keys are provided, create a search selector
        if entity_keys:
            placeholders = ','.join('?' for _ in entity_keys)
            cur.execute(f"SELECT id FROM embeddings WHERE entity_key IN ({placeholders})", entity_keys)
            ids_to_search = [row[0] for row in cur.fetchall()]
            
            if not ids_to_search:
                return []

            # Use IDSelectorArray to restrict the search to a specific set of IDs
            id_selector = faiss.IDSelectorArray(np.array(ids_to_search, dtype='int64'))
            search_params = faiss.SearchParameters(sel=id_selector)

        # Perform the search. If search_params is None, it's a global search.
        D, I = self.index.search(v, top_k, params=search_params)
        
        result_ids = I[0].tolist()
        scores = D[0]

        # Retrieve metadata for the search results
        results = []
        for i, id_ in enumerate(result_ids):
            # In a filtered search, FAISS might return -1 for slots it couldn't fill
            if id_ < 0:
                continue
            cur.execute('SELECT entity_key, source, chunk_text FROM embeddings WHERE id=?', (int(id_),))
            row = cur.fetchone()
            if row:
                results.append({
                    'id': id_, 
                    'score': float(scores[i]), 
                    'entity_key': row[0], 
                    'source': row[1], 
                    'chunk_text': row[2]
                })
        return results

    def load(self):
        if os.path.exists(self.index_path):
            self.index = faiss.read_index(self.index_path)

    def remove_by_ids(self, ids: List[int]) -> int:
        """从FAISS索引中移除给定的向量ID，并持久化索引。
        如果当前索引类型不支持删除 (如 HNSW)，则尝试迁移到 IndexFlatIP。
        """
        if not ids or self.index is None:
            return 0
        
        id_arr = np.array(ids, dtype='int64')
        
        try:
            removed = self.index.remove_ids(id_arr)
        except RuntimeError as e:
            if "remove_ids not implemented" in str(e):
                print("Current index type does not support removal. Migrating to IndexFlatIP...")
                # 迁移策略：重建索引
                # 1. 获取所有现有 ID
                # 注意：IndexIDMap2 维护了 id_map，可以直接获取所有 ID
                # 但我们需要遍历所有 ID，排除掉要删除的，然后 reconstruct 向量
                
                # 获取所有 ID (faiss-python 绑定可能不直接暴露 id_map 为列表，需通过 faiss.vector_to_array)
                # 简单方法：假设 ID 是连续的？不，ID 是 SQLite 的 rowid，不一定连续。
                # 我们可以从 SQLite 获取所有 ID，然后查询 FAISS
                
                cur = self.conn.cursor()
                cur.execute("SELECT id FROM embeddings")
                all_ids = [row[0] for row in cur.fetchall()]
                
                ids_to_keep = set(all_ids) - set(ids)
                
                if not ids_to_keep:
                    # 如果删完后为空，直接重置
                    self.index = None
                    if os.path.exists(self.index_path):
                        os.remove(self.index_path)
                    return len(ids)

                # 2. 创建新索引
                dim = self.index.d
                new_inner_index = faiss.IndexFlatIP(dim)
                new_index = faiss.IndexIDMap2(new_inner_index)
                
                # 3. 迁移数据
                vectors = []
                valid_ids = []
                
                for i in ids_to_keep:
                    try:
                        # reconstruct 需要 ID
                        vec = self.index.reconstruct(i)
                        vectors.append(vec)
                        valid_ids.append(i)
                    except Exception as ex:
                        print(f"Failed to reconstruct vector for id {i}: {ex}")
                
                if vectors:
                    vectors_np = np.array(vectors).astype('float32')
                    ids_np = np.array(valid_ids).astype('int64')
                    new_index.add_with_ids(vectors_np, ids_np)
                
                # 4. 替换并保存
                self.index = new_index
                removed = len(ids) # 假设全部成功移除
            else:
                raise e

        # persist after removal
        faiss.write_index(self.index, self.index_path)
        return int(removed)