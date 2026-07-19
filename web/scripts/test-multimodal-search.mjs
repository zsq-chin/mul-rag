import assert from 'node:assert/strict'
import {
  getSearchResultFileId,
  getSearchResultSourceRows,
  getSearchResultType,
  parseSearchSource,
} from '../src/utils/multimodalSearch.mjs'

const imageResult = {
  entity_key: '丰页1-2-A14HF-钻井工程设计',
  source: JSON.stringify({
    file_id: '丰页1-2-A14HF-钻井工程设计',
    page: 10,
    type: 'image',
    'Header 1': '井身结构设计',
    'Header 2': '井身结构示意图',
    image_path: '图3-3 井身结构图.png',
  }),
}

assert.deepEqual(parseSearchSource(imageResult), {
  file_id: '丰页1-2-A14HF-钻井工程设计',
  page: 10,
  type: 'image',
  'Header 1': '井身结构设计',
  'Header 2': '井身结构示意图',
  image_path: '图3-3 井身结构图.png',
})
assert.equal(getSearchResultFileId(imageResult), '丰页1-2-A14HF-钻井工程设计')
assert.equal(getSearchResultType(imageResult), '图像片段')
assert.deepEqual(getSearchResultSourceRows(imageResult), [
  { label: '文件', value: '丰页1-2-A14HF-钻井工程设计' },
  { label: '页码', value: '10' },
  { label: '类型', value: '图像片段' },
  { label: '章节', value: '井身结构设计 / 井身结构示意图' },
  { label: '图片', value: '图3-3 井身结构图.png' },
])

const malformedResult = {
  entity_key: 'fallback-file',
  source: '{broken json',
}

assert.doesNotThrow(() => parseSearchSource(malformedResult))
assert.deepEqual(parseSearchSource(malformedResult), {})
assert.equal(getSearchResultFileId(malformedResult), 'fallback-file')
assert.deepEqual(getSearchResultSourceRows(malformedResult), [
  { label: '文件', value: 'fallback-file' },
])

const metadataResult = {
  fileId: 'metadata-file',
  metadata: {
    source_page_num: 7,
    type: 'text',
    'Header 1': '钻井液设计',
  },
}

assert.deepEqual(getSearchResultSourceRows(metadataResult), [
  { label: '文件', value: 'metadata-file' },
  { label: '页码', value: '7' },
  { label: '类型', value: 'text' },
  { label: '章节', value: '钻井液设计' },
])

console.log('multimodal search metadata tests passed')
