import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const localFeatures = readFileSync(
  new URL('../src/apis/local_features.js', import.meta.url),
  'utf8',
)

test('config history API carries auth and hits the right endpoints', () => {
  assert.match(localFeatures, /configHistoryApi = \{/)
  assert.match(localFeatures, /apiGet\(`\/api\/config\/history\?/)
  assert.match(localFeatures, /apiGet\(`\/api\/config\/history\/\$\{changeId\}`, \{\}, true\)/)
  assert.match(localFeatures, /apiPost\(`\/api\/config\/history\/\$\{changeId\}\/rollback`/)
})

test('config history filters pass through URLSearchParams and rollback posts description', () => {
  assert.match(localFeatures, /history: \(params\) =>/)
  assert.match(localFeatures, /Object\.entries\(params \|\| \{\}\)\.filter\(\(\[, v\]\) => v !== undefined && v !== ''\)/)
  assert.match(localFeatures, /rollback: \(changeId, description\) =>/)
  assert.match(localFeatures, /\{ description \}/)
})
