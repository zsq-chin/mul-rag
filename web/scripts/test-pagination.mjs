import assert from 'node:assert/strict'

import { clampPage, paginateItems } from '../src/utils/pagination.mjs'

const items = Array.from({ length: 100 }, (_, index) => ({ id: index + 1 }))

assert.deepEqual(
  paginateItems(items, 1, 24).map((item) => item.id),
  Array.from({ length: 24 }, (_, index) => index + 1),
  'first page should only expose the first 24 items',
)

assert.deepEqual(
  paginateItems(items, 5, 24).map((item) => item.id),
  [97, 98, 99, 100],
  'last page should expose only remaining items',
)

assert.equal(clampPage(99, items.length, 24), 5)
assert.equal(clampPage(0, items.length, 24), 1)
assert.equal(clampPage(3, 0, 24), 1)
