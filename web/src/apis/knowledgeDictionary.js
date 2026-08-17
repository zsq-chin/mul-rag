import { apiGet, apiPost, apiPatch, apiDelete, apiRequest } from './base'

/**
 * 知识字典 API 客户端（/api/knowledge-dictionaries）
 * 文档：docs/superpowers/specs/2026-08-16-knowledge-dictionary-generation-design.md §13
 */

export const knowledgeDictionaryApi = {
  // ---- 字典与版本 ----
  listDictionaries({ keyword = '', status = '', domain = '', page = 1, pageSize = 20 } = {}) {
    const params = new URLSearchParams({ keyword, status, domain, page, page_size: pageSize })
    return apiGet(`/api/knowledge-dictionaries?${params}`, {}, true)
  },
  createDictionary(payload) {
    return apiPost('/api/knowledge-dictionaries', payload, {}, true)
  },
  getDictionary(dictionaryId) {
    return apiGet(`/api/knowledge-dictionaries/${dictionaryId}`, {}, true)
  },
  updateDictionary(dictionaryId, payload) {
    return apiPatch(`/api/knowledge-dictionaries/${dictionaryId}`, payload, {}, true)
  },
  deleteDictionary(dictionaryId) {
    return apiDelete(`/api/knowledge-dictionaries/${dictionaryId}`, {}, true)
  },
  listVersions(dictionaryId) {
    return apiGet(`/api/knowledge-dictionaries/${dictionaryId}/versions`, {}, true)
  },
  getVersion(dictionaryId, versionId) {
    return apiGet(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}`, {}, true)
  },
  publishVersion(dictionaryId, versionId) {
    return apiPost(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/publish`, {}, {}, true)
  },
  withdrawVersion(dictionaryId, versionId) {
    return apiPost(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/withdraw`, {}, {}, true)
  },

  // ---- 上传来源 ----
  uploadSource(file) {
    const formData = new FormData()
    formData.append('file', file)
    return apiRequest('/api/knowledge-dictionaries/upload', { method: 'POST', body: formData }, true)
  },

  // ---- 生成与任务 ----
  generate(payload) {
    return apiPost('/api/knowledge-dictionaries/generate', payload, {}, true)
  },
  getJob(jobId) {
    return apiGet(`/api/knowledge-dictionaries/jobs/${jobId}`, {}, true)
  },
  cancelJob(jobId) {
    return apiPost(`/api/knowledge-dictionaries/jobs/${jobId}/cancel`, {}, {}, true)
  },
  retryJob(jobId) {
    return apiPost(`/api/knowledge-dictionaries/jobs/${jobId}/retry`, {}, {}, true)
  },
  seedImport() {
    return apiPost('/api/knowledge-dictionaries/seed-import', {}, {}, true)
  },

  // ---- 条目与审核 ----
  listEntries(dictionaryId, versionId, { category = '', reviewStatus = '', keyword = '', sourceFile = '', minConfidence = null, missingFields = false, conflictOnly = false, page = 1, pageSize = 20 } = {}) {
    const params = new URLSearchParams({
      category, review_status: reviewStatus, keyword, source_file: sourceFile,
      missing_fields: missingFields ? 'true' : 'false', conflict_only: conflictOnly ? 'true' : 'false',
      page, page_size: pageSize,
    })
    if (minConfidence !== null && minConfidence !== undefined && minConfidence !== '') {
      params.set('min_confidence', minConfidence)
    }
    return apiGet(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/entries?${params}`, {}, true)
  },
  createEntry(dictionaryId, versionId, payload) {
    return apiPost(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/entries`, payload, {}, true)
  },
  updateEntry(dictionaryId, versionId, entryId, payload) {
    return apiPatch(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/entries/${entryId}`, payload, {}, true)
  },
  deleteEntry(dictionaryId, versionId, entryId) {
    return apiDelete(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/entries/${entryId}`, {}, true)
  },
  getEvidences(dictionaryId, versionId, entryId) {
    return apiGet(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/entries/${entryId}/evidences`, {}, true)
  },
  reviewEntry(dictionaryId, versionId, entryId, payload) {
    return apiPost(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/entries/${entryId}/review`, payload, {}, true)
  },
  batchReview(dictionaryId, versionId, payload) {
    return apiPost(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/entries/batch-review`, payload, {}, true)
  },
  mergeEntries(dictionaryId, versionId, payload) {
    return apiPost(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/entries/merge`, payload, {}, true)
  },
  mergeSuggestions(dictionaryId, versionId, limit = 10) {
    return apiGet(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/entries/merge-suggestions?limit=${limit}`, {}, true)
  },

  // ---- 向量索引与检索 ----
  buildIndex(dictionaryId, versionId) {
    return apiPost(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/index`, {}, {}, true)
  },
  indexStatus(dictionaryId, versionId) {
    return apiGet(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/index-status`, {}, true)
  },
  search(payload) {
    return apiPost('/api/knowledge-dictionaries/search', payload, {}, true)
  },

  // ---- 导出 ----
  exportVersion(dictionaryId, versionId, format = 'xlsx') {
    return apiRequest(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/export?format=${format}`, {}, true)
  },
}

/** 下载导出文件（处理中文文件名响应头）。 */
export async function downloadDictionaryExport(dictionaryId, versionId, format = 'xlsx') {
  const response = await fetch(`/api/knowledge-dictionaries/${dictionaryId}/versions/${versionId}/export?format=${format}`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('user_token') || ''}` },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail?.message || `导出失败（HTTP ${response.status}）`)
  }
  const blob = await response.blob()
  const disposition = response.headers.get('content-disposition') || ''
  let filename = `dictionary-${dictionaryId}-V${versionId}.${format}`
  const starMatch = disposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (starMatch) {
    filename = decodeURIComponent(starMatch[1])
  }
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
  return filename
}
