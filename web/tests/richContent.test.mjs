import test from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'

import { renderRichContent, stripInlineImages, stripMarkdownImageSyntax } from '../src/utils/richContent.mjs'

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

test('stripInlineImages replaces img tags with alt text', () => {
  const window = new JSDOM('').window
  const input = '<p>Before</p><img src="/images/foo.png" alt="Figure 1"><p>After</p>'
  const result = stripInlineImages(input, window)

  assert.doesNotMatch(result, /<img/)
  assert.match(result, /data-rich-caption/)
  assert.match(result, /Figure 1/)
  assert.match(result, /Before/)
  assert.match(result, /After/)
})

test('stripInlineImages removes img tags without alt text', () => {
  const window = new JSDOM('').window
  const input = '<p>Text</p><img src="/images/foo.png"><p>More</p>'
  const result = stripInlineImages(input, window)

  assert.doesNotMatch(result, /<img/)
  assert.doesNotMatch(result, /data-rich-caption/)
  assert.match(result, /Text/)
  assert.match(result, /More/)
})

test('stripInlineImages is a no-op when no img tags present', () => {
  const window = new JSDOM('').window
  const input = '<p>No images here</p>'
  const result = stripInlineImages(input, window)

  assert.equal(result, input)
})

test('stripMarkdownImageSyntax replaces raw markdown images with alt text', () => {
  const input = 'Some text ![图 4-2 2# 井含水习投影](/images/图 4-2 2# 井含水习投影.png) more text'
  const result = stripMarkdownImageSyntax(input)

  assert.doesNotMatch(result, /!\[/)
  assert.match(result, /data-rich-caption/)
  assert.match(result, /图 4-2 2# 井含水习投影/)
  assert.match(result, /Some text/)
  assert.match(result, /more text/)
})

test('stripMarkdownImageSyntax is a no-op when no markdown images present', () => {
  const input = 'No images here'
  const result = stripMarkdownImageSyntax(input)

  assert.equal(result, input)
})

test('stripMarkdownImageSyntax handles multiple images', () => {
  const input = '![First](/images/1.png) and ![Second](/images/2.png)'
  const result = stripMarkdownImageSyntax(input)

  assert.doesNotMatch(result, /!\[/)
  assert.match(result, /First/)
  assert.match(result, /Second/)
})

test('full pipeline strips markdown images and rendered img tags', () => {
  const window = new JSDOM('').window
  const content = '![图 4-2 2# 井含水习投影](/images/图 4-2 2# 井含水习投影.png)\n\nSome description'
  const stripped = stripMarkdownImageSyntax(content)
  const rendered = renderRichContent(stripped, window)
  const result = stripInlineImages(rendered, window)

  assert.doesNotMatch(result, /!\[/)
  assert.doesNotMatch(result, /<img/)
  assert.match(result, /data-rich-caption/)
  assert.match(result, /图 4-2 2# 井含水习投影/)
  assert.match(result, /Some description/)
})

test('stripInlineImages preserves table markup', () => {
  const window = new JSDOM('').window
  const input = '<table><tr><td>Cell</td></tr></table><img src="/images/x.png" alt="X">'
  const result = stripInlineImages(input, window)

  assert.match(result, /<table>/)
  assert.match(result, /<td>Cell<\/td>/)
  assert.doesNotMatch(result, /<img/)
  assert.match(result, /data-rich-caption.*X/)
})
