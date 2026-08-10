import { randomUUID } from 'node:crypto'
import type {
  CrawlerTask,
  OperationAction,
  OperationChange,
  OperationLog,
  OperationRunMode,
  RunRecord,
  TaskCategory,
} from './types.js'

type TaskOperationAction = Extract<OperationAction, 'task-created' | 'task-updated' | 'task-deleted'>
type AuditedTaskField =
  | 'categoryId'
  | 'name'
  | 'url'
  | 'scriptPath'
  | 'details'
  | 'pythonPath'
  | 'enabled'
  | 'intervalMinutes'
  | 'timeoutSeconds'
  | 'extraArgs'

const AUDITED_TASK_FIELDS: Array<{ field: AuditedTaskField; label: string }> = [
  { field: 'categoryId', label: '任务分类' },
  { field: 'name', label: '任务名称' },
  { field: 'url', label: '目标网址' },
  { field: 'scriptPath', label: '脚本文件' },
  { field: 'details', label: '脚本详情' },
  { field: 'pythonPath', label: 'Python 解释器' },
  { field: 'enabled', label: '启用状态' },
  { field: 'intervalMinutes', label: '运行间隔' },
  { field: 'timeoutSeconds', label: '超时时间' },
  { field: 'extraArgs', label: '额外参数' },
]

function valuesEqual(before: CrawlerTask[AuditedTaskField], after: CrawlerTask[AuditedTaskField]): boolean {
  return JSON.stringify(before) === JSON.stringify(after)
}

function formatTaskValue(
  task: CrawlerTask,
  field: AuditedTaskField,
  categories: readonly TaskCategory[],
): string {
  const value = task[field]
  if (field === 'categoryId') return categories.find((category) => category.id === value)?.name || String(value)
  if (field === 'enabled') return value ? '启用' : '停用'
  if (field === 'intervalMinutes') return `${value} 分钟`
  if (field === 'timeoutSeconds') return `${value} 秒`
  if (field === 'extraArgs') return (value as string[]).length > 0 ? (value as string[]).join(' ') : '无'
  if (field === 'pythonPath') return value ? String(value) : '跟随系统设置'
  if (field === 'details') return value ? String(value) : '未填写'
  return String(value)
}

export function taskChanges(
  before: CrawlerTask | undefined,
  after: CrawlerTask | undefined,
  categories: readonly TaskCategory[],
): OperationChange[] {
  return AUDITED_TASK_FIELDS.flatMap(({ field, label }) => {
    if (before && after && valuesEqual(before[field], after[field])) return []
    return [{
      field,
      label,
      before: before ? formatTaskValue(before, field, categories) : undefined,
      after: after ? formatTaskValue(after, field, categories) : undefined,
    }]
  })
}

export function createTaskOperation(input: {
  action: TaskOperationAction
  operator: string
  before?: CrawlerTask
  after?: CrawlerTask
  categories: readonly TaskCategory[]
  occurredAt?: string
}): OperationLog {
  const task = input.after || input.before
  if (!task) throw new Error('Task operation requires a before or after value')
  const changes = taskChanges(input.before, input.after, input.categories)
  const verb = {
    'task-created': '新增',
    'task-updated': '编辑',
    'task-deleted': '删除',
  }[input.action]
  const noChanges = input.action === 'task-updated' && changes.length === 0 ? '，配置未发生变化' : ''

  return {
    id: randomUUID(),
    operator: input.operator,
    action: input.action,
    occurredAt: input.occurredAt || new Date().toISOString(),
    taskId: task.id,
    taskName: task.name,
    summary: `${verb}脚本任务“${task.name}”${noChanges}`,
    changes,
  }
}

export function createRunStartedOperation(input: {
  operator: string
  mode: OperationRunMode
  run: RunRecord
}): OperationLog {
  const modeLabel = input.mode === 'batch' ? '批量运行' : '单个运行'
  return {
    id: randomUUID(),
    operator: input.operator,
    action: 'run-started',
    occurredAt: input.run.queuedAt,
    taskId: input.run.taskId,
    taskName: input.run.taskName,
    summary: `开始${modeLabel}脚本“${input.run.taskName}”`,
    runId: input.run.id,
    runMode: input.mode,
    changes: [
      { field: 'runMode', label: '运行方式', after: modeLabel },
      { field: 'runId', label: '运行编号', after: input.run.id },
      { field: 'startedAt', label: '开始运行时间', after: input.run.queuedAt },
    ],
  }
}

export function createRunStoppedOperation(input: {
  operator: string
  run: RunRecord
  stoppedAt?: string
}): OperationLog {
  const stoppedAt = input.stoppedAt || new Date().toISOString()
  return {
    id: randomUUID(),
    operator: input.operator,
    action: 'run-stopped',
    occurredAt: stoppedAt,
    taskId: input.run.taskId,
    taskName: input.run.taskName,
    summary: `停止脚本“${input.run.taskName}”`,
    runId: input.run.id,
    changes: [
      { field: 'runId', label: '运行编号', after: input.run.id },
      { field: 'stoppedAt', label: '停止时间', after: stoppedAt },
    ],
  }
}
