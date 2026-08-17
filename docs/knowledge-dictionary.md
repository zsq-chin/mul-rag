# 知识字典功能实现说明

设计文档：[docs/superpowers/specs/2026-08-16-knowledge-dictionary-generation-design.md](superpowers/specs/2026-08-16-knowledge-dictionary-generation-design.md)

## 代码结构

```
server/models/knowledge_dictionary_models.py      # 6 张表（字典/版本/来源/条目/证据/任务）
server/schemas/knowledge_dictionary.py            # Pydantic 请求模型（§13）
server/services/knowledge_dictionary/
    errors.py          # 统一业务错误（§14.1：error_code/message/trace_id/details）
    permissions.py     # 角色权限（§3：admin==superadmin，user 只读）
    repository.py      # 关系数据读写与事务边界（§6）
    service.py         # 版本/审核/发布/撤回业务不变量（§11）
    source_adapters.py # 三种来源 -> 节点流 + 快照/变更检测/上传校验（§7）
    extractor.py       # 候选抽取 + 证据校验（§8.1/§8.2）
    normalizer.py      # 名称/单位/类型/同义词标准化 + 去重键 + 置信度（§8.3/§8.4）
    jobs.py            # 持久化任务：租约/心跳/检查点/取消/重试/生成流水线（§12）
    vector_indexer.py  # 独立 Milvus 集合 + 增量索引 + 一致性校验 + 检索（§10）
    seed_import.py     # XinJiang 种子幂等迁移（§9）
    export_service.py  # XLSX/CSV/JSON 导出 + CSV 公式注入防护（§13.5）
server/routers/knowledge_dictionary_router.py     # API 路由（前缀 /api/knowledge-dictionaries）
server/dictionary_worker.py                       # worker 进程入口（租约领取任务）
web/src/apis/knowledgeDictionary.js               # 前端 API 客户端
web/src/views/KnowledgeDictionaryView.vue         # 字典列表 + 生成向导 + 任务面板
web/src/components/KnowledgeDictionaryDetail.vue  # 版本/审核工作台/只读检索
```

## 关键设计决策

- **关系数据库是唯一事实来源**：字典/版本/审核状态/证据全部在 SQLite（`db_manager`），Milvus 只是可重建的派生索引（集合 `knowledge_dictionary_entries_v1`）。
- **API 进程不跑长任务**：`POST /generate` 只创建任务（202 + job_id）；`dictionary-worker`（docker-compose 同名服务，同代码镜像）通过任务租约领取 `generate/index/import_seed` 任务；租约过期自动标记 `interrupted`，可从检查点重试。
- **生成流水线**：来源快照冻结 → 节点批次 → 模型结构化抽取（JSON Schema 校验 + 一次结构修复 + 有限重试）→ 证据引文归一化匹配（无证据直接丢弃）→ 名称/单位/类型标准化 → 同一版本精确去重合并（幂等，证据按哈希去重）→ 单位/类型冲突进入 `conflict`。
- **发布门禁（§11.2）**：pending/conflict 未处理、通过条目缺必填字段或证据、来源快照变化、索引非 ready、条目数与向量数不一致、embedding 配置不一致、存在活动/失败任务——任一条件不满足即 409 `DICTIONARY_PUBLISH_BLOCKED`。
- **检索授权**：Milvus 过滤只做粗筛；召回后回查关系数据库再次校验版本/条目状态与用户权限（§10.4：Milvus 元数据不能代替最终授权）。
- **种子迁移幂等**：`seed_meta.importer_version + source_hash` 相同即跳过；源文件变化创建新草稿版本。

## 环境变量

见 `.env.example` 中「Knowledge dictionary」一节：`DICTIONARY_VECTOR_ENABLED`、`DICTIONARY_GENERATE_CONCURRENCY`、`DICTIONARY_INDEX_CONCURRENCY`、`DICTIONARY_LEASE_TTL_SECONDS`、`DICTIONARY_WORKER_POLL_SECONDS`、`DICTIONARY_UPLOAD_ROOT`。

## 测试

- `test/test_knowledge_dictionary_core.py`：标准化/权限/字典生命周期/审核/合并/发布门禁/导出（24 项）
- `test/test_knowledge_dictionary_pipeline.py`：抽取/证据/任务租约/生成端到端/种子迁移（21 项）
- `test/test_knowledge_dictionary_vector.py`：假 Milvus 客户端下的索引/一致性/检索权限/发布（8 项）
- `test/test_knowledge_dictionary_router_api.py`：真实 TestClient 走 HTTP 路由（11 项）

```powershell
python -m unittest test.test_knowledge_dictionary_core test.test_knowledge_dictionary_pipeline test.test_knowledge_dictionary_vector test.test_knowledge_dictionary_router_api -v
```

## 本地运行 worker

```powershell
uv run python server/dictionary_worker.py
```
