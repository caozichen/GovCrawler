<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import {
  CheckCircle2,
  FileCode2,
  FileText,
  Filter,
  LoaderCircle,
  Pencil,
  Play,
  Plus,
  Search,
  ShieldCheck,
  Square,
  Tags,
  Trash2,
} from '@lucide/vue'
import CategoryBadge from '../components/CategoryBadge.vue'
import CategoryDialog from '../components/CategoryDialog.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import StatusBadge from '../components/StatusBadge.vue'
import TaskDetailsDrawer from '../components/TaskDetailsDrawer.vue'
import TaskDrawer from '../components/TaskDrawer.vue'
import { api, ApiError } from '../services/api'
import { toast } from '../services/toast'
import { useConsoleStore } from '../stores/console'
import type { CrawlerTask, RunStatus, TaskPayload } from '../types'
import { mapSettledWithConcurrency } from '../utils/concurrency'

const VALIDATION_CONCURRENCY = 3
const activeRunStatuses: Array<RunStatus | 'idle'> = ['queued', 'running', 'stopping']

const consoleStore = useConsoleStore()
const { tasks, categories, runs, settings, dashboard, loading } = storeToRefs(consoleStore)
const query = ref('')
const categoryFilter = ref('all')
const categoryDialogOpen = ref(false)
const drawerOpen = ref(false)
const editingTask = ref<CrawlerTask | null>(null)
const saving = ref(false)
const detailsDrawerOpen = ref(false)
const detailsTask = ref<CrawlerTask | null>(null)
const detailsSaving = ref(false)
const openingScriptId = ref('')
const deletingTask = ref<CrawlerTask | null>(null)
const deleting = ref(false)
const selectedTaskIds = ref(new Set<string>())
const pendingRunTaskIds = ref(new Set<string>())
const pendingStopTaskIds = ref(new Set<string>())
const bulkAction = ref<'run' | 'validate' | 'delete' | null>(null)
const bulkDeleteOpen = ref(false)

const filteredTasks = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  return tasks.value.filter((task) => {
    const matchesCategory = categoryFilter.value === 'all' || task.categoryId === categoryFilter.value
    const matchesKeyword = !keyword
      || [task.name, task.url, task.scriptPath, task.details].some((value) => value.toLowerCase().includes(keyword))
    return matchesCategory && matchesKeyword
  })
})

const categoryById = computed(() => new Map(categories.value.map((category) => [category.id, category])))

const selectedTasks = computed(() => tasks.value.filter((task) => selectedTaskIds.value.has(task.id)))
const selectedCount = computed(() => selectedTasks.value.length)
const selectedVisibleCount = computed(() => filteredTasks.value.filter((task) => selectedTaskIds.value.has(task.id)).length)
const allVisibleSelected = computed(() => filteredTasks.value.length > 0 && selectedVisibleCount.value === filteredTasks.value.length)
const someVisibleSelected = computed(() => selectedVisibleCount.value > 0 && !allVisibleSelected.value)
const runnableSelectedTasks = computed(() => selectedTasks.value.filter((task) => !isTaskOperationLocked(task)))
const lockedSelectedCount = computed(() => selectedTasks.value.filter((task) => isTaskOperationLocked(task)).length)
const bulkActionBusy = computed(() => bulkAction.value !== null || consoleStore.runSubmissionBusy)

watch([query, categoryFilter], () => {
  selectedTaskIds.value = new Set()
  bulkDeleteOpen.value = false
})

watch(
  () => tasks.value.map((task) => task.id),
  (taskIds) => {
    const availableIds = new Set(taskIds)
    const nextSelection = new Set([...selectedTaskIds.value].filter((id) => availableIds.has(id)))
    if (nextSelection.size !== selectedTaskIds.value.size) selectedTaskIds.value = nextSelection
  },
)

const latestByTask = computed(() => {
  const map = new Map<string, (typeof runs.value)[number]>()
  for (const run of runs.value) if (!map.has(run.taskId)) map.set(run.taskId, run)
  return map
})

function runtimeStatus(task: CrawlerTask): RunStatus | 'idle' {
  return latestByTask.value.get(task.id)?.status || 'idle'
}

function isTaskRuntimeActive(task: CrawlerTask): boolean {
  return activeRunStatuses.includes(runtimeStatus(task))
}

function isTaskOperationLocked(task: CrawlerTask): boolean {
  return isTaskRuntimeActive(task)
    || pendingRunTaskIds.value.has(task.id)
    || pendingStopTaskIds.value.has(task.id)
}

function canStopTask(task: CrawlerTask): boolean {
  return ['queued', 'running'].includes(runtimeStatus(task)) && !pendingStopTaskIds.value.has(task.id)
}

function updatePendingTask(setRef: typeof pendingRunTaskIds, id: string, pending: boolean) {
  const next = new Set(setRef.value)
  if (pending) next.add(id)
  else next.delete(id)
  setRef.value = next
}

function openCreate() {
  editingTask.value = null
  drawerOpen.value = true
}

function openEdit(task: CrawlerTask) {
  if (isTaskOperationLocked(task)) return
  editingTask.value = task
  drawerOpen.value = true
}

function openDetails(task: CrawlerTask) {
  if (isTaskOperationLocked(task)) return
  detailsTask.value = task
  detailsDrawerOpen.value = true
}

function requestDeleteTask(task: CrawlerTask) {
  if (isTaskOperationLocked(task)) return
  deletingTask.value = task
}

function setTaskSelected(id: string, selected: boolean) {
  const nextSelection = new Set(selectedTaskIds.value)
  if (selected) nextSelection.add(id)
  else nextSelection.delete(id)
  selectedTaskIds.value = nextSelection
}

function handleTaskSelection(id: string, event: Event) {
  setTaskSelected(id, (event.target as HTMLInputElement).checked)
}

function handleVisibleSelection(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  const nextSelection = new Set(selectedTaskIds.value)
  for (const task of filteredTasks.value) {
    if (checked) nextSelection.add(task.id)
    else nextSelection.delete(task.id)
  }
  selectedTaskIds.value = nextSelection
}

async function saveTask(payload: TaskPayload) {
  saving.value = true
  try {
    await consoleStore.saveTask(payload, editingTask.value?.id)
    drawerOpen.value = false
    toast.success(editingTask.value ? '任务配置已更新。' : '任务已创建。')
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    saving.value = false
  }
}

async function saveDetails(details: string) {
  if (!detailsTask.value) return
  detailsSaving.value = true
  try {
    await consoleStore.saveTaskDetails(detailsTask.value.id, details)
    detailsDrawerOpen.value = false
    toast.success('脚本详情已保存。')
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    detailsSaving.value = false
  }
}

async function runTask(task: CrawlerTask) {
  if (bulkActionBusy.value || isTaskOperationLocked(task)) return
  updatePendingTask(pendingRunTaskIds, task.id, true)
  try {
    const submitted = await consoleStore.runTask(task.id)
    if (submitted) toast.success(`“${task.name}”已进入运行队列。`)
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    updatePendingTask(pendingRunTaskIds, task.id, false)
  }
}

async function runSelectedTasks() {
  if (bulkActionBusy.value || runnableSelectedTasks.value.length === 0) return
  const targets = [...runnableSelectedTasks.value]
  const skippedCount = selectedCount.value - targets.length
  for (const task of targets) updatePendingTask(pendingRunTaskIds, task.id, true)
  bulkAction.value = 'run'
  try {
    const results = await consoleStore.runTasks(targets.map((task) => task.id))
    if (!results) return
    const acceptedCount = results.filter((result) => result.status === 'fulfilled').length
    const failedCount = results.length - acceptedCount
    if (failedCount === 0 && skippedCount === 0) {
      toast.success(`已将 ${acceptedCount} 个任务加入运行队列。`)
    } else {
      toast.error(`批量运行完成：已加入 ${acceptedCount} 个，跳过 ${skippedCount} 个，失败 ${failedCount} 个。`)
    }
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    for (const task of targets) updatePendingTask(pendingRunTaskIds, task.id, false)
    bulkAction.value = null
  }
}

async function validateSelectedTasks() {
  if (bulkActionBusy.value || selectedTasks.value.length === 0) return
  const targets = [...selectedTasks.value]
  bulkAction.value = 'validate'
  try {
    const results = await mapSettledWithConcurrency(
      targets,
      VALIDATION_CONCURRENCY,
      (task) => api.validateTask(task.id),
    )
    let passedCount = 0
    const failureReasons = new Map<string, number>()
    results.forEach((result) => {
      if (result.status === 'fulfilled' && result.value.valid) {
        passedCount += 1
        return
      }
      const reason = result.status === 'fulfilled'
        ? validationFailureReason(result.value.checks)
        : validationRequestFailureReason(result.reason)
      failureReasons.set(reason, (failureReasons.get(reason) || 0) + 1)
    })
    const failedCount = results.length - passedCount
    if (failedCount === 0) toast.success(`${passedCount} 个任务的配置检查全部通过。`)
    else toast.error(`配置检查完成：通过 ${passedCount} 个，配置未通过 ${failedCount} 个（${formatFailureReasons(failureReasons)}）。`)
  } finally {
    bulkAction.value = null
  }
}

async function confirmBulkDelete() {
  if (bulkActionBusy.value || selectedTasks.value.length === 0) return
  if (lockedSelectedCount.value > 0) {
    toast.error(`有 ${lockedSelectedCount.value} 个任务正在提交、运行或停止中，不能删除。`)
    return
  }
  const targets = [...selectedTasks.value]
  const deletedIds = new Set<string>()
  bulkAction.value = 'delete'
  try {
    for (const task of targets) {
      try {
        await api.deleteTask(task.id)
        deletedIds.add(task.id)
      } catch {
        // Keep failed items selected so the user can retry them.
      }
    }
    selectedTaskIds.value = new Set([...selectedTaskIds.value].filter((id) => !deletedIds.has(id)))
    bulkDeleteOpen.value = false
    await consoleStore.refresh(true)
    const failedCount = targets.length - deletedIds.size
    if (failedCount === 0) toast.success(`已删除 ${deletedIds.size} 个任务，历史运行目录保持不变。`)
    else toast.error(`批量删除完成：已删除 ${deletedIds.size} 个，失败 ${failedCount} 个。`)
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    bulkAction.value = null
  }
}

async function stopTask(task: CrawlerTask) {
  const run = latestByTask.value.get(task.id)
  if (!run || !canStopTask(task)) return
  updatePendingTask(pendingStopTaskIds, task.id, true)
  try {
    await consoleStore.stopRun(run.id)
    toast.success(`已停止“${task.name}”。`)
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    updatePendingTask(pendingStopTaskIds, task.id, false)
  }
}

async function openScript(task: CrawlerTask) {
  if (openingScriptId.value) return
  openingScriptId.value = task.id
  try {
    await api.openTaskScript(task.id)
    toast.success('已在文件管理器中定位爬虫文件。')
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    openingScriptId.value = ''
  }
}

async function validateTask(task: CrawlerTask) {
  if (bulkActionBusy.value || isTaskOperationLocked(task)) return
  try {
    const result = await api.validateTask(task.id)
    if (result.valid) toast.success('Python、脚本和输出目录检查通过。')
    else {
      const failed = Object.entries(result.checks)
        .filter(([, passed]) => !passed)
        .map(([name]) => ({ python: 'Python', script: '爬虫文件', output: '输出目录' } as Record<string, string>)[name] || name)
      toast.error(`${failed.join('、')}检查未通过。`)
    }
  } catch (error) {
    toast.error(messageOf(error))
  }
}

function validationFailureReason(checks: Record<string, boolean>): string {
  const labels: Record<string, string> = {
    python: 'Python 不可用',
    script: '爬虫文件不存在或格式不正确',
    output: '输出目录不可写',
  }
  const failed = Object.entries(checks)
    .filter(([, passed]) => !passed)
    .map(([name]) => labels[name] || `${name} 检查失败`)
  return failed.join('、') || '未知配置错误'
}

function validationRequestFailureReason(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof TypeError) return '无法连接本地控制服务'
  return '检查请求失败'
}

function formatFailureReasons(reasons: Map<string, number>): string {
  return [...reasons.entries()]
    .map(([reason, count]) => count > 1 ? `${reason} ${count} 个` : reason)
    .join('；')
}

async function validateDraft(payload: TaskPayload) {
  try {
    const result = await api.validateTaskDraft(payload)
    if (result.valid) toast.success('当前表单的 Python、脚本和输出目录检查通过。')
    else {
      const labels: Record<string, string> = { python: 'Python', script: '爬虫文件', output: '输出目录' }
      const failed = Object.entries(result.checks)
        .filter(([, passed]) => !passed)
        .map(([name]) => labels[name] || name)
      toast.error(`${failed.join('、')}检查未通过。`)
    }
  } catch (error) {
    toast.error(messageOf(error))
  }
}

async function confirmDelete() {
  if (!deletingTask.value) return
  deleting.value = true
  try {
    await consoleStore.deleteTask(deletingTask.value.id)
    toast.success('任务已删除，历史运行目录保持不变。')
    deletingTask.value = null
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    deleting.value = false
  }
}

function formatTime(value?: string) {
  if (!value) return '--'
  return new Date(value).toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : '操作失败，请检查本地服务。'
}
</script>

<template>
  <section class="workspace-page">
    <div class="metric-strip" aria-label="任务概览">
      <div class="metric-item">
        <span>任务总数</span>
        <strong>{{ dashboard.taskCount }}</strong>
        <small>ALL TASKS</small>
      </div>
      <div class="metric-item metric-item--blue">
        <span>正在执行</span>
        <strong>{{ dashboard.activeCount }}</strong>
        <small>ACTIVE</small>
      </div>
      <div class="metric-item metric-item--success">
        <span>今日成功</span>
        <strong>{{ dashboard.successToday }}</strong>
        <small>SUCCEEDED</small>
      </div>
      <div class="metric-item metric-item--danger">
        <span>今日失败</span>
        <strong>{{ dashboard.failedToday }}</strong>
        <small>FAILED</small>
      </div>
    </div>

    <div class="section-heading">
      <div>
        <span class="eyebrow">TASK REGISTRY</span>
        <h2>爬虫任务</h2>
      </div>
      <button class="button button--primary" type="button" @click="openCreate">
        <Plus :size="17" /> 新增任务
      </button>
    </div>

    <div class="table-toolbar">
      <label class="search-box">
        <Search :size="17" />
        <input v-model="query" type="search" placeholder="搜索任务或网址" :disabled="bulkActionBusy" />
      </label>
      <label class="select-shell">
        <Filter :size="16" />
        <select v-model="categoryFilter" aria-label="按分类筛选任务" :disabled="bulkActionBusy">
          <option value="all">全部分类</option>
          <option v-for="category in categories" :key="category.id" :value="category.id">{{ category.name }}</option>
        </select>
      </label>
      <button class="button button--ghost category-settings-button" type="button" :disabled="bulkActionBusy" @click="categoryDialogOpen = true">
        <Tags :size="16" /> 分类配置
      </button>
      <div v-if="selectedCount" class="bulk-actions" aria-label="批量操作">
        <span class="bulk-selection-summary" role="status">已选 <strong>{{ selectedCount }}</strong> 个任务</span>
        <button
          class="button button--primary bulk-action-button"
          type="button"
          :disabled="bulkActionBusy || runnableSelectedTasks.length === 0"
          @click="runSelectedTasks"
        >
          <LoaderCircle v-if="bulkAction === 'run'" class="loading-indicator" :size="16" />
          <Play v-else :size="16" />
          运行
        </button>
        <button
          class="button button--ghost bulk-action-button"
          type="button"
          :disabled="bulkActionBusy || lockedSelectedCount > 0"
          :title="lockedSelectedCount > 0 ? '提交或运行中的任务不能检查配置' : '检查所选任务配置'"
          @click="validateSelectedTasks"
        >
          <LoaderCircle v-if="bulkAction === 'validate'" class="loading-indicator" :size="16" />
          <ShieldCheck v-else :size="16" />
          检查配置
        </button>
        <button
          class="button button--danger bulk-action-button"
          type="button"
          :disabled="bulkActionBusy || lockedSelectedCount > 0"
          :title="lockedSelectedCount > 0 ? '提交或运行中的任务不能删除' : '删除所选任务'"
          @click="bulkDeleteOpen = true"
        >
          <Trash2 :size="16" /> 删除
        </button>
      </div>
      <span v-else class="table-toolbar__count">{{ filteredTasks.length }} TASKS</span>
    </div>

    <div class="data-panel" :class="{ 'data-panel--loading': loading }">
      <div class="table-scroll">
        <table class="data-table task-table">
          <thead>
            <tr>
              <th class="task-select-column">
                <input
                  class="task-selection-checkbox"
                  type="checkbox"
                  aria-label="选择当前筛选结果中的全部任务"
                  :checked="allVisibleSelected"
                  :indeterminate="someVisibleSelected"
                  :disabled="bulkActionBusy || filteredTasks.length === 0"
                  @change="handleVisibleSelection"
                />
              </th>
              <th>分类</th>
              <th>任务 / 网址</th>
              <th>爬虫文件</th>
              <th>运行状态</th>
              <th>上次尝试</th>
              <th class="table-actions-heading">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in filteredTasks" :key="task.id" :class="{ 'task-row--selected': selectedTaskIds.has(task.id) }">
              <td class="task-select-column">
                <input
                  class="task-selection-checkbox"
                  type="checkbox"
                  :aria-label="`选择任务：${task.name}`"
                  :checked="selectedTaskIds.has(task.id)"
                  :disabled="bulkActionBusy"
                  @change="handleTaskSelection(task.id, $event)"
                />
              </td>
              <td><CategoryBadge :category="categoryById.get(task.categoryId)" /></td>
              <td>
                <div class="primary-cell">
                  <strong>{{ task.name }}</strong>
                  <a :href="task.url" target="_blank" rel="noreferrer" :title="task.url">{{ task.url }}</a>
                </div>
              </td>
              <td>
                <div class="path-cell" :title="task.scriptPath">
                  <button
                    class="path-cell__open"
                    type="button"
                    title="在文件夹中定位"
                    aria-label="在文件夹中定位爬虫文件"
                    :disabled="Boolean(openingScriptId)"
                    @click="openScript(task)"
                  >
                    <LoaderCircle v-if="openingScriptId === task.id" class="loading-indicator" :size="15" />
                    <FileCode2 v-else :size="15" />
                  </button>
                  <span>{{ task.scriptPath }}</span>
                </div>
              </td>
              <td><StatusBadge :status="runtimeStatus(task)" /></td>
              <td><span class="time-cell">{{ formatTime(task.lastAttemptAt) }}</span></td>
              <td>
                <div class="row-actions">
                  <button
                    class="icon-button icon-button--run"
                    type="button"
                    title="立即运行"
                    aria-label="立即运行"
                    :disabled="bulkActionBusy || isTaskOperationLocked(task)"
                    @click="runTask(task)"
                  >
                    <LoaderCircle v-if="pendingRunTaskIds.has(task.id)" class="loading-indicator" :size="16" />
                    <Play v-else :size="16" />
                  </button>
                  <button class="icon-button" type="button" title="脚本详情" aria-label="脚本详情" :disabled="bulkActionBusy || isTaskOperationLocked(task)" @click="openDetails(task)">
                    <FileText :size="16" />
                  </button>
                  <button class="icon-button" type="button" title="检查配置" aria-label="检查配置" :disabled="bulkActionBusy || isTaskOperationLocked(task)" @click="validateTask(task)">
                    <ShieldCheck :size="16" />
                  </button>
                  <button class="icon-button" type="button" title="编辑" aria-label="编辑" :disabled="bulkActionBusy || isTaskOperationLocked(task)" @click="openEdit(task)">
                    <Pencil :size="16" />
                  </button>
                  <button class="icon-button icon-button--danger" type="button" title="删除" aria-label="删除" :disabled="bulkActionBusy || isTaskOperationLocked(task)" @click="requestDeleteTask(task)">
                    <Trash2 :size="16" />
                  </button>
                  <button
                    class="icon-button icon-button--stop"
                    type="button"
                    title="强行停止"
                    aria-label="强行停止"
                    :disabled="!canStopTask(task)"
                    @click="stopTask(task)"
                  >
                    <LoaderCircle
                      v-if="pendingStopTaskIds.has(task.id) || runtimeStatus(task) === 'stopping'"
                      class="loading-indicator"
                      :size="16"
                    />
                    <Square v-else :size="15" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="!loading && filteredTasks.length === 0" class="empty-state">
        <CheckCircle2 :size="28" />
        <strong>没有匹配的任务</strong>
        <button class="button button--primary" type="button" @click="openCreate"><Plus :size="17" /> 新增任务</button>
      </div>
    </div>
  </section>

  <TaskDrawer
    :open="drawerOpen"
    :task="editingTask"
    :categories="categories"
    :settings="settings"
    :saving="saving"
    @close="drawerOpen = false"
    @save="saveTask"
    @validate="validateDraft"
  />

  <CategoryDialog :open="categoryDialogOpen" @close="categoryDialogOpen = false" />

  <TaskDetailsDrawer
    :open="detailsDrawerOpen"
    :task="detailsTask"
    :saving="detailsSaving"
    @close="detailsDrawerOpen = false"
    @save="saveDetails"
  />

  <ConfirmDialog
    :open="Boolean(deletingTask)"
    title="删除任务"
    :message="`确定删除“${deletingTask?.name || ''}”吗？历史记录和已生成目录不会被删除。`"
    confirm-text="删除"
    :busy="deleting"
    @close="deletingTask = null"
    @confirm="confirmDelete"
  />

  <ConfirmDialog
    :open="bulkDeleteOpen"
    title="批量删除任务"
    :message="`确定删除已勾选的 ${selectedCount} 个任务吗？历史记录和已生成目录不会被删除。`"
    confirm-text="删除"
    :busy="bulkAction === 'delete'"
    @close="bulkDeleteOpen = false"
    @confirm="confirmBulkDelete"
  />
</template>
