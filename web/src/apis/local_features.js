/**
 * 本机功能 API 聚合模块。
 * 全部调用 requiresAuth=true（除公共健康检查外）。
 * 后端统一返回 {status, data, message}，列表接口 data 为 {items, page, page_size, total}。
 */
import { apiGet, apiPut, apiPatch, apiPost, apiDelete } from './base'
import { useUserStore } from '@/stores/user'

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

/** 知识治理：分级分类元数据 + 受控下载 */
export const governanceApi = {
  list: (dbId, params) => {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== ''),
      ),
    ).toString()
    return apiGet(`/api/governance/databases/${dbId}/documents?${qs}`, {}, true)
  },
  get: (dbId, fileId) =>
    apiGet(`/api/governance/databases/${dbId}/documents/${fileId}`, {}, true),
  update: (dbId, fileId, payload) =>
    apiPatch(`/api/governance/databases/${dbId}/documents/${fileId}`, payload, {}, true),
  sync: (dbId) => apiPost(`/api/governance/databases/${dbId}/sync`, {}, {}, true),
  downloadUrl: (dbId, fileId) =>
    `/api/governance/databases/${dbId}/documents/${fileId}/download`,
  exportUrl: (dbId, format = 'xlsx') =>
    `/api/governance/databases/${dbId}/export?format=${format}`,
}

/**
 * 受控下载/导出：携带认证头请求文件，触发浏览器保存。
 * 返回 { ok, message }；403/404/500 时返回可展示的错误信息。
 */
export async function downloadAuthenticated(url, filename) {
  const userStore = useUserStore()
  const res = await fetch(url, { headers: userStore.getAuthHeaders() })
  if (!res.ok) {
    let msg = `下载失败: ${res.status}`
    try {
      const body = await res.json()
      msg = body.detail || body.message || msg
    } catch (e) {
      // 忽略非 JSON 错误体
    }
    return { ok: false, message: msg }
  }
  const blob = await res.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objectUrl)
  return { ok: true, message: '' }
}
