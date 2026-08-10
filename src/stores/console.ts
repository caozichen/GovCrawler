import { defineStore } from 'pinia'
import { api } from '../services/api'
import type { AppSettings, CrawlerTask, DashboardSummary, RunRecord, TaskCategory, TaskPayload } from '../types'

const emptyDashboard: DashboardSummary = {
  taskCount: 0,
  enabledCount: 0,
  activeCount: 0,
  successToday: 0,
  failedToday: 0,
  scheduler: { running: false },
}

export const useConsoleStore = defineStore('console', {
  state: () => ({
    tasks: [] as CrawlerTask[],
    categories: [] as TaskCategory[],
    runs: [] as RunRecord[],
    settings: null as AppSettings | null,
    dashboard: emptyDashboard,
    loading: false,
    runSubmissionBusy: false,
  }),
  actions: {
    async refresh(silent = false) {
      if (!silent) this.loading = true
      try {
        const [tasks, categories, runs, settings, dashboard] = await Promise.all([
          api.tasks(),
          api.categories(),
          api.runs(),
          api.settings(),
          api.dashboard(),
        ])
        this.tasks = tasks
        this.categories = categories
        this.runs = runs
        this.settings = settings
        this.dashboard = dashboard
      } finally {
        if (!silent) this.loading = false
      }
    },
    async saveTask(payload: TaskPayload, id?: string) {
      if (id) await api.updateTask(id, payload)
      else await api.createTask(payload)
      await this.refresh(true)
    },
    async saveTaskDetails(id: string, details: string) {
      await api.updateTaskDetails(id, details)
      await this.refresh(true)
    },
    async deleteTask(id: string) {
      await api.deleteTask(id)
      await this.refresh(true)
    },
    async createCategory(payload: Pick<TaskCategory, 'name' | 'color'>) {
      await api.createCategory(payload)
      await this.refresh(true)
    },
    async updateCategory(id: string, payload: Pick<TaskCategory, 'name' | 'color'>) {
      await api.updateCategory(id, payload)
      await this.refresh(true)
    },
    async deleteCategory(id: string) {
      const result = await api.deleteCategory(id)
      await this.refresh(true)
      return result
    },
    async runTask(id: string) {
      if (this.runSubmissionBusy) return false
      this.runSubmissionBusy = true
      try {
        await api.runTask(id)
        await this.refresh(true)
        return true
      } finally {
        this.runSubmissionBusy = false
      }
    },
    async runTasks(ids: string[]) {
      if (this.runSubmissionBusy) return null
      this.runSubmissionBusy = true
      try {
        const results = await Promise.allSettled(ids.map((id) => api.runTask(id)))
        await this.refresh(true)
        return results
      } finally {
        this.runSubmissionBusy = false
      }
    },
    async runAll() {
      if (this.runSubmissionBusy) return null
      this.runSubmissionBusy = true
      try {
        const result = await api.runAll()
        await this.refresh(true)
        return result
      } finally {
        this.runSubmissionBusy = false
      }
    },
    async startScheduler() {
      await api.startScheduler()
      await this.refresh(true)
    },
    async stopScheduler() {
      await api.stopScheduler()
      await this.refresh(true)
    },
    async stopRun(id: string) {
      await api.stopRun(id)
      await this.refresh(true)
    },
  },
})
