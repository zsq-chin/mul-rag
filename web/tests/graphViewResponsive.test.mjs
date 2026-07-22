import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// ---------------------------------------------------------------------------
// GraphView.vue responsive regression guard.
//
// These tests parse the SFC source to verify the mobile media query exists
// and contains the critical layout rules. They do NOT snapshot exact values
// (which would be brittle) — they check structural invariants that, if
// violated, cause the exact overflow / squeeze / off-screen regressions
// described in the bug report.
// ---------------------------------------------------------------------------

const __dirname = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(resolve(__dirname, '../src/views/GraphView.vue'), 'utf-8')

// Extract the <style> block.
const styleMatch = src.match(/<style[^>]*>([\s\S]*?)<\/style>/)
assert.ok(styleMatch, 'GraphView.vue has no <style> block')

const style = styleMatch[1]

// Find the mobile media query.
const mobileQuery = style.match(/@media\s*\(max-width:\s*768px\)\s*\{([\s\S]*?)\n\}/)
assert.ok(mobileQuery, 'GraphView.vue has no @media (max-width: 768px) rule')

const mobileCSS = mobileQuery[1]

test('mobile media query targets <= 768px', () => {
  assert.ok(
    mobileQuery[0].includes('max-width: 768px'),
    'Mobile breakpoint must be 768px'
  )
})

test('header content flex-wrap is set via :deep selector', () => {
  assert.ok(
    mobileCSS.includes(':deep(.header-content)'),
    'Must target .header-content inside HeaderComponent via :deep'
  )
  assert.ok(
    mobileCSS.includes('flex-wrap: wrap'),
    '.header-content must have flex-wrap: wrap so title/actions can stack'
  )
})

test('header actions flex-wrap is set via :deep selector', () => {
  assert.ok(
    mobileCSS.includes(':deep(.header-actions)'),
    'Must target .header-actions inside HeaderComponent via :deep'
  )
})

test('actions wrapper switches to column layout on mobile', () => {
  assert.ok(
    mobileCSS.includes('flex-direction: column'),
    '.actions must switch to column layout to prevent horizontal overflow'
  )
})

test('actions-left and actions-right wrap their children', () => {
  assert.ok(
    mobileCSS.includes('.actions-left'),
    'Must target .actions-left for wrapping'
  )
  assert.ok(
    mobileCSS.includes('.actions-right'),
    'Must target .actions-right for wrapping'
  )
})

test('main-content stacks vertically with natural height', () => {
  assert.ok(
    mobileCSS.includes('.main-content'),
    'Must target .main-content for vertical stacking'
  )
  // The mobile rule should set flex-direction: column AND height: auto
  // so the control-panel + graph-panel stack and don't overflow.
  assert.ok(
    mobileCSS.includes('height: auto'),
    '.main-content must use natural height on mobile (not fixed viewport height)'
  )
})

test('control-panel is full width on mobile', () => {
  assert.ok(
    mobileCSS.includes('.control-panel'),
    'Must target .control-panel for full-width override'
  )
  assert.ok(
    mobileCSS.includes('width: 100%'),
    '.control-panel must be full width on mobile (was 400px fixed)'
  )
})

test('graph-panel has a stable minimum height on mobile', () => {
  assert.ok(
    mobileCSS.includes('.graph-panel'),
    'Must target .graph-panel for minimum height'
  )
  assert.ok(
    mobileCSS.includes('min-height'),
    '.graph-panel needs min-height to remain visible below control-panel'
  )
})

test('container adapts to mobile viewport', () => {
  assert.ok(
    mobileCSS.includes('#container'),
    'Must target #container for mobile size adaptation'
  )
})

test('desktop styles remain unchanged (no media query leak)', () => {
  // Verify that the desktop .main-content still has the original fixed height
  // outside the media query.
  const desktopMainContent = style.match(/\.main-content\s*\{[^}]*\}/)
  assert.ok(desktopMainContent, '.main-content rule not found')
  assert.ok(
    desktopMainContent[0].includes('calc(100vh'),
    'Desktop .main-content must still use fixed viewport height'
  )
})

test('desktop control-panel is still 400px', () => {
  const desktopControlPanel = style.match(/\.control-panel\s*\{[^}]*\}/)
  assert.ok(desktopControlPanel, '.control-panel rule not found')
  assert.ok(
    desktopControlPanel[0].includes('400px'),
    'Desktop .control-panel must still be 400px wide'
  )
})

test('HeaderComponent.vue is NOT modified', () => {
  const headerSrc = readFileSync(
    resolve(__dirname, '../src/components/HeaderComponent.vue'), 'utf-8'
  )
  // Verify no responsive rules were added to the shared component.
  assert.ok(
    !headerSrc.includes('@media'),
    'HeaderComponent must NOT contain media queries — responsive rules belong in GraphView.vue'
  )
})
