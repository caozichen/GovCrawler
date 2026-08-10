import assert from 'node:assert/strict'
import test from 'node:test'
import { mapSettledWithConcurrency } from './concurrency.js'

test('limits concurrent workers and preserves result order', async () => {
  let active = 0
  let maxActive = 0
  const results = await mapSettledWithConcurrency([1, 2, 3, 4, 5, 6, 7], 3, async (value) => {
    active += 1
    maxActive = Math.max(maxActive, active)
    await new Promise((resolve) => setTimeout(resolve, 10))
    active -= 1
    return value * 2
  })

  assert.equal(maxActive, 3)
  assert.deepEqual(
    results.map((result) => result.status === 'fulfilled' ? result.value : null),
    [2, 4, 6, 8, 10, 12, 14],
  )
})

test('captures worker failures without stopping remaining work', async () => {
  const results = await mapSettledWithConcurrency([1, 2, 3], 2, async (value) => {
    if (value === 2) throw new Error('failed')
    return value
  })

  assert.equal(results[0]?.status, 'fulfilled')
  assert.equal(results[1]?.status, 'rejected')
  assert.equal(results[2]?.status, 'fulfilled')
})

test('rejects invalid concurrency values', async () => {
  await assert.rejects(() => mapSettledWithConcurrency([1], 0, async (value) => value), RangeError)
})
