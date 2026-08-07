export type RunStatus =
  | 'queued'
  | 'running'
  | 'stopping'
  | 'succeeded'
  | 'failed'
  | 'timed_out'
  | 'stopped'
  | 'interrupted'

export interface TaskCategory {
  id: string
  name: string
  color: string
}

export interface CrawlerTask {
  id: string
  categoryId: string
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

export type TaskPayload = Omit<
  CrawlerTask,
  'id' | 'details' | 'createdAt' | 'updatedAt' | 'lastAttemptAt' | 'lastSuccessAt' | 'nextRunAt'
>

export interface RunRecord {
  id: string
  taskId: string
  taskName: string
  categoryId: string
  trigger: 'manual' | 'schedule' | 'run-all'
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

export interface DashboardSummary {
  taskCount: number
  enabledCount: number
  activeCount: number
  successToday: number
  failedToday: number
  scheduler: { running: boolean; startedAt?: string }
}

export interface ValidationResult {
  valid: boolean
  checks: Record<string, boolean>
}

export type PathPickerKind = 'python-script' | 'python-executable' | 'directory'
