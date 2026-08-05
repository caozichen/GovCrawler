<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import {
  Activity,
  Bot,
  ChevronRight,
  Clock3,
  History,
  LogOut,
  Menu,
  Play,
  RefreshCw,
  Settings,
  Square,
  X,
} from '@lucide/vue'
import { ApiError } from '../services/api'
import { toast } from '../services/toast'
import { useAuthStore } from '../stores/auth'
import { useConsoleStore } from '../stores/console'
import ThemeToggle from './ThemeToggle.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const consoleStore = useConsoleStore()
const menuOpen = ref(false)
const actionBusy = ref(false)
let poller: number | undefined

const pageTitle = computed(() => {
  if (route.name === 'runs') return '运行历史'
  if (route.name === 'settings') return '系统设置'
  return '任务控制台'
})

const schedulerRunning = computed(() => consoleStore.dashboard.scheduler.running)

onMounted(async () => {
  try {
    await consoleStore.refresh()
  } catch (error) {
    toast.error(messageOf(error))
  }
  poller = window.setInterval(() => {
    void consoleStore.refresh(true).catch(() => undefined)
  }, 3000)
})

onBeforeUnmount(() => {
  if (poller) window.clearInterval(poller)
})

watch(
  () => route.fullPath,
  () => {
    menuOpen.value = false
  },
)

async function toggleScheduler() {
  actionBusy.value = true
  try {
    if (schedulerRunning.value) {
      await consoleStore.stopScheduler()
      toast.info('调度已暂停，正在运行的任务不会被终止。')
    } else {
      await consoleStore.startScheduler()
      toast.success('调度已启动。')
    }
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    actionBusy.value = false
  }
}

async function runAll() {
  if (consoleStore.dashboard.enabledCount === 0) {
    toast.info('当前没有已启用的任务。')
    return
  }
  actionBusy.value = true
  try {
    const result = await consoleStore.runAll()
    toast.success(`已提交 ${result.accepted.length} 个任务。`)
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    actionBusy.value = false
  }
}

async function logout() {
  try {
    await auth.logout()
  } catch {
    toast.error('退出请求失败，本地登录状态已清除。')
  } finally {
    await router.replace({ name: 'login' })
  }
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : '本地控制服务暂时不可用。'
}
</script>

<template>
  <div class="app-shell">
    <div v-if="menuOpen" class="sidebar-scrim" @click="menuOpen = false" />
    <aside class="sidebar" :class="{ 'sidebar--open': menuOpen }">
      <div class="brand-lockup">
        <div class="brand-mark" title="PA Crawler" aria-label="PA Crawler"><Bot :size="21" /></div>
        <div>
          <strong>PA Crawler</strong>
          <span>LOCAL OPS</span>
        </div>
        <button class="icon-button sidebar__close" type="button" aria-label="关闭菜单" @click="menuOpen = false">
          <X :size="19" />
        </button>
      </div>

      <nav class="side-nav" aria-label="主导航">
        <RouterLink :to="{ name: 'tasks' }" title="任务控制台" aria-label="任务控制台">
          <Activity :size="18" />
          <span>任务控制台</span>
          <ChevronRight :size="15" class="side-nav__chevron" />
        </RouterLink>
        <RouterLink :to="{ name: 'runs' }" title="运行历史" aria-label="运行历史">
          <History :size="18" />
          <span>运行历史</span>
          <ChevronRight :size="15" class="side-nav__chevron" />
        </RouterLink>
        <RouterLink :to="{ name: 'settings' }" title="系统设置" aria-label="系统设置">
          <Settings :size="18" />
          <span>系统设置</span>
          <ChevronRight :size="15" class="side-nav__chevron" />
        </RouterLink>
      </nav>

      <div class="sidebar-status">
        <div class="sidebar-status__row">
          <span class="live-dot" :class="{ 'live-dot--active': schedulerRunning }" />
          <span>{{ schedulerRunning ? '调度运行中' : '调度已暂停' }}</span>
        </div>
        <div class="sidebar-status__meta">
          <Clock3 :size="14" />
          本机时间 {{ new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }}
        </div>
      </div>
    </aside>

    <main class="main-stage">
      <header class="topbar">
        <div class="topbar__inner">
          <div class="topbar__title">
            <button class="icon-button topbar__menu" type="button" aria-label="打开菜单" @click="menuOpen = true">
              <Menu :size="20" />
            </button>
            <div>
              <span class="eyebrow">CONTROL CENTER</span>
              <h1>{{ pageTitle }}</h1>
            </div>
          </div>

          <div class="topbar__actions">
            <button
              class="button button--ghost topbar__run-all"
              type="button"
              title="立即运行全部"
              aria-label="立即运行全部"
              :disabled="actionBusy"
              @click="runAll"
            >
              <RefreshCw :size="17" />
              <span>立即运行全部</span>
            </button>
            <button
              class="button"
              :class="schedulerRunning ? 'button--warning' : 'button--primary'"
              type="button"
              :title="schedulerRunning ? '停止调度' : '启动调度'"
              :aria-label="schedulerRunning ? '停止调度' : '启动调度'"
              :disabled="actionBusy"
              @click="toggleScheduler"
            >
              <Square v-if="schedulerRunning" :size="16" />
              <Play v-else :size="17" />
              <span>{{ schedulerRunning ? '停止调度' : '启动调度' }}</span>
            </button>
            <ThemeToggle />
            <div class="user-menu">
              <span class="user-avatar">{{ auth.user?.username.slice(0, 1).toUpperCase() }}</span>
              <span class="user-menu__name">{{ auth.user?.username }}</span>
              <button class="icon-button" type="button" title="退出登录" aria-label="退出登录" @click="logout">
                <LogOut :size="17" />
              </button>
            </div>
          </div>
        </div>
      </header>

      <div class="page-content">
        <RouterView />
      </div>
    </main>
  </div>
</template>
