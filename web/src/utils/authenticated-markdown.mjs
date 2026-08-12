// 把 v-html 渲染出的、指向 SAGE 鉴权代理（/api/multimodal/**）的 <img> 转为
// 携带 Bearer 令牌的懒加载图片。
//
// 浏览器原生 <img> 不会给请求附加 Authorization 头，直接指向鉴权代理的
// `<img src="/api/multimodal/pdf/images?...">` 会得到 401。这里把这类 src
// 改写成 data-auth-src（不发起请求），再用 fetchAuthenticatedBlob 取到 Blob
// 后换成 objectURL —— 与 AuthenticatedImage 同一套令牌通道，且按视口懒加载。
import { fetchAuthenticatedBlob } from './authenticated-image.mjs'
import { observeUntilVisible } from './lazy-image.mjs'

// 命中的代理前缀：SAGE 多模态白名单代理都挂在 /api/multimodal/ 下。
const PROXIED_PREFIX = '/api/multimodal/'

// 重写 HTML：把 <img src="/api/multimodal/..."> 改为 data-auth-src（不带 src，
// 避免空 src 触发对当前页面的请求），并加占位样式。
export const rewriteProxiedImageSrcs = (html) => {
  if (!html || !html.includes(PROXIED_PREFIX)) return html
  return html.replace(
    /<img\b([^>]*?)\bsrc="(\/api\/multimodal\/[^"]*)"/gi,
    (match, head, proxiedUrl) => {
      const cleaned = head
        .replace(/\bdata-auth-src="[^"]*"/gi, '')
        .replace(/\bsrc=""?/gi, '')
      return `<img${cleaned} data-auth-src="${proxiedUrl}" loading="lazy" decoding="async"`
    },
  )
}

const revokeAll = (objectUrls) => {
  objectUrls.forEach((url) => {
    try {
      URL.revokeObjectURL(url)
    } catch {
      /* no-op */
    }
  })
  objectUrls.clear()
}

// 对容器内所有 data-auth-src 图片执行鉴权懒加载；返回清理函数。
// 返回的 Promise 在全部可见图片加载完成后 resolve（用于测试等待）。
export const hydrateAuthenticatedImages = (root, token, fetchImpl = fetch) => {
  if (!root || typeof root.querySelectorAll !== 'function') return () => {}
  const objectUrls = new Set()
  const imgs = Array.from(root.querySelectorAll('img[data-auth-src]'))
  let active = 0
  let settled = false
  let resolveAll = null
  const done = new Promise((resolve) => {
    resolveAll = resolve
  })

  const maybeSettled = () => {
    if (settled && active === 0) resolveAll?.()
  }

  for (const img of imgs) {
    const source = img.getAttribute('data-auth-src')
    if (!source || img.dataset.sageHydrated) continue
    img.dataset.sageHydrated = '1'
    img.classList.add('sage-auth-image')

    const load = async () => {
      active += 1
      try {
        const blob = await fetchAuthenticatedBlob(source, token, fetchImpl)
        if (img.dataset.sageRevoked) return
        const objectUrl = URL.createObjectURL(blob)
        objectUrls.add(objectUrl)
        img.src = objectUrl
        img.removeAttribute('style')
        img.classList.add('sage-auth-image-loaded')
      } catch (err) {
        if (err?.name !== 'AbortError' && !img.dataset.sageRevoked) {
          img.dataset.sageHydrated = 'error'
          img.alt = '图片加载失败'
        }
      } finally {
        active -= 1
        maybeSettled()
      }
    }

    observeUntilVisible(img, () => {
      load()
    })
  }

  settled = true
  maybeSettled()

  return () => {
    imgs.forEach((img) => {
      img.dataset.sageRevoked = '1'
      img.removeAttribute('src')
    })
    revokeAll(objectUrls)
  }
}

export const revokeAuthenticatedImageUrls = revokeAll
