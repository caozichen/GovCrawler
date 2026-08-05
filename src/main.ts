import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { initializeTheme } from './composables/theme'
import { router } from './router'
import { useAuthStore } from './stores/auth'
import './styles.css'

initializeTheme()

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)

const auth = useAuthStore(pinia)
await auth.restore()
window.addEventListener('pa:auth-required', () => {
  auth.user = null
  if (router.currentRoute.value.name !== 'login') void router.replace({ name: 'login' })
})

app.use(router)
app.mount('#app')
