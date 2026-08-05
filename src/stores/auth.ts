import { defineStore } from 'pinia'
import { api } from '../services/api'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as { username: string } | null,
    checked: false,
    loading: false,
  }),
  actions: {
    async restore() {
      try {
        this.user = await api.me()
      } catch {
        this.user = null
      } finally {
        this.checked = true
      }
    },
    async login(username: string, password: string) {
      this.loading = true
      try {
        this.user = await api.login(username, password)
      } finally {
        this.loading = false
      }
    },
    async logout() {
      try {
        await api.logout()
      } finally {
        this.user = null
      }
    },
  },
})
