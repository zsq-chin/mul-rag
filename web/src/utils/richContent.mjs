import createDOMPurify from 'dompurify'
import { marked } from 'marked'

const TABLE_MARKUP = /<\/?(?:table|thead|tbody|tr|th|td)\b/i

const ALLOWED_TAGS = [
  'p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'ul', 'ol', 'li',
  'blockquote', 'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr',
]

const ALLOWED_ATTR = [
  'href', 'target', 'rel', 'src', 'alt', 'title', 'rowspan', 'colspan',
  'loading',
]

export function renderRichContent(input, window) {
  const source = String(input || '')
  const html = TABLE_MARKUP.test(source) ? source : marked.parse(source)
  const purifier = createDOMPurify(window)

  purifier.addHook('afterSanitizeAttributes', node => {
    if (node.tagName === 'IMG') {
      node.setAttribute('loading', 'lazy')
    }
    if (node.tagName === 'A' && node.getAttribute('target') === '_blank') {
      node.setAttribute('rel', 'noopener noreferrer')
    }
  })

  return purifier.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_UNKNOWN_PROTOCOLS: false,
  })
}
