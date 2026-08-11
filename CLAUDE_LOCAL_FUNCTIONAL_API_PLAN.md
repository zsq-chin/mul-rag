# Claude 本机功能性接口完善工作计划

> 执行对象：Claude Code
>
> 工作目录：`D:\shanhai\sage-master\sage-master (2)\sage-master`
>
> 需求来源：`经费预算内容55万6.12.xlsx` 中“表A.1 软件测试功能点估算”第 6-42 行

## 1. 任务目标

在当前 Sage 项目中，完成可以完全在本机实现和验收的功能性接口及配套页面。优先补齐回答反馈、知识治理元数据、文档下载控制、问答测试集、操作审计、配置历史、备份恢复、系统监控和邮件告警。

本轮不修改远程多模态知识库，不调整远程接口协议，不修改文档解析、数据清洗、知识抽取、向量索引和检索算法。

所有新增功能继续使用当前项目的技术栈：FastAPI、SQLAlchemy、SQLite、Vue 3、Pinia、Ant Design Vue、Node Test Runner 和 Python unittest。

## 2. 强制边界

### 2.1 禁止修改的远程多模态代码

以下文件和目录本轮必须保持零差异：

```text
mul_rag/**
server/utils/multimodal_remote.py
server/routers/multimodal_proxy_router.py
server/services/http_clients.py
web/src/apis/multimodal.js
web/src/views/MultimodalKbView.vue
web/src/components/AuthenticatedImage.vue
web/src/utils/multimodalSearch.mjs
docker-compose.yml 中 MULTIMODAL_* 配置
docker-compose.prod.yml 中 MULTIMODAL_* 配置
.env 和 .env.example 中 MULTIMODAL_* 配置
```

禁止向 `10.16.33.2:8002` 发起开发测试请求。远程服务是否在线不得影响本轮测试。

每个阶段提交前执行：

```powershell
git diff -- mul_rag server/utils/multimodal_remote.py server/routers/multimodal_proxy_router.py server/services/http_clients.py web/src/apis/multimodal.js web/src/views/MultimodalKbView.vue web/src/components/AuthenticatedImage.vue web/src/utils/multimodalSearch.mjs
```

预期：无输出。

### 2.2 禁止修改的数据处理核心

以下模块只允许调用已有公开方法，不允许修改实现：

```text
src/core/indexing.py
src/core/knowledgebase.py
src/core/retriever.py
src/core/graph_retrieval.py
indexing/**
indexing_drill/**
graphrag_api/**
tianshu/**
```

不得新增或修改以下算法：分块、OCR、清洗、标注、知识抽取、知识融合、Embedding、Milvus 索引、重排、查询扩展、GraphRAG 和多模态检索。

### 2.3 安全约束

- 普通用户只能提交和查看自己的回答反馈。
- `admin` 可以管理普通用户，但不能访问系统运维、知识治理和全局统计。
- `superadmin` 才能管理知识治理、测试集、审计、备份、配置、监控和告警。
- API 不得返回文件绝对路径、密码、JWT 密钥、模型 API Key、SMTP 密码或主密钥。
- 日志、审计详情和异常信息不得包含上述秘密。
- 文件下载、备份下载和恢复必须防止 `..`、绝对路径、软链接逃逸和 Zip Slip。
- 不在数据库中明文保存新的秘密；SMTP 密码只从环境变量读取。

## 3. 表格功能点范围判定

### 3.1 本轮直接补齐

| 表格编号 | 功能项 | 本轮交付 |
|---|---|---|
| 8 | 知识版本管理 | 记录本机知识文档元数据和源文件校验值的版本历史；支持版本列表、详情和版本源文件下载，不触发重建索引 |
| 11 | 知识下载控制 | 按角色、保密等级和下载开关控制原文下载，使用流式响应并记录审计日志 |
| 16 | 知识分级分类管理 | 管理专业领域、知识类型、保密等级、标签和下载策略 |
| 19 | 知识导入导出 | 本轮只补齐元数据 JSON/XLSX 导出；知识内容批量导入沿用现有上传接口，不新增解析逻辑 |
| 25 | 问答反馈机制 | 点赞/点踩真实入库、可修改、可取消、可统计 |
| 29 | 问答测试集管理 | 测试集和测试用例 CRUD、JSON/CSV 导入导出；不执行模型自动评测 |
| 34 | 操作日志审计 | 统一记录登录、用户、模型、知识、配置、下载、备份和告警关键操作，并提供筛选分页接口 |
| 35 | 数据备份与恢复 | 备份本机 SQLite、非秘密配置、日志和可选源文档；提供校验、预检、恢复和下载 |
| 36 | 系统监控 | 检查 API、SQLite、Milvus、Neo4j、磁盘、GPU 和本机依赖状态；不检查远程多模态服务 |
| 37 | 告警通知模块 | 本机告警规则、告警事件、SMTP 邮件通知和手动测试邮件 |

### 3.2 已有能力，只做验收和必要加固

| 表格编号 | 功能项 | 当前状态 | 本轮动作 |
|---|---|---|---|
| 6 | 文档上传 | `POST /api/data/upload` 已存在 | 增加审计记录、文件大小和扩展名校验，不改变后续解析流程 |
| 9 | 知识检索接口 | 普通知识库检索和问答检索已存在 | 只做权限和返回结构验收，不修改检索算法 |
| 10 | 知识预览 | `GET /api/data/document` 和右侧引用栏已存在 | 防止绝对路径泄露，补齐治理元数据展示 |
| 21 | 大模型问答生成 | 已存在流式问答 | 只验证错误处理和权限 |
| 22 | 幻觉控制 | 已有检索上下文和引用 | 增加“无有效证据时明确说明证据不足”的回归测试，不改远程检索 |
| 23 | 问答交互界面 | 已存在 | 只接入反馈状态和错误提示 |
| 24 | 搜索结果展示 | `RefsSidebar.vue` 已存在 | 只补齐本机知识治理元数据，不改多模态引用展示 |
| 26 | 问答日志 | `ChatRecord` 和线程记录已存在 | 统一关联 `conversation_id`、`message_id` 和反馈，不重复保存完整回答 |
| 27 | 问答效果分析 | `statistics_router.py` 和 `AnswerStatistics.vue` 已存在 | 增加满意度、点赞率、点踩原因分布和有反馈回答数 |
| 28 | 模型版本管理 | 用户自定义模型、安全保存和切换已存在 | 只做回归测试，不修改密钥存储方式 |
| 30 | 用户权限管理 | `superadmin/admin/user` 角色控制已存在 | 为新增接口补齐角色矩阵测试 |
| 32 | 私有化部署适配 | Docker Compose 已存在 | 新增本机功能所需非秘密环境变量示例，不改远程配置 |
| 33 | 系统配置管理 | `/api/config` 已存在 | 增加字段白名单、秘密脱敏、配置历史和回滚；保持旧接口兼容 |

### 3.3 本轮明确延期

以下功能依赖数据处理、索引或远程知识库，本轮不实现：

```text
1  多源数据采集
2  数据清洗
3  数据标注
4  数据分类算法
5  数据整合
7  文档解析
12 多源数据质量监控
13 知识抽取
14 知识融合
15 图数据库和向量数据库混合存储改造
17 知识内容自动更新调度
18 知识准确率和覆盖率自动评估
20 向量检索引擎改造
31 知识库索引整体版本发布与真实回滚
```

编号 31 只能在未来索引层支持版本隔离后实现。本轮不得用“切换一个数据库字段”冒充真实回滚。

## 4. 推荐架构

采用“现有单体内增加隔离模块”的方案：

```text
Vue 页面/组件
  -> 本机功能 API
  -> 独立 FastAPI Router
  -> 独立 Service
  -> SQLAlchemy/SQLite + 本机受控目录
```

不把新功能继续堆进 `chat_router.py`、`data_router.py` 或远程代理。仅在现有上传、模型和用户管理端点中加入一行审计调用；业务逻辑全部放入新 Service。

未采用的方案：

1. 直接扩展现有大 Router：开发快，但会继续扩大耦合，容易误改问答和数据处理。
2. 新建独立运维微服务：隔离最好，但当前体量下会引入重复鉴权、部署和数据库同步，过度设计。

## 5. 文件结构

建议创建：

```text
server/models/feedback_model.py
server/models/governance_model.py
server/models/evaluation_model.py
server/models/operations_model.py

server/schemas/feedback.py
server/schemas/governance.py
server/schemas/evaluation.py
server/schemas/operations.py

server/services/feedback_service.py
server/services/audit_service.py
server/services/governance_service.py
server/services/evaluation_service.py
server/services/backup_service.py
server/services/monitoring_service.py
server/services/alert_service.py
server/services/config_history_service.py

server/routers/feedback_router.py
server/routers/governance_router.py
server/routers/evaluation_router.py
server/routers/audit_router.py
server/routers/operations_router.py

web/src/apis/local_features.js
web/src/views/OperationsView.vue
web/src/views/EvaluationView.vue
web/src/components/KnowledgeGovernancePanel.vue

web/tests/feedback.test.mjs
web/tests/localFeaturesAccess.test.mjs
web/tests/operationsView.test.mjs

test/test_feedback_api.py
test/test_governance_api.py
test/test_evaluation_api.py
test/test_audit_api.py
test/test_backup_service.py
test/test_monitoring_service.py
test/test_alert_service.py
test/test_config_history.py
test/test_local_feature_access.py
```

允许修改：

```text
server/models/__init__.py
server/db_manager.py
server/routers/__init__.py
server/main.py
server/routers/auth_router.py
server/routers/user_model_router.py
server/routers/data_router.py（仅输入校验和审计钩子）
server/routers/statistics_router.py
server/services/statistics_aggregation.py
server/routers/base_router.py
web/src/components/RefsComponent.vue
web/src/components/RefsSidebar.vue
web/src/views/AnswerStatistics.vue
web/src/views/DataBaseInfoView.vue
web/src/views/SettingView.vue
web/src/router/index.js
web/src/utils/access.mjs
web/src/layouts/AppLayout.vue
.env.example（只添加本机 SMTP/备份变量）
```

## 6. 统一接口约定

### 6.1 成功响应

```json
{
  "status": "success",
  "data": {},
  "message": ""
}
```

列表接口：

```json
{
  "status": "success",
  "data": {
    "items": [],
    "page": 1,
    "page_size": 20,
    "total": 0
  }
}
```

### 6.2 错误响应

继续使用 FastAPI 标准错误：

```json
{
  "detail": "用户可理解的错误说明"
}
```

规则：400 参数错误，401 未登录，403 无权限，404 不存在，409 状态冲突，413 文件过大，422 请求校验错误，500 内部错误，503 依赖不可用。

### 6.3 分页和时间

- `page` 从 1 开始，默认 1。
- `page_size` 默认 20，最大 100。
- 时间统一返回带时区的 ISO 8601 字符串。
- 数据库时间存 UTC，前端按本地时区展示。

## 7. 分阶段实施任务

### 阶段 0：建立基线和防误改检查

- [ ] 新建分支 `feature/local-functional-apis`。
- [ ] 记录 `git status --short`，必须先确认工作区是否干净；不得覆盖已有用户修改。
- [ ] 运行当前后端和前端测试，保存失败基线。
- [ ] 记录禁止修改文件的 Git blob ID，结束时重新比对。
- [ ] 新增 `test/test_local_scope_guard.py`，检查本机功能路由不能导入 `multimodal_remote` 或 `mul_rag`。

基线命令：

```powershell
docker compose config --quiet
docker exec api-dev uv run python -m unittest discover -s test -p "test_*.py"
docker exec web-dev pnpm test
docker exec web-dev pnpm build
```

验收：能区分既有失败与新增失败；禁止修改路径无差异。

### 阶段 1：数据库模型注册和通用审计服务

- [ ] 创建新模型文件并在 `server/db_manager.py` 显式导入，保证 `Base.metadata.create_all()` 能建表。
- [ ] 不修改现有表字段，所有结构变化使用新表，避免 SQLite 自动建表无法迁移旧列的问题。
- [ ] 创建 `AuditService.record()`，只接受结构化动作码和已脱敏详情。
- [ ] 审计写入失败不得导致原业务失败，但必须写警告日志，警告中不能包含秘密。

新增表至少包括：

```text
answer_feedback
knowledge_governance
knowledge_document_versions
evaluation_suites
evaluation_cases
config_change_history
backup_jobs
alert_rules
alert_events
```

`OperationLog` 继续复用，不重复创建第二张审计表。

验收：临时 SQLite 数据库可以创建全部表；重复初始化幂等；外键开启后删除用户不会留下非法反馈记录。

### 阶段 2：回答点赞/点踩真实接口

实现接口：

```text
PUT    /api/feedback/messages/{message_id}
GET    /api/feedback/messages/{message_id}
DELETE /api/feedback/messages/{message_id}
GET    /api/feedback/mine
GET    /api/feedback/summary                 superadmin
```

写入请求：

```json
{
  "conversation_id": "会话ID",
  "rating": "up",
  "reason": "证据充分",
  "comment": "可选补充说明"
}
```

约束：

- `(user_id, message_id)` 唯一，重复点击使用 upsert，不产生重复记录。
- `rating` 只能是 `up` 或 `down`。
- 普通用户不能伪造其他用户反馈。
- 服务端根据当前用户和聊天记录校验 `message_id` 所有权。
- 不在反馈表重复保存完整回答，只保存消息标识、评价和必要快照摘要。

前端修改 `RefsComponent.vue`：

- 点赞和点踩显示选中态。
- 再次点击已选按钮表示取消。
- 点踩后弹出可选原因，不阻塞提交。
- 请求失败回滚界面状态。
- 不允许对仍在生成的回答评价。

验收测试：创建、覆盖、取消、越权、非法 rating、并发 upsert、刷新后状态恢复全部通过。

### 阶段 3：反馈接入问答统计

- [ ] 在现有 `/api/statistics/overview` 增加 `feedback` 区域，不删除旧字段。
- [ ] 增加总反馈数、点赞数、点踩数、满意度、反馈覆盖率和点踩原因 Top 10。
- [ ] `AnswerStatistics.vue` 增加紧凑的反馈指标和原因分布，不改变现有问答趋势、热门问题和社区模块。
- [ ] 当没有反馈时显示 0 和空状态，不使用演示数据。

建议返回：

```json
{
  "feedback": {
    "total": 0,
    "up": 0,
    "down": 0,
    "satisfaction_rate": 0.0,
    "coverage_rate": 0.0,
    "down_reasons": []
  }
}
```

验收：统计严格来自真实反馈表；删除反馈后统计同步变化；旧前端字段保持兼容。

### 阶段 4：知识分级分类、预览和下载控制

实现接口：

```text
GET   /api/governance/databases/{db_id}/documents/{file_id}
PATCH /api/governance/databases/{db_id}/documents/{file_id}       superadmin
GET   /api/governance/databases/{db_id}/documents
GET   /api/governance/databases/{db_id}/documents/{file_id}/download
GET   /api/governance/databases/{db_id}/export?format=json|xlsx   superadmin
POST  /api/governance/databases/{db_id}/sync                       superadmin
```

治理字段：

```text
domain             专业领域
knowledge_type     报告/论文/设计图/日志/标准/其他
confidentiality    public/internal/restricted
tags               JSON 字符串数组
download_allowed   是否允许下载
owner_department   责任部门
source_updated_at  来源更新时间
usage_count        使用次数，只能由服务端更新
```

规则：

- `sync` 只读取现有知识库和文件信息，为缺失记录补建治理元数据，不解析和重建索引。
- 预览接口不得返回 `KnowledgeFile.path` 或其他绝对路径。
- `restricted` 仅 `superadmin` 可下载。
- `internal` 允许已登录用户预览，只有 `download_allowed=true` 时允许下载。
- 下载使用 `StreamingResponse`，Content-Disposition 文件名必须安全编码。
- 所有下载成功和拒绝事件都写审计日志。
- XLSX 导出只导出元数据，不导出文档正文、向量和秘密。

前端在 `DataBaseInfoView.vue` 中嵌入 `KnowledgeGovernancePanel.vue`，不要新建卡片套卡片。表格支持筛选、分页、编辑治理字段、预览和受控下载。

验收：路径遍历、软链接逃逸、伪造 `db_id/file_id`、越权下载、绝对路径泄露测试全部通过。

### 阶段 5：知识文档版本历史

实现接口：

```text
GET  /api/governance/databases/{db_id}/documents/{file_id}/versions
POST /api/governance/databases/{db_id}/documents/{file_id}/versions/snapshot  superadmin
GET  /api/governance/databases/{db_id}/documents/{file_id}/versions/{version}/download
```

每个版本记录：版本号、源文件 SHA-256、文件大小、治理元数据快照、创建人、创建时间和说明。源文件副本保存到：

```text
saves/data/knowledge_versions/{safe_db_id}/{safe_file_id}/{version}/
```

限制：

- 版本快照只复制本机源文件，不修改 Milvus、知识节点或索引。
- 相同 SHA-256 不重复复制文件，但可以记录新的元数据版本。
- 本轮不提供“恢复并重新索引”按钮。
- 页面明确标注“恢复检索版本需要后续索引版本功能”，不得声称已完成知识库整体回滚。

验收：版本号单调递增；并发创建不会重号；版本文件只能从受控目录读取。

### 阶段 6：问答测试集管理

实现接口：

```text
POST   /api/evaluation/suites
GET    /api/evaluation/suites
GET    /api/evaluation/suites/{suite_id}
PATCH  /api/evaluation/suites/{suite_id}
DELETE /api/evaluation/suites/{suite_id}
POST   /api/evaluation/suites/{suite_id}/cases
PATCH  /api/evaluation/suites/{suite_id}/cases/{case_id}
DELETE /api/evaluation/suites/{suite_id}/cases/{case_id}
POST   /api/evaluation/suites/{suite_id}/import?format=json|csv
GET    /api/evaluation/suites/{suite_id}/export?format=json|csv
```

测试用例字段：问题、标准答案、关键要点数组、知识库标识、分类、难度、启用状态和备注。

限制：

- 仅 `superadmin` 可访问。
- 导入先全量校验，再单事务写入；任一行失败则全部回滚并返回行号。
- 单文件最大 5 MB，单次最多 5000 条。
- 本轮只管理测试集，不自动调用模型，不访问远程多模态知识库。

创建 `EvaluationView.vue`，使用表格、抽屉编辑和导入结果对话框。不要加入假的准确率结果。

验收：CRUD、分页、搜索、事务回滚、CSV 公式注入防护、JSON/CSV 往返一致性通过。

### 阶段 7：统一操作审计

实现接口：

```text
GET /api/audit/events
GET /api/audit/events/{event_id}
GET /api/audit/actions
```

仅 `superadmin` 可访问。支持按用户、动作、资源类型、状态和时间范围筛选。

动作码至少包括：

```text
auth.login
user.create
user.update
user.delete
model.create
model.update
model.delete
model.select
knowledge.upload
knowledge.delete
knowledge.metadata.update
knowledge.download
knowledge.export
feedback.upsert
feedback.delete
evaluation.import
config.update
config.rollback
backup.create
backup.restore
alert.rule.update
alert.email.test
```

详情保存 JSON 字符串，只允许白名单字段。模型审计只记录模型 ID、名称和 API Base，不记录 API Key。

验收：成功和失败操作都有状态；分页稳定；普通用户和 admin 访问返回 403；秘密扫描无命中。

### 阶段 8：系统配置历史和安全回滚

保留现有 `/api/config`、`/api/config/update`，增加：

```text
GET  /api/config/history
GET  /api/config/history/{change_id}
POST /api/config/history/{change_id}/rollback
```

要求：

- 建立可修改字段白名单，拒绝未知键和秘密键。
- `dump_config()` 输出经过统一脱敏器处理。
- 每次修改保存修改前、修改后、操作人和说明。
- 回滚只回滚该次变更涉及的非秘密字段，写入新的历史记录，不删除旧历史。
- 修改成功后明确返回哪些组件需要重启，不自动重启整个系统。

验收：未知配置、秘密字段、类型错误返回 400；回滚幂等；历史中不存在 API Key、密码和 Token。

### 阶段 9：本机备份、预检和恢复

实现接口：

```text
POST /api/operations/backups
GET  /api/operations/backups
GET  /api/operations/backups/{backup_id}
GET  /api/operations/backups/{backup_id}/download
POST /api/operations/backups/{backup_id}/verify
POST /api/operations/backups/{backup_id}/restore/preview
POST /api/operations/backups/{backup_id}/restore
DELETE /api/operations/backups/{backup_id}
```

备份目录：

```text
saves/backups/
```

备份内容：

- 使用 SQLite Backup API 创建一致性 `server.db` 副本，禁止直接复制正在写入的 WAL 数据库。
- 非秘密系统配置。
- 应用日志，可通过参数关闭。
- 知识治理、测试集和版本源文件已经包含在 SQLite/受控版本目录中。
- 可选包含现有知识源文件，必须限制在配置的白名单根目录内。

不得包含：`.env`、模型主密钥、SMTP 密码、远程知识库数据、Milvus/Neo4j 数据卷和 `mul_rag`。

恢复流程：

1. 校验 ZIP 条目、SHA-256、manifest 版本和可用磁盘。
2. `preview` 返回将新增、覆盖和跳过的内容。
3. 正式恢复必须携带 preview 返回的一次性确认令牌。
4. 恢复前自动创建恢复点。
5. SQLite 恢复失败时恢复原数据库。

验收：Zip Slip、损坏压缩包、校验值错误、空间不足、重复确认令牌、恢复中断测试通过。

### 阶段 10：本机系统监控

实现接口：

```text
GET /api/operations/health
GET /api/operations/metrics
GET /api/operations/dependencies
```

监控项：

```text
API 进程
SQLite 可读写状态和文件大小
Milvus 连接
Neo4j 连接
磁盘总量/剩余量
备份目录可写状态
GPU 是否存在、显存使用率、GPU 利用率
最近一次备份结果
最近一次告警结果
```

要求：

- 每个检查项独立超时，单个依赖失败不能拖死整个接口。
- GPU 不存在时返回 `unavailable`，不能导致 500。
- 不检查远程多模态知识库，不把远程离线显示成系统故障。
- `/api/health` 保持轻量；详细检查只放在 `/api/operations/*`。

验收：依赖成功、超时、拒绝连接和 GPU 不存在四类场景均返回结构化状态。

### 阶段 11：邮件告警

新增非秘密配置示例：

```text
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_USE_TLS=true
ALERT_CHECK_INTERVAL_SECONDS=60
```

实现接口：

```text
POST   /api/operations/alert-rules
GET    /api/operations/alert-rules
PATCH  /api/operations/alert-rules/{rule_id}
DELETE /api/operations/alert-rules/{rule_id}
GET    /api/operations/alert-events
POST   /api/operations/alert-events/{event_id}/acknowledge
POST   /api/operations/email/test
```

首批规则类型：磁盘剩余比例、SQLite 检查失败、Milvus 不可用、Neo4j 不可用、GPU 显存使用率和备份连续失败。

要求：

- FastAPI lifespan 启动一个可取消的本机检查任务，关闭应用时等待任务退出。
- 相同告警设置冷却时间，避免邮件风暴。
- 恢复正常时记录 resolved 状态，可选择发送恢复通知。
- SMTP 未配置时测试邮件返回 503，监控主流程仍正常。
- 邮件和日志不得输出 SMTP 密码。

验收：触发、去重、冷却、恢复、确认和 SMTP 失败路径通过。

### 阶段 12：本机功能管理页面和权限导航

新增两个超级管理员页面：

```text
/evaluation   问答测试集
/operations   系统运维
```

`OperationsView.vue` 使用页签组织：运行状态、审计日志、备份恢复、配置历史、告警规则。知识治理继续放在知识库详情页，不再创建第三个一级页面。

权限要求：

- 导航只对 `superadmin` 显示。
- 直接输入 URL 时，`admin` 和 `user` 被路由守卫送回 `/chat`。
- 后端仍必须返回 403，不能只依赖前端隐藏。
- 用户管理保持当前抽屉形式，不改回页面跳转。

UI 要求：

- 使用现有主题变量，亮色文字为深色、暗色文字为浅色。
- 表格使用服务器分页，不一次加载全部审计或告警。
- 破坏性恢复操作使用二次确认和预检摘要。
- 不使用嵌套卡片，不增加营销式标题区。

验收：三种角色导航和直达路由测试通过；亮色/暗色桌面视口无文字重叠。

### 阶段 13：全量验收和交付

执行：

```powershell
git diff --check
docker compose config --quiet
docker exec api-dev uv run python -m unittest discover -s test -p "test_*.py"
docker exec web-dev pnpm test
docker exec web-dev pnpm build
```

再执行安全扫描：

```powershell
rg -n "api[_-]?key|password|secret|token" server web/src test -g "*.py" -g "*.js" -g "*.vue" -g "*.mjs"
```

人工验收流程：

1. 普通用户登录，完成一次问答并点赞，刷新后仍显示点赞。
2. 普通用户尝试访问运维、审计、测试集和 restricted 文档下载，均被拒绝。
3. admin 管理普通用户，但不能进入系统运维。
4. superadmin 查看反馈统计、编辑知识治理元数据并导出 XLSX。
5. superadmin 创建测试集，导入 CSV，再导出并核对条数。
6. superadmin 创建备份、校验备份、执行恢复预检。
7. 查看本机依赖状态；断开一个测试依赖后接口仍能返回其他检查项。
8. 创建低磁盘阈值测试规则，验证告警、冷却和确认。
9. 确认多模态知识库页面和远程问答行为与本轮开始前一致。

最终检查禁止修改路径：

```powershell
git diff -- mul_rag server/utils/multimodal_remote.py server/routers/multimodal_proxy_router.py server/services/http_clients.py web/src/apis/multimodal.js web/src/views/MultimodalKbView.vue web/src/components/AuthenticatedImage.vue web/src/utils/multimodalSearch.mjs
```

预期：无输出。

## 8. 提交顺序

每个提交只包含一个可独立验收的阶段：

```text
test(scope): guard local functional API boundaries
feat(feedback): persist answer ratings
feat(statistics): include real feedback metrics
feat(governance): manage document access metadata
feat(governance): record local document versions
feat(evaluation): manage QA test suites
feat(audit): expose filtered operation logs
feat(config): add safe configuration history
feat(backup): add verified local backups
feat(operations): report local dependency health
feat(alerts): send deduplicated system alerts
feat(web): add local evaluation and operations views
test(e2e): verify local functional interfaces
```

禁止使用一个大提交完成所有任务。每个提交前运行该阶段的后端测试、前端测试以及禁止修改路径检查。

## 9. Claude 执行规则

1. 开始前完整阅读本文件，不得边读边改。
2. 按阶段顺序执行，不得同时大范围修改多个 Router。
3. 每个阶段先写失败测试，再写最小实现，再运行回归测试。
4. 不确定数据处理接口时，只调用现有公开方法，不进入 `src/core` 修改。
5. 发现必须修改远程多模态接口或索引算法才能继续时，立即停止该子任务并记录为延期，不得自行突破边界。
6. 不删除或回退当前分支已有功能，不覆盖用户未提交的修改。
7. 不使用演示数据、硬编码成功结果或只做前端假按钮。
8. 每阶段汇报：修改文件、接口清单、测试结果、遗留风险和禁止路径检查结果。

## 10. 完成定义

只有同时满足以下条件才算完成：

- 本轮直接补齐的 10 类功能均有真实后端接口、持久化、权限和前端入口。
- 已有能力的加固没有破坏当前问答、模型切换、知识库和用户管理。
- 所有新增接口都有成功、失败、越权和边界测试。
- 前后端测试及构建通过，或明确列出与本轮无关的既有失败。
- 远程多模态和数据处理禁止路径保持零差异。
- 没有秘密、绝对路径和伪造统计泄露到响应、日志、审计或前端。
- 交付说明明确指出：真实知识库索引版本回滚、数据处理和远程多模态接口不在本轮范围。
