import { randomUUID } from 'node:crypto'
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import path from 'node:path'
import type { CrawlerTask, PersistedState } from './types.js'

function createDefaultState(projectRoot: string): PersistedState {
  const now = new Date().toISOString()
  const exampleTask: CrawlerTask = {
    id: randomUUID(),
    name: '示例网页采集',
    url: 'https://example.com',
    scriptPath: path.join(projectRoot, 'crawlers', 'example.py'),
    pythonPath: '',
    enabled: false,
    intervalMinutes: 30,
    timeoutSeconds: 120,
    extraArgs: [],
    createdAt: now,
    updatedAt: now,
  }

  return {
    schemaVersion: 1,
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
      this.recoverInterruptedRuns()
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      this.state = createDefaultState(this.projectRoot)
    }
    this.state.scheduler = { running: false }
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

  private async persist(): Promise<void> {
    const temporaryPath = `${this.statePath}.tmp`
    await writeFile(temporaryPath, `${JSON.stringify(this.state, null, 2)}\n`, 'utf8')
    await rename(temporaryPath, this.statePath)
  }
}
