<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElInput, ElOption, ElSelect } from 'element-plus'
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ExternalLink,
  Filter,
  FolderOpen,
  LoaderCircle,
  Search,
  Square,
  TerminalSquare,
} from '@lucide/vue'
import RunDrawer from '../components/RunDrawer.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, ApiError } from '../services/api'
import { toast } from '../services/toast'
import { useConsoleStore } from '../stores/console'
import type { RunRecord, RunStatus } from '../types'
import { paginateItems } from '../utils/pagination'
import CategoryBadge from '../components/CategoryBadge.vue'

const consoleStore = useConsoleStore()
const { runs, categories } = storeToRefs(consoleStore)
const query = ref('')
const categoryFilter = ref('all')
const statusFilter = ref<'all' | RunStatus>('all')
const selectedRunId = ref('')
const openingRunId = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const selectedRun = computed(() => runs.value.find((run) => run.id === selectedRunId.value) || null)
const categoryById = computed(() => new Map(categories.value.map((category) => [category.id, category])))

const filteredRuns = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return runs.value.filter((run) => {
    const matchesKeyword = !keyword || run.taskName.toLowerCase().includes(keyword) || run.outputDir.toLowerCase().includes(keyword)
    const matchesCategory = categoryFilter.value === 'all' || run.categoryId === categoryFilter.value
    return matchesKeyword && matchesCategory && (statusFilter.value === 'all' || run.status === statusFilter.value)
  })
})

const pagination = computed(() => paginateItems(filteredRuns.value, currentPage.value, pageSize.value))
const paginatedRuns = computed(() => pagination.value.items)

watch([query, categoryFilter, statusFilter, pageSize], () => {
  currentPage.value = 1
})

watch(() => pagination.value.totalPages, (nextTotal) => {
  if (currentPage.value > nextTotal) currentPage.value = nextTotal
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
      <div class="search-box">
        <ElInput v-model="query" type="search" placeholder="搜索任务或输出目录" aria-label="搜索任务或输出目录" clearable>
          <template #prefix><Search :size="17" /></template>
        </ElInput>
      </div>
      <div class="select-shell">
        <ElSelect v-model="categoryFilter" aria-label="按分类筛选运行记录">
          <template #prefix><Filter :size="16" /></template>
          <ElOption label="全部分类" value="all" />
          <ElOption v-for="category in categories" :key="category.id" :label="category.name" :value="category.id" />
        </ElSelect>
      </div>
      <div class="select-shell">
        <ElSelect v-model="statusFilter" aria-label="按运行结果筛选运行记录">
          <template #prefix><Filter :size="16" /></template>
          <ElOption label="全部结果" value="all" />
          <ElOption label="运行中" value="running" />
          <ElOption label="成功" value="succeeded" />
          <ElOption label="失败" value="failed" />
          <ElOption label="超时" value="timed_out" />
          <ElOption label="已停止" value="stopped" />
        </ElSelect>
      </div>
    </div>

    <div class="data-panel">
      <div class="table-scroll">
        <table class="data-table run-table">
          <thead>
            <tr>
              <th>开始时间</th>
              <th>分类</th>
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
            <tr v-for="run in paginatedRuns" :key="run.id" :class="{ 'row--failed': ['failed', 'timed_out', 'interrupted'].includes(run.status) }">
              <td><span class="time-cell">{{ formatTime(run.startedAt || run.queuedAt) }}</span></td>
              <td><CategoryBadge :category="categoryById.get(run.categoryId)" /></td>
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

      <div v-else class="pagination-bar" aria-label="运行历史分页">
        <span class="pagination-summary">显示 {{ pagination.start }}-{{ pagination.end }} 条，共 {{ pagination.totalItems }} 条</span>
        <div class="pagination-controls">
          <label class="pagination-size">
            <span>每页</span>
            <ElSelect v-model="pageSize" aria-label="每页显示条数">
              <ElOption label="10 条" :value="10" />
              <ElOption label="20 条" :value="20" />
              <ElOption label="50 条" :value="50" />
              <ElOption label="100 条" :value="100" />
            </ElSelect>
          </label>
          <div class="pagination-pages">
            <button class="icon-button icon-button--small" type="button" title="第一页" aria-label="第一页" :disabled="currentPage === 1" @click="currentPage = 1">
              <ChevronsLeft :size="15" />
            </button>
            <button class="icon-button icon-button--small" type="button" title="上一页" aria-label="上一页" :disabled="currentPage === 1" @click="currentPage -= 1">
              <ChevronLeft :size="15" />
            </button>
            <span class="pagination-current" aria-live="polite">第 {{ pagination.page }} / {{ pagination.totalPages }} 页</span>
            <button class="icon-button icon-button--small" type="button" title="下一页" aria-label="下一页" :disabled="pagination.page === pagination.totalPages" @click="currentPage = pagination.page + 1">
              <ChevronRight :size="15" />
            </button>
            <button class="icon-button icon-button--small" type="button" title="最后一页" aria-label="最后一页" :disabled="pagination.page === pagination.totalPages" @click="currentPage = pagination.totalPages">
              <ChevronsRight :size="15" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </section>

  <RunDrawer :open="Boolean(selectedRun)" :run="selectedRun" @close="selectedRunId = ''" @refresh="consoleStore.refresh(true)" />
</template>
