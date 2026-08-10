<script setup lang="ts">
import { TriangleAlert, X } from '@lucide/vue'

const props = defineProps<{
  open: boolean
  title: string
  message: string
  confirmText?: string
  busy?: boolean
}>()

const emit = defineEmits<{
  close: []
  confirm: []
}>()

function requestClose() {
  if (!props.busy) emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-backdrop" @click.self="requestClose">
      <section class="confirm-dialog" role="dialog" aria-modal="true" :aria-label="title">
        <button class="icon-button confirm-dialog__close" type="button" aria-label="关闭" :disabled="busy" @click="requestClose">
          <X :size="18" />
        </button>
        <div class="confirm-dialog__icon"><TriangleAlert :size="22" /></div>
        <h2>{{ title }}</h2>
        <p>{{ message }}</p>
        <div class="confirm-dialog__actions">
          <button class="button button--ghost" type="button" :disabled="busy" @click="requestClose">取消</button>
          <button class="button button--danger" type="button" :disabled="busy" @click="$emit('confirm')">
            {{ busy ? '处理中' : confirmText || '确认' }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>
