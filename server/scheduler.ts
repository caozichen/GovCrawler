import { RunManager } from './runner.js'
import { StateStore } from './store.js'

const MAX_TIMER_DELAY_MS = 2_147_000_000
const RETRY_DELAY_MS = 30_000

export class SchedulerService {
  private readonly timers = new Map<string, NodeJS.Timeout>()
  private generation = 0

  constructor(
    private readonly store: StateStore,
    private readonly runner: RunManager,
  ) {
    this.runner.on('completed', ({ taskId }: { taskId: string }) => {
      this.scheduleSafely(taskId, this.generation)
    })
  }

  async start(): Promise<void> {
    if (this.store.snapshot().scheduler.running) return
    const generation = ++this.generation
    await this.store.update((draft) => {
      if (generation !== this.generation) return
      draft.scheduler = { running: true, startedAt: new Date().toISOString() }
    })
    if (!this.isCurrent(generation)) return

    for (const task of this.store.snapshot().tasks.filter((item) => item.enabled)) {
      void this.runner
        .enqueue(task.id, 'schedule')
        .catch((error) => this.handleScheduleError(task.id, generation, error))
    }
  }

  async stop(): Promise<void> {
    ++this.generation
    for (const timer of this.timers.values()) clearTimeout(timer)
    this.timers.clear()
    await this.store.update((draft) => {
      draft.scheduler = { running: false }
      for (const task of draft.tasks) task.nextRunAt = undefined
    })
  }

  async refreshTask(taskId: string): Promise<void> {
    this.clearTaskTimer(taskId)
    const generation = this.generation
    const state = this.store.snapshot()
    const task = state.tasks.find((item) => item.id === taskId)
    if (!state.scheduler.running || !task?.enabled || this.runner.isTaskBusy(task.id)) return
    try {
      await this.scheduleAfterInterval(task.id, generation)
    } catch (error) {
      this.handleScheduleError(task.id, generation, error)
    }
  }

  private scheduleSafely(taskId: string, generation: number): void {
    void this.scheduleAfterInterval(taskId, generation).catch((error) => {
      this.handleScheduleError(taskId, generation, error)
    })
  }

  private async scheduleAfterInterval(taskId: string, generation: number): Promise<void> {
    if (!this.isCurrent(generation, taskId)) return
    const task = this.store.snapshot().tasks.find((item) => item.id === taskId)
    if (!task) return

    const nextRunAt = new Date(Date.now() + task.intervalMinutes * 60_000).toISOString()
    this.clearTaskTimer(taskId)
    await this.store.update((draft) => {
      if (generation !== this.generation || !draft.scheduler.running) return
      const current = draft.tasks.find((item) => item.id === taskId)
      if (current?.enabled) current.nextRunAt = nextRunAt
    })
    if (!this.isCurrent(generation, taskId)) return
    this.armTimer(taskId, nextRunAt, generation)
  }

  private armTimer(taskId: string, nextRunAt: string, generation: number): void {
    if (!this.isCurrent(generation, taskId)) return
    const remaining = Math.max(0, new Date(nextRunAt).getTime() - Date.now())
    const timer = setTimeout(() => {
      if (!this.isCurrent(generation, taskId)) {
        this.timers.delete(taskId)
        return
      }

      const currentRemaining = new Date(nextRunAt).getTime() - Date.now()
      if (currentRemaining > 0) {
        this.armTimer(taskId, nextRunAt, generation)
        return
      }

      this.timers.delete(taskId)
      void this.runner
        .enqueue(taskId, 'schedule')
        .catch((error) => this.handleScheduleError(taskId, generation, error))
    }, Math.min(remaining, MAX_TIMER_DELAY_MS))
    this.timers.set(taskId, timer)
  }

  private handleScheduleError(taskId: string, generation: number, error: unknown): void {
    console.error(`[scheduler] Task ${taskId} could not be scheduled`, error)
    if (!this.isCurrent(generation, taskId)) return
    this.clearTaskTimer(taskId)
    const retryAt = new Date(Date.now() + RETRY_DELAY_MS).toISOString()

    void this.store
      .update((draft) => {
        if (generation !== this.generation || !draft.scheduler.running) return
        const task = draft.tasks.find((item) => item.id === taskId)
        if (task?.enabled) task.nextRunAt = retryAt
      })
      .then(() => {
        if (this.isCurrent(generation, taskId)) this.armTimer(taskId, retryAt, generation)
      })
      .catch((persistError) => {
        console.error(`[scheduler] Task ${taskId} retry state could not be persisted`, persistError)
        if (this.isCurrent(generation, taskId)) this.armTimer(taskId, retryAt, generation)
      })
  }

  private isCurrent(generation: number, taskId?: string): boolean {
    if (generation !== this.generation) return false
    const state = this.store.snapshot()
    if (!state.scheduler.running) return false
    if (!taskId) return true
    return Boolean(state.tasks.find((task) => task.id === taskId)?.enabled)
  }

  private clearTaskTimer(taskId: string): void {
    const timer = this.timers.get(taskId)
    if (timer) clearTimeout(timer)
    this.timers.delete(taskId)
  }
}
