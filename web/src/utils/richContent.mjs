import createDOMPurify from 'dompurify'
import { marked } from 'marked'

const TABLE_MARKUP = /<\/?(?:table|thead|tbody|tr|th|td)\b/i

const ALLOWED_TAGS = [
  'p', 'br', 'strong', 'em', 'del', 'code', 'pre', 'ul', 'ol', 'li',
  'blockquote', 'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'span',
]

const ALLOWED_ATTR = [
  'href', 'target', 'rel', 'src', 'alt', 'title', 'rowspan', 'colspan',
  'loading', 'data-rich-caption',
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

/**
 * Strip raw <img> tags from sanitized HTML, replacing each with its alt text
 * wrapped in a <span>.  Used when authenticated images are supplied separately
 * so that unauthenticated inline image elements are not rendered.
 */
export function stripInlineImages(html, window) {
  if (!html || !html.includes('<img')) return html

  const doc = new window.DOMParser().parseFromString(html, 'text/html')
  for (const img of Array.from(doc.querySelectorAll('img'))) {
    const alt = img.getAttribute('alt')
    if (alt) {
      const span = doc.createElement('span')
      span.setAttribute('data-rich-caption', '')
      span.textContent = alt
      img.replaceWith(span)
    } else {
      img.remove()
    }
  }
  return doc.body.innerHTML
}

const MARKDOWN_IMAGE_RE = /!\[([^\]]*)\]\(([^)]+)\)/g

/**
 * Strip raw markdown image syntax `![alt](url)` from text, replacing each
 * occurrence with the alt text wrapped in a marker span.  This handles cases
 * where `marked` cannot parse the image (e.g. spaces in the path) so that
 * raw markdown fragments are not rendered to the user.
 */
export function stripMarkdownImageSyntax(source) {
  if (!source || !source.includes('![')) return source
  return source.replace(MARKDOWN_IMAGE_RE, (_match, alt) => {
    if (!alt) return ''
    return `<span data-rich-caption>${alt}</span>`
  })
}
