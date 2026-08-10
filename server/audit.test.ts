import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createRunStartedOperation,
  createRunStoppedOperation,
  createTaskOperation,
  taskChanges,
} from './audit.js'
import { DEFAULT_TASK_CATEGORIES } from './store.js'
import type { CrawlerTask, RunRecord } from './types.js'

const task: CrawlerTask = {
  id: 'task-1',
  categoryId: 'agency',
  name: '示例脚本',
  url: 'https://example.com',
  scriptPath: 'crawlers/example.py',
  details: '采集示例页面',
  pythonPath: '',
  enabled: false,
  intervalMinutes: 30,
  timeoutSeconds: 120,
  extraArgs: [],
  createdAt: '2026-08-10T00:00:00.000Z',
  updatedAt: '2026-08-10T00:00:00.000Z',
}

const run: RunRecord = {
  id: 'run-1',
  taskId: task.id,
  taskName: task.name,
  categoryId: task.categoryId,
  trigger: 'run-all',
  status: 'queued',
  queuedAt: '2026-08-10T01:02:03.000Z',
  outputDir: 'output/run-1',
}

test('task edit audit includes only fields whose values changed', () => {
  const after = {
    ...task,
    url: 'https://example.com/updated',
    enabled: true,
    extraArgs: ['--limit', '20'],
  }

  const changes = taskChanges(task, after, DEFAULT_TASK_CATEGORIES)

  assert.deepEqual(changes.map((change) => change.field), ['url', 'enabled', 'extraArgs'])
  assert.deepEqual(changes.find((change) => change.field === 'enabled'), {
    field: 'enabled',
    label: '启用状态',
    before: '停用',
    after: '启用',
  })
})

test('task create and delete audit retain a concrete task snapshot', () => {
  const created = createTaskOperation({
    action: 'task-created',
    operator: 'admin',
    after: task,
    categories: DEFAULT_TASK_CATEGORIES,
  })
  const deleted = createTaskOperation({
    action: 'task-deleted',
    operator: 'admin',
    before: task,
    categories: DEFAULT_TASK_CATEGORIES,
  })

  assert.equal(created.changes.length, 10)
  assert.equal(created.changes.find((change) => change.field === 'categoryId')?.after, '机关单位')
  assert.equal(deleted.changes.length, 10)
  assert.equal(deleted.changes.find((change) => change.field === 'scriptPath')?.before, task.scriptPath)
})

test('run audit records batch mode and explicit start and stop times', () => {
  const started = createRunStartedOperation({ operator: 'admin', mode: 'batch', run })
  const stopped = createRunStoppedOperation({
    operator: 'admin',
    run,
    stoppedAt: '2026-08-10T01:05:00.000Z',
  })

  assert.equal(started.runMode, 'batch')
  assert.equal(started.changes.find((change) => change.field === 'runMode')?.after, '批量运行')
  assert.equal(started.occurredAt, run.queuedAt)
  assert.equal(stopped.occurredAt, '2026-08-10T01:05:00.000Z')
  assert.equal(stopped.changes.find((change) => change.field === 'stoppedAt')?.after, stopped.occurredAt)
})
