import { reactive } from 'vue'

export interface ToastMessage {
  id: number
  type: 'success' | 'error' | 'info'
  message: string
}

const state = reactive<{ items: ToastMessage[] }>({ items: [] })
let nextId = 1

function push(type: ToastMessage['type'], message: string): void {
  const id = nextId++
  state.items.push({ id, type, message })
  window.setTimeout(() => remove(id), 4200)
}

function remove(id: number): void {
  const index = state.items.findIndex((item) => item.id === id)
  if (index >= 0) state.items.splice(index, 1)
}

export const toast = {
  state,
  success: (message: string) => push('success', message),
  error: (message: string) => push('error', message),
  info: (message: string) => push('info', message),
  remove,
}
