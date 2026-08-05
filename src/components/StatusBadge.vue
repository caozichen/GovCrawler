<script setup lang="ts">
import type { RunStatus } from '../types'

defineProps<{
  status: RunStatus | 'idle' | 'enabled' | 'disabled' | 'healthy' | 'warning'
}>()

const labels: Record<string, string> = {
  queued: '排队中',
  running: '运行中',
  stopping: '停止中',
  succeeded: '成功',
  failed: '失败',
  timed_out: '超时',
  stopped: '已停止',
  interrupted: '已中断',
  idle: '待机',
  enabled: '已启用',
  disabled: '已禁用',
  healthy: '正常',
  warning: '需检查',
}
</script>

<template>
  <span class="status-badge" :class="`status-badge--${status}`">
    <span class="status-badge__dot" />
    {{ labels[status] }}
  </span>
</template>
