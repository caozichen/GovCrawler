<script setup lang="ts">
import type { TaskCategory } from '../types'

defineProps<{
  category?: TaskCategory | null
}>()
</script>

<template>
  <span
    v-if="category"
    class="category-badge"
    :style="{ '--category-color': category.color }"
    :title="category.name"
  >
    {{ category.name }}
  </span>
  <span v-else class="category-badge category-badge--missing">未分类</span>
</template>
