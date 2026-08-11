import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const localFeatures = readFileSync(
  new URL('../src/apis/local_features.js', import.meta.url),
  'utf8',
)

test('alert API carries auth and hits the right endpoints', () => {
  assert.match(localFeatures, /alertApi = \{/)
  assert.match(localFeatures, /rules: \(\) => apiGet\('\/api\/operations\/alert-rules', \{\}, true\)/)
  assert.match(localFeatures, /createRule: \(payload\) => apiPost\('\/api\/operations\/alert-rules', payload, \{\}, true\)/)
  assert.match(localFeatures, /apiPatch\(`\/api\/operations\/alert-rules\/\$\{ruleId\}`, payload, \{\}, true\)/)
  assert.match(localFeatures, /apiDelete\(`\/api\/operations\/alert-rules\/\$\{ruleId\}`, \{\}, true\)/)
  assert.match(localFeatures, /apiGet\(`\/api\/operations\/alert-events\?/)
  assert.match(localFeatures, /apiPost\(`\/api\/operations\/alert-events\/\$\{eventId\}\/acknowledge`, \{\}, \{\}, true\)/)
  assert.match(localFeatures, /testEmail: \(toEmail\) => apiPost\('\/api\/operations\/email\/test', \{ to_email: toEmail \}/)
})

test('alert events filters pass through URLSearchParams', () => {
  assert.match(localFeatures, /events: \(params\) =>/)
  assert.match(localFeatures, /Object\.entries\(params \|\| \{\}\)\.filter\(\(\[, v\]\) => v !== undefined && v !== ''\)/)
})
