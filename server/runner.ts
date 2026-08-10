import { EventEmitter } from 'node:events'
import { randomUUID } from 'node:crypto'
import { createReadStream, createWriteStream } from 'node:fs'
import { readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { spawn, type ChildProcess } from 'node:child_process'
import { createRunStartedOperation } from './audit.js'
import { AppError } from './errors.js'
import { StateStore } from './store.js'
import type { AppSettings, CrawlerTask, OperationRunMode, RunRecord, RunStatus, RunTrigger } from './types.js'
import { allocateRunDirectory, pathExists } from './utils.js'

interface QueuedItem {
  task: CrawlerTask
  run: RunRecord
}

interface ActiveItem extends QueuedItem {
  child?: ChildProcess
  stdout?: ReturnType<typeof createWriteStream>
  stderr?: ReturnType<typeof createWriteStream>
  timeout?: NodeJS.Timeout
  killGrace?: NodeJS.Timeout
  requestedStatus?: 'stopped' | 'timed_out'
  settled: boolean
}

export interface SpawnSpec {
  command: string
  args: string[]
  cwd: string
}

interface RunAuditContext {
  operator: string
  mode: OperationRunMode
}

export function buildSpawnSpec(
  projectRoot: string,
  task: CrawlerTask,
  settings: AppSettings,
  run: RunRecord,
): SpawnSpec {
  const scriptPath = path.isAbsolute(task.scriptPath)
    ? path.normalize(task.scriptPath)
    : path.resolve(projectRoot, task.scriptPath)
  return {
    command: task.pythonPath || settings.pythonPath,
    args: [
      scriptPath,
      '--url',
      task.url,
      '--output-dir',
      run.outputDir,
      '--run-id',
      run.id,
      ...task.extraArgs,
    ],
    cwd: path.dirname(scriptPath),
  }
}

export class RunManager extends EventEmitter {
  private readonly queued: QueuedItem[] = []
  private readonly active = new Map<string, ActiveItem>()
  private readonly busyTaskIds = new Set<string>()
  private pumping = false

  constructor(
    private readonly projectRoot: string,
    private readonly store: StateStore,
  ) {
    super()
  }

  isTaskBusy(taskId: string): boolean {
    return this.busyTaskIds.has(taskId)
  }

  async enqueue(taskId: string, trigger: RunTrigger, audit?: RunAuditContext): Promise<RunRecord> {
    const state = this.store.snapshot()
    const task = state.tasks.find((item) => item.id === taskId)
    if (!task) throw new AppError(404, 'TASK_NOT_FOUND', '没有找到这个任务。')
    if (!task.enabled && trigger === 'schedule') {
      throw new AppError(409, 'TASK_DISABLED', '任务已禁用，请先启用后再运行。')
    }
    if (this.busyTaskIds.has(taskId)) {
      throw new AppError(409, 'RUN_ALREADY_ACTIVE', '这个任务已经在排队或运行中。')
    }

    this.busyTaskIds.add(task.id)
    try {
      const queuedAt = new Date().toISOString()
      const run: RunRecord = {
        id: randomUUID(),
        taskId: task.id,
        taskName: task.name,
        categoryId: task.categoryId,
        trigger,
        status: 'queued',
        queuedAt,
        outputDir: await allocateRunDirectory(state.settings.outputRoot),
      }
      await Promise.all([
        writeFile(path.join(run.outputDir, 'stdout.log'), '', 'utf8'),
        writeFile(path.join(run.outputDir, 'stderr.log'), '', 'utf8'),
        this.writeManifest(run, task),
      ])
      await this.store.update((draft) => {
        draft.runs.unshift(run)
        if (audit) draft.operationLogs.unshift(createRunStartedOperation({ ...audit, run }))
      })
      this.queued.push({ task: structuredClone(task), run: structuredClone(run) })
      this.requestPump()
      return structuredClone(run)
    } catch (error) {
      this.busyTaskIds.delete(task.id)
      throw error
    }
  }

  async stop(runId: string): Promise<void> {
    const queuedIndex = this.queued.findIndex((item) => item.run.id === runId)
    if (queuedIndex >= 0) {
      const [item] = this.queued.splice(queuedIndex, 1)
      if (item) await this.finalize({ ...item, settled: false }, 'stopped', undefined, '用户取消了排队任务。')
      return
    }

    const item = this.active.get(runId)
    if (!item) throw new AppError(409, 'RUN_NOT_ACTIVE', '这个运行记录已经结束。')
    if (item.requestedStatus) return

    item.requestedStatus = 'stopped'
    try {
      await this.updateRun(runId, { status: 'stopping' })
    } catch (error) {
      this.reportBackgroundError('Unable to persist stopping state', error)
    } finally {
      await this.terminateProcess(item)
    }
  }

  async stopAll(): Promise<void> {
    const runIds = [...this.queued.map((item) => item.run.id), ...this.active.keys()]
    await Promise.allSettled(runIds.map((runId) => this.stop(runId)))

    const deadline = Date.now() + 8_000
    while (this.busyTaskIds.size > 0 && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 100))
    }

    for (const item of this.active.values()) {
      await this.finalize(
        item,
        'interrupted',
        undefined,
        '应用关闭时未能确认子进程已经退出。',
        'SHUTDOWN_TERMINATION_UNCONFIRMED',
      )
    }
  }

  async readLog(runId: string, stream: 'stdout' | 'stderr'): Promise<string> {
    const run = this.store.snapshot().runs.find((item) => item.id === runId)
    if (!run) throw new AppError(404, 'RUN_NOT_FOUND', '没有找到这条运行记录。')
    const filePath = path.join(run.outputDir, `${stream}.log`)
    try {
      const content = await readFile(filePath, 'utf8')
      return content.length > 200_000 ? content.slice(-200_000) : content
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return ''
      throw error
    }
  }

  private async pump(): Promise<void> {
    if (this.pumping) return
    this.pumping = true
    try {
      while (this.queued.length > 0) {
        const maxConcurrency = this.store.snapshot().settings.maxConcurrency
        if (this.active.size >= maxConcurrency) break
        const item = this.queued.shift()
        if (item) await this.start(item)
      }
    } finally {
      this.pumping = false
    }
  }

  private async start(item: QueuedItem): Promise<void> {
    const active: ActiveItem = { ...item, settled: false }
    this.active.set(item.run.id, active)
    try {
      const startedAt = new Date().toISOString()
      active.run.status = 'running'
      active.run.startedAt = startedAt

      await this.store.update((draft) => {
        const run = draft.runs.find((record) => record.id === item.run.id)
        if (run) Object.assign(run, { status: 'running', startedAt })
        const task = draft.tasks.find((record) => record.id === item.task.id)
        if (task) {
          task.lastAttemptAt = startedAt
          task.nextRunAt = undefined
        }
      })
      if (active.settled || active.requestedStatus) return
      await this.writeManifest(active.run, active.task)
      if (active.settled || active.requestedStatus) return

      const settings = this.store.snapshot().settings
      const spec = buildSpawnSpec(this.projectRoot, active.task, settings, active.run)
      if (path.extname(spec.args[0] ?? '').toLowerCase() !== '.py' || !(await pathExists(spec.args[0] ?? ''))) {
        await this.finalize(active, 'failed', undefined, 'Python 爬虫文件不存在或不是 .py 文件。', 'SCRIPT_NOT_FOUND')
        return
      }
      if (active.settled || active.requestedStatus) return

      const stdout = createWriteStream(path.join(active.run.outputDir, 'stdout.log'), { flags: 'a' })
      const stderr = createWriteStream(path.join(active.run.outputDir, 'stderr.log'), { flags: 'a' })
      active.stdout = stdout
      active.stderr = stderr

      const child = spawn(spec.command, spec.args, {
        cwd: spec.cwd,
        shell: false,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, PYTHONUNBUFFERED: '1' },
      })
      active.child = child
      child.stdout?.pipe(stdout)
      child.stderr?.pipe(stderr)

      child.once('error', (error) => {
        stderr.write(`\n${error.message}\n`)
        void this.finalize(active, 'failed', undefined, `无法启动 Python：${error.message}`, 'PYTHON_START_FAILED')
      })
      child.once('close', (exitCode) => {
        const requested = active.requestedStatus
        const status: RunStatus = requested || (exitCode === 0 ? 'succeeded' : 'failed')
        const message = status === 'failed' ? `爬虫退出码为 ${exitCode ?? 'unknown'}。` : undefined
        void this.finalize(active, status, exitCode ?? undefined, message, status === 'failed' ? 'CRAWLER_EXITED' : undefined)
      })

      active.timeout = setTimeout(() => {
        active.requestedStatus = 'timed_out'
        void (async () => {
          try {
            await this.updateRun(active.run.id, { status: 'stopping' })
          } catch (error) {
            this.reportBackgroundError('Unable to persist timeout state', error)
          } finally {
            await this.terminateProcess(active)
          }
        })().catch((error) => this.reportBackgroundError('Unable to stop timed out process', error))
      }, active.task.timeoutSeconds * 1000)
    } catch (error) {
      await this.finalize(
        active,
        'failed',
        undefined,
        `任务执行器发生异常：${(error as Error).message}`,
        'RUNNER_INTERNAL_ERROR',
      )
    }
  }

  private async finalize(
    item: ActiveItem,
    status: RunStatus,
    exitCode?: number,
    errorMessage?: string,
    errorCode?: string,
  ): Promise<void> {
    if (item.settled) return
    item.settled = true
    if (item.timeout) clearTimeout(item.timeout)
    if (item.killGrace) clearTimeout(item.killGrace)
    item.stdout?.end()
    item.stderr?.end()

    const finishedAt = new Date().toISOString()
    const durationMs = item.run.startedAt
      ? Math.max(0, new Date(finishedAt).getTime() - new Date(item.run.startedAt).getTime())
      : 0
    let recordCount: number | undefined
    if (status === 'succeeded') {
      try {
        recordCount = await this.countRecords(item.run.outputDir)
      } catch (error) {
        this.reportBackgroundError('Unable to count output records', error)
      }
    }
    Object.assign(item.run, { status, exitCode, errorMessage, errorCode, finishedAt, durationMs, recordCount })

    try {
      await this.store.update((draft) => {
        const run = draft.runs.find((record) => record.id === item.run.id)
        if (run) Object.assign(run, item.run)
        const task = draft.tasks.find((record) => record.id === item.task.id)
        if (task && status === 'succeeded') task.lastSuccessAt = finishedAt
      })
    } catch (error) {
      this.reportBackgroundError('Unable to persist completed run', error)
    }
    try {
      await this.writeManifest(item.run, item.task)
    } catch (error) {
      this.reportBackgroundError('Unable to update run manifest', error)
    } finally {
      this.active.delete(item.run.id)
      this.busyTaskIds.delete(item.task.id)
      try {
        this.emit('completed', { taskId: item.task.id, runId: item.run.id, status, trigger: item.run.trigger })
      } catch (error) {
        this.reportBackgroundError('Run completion listener failed', error)
      }
      this.requestPump()
    }
  }

  private async updateRun(runId: string, values: Partial<RunRecord>): Promise<void> {
    await this.store.update((draft) => {
      const run = draft.runs.find((item) => item.id === runId)
      if (run) Object.assign(run, values)
    })
  }

  private async terminateProcess(item: ActiveItem): Promise<void> {
    const child = item.child
    if (!child?.pid) {
      await this.finalize(item, item.requestedStatus || 'stopped', undefined, '任务已停止。')
      return
    }

    if (process.platform === 'win32') {
      const killer = spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        shell: false,
        windowsHide: true,
        stdio: 'ignore',
      })
      killer.once('error', () => child.kill())
      killer.once('close', (code) => {
        if (code !== 0 && !item.settled) child.kill()
      })
    } else {
      child.kill('SIGTERM')
    }

    item.killGrace = setTimeout(() => {
      if (item.settled) return
      child.kill('SIGKILL')
      void this.finalize(
        item,
        item.requestedStatus || 'stopped',
        undefined,
        '无法确认子进程已经退出，运行记录已强制结束。',
        'PROCESS_TERMINATION_UNCONFIRMED',
      )
    }, 5_000)
  }

  private async countRecords(outputDir: string): Promise<number | undefined> {
    const filePath = path.join(outputDir, 'records.jsonl')
    if (!(await pathExists(filePath))) return undefined
    const input = createReadStream(filePath, { encoding: 'utf8' })
    let count = 0
    let lastCharacter = ''
    for await (const chunk of input) {
      const text = String(chunk)
      count += (text.match(/\n/g) || []).length
      lastCharacter = text.at(-1) || lastCharacter
    }
    if (lastCharacter && lastCharacter !== '\n') count += 1
    return count
  }

  private async writeManifest(run: RunRecord, task: CrawlerTask): Promise<void> {
    const manifest = {
      schemaVersion: 1,
      run,
      task: {
        id: task.id,
        categoryId: task.categoryId,
        name: task.name,
        url: task.url,
        scriptPath: task.scriptPath,
        intervalMinutes: task.intervalMinutes,
      },
    }
    await writeFile(path.join(run.outputDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
  }

  private requestPump(): void {
    queueMicrotask(() => {
      void this.pump().catch((error) => this.reportBackgroundError('Run queue pump failed', error))
    })
  }

  private reportBackgroundError(message: string, error: unknown): void {
    console.error(`[runner] ${message}`, error)
  }
}
