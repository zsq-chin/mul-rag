# 压裂知识库平台功能完善与稳定性设计

## 1. 背景与目标

本次改造覆盖八个彼此关联但可独立验收的子项目：

1. 为整个 Vue 应用提供可持久化的亮色/暗色主题切换。
2. 让知识问答主回答和右侧引用面板正确显示图片、Markdown 表格和带合并单元格的 HTML 表格。
3. 修通多模态知识库从远端检索结果、主后端归一化、图片代理到前端展示的完整链路。
4. 将知识图谱生成改造成有真实状态、可追踪、可重试、可自动导入 Neo4j 的任务流水线，并修正图谱增强问答。
5. 隔离长任务和阻塞调用，增加有界并发、连接池、超时和生产部署配置，使过载表现为排队或明确拒绝，而不是服务崩溃。
6. 在知识问答页提供按用户保存的自定义模型新增、切换、编辑、删除和最近使用能力，并保证 API Key 不会从后端回传或写入浏览器持久存储。
7. 建立 `superadmin`、`admin`、`user` 三角色权限体系：超级管理员拥有全部功能，管理员只能使用知识问答并管理普通用户，普通用户只能使用知识问答。
8. 将用户管理从独立页面跳转改成主布局内的右侧抽屉，并按角色限制可见用户和可执行操作。

本设计选择分阶段稳健改造。它不在本阶段引入 Redis/Celery，但任务 API 和任务存储边界必须允许后续替换为外部队列。

## 2. 当前问题证据

- `web/src/assets/theme.js` 只有浅色 token，组件中存在大量硬编码白色和黑色。
- `MessageComponent.vue` 使用固定 Markdown 预览 ID，缺少响应式图片和表格容器样式。
- `RefsSidebar.vue` 用文本插值显示检索片段，远端返回的 HTML 表格会被转义。
- `server/utils/multimodal_remote.py` 只处理 Markdown 图片和单个 `image_path`，遗漏 `referenced_images`。
- 远端 `钻井设计资料` 已验证会返回 `./images/*.png`、`source.image_path`、`source.referenced_images` 和带 `rowspan`/`colspan` 的 HTML 表格。
- `GraphView.vue` 在部分失败分支仍提示生成成功，且只有布尔 loading，没有任务进度。
- `graphrag_api/main.py` 同步运行 GraphRAG，只生成 Parquet 工件，不会自动导入 Neo4j。
- 现有 GraphRAG 工件 `create_final_relationships.parquet` 包含 `source`、`target`、`description` 等可直接转换为 `h`、`t`、`r` 的字段。
- 多个 `async` 路由直接调用 `requests` 或 `time.sleep`；长任务与问答共享执行资源。
- 当前自定义模型配置保存在全局 `config.custom_models` 中，无法隔离不同用户，也无法可靠记录个人最近使用模型。
- `/usermanagement` 被配置成无需登录的独立路由，前端路由守卫整体处于注释状态，侧栏也未按角色过滤。
- 多个知识库和图谱管理接口仍允许 `admin` 访问，与新的角色矩阵不一致。
- `src/utils/web_search_bocha.py` 曾包含硬编码的第三方 API Key，且模型异常信息会暴露部分密钥字符；现有密钥必须在供应商侧轮换，代码改为只从环境变量读取并完全脱敏日志。

## 3. 总体架构

### 3.1 服务边界

- Vue 前端负责主题状态、任务进度展示和安全富内容展示。
- 主 FastAPI 服务负责鉴权、问答编排、多模态结果归一化、资源代理和 Neo4j 导入。
- 主 FastAPI 服务同时负责用户模型凭据的加密存储、所有权校验和运行时解密；浏览器只持有不透明模型记录 ID。
- `graphrag-worker` 负责 GraphRAG 长任务、任务持久化、阶段推进、产物转换和失败重试。
- Neo4j 负责最终图数据；GraphRAG 输出目录和任务数据库通过 Docker volume 持久化。

### 3.2 Git 与交付方式

- 所有修改位于 `feature/platform-hardening` 分支。
- 八个子项目按依赖关系形成可审查提交。
- 每个提交必须先由 Claude Code 在限定范围内实现，再由 Codex 检查 diff、运行测试并验证页面或接口行为。

## 4. 主题系统

### 4.1 状态与初始化

- 新增独立 Pinia 主题 store，状态仅允许 `light` 或 `dark`。
- 用户选择写入 `localStorage`；首次访问没有记录时使用系统 `prefers-color-scheme`。
- store 同步更新 `document.documentElement.dataset.theme` 和 Ant Design Vue 的主题配置。

### 4.2 UI

- 在桌面侧边栏底部、用户入口附近增加图标按钮。
- 亮色状态显示月亮图标，暗色状态显示太阳图标，并提供中文 tooltip。
- 移动端导航提供同一操作入口。
- 不增加第三种“自动”模式，避免偏离用户要求的亮/暗二选一。

### 4.3 样式

- Ant Design 使用 `defaultAlgorithm`/`darkAlgorithm`。
- 全局语义变量覆盖页面背景、容器背景、浮层背景、文字、次要文字、边框、悬停和错误状态。
- 本次涉及的布局、问答、输入框、引用侧栏和图谱页面不得保留影响暗色可读性的硬编码白色/黑色。

## 5. 问答富内容与多模态展示

### 5.1 归一化结果契约

主后端对每条多模态结果输出稳定结构：

```json
{
  "id": 1,
  "rank": 1,
  "score": 0.9,
  "fileId": "文件标识",
  "fileName": "显示名称",
  "page": 10,
  "contentType": "image|table|table_row|text",
  "text": "保留 Markdown 和允许的 HTML 表格",
  "images": [
    {
      "path": "图3-3 井身结构图.png",
      "url": "/api/chat/multimodal/image?...",
      "alt": "图3-3 井身结构图"
    }
  ],
  "metadata": {}
}
```

### 5.2 图片提取

- 支持 Markdown `./images/...` 和 `images/...`。
- 支持 `image_path`、`imagePath`、`img_name`、`referenced_images` 和兼容的图片数组字段。
- 图片去重后保留原始顺序。
- `fileId` 优先使用远端 `source.file_id`，不使用显示文件名替代存储标识。
- 图片地址只指向主后端代理，不把内网远端地址暴露给浏览器。

### 5.3 图片代理

- 使用异步 HTTP 客户端和流式响应，不把整张大图重复复制到内存。
- 对知识库 ID、文件 ID、图片路径长度和父目录片段进行校验。
- 转发受控的 `Content-Type`、`Content-Length`、`ETag` 和缓存头。
- 明确区分远端 404、超时和服务不可用，前端显示占位错误而不破坏整个引用面板。

### 5.4 富内容渲染

- 主回答继续使用现有 Markdown 预览能力，但预览 ID 必须基于消息 ID 唯一。
- 右侧引用面板使用独立富内容组件，将 Markdown 转换后通过 DOMPurify 消毒，再渲染允许的表格标签。
- 允许 `table`、`thead`、`tbody`、`tr`、`th`、`td` 及安全的 `rowspan`、`colspan`；禁止脚本、事件属性和危险 URL。
- 表格放在横向滚动容器内，单元格可换行；图片使用稳定宽高约束、懒加载和点击查看原图。
- 文本中的 Markdown 图片和单独的图片列表去重，避免重复展示。

## 6. 图谱生成流水线

### 6.1 状态机

```text
queued -> copying -> building -> converting -> importing -> indexing -> completed
                                                            -> failed
任一运行阶段 -> cancelling -> cancelled
worker 重启时的运行任务 -> interrupted
failed/cancelled/interrupted -> retry -> 最近一个可安全重试的阶段
```

推荐进度区间：

- `queued`: 0%
- `copying`: 1%-8%
- `building`: 10%-70%，根据 GraphRAG 日志中的工作流步骤更新
- `converting`: 72%-78%
- `importing`: 80%-94%
- `indexing`: 95%-99%
- `completed`: 100%

进度只能单调增加。失败状态保存阶段、错误摘要、日志尾部和时间戳。

### 6.2 worker API

- `POST /jobs`：提交 `graphType` 和输入配置，立即返回 `202 + taskId`。
- `GET /jobs/{taskId}`：返回状态、阶段、进度、时间、产物、导入统计和错误。
- `POST /jobs/{taskId}/retry`：重试失败或中断任务。
- `POST /jobs/{taskId}/cancel`：请求取消正在运行的任务。
- 同一图谱类型只允许一个活动构建任务；重复提交返回现有任务或 `409`。

任务记录保存在 worker 挂载目录内的 SQLite 数据库。worker 启动时将未完成任务标记为 `interrupted`，允许用户重试，不谎报完成。

### 6.3 产物转换

- 构建前记录开始时间和输出目录快照，完成后只选择本任务产生的最新输出目录。
- 必须找到 `artifacts/create_final_relationships.parquet` 才能进入转换阶段。
- 校验 `source`、`target`、`description` 列，过滤空实体和空关系。
- 输出 UTF-8 CSV，列固定为 `h,r,t`，并记录行数、哈希和绝对产物路径。
- 转换失败不得清理输入文件；只有整条流水线成功后才按配置归档输入。

### 6.4 Neo4j 自动导入

- worker 调用主 API 的内部导入接口，传递任务 ID、图谱类型和受允许目录约束的 CSV 相对路径。
- 内部导入接口使用 `GRAPH_INTERNAL_TOKEN` 做服务间认证，不接受浏览器会话直接调用。
- 主 API 只允许读取挂载的 `indexing/ground_graph_fill` 和 `indexing_drill/drill_graph_fill`，拒绝绝对路径和父目录跳转。
- 主 API 使用专门的图谱导入服务，批量 `MERGE` 节点与关系；同一 CSV 重试不得增加重复节点或关系。
- 关系类型作为属性保存，避免将未校验文本拼入 Cypher 类型。
- 导入后为缺少 embedding 的节点分批补向量，并创建或验证向量索引。
- 只有 CSV 转换、Neo4j 导入、向量索引全部成功，任务才可标记 `completed`。

### 6.5 前端进度

- `GraphView` 提交任务后保存 task ID 并轮询状态。
- 展示总进度、当前阶段、耗时、文件数、关系数和失败原因。
- 页面刷新后恢复当前任务；切换图谱类型不会丢失任务。
- 失败时只显示失败，不得在 `else` 或 `catch` 分支提示成功。

## 7. 图谱增强问答

- `use_graph` 只依赖知识图谱开关和 Neo4j 可用状态，不错误依赖知识库开关。
- 实体抽取结果做去空、去重和规范化；抽取为空时使用原始问题做一次图谱向量查询回退。
- 相似度阈值、最大实体数、跳数和最大关系数来自配置或请求 meta，并有安全上限。
- 查询结果按相关性排序并对三元组去重。
- 提示词同时包含节点名称、节点可用属性、关系两端和关系描述，附稳定引用编号。
- 图谱不可用时记录结构化错误并继续普通问答，不让整个问答请求失败。

## 8. 并发与稳定性

### 8.1 资源隔离

- GraphRAG 构建在独立 worker 中运行，与问答 API 隔离。
- 主 API 为检索、模型推理、图谱导入分别设置有界并发限制。
- 同步模型、Milvus 和 Neo4j 调用通过受限线程执行，不直接占用事件循环。
- 全局执行器设置明确的 `max_workers`，不得使用无业务边界的默认执行器。

### 8.2 网络与数据库

- 多模态和 GraphRAG 服务使用复用连接的 HTTP 客户端，设置连接、读取和总超时。
- 所有代理请求处理客户端断开并及时关闭上游响应。
- 当前 SQLite 开启 WAL 和 busy timeout；生产多进程部署前通过 `DATABASE_URL` 切换 MySQL。
- SQLAlchemy session 必须按请求或任务创建并在 `finally` 中关闭。

### 8.3 部署

- 开发 compose 可以保留热更新。
- 增加生产配置，移除 `--reload`，设置健康检查、并发上限、优雅关闭和资源限制。
- 本地模型仍在 API 进程内时默认只启动一个 API worker，避免重复加载导致 GPU/内存耗尽。
- 模型服务外置后，API 才切换为多 worker 或多副本。

### 8.4 过载行为

- 达到重型任务并发上限时返回明确的 `429` 或 `503` 和可重试提示。
- 已接收的图谱任务必须可查询，不允许无记录丢失。
- 单个远端服务超时不能拖垮健康检查、静态资源和其他普通 API。

## 9. 自定义模型与密钥安全

### 9.1 用户模型数据

新增按用户归属的模型凭据记录，至少包含：`id`、`user_id`、`display_name`、`provider`、`model_name`、`api_base`、`encrypted_api_key`、`key_hint`、`key_version`、`last_used_at`、`created_at` 和 `updated_at`。

- 模型记录只能由所属用户读取、修改、选择和删除；超级管理员也不通过普通列表接口读取其他用户密钥。
- `display_name` 在同一用户内唯一，`last_used_at` 用于最近使用排序，删除当前模型后回退到系统默认模型。
- 内置模型仍使用现有模型 ID；自定义模型在聊天请求中只传不透明的 `user_model_id`。
- 后端根据当前登录用户校验记录所有权，运行时短暂解密 API Key 并构造模型客户端，不接受浏览器传入的 API Key 或任意 `api_base` 覆盖。

### 9.2 密钥生命周期

- 使用环境变量 `MODEL_CREDENTIAL_MASTER_KEY` 提供主密钥，通过经过验证的加密库进行认证加密；主密钥不得写入数据库、Git、前端构建产物或日志。
- API Key 只在新增或显式替换时进入请求体，数据库只保存密文；查询接口仅返回固定掩码和最多末四位提示，不返回密文或可恢复内容。
- 编辑模型时省略 API Key 表示保留旧值，删除模型时删除密文记录；序列化、异常和审计日志统一移除请求头、请求体密钥及部分密钥前缀。
- `src/utils/web_search_bocha.py` 改为读取 `BOCHA_API_KEY`；已经进入 Git 历史的旧密钥必须在供应商侧立即吊销并更换。是否重写共享 Git 历史作为单独运维任务处理，不在本次实现中自动执行。
- 自定义 `api_base` 默认只允许 HTTPS；开发环境例外需显式配置。解析主机后拒绝环回、链路本地、云元数据和未获准的内网地址，防止 SSRF。

### 9.3 API 与问答页交互

- 提供 `/api/chat/user-models` 的列表、新增、编辑、删除接口，以及模型连通性验证和选择接口。
- 模型连通性验证使用短超时和受限并发，失败只返回脱敏错误，不持久化明文。
- 知识问答顶部模型选择器同时显示内置模型和当前用户模型，最近使用优先；提供新增、编辑和删除入口。
- 新增/编辑使用模态框，API Key 输入框不可回显旧值；页面刷新后从后端恢复模型列表和最近使用状态。
- 前端不得把 API Key 放入 `localStorage`、`sessionStorage`、Pinia 持久化、URL、错误提示或分析事件。

## 10. 角色权限与原页用户管理

### 10.1 权限矩阵

| 能力 | `superadmin` | `admin` | `user` |
| --- | --- | --- | --- |
| 知识问答与个人自定义模型 | 允许 | 允许 | 允许 |
| 管理普通用户 | 允许 | 允许 | 禁止 |
| 管理管理员和角色 | 允许 | 禁止 | 禁止 |
| 普通知识库、多模态知识库、知识图谱、数据库、统计和系统设置 | 允许 | 禁止 | 禁止 |
| 在问答中选择或使用多模态知识库 | 允许 | 禁止 | 禁止 |

### 10.2 后端强制鉴权

- 所有知识库、多模态管理、图谱、数据库、统计和系统配置接口统一要求 `get_superadmin_user`。
- 聊天接口允许三种已登录角色使用；当非超级管理员提交知识库管理、多模态或图谱专属选项时，后端明确拒绝，不能只依赖前端隐藏。
- 管理员的用户列表只返回普通用户，创建时角色固定为 `user`，只能修改或删除普通用户；不能查看、创建、提升、降级或删除管理员和超级管理员。
- 超级管理员保留全部用户与角色管理能力。所有写操作记录操作者、目标用户、动作、结果和时间，但不记录密码、Token 或模型 API Key。
- 修复并启用前端路由守卫；启动时通过 `/me` 恢复服务端身份，页面元数据只负责用户体验，最终授权始终由后端执行。

### 10.3 导航与用户管理抽屉

- 主侧栏根据用户角色生成：三种角色都显示知识问答；管理员额外显示用户管理触发项；超级管理员显示完整功能。
- 用户管理不再注册为独立业务页面，也不通过路由跳转。点击侧栏入口后，在当前页面右侧打开固定宽度、移动端全宽的抽屉。
- 现有 `UserManagementComponent` 改造成可嵌入面板，移除路由耦合，在抽屉关闭后保持当前知识问答会话和滚动位置。
- 抽屉内包含搜索、分页、新增、编辑、启停或删除所需状态；管理员界面不显示角色提升控件，超级管理员可看到完整角色操作。
- 对无权限路由的直接访问跳转到知识问答并提示权限不足；退出登录后清空角色相关菜单、模型列表和抽屉状态。

## 11. 测试与验收

### 11.1 自动化测试

- 主题 store：初始化、持久化、DOM 属性和 Ant Design 配置。
- 富内容：Markdown 表格、HTML 合并单元格、脚本清理、图片去重和溢出样式。
- 多模态归一化：单图、多图数组、中文文件名、畸形 source、缺失 file ID、路径校验。
- 图谱任务：合法状态转换、单调进度、并发提交冲突、取消、失败、重试和重启中断。
- GraphRAG 转换：真实 Parquet schema 转为 `h,r,t`，空值过滤和确定性哈希。
- Neo4j 导入：批量写入、重复导入幂等、失败回滚或可重试、embedding 补全。
- 图谱问答：开关条件、实体回退、结果去重、上下文格式和图谱失败降级。
- 用户模型：所有权隔离、密钥加密往返、响应不含密钥、最近使用排序、删除回退、错误完全脱敏和 SSRF 拒绝。
- 角色权限：三角色逐接口矩阵、管理员列表过滤、越权修改拒绝、非超级管理员伪造多模态或图谱参数拒绝。
- 前端权限：角色菜单、路由守卫、用户管理抽屉保持当前页面、模型新增/切换/刷新恢复，且浏览器存储中不存在 API Key。

### 11.2 真实联调

- 使用远端 `http://10.16.33.2:8002/api/v1`。
- 选择 `钻井设计资料`，查询“井身结构设计图”。
- 右侧必须显示至少一张可打开的真实 PNG，并正确展示检索结果中的复杂 HTML 表格。
- 图片请求状态为 200、Content-Type 为 `image/png`，中文路径不乱码。
- 亮暗模式下分别检查问答、引用侧栏、知识库、图谱和多模态页面。
- 分别以三种角色登录核对菜单、直接 URL 访问和接口返回；管理员只能在抽屉中看到并管理普通用户。
- 新增一个 OpenAI 兼容自定义模型，验证、切换、问答、刷新恢复和删除；数据库、接口响应、前端存储和日志均不得出现明文 API Key。

### 11.3 图谱联调

- 使用小型输入完成一次完整任务，观察每个阶段并达到 100%。
- 验证生成 CSV 存在且列为 `h,r,t`。
- 验证 Neo4j 节点和关系数量增加；重复导入同一 CSV 后数量不重复增长。
- 开启知识图谱问答，回答中使用已导入关系，右侧图谱引用可查看。

### 11.4 并发验收

- 图谱构建期间健康检查持续成功，普通列表与图片接口保持响应。
- 并发图片代理请求不出现持续内存增长或连接泄漏。
- 并发问答超过重型任务上限时有序排队或返回明确过载状态，不出现进程退出。
- 测试结束后检查容器重启次数、5xx、数据库锁错误、超时和内存曲线。

## 12. 实施顺序与提交边界

1. `fix(security): remove embedded secrets and enforce role matrix`
2. `feat(users): embed role-aware user management drawer`
3. `feat(chat): add encrypted per-user model credentials and selector`
4. `feat(theme): add persistent light and dark themes`
5. `fix(chat): render safe tables and responsive images in references`
6. `fix(multimodal): normalize and proxy retrieved images reliably`
7. `feat(graph): add durable graph build pipeline and neo4j import`
8. `fix(graph-qa): correct graph retrieval and prompt context`
9. `perf(server): isolate blocking work and bound concurrency`
10. `test(e2e): verify roles models multimodal graph and load scenarios`

每一步完成后都执行对应单元测试、前端构建和必要的 Docker/浏览器验收，不能用后续步骤掩盖当前步骤失败。

## 13. 非目标

- 本轮不重写整个前端设计系统。
- 本轮不把所有业务数据库强制迁移到 MySQL，但会提供可配置连接和迁移方向。
- 本轮不直接引入 Redis/Celery；任务服务接口必须保持可替换性。
- 本轮不承诺在本地模型仍嵌入 API 进程时盲目增加 Uvicorn worker 数。
