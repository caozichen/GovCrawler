import assert from 'node:assert/strict'
import test from 'node:test'
import { createPinia } from 'pinia'
import { useConsoleStore } from './console'

test('batch run marks every submitted task as a batch operation', async (context) => {
  const originalFetch = globalThis.fetch
  const submittedModes: string[] = []
  globalThis.fetch = (async (_input, init) => {
    submittedModes.push(JSON.parse(String(init?.body)).mode as string)
    return new Response(JSON.stringify({ ok: true, data: {} }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch
  context.after(() => {
    globalThis.fetch = originalFetch
  })

  const store = useConsoleStore(createPinia())
  let refreshCount = 0
  store.refresh = async () => {
    refreshCount += 1
  }

  const results = await store.runTasks(['task-1', 'task-2'])

  assert.equal(results?.length, 2)
  assert.deepEqual(submittedModes, ['batch', 'batch'])
  assert.equal(refreshCount, 1)
})

test('batch stop settles every request and refreshes state once', async (context) => {
  const originalFetch = globalThis.fetch
  const requestedUrls: string[] = []
  globalThis.fetch = (async (input) => {
    const url = String(input)
    requestedUrls.push(url)
    if (url.includes('/run-2/')) {
      return new Response(JSON.stringify({
        ok: false,
        error: { code: 'RUN_NOT_ACTIVE', message: '这个运行记录已经结束。' },
      }), { status: 409, headers: { 'Content-Type': 'application/json' } })
    }
    return new Response(JSON.stringify({ ok: true, data: null }), {
      status: 202,
      headers: { 'Content-Type': 'application/json' },
    })
  }) as typeof fetch
  context.after(() => {
    globalThis.fetch = originalFetch
  })

  const store = useConsoleStore(createPinia())
  let refreshCount = 0
  store.refresh = async () => {
    refreshCount += 1
  }

  const results = await store.stopRuns(['run-1', 'run-2', 'run-3'])

  assert.equal(results?.length, 3)
  assert.equal(results?.filter((result) => result.status === 'fulfilled').length, 2)
  assert.deepEqual(requestedUrls, ['/api/runs/run-1/stop', '/api/runs/run-2/stop', '/api/runs/run-3/stop'])
  assert.equal(refreshCount, 1)
  assert.equal(store.stopSubmissionBusy, false)
})
