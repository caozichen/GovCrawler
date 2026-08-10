<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElInput, ElOption, ElSelect } from 'element-plus'
import {
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ClipboardList,
  Filter,
  LoaderCircle,
  RefreshCw,
  Search,
} from '@lucide/vue'
import { api, ApiError } from '../services/api'
import { toast } from '../services/toast'
import type { OperationAction, OperationChange, OperationLog } from '../types'
import { paginateItems } from '../utils/pagination'

const operationLogs = ref<OperationLog[]>([])
const loading = ref(false)
const query = ref('')
const actionFilter = ref<'all' | OperationAction>('all')
const currentPage = ref(1)
const pageSize = ref(20)

const filteredLogs = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return operationLogs.value.filter((log) => {
    if (actionFilter.value !== 'all' && log.action !== actionFilter.value) return false
    if (!keyword) return true
    const changeText = log.changes
      .flatMap((change) => [change.label, change.before || '', change.after || ''])
      .join(' ')
    return [log.operator, log.taskName, log.summary, log.runId || '', changeText]
      .some((value) => value.toLowerCase().includes(keyword))
  })
})

const pagination = computed(() => paginateItems(filteredLogs.value, currentPage.value, pageSize.value))

watch([query, actionFilter, pageSize], () => {
  currentPage.value = 1
})

watch(() => pagination.value.totalPages, (nextTotal) => {
  if (currentPage.value > nextTotal) currentPage.value = nextTotal
})

onMounted(() => {
  void loadOperationLogs()
})

async function loadOperationLogs() {
  if (loading.value) return
  loading.value = true
  try {
    operationLogs.value = await api.operationLogs()
  } catch (error) {
    toast.error(error instanceof ApiError ? error.message : '无法加载操作记录。')
  } finally {
    loading.value = false
  }
}

function actionLabel(action: OperationAction): string {
  return {
    'task-created': '新增任务',
    'task-updated': '编辑任务',
    'task-deleted': '删除任务',
    'run-started': '开始运行',
    'run-stopped': '停止运行',
  }[action]
}

function changeSummary(log: OperationLog): string {
  if (log.changes.length === 0) return '配置未发生变化'
  if (log.action === 'task-created') return `查看新增的 ${log.changes.length} 项配置`
  if (log.action === 'task-deleted') return `查看删除前的 ${log.changes.length} 项配置`
  if (log.action === 'task-updated') return `查看 ${log.changes.length} 处改动`
  return `查看 ${log.changes.length} 项运行信息`
}

function displayChangeValue(change: OperationChange, side: 'before' | 'after'): string {
  const value = change[side]
  if (!value) return '未设置'
  if (['startedAt', 'stoppedAt'].includes(change.field)) return formatTime(value)
  return value
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}
</script>

<template>
  <section class="workspace-page operation-page">
    <div class="section-heading section-heading--top">
      <div>
        <span class="eyebrow">AUDIT TRAIL</span>
        <h2>操作记录</h2>
      </div>
      <button class="button button--ghost" type="button" :disabled="loading" @click="loadOperationLogs">
        <LoaderCircle v-if="loading" class="loading-indicator" :size="17" />
        <RefreshCw v-else :size="17" />
        {{ loading ? '刷新中' : '刷新记录' }}
      </button>
    </div>

    <div class="table-toolbar">
      <div class="search-box">
        <ElInput v-model="query" type="search" placeholder="搜索操作人、任务或变更内容" aria-label="搜索操作日志" clearable>
          <template #prefix><Search :size="17" /></template>
        </ElInput>
      </div>
      <div class="select-shell">
        <ElSelect v-model="actionFilter" aria-label="按操作类型筛选">
          <template #prefix><Filter :size="16" /></template>
          <ElOption label="全部操作" value="all" />
          <ElOption label="新增任务" value="task-created" />
          <ElOption label="编辑任务" value="task-updated" />
          <ElOption label="删除任务" value="task-deleted" />
          <ElOption label="开始运行" value="run-started" />
          <ElOption label="停止运行" value="run-stopped" />
        </ElSelect>
      </div>
      <span class="table-toolbar__count">{{ filteredLogs.length }} RECORDS</span>
    </div>

    <div class="data-panel" :class="{ 'data-panel--loading': loading }">
      <div class="table-scroll">
        <table class="data-table operation-table">
          <thead>
            <tr>
              <th>操作时间</th>
              <th>操作人</th>
              <th>操作类型</th>
              <th>脚本对象</th>
              <th>具体内容</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="log in pagination.items" :key="log.id">
              <td><span class="time-cell">{{ formatTime(log.occurredAt) }}</span></td>
              <td><span class="operation-operator">{{ log.operator }}</span></td>
              <td>
                <span class="operation-type" :class="`operation-type--${log.action}`">{{ actionLabel(log.action) }}</span>
              </td>
              <td>
                <div class="operation-target">
                  <strong>{{ log.taskName }}</strong>
                  <span>{{ log.summary }}</span>
                </div>
              </td>
              <td>
                <details v-if="log.changes.length" class="operation-changes">
                  <summary>{{ changeSummary(log) }}</summary>
                  <div class="operation-change-list">
                    <div v-for="change in log.changes" :key="change.field" class="operation-change-row">
                      <strong>{{ change.label }}</strong>
                      <template v-if="log.action === 'task-updated'">
                        <span class="operation-value operation-value--before">{{ displayChangeValue(change, 'before') }}</span>
                        <ArrowRight :size="14" />
                        <span class="operation-value operation-value--after">{{ displayChangeValue(change, 'after') }}</span>
                      </template>
                      <span v-else-if="log.action === 'task-deleted'" class="operation-value operation-value--before">
                        {{ displayChangeValue(change, 'before') }}
                      </span>
                      <span v-else class="operation-value operation-value--after">
                        {{ displayChangeValue(change, 'after') }}
                      </span>
                    </div>
                  </div>
                </details>
                <span v-else class="operation-no-change">配置未发生变化</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="!loading && filteredLogs.length === 0" class="empty-state">
        <ClipboardList :size="28" />
        <strong>{{ operationLogs.length === 0 ? '暂无操作记录' : '没有匹配的操作记录' }}</strong>
      </div>

      <div v-if="filteredLogs.length > 0" class="pagination-bar" aria-label="操作记录分页">
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
            <button class="icon-button icon-button--small" type="button" title="第一页" aria-label="第一页" :disabled="pagination.page === 1" @click="currentPage = 1">
              <ChevronsLeft :size="15" />
            </button>
            <button class="icon-button icon-button--small" type="button" title="上一页" aria-label="上一页" :disabled="pagination.page === 1" @click="currentPage = pagination.page - 1">
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
</template>
