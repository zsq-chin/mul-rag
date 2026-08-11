/**
 * 本机功能 API 聚合模块。
 * 全部调用 requiresAuth=true（除公共健康检查外）。
 * 后端统一返回 {status, data, message}，列表接口 data 为 {items, page, page_size, total}。
 */
import { apiGet, apiPut, apiPatch, apiPost, apiDelete } from './base'

/** 问答反馈 */
export const feedbackApi = {
  upsert: (messageId, payload) =>
    apiPut(`/api/feedback/messages/${messageId}`, payload, {}, true),
  get: (messageId) =>
    apiGet(`/api/feedback/messages/${messageId}`, {}, true),
  remove: (messageId) =>
    apiDelete(`/api/feedback/messages/${messageId}`, {}, true),
  mine: (page = 1, pageSize = 20) =>
    apiGet(`/api/feedback/mine?page=${page}&page_size=${pageSize}`, {}, true),
  summary: () => apiGet('/api/feedback/summary', {}, true),
}
