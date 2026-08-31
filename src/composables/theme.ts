import { computed, readonly, ref } from 'vue'

export type Theme = 'dark' | 'light'

const STORAGE_KEY = 'pa-crawler-theme'
const theme = ref<Theme>('light')
let initialized = false

function applyTheme(value: Theme) {
  if (typeof document === 'undefined') return
  document.documentElement.dataset.theme = value
  document.documentElement.style.colorScheme = value
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', value === 'dark' ? '#111318' : '#f3f6fb')
}

function storedTheme(): Theme {
  if (typeof window === 'undefined') return 'light'
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export function initializeTheme() {
  if (initialized) return
  initialized = true
  theme.value = storedTheme()
  applyTheme(theme.value)
}

export function useTheme() {
  initializeTheme()

  function setTheme(value: Theme) {
    theme.value = value
    applyTheme(value)
    try {
      window.localStorage.setItem(STORAGE_KEY, value)
    } catch {
      // The selected theme still applies when browser storage is unavailable.
    }
  }

  function toggleTheme() {
    setTheme(theme.value === 'dark' ? 'light' : 'dark')
  }

  return {
    theme: readonly(theme),
    isDark: computed(() => theme.value === 'dark'),
    setTheme,
    toggleTheme,
  }
}
