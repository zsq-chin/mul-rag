import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// ---------------------------------------------------------------------------
// Chat page console hygiene + stream lifecycle regression guard
// (5.1.1, 5.3.5, 5.4.3).
//
// 1. Shift+Enter newline must operate on the string directly (inputText is a
//    string, not a ref) — the old `conv.value.inputText.value = ...` code
//    crashed with a TypeError on `.substring` of undefined.
// 2. Stream fetch must arm a client-side timeout watchdog so a hung model
//    stream ends the loading state, and must dispose its watcher/timer.
// 3. Chat-path components must not print full messages / refs / params to the
//    browser console.
// 4. The API error layer must not leak internal container names or paths.
// ---------------------------------------------------------------------------

const __dirname = dirname(fileURLToPath(import.meta.url))

const read = (p) => readFileSync(resolve(__dirname, p), 'utf-8')

const chatSrc = read('../src/components/ChatComponent.vue')
const itemChatSrc = read('../src/components/ItemChatComponent.vue')
const guideChatSrc = read('../src/components/GuideChatComponent.vue')
const writerChatSrc = read('../src/components/WriterChatComponent.vue')
const refsSidebarSrc = read('../src/components/RefsSidebar.vue')
const refsSrc = read('../src/components/RefsComponent.vue')
const chatViewSrc = read('../src/views/ChatView.vue')
const baseApiSrc = read('../src/apis/base.js')

// 所有"对话输入区"组件：主聊天页、单点/实体对话页、议事助手、公文写作。
// 它们共享同一套 MessageInputComponent + handleKeyDown 换行逻辑与流式逻辑。
const chatComponents = {
  ChatComponent: chatSrc,
  ItemChatComponent: itemChatSrc,
  GuideChatComponent: guideChatSrc,
  WriterChatComponent: writerChatSrc,
  RefsSidebar: refsSidebarSrc,
  RefsComponent: refsSrc,
  ChatView: chatViewSrc,
}

// =========================================================================
// 1. Shift+Enter newline bug (5.1.1)
// =========================================================================

test('Shift+Enter newline operates on the string directly, never .value.substring', () => {
  const shiftEnterComponents = {
    ChatComponent: chatSrc,
    ItemChatComponent: itemChatSrc,
    GuideChatComponent: guideChatSrc,
    WriterChatComponent: writerChatSrc,
  }
  for (const [name, src] of Object.entries(shiftEnterComponents)) {
    assert.doesNotMatch(
      src,
      /inputText\.value\.substring/,
      `${name} must not call .substring on .value of a plain string`,
    )
    assert.match(
      src,
      /const newText\s*=\s*[\s\S]*?substring[\s\S]*?\n[\s\S]*?conv\.value\.inputText\s*=\s*newText/,
      `${name} must assign the sliced string back to conv.value.inputText`,
    )
  }
})

// =========================================================================
// 2. Stream timeout watchdog + watcher/timer disposal (5.3.5)
// =========================================================================

test('fetchChatResponse arms a client-side timeout watchdog that aborts the stream', () => {
  for (const [name, src] of Object.entries({ ChatComponent: chatSrc, ItemChatComponent: itemChatSrc })) {
    assert.match(src, /STREAM_TIMEOUT_MS\s*=\s*120000/, `${name} must define a 120s stream timeout`)
    assert.match(
      src,
      /let timeoutHandle\s*=\s*setTimeout\([\s\S]*?controller\.abort\(\)/,
      `${name} watchdog must abort the controller on timeout`,
    )
    assert.match(
      src,
      /status:\s*'error'[\s\S]*?message:\s*'请求超时，请重试'/,
      `${name} watchdog must mark the message as a timeout error`,
    )
  }
})

test('stream completion and error both clear the watchdog timer', () => {
  for (const [name, src] of Object.entries({ ChatComponent: chatSrc, ItemChatComponent: itemChatSrc })) {
    assert.match(
      src,
      /if \(done\)\s*\{[\s\S]*?clearTimeoutHandle\(\)/,
      `${name} must clear the watchdog timer on stream end`,
    )
    assert.match(
      src,
      /\.catch\(\(error\)\s*=>\s*\{[\s\S]*?clearTimeoutHandle\(\)/,
      `${name} must clear the watchdog timer on stream error/cancel`,
    )
  }
})

test('the isStreaming watcher is stored and disposed, not leaked per message', () => {
  for (const [name, src] of Object.entries({ ChatComponent: chatSrc, ItemChatComponent: itemChatSrc })) {
    assert.match(src, /const stopWatch\s*=\s*watch\(isStreaming/, `${name} must store the watcher handle`)
    assert.match(src, /stopWatch\(\)/, `${name} must call the stored watcher stop handle on done/catch`)
  }
})

// =========================================================================
// 3. Console hygiene in chat-path components (5.4.3)
// =========================================================================

test('chat components contain no console.log / console.debug at all', () => {
  for (const [name, src] of Object.entries(chatComponents)) {
    assert.doesNotMatch(src, /console\.(log|debug)\(/, `${name} must not print console.log/debug`)
  }
})

test('no chat component stringifies full refs/messages/params to the console', () => {
  for (const [name, src] of Object.entries(chatComponents)) {
    assert.doesNotMatch(src, /console\.(log|debug)\([^)]*(JSON\.stringify|latestRefs|conv\.value\.messages|params)/,
      `${name} must not log full refs/messages/params`)
  }
})

// =========================================================================
// 4. API error layer must not leak internal container names (9.3.3 / 5.4.3)
// =========================================================================

test('API error message does not leak container names or docker commands', () => {
  assert.doesNotMatch(baseApiSrc, /docker logs/, 'base.js must not reference docker logs')
  assert.doesNotMatch(baseApiSrc, /api-dev/, 'base.js must not leak the internal container name api-dev')
})
