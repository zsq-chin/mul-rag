export const observeUntilVisible = (
  element,
  onVisible,
  Observer = globalThis.IntersectionObserver,
) => {
  if (!element || typeof Observer !== 'function') {
    onVisible()
    return () => {}
  }

  let disconnected = false
  const observer = new Observer((entries) => {
    if (disconnected || !entries.some(entry => entry.isIntersecting)) return
    disconnected = true
    observer.disconnect()
    onVisible()
  }, { rootMargin: '200px 0px' })

  observer.observe(element)
  return () => {
    if (disconnected) return
    disconnected = true
    observer.disconnect()
  }
}
