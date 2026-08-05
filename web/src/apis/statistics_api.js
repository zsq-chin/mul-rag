import { apiGet, apiPost } from './base'

/**
 * 问答统计 API 模块
 * 对应后端 statistics_router.py 的接口。
 * 注意：所有接口均要求 superadmin 权限，调用时一律带上认证头。
 */

// 1. 获取统计数据总览（真实问答数据聚合）
// 对应后端: GET /api/statistics/overview?days=14
export const getStatisticsOverview = (params = {}) => {
  const days = params.days || 14
  return apiGet(`/api/statistics/overview?days=${days}`, {}, true)
}

// 2. 把真实热门问题同步进社区 questions 表
// 对应后端: POST /api/statistics/sync-questions
export const syncQuestions = () => {
  return apiPost('/api/statistics/sync-questions', {}, {}, true)
}

// 3. 获取热门问题排行榜（社区板块，由 sync-questions 填充）
// 对应后端: GET /api/statistics/top-questions
export const getTopQuestions = (params = {}) => {
  const limit = params.limit || 10
  return apiGet(`/api/statistics/top-questions?limit=${limit}`, {}, true)
}

// 4. 获取某个问题的讨论列表
// 对应后端: GET /api/statistics/questions/{questionId}/discussions
export const getQuestionDiscussions = (questionId) => {
  return apiGet(`/api/statistics/questions/${questionId}/discussions`, {}, true)
}

// 5. 发布讨论/评论
// 对应后端: POST /api/statistics/questions/{questionId}/discussions
export const createDiscussion = (questionId, data) => {
  return apiPost(
    `/api/statistics/questions/${questionId}/discussions`,
    data,
    {},
    true // 需要认证
  )
}

// 6. 发布求助
// 对应后端: POST /api/statistics/help-requests
export const createHelpRequest = (data) => {
  return apiPost(
    `/api/statistics/help-requests`,
    data,
    {},
    true // 需要认证
  )
}
