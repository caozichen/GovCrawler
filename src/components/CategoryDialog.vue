<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { Check, Palette, Pencil, Plus, Save, Tags, Trash2, X } from '@lucide/vue'
import { ApiError } from '../services/api'
import { toast } from '../services/toast'
import { useConsoleStore } from '../stores/console'
import type { TaskCategory } from '../types'
import CategoryBadge from './CategoryBadge.vue'
import ConfirmDialog from './ConfirmDialog.vue'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const consoleStore = useConsoleStore()
const { categories, tasks } = storeToRefs(consoleStore)
const editingId = ref('')
const name = ref('')
const color = ref('#3B82F6')
const saving = ref(false)
const deleting = ref(false)
const deletingCategory = ref<TaskCategory | null>(null)
const presetColors = ['#3B82F6', '#22A06B', '#D98620', '#E05252', '#8B5CF6', '#0891B2', '#64748B']

const editing = computed(() => categories.value.find((category) => category.id === editingId.value) || null)

watch(
  () => props.open,
  (open) => {
    if (open) resetForm()
  },
)

function usageCount(categoryId: string) {
  return tasks.value.filter((task) => task.categoryId === categoryId).length
}

function startEdit(category: TaskCategory) {
  editingId.value = category.id
  name.value = category.name
  color.value = category.color
}

function resetForm() {
  editingId.value = ''
  name.value = ''
  color.value = '#3B82F6'
}

async function submit() {
  const trimmedName = name.value.trim()
  if (!trimmedName) {
    toast.error('请输入分类名称。')
    return
  }
  const duplicate = categories.value.some(
    (category) => category.id !== editingId.value && category.name.toLowerCase() === trimmedName.toLowerCase(),
  )
  if (duplicate) {
    toast.error('分类名称已存在。')
    return
  }

  saving.value = true
  try {
    const payload = { name: trimmedName, color: color.value.toUpperCase() }
    if (editing.value) {
      await consoleStore.updateCategory(editing.value.id, payload)
      toast.success('分类已更新。')
    } else {
      await consoleStore.createCategory(payload)
      toast.success('分类已添加。')
    }
    resetForm()
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    saving.value = false
  }
}

async function confirmDelete() {
  if (!deletingCategory.value) return
  deleting.value = true
  try {
    const result = await consoleStore.deleteCategory(deletingCategory.value.id)
    toast.success(
      result.reassignedTasks > 0
        ? `分类已删除，${result.reassignedTasks} 个任务已转移到其他分类。`
        : '分类已删除。',
    )
    if (editingId.value === deletingCategory.value.id) resetForm()
    deletingCategory.value = null
  } catch (error) {
    toast.error(messageOf(error))
  } finally {
    deleting.value = false
  }
}

function messageOf(error: unknown): string {
  return error instanceof ApiError ? error.message : '分类操作失败，请检查本地服务。'
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="modal-backdrop" @click.self="emit('close')">
      <section class="category-dialog" role="dialog" aria-modal="true" aria-label="分类配置">
        <header class="category-dialog__header">
          <div>
            <span class="eyebrow">TASK CATEGORIES</span>
            <h2>分类配置</h2>
          </div>
          <button class="icon-button" type="button" aria-label="关闭" title="关闭" @click="emit('close')">
            <X :size="19" />
          </button>
        </header>

        <div class="category-dialog__body">
          <form class="category-editor" @submit.prevent="submit">
            <label class="field">
              <span>分类名称</span>
              <div class="input-shell input-shell--plain">
                <Tags :size="16" />
                <input v-model="name" maxlength="20" placeholder="输入分类名称" />
              </div>
            </label>
            <label class="field">
              <span>标签颜色</span>
              <div class="category-color-control">
                <Palette :size="16" />
                <input v-model="color" type="color" aria-label="标签颜色" />
                <span>{{ color.toUpperCase() }}</span>
              </div>
            </label>
            <div class="category-editor__actions">
              <button v-if="editing" class="button button--ghost" type="button" :disabled="saving" @click="resetForm">
                取消编辑
              </button>
              <button class="button button--primary" type="submit" :disabled="saving">
                <Save v-if="editing" :size="16" />
                <Plus v-else :size="16" />
                {{ saving ? '保存中' : editing ? '保存修改' : '添加分类' }}
              </button>
            </div>
          </form>

          <div class="category-color-presets" aria-label="常用颜色">
            <button
              v-for="preset in presetColors"
              :key="preset"
              class="category-color-preset"
              :class="{ 'category-color-preset--active': color.toUpperCase() === preset }"
              :style="{ '--preset-color': preset }"
              type="button"
              :title="preset"
              :aria-label="`使用颜色 ${preset}`"
              @click="color = preset"
            >
              <Check v-if="color.toUpperCase() === preset" :size="12" />
            </button>
          </div>

          <div class="category-list-heading">
            <span>现有分类</span>
            <strong>{{ categories.length }} CATEGORIES</strong>
          </div>
          <div class="category-list">
            <div v-for="category in categories" :key="category.id" class="category-list__row">
              <CategoryBadge :category="category" />
              <span class="category-list__usage">{{ usageCount(category.id) }} 个任务</span>
              <div class="row-actions">
                <button class="icon-button icon-button--small" type="button" title="编辑分类" aria-label="编辑分类" @click="startEdit(category)">
                  <Pencil :size="15" />
                </button>
                <button
                  class="icon-button icon-button--small icon-button--danger"
                  type="button"
                  title="删除分类"
                  aria-label="删除分类"
                  :disabled="categories.length <= 1"
                  @click="deletingCategory = category"
                >
                  <Trash2 :size="15" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </Teleport>

  <ConfirmDialog
    :open="Boolean(deletingCategory)"
    title="删除分类"
    :message="usageCount(deletingCategory?.id || '') > 0
      ? `“${deletingCategory?.name || ''}”下有 ${usageCount(deletingCategory?.id || '')} 个任务。删除后会自动转移到其他分类，确定继续吗？`
      : `确定删除“${deletingCategory?.name || ''}”吗？`"
    confirm-text="删除"
    :busy="deleting"
    @close="deletingCategory = null"
    @confirm="confirmDelete"
  />
</template>
