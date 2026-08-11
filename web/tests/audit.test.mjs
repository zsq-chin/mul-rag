import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const localFeatures = readFileSync(
  new URL('../src/apis/local_features.js', import.meta.url),
  'utf8',
)

test('audit API calls carry auth and hit the right endpoints', () => {
  assert.match(localFeatures, /auditApi = \{/)
  assert.match(localFeatures, /apiGet\(`\/api\/audit\/events\?/)
  assert.match(localFeatures, /apiGet\(`\/api\/audit\/events\/\$\{eventId\}`, \{\}, true\)/)
  assert.match(localFeatures, /apiGet\('\/api\/audit\/actions', \{\}, true\)/)
})

test('audit events pass arbitrary filters through URLSearchParams with auth', () => {
  assert.match(localFeatures, /const qs = new URLSearchParams/)
  assert.match(localFeatures, /Object\.entries\(params \|\| \{\}\)\.filter\(\(\[, v\]\) => v !== undefined && v !== ''\)/)
  assert.match(localFeatures, /events: \(params\) =>/)
  assert.match(localFeatures, /event: \(eventId\) =>/)
})
