import { randomUUID } from 'node:crypto'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import express, { type NextFunction, type Request, type Response } from 'express'
import { ZodError } from 'zod'
import { AuthService } from './auth.js'
import { AppError } from './errors.js'
import { openOutputDirectory } from './open-directory.js'
import { pickLocalPath } from './path-picker.js'
import { RunManager } from './runner.js'
import { SchedulerService } from './scheduler.js'
import { StateStore } from './store.js'
import type { CrawlerTask, TaskInput } from './types.js'
import { pathExists } from './utils.js'
import { loginSchema, pathPickerSchema, settingsSchema, taskInputSchema } from './validation.js'

const moduleDirectory = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(process.env.PA_PROJECT_ROOT || path.join(moduleDirectory, '..'))
const port = Number(process.env.PA_PORT || 4318)
const host = '127.0.0.1'

const store = new StateStore(projectRoot)
await store.init()
const auth = new AuthService()
const runner = new RunManager(projectRoot, store)
const scheduler = new SchedulerService(store, runner)
const app = express()

type AsyncHandler = (request: Request, response: Response, next: NextFunction) => Promise<void>
const asyncRoute = (handler: AsyncHandler) => (request: Request, response: Response, next: NextFunction) => {
  void handler(request, response, next).catch(next)
}

app.disable('x-powered-by')
app.use(express.json({ limit: '128kb' }))
app.use((request, response, next) => {
  const requestId = randomUUID()
  response.locals.requestId = requestId
  response.setHeader('X-Request-Id', requestId)

  const hostname = request.hostname.replace(/^\[|\]$/g, '')
  if (!['127.0.0.1', 'localhost', '::1'].includes(hostname)) {
    next(new AppError(403, 'LOCAL_ACCESS_ONLY', '控制服务只接受本机访问。'))
    return
  }

  const origin = request.headers.origin
  if (origin) {
    try {
      const originHost = new URL(origin).hostname
      if (!['127.0.0.1', 'localhost', '::1'].includes(originHost)) {
        next(new AppError(403, 'INVALID_ORIGIN', '请求来源不被允许。'))
        return
      }
    } catch {
      next(new AppError(403, 'INVALID_ORIGIN', '请求来源不被允许。'))
      return
    }
  }
  next()
})

app.get('/api/health', (_request, response) => {
  response.json({ ok: true, data: { status: 'ready', version: '0.1.0' } })
})

app.post('/api/auth/login', (request, response, next) => {
  try {
    const credentials = loginSchema.parse(request.body)
    const token = auth.login(credentials.username, credentials.password)
    auth.setSessionCookie(response, token)
    response.json({ ok: true, data: { username: credentials.username } })
  } catch (error) {
    next(error)
  }
})

app.post('/api/auth/logout', (request, response) => {
  auth.clearSession(request, response)
  response.json({ ok: true, data: null })
})

app.get('/api/auth/me', auth.requireSession, (request, response) => {
  response.json({ ok: true, data: auth.currentUser(request) })
})

app.use('/api', auth.requireSession)

app.post(
  '/api/system/pick-path',
  asyncRoute(async (request, response) => {
    const input = pathPickerSchema.parse(request.body)
    const selectedPath = await pickLocalPath(input.kind, input.initialPath)
    response.json({ ok: true, data: { path: selectedPath } })
  }),
)

app.get('/api/dashboard', (_request, response) => {
  const state = store.snapshot()
  const startOfToday = new Date()
  startOfToday.setHours(0, 0, 0, 0)
  const todayRuns = state.runs.filter((run) => new Date(run.queuedAt).getTime() >= startOfToday.getTime())
  response.json({
    ok: true,
    data: {
      taskCount: state.tasks.length,
      enabledCount: state.tasks.filter((task) => task.enabled).length,
      activeCount: state.runs.filter((run) => ['queued', 'running', 'stopping'].includes(run.status)).length,
      successToday: todayRuns.filter((run) => run.status === 'succeeded').length,
      failedToday: todayRuns.filter((run) => ['failed', 'timed_out', 'interrupted'].includes(run.status)).length,
      scheduler: state.scheduler,
    },
  })
})

app.get('/api/tasks', (_request, response) => {
  response.json({ ok: true, data: store.snapshot().tasks })
})

app.post(
  '/api/tasks',
  asyncRoute(async (request, response) => {
    const input = taskInputSchema.parse(request.body) as TaskInput
    const now = new Date().toISOString()
    const task: CrawlerTask = {
      ...input,
      pythonPath: input.pythonPath || '',
      extraArgs: input.extraArgs || [],
      id: randomUUID(),
      createdAt: now,
      updatedAt: now,
    }
    await store.update((draft) => {
      draft.tasks.push(task)
    })
    await scheduler.refreshTask(task.id)
    response.status(201).json({ ok: true, data: task })
  }),
)

app.patch(
  '/api/tasks/:id',
  asyncRoute(async (request, response) => {
    const input = taskInputSchema.parse(request.body) as TaskInput
    let updated = false
    await store.update((draft) => {
      const task = draft.tasks.find((item) => item.id === request.params.id)
      if (!task) return
      Object.assign(task, input, { extraArgs: input.extraArgs || [], updatedAt: new Date().toISOString() })
      updated = true
    })
    if (!updated) throw new AppError(404, 'TASK_NOT_FOUND', '没有找到这个任务。')
    await scheduler.refreshTask(request.params.id)
    response.json({ ok: true, data: store.snapshot().tasks.find((item) => item.id === request.params.id) })
  }),
)

app.delete(
  '/api/tasks/:id',
  asyncRoute(async (request, response) => {
    if (runner.isTaskBusy(request.params.id)) {
      throw new AppError(409, 'TASK_IS_ACTIVE', '任务运行中，不能删除。')
    }
    let removed = false
    await store.update((draft) => {
      const index = draft.tasks.findIndex((item) => item.id === request.params.id)
      if (index >= 0) {
        draft.tasks.splice(index, 1)
        removed = true
      }
    })
    if (!removed) throw new AppError(404, 'TASK_NOT_FOUND', '没有找到这个任务。')
    await scheduler.refreshTask(request.params.id)
    response.json({ ok: true, data: null })
  }),
)

app.post(
  '/api/tasks/validate',
  asyncRoute(async (request, response) => {
    const input = taskInputSchema.parse(request.body) as TaskInput
    const state = store.snapshot()
    const scriptPath = path.isAbsolute(input.scriptPath) ? input.scriptPath : path.resolve(projectRoot, input.scriptPath)
    const checks = {
      script: path.extname(scriptPath).toLowerCase() === '.py' && (await pathExists(scriptPath)),
      output: await ensureDirectory(state.settings.outputRoot),
      python: await checkPython(input.pythonPath || state.settings.pythonPath),
    }
    response.json({ ok: true, data: { valid: Object.values(checks).every(Boolean), checks } })
  }),
)

app.post(
  '/api/tasks/:id/validate',
  asyncRoute(async (request, response) => {
    const state = store.snapshot()
    const task = state.tasks.find((item) => item.id === request.params.id)
    if (!task) throw new AppError(404, 'TASK_NOT_FOUND', '没有找到这个任务。')
    const scriptPath = path.isAbsolute(task.scriptPath) ? task.scriptPath : path.resolve(projectRoot, task.scriptPath)
    const python = task.pythonPath || state.settings.pythonPath
    const checks = {
      script: path.extname(scriptPath).toLowerCase() === '.py' && (await pathExists(scriptPath)),
      output: await ensureDirectory(state.settings.outputRoot),
      python: await checkPython(python),
    }
    response.json({ ok: true, data: { valid: Object.values(checks).every(Boolean), checks } })
  }),
)

app.post(
  '/api/tasks/:id/run',
  asyncRoute(async (request, response) => {
    const run = await runner.enqueue(request.params.id, 'manual')
    response.status(202).json({ ok: true, data: run })
  }),
)

app.post(
  '/api/tasks/run-all',
  asyncRoute(async (_request, response) => {
    const tasks = store.snapshot().tasks.filter((task) => task.enabled)
    const accepted: string[] = []
    const skipped: string[] = []
    for (const task of tasks) {
      try {
        accepted.push((await runner.enqueue(task.id, 'run-all')).id)
      } catch (error) {
        if (error instanceof AppError && error.status === 409) skipped.push(task.id)
        else throw error
      }
    }
    response.status(202).json({ ok: true, data: { accepted, skipped } })
  }),
)

app.get('/api/runs', (_request, response) => {
  response.json({ ok: true, data: store.snapshot().runs })
})

app.get('/api/runs/:id', (request, response, next) => {
  const run = store.snapshot().runs.find((item) => item.id === request.params.id)
  if (!run) {
    next(new AppError(404, 'RUN_NOT_FOUND', '没有找到这条运行记录。'))
    return
  }
  response.json({ ok: true, data: run })
})

app.get(
  '/api/runs/:id/logs',
  asyncRoute(async (request, response) => {
    const stream = request.query.stream === 'stderr' ? 'stderr' : 'stdout'
    response.json({ ok: true, data: { stream, text: await runner.readLog(request.params.id, stream) } })
  }),
)

app.post(
  '/api/runs/:id/stop',
  asyncRoute(async (request, response) => {
    await runner.stop(request.params.id)
    response.status(202).json({ ok: true, data: null })
  }),
)

app.post(
  '/api/runs/:id/open',
  asyncRoute(async (request, response) => {
    const run = store.snapshot().runs.find((item) => item.id === request.params.id)
    if (!run) throw new AppError(404, 'RUN_NOT_FOUND', '没有找到这条运行记录。')

    await openOutputDirectory(run.outputDir)
    response.json({ ok: true, data: null })
  }),
)

app.get('/api/scheduler', (_request, response) => {
  response.json({ ok: true, data: store.snapshot().scheduler })
})

app.post(
  '/api/scheduler/start',
  asyncRoute(async (_request, response) => {
    await scheduler.start()
    response.json({ ok: true, data: store.snapshot().scheduler })
  }),
)

app.post(
  '/api/scheduler/stop',
  asyncRoute(async (_request, response) => {
    await scheduler.stop()
    response.json({ ok: true, data: store.snapshot().scheduler })
  }),
)

app.get('/api/settings', (_request, response) => {
  response.json({ ok: true, data: store.snapshot().settings })
})

app.put(
  '/api/settings',
  asyncRoute(async (request, response) => {
    const settings = settingsSchema.parse(request.body)
    settings.outputRoot = path.resolve(settings.outputRoot)
    await store.update((draft) => {
      draft.settings = settings
    })
    response.json({ ok: true, data: settings })
  }),
)

app.post(
  '/api/settings/validate',
  asyncRoute(async (request, response) => {
    const settings = settingsSchema.parse(request.body)
    const checks = {
      python: await checkPython(settings.pythonPath),
      output: await ensureDirectory(settings.outputRoot),
    }
    response.json({ ok: true, data: { valid: Object.values(checks).every(Boolean), checks } })
  }),
)

const distPath = path.join(projectRoot, 'dist')
if (await pathExists(distPath)) {
  app.use(express.static(distPath))
  app.get('*', (_request, response) => response.sendFile(path.join(distPath, 'index.html')))
}

app.use((error: unknown, _request: Request, response: Response, _next: NextFunction) => {
  const requestId = String(response.locals.requestId || '')
  if (error instanceof ZodError) {
    response.status(422).json({
      ok: false,
      error: { code: 'VALIDATION_ERROR', message: '提交的数据不完整或格式不正确。', details: error.issues, requestId },
    })
    return
  }
  if (error instanceof AppError) {
    response.status(error.status).json({
      ok: false,
      error: { code: error.code, message: error.message, details: error.details, requestId },
    })
    return
  }
  console.error(`[${requestId}]`, error)
  response.status(500).json({
    ok: false,
    error: { code: 'INTERNAL_ERROR', message: '本地控制程序发生异常。', requestId },
  })
})

const server = app.listen(port, host, () => {
  console.log(`PA Crawler Console API: http://${host}:${port}`)
  console.log(`Project root: ${projectRoot}`)
})

let shuttingDown = false
async function shutdown(signal: string): Promise<void> {
  if (shuttingDown) return
  shuttingDown = true
  console.log(`Received ${signal}; stopping scheduler and crawler processes...`)
  const deadline = setTimeout(() => process.exit(1), 12_000)

  try {
    await scheduler.stop()
  } catch (error) {
    console.error('Scheduler shutdown failed', error)
  }
  try {
    await runner.stopAll()
  } catch (error) {
    console.error('Crawler process shutdown failed', error)
  }

  server.close(() => {
    clearTimeout(deadline)
    process.exit(0)
  })
}

process.once('SIGINT', () => void shutdown('SIGINT'))
process.once('SIGTERM', () => void shutdown('SIGTERM'))

async function ensureDirectory(directory: string): Promise<boolean> {
  try {
    await mkdir(path.resolve(directory), { recursive: true })
    return true
  } catch {
    return false
  }
}

async function checkPython(command: string): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false
    const child = spawn(command, ['--version'], { shell: false, windowsHide: true, stdio: 'ignore' })
    const finish = (result: boolean) => {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      resolve(result)
    }
    const timeout = setTimeout(() => {
      child.kill()
      finish(false)
    }, 3000)
    child.once('error', () => finish(false))
    child.once('close', (code) => finish(code === 0))
  })
}
