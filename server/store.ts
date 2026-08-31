import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import path from 'node:path'
import {
  BUILTIN_CRAWLER_CATALOG_VERSION,
  BUILTIN_CRAWLER_DEFINITIONS,
  createBuiltinTask,
  type BuiltinCrawlerDefinition,
} from './builtin-tasks.js'
import { getDefaultScriptDetails } from './script-details.js'
import type { PersistedState, TaskCategory } from './types.js'

export const MAX_OPERATION_LOGS = 1000

export const DEFAULT_TASK_CATEGORIES: TaskCategory[] = [
  { id: 'municipal', name: '市级单位', color: '#3B82F6' },
  { id: 'district', name: '区级单位', color: '#22A06B' },
  { id: 'agency', name: '机关单位', color: '#D98620' },
]

export function inferTaskCategoryId(taskName: string): string {
  if (/朝阳区|东城区|西城区|通州区|大兴区|海淀区|经济技术开发区/.test(taskName)) return 'district'
  if (/人民政府门户|首都之窗|示例/.test(taskName)) return 'agency'
  return 'municipal'
}

export function defaultPythonCommand(platform: NodeJS.Platform = process.platform): string {
  return platform === 'win32' ? 'python' : 'python3'
}

function createDefaultState(projectRoot: string): PersistedState {
  const now = new Date().toISOString()

  return {
    schemaVersion: 4,
    builtinCatalogVersion: BUILTIN_CRAWLER_CATALOG_VERSION,
    categories: structuredClone(DEFAULT_TASK_CATEGORIES),
    tasks: BUILTIN_CRAWLER_DEFINITIONS.map((definition) => createBuiltinTask(definition, now)),
    runs: [],
    operationLogs: [],
    settings: {
      pythonPath: defaultPythonCommand(),
      outputRoot: projectRoot,
      maxConcurrency: 2,
      defaultTimeoutSeconds: 600,
      retentionDays: 30,
    },
    scheduler: { running: false },
  }
}

export class StateStore {
  private state!: PersistedState
  private lock: Promise<void> = Promise.resolve()
  private readonly stateDir: string
  private readonly statePath: string

  constructor(private readonly projectRoot: string) {
    this.stateDir = path.join(projectRoot, '.crawler-console')
    this.statePath = path.join(this.stateDir, 'state.json')
  }

  async init(): Promise<void> {
    await mkdir(this.stateDir, { recursive: true })
    try {
      this.state = JSON.parse(await readFile(this.statePath, 'utf8')) as PersistedState
      this.normalizeState()
      this.recoverInterruptedRuns()
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      this.state = createDefaultState(this.projectRoot)
    }
    this.state.scheduler = { running: false }
    for (const task of this.state.tasks) task.nextRunAt = undefined
    await this.persist()
  }

  snapshot(): PersistedState {
    return structuredClone(this.state)
  }

  async update(mutator: (draft: PersistedState) => void | Promise<void>): Promise<PersistedState> {
    let release!: () => void
    const previous = this.lock
    this.lock = new Promise<void>((resolve) => {
      release = resolve
    })

    await previous
    try {
      const draft = structuredClone(this.state)
      await mutator(draft)
      draft.runs = draft.runs.slice(0, 500)
      draft.operationLogs = draft.operationLogs.slice(0, MAX_OPERATION_LOGS)
      this.state = draft
      await this.persist()
      return structuredClone(this.state)
    } finally {
      release()
    }
  }

  private recoverInterruptedRuns(): void {
    const finishedAt = new Date().toISOString()
    for (const run of this.state.runs) {
      if (['queued', 'running', 'stopping'].includes(run.status)) {
        run.status = 'interrupted'
        run.finishedAt = finishedAt
        run.errorCode = 'APP_RESTARTED'
        run.errorMessage = '本地控制程序重启，任务状态已恢复为中断。'
      }
    }
  }

  private normalizeState(): void {
    if (!Array.isArray(this.state.operationLogs)) this.state.operationLogs = []
    if (!Array.isArray(this.state.categories) || this.state.categories.length === 0) {
      this.state.categories = structuredClone(DEFAULT_TASK_CATEGORIES)
    }
    const categoryIds = new Set(this.state.categories.map((category) => category.id))
    const fallbackCategoryId = this.state.categories[0]?.id || 'municipal'
    const catalogVersion = Number(this.state.builtinCatalogVersion) || 0
    if (catalogVersion < BUILTIN_CRAWLER_CATALOG_VERSION) this.addMissingBuiltinTasks(categoryIds)
    for (const task of this.state.tasks) {
      if (typeof task.details !== 'string') task.details = getDefaultScriptDetails(task.scriptPath)
      if (!categoryIds.has(task.categoryId)) {
        const inferredCategoryId = inferTaskCategoryId(task.name)
        task.categoryId = categoryIds.has(inferredCategoryId) ? inferredCategoryId : fallbackCategoryId
      }
    }
    for (const run of this.state.runs) {
      if (categoryIds.has(run.categoryId)) continue
      const task = this.state.tasks.find((item) => item.id === run.taskId)
      const inferredCategoryId = inferTaskCategoryId(run.taskName)
      run.categoryId = task?.categoryId
        || (categoryIds.has(inferredCategoryId) ? inferredCategoryId : fallbackCategoryId)
    }
    this.state.builtinCatalogVersion = BUILTIN_CRAWLER_CATALOG_VERSION
    this.state.schemaVersion = 4
  }

  private addMissingBuiltinTasks(categoryIds: ReadonlySet<string>): void {
    const now = new Date().toISOString()
    const fallbackCategoryId = this.state.categories[0]?.id || 'municipal'

    for (const definition of BUILTIN_CRAWLER_DEFINITIONS) {
      const pathMatch = this.state.tasks.find((task) => this.matchesBuiltinPath(task.scriptPath, definition))
      const existing = this.state.tasks.find(
        (task) => task.builtinId === definition.id || task.id === definition.id,
      ) || pathMatch

      if (existing) {
        existing.builtinId = definition.id
        if (existing === pathMatch) existing.scriptPath = definition.scriptPath
        continue
      }

      const categoryId = categoryIds.has(definition.categoryId) ? definition.categoryId : fallbackCategoryId
      this.state.tasks.push(createBuiltinTask(definition, now, categoryId))
    }
  }

  private matchesBuiltinPath(scriptPath: string, definition: BuiltinCrawlerDefinition): boolean {
    const candidate = path.resolve(this.projectRoot, scriptPath.replace(/[\\/]/g, path.sep))
    const expected = path.resolve(this.projectRoot, definition.scriptPath.replaceAll('/', path.sep))
    return process.platform === 'win32'
      ? candidate.toLowerCase() === expected.toLowerCase()
      : candidate === expected
  }

  private async persist(): Promise<void> {
    const temporaryPath = `${this.statePath}.tmp`
    await writeFile(temporaryPath, `${JSON.stringify(this.state, null, 2)}\n`, 'utf8')
    await rename(temporaryPath, this.statePath)
  }
}
