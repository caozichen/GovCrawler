import assert from 'node:assert/strict'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { DEFAULT_TASK_CATEGORIES, inferTaskCategoryId, MAX_OPERATION_LOGS, StateStore } from './store.js'

test('infers the three default categories from existing task names', () => {
  assert.equal(inferTaskCategoryId('北京市交通委员会招聘公告'), 'municipal')
  assert.equal(inferTaskCategoryId('北京市朝阳区公开招聘'), 'district')
  assert.equal(inferTaskCategoryId('北京市人民政府门户网站招聘公告'), 'agency')
})

test('migrates legacy tasks and runs to category-aware state', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-category-migration-'))
  const stateDirectory = path.join(root, '.crawler-console')
  await mkdir(stateDirectory, { recursive: true })
  const taskId = 'legacy-task'
  const legacyState = {
    schemaVersion: 1,
    tasks: [{
      id: taskId,
      name: '北京市海淀区招聘公告',
      url: 'https://example.com',
      scriptPath: path.join(root, 'crawler.py'),
      details: '',
      pythonPath: '',
      enabled: true,
      intervalMinutes: 30,
      timeoutSeconds: 120,
      extraArgs: [],
      createdAt: '',
      updatedAt: '',
    }],
    runs: [{
      id: 'legacy-run',
      taskId,
      taskName: '北京市海淀区招聘公告',
      trigger: 'manual',
      status: 'succeeded',
      queuedAt: '',
      outputDir: root,
    }],
    settings: {
      pythonPath: 'python',
      outputRoot: root,
      maxConcurrency: 1,
      defaultTimeoutSeconds: 120,
      retentionDays: 30,
    },
    scheduler: { running: false },
  }
  await writeFile(path.join(stateDirectory, 'state.json'), JSON.stringify(legacyState), 'utf8')

  try {
    const store = new StateStore(root)
    await store.init()
    const migrated = store.snapshot()
    assert.equal(migrated.schemaVersion, 3)
    assert.deepEqual(migrated.categories, DEFAULT_TASK_CATEGORIES)
    assert.equal(migrated.tasks[0]?.categoryId, 'district')
    assert.equal(migrated.runs[0]?.categoryId, 'district')
    assert.deepEqual(migrated.operationLogs, [])
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('retains only the most recent operation logs', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-operation-retention-'))

  try {
    const store = new StateStore(root)
    await store.init()
    const task = store.snapshot().tasks[0]
    assert.ok(task)

    await store.update((draft) => {
      for (let index = 0; index < MAX_OPERATION_LOGS + 5; index += 1) {
        draft.operationLogs.unshift({
          id: `operation-${index}`,
          operator: 'admin',
          action: 'task-updated',
          occurredAt: new Date(index).toISOString(),
          taskId: task.id,
          taskName: task.name,
          summary: `编辑脚本任务“${task.name}”`,
          changes: [],
        })
      }
    })

    const retained = store.snapshot().operationLogs
    assert.equal(retained.length, MAX_OPERATION_LOGS)
    assert.equal(retained[0]?.id, `operation-${MAX_OPERATION_LOGS + 4}`)
    assert.equal(retained.at(-1)?.id, 'operation-5')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
