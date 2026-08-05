import { access, mkdir } from 'node:fs/promises'
import path from 'node:path'

const pad = (value: number, size = 2) => String(value).padStart(size, '0')

export function formatRunDirectoryName(date: Date): string {
  return [
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    `${pad(date.getHours())}-${pad(date.getMinutes())}-${pad(date.getSeconds())}-${pad(date.getMilliseconds(), 3)}`,
  ].join('_')
}

export async function allocateRunDirectory(outputRoot: string, now = new Date()): Promise<string> {
  const root = path.resolve(outputRoot)
  await mkdir(root, { recursive: true })

  for (let offset = 0; offset < 1000; offset += 1) {
    const candidate = path.resolve(root, formatRunDirectoryName(new Date(now.getTime() + offset)))
    const relative = path.relative(root, candidate)
    if (relative.startsWith('..') || path.isAbsolute(relative)) {
      throw new Error('Generated run directory escaped the configured output root')
    }

    try {
      await mkdir(candidate, { recursive: false })
      return candidate
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code
      if (code !== 'EEXIST') throw error
    }
  }

  throw new Error('Could not allocate a unique run directory')
}

export async function pathExists(targetPath: string): Promise<boolean> {
  try {
    await access(targetPath)
    return true
  } catch {
    return false
  }
}

export function durationLabel(milliseconds?: number): string {
  if (milliseconds === undefined) return '--'
  if (milliseconds < 1000) return `${milliseconds} ms`
  return `${(milliseconds / 1000).toFixed(1)} s`
}
