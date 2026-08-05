<script setup lang="ts">
import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { ExternalLink, Filter, FolderOpen, LoaderCircle, Search, Square, TerminalSquare } from '@lucide/vue'
import RunDrawer from '../components/RunDrawer.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, ApiError } from '../services/api'
import { toast } from '../services/toast'
import { useConsoleStore } from '../stores/console'
import type { RunRecord, RunStatus } from '../types'

const consoleStore = useConsoleStore()
const { runs } = storeToRefs(consoleStore)
const query = ref('')
const statusFilter = ref<'all' | RunStatus>('all')
const selectedRunId = ref('')
const openingRunId = ref('')
const selectedRun = computed(() => runs.value.find((run) => run.id === selectedRunId.value) || null)

const filteredRuns = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return runs.value.filter((run) => {
    const matchesKeyword = !keyword || run.taskName.toLowerCase().includes(keyword) || run.outputDir.toLowerCase().includes(keyword)
    return matchesKeyword && (statusFilter.value === 'all' || run.status === statusFilter.value)
  })
})

async function openDirectory(run: RunRecord) {
  if (openingRunId.value) return
  openingRunId.value = run.id
  try {
    await api.openRun(run.id)
    toast.success('已打开运行结果目录。')
  } catch (error) {
    toast.error(error instanceof ApiError ? error.message : '无法打开目录。')
  } finally {
    openingRunId.value = ''
  }
}

async function stopRun(run: RunRecord) {
  try {
    await consoleStore.stopRun(run.id)
    toast.info('已发送停止请求。')
  } catch (error) {
    toast.error(error instanceof ApiError ? error.message : '无法停止任务。')
  }
}

function formatTime(value?: string) {
  return value ? new Date(value).toLocaleString('zh-CN') : '--'
}

function formatDuration(value?: number) {
  if (value === undefined) return '--'
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`
}

function triggerLabel(trigger: RunRecord['trigger']) {
  return { manual: '手动', schedule: '调度', 'run-all': '批量' }[trigger]
}
</script>

<template>
  <section class="workspace-page">
    <div class="section-heading section-heading--top">
      <div>
        <span class="eyebrow">EXECUTION LEDGER</span>
        <h2>运行记录</h2>
      </div>
      <span class="record-counter">{{ runs.length }} RECORDS</span>
    </div>

    <div class="table-toolbar">
      <label class="search-box">
        <Search :size="17" />
        <input v-model="query" type="search" placeholder="搜索任务或输出目录" />
      </label>
      <label class="select-shell">
        <Filter :size="16" />
        <select v-model="statusFilter">
          <option value="all">全部结果</option>
          <option value="running">运行中</option>
          <option value="succeeded">成功</option>
          <option value="failed">失败</option>
          <option value="timed_out">超时</option>
          <option value="stopped">已停止</option>
        </select>
      </label>
    </div>

    <div class="data-panel">
      <div class="table-scroll">
        <table class="data-table run-table">
          <thead>
            <tr>
              <th>开始时间</th>
              <th>任务</th>
              <th>触发方式</th>
              <th>状态</th>
              <th>耗时</th>
              <th>数据量</th>
              <th>输出目录</th>
              <th class="table-actions-heading">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="run in filteredRuns" :key="run.id" :class="{ 'row--failed': ['failed', 'timed_out', 'interrupted'].includes(run.status) }">
              <td><span class="time-cell">{{ formatTime(run.startedAt || run.queuedAt) }}</span></td>
              <td><strong>{{ run.taskName }}</strong></td>
              <td><span class="trigger-chip">{{ triggerLabel(run.trigger) }}</span></td>
              <td><StatusBadge :status="run.status" /></td>
              <td><span class="mono-value">{{ formatDuration(run.durationMs) }}</span></td>
              <td><span class="mono-value">{{ run.recordCount ?? '--' }}</span></td>
              <td><div class="path-cell path-cell--output" :title="run.outputDir">{{ run.outputDir }}</div></td>
              <td>
                <div class="row-actions">
                  <button class="icon-button" type="button" title="查看日志" aria-label="查看日志" @click="selectedRunId = run.id">
                    <TerminalSquare :size="16" />
                  </button>
                  <button
                    class="icon-button"
                    type="button"
                    :title="openingRunId === run.id ? '正在打开目录' : '打开目录'"
                    :aria-label="openingRunId === run.id ? '正在打开目录' : '打开目录'"
                    :aria-busy="openingRunId === run.id"
                    :disabled="Boolean(openingRunId)"
                    @click="openDirectory(run)"
                  >
                    <LoaderCircle v-if="openingRunId === run.id" class="loading-indicator" :size="16" />
                    <FolderOpen v-else :size="16" />
                  </button>
                  <button
                    v-if="['queued', 'running', 'stopping'].includes(run.status)"
                    class="icon-button icon-button--danger"
                    type="button"
                    title="停止任务"
                    aria-label="停止任务"
                    @click="stopRun(run)"
                  >
                    <Square :size="15" />
                  </button>
                  <button v-else class="icon-button" type="button" title="查看详情" aria-label="查看详情" @click="selectedRunId = run.id">
                    <ExternalLink :size="16" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="filteredRuns.length === 0" class="empty-state">
        <TerminalSquare :size="28" />
        <strong>暂无运行记录</strong>
      </div>
    </div>
  </section>

  <RunDrawer :open="Boolean(selectedRun)" :run="selectedRun" @close="selectedRunId = ''" @refresh="consoleStore.refresh(true)" />
</template>
