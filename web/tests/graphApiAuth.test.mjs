import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// ---------------------------------------------------------------------------
// Structural verification that every graphApi method enforces superadmin auth.
//
// admin_api.js depends on Vue stores so it cannot be imported directly in Node.
// Instead we parse the exported graphApi block from source and verify two
// invariants that, if violated, cause the exact 401 observed in the Playwright
// integration test:
//   1. Every method body contains `checkSuperAdminPermission()` (not checkAdminPermission)
//   2. Every apiGet / apiPost / apiDelete call passes `true` as requiresAuth
// ---------------------------------------------------------------------------

const __dirname = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(resolve(__dirname, '../src/apis/admin_api.js'), 'utf-8')
const graphViewSrc = readFileSync(resolve(__dirname, '../src/views/GraphView.vue'), 'utf-8')

// Extract the graphApi object body (from the opening `{` to its matching `}`).
const graphApiStart = src.indexOf('export const graphApi = {')
assert.ok(graphApiStart !== -1, 'graphApi export not found in admin_api.js')

let braceDepth = 0
let graphApiBody = ''
let started = false
for (let i = src.indexOf('{', graphApiStart); i < src.length; i++) {
  if (src[i] === '{') braceDepth++
  if (started) graphApiBody += src[i]
  if (src[i] === '{' && !started) started = true
  if (src[i] === '}') braceDepth--
  if (braceDepth === 0 && started) break
}

// Extract individual method blocks by matching "methodName: async" patterns.
const methodPattern = /(\w+):\s*async\s*\([^)]*\)\s*=>\s*\{/g
const methodNames = []
const methodBodies = []
let match
while ((match = methodPattern.exec(graphApiBody)) !== null) {
  methodNames.push(match[1])
  // Find the matching closing brace for this method.
  let depth = 0
  let body = ''
  for (let j = match.index + match[0].length - 1; j < graphApiBody.length; j++) {
    if (graphApiBody[j] === '{') depth++
    body += graphApiBody[j]
    if (graphApiBody[j] === '}') {
      depth--
      if (depth === 0) break
    }
  }
  methodBodies.push(body)
}

test('graphApi has the expected methods', () => {
  const expected = [
    'getGraphInfo', 'getNodes', 'queryNode',
    'addByJsonl', 'addForGraphrag', 'file_handle',
    'buildGraph', 'build_drillGraph',
    'getFileList', 'getDownloadableFiles', 'deleteGraphFile',
    'downloadFile', 'indexNodes',
  ]
  assert.deepEqual(methodNames.sort(), expected.sort(),
    'graphApi method set changed — update this test and verify auth for new methods')
})

for (let i = 0; i < methodNames.length; i++) {
  const name = methodNames[i]
  const body = methodBodies[i]

  test(`${name} calls checkSuperAdminPermission`, () => {
    assert.ok(
      body.includes('checkSuperAdminPermission()'),
      `${name} does NOT call checkSuperAdminPermission — will cause 401 for non-superadmin`
    )
    assert.ok(
      !body.includes('checkAdminPermission()'),
      `${name} still calls checkAdminPermission instead of checkSuperAdminPermission`
    )
  })

  test(`${name} passes requiresAuth=true to the API helper`, () => {
    // Match apiGet / apiPost / apiDelete calls and verify `true` is the last
    // positional argument before the closing paren.
    const apiCallPattern = /api(?:Get|Post|Put|Delete)\s*\(/g
    let apiMatch
    let foundApiCall = false
    while ((apiMatch = apiCallPattern.exec(body)) !== null) {
      foundApiCall = true
      // Extract the full call up to the matching `)`.
      let parenDepth = 0
      let call = ''
      for (let k = apiMatch.index; k < body.length; k++) {
        if (body[k] === '(') parenDepth++
        call += body[k]
        if (body[k] === ')') {
          parenDepth--
          if (parenDepth === 0) break
        }
      }
      // The requiresAuth param must be `true` somewhere in the call args.
      assert.ok(
        call.includes(', true)'),
        `${name}: API call "${call.trim()}" does not pass requiresAuth=true`
      )
    }
    assert.ok(foundApiCall, `${name} has no apiGet/apiPost/apiDelete call`)
  })
}

// ---------------------------------------------------------------------------
// GraphView.vue: every direct fetch() call must pass auth headers
// ---------------------------------------------------------------------------

test('GraphView.vue fetch() calls include auth headers', () => {
  // Match all fetch( occurrences in GraphView.vue script section.
  const fetchCallPattern = /\bfetch\s*\(/g
  let match
  let fetchCount = 0
  while ((match = fetchCallPattern.exec(graphViewSrc)) !== null) {
    fetchCount++
    // Grab enough context after the call to see if headers are passed.
    const after = graphViewSrc.slice(match.index, match.index + 300)
    assert.ok(
      after.includes('getAuthHeaders') || after.includes('headers'),
      `GraphView.vue fetch() call at offset ${match.index} does not pass auth headers — ` +
      `the download route is superadmin-only and will 401 without credentials`
    )
  }
  assert.ok(fetchCount > 0, 'No fetch() calls found in GraphView.vue — test is no longer guarding anything')
})
