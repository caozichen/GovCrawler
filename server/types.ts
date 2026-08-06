export type RunTrigger = 'manual' | 'schedule' | 'run-all'

export type RunStatus =
  | 'queued'
  | 'running'
  | 'stopping'
  | 'succeeded'
  | 'failed'
  | 'timed_out'
  | 'stopped'
  | 'interrupted'

export interface CrawlerTask {
  id: string
  name: string
  url: string
  scriptPath: string
  details: string
  pythonPath: string
  enabled: boolean
  intervalMinutes: number
  timeoutSeconds: number
  extraArgs: string[]
  createdAt: string
  updatedAt: string
  lastAttemptAt?: string
  lastSuccessAt?: string
  nextRunAt?: string
}

export interface RunRecord {
  id: string
  taskId: string
  taskName: string
  trigger: RunTrigger
  status: RunStatus
  queuedAt: string
  startedAt?: string
  finishedAt?: string
  durationMs?: number
  outputDir: string
  exitCode?: number
  recordCount?: number
  errorCode?: string
  errorMessage?: string
}

export interface AppSettings {
  pythonPath: string
  outputRoot: string
  maxConcurrency: number
  defaultTimeoutSeconds: number
  retentionDays: number
}

export interface SchedulerState {
  running: boolean
  startedAt?: string
}

export interface PersistedState {
  schemaVersion: 1
  tasks: CrawlerTask[]
  runs: RunRecord[]
  settings: AppSettings
  scheduler: SchedulerState
}

export interface TaskInput {
  name: string
  url: string
  scriptPath: string
  pythonPath?: string
  enabled: boolean
  intervalMinutes: number
  timeoutSeconds: number
  extraArgs?: string[]
}
