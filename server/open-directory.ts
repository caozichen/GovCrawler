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

type OpenTargetKind = 'directory' | 'file'

export async function openOutputDirectory(
  targetPath: string,
  options: OpenDirectoryOptions = {},
): Promise<void> {
  await openLocalPath(targetPath, 'directory', options)
}

export async function revealFileInDirectory(
  targetPath: string,
  options: OpenDirectoryOptions = {},
): Promise<void> {
  await openLocalPath(targetPath, 'file', options)
}

async function openLocalPath(
  targetPath: string,
  kind: OpenTargetKind,
  options: OpenDirectoryOptions,
): Promise<void> {
  const platform = options.platform ?? process.platform
  if (!['win32', 'darwin'].includes(platform)) {
    throw new AppError(501, 'OPEN_PATH_UNSUPPORTED', '当前系统暂不支持打开文件管理器。')
  }

  let targetStats
  try {
    targetStats = await stat(targetPath)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      const code = kind === 'directory' ? 'OUTPUT_DIRECTORY_NOT_FOUND' : 'SCRIPT_FILE_NOT_FOUND'
      const message = kind === 'directory' ? '运行结果目录已不存在。' : '爬虫文件已不存在。'
      throw new AppError(404, code, message)
    }
    throw error
  }

  if (kind === 'directory' && !targetStats.isDirectory()) {
    throw new AppError(422, 'OUTPUT_PATH_IS_NOT_DIRECTORY', '运行结果路径不是目录。')
  }
  if (kind === 'file' && !targetStats.isFile()) {
    throw new AppError(422, 'SCRIPT_PATH_IS_NOT_FILE', '爬虫文件路径不是文件。')
  }

  const invocation = buildOpenInvocation(platform, targetPath, kind)

  const spawnProcess = options.spawnProcess ?? spawn
  await new Promise<void>((resolve, reject) => {
    let child: ChildProcess
    try {
      child = spawnProcess(invocation.command, invocation.args, {
        shell: false,
        windowsHide: false,
        detached: true,
        stdio: 'ignore',
      })
    } catch (error) {
      reject(openFailed(error, kind))
      return
    }

    let settled = false
    child.once('error', (error) => {
      if (settled) return
      settled = true
      reject(openFailed(error, kind))
    })
    child.once('spawn', () => {
      if (settled) return
      settled = true
      child.unref()
      resolve()
    })
  })
}

function buildOpenInvocation(platform: NodeJS.Platform, targetPath: string, kind: OpenTargetKind) {
  if (platform === 'darwin') {
    return { command: 'open', args: kind === 'file' ? ['-R', targetPath] : [targetPath] }
  }
  return { command: 'explorer.exe', args: kind === 'file' ? [`/select,${targetPath}`] : [targetPath] }
}

function openFailed(error: unknown, kind: OpenTargetKind): AppError {
  const code = (error as NodeJS.ErrnoException).code
  const message = kind === 'directory' ? '无法打开运行结果目录。' : '无法在文件管理器中定位爬虫文件。'
  return new AppError(500, 'OPEN_PATH_FAILED', message, code ? { cause: code } : undefined)
}
