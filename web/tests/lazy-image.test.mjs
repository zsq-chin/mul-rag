import test from 'node:test'
import assert from 'node:assert/strict'
import { observeUntilVisible } from '../src/utils/lazy-image.mjs'

test('blob loading waits until the image enters the viewport', () => {
  let observerCallback
  let observedElement
  let disconnectCount = 0
  let loadCount = 0

  class FakeIntersectionObserver {
    constructor(callback) {
      observerCallback = callback
    }

    observe(element) {
      observedElement = element
    }

    disconnect() {
      disconnectCount += 1
    }
  }

  const element = { id: 'image' }
  const cleanup = observeUntilVisible(element, () => { loadCount += 1 }, FakeIntersectionObserver)

  assert.equal(observedElement, element)
  assert.equal(loadCount, 0)

  observerCallback([{ isIntersecting: false }])
  assert.equal(loadCount, 0)

  observerCallback([{ isIntersecting: true }])
  assert.equal(loadCount, 1)
  assert.equal(disconnectCount, 1)

  cleanup()
  assert.equal(disconnectCount, 1)
})

test('blob loading starts immediately when IntersectionObserver is unavailable', () => {
  let loadCount = 0

  const cleanup = observeUntilVisible({}, () => { loadCount += 1 }, undefined)

  assert.equal(loadCount, 1)
  cleanup()
})
