import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'

import { renderRichContent } from '../src/utils/richContent.mjs'

test('keeps table spans and removes executable markup', () => {
  const window = new JSDOM('').window
  const html = renderRichContent(
    '<table><tr><td rowspan="2" colspan="3" onclick="alert(1)">A</td></tr></table><script>alert(2)</script>',
    window,
  )

  assert.match(html, /rowspan="2"/)
  assert.match(html, /colspan="3"/)
  assert.doesNotMatch(html, /onclick|script|alert/)
})

test('renders a markdown table', () => {
  const window = new JSDOM('').window
  const html = renderRichContent('| A | B |\n| - | - |\n| 1 | 2 |', window)

  assert.match(html, /<table>/)
  assert.match(html, /<td>1<\/td>/)
})

test('removes dangerous link protocols', () => {
  const window = new JSDOM('').window
  const html = renderRichContent('[run](javascript:alert(1))', window)

  assert.doesNotMatch(html, /javascript:|alert/)
})
