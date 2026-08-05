<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { Clipboard, FolderOpen, LoaderCircle, RefreshCw, Square, TerminalSquare, X } from '@lucide/vue'
import { api, ApiError } from '../services/api'
import { toast } from '../services/toast'
import type { RunRecord } from '../types'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{
  open: boolean
  run: RunRecord | null
}>()

const emit = defineEmits<{
  close: []
  refresh: []
}>()

const activeStream = ref<'stdout' | 'stderr'>('stdout')
const logText = ref('')
const loadingLog = ref(false)
const logViewer = ref<HTMLElement | null>(null)
const followingLogTail = ref(true)
const openingDirectory = ref(false)
const stopping = ref(false)
let poller: number | undefined
let logContext = ''

const isActive = computed(() => Boolean(props.run && ['queued', 'running', 'stopping'].includes(props.run.status)))

watch(
  () => [props.open, props.run?.id, props.run?.status, activeStream.value] as const,
  () => {
    if (poller) window.clearInterval(poller)
    const nextContext = props.open && props.run ? `${props.run.id}:${activeStream.value}` : ''
    if (nextContext !== logContext) {
      logContext = nextContext
      followingLogTail.value = true
    }
    if (!props.open || !props.run) return
    void loadLog()
    if (isActive.value) poller = window.setInterval(() => void loadLog(true), 2000)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (poller) window.clearInterval(poller)
})

async function loadLog(silent = false) {
  if (!props.run) return
  if (!silent) loadingLog.value = true
  let loaded = false
  try {
    logText.value = (await api.log(props.run.id, activeStream.value)).text
    loaded = true
  } catch (error) {
    if (!silent) toast.error(messageOf(error))
  } finally {
    if (!silent) loadingLog.value = false
  }
  if (loaded && followingLogTail.value) {
    await nextTick()
    scrollLogToBottom()
  }
}

function scrollLogToBottom() {
  if (!logViewer.value) return
  logViewer.value.scrollTop = logViewer.value.scrollHeight
}

function handleLogScroll(event: Event) {
  const target = event.currentTarget as HTMLElement
  const distanceFromBottom = target.scrollHeight - target.scrollTop - target.clientHeight
  followingLogTail.value = distanceFromBottom <= 24
}

async function stop() {
  if (!props.run) return
  stopping.value = true
  try {
    await api.stopRun(props.run.id)
    toast.info('已发送停止请求。')
    emit('refresh')
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    stopping.value = false
  }
}

async function openDirectory() {
  if (!props.run || openingDirectory.value) return
  openingDirectory.value = true
  try {
    await api.openRun(props.run.id)
    toast.success('已打开运行结果目录。')
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    openingDirectory.value = false
  }
}

async function copyLog() {
  await navigator.clipboard.writeText(logText.value)
  toast.success('日志已复制。')
}

function formatTime(value?: string) {
  return value ? new Date(value).toLocaleString('zh-CN') : '--'
}

function formatDuration(value?: number) {
  if (value === undefined) return '--'
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : '操作失败。'
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open && run" class="drawer-backdrop" @click.self="emit('close')">
      <aside class="drawer drawer--wide" role="dialog" aria-modal="true" aria-label="运行详情">
        <header class="drawer__header">
          <div>
            <span class="eyebrow">RUN INSPECTOR</span>
            <h2>{{ run.taskName }}</h2>
          </div>
          <button class="icon-button" type="button" aria-label="关闭" @click="emit('close')"><X :size="19" /></button>
        </header>

        <div class="drawer__body run-inspector">
          <div class="run-summary-band">
            <div><span>状态</span><StatusBadge :status="run.status" /></div>
            <div><span>开始时间</span><strong>{{ formatTime(run.startedAt || run.queuedAt) }}</strong></div>
            <div><span>耗时</span><strong>{{ formatDuration(run.durationMs) }}</strong></div>
            <div><span>数据量</span><strong>{{ run.recordCount ?? '--' }}</strong></div>
          </div>

          <section v-if="run.errorMessage" class="error-summary">
            <span>{{ run.errorCode || 'RUN_ERROR' }}</span>
            <p>{{ run.errorMessage }}</p>
          </section>

          <section class="run-path-section">
            <span>输出目录</span>
            <code>{{ run.outputDir }}</code>
            <button
              class="button button--ghost"
              type="button"
              :aria-busy="openingDirectory"
              :disabled="openingDirectory"
              @click="openDirectory"
            >
              <LoaderCircle v-if="openingDirectory" class="loading-indicator" :size="16" />
              <FolderOpen v-else :size="16" />
              {{ openingDirectory ? '打开中' : '打开目录' }}
            </button>
          </section>

          <section class="log-section">
            <header class="log-toolbar">
              <div class="segmented-control">
                <button type="button" :class="{ active: activeStream === 'stdout' }" @click="activeStream = 'stdout'">STDOUT</button>
                <button type="button" :class="{ active: activeStream === 'stderr' }" @click="activeStream = 'stderr'">STDERR</button>
              </div>
              <div>
                <button class="icon-button" type="button" title="刷新日志" aria-label="刷新日志" @click="loadLog()"><RefreshCw :size="16" /></button>
                <button class="icon-button" type="button" title="复制日志" aria-label="复制日志" @click="copyLog"><Clipboard :size="16" /></button>
              </div>
            </header>
            <pre ref="logViewer" class="log-viewer" @scroll="handleLogScroll"><span v-if="loadingLog">Loading log...</span><span v-else-if="logText">{{ logText }}</span><span v-else class="log-empty"><TerminalSquare :size="18" /> No output</span></pre>
          </section>
        </div>

        <footer class="drawer__footer drawer__footer--fixed">
          <button v-if="isActive" class="button button--danger" type="button" :disabled="stopping" @click="stop">
            <Square :size="16" /> {{ stopping ? '停止中' : '停止任务' }}
          </button>
          <div class="drawer__footer-spacer" />
          <button class="button button--ghost" type="button" @click="emit('close')">关闭</button>
        </footer>
      </aside>
    </div>
  </Teleport>
</template>
