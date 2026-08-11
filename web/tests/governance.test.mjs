import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const localFeatures = readFileSync(
  new URL('../src/apis/local_features.js', import.meta.url),
  'utf8',
)
const panel = readFileSync(
  new URL('../src/components/KnowledgeGovernancePanel.vue', import.meta.url),
  'utf8',
)
const dbInfoView = readFileSync(
  new URL('../src/views/DataBaseInfoView.vue', import.meta.url),
  'utf8',
)

test('governance API calls carry auth and hit the right endpoints', () => {
  assert.match(localFeatures, /apiGet\(`\/api\/governance\/databases\/\$\{dbId\}\/documents\?/)
  assert.match(localFeatures, /apiGet\(`\/api\/governance\/databases\/\$\{dbId\}\/documents\/\$\{fileId\}`, \{\}, true\)/)
  assert.match(localFeatures, /apiPatch\(`\/api\/governance\/databases\/\$\{dbId\}\/documents\/\$\{fileId\}`, payload, \{\}, true\)/)
  assert.match(localFeatures, /apiPost\(`\/api\/governance\/databases\/\$\{dbId\}\/sync`, \{\}, \{\}, true\)/)
})

test('governance download and export URLs are defined', () => {
  assert.match(localFeatures, /downloadUrl: \(dbId, fileId\) =>/)
  assert.match(localFeatures, /`\/api\/governance\/databases\/\$\{dbId\}\/documents\/\$\{fileId\}\/download`/)
  assert.match(localFeatures, /exportUrl: \(dbId, format = 'xlsx'\) =>/)
})

test('downloadAuthenticated sends auth headers and triggers browser save', () => {
  assert.match(localFeatures, /export async function downloadAuthenticated\(url, filename\)/)
  assert.match(localFeatures, /fetch\(url, \{ headers: userStore\.getAuthHeaders\(\) \}\)/)
  assert.match(localFeatures, /URL\.createObjectURL\(blob\)/)
  assert.match(localFeatures, /a\.download = filename/)
  // 拒绝/失败时返回可展示的错误信息
  assert.match(localFeatures, /return \{ ok: false, message: msg \}/)
})

test('governance panel receives dbId prop and loads on mount', () => {
  assert.match(panel, /dbId:/)
  assert.match(panel, /type: String/)
  assert.match(panel, /onMounted\(load\)/)
  assert.match(panel, /governanceApi\.list\(props\.dbId/)
})

test('governance panel has filter, pagination and management controls', () => {
  // 筛选
  assert.match(panel, /v-model:value="filters\.keyword"/)
  assert.match(panel, /filters\.confidentiality/)
  assert.match(panel, /showSizeChanger: true/)
  // 同步 + 导出
  assert.match(panel, /governanceApi\.sync\(props\.dbId\)/)
  assert.match(panel, /governanceApi\.exportUrl\(props\.dbId, 'xlsx'\)/)
  // 受控下载走认证下载
  assert.match(panel, /downloadAuthenticated\(/)
  assert.match(panel, /governanceApi\.downloadUrl\(props\.dbId, record\.file_id\)/)
})

test('governance panel edits governance fields in a modal', () => {
  assert.match(panel, /v-model:open="editVisible"/)
  assert.match(panel, /editForm\.domain/)
  assert.match(panel, /editForm\.knowledge_type/)
  assert.match(panel, /editForm\.confidentiality/)
  assert.match(panel, /editForm\.tags/)
  assert.match(panel, /editForm\.download_allowed/)
  assert.match(panel, /editForm\.owner_department/)
  assert.match(panel, /governanceApi\.update\(props\.dbId, editForm\.file_id/)
})

test('governance panel previews document metadata without content', () => {
  assert.match(panel, /handlePreview\(record\)/)
  assert.match(panel, /governanceApi\.get\(props\.dbId, record\.file_id\)/)
  assert.match(panel, /v-model:open="previewVisible"/)
})

test('governance panel never displays absolute paths', () => {
  assert.doesNotMatch(panel, /record\.path/)
  assert.doesNotMatch(panel, /source_updated_at.*path/)
})

test('governance panel is embedded in the database info view for superadmin only', () => {
  assert.match(dbInfoView, /import KnowledgeGovernancePanel from '@\/components\/KnowledgeGovernancePanel\.vue'/)
  assert.match(dbInfoView, /<KnowledgeGovernancePanel :db-id="databaseId" \/>/)
  assert.match(dbInfoView, /key="governance" v-if="userStore\.isSuperAdmin"/)
  assert.match(dbInfoView, /SafetyCertificateOutlined/)
})

test('governance version APIs hit the right endpoints with auth', () => {
  assert.match(localFeatures, /versions: \(dbId, fileId\) =>/)
  assert.match(localFeatures, /apiGet\(`\/api\/governance\/databases\/\$\{dbId\}\/documents\/\$\{fileId\}\/versions`, \{\}, true\)/)
  assert.match(localFeatures, /snapshot: \(dbId, fileId, note\) =>/)
  assert.match(localFeatures, /apiPost\(`\/api\/governance\/databases\/\$\{dbId\}\/documents\/\$\{fileId\}\/versions\/snapshot`, \{ note \}, \{\}, true\)/)
  assert.match(localFeatures, /versionDownloadUrl: \(dbId, fileId, version\) =>/)
  assert.match(localFeatures, /`\/api\/governance\/databases\/\$\{dbId\}\/documents\/\$\{fileId\}\/versions\/\$\{version\}\/download`/)
})

test('governance panel shows version history with snapshot and download', () => {
  assert.match(panel, /handleVersions\(record\)/)
  assert.match(panel, /governanceApi\.versions\(props\.dbId, versionsFile\.value\.file_id\)/)
  assert.match(panel, /handleSnapshot\(\)/)
  assert.match(panel, /governanceApi\.snapshot\(props\.dbId, versionsFile\.value\.file_id, snapshotNote\.value\)/)
  assert.match(panel, /handleVersionDownload\(record\)/)
  assert.match(panel, /governanceApi\.versionDownloadUrl\(props\.dbId, versionsFile\.value\.file_id, record\.version\)/)
  assert.match(panel, /v-model:open="versionsVisible"/)
  assert.match(panel, /versionColumns/)
})

test('version panel disclaims reindex is not included', () => {
  // 明确标注恢复检索需要后续索引版本功能
  assert.match(panel, /恢复检索版本需要后续索引版本功能/)
  assert.match(panel, /不重建知识索引/)
})
