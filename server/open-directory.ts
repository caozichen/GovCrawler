import { spawn, type ChildProcess, type SpawnOptions } from 'node:child_process'
import { stat } from 'node:fs/promises'
import { AppError } from './errors.js'

export type SpawnProcess = (
  command: string,
  args: readonly string[],
  options: SpawnOptions,
) => ChildProcess

interface OpenDirectoryOptions {
  platform?: NodeJS.Platform
  spawnProcess?: SpawnProcess
}

export async function openOutputDirectory(
  targetPath: string,
  options: OpenDirectoryOptions = {},
): Promise<void> {
  const platform = options.platform ?? process.platform
  if (platform !== 'win32') {
    throw new AppError(501, 'OPEN_DIRECTORY_UNSUPPORTED', '当前系统暂不支持打开目录。')
  }

  let targetStats
  try {
    targetStats = await stat(targetPath)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      throw new AppError(404, 'OUTPUT_DIRECTORY_NOT_FOUND', '运行结果目录已不存在。')
    }
    throw error
  }

  if (!targetStats.isDirectory()) {
    throw new AppError(422, 'OUTPUT_PATH_IS_NOT_DIRECTORY', '运行结果路径不是目录。')
  }

  const spawnProcess = options.spawnProcess ?? spawn
  await new Promise<void>((resolve, reject) => {
    let child: ChildProcess
    try {
      child = spawnProcess('explorer.exe', [targetPath], {
        shell: false,
        windowsHide: false,
        detached: true,
        stdio: 'ignore',
      })
    } catch (error) {
      reject(openFailed(error))
      return
    }

    let settled = false
    child.once('error', (error) => {
      if (settled) return
      settled = true
      reject(openFailed(error))
    })
    child.once('spawn', () => {
      if (settled) return
      settled = true
      child.unref()
      resolve()
    })
  })
}

function openFailed(error: unknown): AppError {
  const code = (error as NodeJS.ErrnoException).code
  return new AppError(500, 'OPEN_DIRECTORY_FAILED', '无法打开运行结果目录。', code ? { cause: code } : undefined)
}
