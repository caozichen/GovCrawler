import { randomUUID } from 'node:crypto'
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { getDefaultScriptDetails } from './script-details.js'
import type { CrawlerTask, PersistedState, TaskCategory } from './types.js'

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

function createDefaultState(projectRoot: string): PersistedState {
  const now = new Date().toISOString()
  const exampleTask: CrawlerTask = {
    id: randomUUID(),
    categoryId: 'agency',
    name: '示例网页采集',
    url: 'https://example.com',
    scriptPath: path.join(projectRoot, 'crawlers', 'example.py'),
    details: getDefaultScriptDetails('example.py'),
    pythonPath: '',
    enabled: false,
    intervalMinutes: 30,
    timeoutSeconds: 120,
    extraArgs: [],
    createdAt: now,
    updatedAt: now,
  }

  return {
    schemaVersion: 2,
    categories: structuredClone(DEFAULT_TASK_CATEGORIES),
    tasks: [exampleTask],
    runs: [],
    settings: {
      pythonPath: 'python',
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
    if (!Array.isArray(this.state.categories) || this.state.categories.length === 0) {
      this.state.categories = structuredClone(DEFAULT_TASK_CATEGORIES)
    }
    const categoryIds = new Set(this.state.categories.map((category) => category.id))
    for (const task of this.state.tasks) {
      if (typeof task.details !== 'string') task.details = getDefaultScriptDetails(task.scriptPath)
      if (!categoryIds.has(task.categoryId)) task.categoryId = inferTaskCategoryId(task.name)
    }
    for (const run of this.state.runs) {
      if (categoryIds.has(run.categoryId)) continue
      const task = this.state.tasks.find((item) => item.id === run.taskId)
      run.categoryId = task?.categoryId || inferTaskCategoryId(run.taskName)
    }
    this.state.schemaVersion = 2
  }

  private async persist(): Promise<void> {
    const temporaryPath = `${this.statePath}.tmp`
    await writeFile(temporaryPath, `${JSON.stringify(this.state, null, 2)}\n`, 'utf8')
    await rename(temporaryPath, this.statePath)
  }
}
