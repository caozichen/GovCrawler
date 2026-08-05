import { createRouter, createWebHistory } from 'vue-router'
import LoginView from './views/LoginView.vue'
import TasksView from './views/TasksView.vue'
import RunsView from './views/RunsView.vue'
import SettingsView from './views/SettingsView.vue'
import AppShell from './components/AppShell.vue'
import { useAuthStore } from './stores/auth'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView },
    {
      path: '/',
      component: AppShell,
      meta: { requiresAuth: true },
      children: [
        { path: '', name: 'tasks', component: TasksView },
        { path: 'runs', name: 'runs', component: RunsView },
        { path: 'settings', name: 'settings', component: SettingsView },
      ],
    },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth && !auth.user) return { name: 'login' }
  if (to.name === 'login' && auth.user) return { name: 'tasks' }
  return true
})
