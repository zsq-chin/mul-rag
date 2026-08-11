import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const localFeatures = readFileSync(
  new URL('../src/apis/local_features.js', import.meta.url),
  'utf8',
)

test('backup API carries auth and hits the right endpoints', () => {
  assert.match(localFeatures, /backupApi = \{/)
  assert.match(localFeatures, /apiPost\('\/api\/operations\/backups', payload, \{\}, true\)/)
  assert.match(localFeatures, /apiGet\(`\/api\/operations\/backups\?page=/)
  assert.match(localFeatures, /apiGet\(`\/api\/operations\/backups\/\$\{backupId\}`, \{\}, true\)/)
  assert.match(localFeatures, /apiPost\(`\/api\/operations\/backups\/\$\{backupId\}\/verify`/)
  assert.match(localFeatures, /apiPost\(`\/api\/operations\/backups\/\$\{backupId\}\/restore\/preview`/)
  assert.match(localFeatures, /apiPost\(`\/api\/operations\/backups\/\$\{backupId\}\/restore`, \{ token \}/)
  assert.match(localFeatures, /apiDelete\(`\/api\/operations\/backups\/\$\{backupId\}`, \{\}, true\)/)
})

test('backup download URL is exposed for downloadAuthenticated', () => {
  assert.match(localFeatures, /downloadUrl: \(backupId\) =>/)
  assert.match(localFeatures, /`\/api\/operations\/backups\/\$\{backupId\}\/download`/)
})
