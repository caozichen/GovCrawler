import assert from 'node:assert/strict'
import test from 'node:test'
import { paginateItems } from './pagination'

test('paginates items and reports the visible range', () => {
  const result = paginateItems(Array.from({ length: 45 }, (_, index) => index + 1), 2, 20)

  assert.deepEqual(result.items, Array.from({ length: 20 }, (_, index) => index + 21))
  assert.equal(result.page, 2)
  assert.equal(result.totalPages, 3)
  assert.equal(result.start, 21)
  assert.equal(result.end, 40)
})

test('clamps an out-of-range page to the last populated page', () => {
  const result = paginateItems(Array.from({ length: 21 }, (_, index) => index + 1), 9, 20)

  assert.deepEqual(result.items, [21])
  assert.equal(result.page, 2)
  assert.equal(result.start, 21)
  assert.equal(result.end, 21)
})

test('represents an empty collection as page one with an empty range', () => {
  const result = paginateItems([], 3, 20)

  assert.deepEqual(result.items, [])
  assert.equal(result.page, 1)
  assert.equal(result.totalPages, 1)
  assert.equal(result.start, 0)
  assert.equal(result.end, 0)
})

test('rejects an invalid page size', () => {
  assert.throws(() => paginateItems([], 1, 0), RangeError)
})
