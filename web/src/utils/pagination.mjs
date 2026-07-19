export const clampPage = (page, total, pageSize) => {
  const safePageSize = Math.max(1, Number(pageSize) || 1)
  const maxPage = Math.max(1, Math.ceil((Number(total) || 0) / safePageSize))
  const nextPage = Math.max(1, Number(page) || 1)
  return Math.min(nextPage, maxPage)
}

export const paginateItems = (items, page, pageSize) => {
  const list = Array.isArray(items) ? items : []
  const safePageSize = Math.max(1, Number(pageSize) || 1)
  const safePage = clampPage(page, list.length, safePageSize)
  const start = (safePage - 1) * safePageSize
  return list.slice(start, start + safePageSize)
}
