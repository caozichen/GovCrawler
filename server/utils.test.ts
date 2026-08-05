import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { allocateRunDirectory, formatRunDirectoryName } from './utils.js'

test('formats Windows-safe local timestamp directories', () => {
  const value = formatRunDirectoryName(new Date(2026, 7, 4, 15, 30, 12, 45))
  assert.equal(value, '2026-08-04_15-30-12-045')
  assert.match(value, /^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-\d{3}$/)
})

test('allocates unique directories without leaving the root', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-run-test-'))
  const now = new Date(2026, 7, 4, 15, 30, 12, 45)

  try {
    const first = await allocateRunDirectory(root, now)
    const second = await allocateRunDirectory(root, now)
    assert.notEqual(first, second)
    assert.equal(path.dirname(first), root)
    assert.equal(path.dirname(second), root)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
