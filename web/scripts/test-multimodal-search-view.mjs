import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const viewUrl = new URL('../src/views/MultimodalKbView.vue', import.meta.url)
const source = await readFile(viewUrl, 'utf8')

assert.doesNotMatch(source, /class="header-section"/)
assert.doesNotMatch(source, /key="remoteQa"/)
assert.doesNotMatch(source, /远端问答/)
assert.doesNotMatch(source, /remoteQa[A-Z]/)
assert.match(source, /key="search" tab="[^"]*多模态检索/)
assert.match(source, /getSearchResultSourceRows/)
assert.match(source, /const fileId = getSearchResultFileId\(item\)/)
assert.match(source, /class="search-result-source"/)
assert.match(source, /loading="lazy"/)
assert.match(source, /decoding="async"/)

console.log('multimodal search view structure tests passed')
