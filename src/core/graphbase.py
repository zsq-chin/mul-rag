import csv
import os
import json
import warnings
import chardet
import sys
import traceback
import shutil
from pathlib import Path
from typing import List
import requests

import torch
from neo4j import GraphDatabase as GD

from src import config
from src.utils import logger

warnings.filterwarnings("ignore", category=UserWarning)


UIE_MODEL = None

class GraphDatabase:
    def __init__(self):
        self.driver = None
        self.files = []
        self.status = "closed"
        self.kgdb_name = "neo4j"
        self.embed_model_name = None
        self.work_dir = os.path.join(config.save_dir, "knowledge_graph", self.kgdb_name)
        os.makedirs(self.work_dir, exist_ok=True)

        # 尝试加载已保存的图数据库信息
        if not self.load_graph_info():
            logger.debug("创建新的图数据库配置")

        self.start()

    def start(self):
        if not config.enable_knowledge_graph:
            return

        uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        username = os.environ.get("NEO4J_USERNAME")
        password = os.environ.get("NEO4J_PASSWORD")
        if not username or not password:
            logger.error(
                "Neo4j credentials not configured; "
                "set NEO4J_USERNAME and NEO4J_PASSWORD environment variables"
            )
            self.status = "closed"
            config.enable_knowledge_graph = False
            return
        logger.info(f"Connecting to Neo4j: {uri}/{self.kgdb_name}")
        try:
            self.driver = GD.driver(f"{uri}/{self.kgdb_name}", auth=(username, password))
            self.status = "open"
            logger.info(f"Connected to Neo4j: {self.get_graph_info(self.kgdb_name)}")
            # 连接成功后保存图数据库信息
            self.save_graph_info(self.kgdb_name)
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}, {uri}, {self.kgdb_name}")
            self.status = "closed"
            config.enable_knowledge_graph = False

    def close(self):
        """关闭数据库连接"""
        self.driver.close()

    def is_running(self):
        """检查图数据库是否正在运行"""
        if not config.enable_knowledge_graph:
            return False
        return self.status == "open"

    def get_sample_nodes(self, kgdb_name='neo4j', num=50):
        """获取指定数据库的 num 个节点信息"""
        self.use_database(kgdb_name)
        def query(tx, num):
            result = tx.run("MATCH (n)-[r]->(m) RETURN n, r, m LIMIT $num", num=int(num))
            return result.values()

        with self.driver.session() as session:
            return session.execute_read(query, num)

    def create_graph_database(self, kgdb_name):
        """创建新的数据库，如果已存在则返回已有数据库的名称"""
        with self.driver.session() as session:
            existing_databases = session.run("SHOW DATABASES")
            existing_db_names = [db['name'] for db in existing_databases]

            if existing_db_names:
                print(f"已存在数据库: {existing_db_names[0]}")
                return existing_db_names[0]  # 返回所有已有数据库名称

            session.run(f"CREATE DATABASE {kgdb_name}")
            print(f"数据库 '{kgdb_name}' 创建成功.")
            return kgdb_name  # 返回创建的数据库名称

    def use_database(self, kgdb_name="neo4j"):
        """切换到指定数据库"""
        assert kgdb_name == self.kgdb_name, f"传入的数据库名称 '{kgdb_name}' 与当前实例的数据库名称 '{self.kgdb_name}' 不一致"
        if self.status == "closed":
            self.start()

    def txt_add_entity(self, triples, kgdb_name='neo4j'):
        """添加实体三元组"""
        self.use_database(kgdb_name)
        def create(tx, triples):
            for triple in triples:
                h = triple['h']
                t = triple['t']
                r = triple['r']
                query = (
                    "MERGE (a:Entity {name: $h}) "
                    "MERGE (b:Entity {name: $t}) "
                    "MERGE (a)-[:" + r.replace(" ", "_") + "]->(b)"
                )
                tx.run(query, h=h, t=t)

        with self.driver.session() as session:
            session.execute_write(create, triples)

    async def txt_add_vector_entity(self, triples, kgdb_name='neo4j'):
        """添加实体三元组"""
        self.use_database(kgdb_name)
        def _index_exists(tx, index_name):
            """检查索引是否存在"""
            result = tx.run("SHOW INDEXES")
            for record in result:
                if record["name"] == index_name:
                    return True
            return False

        def _create_graph(tx, data):
            """添加一个三元组"""
            for entry in data:
                tx.run("""
                MERGE (h:Entity {name: $h})
                MERGE (t:Entity {name: $t})
                MERGE (h)-[r:RELATION {type: $r}]->(t)
                """, h=entry['h'], t=entry['t'], r=entry['r'])

        def _create_vector_index(tx, dim):
            """创建向量索引"""
            # NOTE 这里是否是会重复构建索引？
            index_name = "entityEmbeddings"
            if not _index_exists(tx, index_name):
                tx.run(f"""
                CREATE VECTOR INDEX {index_name}
                FOR (n: Entity) ON (n.embedding)
                OPTIONS {{indexConfig: {{
                `vector.dimensions`: {dim},
                `vector.similarity_function`: 'cosine'
                }} }};
                """)

        def _get_nodes_without_embedding(tx, entity_names):
            """获取没有embedding的节点列表"""
            # 构建参数字典，将列表转换为"param0"、"param1"等键值对形式
            params = {f"param{i}": name for i, name in enumerate(entity_names)}

            # 构建查询参数列表
            param_placeholders = ", ".join([f"${key}" for key in params.keys()])

            # 执行查询
            result = tx.run(f"""
            MATCH (n:Entity)
            WHERE n.name IN [{param_placeholders}] AND n.embedding IS NULL
            RETURN n.name AS name
            """, params)

            return [record["name"] for record in result]

        def _batch_set_embeddings(tx, entity_embedding_pairs):
            """批量设置实体的嵌入向量"""
            for entity_name, embedding in entity_embedding_pairs:
                tx.run("""
                MATCH (e:Entity {name: $name})
                CALL db.create.setNodeVectorProperty(e, 'embedding', $embedding)
                """, name=entity_name, embedding=embedding)

        # 判断模型名称是否匹配
        cur_embed_info = config.embed_model_names[config.embed_model]
        self.embed_model_name = self.embed_model_name or cur_embed_info.get('name')
        assert self.embed_model_name == cur_embed_info.get('name') or self.embed_model_name is None, \
            f"embed_model_name={self.embed_model_name}, {cur_embed_info.get('name')=}"

        with self.driver.session() as session:
            logger.info(f"Adding entity to {kgdb_name}")
            session.execute_write(_create_graph, triples)
            logger.info(f"Creating vector index for {kgdb_name} with {config.embed_model}")
            session.execute_write(_create_vector_index, cur_embed_info['dimension'])

            # 收集所有需要处理的实体名称，去重
            all_entities = []
            for entry in triples:
                if entry['h'] not in all_entities:
                    all_entities.append(entry['h'])
                if entry['t'] not in all_entities:
                    all_entities.append(entry['t'])

            # 筛选出没有embedding的节点
            nodes_without_embedding = session.execute_read(_get_nodes_without_embedding, all_entities)
            if not nodes_without_embedding:
                logger.info("所有实体已有embedding，无需重新计算")
                return

            logger.info(f"需要为{len(nodes_without_embedding)}/{len(all_entities)}个实体计算embedding")

            # 批量处理实体
            max_batch_size = 1024  # 限制此部分的主要是内存大小 1024 * 1024 * 4 / 1024 / 1024 = 4GB
            total_entities = len(nodes_without_embedding)

            for i in range(0, total_entities, max_batch_size):
                batch_entities = nodes_without_embedding[i:i+max_batch_size]
                logger.debug(
                    f"Processing entities batch "
                    f"{i//max_batch_size + 1}/{(total_entities-1)//max_batch_size + 1} "
                    f"({len(batch_entities)} entities)"
                )

                # 批量获取嵌入向量
                batch_embeddings = await self.aget_embedding(batch_entities)

                # 将实体名称和嵌入向量配对
                entity_embedding_pairs = list(zip(batch_entities, batch_embeddings))

                # 批量写入数据库
                session.execute_write(_batch_set_embeddings, entity_embedding_pairs)

            # 数据添加完成后保存图信息
            self.save_graph_info()

    async def jsonl_file_add_entity(self, file_path, kgdb_name='neo4j'):
        prev_status = self.status
        self.status = "processing"
        kgdb_name = kgdb_name or 'neo4j'
        try:
            self.use_database(kgdb_name)
            logger.info(f"Start adding entity to {kgdb_name} with {file_path}")

            with open(file_path, 'rb') as f:
                raw_data = f.read(4096)
                result = chardet.detect(raw_data)
                encoding = result['encoding'] or 'utf-8'
                print(f"检测到文件编码: {encoding}")

            triples = []
            with open(file_path, encoding=encoding, errors='ignore') as csvfile:
                reader = csv.DictReader(csvfile)
                if not set(['h', 'r', 't']).issubset(reader.fieldnames):
                    raise ValueError("CSV 文件必须包含列: h, r, t")
                for row in reader:
                    triples.append({
                        'h': row['h'].strip(),
                        'r': row['r'].strip(),
                        't': row['t'].strip()
                    })

            await self.txt_add_vector_entity(triples, kgdb_name)
            self.save_graph_info()
            return kgdb_name
        finally:
            self.status = prev_status

    def file_Handle(
            self,
            input_file: Path,
            external_api_url: str = "http://host.docker.internal:8000/api/v1/tasks/submit"  # 替换为实际地址
    ):
        print(f"\n===== 开始向外部接口 {external_api_url} 提交文件 =====")

        try:
            with open(input_file, "rb") as f:
                files = {"file": (input_file.name, f)}

                file_suffix = input_file.suffix.lower()
                data = {
                    # 核心修复：lang从auto改为ch（中文，接口表格识别支持）
                    "lang": "ch",
                    "method": "auto",
                    "formula_enable": "true",
                    "table_enable": "true",
                    "priority": "0",

                    # DeepSeek OCR 专用参数
                    "deepseek_resolution": "base",
                    "deepseek_prompt_type": "document",

                    # 视频处理参数
                    "keep_audio": "false",
                    "enable_keyframe_ocr": "false",
                    "ocr_backend": "paddleocr-vl",
                    "keep_keyframes": "false",

                    # 水印去除参数
                    "remove_watermark": "false",
                    "watermark_conf_threshold": "0.35",
                    "watermark_dilation": "10"
                }

                # 后端选择（严格避开deepseek-ocr）
                if file_suffix in ['.mp3', '.wav', '.ogg', '.flac']:
                    data["backend"] = "sensevoice"
                    data["lang"] = "ch"
                elif file_suffix in ['.mp4', '.avi', '.mov', '.mkv']:
                    data["backend"] = "video"
                    data["lang"] = "ch"
                else:
                    data["backend"] = "pipeline"

                # 发送请求
                response = requests.post(
                    url=external_api_url,
                    files=files,
                    data=data,
                    timeout=180
                )
                response.raise_for_status()
                result = response.json()

                print(f"✅ 成功提交文件: {input_file.name}")
                print(f"   任务ID: {result.get('task_id')}")
                print(f"   后端: {data['backend']} | 语言: {data['lang']}\n")

                return result  # ✅ 返回接口结果给调用者（比如 FastAPI 接口）

        except Exception as e:
            print(f"❌ 提交文件 {input_file.name} 失败: {str(e)}\n")
            return {"success": False, "message": str(e)}

        finally:
            print("===== 文件提交完成 =====")

    def copy_output(self, task_name: str) -> Path:
        # 1. 去掉 task_name 中的文件后缀，得到新变量 task_basename
        task_path = Path(task_name)
        task_basename = task_path.stem  # 例如："顺丰电子发票_f9df.pdf" → "顺丰电子发票_f9df"

        # 2. 使用 task_basename 拼接路径（根据实际目录结构调整）
        OUTPUT_DIR = Path("/app/output")
        source_md = (
                OUTPUT_DIR /
                task_basename /  # 用无后缀的名称作为目录
                f"{task_basename}.pdf" /  # 假设中间层需要带 .pdf 后缀
                "auto" /
                f"{task_basename}.pdf.md"  # 最终 md 文件名
        )

        print(f"尝试查找的文件路径: {source_md}")
        if not source_md.exists():
            raise FileNotFoundError(f"未找到输出文件: {source_md}")

        copy_dir = Path("/app/saves/data/copypath")
        copy_dir.mkdir(parents=True, exist_ok=True)

        target = copy_dir / source_md.name
        shutil.copy2(source_md, target)
        print(f"📁 已复制 {source_md} → {target}")
        return target

    def delete_entity(self, entity_name=None, kgdb_name="neo4j"):
        """删除数据库中的指定实体三元组, 参数entity_name为空则删除全部实体"""
        self.use_database(kgdb_name)
        with self.driver.session() as session:
            if entity_name:
                session.execute_write(self._delete_specific_entity, entity_name)
            else:
                session.execute_write(self._delete_all_entities)

    def _delete_specific_entity(self, tx, entity_name):
        query = """
        MATCH (n {name: $entity_name})
        DETACH DELETE n
        """
        tx.run(query, entity_name=entity_name)

    def _delete_all_entities(self, tx):
        query = """
        MATCH (n)
        DETACH DELETE n
        """
        tx.run(query)

    @staticmethod
    def _sanitize_node_properties(props):
        """Copy node properties, excluding embedding vectors.

        Returns a *new* dict so driver-owned objects are never mutated.
        """
        sanitized = {}
        for k, v in props.items():
            if k in ("embedding", "entityEmbeddings"):
                continue
            sanitized[k] = v
        return sanitized

    @staticmethod
    def _legacy_row_to_structured(row, entity_score):
        """Convert one legacy ``[source_node, [rel, …], target_node]`` row
        into a structured relation dict suitable for ``rank_unique_relations``.
        """
        fallback_src, relationships, fallback_tgt = row[0], row[1], row[2]

        dicts = []
        for rel in relationships:
            rel_props = dict(getattr(rel, "_properties", {}))
            relation_type = rel_props.get("type", "") or getattr(rel, "type", "unknown")
            relation_desc = rel_props.get("description", "")
            relation_id = getattr(rel, "element_id", None)

            # Use each relationship's own endpoints when available.
            rel_nodes = getattr(rel, "nodes", None)
            if rel_nodes and len(rel_nodes) == 2:
                src_node, tgt_node = rel_nodes
            else:
                src_node, tgt_node = fallback_src, fallback_tgt

            src_props = dict(getattr(src_node, "_properties", {}))
            tgt_props = dict(getattr(tgt_node, "_properties", {}))

            dicts.append({
                "source": src_props.get("name", "unknown"),
                "source_id": getattr(src_node, "element_id", None),
                "source_properties": GraphDatabase._sanitize_node_properties(src_props),
                "source_desc": src_props.get("description", ""),
                "target": tgt_props.get("name", "unknown"),
                "target_id": getattr(tgt_node, "element_id", None),
                "target_properties": GraphDatabase._sanitize_node_properties(tgt_props),
                "target_desc": tgt_props.get("description", ""),
                "relation": relation_type,
                "relation_id": relation_id,
                "relation_desc": relation_desc,
                "score": entity_score,
            })
        return dicts

    def query_node(self, entity_name, threshold=0.78, kgdb_name='neo4j', hops=2, max_entities=5, max_relations=100, **kwargs):
        """知识图谱查询节点的入口:"""
        # Clamp max_relations to [1, 100]
        try:
            max_relations = int(max_relations)
        except (TypeError, ValueError):
            max_relations = 100
        max_relations = max(1, min(max_relations, 100))
        # 判断是否启动
        if not self.is_running():
            raise Exception("图数据库未启动")

        self.use_database(kgdb_name)
        def _index_exists(tx, index_name):
            """检查索引是否存在"""
            result = tx.run("SHOW INDEXES")
            for record in result:
                if record["name"] == index_name:
                    return True
            return False

        def query(tx, text):
            # 首先检查索引是否存在
            if not _index_exists(tx, "entityEmbeddings"):
                raise Exception("向量索引不存在，请先创建索引")

            embedding = self.get_embedding(text)
            result = tx.run("""
            CALL db.index.vector.queryNodes('entityEmbeddings', 10, $embedding)
            YIELD node AS similarEntity, score
            RETURN similarEntity.name AS name, score
            """, embedding=embedding)
            return result.values()

        try:
            with self.driver.session() as session:
                # query是函数，后面紧跟参数，这里查询的是向量不是实体名称
                results = session.execute_read(query, entity_name)
        except Exception as e:
            if "向量索引不存在" in str(e):
                logger.error(f"向量索引不存在，请先创建索引: {e}, {traceback.format_exc()}")
                return []
            raise e

        # 筛选出分数高于阈值的实体，保留分数用于下游引用
        qualified_entities = [
            (result[0], result[1])
            for result in results[:max_entities]
            if result[1] > threshold
        ]
        logger.debug(
            f"Graph Query Entities: {entity_name}, "
            f"{[e[0] for e in qualified_entities]=}"
        )

        # 对每个合格的实体进行查询，并转换为结构化行
        structured_rows = []
        remaining = max_relations
        for entity, score in qualified_entities:
            if remaining <= 0:
                break
            legacy_rows = self.query_specific_entity(
                entity_name=entity, hops=hops, kgdb_name=kgdb_name,
                limit=remaining,
            )
            for row in legacy_rows:
                if remaining <= 0:
                    break
                if isinstance(row, dict):
                    # Already structured (defensive) — copy to avoid
                    # mutating caller-owned data when filling score.
                    if "score" not in row:
                        row = {**row, "score": score}
                    structured_rows.append(row)
                    remaining -= 1
                elif isinstance(row, (list, tuple)) and len(row) >= 3 and isinstance(row[1], list):
                    converted = self._legacy_row_to_structured(row, score)
                    for r in converted:
                        if remaining <= 0:
                            break
                        structured_rows.append(r)
                        remaining -= 1

        return structured_rows

    def query_specific_entity(self, entity_name, kgdb_name='neo4j', hops=2, limit=100):
        """查询指定实体三元组信息（无向关系）"""
        if not entity_name:
            logger.warning("实体名称为空")
            return []

        self.use_database(kgdb_name)

        def query(tx, entity_name, hops, limit):
            try:
                # hops查询深度
                query_str = f"""
                MATCH (n {{name: $entity_name}})-[r*1..{hops}]-(m)
                RETURN n AS n, r, m AS m
                LIMIT $limit
                """
                result = tx.run(query_str, entity_name=entity_name, limit=limit)

                if not result:
                    logger.info(f"未找到实体 {entity_name} 的相关信息")
                    return []

                return result.values()

            except Exception as e:
                logger.error(f"查询实体 {entity_name} 失败: {str(e)}")
                return []

        try:
            with self.driver.session() as session:
                return session.execute_read(query, entity_name, hops, limit)
        except Exception as e:
            logger.error(f"数据库会话异常: {str(e)}")
            return []

    def query_all_nodes_and_relationships(self, kgdb_name='neo4j', hops = 2):
        """查询图数据库中所有三元组信息 NEVER USE"""
        self.use_database(kgdb_name)
        def query(tx, hops):
            result = tx.run(f"""
            MATCH (n)-[r*1..{hops}]->(m)
            RETURN n AS n, r, m AS m
            """)
            values = result.values()
            values = clean_triples_embedding(values)
            return values

        with self.driver.session() as session:
            return session.execute_read(query, hops)

    def query_by_relationship_type(self, relationship_type, kgdb_name='neo4j', hops = 2):
        """查询指定关系三元组信息 NEVER USE"""
        self.use_database(kgdb_name)
        def query(tx, relationship_type, hops):
            result = tx.run(f"""
            MATCH (n)-[r:`{relationship_type}`*1..{hops}]->(m)
            RETURN n AS n, r, m AS m
            """)
            values = result.values()
            values = clean_triples_embedding(values)
            return values

        with self.driver.session() as session:
            return session.execute_read(query, relationship_type, hops)

    def query_entity_like(self, keyword, kgdb_name='neo4j', hops = 2):
        """模糊查询 NEVER USE"""
        self.use_database(kgdb_name)
        def query(tx, keyword, hops):
            result = tx.run(f"""
            MATCH (n:Entity)
            WHERE n.name CONTAINS $keyword
            MATCH (n)-[r*1..{hops}]->(m)
            RETURN n AS n, r, m AS m
            """, keyword=keyword)
            values = result.values()
            values = clean_triples_embedding(values)
            return values

        with self.driver.session() as session:
            return session.execute_read(query, keyword, hops)

    def query_node_info(self, node_name, kgdb_name='neo4j', hops = 2):
        """查询指定节点的详细信息返回信息 NEVER USE"""
        self.use_database(kgdb_name)  # 切换到指定数据库
        def query(tx, node_name, hops):
            result = tx.run(f"""
            MATCH (n {{name: $node_name}})
            OPTIONAL MATCH (n)-[r*1..{hops}]->(m)
            RETURN n AS n, r, m AS m
            """, node_name=node_name)
            values = result.values()
            values = clean_triples_embedding(values)
            return values

        with self.driver.session() as session:
            return session.execute_read(query, node_name, hops)

    async def aget_embedding(self, text):
        from src import knowledge_base

        if isinstance(text, list):
            outputs = await knowledge_base.embed_model.abatch_encode(text, batch_size=40)
            return outputs
        else:
            outputs = await knowledge_base.embed_model.aencode(text)
            return outputs

    def get_embedding(self, text):
        from src import knowledge_base

        if isinstance(text, list):
            outputs = knowledge_base.embed_model.batch_encode(text, batch_size=40)
            return outputs
        else:
            outputs = knowledge_base.embed_model.encode([text])[0]
            return outputs

    def set_embedding(self, tx, entity_name, embedding, namespace=None):
        if namespace is None:
            tx.run("""
            MATCH (e:Entity {name: $name})
            CALL db.create.setNodeVectorProperty(e, 'embedding', $embedding)
            """, name=entity_name, embedding=embedding)
        else:
            tx.run("""
            MATCH (e:Entity {name: $name, kgdb_name: $namespace})
            CALL db.create.setNodeVectorProperty(e, 'embedding', $embedding)
            """, name=entity_name, embedding=embedding, namespace=namespace)

    def get_namespace_counts(self, kgdb_name):
        """Return node and relationship counts for a namespace via parameterized Cypher."""
        def _read_counts(tx, namespace):
            node_count = int(
                tx.run(
                    "MATCH (n:Entity {kgdb_name: $namespace}) RETURN count(n) AS cnt",
                    namespace=namespace,
                ).single()["cnt"]
            )
            relationship_count = int(
                tx.run(
                    "MATCH ()-[r:RELATION {kgdb_name: $namespace}]->() RETURN count(r) AS cnt",
                    namespace=namespace,
                ).single()["cnt"]
            )
            return {"node_count": node_count, "relationship_count": relationship_count}

        with self.driver.session() as session:
            return session.execute_read(_read_counts, kgdb_name)

    def get_graph_info(self, graph_name="neo4j"):
        self.use_database(graph_name)
        def query(tx):
            entity_count = tx.run("MATCH (n) RETURN count(n) AS count").single()["count"]
            relationship_count = tx.run("MATCH ()-[r]->() RETURN count(r) AS count").single()["count"]
            triples_count = tx.run("MATCH (n)-[r]->(m) RETURN count(n) AS count").single()["count"]

            # 获取所有标签
            labels = tx.run("CALL db.labels() YIELD label RETURN collect(label) AS labels").single()["labels"]

            return {
                "graph_name": graph_name,
                "entity_count": entity_count,
                "relationship_count": relationship_count,
                "triples_count": triples_count,
                "labels": labels,
                "status": self.status,
                "embed_model_name": self.embed_model_name,
                "unindexed_node_count": self.query_nodes_without_embedding(graph_name)
            }

        try:
            if self.status == "open" and self.driver and self.is_running():
                # 获取数据库信息
                with self.driver.session() as session:
                    graph_info = session.execute_read(query)

                    # 添加时间戳
                    from datetime import datetime
                    graph_info["last_updated"] = datetime.now().isoformat()
                    return graph_info

        except Exception as e:
            logger.error(f"获取图数据库信息失败：{e}, {traceback.format_exc()}")
            return None

    def save_graph_info(self, graph_name="neo4j"):
        """
        将图数据库的基本信息保存到工作目录中的JSON文件
        保存的信息包括：数据库名称、状态、嵌入模型名称等
        """
        try:
            graph_info = self.get_graph_info(graph_name)
            if graph_info is None:
                logger.error("图数据库信息为空，无法保存")
                return False

            info_file_path = os.path.join(self.work_dir, "graph_info.json")
            with open(info_file_path, 'w', encoding='utf-8') as f:
                json.dump(graph_info, f, ensure_ascii=False, indent=2)

            # logger.info(f"图数据库信息已保存到：{info_file_path}")
            return True
        except Exception as e:
            logger.error(f"保存图数据库信息失败：{e}")
            return False

    def query_nodes_without_embedding(self, kgdb_name='neo4j'):
        """查询没有嵌入向量的节点

        Returns:
            list: 没有嵌入向量的节点列表
        """
        self.use_database(kgdb_name)

        def query(tx):
            result = tx.run("""
            MATCH (n:Entity)
            WHERE n.embedding IS NULL
            RETURN n.name AS name
            """)
            return [record["name"] for record in result]

        with self.driver.session() as session:
            return session.execute_read(query)

    def load_graph_info(self):
        """
        从工作目录中的JSON文件加载图数据库的基本信息
        返回True表示加载成功，False表示加载失败
        """
        try:
            info_file_path = os.path.join(self.work_dir, "graph_info.json")
            if not os.path.exists(info_file_path):
                logger.debug(f"图数据库信息文件不存在：{info_file_path}")
                return False

            with open(info_file_path, encoding='utf-8') as f:
                graph_info = json.load(f)

            # 更新对象属性
            if graph_info.get("embed_model_name"):
                self.embed_model_name = graph_info["embed_model_name"]

            # 如果需要，可以加载更多信息
            # 注意：这里不更新self.kgdb_name，因为它是在初始化时设置的

            logger.info(f"已加载图数据库信息，最后更新时间：{graph_info.get('last_updated')}")
            return True
        except Exception as e:
            logger.error(f"加载图数据库信息失败：{e}")
            return False

    def add_embedding_to_nodes(self, node_names=None, kgdb_name='neo4j', namespace=None):
        """为节点添加嵌入向量

        Args:
            node_names (list, optional): 要添加嵌入向量的节点名称列表，None表示所有没有嵌入向量的节点
            kgdb_name (str, optional): 图数据库名称，默认为'neo4j'
            namespace (str, optional): 命名空间，用于过滤节点

        Returns:
            int: 成功添加嵌入向量的节点数量
        """
        self.use_database(self.kgdb_name if namespace is not None else kgdb_name)

        def _read_names(tx):
            """查询没有嵌入向量的节点名称"""
            query = "MATCH (n:Entity) WHERE n.embedding IS NULL"
            params = {}
            if namespace is not None:
                query += " AND n.kgdb_name = $namespace"
                params["namespace"] = namespace
            if node_names is not None:
                query += " AND n.name IN $node_names"
                params["node_names"] = node_names
            query += " RETURN n.name AS name"
            result = tx.run(query, **params)
            return [record["name"] for record in result]

        count = 0
        with self.driver.session() as session:
            names = session.execute_read(_read_names)
            names = sorted(set(names))
            for name in names:
                try:
                    embedding = self.get_embedding(name)
                    session.execute_write(self.set_embedding, name, embedding, namespace)
                    count += 1
                except Exception as e:
                    logger.error(f"为节点 '{name}' 添加嵌入向量失败: {e}, {traceback.format_exc()}")

        return count

    def ensure_entity_vector_index(self, dimension=None):
        if dimension is None:
            dimension = config.embed_model_names[config.embed_model]['dimension']
        dimension = int(dimension)
        if dimension < 1 or dimension > 4096:
            raise ValueError("dimension must be between 1 and 4096")

        index_name = "entityEmbeddings"

        with self.driver.session() as session:
            def _check_index(tx):
                result = tx.run(
                    "SHOW INDEXES YIELD name, state WHERE name = $index_name RETURN name, state",
                    index_name=index_name,
                )
                for record in result:
                    return record
                return None

            record = session.execute_read(_check_index)

            if record is not None and record['state'] == 'ONLINE':
                return True

            if record is None:
                def _create_index(tx):
                    tx.run(
                        "CREATE VECTOR INDEX entityEmbeddings IF NOT EXISTS "
                        "FOR (n:Entity) ON (n.embedding) "
                        "OPTIONS {indexConfig: {"
                        "`vector.dimensions`: $dimension, "
                        "`vector.similarity_function`: 'cosine'"
                        "}}",
                        dimension=dimension,
                    )
                session.execute_write(_create_index)

            def _await_index(tx):
                result = tx.run("CALL db.awaitIndex('entityEmbeddings', 300)")
                try:
                    return result.single()
                except Exception:
                    return None

            session.execute_read(_await_index)
            return True

    def _extract_relationship_info(self, relationship, source_name=None, target_name=None, node_dict=None):
        """
        提取关系信息并返回格式化的节点和边信息
        """
        rel_id = relationship.element_id
        nodes = relationship.nodes
        if len(nodes) != 2:
            return None, None

        source, target = nodes
        source_id = source.element_id
        target_id = target.element_id

        source_name = node_dict[source_id]["name"] if source_name is None else source_name
        target_name = node_dict[target_id]["name"] if target_name is None else target_name

        relationship_type = relationship._properties.get("type", "unknown")
        if relationship_type == "unknown":
            relationship_type = relationship.type

        edge_info = {
            "id": rel_id,
            "type": relationship_type,
            "source_id": source_id,
            "target_id": target_id,
            "source_name": source_name,
            "target_name": target_name,
        }

        node_info = [
            {"id": source_id, "name": source_name},
            {"id": target_id, "name": target_name},
        ]

        return node_info, edge_info

    def format_general_results(self, results):
        formatted_results = {"nodes": [], "edges": []}

        for item in results:
            relationship = item[1]
            source_name = item[0]._properties.get("name", "unknown")
            target_name = item[2]._properties.get("name", "unknown") if len(item) > 2 else "unknown"

            node_info, edge_info = self._extract_relationship_info(relationship, source_name, target_name)
            if node_info is None or edge_info is None:
                continue

            for node in node_info:
                if node["id"] not in [n["id"] for n in formatted_results["nodes"]]:
                    formatted_results["nodes"].append(node)

            formatted_results["edges"].append(edge_info)

        return formatted_results

    def format_query_result_to_graph(self, query_results):
        """Convert query results into {"nodes": [], "edges": []} format.

        Accepts two input shapes:

        *Legacy Neo4j rows* – ``[source_node, [relationship, …], target_node]``
          where nodes have ``element_id`` and ``_properties``.

        *Structured relation dicts* – dictionaries produced by
          ``rank_unique_relations`` with keys ``source``, ``target``,
          ``relation``, ``score``, ``ref_id``, and optional
          ``source_id``/``target_id``/``source_properties``/``target_properties``.
        """
        formatted_results = {"nodes": [], "edges": []}
        node_dict = {}
        edge_dict = {}

        for item in query_results:
            if isinstance(item, dict):
                # Structured relation dict
                src_name = item.get("source", "")
                tgt_name = item.get("target", "")
                src_id = item.get("source_id") or f"node:{src_name}"
                tgt_id = item.get("target_id") or f"node:{tgt_name}"

                src_props = item.get("source_properties")
                tgt_props = item.get("target_properties")
                if isinstance(src_props, dict):
                    src_name = src_props.get("name", src_name)
                if isinstance(tgt_props, dict):
                    tgt_name = tgt_props.get("name", tgt_name)

                node_dict[src_id] = {"id": src_id, "name": src_name}
                node_dict[tgt_id] = {"id": tgt_id, "name": tgt_name}

                relation = item.get("relation", "unknown")
                # Prefer relation_id for a stable, deterministic edge ID;
                # fall back to a composite of source, relation, and target.
                relation_id = item.get("relation_id")
                edge_id = relation_id if relation_id else f"{src_id}:{relation}:{tgt_id}"
                edge_info = {
                    "id": edge_id,
                    "type": relation,
                    "source_id": src_id,
                    "target_id": tgt_id,
                    "source_name": src_name,
                    "target_name": tgt_name,
                    "ref_id": item.get("ref_id", ""),
                    "score": item.get("score", 0.0),
                }
                # Carry through descriptions and raw properties for sidebar use.
                for desc_key in ("source_desc", "target_desc", "relation_desc"):
                    if desc_key in item:
                        edge_info[desc_key] = item[desc_key]
                if src_props:
                    edge_info["source_properties"] = src_props
                if tgt_props:
                    edge_info["target_properties"] = tgt_props
                edge_dict[edge_id] = edge_info
            elif isinstance(item, (list, tuple)) and len(item) >= 3:
                # Legacy Neo4j format: [source_node, [rel, …], target_node]
                if not isinstance(item[1], list):
                    continue

                node_dict[item[0].element_id] = dict(
                    id=item[0].element_id,
                    name=item[0]._properties.get("name", "Unknown"),
                )
                node_dict[item[2].element_id] = dict(
                    id=item[2].element_id,
                    name=item[2]._properties.get("name", "Unknown"),
                )

                for relationship in item[1]:
                    try:
                        node_info, edge_info = self._extract_relationship_info(
                            relationship, node_dict=node_dict
                        )
                        if node_info is None or edge_info is None:
                            continue
                        edge_dict[edge_info["id"]] = edge_info
                    except Exception as e:
                        logger.error(
                            f"处理关系时出错: {e}, "
                            f"关系: {relationship}, {traceback.format_exc()}"
                        )
                        continue

        formatted_results["nodes"] = list(node_dict.values())
        formatted_results["edges"] = list(edge_dict.values())
        return formatted_results

def clean_triples_embedding(triples):
    for item in triples:
        if hasattr(item[0], '_properties'):
            item[0]._properties['embedding'] = None
        if hasattr(item[2], '_properties'):
            item[2]._properties['embedding'] = None
    return triples


if __name__ == "__main__":
    pass
