import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const refsComponent = readFileSync(
  new URL('../src/components/RefsComponent.vue', import.meta.url),
  'utf8',
)
const messageComponent = readFileSync(
  new URL('../src/components/MessageComponent.vue', import.meta.url),
  'utf8',
)
const chatComponent = readFileSync(
  new URL('../src/components/ChatComponent.vue', import.meta.url),
  'utf8',
)
const localFeatures = readFileSync(
  new URL('../src/apis/local_features.js', import.meta.url),
  'utf8',
)

test('feedback API calls carry auth and hit the right endpoints', () => {
  assert.match(localFeatures, /apiPut\(`\/api\/feedback\/messages\/\$\{messageId\}`, payload, \{\}, true\)/)
  assert.match(localFeatures, /apiGet\(`\/api\/feedback\/messages\/\$\{messageId\}`, \{\}, true\)/)
  assert.match(localFeatures, /apiDelete\(`\/api\/feedback\/messages\/\$\{messageId\}`, \{\}, true\)/)
  assert.match(localFeatures, /apiGet\(`\/api\/feedback\/mine\?page=\$\{page\}&page_size=\$\{pageSize\}`, \{\}, true\)/)
  assert.match(localFeatures, /apiGet\('\/api\/feedback\/summary', \{\}, true\)/)
})

test('like and dislike handlers are implemented', () => {
  assert.match(refsComponent, /const\s+likeThisResponse\s*=\s*async\s*\(m\)/)
  assert.match(refsComponent, /const\s+dislikeThisResponse\s*=\s*async\s*\(m\)/)
})

test('feedback buttons show selected state and disable while generating', () => {
  // 选中态 class
  assert.match(refsComponent, /:class="\{\s*active:\s*ratingFor\(msg\)\s*===\s*'up'/)
  assert.match(refsComponent, /:class="\{\s*active:\s*ratingFor\(msg\)\s*===\s*'down'/)
  // 生成中禁用
  assert.match(refsComponent, /disabled:\s*!canRate\(msg\)/)
  assert.match(refsComponent, /const canRate = \(m\) =>/)
  assert.match(refsComponent, /m\.status === 'finished'/)
})

test('re-clicking a selected button cancels via DELETE', () => {
  assert.match(refsComponent, /if \(prev === 'up'\)/)
  assert.match(refsComponent, /await feedbackApi\.remove\(m\.id\)/)
  assert.match(refsComponent, /if \(prev === 'down'\)/)
  assert.match(refsComponent, /await feedbackApi\.remove\(m\.id\)/)
})

test('request failure rolls the UI state back', () => {
  assert.match(refsComponent, /const prev = st\.rating/)
  assert.match(refsComponent, /st\.rating = prev \/\/ 失败回滚界面状态/)
  assert.match(refsComponent, /catch \(e\) \{[\s\S]*?st\.rating = prev/)
})

test('downvote opens an optional non-blocking reason dialog', () => {
  assert.match(refsComponent, /<a-modal/)
  assert.match(refsComponent, /reasonVisible/)
  assert.match(refsComponent, /const submitReason = async/)
  // 先提交点踩，再弹原因 → 不阻塞
  assert.match(refsComponent, /await feedbackApi\.upsert\(m\.id, \{[\s\S]*?rating: 'down'[\s\S]*?reasonVisible\.value = true/)
})

test('feedback state is restored after refresh from the server', () => {
  assert.match(refsComponent, /const loadFeedback = async/)
  assert.match(refsComponent, /await feedbackApi\.get\(m\.id\)/)
  assert.match(refsComponent, /watch\(/)
  assert.match(refsComponent, /status === 'finished'\) loadFeedback\(msg\.value\)/)
  assert.match(refsComponent, /st\.rating = data\.rating/)
})

test('conversation id is threaded from chat to refs', () => {
  // RefsComponent 接收 conversationId prop
  assert.match(refsComponent, /conversationId:\s*\{[\s\S]*?type: String/)
  // MessageComponent 透传
  assert.match(messageComponent, /conversationId:\s*\{[\s\S]*?type: String/)
  assert.match(messageComponent, /:conversation-id="conversationId"/)
  // ChatComponent 传入 conv.id
  assert.match(chatComponent, /:conversation-id="conv\.id"/)
})

test('feedback payload sends conversation id', () => {
  assert.match(refsComponent, /conversation_id:\s*props\.conversationId/)
})
