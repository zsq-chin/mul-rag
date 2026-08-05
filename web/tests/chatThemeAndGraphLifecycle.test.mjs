import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const messageComponent = readFileSync(
  new URL('../src/components/MessageComponent.vue', import.meta.url),
  'utf8',
)
const chatComponent = readFileSync(
  new URL('../src/components/ChatComponent.vue', import.meta.url),
  'utf8',
)
const graphView = readFileSync(
  new URL('../src/views/GraphView.vue', import.meta.url),
  'utf8',
)

test('knowledge-chat answers follow the selected light or dark theme without a white panel', () => {
  assert.match(messageComponent, /useThemeStore/)
  assert.match(messageComponent, /const\s+themeStore\s*=\s*useThemeStore\(\)/)
  assert.match(messageComponent, /<MdPreview[\s\S]*?:theme="themeStore\.mode"[\s\S]*?class="message-md"/)
  assert.match(messageComponent, /color:\s*var\(--text-primary\)/)
  assert.match(
    messageComponent,
    /\.message-md(?:\.md-editor)?\s*\{[^}]*--md-bk-color:\s*transparent[^}]*\}/,
    'the Markdown root must override the library background variable on itself',
  )
  assert.match(
    messageComponent,
    /\.message-md(?:\.md-editor)?\s*\{[^}]*background(?:-color)?:\s*transparent[^}]*\}/,
    'the Markdown root must explicitly remain transparent',
  )
  assert.match(
    messageComponent,
    /\.message-md(?:\.md-editor)?\s*\{[^}]*--md-color:\s*var\(--text-primary\)[^}]*\}/,
    'the Markdown library root color token must follow the application theme',
  )
  assert.match(
    messageComponent,
    /\.message-md\s+\.md-editor-preview[^{]*\{[^}]*--md-theme-color:\s*var\(--text-primary\)[^}]*\}/,
    'the selected preview theme must use the application text token',
  )
})

test('full-snapshot revisions replace rendered chat content', () => {
  assert.match(chatComponent, /replace_content/)
  assert.match(
    chatComponent,
    /if\s*\(\s*info\.replace_content[\s\S]*?msg\.content\s*=\s*info\.content/,
  )
})

test('chat messages scroll inside their own pane and cannot overflow behind the composer', () => {
  assert.match(chatComponent, /class="chat-box"\s+ref="messagesContainer"/)
  assert.match(
    chatComponent,
    /\.chat\s*\{[\s\S]*?overflow-y:\s*hidden/,
  )
  assert.match(
    chatComponent,
    /\.chat-box\s*\{[\s\S]*?overflow-y:\s*auto/,
  )
  assert.match(
    chatComponent,
    /messagesContainer\.value\.scrollHeight\s*-\s*messagesContainer\.value\.scrollTop/,
  )
  assert.doesNotMatch(
    chatComponent,
    /chatContainer\.value\.scrollHeight\s*-\s*chatContainer\.value\.scrollTop/,
  )
})

test('knowledge graph creates one G6 graph and owns its resize listener for one mount', () => {
  assert.equal(
    graphView.match(/\bnew\s+Graph\s*\(/g)?.length ?? 0,
    1,
    'a render must not leak a discarded G6 graph instance',
  )
  assert.equal(
    graphView.match(/window\.addEventListener\(['"]resize['"]/g)?.length ?? 0,
    1,
  )
  assert.equal(
    graphView.match(/window\.removeEventListener\(['"]resize['"]/g)?.length ?? 0,
    1,
  )

  const initGraph = graphView.slice(
    graphView.indexOf('const initGraph'),
    graphView.indexOf('onMounted('),
  )
  assert.doesNotMatch(
    initGraph,
    /window\.addEventListener\(['"]resize['"]/,
    'reinitializing the graph must not register another global listener',
  )
})

test('knowledge graph destroys and clears its G6 instance when the view unmounts', () => {
  const unmountHook = graphView.slice(
    graphView.indexOf('onBeforeUnmount('),
    graphView.indexOf('const handleFileUpload'),
  )

  assert.match(unmountHook, /graphInstance(?:\?\.)?\.destroy\(\)/)
  assert.match(unmountHook, /graphInstance\s*=\s*null/)
})

test('knowledge graph cancels delayed rendering and safely handles an unavailable view', () => {
  const timerAssignment = graphView.match(
    /(\w*[Rr]ender\w*[Tt]imer)\s*=\s*setTimeout\(/,
  )
  assert.ok(timerAssignment)
  const timerName = timerAssignment[1]
  const timerAssignmentIndex = graphView.indexOf(timerAssignment[0])
  const timerSetup = graphView.slice(
    Math.max(0, timerAssignmentIndex - 250),
    timerAssignmentIndex + timerAssignment[0].length,
  )
  assert.match(
    timerSetup,
    new RegExp(`clearTimeout\\(${timerName}\\)[\\s\\S]*${timerName}\\s*=\\s*setTimeout\\(`),
    'reloading graph data must cancel the previous delayed render',
  )

  const renderGraph = graphView.slice(
    graphView.indexOf('const randerGraph'),
    graphView.indexOf('const initGraph'),
  )
  assert.match(renderGraph, /if\s*\(\s*!container\.value\s*\)\s*(?:\{\s*)?return/)
  assert.match(renderGraph, /if\s*\(\s*!graphInstance\s*\)\s*(?:\{\s*)?return/)
  assert.match(
    renderGraph,
    /(?:await\s+\w+\.render\(\)|\w+\.render\(\)\.catch\()/,
    'G6 render failures must be observed instead of becoming unhandled rejections',
  )
  const localInstance = renderGraph.match(
    /const\s+(\w+)\s*=\s*graphInstance/,
  )
  assert.ok(
    localInstance,
    'each asynchronous render must retain the exact G6 instance that it started',
  )
  assert.match(
    renderGraph,
    new RegExp(`await\\s+${localInstance[1]}\\.render\\(\\)`),
  )
  assert.match(
    renderGraph,
    new RegExp(`graphInstance\\s*===\\s*${localInstance[1]}`),
    'a failed older render must not clear a newer graph instance',
  )
  assert.ok(
    renderGraph.indexOf('try {') < renderGraph.indexOf('initGraph()'),
    'graph construction and event binding must be inside an observed error boundary',
  )

  const unmountHook = graphView.slice(
    graphView.indexOf('onBeforeUnmount('),
    graphView.indexOf('const handleFileUpload'),
  )
  assert.match(unmountHook, new RegExp(`clearTimeout\\(${timerName}\\)`))
})
