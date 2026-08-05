# 问答统计模块说明

## 概览

问答统计页面（`/statistics`，仅 **superadmin** 可见）已经基于**真实问答数据**实现，不再使用模拟数据。

数据来源：

- `chat_records` 表：用户在问答页面保存的对话记录（含 `messages`），用于统计提问/回答数、每日趋势、热门问题、活跃用户。
- `thread` 表：对话线程，用于统计线程数和按智能体分布。

页面结构：

1. 顶部统计卡片：总提问数、总回答数、会话记录、活跃用户、对话线程。
2. 图表区：
   - 每日问答趋势（近 14 天折线/柱状图）
   - 按智能体分布（饼图）
   - 热门问题 TOP10（真实聚合）
   - 活跃用户排行（柱状图）
3. 社区互动区：热门提问榜 + 问题全库 + 讨论 + 专家求助。

## 后端接口（server/routers/statistics_router.py）

所有接口均需 superadmin 权限（JWT + `get_superadmin_user`）。

### 1. 统计数据总览
```
GET /api/statistics/overview?days=14
```
返回：
```json
{
  "status": "success",
  "data": {
    "totals": { "questions": 12, "answers": 10, "conversations": 4, "threads": 3, "active_users": 2 },
    "daily_trend": [ { "date": "2026-08-01", "questions": 3, "answers": 2, "conversations": 1 } ],
    "agent_distribution": [ { "name": "默认智能体", "value": 3 } ],
    "hot_questions": [ { "question": "xxx", "count": 5 } ],
    "top_users": [ { "user_id": 1, "username": "张三", "records": 4, "questions": 6 } ],
    "recent_activity": [ { "time": "2026-08-01 10:00", "username": "张三", "title": "对话标题" } ]
  }
}
```

### 2. 同步热门问题到社区
```
POST /api/statistics/sync-questions
```
把真实对话中的高频问题按标题 upsert 进 `questions` 表（category 记为「用户提问」），供社区讨论/求助使用；同时清理早期 seed 的演示数据（无讨论/求助关联时）。
```json
{ "status": "success", "data": { "synced": 3, "updated": 5, "removed": 6, "total": 8 } }
```

### 3. 热门问题列表（社区）
```
GET /api/statistics/top-questions?limit=10
```
返回 `questions` 表按提问次数倒序，含 `discussionCount` / `helpCount`。

### 4. 讨论与求助
```
GET  /api/statistics/questions/{questionId}/discussions
POST /api/statistics/questions/{questionId}/discussions     body: { "content": "..." }
POST /api/statistics/help-requests                          body: { "questionId": 1, "title": "...", "description": "...", "email": "..." }
```

## 前端（web/src/apis/statistics_api.js）

所有调用统一带 `requiresAuth = true`。

| 方法 | 接口 |
|---|---|
| `getStatisticsOverview({days})` | GET `/api/statistics/overview` |
| `syncQuestions()` | POST `/api/statistics/sync-questions` |
| `getTopQuestions({limit})` | GET `/api/statistics/top-questions` |
| `getQuestionDiscussions(id)` | GET `/api/statistics/questions/{id}/discussions` |
| `createDiscussion(id, {content})` | POST `/api/statistics/questions/{id}/discussions` |
| `createHelpRequest(payload)` | POST `/api/statistics/help-requests` |

`AnswerStatistics.vue` 挂载时依次执行：`syncQuestions()` → `getTopQuestions()` → `getStatisticsOverview()` → 渲染 echarts。

## 聚合逻辑

真实数据聚合的纯函数在 `server/services/statistics_aggregation.py`（不依赖数据库，可独立单测）：

- `normalize_question`：归一化问题文本，用于热门问题去重。
- `iter_conv_messages`：解析对话记录 JSON，容忍 messages/history 两种结构。
- `aggregate_records`：统计提问/回答/会话数、按日期计数、热门问题、每用户统计。
- `build_daily_trend`：补齐最近 N 天的趋势序列。
- `top_users`：合并用户名并按提问数排序。

测试：`test/test_statistics_aggregation.py`（13 个用例）。
