import assert from 'node:assert/strict'
import { mkdir, mkdtemp, readdir, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import {
  BUILTIN_CRAWLER_CATALOG_VERSION,
  BUILTIN_CRAWLER_DEFINITIONS,
} from './builtin-tasks.js'
import {
  DEFAULT_TASK_CATEGORIES,
  defaultPythonCommand,
  inferTaskCategoryId,
  MAX_OPERATION_LOGS,
  StateStore,
} from './store.js'

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

test('infers the three default categories from existing task names', () => {
  assert.equal(inferTaskCategoryId('北京市交通委员会招聘公告'), 'municipal')
  assert.equal(inferTaskCategoryId('北京市朝阳区公开招聘'), 'district')
  assert.equal(inferTaskCategoryId('北京市人民政府门户网站招聘公告'), 'agency')
})

test('registers every repository crawler with portable defaults on first start', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-builtin-crawlers-'))

  try {
    const store = new StateStore(root)
    await store.init()
    const state = store.snapshot()
    const catalogPaths = BUILTIN_CRAWLER_DEFINITIONS.map((definition) => definition.scriptPath).sort()
    const crawlerPaths = (await readdir(path.join(repositoryRoot, 'crawlers')))
      .filter((filename) => filename.endsWith('.py') && filename !== '__init__.py')
      .map((filename) => `crawlers/${filename}`)
      .sort()

    assert.equal(state.schemaVersion, 4)
    assert.equal(state.builtinCatalogVersion, BUILTIN_CRAWLER_CATALOG_VERSION)
    assert.equal(state.tasks.length, BUILTIN_CRAWLER_DEFINITIONS.length)
    assert.deepEqual(catalogPaths, crawlerPaths)
    assert.deepEqual(state.tasks.map((task) => task.scriptPath).sort(), catalogPaths)
    assert.ok(state.tasks.every((task) => !path.isAbsolute(task.scriptPath) && !task.scriptPath.includes('\\')))
    assert.ok(state.tasks.every((task) => state.categories.some((category) => category.id === task.categoryId)))
    assert.equal(state.settings.pythonPath, defaultPythonCommand())
    assert.equal(defaultPythonCommand('win32'), 'python')
    assert.equal(defaultPythonCommand('darwin'), 'python3')
  } finally {
    await rm(root, { recursive: true, force: true })
  }
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
    assert.equal(migrated.schemaVersion, 4)
    assert.equal(migrated.builtinCatalogVersion, BUILTIN_CRAWLER_CATALOG_VERSION)
    assert.deepEqual(migrated.categories, DEFAULT_TASK_CATEGORIES)
    assert.equal(migrated.tasks[0]?.categoryId, 'district')
    assert.equal(migrated.tasks.length, BUILTIN_CRAWLER_DEFINITIONS.length + 1)
    assert.equal(migrated.runs[0]?.categoryId, 'district')
    assert.deepEqual(migrated.operationLogs, [])
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('imports missing built-ins once without overwriting existing task configuration', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-builtin-migration-'))
  const stateDirectory = path.join(root, '.crawler-console')
  await mkdir(stateDirectory, { recursive: true })
  const exampleId = 'existing-example'
  const customId = 'custom-task'
  const existingState = {
    schemaVersion: 3,
    categories: DEFAULT_TASK_CATEGORIES,
    tasks: [
      {
        id: exampleId,
        categoryId: 'agency',
        name: '我修改过的示例任务',
        url: 'https://example.org/custom',
        scriptPath: path.join(root, 'crawlers', 'example.py'),
        details: '保留我的说明',
        pythonPath: '/custom/python',
        enabled: true,
        intervalMinutes: 45,
        timeoutSeconds: 321,
        extraArgs: ['--custom'],
        createdAt: '2026-01-01T00:00:00.000Z',
        updatedAt: '2026-01-02T00:00:00.000Z',
      },
      {
        id: customId,
        categoryId: 'agency',
        name: '自定义脚本',
        url: 'https://example.net',
        scriptPath: path.join(root, 'other', 'example.py'),
        details: 'custom',
        pythonPath: '',
        enabled: false,
        intervalMinutes: 30,
        timeoutSeconds: 120,
        extraArgs: [],
        createdAt: '',
        updatedAt: '',
      },
    ],
    runs: [],
    operationLogs: [],
    settings: {
      pythonPath: 'python-custom',
      outputRoot: root,
      maxConcurrency: 1,
      defaultTimeoutSeconds: 120,
      retentionDays: 30,
    },
    scheduler: { running: false },
  }
  await writeFile(path.join(stateDirectory, 'state.json'), JSON.stringify(existingState), 'utf8')

  try {
    const firstStore = new StateStore(root)
    await firstStore.init()
    const first = firstStore.snapshot()
    const existingExample = first.tasks.find((task) => task.id === exampleId)
    const customTask = first.tasks.find((task) => task.id === customId)

    assert.equal(first.tasks.length, BUILTIN_CRAWLER_DEFINITIONS.length + 1)
    assert.equal(existingExample?.builtinId, 'builtin-example')
    assert.equal(existingExample?.scriptPath, 'crawlers/example.py')
    assert.equal(existingExample?.name, '我修改过的示例任务')
    assert.equal(existingExample?.url, 'https://example.org/custom')
    assert.equal(existingExample?.details, '保留我的说明')
    assert.equal(existingExample?.pythonPath, '/custom/python')
    assert.equal(existingExample?.enabled, true)
    assert.deepEqual(existingExample?.extraArgs, ['--custom'])
    assert.equal(customTask?.scriptPath, path.join(root, 'other', 'example.py'))
    assert.equal(first.settings.pythonPath, 'python-custom')

    const firstIds = first.tasks.map((task) => task.id)
    const secondStore = new StateStore(root)
    await secondStore.init()
    assert.deepEqual(secondStore.snapshot().tasks.map((task) => task.id), firstIds)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('assigns imported built-ins to an existing category when defaults were replaced', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-builtin-category-'))
  const stateDirectory = path.join(root, '.crawler-console')
  const existingExampleId = 'legacy-example'
  await mkdir(stateDirectory, { recursive: true })
  await writeFile(path.join(stateDirectory, 'state.json'), JSON.stringify({
    schemaVersion: 3,
    categories: [{ id: 'custom-category', name: '自定义分类', color: '#123456' }],
    tasks: [{
      id: existingExampleId,
      categoryId: 'agency',
      name: '示例网页采集',
      url: 'https://example.com',
      scriptPath: path.join(root, 'crawlers', 'example.py'),
      details: '',
      pythonPath: '',
      enabled: false,
      intervalMinutes: 30,
      timeoutSeconds: 120,
      extraArgs: [],
      createdAt: '',
      updatedAt: '',
    }],
    runs: [{
      id: 'legacy-run',
      taskId: existingExampleId,
      taskName: '示例网页采集',
      categoryId: 'agency',
      trigger: 'manual',
      status: 'succeeded',
      queuedAt: '',
      outputDir: root,
    }],
    operationLogs: [],
    settings: {
      pythonPath: 'python',
      outputRoot: root,
      maxConcurrency: 1,
      defaultTimeoutSeconds: 120,
      retentionDays: 30,
    },
    scheduler: { running: false },
  }), 'utf8')

  try {
    const store = new StateStore(root)
    await store.init()
    const migrated = store.snapshot()
    assert.equal(migrated.tasks.length, BUILTIN_CRAWLER_DEFINITIONS.length)
    assert.ok(migrated.tasks.every((task) => task.categoryId === 'custom-category'))
    assert.equal(migrated.tasks.find((task) => task.id === existingExampleId)?.builtinId, 'builtin-example')
    assert.equal(migrated.runs[0]?.categoryId, 'custom-category')
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
