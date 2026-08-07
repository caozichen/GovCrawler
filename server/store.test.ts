import assert from 'node:assert/strict'
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { DEFAULT_TASK_CATEGORIES, inferTaskCategoryId, StateStore } from './store.js'

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
    assert.equal(migrated.schemaVersion, 2)
    assert.deepEqual(migrated.categories, DEFAULT_TASK_CATEGORIES)
    assert.equal(migrated.tasks[0]?.categoryId, 'district')
    assert.equal(migrated.runs[0]?.categoryId, 'district')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
