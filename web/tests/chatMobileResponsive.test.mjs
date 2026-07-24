import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

// ---------------------------------------------------------------------------
// Chat mobile responsive regression guard.
//
// Tests verify:
// 1. Sidebar closes on initial load at ≤520px without destroying desktop pref
// 2. Mobile sidebar is a bounded overlay with correct width
// 3. Dark theme sidebar and input use CSS variables, not hardcoded colors
// 4. Flex layout has min-width:0 to prevent clipping on narrow viewports
// ---------------------------------------------------------------------------

const __dirname = dirname(fileURLToPath(import.meta.url))

const chatViewSrc = readFileSync(resolve(__dirname, '../src/views/ChatView.vue'), 'utf-8')
const chatComponentSrc = readFileSync(resolve(__dirname, '../src/components/ChatComponent.vue'), 'utf-8')
const messageInputSrc = readFileSync(resolve(__dirname, '../src/components/MessageInputComponent.vue'), 'utf-8')
const appLayoutSrc = readFileSync(resolve(__dirname, '../src/layouts/AppLayout.vue'), 'utf-8')

// Extract <style> blocks
function extractStyles(src) {
  const styles = []
  const re = /<style[^>]*>([\s\S]*?)<\/style>/g
  let m
  while ((m = re.exec(src)) !== null) styles.push(m[1])
  return styles.join('\n')
}

const chatViewStyle = extractStyles(chatViewSrc)
const chatComponentStyle = extractStyles(chatComponentSrc)
const messageInputStyle = extractStyles(messageInputSrc)
const appLayoutStyle = extractStyles(appLayoutSrc)

// =========================================================================
// 1. Mobile sidebar initial-state policy
// =========================================================================

test('ChatView closes sidebar on mobile viewport without clobbering desktop localStorage', () => {
  // Must read viewport width at mount and override isSidebarOpen when narrow
  assert.match(
    chatViewSrc,
    /innerWidth|matchMedia|clientWidth/,
    'ChatView must check viewport width to decide mobile sidebar state',
  )
  // Must conditionally close (set to false) only on mobile
  assert.match(
    chatViewSrc,
    /isSidebarOpen\s*[=:]\s*false/,
    'ChatView must set isSidebarOpen to false for mobile',
  )
  // Must still read localStorage for desktop — the initial assignment must remain
  assert.match(
    chatViewSrc,
    /localStorage\.getItem\(\s*['"]chat-sidebar-open['"]\s*\)/,
    'ChatView must still read localStorage for the desktop preference',
  )
  // The viewport check must be in the reactive initializer (not onMounted),
  // so the sidebar starts closed from the first render on mobile.
  assert.match(
    chatViewSrc,
    /isSidebarOpen:\s*\(\(\)\s*=>\s*\{[\s\S]*?innerWidth[\s\S]*?return\s+false/,
    'Sidebar init must use an IIFE that returns false on narrow viewport',
  )
})

// =========================================================================
// 2. Mobile sidebar overlay and width
// =========================================================================

test('mobile media query positions sidebar as overlay', () => {
  const mobileQuery = chatViewStyle.match(
    /@media\s*\(max-width:\s*520px\)\s*\{([\s\S]*?)\n\}/,
  )
  assert.ok(mobileQuery, 'ChatView must have a @media (max-width: 520px) rule')
  const mobileCSS = mobileQuery[1]
  assert.ok(
    mobileCSS.includes('position: absolute'),
    'Mobile sidebar must be position:absolute (overlay, not in-flow)',
  )
  assert.ok(
    mobileCSS.includes('z-index'),
    'Mobile sidebar must have z-index for overlay layering',
  )
})

test('mobile sidebar open width uses fixed px, not percentage', () => {
  // The desktop &.is-open uses width:18% which is ~70px on 390px — clipped.
  // Mobile must override with a fixed px width.
  const mobileQuery = chatViewStyle.match(
    /@media\s*\(max-width:\s*520px\)\s*\{([\s\S]*?)\n\}/,
  )
  assert.ok(mobileQuery)
  const mobileCSS = mobileQuery[1]
  // Should have a positive .is-open rule (not just :not(.is-open))
  assert.match(
    mobileCSS,
    /&\.is-open|\.conversations\.is-open/,
    'Mobile media query must include a positive .is-open variant for sidebar open width',
  )
  // Should NOT use percentage width on mobile
  assert.doesNotMatch(
    mobileCSS,
    /is-open[^}]*width:\s*\d+%/,
    'Mobile sidebar open width must not use percentage (causes clipping on narrow viewports)',
  )
})

// =========================================================================
// 3. Dark theme token usage — sidebar
// =========================================================================

test('sidebar conversation delete hover uses theme token, not hardcoded #EEE', () => {
  assert.doesNotMatch(
    chatViewStyle,
    /background-color:\s*#EEE/,
    'Must not use hardcoded #EEE background — invisible in dark theme',
  )
  // Should use a CSS variable instead (Less uses &__delete and &:hover)
  assert.match(
    chatViewStyle,
    /__delete[\s\S]*?&:hover[\s\S]*?background-color:\s*var\(--/,
    'conversation__delete hover must use a CSS variable for theme compatibility',
  )
})

test('sidebar header-title uses explicit theme-aware color', () => {
  assert.match(
    chatViewStyle,
    /\.header-title\s*\{[^}]*color:\s*var\(--/,
    '.header-title must have explicit theme-aware color to be visible in dark mode',
  )
})

// =========================================================================
// 4. Dark theme token usage — input box
// =========================================================================

test('input box focus-within uses theme surface token, not hardcoded white', () => {
  assert.doesNotMatch(
    messageInputStyle,
    /focus-within[\s\S]*?background:\s*white/,
    'Input focus background must not be hardcoded white — breaks dark theme',
  )
  assert.match(
    messageInputStyle,
    /focus-within[\s\S]*?(?:background|background-color):\s*var\(--/,
    'Input focus-within must use a CSS variable for background',
  )
})

test('user-input text color uses theme token, not hardcoded #222', () => {
  assert.doesNotMatch(
    messageInputStyle,
    /\.user-input\s*\{[^}]*color:\s*#222/,
    'Input text color must not be hardcoded #222 — invisible in dark theme',
  )
  assert.match(
    messageInputStyle,
    /\.user-input\s*\{[^}]*color:\s*var\(--/,
    'user-input must use a CSS variable for text color',
  )
})

// =========================================================================
// 5. Flex layout — prevent clipping on narrow viewports
// =========================================================================

test('chat flex child has min-width:0 to prevent overflow on narrow viewports', () => {
  // .chat has flex: 5 5 200px — the 200px flex-basis can cause the flex item
  // to exceed the container width on narrow viewports, pushing content off-screen.
  assert.match(
    chatComponentStyle,
    /\.chat\s*\{[^}]*min-width:\s*0/,
    '.chat flex child needs min-width:0 to shrink below flex-basis on narrow viewports',
  )
})

test('mobile bottom composer does not overflow horizontally', () => {
  const mobileQuery = chatComponentStyle.match(
    /@media\s*\(max-width:\s*520px\)\s*\{([\s\S]*?)\n\}/,
  )
  assert.ok(mobileQuery, 'ChatComponent must have a @media (max-width: 520px) rule')
  // The bottom should either have overflow-x:hidden or max-width:100%
  assert.match(
    mobileQuery[1],
    /\.bottom/,
    'Mobile media query must target .bottom for responsive layout',
  )
})

test('input options wrap on mobile to prevent horizontal overflow', () => {
  const mobileQuery = messageInputStyle.match(
    /@media\s*\(max-width:\s*520px\)\s*\{([\s\S]*?)\n\}/,
  )
  assert.ok(mobileQuery, 'MessageInputComponent must have a mobile media query')
  // Options row must wrap or scroll, not overflow
  assert.match(
    mobileQuery[1],
    /input-options|options__left/,
    'Mobile media query must handle options overflow',
  )
})

test('mobile router view does not retain the hidden desktop top-bar offset', () => {
  const mobileStart = appLayoutStyle.indexOf('@media (max-width: 520px)')
  const overrideStart = appLayoutStyle.indexOf(
    '.app-layout #app-router-view.with-top-bar.with-header',
    mobileStart,
  )

  assert.ok(mobileStart >= 0, 'AppLayout must define the mobile breakpoint')
  assert.ok(overrideStart > mobileStart, 'Mobile layout must override the desktop router offset')
  assert.match(
    appLayoutStyle.slice(overrideStart, appLayoutStyle.indexOf('}', overrideStart) + 1),
    /margin-top:\s*0/,
    'Mobile router view must not keep the desktop 8vh top-bar margin',
  )
})

// =========================================================================
// 6. Structural containment — .bottom must hold its child composer
// =========================================================================

test('.bottom has flex-shrink:0 so the flex algorithm never compresses the composer', () => {
  // In the .chat flex column, .bottom is a sticky child. Without flex-shrink:0,
  // the flex algorithm distributes overflow to .bottom proportionally with
  // .chat-box, crushing .bottom to just its padding height (16px) while the
  // actual .input-box child (147px) escapes visually below the viewport.
  assert.match(
    chatComponentStyle,
    /\.bottom\s*\{[^}]*flex-shrink:\s*0/,
    '.bottom needs flex-shrink:0 to prevent flex compression of the composer',
  )
})

test('.chat-box has min-height:0 so it can shrink below content size', () => {
  // .chat-box uses flex-grow to fill space, but without min-height:0 the
  // default min-height:auto prevents it from shrinking below its content,
  // which forces .bottom to absorb the overflow instead.
  assert.match(
    chatComponentStyle,
    /\.chat-box\s*\{[^}]*min-height:\s*0/,
    '.chat-box needs min-height:0 so it can shrink to make room for .bottom',
  )
})

test('.chat-box flex-basis is 0% not auto, so space allocation starts from zero', () => {
  // With flex-basis:auto the chat-box starts at content size and only grows
  // into free space. With 0% it claims equal share of the container, giving
  // .bottom its full intrinsic height first.
  assert.match(
    chatComponentStyle,
    /\.chat-box\s*\{[^}]*flex:\s*1\s+1\s+0%/,
    '.chat-box must use flex: 1 1 0% to allocate space correctly with .bottom',
  )
})
