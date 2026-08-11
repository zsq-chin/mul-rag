import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const view = readFileSync(
  new URL('../src/views/OperationsView.vue', import.meta.url),
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
const layoutSrc = readFileSync(
  new URL('../src/layouts/AppLayout.vue', import.meta.url),
  'utf8',
)

test('OperationsView is organized into the five required tabs', () => {
  assert.match(view, /a-tabs v-model:activeKey="activeTab"/)
  for (const tab of ['运行状态', '审计日志', '备份恢复', '配置历史', '告警规则']) {
    assert.match(view, new RegExp(`tab="${tab}"`), `missing tab: ${tab}`)
  }
})

test('OperationsView consumes all five local API groups with auth', () => {
  const importBlock = view.match(/import \{[\s\S]*?\} from '@\/apis\/local_features'/)
  assert.ok(importBlock, 'must import from local_features')
  const block = importBlock[0]
  for (const name of ['monitoringApi', 'auditApi', 'backupApi', 'configHistoryApi', 'alertApi', 'downloadAuthenticated']) {
    assert.match(block, new RegExp(`\\b${name}\\b`), `missing import: ${name}`)
  }
  // 关键调用点
  assert.match(view, /monitoringApi\.health\(\)/)
  assert.match(view, /monitoringApi\.dependencies\(\)/)
  assert.match(view, /auditApi\.events\(/)
  assert.match(view, /backupApi\.list\(/)
  assert.match(view, /backupApi\.preview\(/)
  assert.match(view, /backupApi\.restore\(/)
  assert.match(view, /configHistoryApi\.history\(/)
  assert.match(view, /configHistoryApi\.rollback\(/)
  assert.match(view, /alertApi\.rules\(/)
  assert.match(view, /alertApi\.events\(/)
  assert.match(view, /alertApi\.acknowledge\(/)
  assert.match(view, /alertApi\.testEmail\(/)
  assert.match(view, /downloadAuthenticated\(/)
})

test('audit, backup, history and alert tables use server pagination', () => {
  assert.match(view, /showSizeChanger: true/)
  assert.match(view, /onChange: \(page, size\) => \{ auditPage = page; auditPageSize = size; loadAudit\(\) \}/)
  assert.match(view, /onChange: \(page, size\) => \{ backupPage = page; backupPageSize = size; loadBackups\(\) \}/)
  assert.match(view, /onChange: \(page, size\) => \{ historyPage = page; historyPageSize = size; loadHistory\(\) \}/)
  assert.match(view, /onChange: \(page, size\) => \{ eventsPage = page; eventsPageSize = size; loadEvents\(\) \}/)
})

test('destructive restore uses preview summary and double confirmation', () => {
  assert.match(view, /openRestorePreview = async \(record\)/)
  assert.match(view, /backupApi\.preview\(record\.id\)/)
  assert.match(view, /restorePreview\.value\.token/)
  assert.match(view, /Modal\.confirm\(/)
  assert.match(view, /okType: 'danger'/)
  assert.match(view, /restorePreview\.added/)
  assert.match(view, /restorePreview\.overwritten/)
  assert.match(view, /restorePreview\.skipped/)
})

test('OperationsView uses theme variables for light/dark text', () => {
  assert.match(view, /color: var\(--text-primary\)/)
  assert.match(view, /var\(--text-secondary\)/)
  assert.match(view, /var\(--border\)/)
  assert.match(view, /var\(--surface\)/)
})

test('OperationsView avoids nested cards and marketing-style headers', () => {
  assert.doesNotMatch(view, /<a-card/)
  assert.doesNotMatch(view, /营销|数字化|赋能|一站式/)
})

test('OperationsView never touches remote multimodal features', () => {
  assert.doesNotMatch(view, /multimodal/i)
  assert.doesNotMatch(view, /10\.16\.33\.2/)
  assert.doesNotMatch(view, /mul_rag/)
})

test('operations route and nav entry are superadmin-only', () => {
  assert.match(routerSrc, /path: '\/operations'/)
  assert.match(routerSrc, /import\('\.\.\/views\/OperationsView\.vue'\)/)
  assert.match(accessSrc, /path: '\/operations', roles: \['superadmin'\]/)
})

test('operations and evaluation icons are registered in the layout', () => {
  assert.match(layoutSrc, /ClipboardCheck/)
  assert.match(layoutSrc, /ServerCog/)
  assert.match(layoutSrc, /evaluation: ClipboardCheck/)
  assert.match(layoutSrc, /operations: ServerCog/)
})
