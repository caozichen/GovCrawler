<script setup lang="ts">
import { computed, ref } from 'vue'
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
const runningTaskId = ref('')
const openingScriptId = ref('')
const deletingTask = ref<CrawlerTask | null>(null)
const deleting = ref(false)

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

const latestByTask = computed(() => {
  const map = new Map<string, (typeof runs.value)[number]>()
  for (const run of runs.value) if (!map.has(run.taskId)) map.set(run.taskId, run)
  return map
})

function runtimeStatus(task: CrawlerTask): RunStatus | 'idle' {
  return latestByTask.value.get(task.id)?.status || 'idle'
}

function openCreate() {
  editingTask.value = null
  drawerOpen.value = true
}

function openEdit(task: CrawlerTask) {
  editingTask.value = task
  drawerOpen.value = true
}

function openDetails(task: CrawlerTask) {
  detailsTask.value = task
  detailsDrawerOpen.value = true
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
  runningTaskId.value = task.id
  try {
    await consoleStore.runTask(task.id)
    toast.success(`“${task.name}”已进入运行队列。`)
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    runningTaskId.value = ''
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

async function validateTask(id: string) {
  try {
    const result = await api.validateTask(id)
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
        <input v-model="query" type="search" placeholder="搜索任务或网址" />
      </label>
      <label class="select-shell">
        <Filter :size="16" />
        <select v-model="categoryFilter" aria-label="按分类筛选任务">
          <option value="all">全部分类</option>
          <option v-for="category in categories" :key="category.id" :value="category.id">{{ category.name }}</option>
        </select>
      </label>
      <button class="button button--ghost category-settings-button" type="button" @click="categoryDialogOpen = true">
        <Tags :size="16" /> 分类配置
      </button>
      <span class="table-toolbar__count">{{ filteredTasks.length }} TASKS</span>
    </div>

    <div class="data-panel" :class="{ 'data-panel--loading': loading }">
      <div class="table-scroll">
        <table class="data-table task-table">
          <thead>
            <tr>
              <th>分类</th>
              <th>任务 / 网址</th>
              <th>爬虫文件</th>
              <th>运行状态</th>
              <th>上次尝试</th>
              <th class="table-actions-heading">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in filteredTasks" :key="task.id">
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
                    :disabled="runningTaskId === task.id || ['queued', 'running', 'stopping'].includes(runtimeStatus(task))"
                    @click="runTask(task)"
                  >
                    <Play :size="16" />
                  </button>
                  <button class="icon-button" type="button" title="脚本详情" aria-label="脚本详情" @click="openDetails(task)">
                    <FileText :size="16" />
                  </button>
                  <button class="icon-button" type="button" title="检查配置" aria-label="检查配置" @click="validateTask(task.id)">
                    <ShieldCheck :size="16" />
                  </button>
                  <button class="icon-button" type="button" title="编辑" aria-label="编辑" @click="openEdit(task)">
                    <Pencil :size="16" />
                  </button>
                  <button class="icon-button icon-button--danger" type="button" title="删除" aria-label="删除" @click="deletingTask = task">
                    <Trash2 :size="16" />
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
</template>
