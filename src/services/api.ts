import type { AppSettings, CrawlerTask, DashboardSummary, PathPickerKind, RunRecord, TaskPayload, ValidationResult } from '../types'

interface ApiEnvelope<T> {
  ok: true
  data: T
}

interface ApiFailure {
  ok: false
  error: {
    code: string
    message: string
    details?: unknown
    requestId?: string
  }
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly details?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const responseText = await response.text()
  let payload: ApiEnvelope<T> | ApiFailure
  try {
    payload = JSON.parse(responseText) as ApiEnvelope<T> | ApiFailure
  } catch {
    if (response.status === 401 && url !== '/api/auth/login') {
      window.dispatchEvent(new Event('pa:auth-required'))
    }
    throw new ApiError(response.status, 'INVALID_API_RESPONSE', '本地控制服务返回了无法识别的响应。')
  }
  if (!response.ok || !payload.ok) {
    const failure = payload as ApiFailure
    if (response.status === 401 && url !== '/api/auth/login') {
      window.dispatchEvent(new Event('pa:auth-required'))
    }
    throw new ApiError(response.status, failure.error.code, failure.error.message, failure.error.details)
  }
  return payload.data
}

export const api = {
  login: (username: string, password: string) =>
    request<{ username: string }>('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => request<null>('/api/auth/logout', { method: 'POST' }),
  me: () => request<{ username: string }>('/api/auth/me'),
  pickPath: (kind: PathPickerKind, initialPath = '') =>
    request<{ path: string | null }>('/api/system/pick-path', {
      method: 'POST',
      body: JSON.stringify({ kind, initialPath }),
    }),
  dashboard: () => request<DashboardSummary>('/api/dashboard'),
  tasks: () => request<CrawlerTask[]>('/api/tasks'),
  createTask: (payload: TaskPayload) =>
    request<CrawlerTask>('/api/tasks', { method: 'POST', body: JSON.stringify(payload) }),
  updateTask: (id: string, payload: TaskPayload) =>
    request<CrawlerTask>(`/api/tasks/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  updateTaskDetails: (id: string, details: string) =>
    request<CrawlerTask>(`/api/tasks/${id}/details`, { method: 'PATCH', body: JSON.stringify({ details }) }),
  deleteTask: (id: string) => request<null>(`/api/tasks/${id}`, { method: 'DELETE' }),
  validateTask: (id: string) => request<ValidationResult>(`/api/tasks/${id}/validate`, { method: 'POST' }),
  validateTaskDraft: (payload: TaskPayload) =>
    request<ValidationResult>('/api/tasks/validate', { method: 'POST', body: JSON.stringify(payload) }),
  openTaskScript: (id: string) => request<null>(`/api/tasks/${id}/open-script`, { method: 'POST' }),
  runTask: (id: string) => request<RunRecord>(`/api/tasks/${id}/run`, { method: 'POST' }),
  runAll: () => request<{ accepted: string[]; skipped: string[] }>('/api/tasks/run-all', { method: 'POST' }),
  runs: () => request<RunRecord[]>('/api/runs'),
  log: (id: string, stream: 'stdout' | 'stderr') =>
    request<{ stream: string; text: string }>(`/api/runs/${id}/logs?stream=${stream}`),
  stopRun: (id: string) => request<null>(`/api/runs/${id}/stop`, { method: 'POST' }),
  openRun: (id: string) => request<null>(`/api/runs/${id}/open`, { method: 'POST' }),
  startScheduler: () => request<{ running: boolean }>('/api/scheduler/start', { method: 'POST' }),
  stopScheduler: () => request<{ running: boolean }>('/api/scheduler/stop', { method: 'POST' }),
  settings: () => request<AppSettings>('/api/settings'),
  saveSettings: (payload: AppSettings) =>
    request<AppSettings>('/api/settings', { method: 'PUT', body: JSON.stringify(payload) }),
  validateSettings: (payload: AppSettings) =>
    request<ValidationResult>('/api/settings/validate', { method: 'POST', body: JSON.stringify(payload) }),
}
