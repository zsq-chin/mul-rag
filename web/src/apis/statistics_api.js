import { apiGet, apiPost } from './base'

/**
 * 问答统计社区 API 模块
 * 对应后端 statistics_router.py 的接口
 */

// 1. 获取热门问题排行榜
// 对应后端: GET /api/statistics/top-questions
export const getTopQuestions = (params = {}) => {
  // 处理参数，默认 limit 为 10
  const limit = params.limit || 10
  return apiGet(`/api/statistics/top-questions?limit=${limit}`)
}

// 2. 获取某个问题的讨论列表
// 对应后端: GET /api/statistics/questions/{questionId}/discussions
export const getQuestionDiscussions = (questionId) => {
  return apiGet(`/api/statistics/questions/${questionId}/discussions`)
}

// 3. 发布讨论/评论
// 对应后端: POST /api/statistics/questions/{questionId}/discussions
// requiresAuth = true (第三个参数)，表示需要登录才能评论
export const createDiscussion = (questionId, data) => {
  return apiPost(
    `/api/statistics/questions/${questionId}/discussions`, 
    data, 
    {}, 
    true // 需要认证
  )
}

// 4. 发布求助
// 对应后端: POST /api/statistics/help-requests
export const createHelpRequest = (data) => {
  return apiPost(
    `/api/statistics/help-requests`, 
    data, 
    {}, 
    true // 需要认证
  )
}