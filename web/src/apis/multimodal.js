import axios from 'axios'
import { useUserStore } from '@/stores/user'

const trimTrailingSlash = (value) => String(value || '').replace(/\/+$/, '')
const trimLeadingSlash = (value) => String(value || '').replace(/^\/+/, '')

export const MULTIMODAL_API_BASE_URL = trimTrailingSlash(
  import.meta.env.VITE_MULTIMODAL_API_URL || '/api/multimodal'
)

export const getApiBaseUrl = () => MULTIMODAL_API_BASE_URL

export const buildMultimodalAssetUrl = (path, params = {}) => {
  const url = `${MULTIMODAL_API_BASE_URL}/${trimLeadingSlash(path)}`
  const searchParams = new URLSearchParams()

  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      searchParams.append(key, value)
    }
  })

  const queryString = searchParams.toString()
  return queryString ? `${url}?${queryString}` : url
}

const ragRequest = axios.create({
  baseURL: MULTIMODAL_API_BASE_URL,
  timeout: Number(import.meta.env.VITE_MULTIMODAL_TIMEOUT || 60000),
})

ragRequest.interceptors.request.use((config) => {
  const userStore = useUserStore()
  if (userStore.isLoggedIn) {
    config.headers = {
      ...config.headers,
      ...userStore.getAuthHeaders(),
    }
  }
  return config
})

ragRequest.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const data = error.response?.data
    error.message = data?.detail || data?.message || data?.error?.message || error.message
    console.error('Multimodal RAG Backend Error:', error)
    return Promise.reject(error)
  }
)

// ================= Health =================
export const checkHealth = () => ragRequest.get('/health')

// ================= Knowledge Base =================
export const getKbList = () => ragRequest.get('/kb/list')

export const createKb = (data) => ragRequest.post('/kb/create', data)

export const deleteKb = (data) => ragRequest.post('/kb/delete', data)

export const getKbFiles = (params) => ragRequest.get('/kb/files', { params })

export const getKbImages = (params) => ragRequest.get('/kb/images', { params })

export const getAllKbImages = (params) => ragRequest.get('/kb/images/all', { params })

export const getFileOriginal = (params) => ragRequest.get('/kb/file/original', { params })

export const getFileOriginalUrl = (params) => buildMultimodalAssetUrl('/kb/file/original', params)

export const getFileDataFrame = (params) => ragRequest.get('/kb/file/dataframe', { params })

export const getFileContent = (params) => ragRequest.get('/kb/file/content', { params })

export const deleteKbFile = (data) => ragRequest.post('/kb/file/delete', data)

export const updateKbImage = (data) => ragRequest.post('/kb/image/update', data)

export const updateKbImages = (data) => ragRequest.post('/kb/images/update', data)

export const getFileManagerWells = (params = {}) => ragRequest.get('/file-manager/wells', { params })

// ================= PDF / File =================
export const uploadFile = (formData) => {
  return ragRequest.post('/pdf/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export const parseFile = (data) => ragRequest.post('/pdf/parse', data)

export const getFileStatus = (params) => ragRequest.get('/pdf/status', { params })

export const getPdfImages = (params) => ragRequest.get('/pdf/images', { params })

export const getPdfImagesList = (params) => ragRequest.get('/pdf/images_list', { params })

export const getPdfImageSummaries = (params) => ragRequest.get('/pdf/image_summaries', { params })

export const updateImageSummaries = (data) => ragRequest.post('/pdf/image_summaries/update', data)

export const getPdfChunk = (params) => ragRequest.get('/pdf/chunk', { params })

export const getPdfPageUrl = (params) => buildMultimodalAssetUrl('/pdf/page', params)

export const getPdfImageUrl = (params) => buildMultimodalAssetUrl('/pdf/images', params)

// ================= Index & Search =================
export const buildIndex = (data) => ragRequest.post('/index/build', data)

export const searchKb = (data) => ragRequest.post('/index/search', data)

export const getIndexChunks = (params) => ragRequest.get('/index/chunks', { params })

export const getIndexChunksStats = (params) => ragRequest.get('/index/chunks/stats', { params })

export const deleteIndex = (data) => ragRequest.post('/index/delete', data)

// ================= Extraction =================
export const startExtraction = (formData) => {
  return ragRequest.post('/extraction/extract', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export const getExtractionStatus = (params) => ragRequest.get('/extraction/status', { params })

export const getExtractionContent = (params) => ragRequest.get('/extraction/content', { params })

export const checkExtractionFilename = (params) => ragRequest.get('/extraction/check_filename', { params })

export const updateExtractionResult = (data) => ragRequest.post('/extraction/update_result', data)

export const getExtractionImageUrl = (params) => buildMultimodalAssetUrl('/extraction/image', params)

// ================= Preprocess =================
export const getPreprocessMethods = () => ragRequest.get('/preprocess/methods')

export const uploadPreprocessFile = (formData) => {
  return ragRequest.post('/preprocess/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export const runPreprocessWorkbench = (data) => ragRequest.post('/preprocess/workbench/run', data)

export const runGroupedPreprocessWorkbench = (data) => ragRequest.post('/preprocess/workbench/grouped/run', data)

export const storePreprocessWorkbench = (data) => ragRequest.post('/preprocess/workbench/store', data)

export const getPreprocessWorkbenchDataFrame = (params) => {
  return ragRequest.get('/preprocess/workbench/dataframe', { params })
}

export const getPreprocessWorkbenchDownloadUrl = (params) => {
  return buildMultimodalAssetUrl('/preprocess/workbench/download', params)
}

export const getPreprocessArtifactDownloadUrl = (params) => {
  return buildMultimodalAssetUrl('/preprocess/workbench/artifact/download', params)
}

export const runPreprocess = (data) => ragRequest.post('/preprocess/run', data)

export const getPreprocessReport = (params) => ragRequest.get('/preprocess/report', { params })

export const getPreprocessDataFrame = (params) => ragRequest.get('/preprocess/dataframe', { params })

// ================= Structured DB =================
export const getStructuredDbSupported = () => ragRequest.get('/structured-db/supported')

export const getStructuredDbConnections = () => ragRequest.get('/structured-db/connections')

export const connectStructuredDb = (data) => ragRequest.post('/structured-db/connect', data)

export const disconnectStructuredDb = (data) => ragRequest.post('/structured-db/disconnect', data)

export const getStructuredDbSchema = (params) => ragRequest.get('/structured-db/schema', { params })

export const getStructuredDbTable = (params) => ragRequest.get('/structured-db/table', { params })

export const queryStructuredDb = (data) => ragRequest.post('/structured-db/query', data)

// ================= Query / Chat =================
export const unifiedQuery = (data) => ragRequest.post('/query', data)

export const multimodalChat = (data) => ragRequest.post('/chat', data)

export const clearMultimodalChat = (data = {}) => ragRequest.post('/chat/clear', data)
