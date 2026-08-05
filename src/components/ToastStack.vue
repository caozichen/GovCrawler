<script setup lang="ts">
import { CheckCircle2, CircleAlert, Info, X } from '@lucide/vue'
import { toast } from '../services/toast'
</script>

<template>
  <div class="toast-stack" aria-live="polite">
    <div v-for="item in toast.state.items" :key="item.id" class="toast" :class="`toast--${item.type}`">
      <CheckCircle2 v-if="item.type === 'success'" :size="18" />
      <CircleAlert v-else-if="item.type === 'error'" :size="18" />
      <Info v-else :size="18" />
      <span>{{ item.message }}</span>
      <button class="icon-button icon-button--small" type="button" aria-label="关闭提示" @click="toast.remove(item.id)">
        <X :size="15" />
      </button>
    </div>
  </div>
</template>
