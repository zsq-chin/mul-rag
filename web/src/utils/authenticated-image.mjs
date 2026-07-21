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

export const fetchAuthenticatedBlob = async (
  source,
  token,
  fetchImpl = fetch,
  signal,
  origin = globalThis.location?.origin,
) => {
  const headers = token && isSameOrigin(source, origin)
    ? { Authorization: `Bearer ${token}` }
    : {}
  const response = await fetchImpl(source, { headers, signal })
  if (!response.ok) {
    throw new Error(`Image request failed (${response.status})`)
  }
  return response.blob()
}
