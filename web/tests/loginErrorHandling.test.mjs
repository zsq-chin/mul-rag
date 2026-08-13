import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// ---------------------------------------------------------------------------
// Login / first-run error handling regression guard (4.2.1–4.2.3).
//
// Verifies that a backend that is down or returns an empty / non-JSON / 5xx
// response never triggers a JSON parse exception in the login flow, that a
// clear error + retry is shown, and that retry re-runs both the health check
// and the first-run check without a full browser refresh.
// ---------------------------------------------------------------------------

const __dirname = dirname(fileURLToPath(import.meta.url))

const userStoreSrc = readFileSync(resolve(__dirname, '../src/stores/user.js'), 'utf-8')
const loginViewSrc = readFileSync(resolve(__dirname, '../src/views/LoginView.vue'), 'utf-8')

test('user store has a safe JSON reader that never throws on empty/non-JSON bodies', () => {
  assert.match(userStoreSrc, /safeParseJson/, 'user.js must define a safeParseJson helper')
  // The helper must be guarded by try/catch around JSON.parse
  const helperMatch = userStoreSrc.match(/async function safeParseJson[\s\S]*?\n  \}/)
  assert.ok(helperMatch, 'safeParseJson must be a complete function')
  assert.match(helperMatch[0], /try\s*\{[\s\S]*?catch/, 'safeParseJson must catch parse failures')
  assert.match(
    helperMatch[0],
    /response\.text\(\)/,
    'safeParseJson must read text() so empty/non-JSON bodies are handled safely',
  )
})

test('login does not call response.json() on a non-ok response', () => {
  // The non-ok branch must go through safeParseJson (no raw response.json()).
  const loginBlock = userStoreSrc.match(/async function login[\s\S]*?\n  \}/)[0]
  assert.match(loginBlock, /safeParseJson\(response, \{\}\)/,
    'login non-ok branch must parse via safeParseJson, never raw response.json()')
  assert.doesNotMatch(
    loginBlock,
    /if \(!response\.ok\) \{[\s\S]*?await response\.json\(\)/,
    'login must not await response.json() inside the non-ok branch',
  )
  // Success path is guarded too: access_token presence must be validated.
  assert.match(
    loginBlock,
    /!data\s*\|\|\s*!data\.access_token/,
    'login must validate access_token presence after parsing',
  )
})

test('initialize does not call response.json() on a non-ok response', () => {
  const initBlock = userStoreSrc.match(/async function initialize[\s\S]*?\n  \}/)[0]
  assert.match(initBlock, /safeParseJson\(response, \{\}\)/,
    'initialize non-ok branch must parse via safeParseJson')
  assert.doesNotMatch(
    initBlock,
    /if \(!response\.ok\) \{[\s\S]*?await response\.json\(\)/,
    'initialize must not await response.json() inside the non-ok branch',
  )
})

test('checkFirstRun propagates real errors instead of swallowing to false', () => {
  const firstRunBlock = userStoreSrc.match(/async function checkFirstRun[\s\S]*?\n  \}/)[0]
  // Must check response.ok before parsing (non-JSON/5xx → thrown, not silently false)
  assert.match(firstRunBlock, /if \(!response\.ok\)\s*\{[\s\S]*?throw new Error/,
    'checkFirstRun must throw when the endpoint is not ok')
  // Must use safe parsing for the body
  assert.match(firstRunBlock, /safeParseJson\(response/, 'checkFirstRun must parse via safeParseJson')
  // Must rethrow on error (propagate), not return false
  assert.match(firstRunBlock, /throw error/, 'checkFirstRun must rethrow caught errors')
  assert.doesNotMatch(firstRunBlock, /return false/, 'checkFirstRun must not silently return false')
})

test('retry re-runs both health check and first-run check without a full refresh', () => {
  // LoginView must define retryConnection that calls checkServerHealth then
  // checkFirstRunStatus, and the retry button must be bound to it.
  assert.match(loginViewSrc, /const retryConnection/, 'LoginView must define retryConnection')
  const retryBlock = loginViewSrc.match(/const retryConnection[\s\S]*?\n\};\s*\n/)[0]
  assert.match(retryBlock, /checkServerHealth\(\)/, 'retryConnection must re-run the health check')
  assert.match(retryBlock, /checkFirstRunStatus\(\)/, 'retryConnection must re-run the first-run check')
  // The on-screen retry button must be wired to retryConnection.
  assert.match(
    loginViewSrc,
    /@click="retryConnection"/,
    'retry button must call retryConnection (no browser refresh)',
  )
  // first-run failure must surface the server error state rather than silently continuing.
  assert.match(
    loginViewSrc,
    /serverStatus\.value\s*=\s*'error'/,
    'first-run check failure must set the error state so the retry banner shows',
  )
})

test('user store keeps only console.error — no debug console.log', () => {
  // 9.3.2: production frontend must not retain console.log/debug.
  assert.doesNotMatch(userStoreSrc, /console\.log\(/, 'user.js must not contain console.log')
  assert.doesNotMatch(userStoreSrc, /console\.debug\(/, 'user.js must not contain console.debug')
})
