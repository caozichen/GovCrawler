import assert from 'node:assert/strict'
import { type ChildProcess, type SpawnOptions } from 'node:child_process'
import { EventEmitter } from 'node:events'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { AppError } from './errors.js'
import { openOutputDirectory, type SpawnProcess } from './open-directory.js'

test('opens an existing directory in a visible detached Explorer process', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-open-directory-'))
  let invocation: { command: string; args: readonly string[]; options: SpawnOptions } | undefined
  let unrefCalled = false
  const spawnProcess: SpawnProcess = (command, args, options) => {
    invocation = { command, args, options }
    const child = new EventEmitter() as ChildProcess
    child.unref = () => {
      unrefCalled = true
    }
    queueMicrotask(() => child.emit('spawn'))
    return child
  }

  try {
    await openOutputDirectory(root, { platform: 'win32', spawnProcess })
    assert.equal(invocation?.command, 'explorer.exe')
    assert.deepEqual(invocation?.args, [root])
    assert.equal(invocation?.options.shell, false)
    assert.equal(invocation?.options.windowsHide, false)
    assert.equal(invocation?.options.detached, true)
    assert.equal(invocation?.options.stdio, 'ignore')
    assert.equal(unrefCalled, true)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('rejects a missing output directory before launching Explorer', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-open-directory-missing-'))
  const missing = path.join(root, 'missing')
  let spawnCalled = false

  try {
    await assert.rejects(
      openOutputDirectory(missing, {
        platform: 'win32',
        spawnProcess: (() => {
          spawnCalled = true
          throw new Error('should not spawn')
        }) as SpawnProcess,
      }),
      (error: unknown) => error instanceof AppError && error.code === 'OUTPUT_DIRECTORY_NOT_FOUND',
    )
    assert.equal(spawnCalled, false)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('rejects an output path that is not a directory', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-open-directory-file-'))
  const filePath = path.join(root, 'result.txt')
  await writeFile(filePath, 'result', 'utf8')

  try {
    await assert.rejects(
      openOutputDirectory(filePath, { platform: 'win32' }),
      (error: unknown) => error instanceof AppError && error.code === 'OUTPUT_PATH_IS_NOT_DIRECTORY',
    )
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('reports Explorer launch failures', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-open-directory-error-'))
  const spawnProcess: SpawnProcess = () => {
    const child = new EventEmitter() as ChildProcess
    child.unref = () => undefined
    queueMicrotask(() => {
      const error = Object.assign(new Error('not found'), { code: 'ENOENT' })
      child.emit('error', error)
    })
    return child
  }

  try {
    await assert.rejects(
      openOutputDirectory(root, { platform: 'win32', spawnProcess }),
      (error: unknown) => error instanceof AppError && error.code === 'OPEN_DIRECTORY_FAILED',
    )
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
