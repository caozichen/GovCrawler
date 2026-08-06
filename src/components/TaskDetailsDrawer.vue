<script setup lang="ts">
import { ref, watch } from 'vue'
import { FileText, Save, X } from '@lucide/vue'
import type { CrawlerTask } from '../types'

const props = defineProps<{
  open: boolean
  task: CrawlerTask | null
  saving: boolean
}>()

const emit = defineEmits<{
  close: []
  save: [details: string]
}>()

const details = ref('')
const errorMessage = ref('')

watch(
  () => [props.open, props.task] as const,
  () => {
    if (!props.open) return
    details.value = props.task?.details || ''
    errorMessage.value = ''
  },
  { immediate: true },
)

function submit() {
  const value = details.value.trim()
  if (value.length > 500) {
    errorMessage.value = '脚本详情不能超过 500 字。'
    return
  }
  emit('save', value)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="drawer-backdrop" @click.self="emit('close')">
      <aside class="drawer" role="dialog" aria-modal="true" aria-label="脚本详情">
        <header class="drawer__header">
          <div>
            <span class="eyebrow">SCRIPT DETAILS</span>
            <h2>{{ task?.name || '脚本详情' }}</h2>
          </div>
          <button class="icon-button" type="button" aria-label="关闭" @click="emit('close')"><X :size="19" /></button>
        </header>

        <form class="drawer__body details-editor" @submit.prevent="submit">
          <section class="form-section">
            <div class="form-section__title"><FileText :size="15" /> 脚本详情</div>
            <label class="field">
              <span>功能、覆盖范围、核心逻辑、使用方法与注意事项</span>
              <textarea
                v-model="details"
                class="details-editor__textarea"
                maxlength="500"
                rows="16"
                placeholder="填写简短明确的脚本详情"
                autofocus
              />
            </label>
            <div class="details-editor__count" aria-live="polite">{{ details.length }} / 500</div>
          </section>

          <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>

          <footer class="drawer__footer">
            <div class="drawer__footer-spacer" />
            <button class="button button--ghost" type="button" :disabled="saving" @click="emit('close')">取消</button>
            <button class="button button--primary" type="submit" :disabled="saving">
              <Save :size="17" /> {{ saving ? '保存中' : '保存详情' }}
            </button>
          </footer>
        </form>
      </aside>
    </div>
  </Teleport>
</template>
