<script setup lang="ts">
import { ref } from 'vue'
import { ElInput } from 'element-plus'
import { useRouter } from 'vue-router'
import { Bot, LockKeyhole, LogIn, ShieldCheck, UserRound } from '@lucide/vue'
import { ApiError } from '../services/api'
import { useAuthStore } from '../stores/auth'
import ThemeToggle from '../components/ThemeToggle.vue'

const auth = useAuthStore()
const router = useRouter()
const username = ref('admin')
const password = ref('')
const errorMessage = ref('')

async function submit() {
  errorMessage.value = ''
  try {
    await auth.login(username.value.trim(), password.value)
    await router.replace({ name: 'tasks' })
  } catch (error) {
    errorMessage.value = error instanceof ApiError ? error.message : '无法连接本地控制服务。'
  }
}
</script>

<template>
  <main class="login-page">
    <ThemeToggle class="login-theme-toggle" />
    <div class="login-scanline" />
    <section class="login-panel" aria-labelledby="login-title">
      <div class="login-brand">
        <div class="brand-mark brand-mark--large"><Bot :size="27" /></div>
        <div>
          <span class="eyebrow">LOCAL OPERATIONS</span>
          <strong>PA Crawler</strong>
        </div>
      </div>

      <div class="login-heading">
        <div class="login-heading__status"><ShieldCheck :size="15" /> LOCAL ACCESS</div>
        <h1 id="login-title">登录控制台</h1>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <label class="field">
          <span>用户名</span>
          <ElInput v-model="username" name="username" autocomplete="username" placeholder="用户名" required clearable>
            <template #prefix><UserRound :size="18" /></template>
          </ElInput>
        </label>
        <label class="field">
          <span>密码</span>
          <ElInput
            v-model="password"
            name="password"
            type="password"
            autocomplete="current-password"
            placeholder="密码"
            required
            show-password
          >
            <template #prefix><LockKeyhole :size="18" /></template>
          </ElInput>
        </label>

        <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>
        <button class="button button--primary login-submit" type="submit" :disabled="auth.loading">
          <LogIn :size="18" />
          {{ auth.loading ? '登录中' : '登录' }}
        </button>
      </form>

      <footer class="login-footer">
        <span><span class="live-dot live-dot--active" /> API READY</span>
        <span>v0.1.0</span>
      </footer>
    </section>
  </main>
</template>
