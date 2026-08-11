import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const localFeatures = readFileSync(
  new URL('../src/apis/local_features.js', import.meta.url),
  'utf8',
)

test('monitoring API carries auth and hits the right endpoints', () => {
  assert.match(localFeatures, /monitoringApi = \{/)
  assert.match(localFeatures, /health: \(\) => apiGet\('\/api\/operations\/health', \{\}, true\)/)
  assert.match(localFeatures, /metrics: \(\) => apiGet\('\/api\/operations\/metrics', \{\}, true\)/)
  assert.match(localFeatures, /dependencies: \(\) => apiGet\('\/api\/operations\/dependencies', \{\}, true\)/)
})
