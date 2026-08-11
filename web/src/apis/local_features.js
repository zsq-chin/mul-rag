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
  versions: (dbId, fileId) =>
    apiGet(`/api/governance/databases/${dbId}/documents/${fileId}/versions`, {}, true),
  snapshot: (dbId, fileId, note) =>
    apiPost(`/api/governance/databases/${dbId}/documents/${fileId}/versions/snapshot`, { note }, {}, true),
  versionDownloadUrl: (dbId, fileId, version) =>
    `/api/governance/databases/${dbId}/documents/${fileId}/versions/${version}/download`,
}

/** 问答测试集（仅 superadmin；只做用例管理，不调用模型） */
export const evaluationApi = {
  suites: (params) => {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== ''),
      ),
    ).toString()
    return apiGet(`/api/evaluation/suites?${qs}`, {}, true)
  },
  suite: (suiteId) => apiGet(`/api/evaluation/suites/${suiteId}`, {}, true),
  createSuite: (payload) => apiPost('/api/evaluation/suites', payload, {}, true),
  updateSuite: (suiteId, payload) =>
    apiPatch(`/api/evaluation/suites/${suiteId}`, payload, {}, true),
  deleteSuite: (suiteId) => apiDelete(`/api/evaluation/suites/${suiteId}`, {}, true),
  cases: (suiteId, params) => {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== ''),
      ),
    ).toString()
    return apiGet(`/api/evaluation/suites/${suiteId}/cases?${qs}`, {}, true)
  },
  createCase: (suiteId, payload) =>
    apiPost(`/api/evaluation/suites/${suiteId}/cases`, payload, {}, true),
  updateCase: (suiteId, caseId, payload) =>
    apiPatch(`/api/evaluation/suites/${suiteId}/cases/${caseId}`, payload, {}, true),
  deleteCase: (suiteId, caseId) =>
    apiDelete(`/api/evaluation/suites/${suiteId}/cases/${caseId}`, {}, true),
  importUrl: (suiteId, format) =>
    `/api/evaluation/suites/${suiteId}/import?format=${format}`,
  exportUrl: (suiteId, format) =>
    `/api/evaluation/suites/${suiteId}/export?format=${format}`,
}

/** 统一操作审计（仅 superadmin） */
export const auditApi = {
  events: (params) => {
    const qs = new URLSearchParams(
      Object.fromEntries(
        Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== ''),
      ),
    ).toString()
    return apiGet(`/api/audit/events?${qs}`, {}, true)
  },
  event: (eventId) => apiGet(`/api/audit/events/${eventId}`, {}, true),
  actions: () => apiGet('/api/audit/actions', {}, true),
}

/**
 * 带认证头的 multipart 上传（导入用例用）。
 * 返回解析后的 JSON；非 2xx 抛错。
 */
export async function uploadAuthenticated(url, file) {
  const userStore = useUserStore()
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(url, { method: 'POST', headers: userStore.getAuthHeaders(), body: form })
  if (!res.ok) {
    let msg = `上传失败: ${res.status}`
    try {
      const body = await res.json()
      msg = body.detail || body.message || msg
    } catch (e) {
      // 忽略非 JSON 错误体
    }
    throw new Error(msg)
  }
  return res.json()
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
