import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { AppError } from './errors.js'
import { StateStore } from './store.js'
import type { AppSettings, CrawlerTask, RunRecord } from './types.js'
import { buildSpawnSpec, RunManager } from './runner.js'

test('builds a structured Python invocation without a shell command string', () => {
  const root = path.resolve('project root')
  const task = {
    id: 'task-1',
    categoryId: 'agency',
    name: 'Example',
    url: 'https://example.com?q=a b',
    scriptPath: path.join('crawlers', 'example.py'),
    details: 'Example crawler',
    pythonPath: '',
    enabled: true,
    intervalMinutes: 5,
    timeoutSeconds: 60,
    extraArgs: ['--mode', 'full scan'],
    createdAt: '',
    updatedAt: '',
  } satisfies CrawlerTask
  const settings = {
    pythonPath: 'python',
    outputRoot: root,
    maxConcurrency: 1,
    defaultTimeoutSeconds: 60,
    retentionDays: 30,
  } satisfies AppSettings
  const run = {
    id: 'run-1',
    taskId: task.id,
    taskName: task.name,
    categoryId: task.categoryId,
    trigger: 'manual',
    status: 'queued',
    queuedAt: '',
    outputDir: path.join(root, '2026-08-04_15-30-12-000'),
  } satisfies RunRecord

  const spec = buildSpawnSpec(root, task, settings, run)
  assert.equal(spec.command, 'python')
  assert.deepEqual(spec.args.slice(1), [
    '--url',
    task.url,
    '--output-dir',
    run.outputDir,
    '--run-id',
    run.id,
    '--mode',
    'full scan',
  ])
})

test('reserves a task before asynchronous run directory allocation', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-run-lock-'))
  const store = new StateStore(root)
  await store.init()
  const manager = new RunManager(root, store)
  const taskId = store.snapshot().tasks[0]?.id
  assert.ok(taskId)
  await store.update((draft) => {
    const task = draft.tasks.find((item) => item.id === taskId)
    if (task) task.enabled = true
  })

  try {
    const results = await Promise.allSettled([
      manager.enqueue(taskId, 'manual'),
      manager.enqueue(taskId, 'manual'),
    ])
    assert.equal(results.filter((result) => result.status === 'fulfilled').length, 1)
    const rejected = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
    assert.ok(rejected)
    assert.ok(rejected.reason instanceof AppError)
    assert.equal(rejected.reason.code, 'RUN_ALREADY_ACTIVE')

    const deadline = Date.now() + 2_000
    while (manager.isTaskBusy(taskId) && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 20))
    }
    assert.equal(manager.isTaskBusy(taskId), false)
  } finally {
    await manager.stopAll()
    await rm(root, { recursive: true, force: true })
  }
})

test('allows manual execution but rejects scheduled execution for a disabled task', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-disabled-task-'))
  const store = new StateStore(root)
  await store.init()
  const manager = new RunManager(root, store)
  const taskId = store.snapshot().tasks[0]?.id
  assert.ok(taskId)

  try {
    const run = await manager.enqueue(taskId, 'manual')
    assert.equal(run.status, 'queued')
    assert.equal(store.snapshot().runs.length, 1)
    await manager.stopAll()

    await assert.rejects(
      manager.enqueue(taskId, 'schedule'),
      (error: unknown) => error instanceof AppError && error.code === 'TASK_DISABLED',
    )
    assert.equal(manager.isTaskBusy(taskId), false)
  } finally {
    await manager.stopAll()
    await rm(root, { recursive: true, force: true })
  }
})

test('stops a queued task and releases its task lock', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-stop-queued-'))
  const store = new StateStore(root)
  await store.init()
  const manager = new RunManager(root, store)
  const taskId = store.snapshot().tasks[0]?.id
  assert.ok(taskId)
  await store.update((draft) => {
    draft.settings.maxConcurrency = 0
  })

  try {
    const run = await manager.enqueue(taskId, 'manual')
    assert.equal(manager.isTaskBusy(taskId), true)

    await manager.stop(run.id)

    const stoppedRun = store.snapshot().runs.find((item) => item.id === run.id)
    assert.equal(stoppedRun?.status, 'stopped')
    assert.equal(manager.isTaskBusy(taskId), false)
  } finally {
    await manager.stopAll()
    await rm(root, { recursive: true, force: true })
  }
})
