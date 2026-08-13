import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const localFeatures = readFileSync(
  new URL('../src/apis/local_features.js', import.meta.url),
  'utf8',
)
const view = readFileSync(
  new URL('../src/views/EvaluationView.vue', import.meta.url),
  'utf8',
)
const routerSrc = readFileSync(
  new URL('../src/router/index.js', import.meta.url),
  'utf8',
)
const accessSrc = readFileSync(
  new URL('../src/utils/access.mjs', import.meta.url),
  'utf8',
)

test('evaluation API calls carry auth and hit the right endpoints', () => {
  assert.match(localFeatures, /apiGet\(`\/api\/evaluation\/suites\?/)
  assert.match(localFeatures, /apiGet\(`\/api\/evaluation\/suites\/\$\{suiteId\}`, \{\}, true\)/)
  assert.match(localFeatures, /apiPost\('\/api\/evaluation\/suites', payload, \{\}, true\)/)
  assert.match(localFeatures, /apiPatch\(`\/api\/evaluation\/suites\/\$\{suiteId\}`, payload, \{\}, true\)/)
  assert.match(localFeatures, /apiDelete\(`\/api\/evaluation\/suites\/\$\{suiteId\}`, \{\}, true\)/)
  assert.match(localFeatures, /apiGet\(`\/api\/evaluation\/suites\/\$\{suiteId\}\/cases\?/)
  assert.match(localFeatures, /apiPost\(`\/api\/evaluation\/suites\/\$\{suiteId\}\/cases`, payload, \{\}, true\)/)
  assert.match(localFeatures, /apiPatch\(`\/api\/evaluation\/suites\/\$\{suiteId\}\/cases\/\$\{caseId\}`, payload, \{\}, true\)/)
  assert.match(localFeatures, /apiDelete\(`\/api\/evaluation\/suites\/\$\{suiteId\}\/cases\/\$\{caseId\}`, \{\}, true\)/)
})

test('evaluation execute posts to the suite execute endpoint with auth (8.2)', () => {
  assert.match(localFeatures, /executeSuite: \(suiteId\) =>/)
  assert.match(localFeatures, /apiPost\(`\/api\/evaluation\/suites\/\$\{suiteId\}\/execute`, \{\}, \{\}, true\)/)
})

test('EvaluationView offers a run action and renders an honest per-case report', () => {
  assert.match(view, /runSuite\(record\)/)
  assert.match(view, /executeResult/)
  assert.match(view, /执行结果/)
  // 报告按用例展示回答/要点/判定/异常，而非伪造准确率
  assert.match(view, /record\.matched/)
  assert.match(view, /record\.error/)
  assert.doesNotMatch(view, /accuracy/i)
})

test('evaluation import and export use authenticated URLs', () => {
  assert.match(localFeatures, /importUrl: \(suiteId, format\) =>/)
  assert.match(localFeatures, /`\/api\/evaluation\/suites\/\$\{suiteId\}\/import\?format=\$\{format\}`/)
  assert.match(localFeatures, /exportUrl: \(suiteId, format\) =>/)
  assert.match(localFeatures, /`\/api\/evaluation\/suites\/\$\{suiteId\}\/export\?format=\$\{format\}`/)
  // multipart 上传必须带认证头
  assert.match(localFeatures, /export async function uploadAuthenticated\(url, file\)/)
  assert.match(localFeatures, /fetch\(url, \{ method: 'POST', headers: userStore\.getAuthHeaders\(\), body: form \}\)/)
})

test('EvaluationView renders a suite table with management actions', () => {
  assert.match(view, /a-table/)
  assert.match(view, /column\.key === 'action'/)
  assert.match(view, /openSuiteCreate/)
  assert.match(view, /deleteSuite\(record\)/)
  assert.match(view, /openCases\(record\)/)
})

test('EvaluationView edits cases in a drawer (not a fake accuracy panel)', () => {
  assert.match(view, /a-drawer/)
  assert.match(view, /v-model:open="caseEditVisible"/)
  assert.match(view, /关键要点/)
  assert.match(view, /difficulty/)
  assert.match(view, /批量导入/)
  // 明确禁止假准确率结果
  assert.doesNotMatch(view, /accuracy/i)
  assert.doesNotMatch(view, /通过率/)
  assert.doesNotMatch(view, /评估结果/)
})

test('EvaluationView import shows a result dialog with row errors', () => {
  assert.match(view, /importModalVisible/)
  assert.match(view, /row_errors/)
  assert.match(view, /importResult\.row_errors/)
  assert.match(view, /uploadAuthenticated/)
})

test('EvaluationView never calls the model or remote multimodal KB', () => {
  assert.doesNotMatch(view, /runEvaluation|callModel|chat\(/)
  assert.doesNotMatch(view, /multimodal/i)
  assert.doesNotMatch(view, /10\.16\.33\.2/)
})

test('evaluation route and nav entry are superadmin-only', () => {
  assert.match(routerSrc, /path: '\/evaluation'/)
  assert.match(routerSrc, /import\('\.\.\/views\/EvaluationView\.vue'\)/)
  assert.match(accessSrc, /path: '\/evaluation', roles: \['superadmin'\]/)
})
