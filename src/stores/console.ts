import { defineStore } from 'pinia'
import { api } from '../services/api'
import type { AppSettings, CrawlerTask, DashboardSummary, RunRecord, TaskPayload } from '../types'

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
    runs: [] as RunRecord[],
    settings: null as AppSettings | null,
    dashboard: emptyDashboard,
    loading: false,
  }),
  actions: {
    async refresh(silent = false) {
      if (!silent) this.loading = true
      try {
        const [tasks, runs, settings, dashboard] = await Promise.all([
          api.tasks(),
          api.runs(),
          api.settings(),
          api.dashboard(),
        ])
        this.tasks = tasks
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
    async runTask(id: string) {
      await api.runTask(id)
      await this.refresh(true)
    },
    async runAll() {
      const result = await api.runAll()
      await this.refresh(true)
      return result
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
