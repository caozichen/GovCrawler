import assert from 'node:assert/strict'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { AppError } from './errors.js'
import { pickLocalPath, type ExecutePicker } from './path-picker.js'

test('returns a validated Python script selected by the native picker', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-path-picker-'))
  const scriptPath = path.join(root, 'crawler.py')
  await writeFile(scriptPath, 'print("ok")', 'utf8')
  let invocation: { command: string; args: readonly string[]; initialPath?: string } | undefined
  const executePicker: ExecutePicker = async (command, args, options) => {
    invocation = { command, args, initialPath: options.env.PA_PICKER_INITIAL_PATH }
    return { stdout: scriptPath, stderr: '' }
  }

  try {
    const result = await pickLocalPath('python-script', root, {
      platform: 'win32',
      executePicker,
    })

    assert.equal(result, scriptPath)
    assert.equal(invocation?.command, 'powershell.exe')
    assert.equal(invocation?.initialPath, root)
    assert.ok(invocation?.args.includes('-STA'))
    assert.ok(invocation?.args.includes('-WindowStyle'))
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test('returns null when the native picker is cancelled', async () => {
  const executePicker: ExecutePicker = async () => ({ stdout: '', stderr: '' })

  const result = await pickLocalPath('directory', '', {
    platform: 'win32',
    executePicker,
  })

  assert.equal(result, null)
})

test('rejects a non-Python file for the crawler script field', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'pa-path-picker-invalid-'))
  const textPath = path.join(root, 'crawler.txt')
  await writeFile(textPath, 'not Python', 'utf8')
  const executePicker: ExecutePicker = async () => ({ stdout: textPath, stderr: '' })

  try {
    await assert.rejects(
      pickLocalPath('python-script', '', { platform: 'win32', executePicker }),
      (error: unknown) => error instanceof AppError && error.code === 'SELECTED_PATH_NOT_PYTHON_SCRIPT',
    )
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})
