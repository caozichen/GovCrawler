<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { Check, FileCode2, FolderOpen, LoaderCircle, Save, X } from '@lucide/vue'
import { api, ApiError } from '../services/api'
import { toast } from '../services/toast'
import type { AppSettings, CrawlerTask, TaskPayload } from '../types'

const props = defineProps<{
  open: boolean
  task: CrawlerTask | null
  settings: AppSettings | null
  saving: boolean
}>()

const emit = defineEmits<{
  close: []
  save: [payload: TaskPayload]
  validate: [payload: TaskPayload]
}>()

const form = reactive<TaskPayload>({
  name: '',
  url: '',
  scriptPath: '',
  pythonPath: '',
  enabled: true,
  intervalMinutes: 30,
  timeoutSeconds: 600,
  extraArgs: [],
})
const extraArgsText = ref('')
const errorMessage = ref('')
const pickingPath = ref<'scriptPath' | 'pythonPath' | null>(null)
const title = computed(() => (props.task ? '编辑爬虫任务' : '新增爬虫任务'))

watch(
  () => [props.open, props.task] as const,
  () => {
    if (!props.open) return
    Object.assign(form, {
      name: props.task?.name || '',
      url: props.task?.url || '',
      scriptPath: props.task?.scriptPath || '',
      pythonPath: props.task?.pythonPath || '',
      enabled: props.task?.enabled ?? true,
      intervalMinutes: props.task?.intervalMinutes || 30,
      timeoutSeconds: props.task?.timeoutSeconds || props.settings?.defaultTimeoutSeconds || 600,
      extraArgs: props.task?.extraArgs || [],
    })
    extraArgsText.value = (props.task?.extraArgs || []).join('\n')
    errorMessage.value = ''
  },
  { immediate: true },
)

function submit() {
  const payload = buildPayload()
  if (payload) emit('save', payload)
}

function validateDraft() {
  const payload = buildPayload()
  if (payload) emit('validate', payload)
}

async function choosePath(field: 'scriptPath' | 'pythonPath') {
  if (pickingPath.value) return
  pickingPath.value = field
  try {
    const kind = field === 'scriptPath' ? 'python-script' : 'python-executable'
    const result = await api.pickPath(kind, form[field])
    if (result.path) form[field] = result.path
  } catch (error) {
    toast.error(error instanceof ApiError ? error.message : '无法打开本机文件选择器。')
  } finally {
    pickingPath.value = null
  }
}

function buildPayload(): TaskPayload | null {
  errorMessage.value = ''
  if (!form.name.trim() || !form.url.trim() || !form.scriptPath.trim()) {
    errorMessage.value = '请填写任务名称、网址和爬虫文件。'
    return null
  }
  try {
    const url = new URL(form.url)
    if (!['http:', 'https:'].includes(url.protocol)) throw new Error('protocol')
  } catch {
    errorMessage.value = '网址必须是有效的 HTTP 或 HTTPS 地址。'
    return null
  }

  return {
    ...form,
    name: form.name.trim(),
    url: form.url.trim(),
    scriptPath: form.scriptPath.trim(),
    pythonPath: form.pythonPath.trim(),
    extraArgs: extraArgsText.value
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean),
  }
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="drawer-backdrop" @click.self="emit('close')">
      <aside class="drawer" role="dialog" aria-modal="true" :aria-label="title">
        <header class="drawer__header">
          <div>
            <span class="eyebrow">TASK CONFIGURATION</span>
            <h2>{{ title }}</h2>
          </div>
          <button class="icon-button" type="button" aria-label="关闭" @click="emit('close')"><X :size="19" /></button>
        </header>

        <form class="drawer__body" @submit.prevent="submit">
          <section class="form-section">
            <div class="form-section__title"><span>01</span> 基础配置</div>
            <label class="field">
              <span>任务名称</span>
              <input v-model="form.name" placeholder="例如：行业资讯采集" maxlength="80" />
            </label>
            <label class="field">
              <span>目标网址</span>
              <input v-model="form.url" placeholder="https://example.com" inputmode="url" />
            </label>
          </section>

          <section class="form-section">
            <div class="form-section__title"><span>02</span> 执行配置</div>
            <label class="field">
              <span>Python 爬虫文件</span>
              <div class="input-shell input-shell--plain">
                <FileCode2 :size="17" />
                <input v-model="form.scriptPath" placeholder="C:\\path\\crawler.py 或 /Users/name/crawler.py" />
                <button
                  class="input-action"
                  type="button"
                  title="选择 Python 爬虫文件"
                  aria-label="选择 Python 爬虫文件"
                  :disabled="Boolean(pickingPath)"
                  @click="choosePath('scriptPath')"
                >
                  <LoaderCircle v-if="pickingPath === 'scriptPath'" class="loading-indicator" :size="17" />
                  <FolderOpen v-else :size="17" />
                </button>
              </div>
            </label>
            <label class="field">
              <span>Python 解释器（留空使用全局设置）</span>
              <div class="input-shell input-shell--plain">
                <input v-model="form.pythonPath" placeholder="例如：C:\\Python312\\python.exe 或 /usr/bin/python3" />
                <button
                  class="input-action"
                  type="button"
                  title="选择 Python 解释器"
                  aria-label="选择 Python 解释器"
                  :disabled="Boolean(pickingPath)"
                  @click="choosePath('pythonPath')"
                >
                  <LoaderCircle v-if="pickingPath === 'pythonPath'" class="loading-indicator" :size="17" />
                  <FolderOpen v-else :size="17" />
                </button>
              </div>
            </label>
            <label class="field">
              <span>超时（秒）</span>
              <input v-model.number="form.timeoutSeconds" type="number" min="5" max="86400" />
            </label>
            <label class="field">
              <span>额外参数（每行一个）</span>
              <textarea v-model="extraArgsText" rows="4" placeholder="--mode&#10;full" />
            </label>
          </section>

          <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>

          <footer class="drawer__footer">
            <button
              class="button button--ghost"
              type="button"
              :disabled="saving"
              @click="validateDraft"
            >
              <Check :size="17" /> 检查配置
            </button>
            <div class="drawer__footer-spacer" />
            <button class="button button--ghost" type="button" @click="emit('close')">取消</button>
            <button class="button button--primary" type="submit" :disabled="saving">
              <Save :size="17" /> {{ saving ? '保存中' : '保存任务' }}
            </button>
          </footer>
        </form>
      </aside>
    </div>
  </Teleport>
</template>
