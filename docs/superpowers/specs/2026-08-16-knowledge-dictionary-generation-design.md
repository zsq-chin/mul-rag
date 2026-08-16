# 知识字典生成与向量检索设计

## 1. 背景与目标

本功能在现有 SAGE 项目中新增“知识字典”能力，从专业资料中发现并沉淀结构化术语。系统支持三种来源：

1. 选择现有知识库中的一个文件。
2. 临时上传一个文件。
3. 选择整个知识库。

每条知识字典记录至少包含：

- 分类
- 标准名称
- 定义
- 单位
- 数据类型
- 同义词
- 取值规则
- 来源证据

生成结果必须经过人工审核后才能发布。已保存的字典条目同时建立 Milvus 向量索引，为后续字典检索和问答增强提供基础。

本功能沿用现有登录、用户角色、知识库文件解析、自定义模型和 Milvus 基础设施，不修改远端多模态知识库代码。远端多模态知识库仅作为既有黑盒服务，且不是本功能的运行依赖。

## 2. 范围与非目标

### 2.1 本期范围

- 字典列表、详情、版本和状态展示。
- 三种数据来源的生成向导。
- 后台持久化生成任务及进度、取消、失败重试和重启恢复。
- 专业字段候选发现、证据校验、标准化、去重、冲突识别和置信度计算。
- 人工审核、批量审核、编辑、新增、删除和合并。
- 发布、撤回和活动版本管理。
- 独立 Milvus 字典集合、增量索引、索引校验和语义检索。
- XLSX、CSV、JSON 导出。
- 将 `XinJiang` 目录中的旧版压裂字典资产一次性迁移为种子字典。
- 管理员、超级管理员和普通用户的前后端权限控制。

### 2.2 非目标

- 不修改 `mul_rag/**`、`10.16.33.2:8002` 或其他远端多模态服务。
- 本期不把知识字典检索结果自动注入现有知识问答提示词；只提供稳定检索接口，后续单独接入问答编排。
- 不用知识字典替换现有知识库分块或普通 RAG 检索。
- 不允许模型自动发布，也不允许模型在无证据时虚构条目。
- 不自动执行存在歧义的语义合并。
- 不直接把旧版 `XinJiang` Python 脚本作为生产运行时依赖。

## 3. 角色与权限

在知识字典功能内，`admin` 与 `superadmin` 权限完全相同；`user` 只能查看和检索已发布内容。

| 能力 | `superadmin` | `admin` | `user` |
| --- | --- | --- | --- |
| 查看字典列表和已发布详情 | 允许 | 允许 | 允许 |
| 检索已发布活动版本 | 允许 | 允许 | 允许 |
| 查看来源证据 | 允许 | 允许 | 允许 |
| 查看草稿或历史版本 | 允许 | 允许 | 禁止 |
| 创建和编辑字典 | 允许 | 允许 | 禁止 |
| 发起生成、取消和重试任务 | 允许 | 允许 | 禁止 |
| 审核、合并、发布和撤回 | 允许 | 允许 | 禁止 |
| 构建、重建和校验向量索引 | 允许 | 允许 | 禁止 |
| 导出字典 | 允许 | 允许 | 禁止 |
| 删除字典或草稿版本 | 允许 | 允许 | 禁止 |

前端权限仅改善用户体验，后端必须对每个接口独立鉴权。普通用户即使手工构造请求，也不能读取草稿、调用写接口或通过检索参数包含未发布版本。

## 4. 页面与交互设计

### 4.1 导航入口

主导航增加“知识字典”。三类用户都能看到入口：

- 普通用户进入只读视图。
- 管理员和超级管理员进入完整管理视图。

页面遵循当前项目的亮色、暗色主题和现有 Ant Design Vue 组件规范，不创建独立视觉体系。

### 4.2 字典列表

列表是功能首屏，支持按名称、分类、状态、创建人和更新时间筛选。每行显示：

- 字典名称与说明
- 专业领域
- 当前活动版本
- 条目数
- 来源类型摘要
- 发布状态
- 向量索引状态
- 更新时间

管理员和超级管理员可以新建、继续审核、重建索引、导出、撤回或删除；普通用户只显示“查看”。活动字典必须先撤回才能删除，删除采用可审计的软删除并二次确认。

### 4.3 生成向导

“生成知识字典”使用三步向导，不通过跳转堆叠多个业务页面：

1. **选择来源**：知识库文件、上传文件或整个知识库。
2. **配置生成**：字典名称、领域、生成模型、目标分类、是否使用种子字典、文件范围和重复处理策略。
3. **确认任务**：展示来源快照、预计文件数、风险提示和任务配置，提交后立即返回任务编号。

来源交互规则：

- 知识库文件：先选知识库，再在可搜索、可滚动、分页加载的文件列表中选择一个文件。
- 上传文件：支持 PDF、DOCX、XLSX、CSV 和 TXT；默认仅用于本次生成，不自动写入现有知识库。
- 整个知识库：展示文件数量、已解析数量和异常文件数量；提交时冻结文件 ID 与内容哈希快照。

向导不得接收或展示 API Key。生成模型从当前用户可使用的已保存模型中按 `model_id` 选择，密钥只在后端运行时解密。

### 4.4 任务进度

提交后显示持续可恢复的任务面板，包括：

- 总体进度与当前阶段
- 已处理文件、分块和候选条目数
- 已合并、冲突、待审核和失败数量
- 开始时间、耗时和最近心跳
- 取消、失败重试和查看错误摘要

页面刷新或重新登录后根据任务 ID 恢复状态，不依赖浏览器内存保存进度。

### 4.5 审核工作台

审核工作台采用“筛选栏 + 条目表格 + 右侧证据抽屉”：

- 筛选：分类、审核状态、置信度、冲突、缺失字段、来源文件。
- 表格：标准名称、定义、单位、数据类型、同义词、取值规则、置信度、审核状态。
- 证据抽屉：原文片段、文件名、页码或工作表、分块位置、上下文和来源跳转。

管理员可逐条编辑、通过、驳回，也可对同一筛选结果批量操作。批量通过前仍要校验来源证据和必填字段。语义相似条目只给出合并建议，用户确认前不得覆盖原条目。

### 4.6 只读详情与检索

普通用户看到活动版本的只读表格、证据和字典内检索。检索结果显示相似度、命中字段、定义、同义词、单位、规则和来源证据。历史版本、内部置信度和审核备注不对普通用户展示。

## 5. 总体架构

知识字典使用独立业务边界，避免把生成、审核和向量一致性逻辑继续塞进现有 `KnowledgeBase` 类。

```text
Vue 页面
  -> Knowledge Dictionary API
      -> 权限与参数校验
      -> Dictionary Service / Repository -> 关系数据库（唯一事实来源）
      -> Source Adapters -> KnowledgeNode 或安全文件解析器
      -> Job Service -> dictionary-worker
          -> Candidate Extractor
          -> Normalizer / Deduplicator
          -> Evidence Validator
          -> Dictionary Vector Indexer -> Milvus
```

组件职责如下：

- **API Router**：鉴权、请求校验、响应结构和错误映射。
- **Dictionary Service**：版本、审核、发布、撤回和业务不变量。
- **Repository**：关系数据读写与事务边界。
- **Source Adapter**：统一三种来源为带稳定定位信息的文档节点流。
- **Candidate Extractor**：调用选定模型输出严格结构化候选条目。
- **Evidence Validator**：验证候选值能映射回真实原文。
- **Normalizer/Deduplicator**：名称、单位、类型和同义词规范化，执行确定性合并并生成冲突建议。
- **Job Service/Worker**：持久化任务、租约、心跳、检查点、取消和重试。
- **Vector Indexer**：生成规范向量文本，维护 Milvus 增量索引并执行一致性校验。

API 进程只接收任务和查询状态，不使用 FastAPI `BackgroundTasks` 承载长任务。Docker 中增加同代码镜像的 `dictionary-worker` 进程；worker 通过数据库任务租约领取工作，崩溃或重启后可识别中断任务。

## 6. 数据模型

### 6.1 `knowledge_dictionaries`

字典主记录：

- `id`
- `name`
- `description`
- `domain`
- `status`：`draft`、`published`、`withdrawn`
- `active_version_id`
- `created_by`、`updated_by`
- `created_at`、`updated_at`

名称在未删除字典中唯一。活动版本只能指向已发布且索引状态为 `ready` 的版本。

### 6.2 `knowledge_dictionary_versions`

- `id`、`dictionary_id`、`version_no`
- `status`：`draft`、`reviewing`、`published`、`withdrawn`
- `source_snapshot_hash`
- `generation_config`：脱敏后的模型 ID、提示词模板版本和规则版本
- `embedding_config_hash`
- `index_status`：`pending`、`embedding`、`indexed`、`verified`、`ready`、`failed`
- 条目、待审核、冲突和向量计数
- `created_by`、`published_by`
- `created_at`、`published_at`

同一字典的 `version_no` 单调递增；已发布版本内容不可原地修改，变更时创建新草稿版本。

### 6.3 `knowledge_dictionary_sources`

- `id`、`version_id`
- `source_type`：`knowledge_base_file`、`knowledge_base`、`upload`
- `knowledge_base_id`、`file_id`、`file_name`
- `storage_ref`：仅保存受控存储标识，不保存任意绝对路径
- `content_hash`
- `parser_version`
- `snapshot_metadata`

整个知识库来源为每个文件建立来源行，以便检测后续文件变更和精确溯源。

### 6.4 `knowledge_dictionary_entries`

- `id`、`version_id`
- `category`
- `standard_name`
- `normalized_name`
- `definition`
- `unit`、`normalized_unit`
- `data_type`
- `synonyms`：JSON 数组
- `value_rule`：可读说明和可选结构化约束
- `review_status`：`pending`、`approved`、`rejected`、`conflict`
- `confidence`
- `review_note`
- `content_hash`
- `vector_id`
- `index_status`
- `created_by`、`reviewed_by`
- `created_at`、`updated_at`、`reviewed_at`

`standard_name`、`definition` 和至少一条有效证据是发布必填项。`data_type` 使用受控枚举；未知类型保留为 `string` 候选并标记待审核，不能让模型写入任意类型名。

### 6.5 `knowledge_dictionary_evidences`

- `id`、`entry_id`、`source_id`
- `node_id`
- `quote`
- `page_no`、`sheet_name`、`cell_range`
- `start_offset`、`end_offset`
- `locator_metadata`
- `evidence_hash`

证据必须能定位到来源快照。PDF/DOCX 优先使用页码和字符位置，XLSX 使用工作表与单元格范围，知识库文件同时保留 `KnowledgeNode.id`。

### 6.6 `knowledge_dictionary_jobs`

- `id`、`job_type`：`generate`、`index`、`import_seed`、`export`
- `dictionary_id`、`version_id`
- `status`：`queued`、`running`、`cancelling`、`cancelled`、`completed`、`failed`、`interrupted`
- `stage`、`progress`
- 输入配置快照与检查点
- 处理计数和脱敏错误摘要
- `lease_owner`、`lease_expires_at`、`heartbeat_at`
- `requested_by`
- `created_at`、`started_at`、`finished_at`

任务配置不得保存 API Key。生成版本、来源快照和任务记录在同一数据库事务中创建，避免出现没有任务的半成品版本。

## 7. 来源处理

### 7.1 现有知识库文件

直接复用已解析的 `KnowledgeNode`，不重复解析原文件。适配器按节点顺序输出文本、文件 ID、节点 ID、页码和元数据。文件不存在、尚未解析或节点为空时拒绝创建任务，并返回明确错误。

### 7.2 整个知识库

提交时冻结知识库 ID、文件 ID、文件哈希和节点集合版本。任务始终处理该快照；运行期间新增、删除或替换文件不悄悄进入当前版本。发布前再次比较来源哈希，发生变化则阻止发布并提示重新生成。

### 7.3 上传文件

上传文件进入受控临时/业务存储，通过白名单解析器处理。必须同时校验扩展名、MIME、文件签名、大小、压缩展开大小和解析超时。文件名不能决定磁盘路径，禁止目录穿越和外部 URL 拉取。

上传文件默认不进入知识库。字典版本或全部依赖版本被删除后，按保留策略清理原文件；只要有已发布版本依赖，就必须保留可审计来源。

## 8. 生成、证据与标准化

### 8.1 候选发现

文档节点按可配置批次进入模型。系统提示词明确文档内容是不可信数据，文档中的指令不得改变抽取规则。模型只返回满足 JSON Schema 的候选数组，字段包括字典字段、证据引用、候选置信信号和来源节点 ID。

结构化响应处理规则：

1. 首次响应严格校验 JSON Schema。
2. 格式错误时允许一次结构修复。
3. 修复仍失败时按有限次数重试该批次。
4. 超过重试次数则记录批次失败并继续或将任务标记失败，取决于失败比例阈值。

系统不保存或展示模型思维过程，只保存字段映射的简短说明和来源证据。

### 8.2 证据校验

“无证据，不成条目”是强约束：

- 模型返回的引用必须指向本批次真实节点。
- 引文经空白和标点归一化后必须能在节点文本中匹配。
- 定义、单位、数据类型或规则若为模型推断，必须标记为“推断，待审核”，不能伪装成原文。
- 完全没有有效引文的候选直接丢弃并计入拒绝统计。
- 来源文件、节点、页码或单元格和原文片段一并保存。

### 8.3 标准化与去重

标准化顺序：

1. 清理名称空白、全半角、常见标点和大小写差异。
2. 通过种子字典统一已知标准名称和同义词。
3. 通过受控单位映射统一表示，同时保留原单位。
4. 将数据类型映射到受控枚举。
5. 执行同一版本内的确定性精确去重。
6. 对向量相似候选生成合并建议。

标准名称一致、类型兼容且单位一致或可换算时，可以确定性合并并合并证据。语义相似但单位或类型冲突时必须进入 `conflict`，不得自动合并。所有人工合并保留原条目 ID、操作人和变更记录。

### 8.4 置信度

置信度由可解释信号组合产生：

- 是否存在明确原文定义
- 单位和数据类型是否明确
- 是否命中种子字典
- 是否有多个独立来源支持
- 是否存在冲突
- 必填字段完整度

默认分级：

- `>= 0.85`：高置信，仍需人工审核后发布。
- `0.60-0.84`：重点审核。
- `< 0.60`：低置信待处理，不允许批量直接通过。

阈值作为后端配置保存，前端不能提交任意阈值绕过发布规则。

## 9. XinJiang 种子迁移

`XinJiang/extract/水平井压裂数据管理.xlsx` 包含基础表、设计表、施工表和生产表，可作为首个压裂知识字典的主要种子。迁移工具应读取多行表头中的分类、标准字段名、单位和示例值，并合并旧脚本中的同义词、值字典和线索字典。

迁移结果保存为“压裂知识字典 V1”的草稿版本，经过管理员审核、索引和发布后使用。旧 Python 脚本中的硬编码井名、调试打印、提示词和模型调用不进入生产运行路径。

种子导入必须幂等：保存导入器版本和源文件哈希；相同版本、相同哈希重复执行不创建重复字典或条目，源文件变化时创建新草稿版本。

## 10. Milvus 向量索引

### 10.1 存储边界

关系数据库是字典、版本、审核状态和证据的唯一事实来源。Milvus 是可删除、可重建的派生索引，不承担业务状态保存。

字典向量使用独立共享集合，初始物理集合为：

```text
knowledge_dictionary_entries_v1
```

不把字典条目混入现有按知识库组织的文档分块集合，以免过滤、生命周期和发布权限相互污染。后续更换不兼容的 embedding 模型时创建 `knowledge_dictionary_entries_v2` 等新物理集合，不能在 `v1` 中混写不同向量空间。

### 10.2 向量文本

每个字典条目生成一个规范文本向量：

```text
分类：{category}
标准名称：{standard_name}
定义：{definition}
单位：{unit}
数据类型：{data_type}
同义词：{synonyms}
取值规则：{value_rule}
```

证据原文不默认拼入向量文本，避免长证据稀释术语语义；证据通过关系数据库按条目 ID 获取。

Milvus 元数据至少包含：

- `dictionary_id`
- `version_id`
- `entry_id`
- `category`
- `standard_name`
- `data_type`
- `unit`
- `review_status`
- `version_status`
- `content_hash`
- `embedding_config_hash`

### 10.3 模型与增量索引

向量索引使用系统固定的 embedding 模型，不允许每次生成任务自行选择，以保证同一集合维度和距离含义稳定。模型名称、维度、归一化方式和版本共同生成 `embedding_config_hash`。

增量规则：

- `content_hash` 与 embedding 配置都未变化：复用现有向量。
- 条目新增或内容变化：重新生成并 upsert。
- 条目删除或被驳回：删除对应向量。
- embedding 配置变化：创建新集合版本或执行受控全量重建，不在旧集合混写不同向量空间。

生成任务完成后自动排入草稿索引任务，而不是要求管理员逐条触发。草稿索引包含未被驳回的候选，供管理员在审核工作台测试检索；每次编辑、合并、通过或驳回都按 `content_hash` 自动排入增量索引。发布前重新同步索引，只保留 `approved` 条目并执行完整一致性校验。

索引状态按 `pending -> embedding -> indexed -> verified -> ready` 推进。任何阶段失败都记录在版本和任务中，允许从安全检查点重试。

### 10.4 检索权限

- 普通用户只能检索已发布字典的活动版本和 `approved` 条目。
- 管理员和超级管理员默认也检索活动版本，可显式启用草稿测试并指定版本；草稿测试可召回 `pending`、`approved` 和 `conflict` 条目，但永不召回 `rejected` 条目。
- 服务端构造 Milvus 过滤表达式，不能信任前端传入的任意过滤字符串。
- 召回后回查关系数据库，再次校验版本、条目状态和用户权限，Milvus 元数据不能代替最终授权。

### 10.5 一致性校验与发布

索引完成后至少执行：

- 关系数据库期望索引条目数与 Milvus 唯一 `entry_id` 数一致：草稿按未驳回条目计算，发布前按 `approved` 条目计算。
- 不存在当前版本之外的悬挂向量。
- 随机抽样检索可回查到正确关系记录和内容哈希。
- embedding 配置与集合 schema 一致。

只有校验通过，版本索引状态才能变为 `ready`。一致性失败时业务数据保留，发布被阻止，管理员可重新索引。

## 11. 版本、审核与发布

### 11.1 生命周期

```text
生成任务 -> draft -> reviewing -> published
                         |             |
                         v             v
                      draft         withdrawn
```

撤回只取消活动版本，不删除历史数据和审计证据。发布新版本时在一个数据库事务中更新旧活动版本状态、目标版本状态和字典 `active_version_id`。

### 11.2 发布阻断条件

存在任一条件时禁止发布：

- 仍有 `pending` 或 `conflict` 条目未处理。
- 通过条目缺少标准名称、定义或有效证据。
- 来源快照相对提交时发生变化且未明确重新生成。
- 索引状态不是 `ready`。
- 数据库条目计数与 Milvus 向量计数不一致。
- 当前 embedding 配置与索引配置不一致。
- 生成或索引任务仍在运行、取消中或失败。

发布失败不能留下部分活动状态。再次发布必须是幂等操作。

## 12. 任务、并发与恢复

### 12.1 任务协议

创建生成或索引任务立即返回 `202 Accepted` 和 `job_id`。前端轮询任务接口，后续可平滑替换为 SSE，但本期不要求。

worker 按文件、节点批次、模型批次和向量批次写入检查点。服务重启时：

- 租约过期的 `running` 任务标记为 `interrupted`。
- 用户可从最近安全检查点重试。
- 已提交的条目批次保持幂等，不重复写入。
- 取消请求在文件、模型和向量批次边界生效。
- 取消后的部分结果保留为不可发布草稿，供管理员检查或删除。

### 12.2 并发限制

默认全局同时运行一个生成任务和一个索引任务。二者使用独立并发信号量，并与知识问答/普通检索的资源限制隔离。达到上限的任务进入队列，不在 API 请求中长时间等待。

模型调用设置连接、读取和总超时，批次失败有限重试并使用退避。向量写入分批执行，单批失败不得把整个版本错误标记为已完成。

## 13. API 设计

统一前缀：`/api/knowledge-dictionaries`。

### 13.1 字典与版本

- `GET /api/knowledge-dictionaries`
- `POST /api/knowledge-dictionaries`
- `GET /api/knowledge-dictionaries/{dictionary_id}`
- `PATCH /api/knowledge-dictionaries/{dictionary_id}`
- `DELETE /api/knowledge-dictionaries/{dictionary_id}`
- `GET /api/knowledge-dictionaries/{dictionary_id}/versions`
- `GET /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}`
- `POST /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/publish`
- `POST /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/withdraw`

### 13.2 生成与任务

- `POST /api/knowledge-dictionaries/generate`
- `GET /api/knowledge-dictionaries/jobs/{job_id}`
- `POST /api/knowledge-dictionaries/jobs/{job_id}/cancel`
- `POST /api/knowledge-dictionaries/jobs/{job_id}/retry`

`POST /api/knowledge-dictionaries` 只创建可手工维护的空字典。`generate` 使用互斥的来源结构：不传 `dictionary_id` 时创建新字典及 V1，传入现有 `dictionary_id` 时创建下一个草稿版本。后端必须保证一次请求只选择一种来源。整个知识库和已有文件使用 ID，上传文件使用受控上传令牌，不接受客户端绝对路径。

### 13.3 条目与审核

- `GET /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/entries`
- `POST /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/entries`
- `PATCH /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/entries/{entry_id}`
- `DELETE /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/entries/{entry_id}`
- `GET /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/entries/{entry_id}/evidences`
- `POST /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/entries/batch-review`
- `POST /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/entries/merge`

批量接口接收明确的条目 ID 列表和版本并发令牌，不允许仅凭前端筛选条件直接修改未知数量的数据。

条目新增、编辑、删除、审核和合并接口只允许操作草稿或审核中版本。已发布版本保持不可变；需要修订时必须创建新版本。

### 13.4 向量索引与检索

- `POST /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/index`
- `GET /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/index-status`
- `POST /api/knowledge-dictionaries/search`

检索请求支持 `query`、允许的 `dictionary_ids`、`top_k` 和管理员专用 `version_id/include_draft`。`top_k` 设置服务端上限，返回条目、相似度、命中版本和证据摘要，不返回任意 Milvus 内部字段。

### 13.5 导出

- `GET /api/knowledge-dictionaries/{dictionary_id}/versions/{version_id}/export?format=xlsx`
- 支持 `xlsx`、`csv`、`json`。

XLSX 为默认交付格式，包含字典条目、来源证据、版本信息三个工作表。CSV 导出对以 `= + - @` 开头的值进行公式注入防护。导出文件名和响应头正确处理中文。

## 14. 错误、安全与审计

### 14.1 错误响应

所有业务错误采用统一结构：

```json
{
  "error_code": "DICTIONARY_SOURCE_CHANGED",
  "message": "来源文件已变化，请重新生成后再发布",
  "trace_id": "可追踪但不包含敏感信息的标识",
  "details": {}
}
```

主要状态码：

- `400`：互斥来源或普通参数错误。
- `403`：角色无权执行操作或读取草稿。
- `404`：字典、版本、来源、任务或条目不存在。
- `409`：版本并发冲突、重复活动任务、来源变化或发布条件不满足。
- `413`：上传文件或解压内容超限。
- `415`：文件类型不支持或文件签名不匹配。
- `422`：生成结果、字段值或证据校验失败。
- `429`：模型或任务资源达到限额。
- `503`：模型、数据库或 Milvus 暂时不可用。

### 14.2 安全

- API Key 只以加密形式保存在后端，不进入字典任务配置、响应、浏览器存储或日志。
- 上传文件、模型输出和来源文档均视为不可信输入。
- 富文本定义和证据在前端展示前按现有安全渲染规则消毒。
- 不允许前端提交 Milvus 表达式、磁盘路径、集合名或 embedding 模型名。
- 日志记录任务 ID、模型 ID、耗时和错误类别，不记录完整文档、完整提示词、密钥或模型原始异常体。
- 所有创建、编辑、审核、合并、发布、撤回、索引和删除操作记录操作人、对象、前后状态、时间和结果。

## 15. 测试与验收

### 15.1 后端单元测试

- 三种来源请求互斥校验和权限矩阵。
- PDF、DOCX、XLSX、CSV、TXT 适配器及异常文件。
- 名称、单位、数据类型和同义词标准化。
- 精确去重、可合并建议、单位/类型冲突。
- 证据定位、无证据拒绝和推断字段待审核。
- 置信度边界和发布阻断条件。
- 任务状态机、租约、心跳、取消、重试和中断恢复。
- 向量文本、内容哈希、增量 upsert 和删除。
- 普通用户不能通过构造参数访问草稿。
- 导出内容、中文文件名和 CSV 公式注入防护。

### 15.2 集成测试

- 使用真实测试数据库和 Milvus 完成生成、审核、索引、发布、搜索全流程。
- 注入模型超时、结构化输出错误、数据库写入失败和 Milvus 部分失败。
- 验证重试不会产生重复条目、证据和向量。
- 验证关系数据库条目数与 Milvus 唯一向量数一致。
- embedding 配置变化时阻止旧集合混写。
- `XinJiang` 种子导入连续执行两次结果幂等。
- 远端多模态服务关闭时，本功能测试和运行不受影响。

### 15.3 前端与浏览器测试

- 三种来源向导、文件滚动分页、任务进度恢复。
- 审核筛选、证据抽屉、单条和批量操作、冲突合并。
- 管理员与超级管理员操作一致，普通用户只读。
- 普通用户不显示写按钮，直接访问管理 URL 仍被路由和后端共同拒绝。
- 亮色与暗色主题、桌面和移动视口下无文字遮挡或横向布局破坏。
- 大量条目使用服务端分页或虚拟滚动，页面不一次加载全部记录。

### 15.4 上线验收标准

1. 三种来源都能创建持久化任务，并在页面刷新后恢复进度。
2. 每个可发布条目都包含标准名称、定义和至少一条可定位证据。
3. 普通用户只能查看、检索已发布活动版本，所有写接口返回 `403`。
4. 管理员和超级管理员可完成生成、审核、索引、发布、撤回和导出。
5. 已发布版本的数据库批准条目数与 Milvus 向量数一致。
6. 相同内容重建索引不产生重复向量，修改和删除只影响对应条目。
7. Milvus 不可用或一致性校验失败时不能发布，但关系数据库草稿不丢失。
8. worker 重启后任务进入可解释的中断/重试状态，不虚报成功。
9. `XinJiang` 数据可幂等迁移为可审核的“压裂知识字典 V1”。
10. 日志、接口响应和浏览器存储中不出现模型 API Key。

## 16. 实施阶段

1. **数据与权限基础**：增加表、迁移、Repository、角色依赖和审计事件。
2. **种子迁移**：实现 `XinJiang` 解析、标准化和幂等导入，建立首个草稿。
3. **来源与任务**：实现三种 Source Adapter、来源快照、持久化任务和 worker。
4. **生成与审核后端**：实现候选抽取、证据校验、标准化、去重、冲突和审核 API。
5. **向量检索**：实现独立集合、增量索引、一致性校验、发布门禁和搜索 API。
6. **前端页面**：完成列表、生成向导、任务面板、审核工作台、只读详情和权限状态。
7. **导出与上线加固**：完成多格式导出、失败恢复、自动化测试、Docker 验证和上线验收。

每个阶段必须独立通过对应自动化测试后再进入下一阶段。后续实现计划应把这些阶段拆成可逐项交给 Claude Code 执行、可单独提交和可由 Codex 验收的任务。
