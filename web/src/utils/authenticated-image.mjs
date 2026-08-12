const isSameOrigin = (source, origin) => {
  if (!source) return false
  if (!/^[a-z][a-z\d+.-]*:/i.test(source) && !source.startsWith('//')) return true
  if (!origin) return false

  try {
    return new URL(source, origin).origin === origin
  } catch {
    return false
  }
}

// 会话级 Blob 缓存：同一 (URL, token) 的图片（320px 缩略图）在翻页/重渲染时
// 不重复下载。后端 D1/D2 已下发 ETag + Cache-Control（private, max-age），
// 浏览器 HTTP 缓存负责 304 重校验；此内存缓存作为快路径避免重复网络往返。
const blobCache = new Map() // key: `${source}\u0000${token}` -> Blob
const MAX_CACHE_ENTRIES = 300

const cacheKey = (source, token) => `${source}\u0000${token || ''}`

const setCache = (key, blob) => {
  blobCache.delete(key)
  blobCache.set(key, blob)
  if (blobCache.size > MAX_CACHE_ENTRIES) {
    const oldest = blobCache.keys().next().value
    blobCache.delete(oldest)
  }
}

// 同步命中检查：组件可在进入 loading 态前直接拿到缓存，避免闪烁。
export const peekAuthenticatedBlob = (source, token) => blobCache.get(cacheKey(source, token)) || null

export const fetchAuthenticatedBlob = async (
  source,
  token,
  fetchImpl = fetch,
  signal,
  origin = globalThis.location?.origin,
) => {
  const key = cacheKey(source, token)
  const cached = blobCache.get(key)
  if (cached) return cached

  const headers = token && isSameOrigin(source, origin)
    ? { Authorization: `Bearer ${token}` }
    : {}
  const response = await fetchImpl(source, { headers, signal })
  if (!response.ok) {
    if (response.status === 304 && cached) return cached
    throw new Error(`Image request failed (${response.status})`)
  }
  const blob = await response.blob()
  setCache(key, blob)
  return blob
}

// 测试/隔离用：清空会话缓存（调用方自行决定时机）。
export const clearAuthenticatedBlobCache = () => {
  blobCache.clear()
}
