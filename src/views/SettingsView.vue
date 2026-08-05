<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { CheckCircle2, Cpu, Database, FolderOpen, FolderOutput, LoaderCircle, Save, ShieldCheck, XCircle } from '@lucide/vue'
import { api, ApiError } from '../services/api'
import { toast } from '../services/toast'
import { useConsoleStore } from '../stores/console'
import type { AppSettings, ValidationResult } from '../types'

const consoleStore = useConsoleStore()
const { settings } = storeToRefs(consoleStore)
const form = reactive<AppSettings>({
  pythonPath: 'python',
  outputRoot: '',
  maxConcurrency: 2,
  defaultTimeoutSeconds: 600,
  retentionDays: 30,
})
const saving = ref(false)
const validating = ref(false)
const pickingPath = ref<'pythonPath' | 'outputRoot' | null>(null)
const validation = ref<ValidationResult | null>(null)

watch(
  settings,
  (value) => {
    if (value) Object.assign(form, value)
  },
  { immediate: true },
)

watch(
  form,
  () => {
    validation.value = null
  },
  { deep: true },
)

async function save() {
  saving.value = true
  try {
    const saved = await api.saveSettings({ ...form })
    consoleStore.settings = saved
    toast.success('系统设置已保存。')
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    saving.value = false
  }
}

async function validate() {
  validating.value = true
  try {
    validation.value = await api.validateSettings({ ...form })
    if (validation.value.valid) toast.success('运行环境检查通过。')
    else toast.error('部分环境检查未通过。')
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    validating.value = false
  }
}

async function choosePath(field: 'pythonPath' | 'outputRoot') {
  if (pickingPath.value) return
  pickingPath.value = field
  try {
    const kind = field === 'pythonPath' ? 'python-executable' : 'directory'
    const result = await api.pickPath(kind, form[field])
    if (result.path) form[field] = result.path
  } catch (error) {
    toast.error(error instanceof ApiError ? error.message : '无法打开本机路径选择器。')
  } finally {
    pickingPath.value = null
  }
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : '操作失败，请检查本地服务。'
}
</script>

<template>
  <section class="workspace-page settings-page">
    <div class="section-heading section-heading--top">
      <div>
        <span class="eyebrow">SYSTEM PREFERENCES</span>
        <h2>本地环境</h2>
      </div>
      <div class="section-heading__actions">
        <button class="button button--ghost" type="button" :disabled="validating" @click="validate">
          <ShieldCheck :size="17" /> {{ validating ? '检查中' : '检查环境' }}
        </button>
        <button class="button button--primary" type="button" :disabled="saving" @click="save">
          <Save :size="17" /> {{ saving ? '保存中' : '保存设置' }}
        </button>
      </div>
    </div>

    <div v-if="validation" class="validation-band">
      <div :class="validation.checks.python ? 'validation-item--ok' : 'validation-item--bad'" class="validation-item">
        <CheckCircle2 v-if="validation.checks.python" :size="18" />
        <XCircle v-else :size="18" />
        <span>Python 解释器</span>
        <strong>{{ validation.checks.python ? '可用' : '不可用' }}</strong>
      </div>
      <div :class="validation.checks.output ? 'validation-item--ok' : 'validation-item--bad'" class="validation-item">
        <CheckCircle2 v-if="validation.checks.output" :size="18" />
        <XCircle v-else :size="18" />
        <span>输出目录</span>
        <strong>{{ validation.checks.output ? '可写' : '不可写' }}</strong>
      </div>
    </div>

    <div class="settings-layout">
      <section class="settings-section">
        <header>
          <div class="settings-section__icon"><Cpu :size="20" /></div>
          <div>
            <span class="eyebrow">RUNTIME</span>
            <h3>执行环境</h3>
          </div>
        </header>
        <div class="settings-form">
          <label class="field">
            <span>默认 Python 解释器</span>
            <div class="input-shell input-shell--plain">
              <input v-model="form.pythonPath" placeholder="python 或 D:\\Python312\\python.exe" />
              <button
                class="input-action"
                type="button"
                title="选择默认 Python 解释器"
                aria-label="选择默认 Python 解释器"
                :disabled="Boolean(pickingPath)"
                @click="choosePath('pythonPath')"
              >
                <LoaderCircle v-if="pickingPath === 'pythonPath'" class="loading-indicator" :size="17" />
                <FolderOpen v-else :size="17" />
              </button>
            </div>
          </label>
          <div class="field-grid">
            <label class="field">
              <span>最大并发数</span>
              <input v-model.number="form.maxConcurrency" type="number" min="1" max="8" />
            </label>
            <label class="field">
              <span>默认超时（秒）</span>
              <input v-model.number="form.defaultTimeoutSeconds" type="number" min="5" max="86400" />
            </label>
          </div>
        </div>
      </section>

      <section class="settings-section">
        <header>
          <div class="settings-section__icon settings-section__icon--blue"><FolderOutput :size="20" /></div>
          <div>
            <span class="eyebrow">OUTPUT</span>
            <h3>结果目录</h3>
          </div>
        </header>
        <div class="settings-form">
          <label class="field">
            <span>输出根目录</span>
            <div class="input-shell input-shell--plain">
              <input v-model="form.outputRoot" placeholder="D:\\CursorCode\\pa" />
              <button
                class="input-action"
                type="button"
                title="选择输出根目录"
                aria-label="选择输出根目录"
                :disabled="Boolean(pickingPath)"
                @click="choosePath('outputRoot')"
              >
                <LoaderCircle v-if="pickingPath === 'outputRoot'" class="loading-indicator" :size="17" />
                <FolderOpen v-else :size="17" />
              </button>
            </div>
          </label>
          <div class="directory-preview">
            <Database :size="17" />
            <code>{{ form.outputRoot }}\2026-08-04_15-30-12-045</code>
          </div>
          <label class="field field--compact">
            <span>历史保留天数</span>
            <input v-model.number="form.retentionDays" type="number" min="1" max="3650" />
          </label>
        </div>
      </section>
    </div>
  </section>
</template>
