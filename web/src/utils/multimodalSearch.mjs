const toSourceObject = (value) => {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value
  }
  if (typeof value !== 'string' || !value.trim()) {
    return {}
  }

  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch {
    return {}
  }
}

const toDisplayValue = (value) => {
  if (value === undefined || value === null || value === '') return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)

  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

export const parseSearchSource = (item = {}) => ({
  ...toSourceObject(item.metadata),
  ...toSourceObject(item.source),
})

export const getSearchResultFileId = (item = {}) => {
  const source = parseSearchSource(item)
  return toDisplayValue(
    item.fileId ||
    item.file_id ||
    item.entity_key ||
    source.file_id ||
    source.fileId
  )
}

export const getSearchResultType = (item = {}) => {
  const source = parseSearchSource(item)
  const type = toDisplayValue(item.contentType || source.type)
  if (type === 'image') return '图像片段'
  if (type === 'table') return '表格'
  if (type === 'table_row') return '表格行'
  return type
}

export const getSearchResultSourceRows = (item = {}) => {
  const source = parseSearchSource(item)
  const file = getSearchResultFileId(item)
  const page = toDisplayValue(
    item.page ??
    source.page ??
    source.page_num ??
    source.source_page_num
  )
  const type = getSearchResultType(item)
  const headers = ['Header 1', 'Header 2', 'Header 3']
    .map((key) => toDisplayValue(source[key]))
    .filter(Boolean)
    .join(' / ')
  const imagePath = toDisplayValue(
    source.image_path ||
    source.img_name ||
    source.imagePath
  )

  return [
    { label: '文件', value: file },
    { label: '页码', value: page },
    { label: '类型', value: type },
    { label: '章节', value: headers },
    { label: '图片', value: imagePath },
  ].filter((row) => row.value)
}
