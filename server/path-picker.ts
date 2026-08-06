import { execFile } from 'node:child_process'
import { stat } from 'node:fs/promises'
import path from 'node:path'
import { AppError } from './errors.js'

export type PathPickerKind = 'python-script' | 'python-executable' | 'directory'

export interface PickerExecutionOptions {
  encoding: 'utf8'
  env: NodeJS.ProcessEnv
  maxBuffer: number
  windowsHide: boolean
}

export type ExecutePicker = (
  command: string,
  args: readonly string[],
  options: PickerExecutionOptions,
) => Promise<{ stdout: string; stderr: string }>

interface PathPickerOptions {
  platform?: NodeJS.Platform
  executePicker?: ExecutePicker
}

let pickerActive = false

export async function pickLocalPath(
  kind: PathPickerKind,
  initialPath = '',
  options: PathPickerOptions = {},
): Promise<string | null> {
  const platform = options.platform ?? process.platform
  if (!['win32', 'darwin'].includes(platform)) {
    throw new AppError(501, 'PATH_PICKER_UNSUPPORTED', '当前系统暂不支持路径选择器。')
  }
  if (pickerActive) {
    throw new AppError(409, 'PATH_PICKER_ACTIVE', '已有路径选择窗口，请先完成或关闭。')
  }

  pickerActive = true
  try {
    const executePicker = options.executePicker ?? executePowerShellPicker
    const command = platform === 'darwin' ? 'osascript' : 'powershell.exe'
    const args = platform === 'darwin'
      ? ['-e', buildMacPickerScript(kind)]
      : ['-NoProfile', '-STA', '-WindowStyle', 'Hidden', '-Command', buildPickerScript(kind)]
    const pickerInitialPath = platform === 'darwin' ? await resolvePickerInitialPath(initialPath) : initialPath
    const { stdout } = await executePicker(
      command,
      args,
      {
        encoding: 'utf8',
        env: { ...process.env, PA_PICKER_INITIAL_PATH: pickerInitialPath },
        maxBuffer: 1024 * 1024,
        windowsHide: true,
      },
    )
    const selectedPath = stdout.trim()
    if (!selectedPath) return null
    if (!path.isAbsolute(selectedPath)) {
      throw new AppError(422, 'SELECTED_PATH_NOT_ABSOLUTE', '选择器返回的不是绝对路径。')
    }

    const normalizedPath = path.normalize(selectedPath)
    let selectedStats
    try {
      selectedStats = await stat(normalizedPath)
    } catch {
      throw new AppError(404, 'SELECTED_PATH_NOT_FOUND', '选择的文件或目录已经不存在。')
    }

    if (kind === 'directory' && !selectedStats.isDirectory()) {
      throw new AppError(422, 'SELECTED_PATH_NOT_DIRECTORY', '请选择文件夹。')
    }
    if (kind !== 'directory' && !selectedStats.isFile()) {
      throw new AppError(422, 'SELECTED_PATH_NOT_FILE', '请选择文件。')
    }
    if (kind === 'python-script' && path.extname(normalizedPath).toLowerCase() !== '.py') {
      throw new AppError(422, 'SELECTED_PATH_NOT_PYTHON_SCRIPT', '请选择 .py 爬虫文件。')
    }
    if (kind === 'python-executable' && platform === 'win32' && path.extname(normalizedPath).toLowerCase() !== '.exe') {
      throw new AppError(422, 'SELECTED_PATH_NOT_EXECUTABLE', '请选择 Python 可执行文件。')
    }

    return normalizedPath
  } catch (error) {
    if (error instanceof AppError) throw error
    const code = (error as NodeJS.ErrnoException).code
    throw new AppError(
      500,
      'PATH_PICKER_FAILED',
      '无法打开本机路径选择器。',
      code ? { cause: code } : undefined,
    )
  } finally {
    pickerActive = false
  }
}

async function resolvePickerInitialPath(initialPath: string): Promise<string> {
  if (!initialPath || !path.isAbsolute(initialPath)) return ''
  const normalizedPath = path.normalize(initialPath)
  try {
    const initialStats = await stat(normalizedPath)
    return initialStats.isDirectory() ? normalizedPath : path.dirname(normalizedPath)
  } catch {
    return ''
  }
}

function executePowerShellPicker(
  command: string,
  args: readonly string[],
  options: PickerExecutionOptions,
): Promise<{ stdout: string; stderr: string }> {
  return new Promise((resolve, reject) => {
    execFile(command, [...args], options, (error, stdout, stderr) => {
      if (error) {
        reject(error)
        return
      }
      resolve({ stdout, stderr })
    })
  })
}

function buildPickerScript(kind: PathPickerKind): string {
  const common = [
    '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8',
    'Add-Type -AssemblyName System.Windows.Forms',
    '$initial = $env:PA_PICKER_INITIAL_PATH',
    '$owner = New-Object System.Windows.Forms.Form',
    '$owner.TopMost = $true',
    '$owner.ShowInTaskbar = $false',
    '$owner.WindowState = [System.Windows.Forms.FormWindowState]::Minimized',
  ]

  if (kind === 'directory') {
    return [
      ...common,
      '$dialog = New-Object System.Windows.Forms.FolderBrowserDialog',
      "$dialog.Description = '选择输出目录'",
      '$dialog.ShowNewFolderButton = $true',
      "if ($initial -and (Test-Path -LiteralPath $initial)) { $item = Get-Item -LiteralPath $initial; if ($item.PSIsContainer) { $dialog.SelectedPath = $item.FullName } else { $dialog.SelectedPath = $item.DirectoryName } }",
      'try { $result = $dialog.ShowDialog($owner); if ($result -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.SelectedPath) } } finally { $dialog.Dispose(); $owner.Dispose() }',
    ].join('; ')
  }

  const filter = kind === 'python-script'
    ? 'Python files (*.py)|*.py|All files (*.*)|*.*'
    : 'Executable files (*.exe)|*.exe|All files (*.*)|*.*'
  const title = kind === 'python-script' ? '选择 Python 爬虫文件' : '选择 Python 解释器'
  return [
    ...common,
    '$dialog = New-Object System.Windows.Forms.OpenFileDialog',
    `$dialog.Title = '${title}'`,
    `$dialog.Filter = '${filter}'`,
    '$dialog.CheckFileExists = $true',
    '$dialog.Multiselect = $false',
    "if ($initial -and (Test-Path -LiteralPath $initial)) { $item = Get-Item -LiteralPath $initial; if ($item.PSIsContainer) { $dialog.InitialDirectory = $item.FullName } else { $dialog.InitialDirectory = $item.DirectoryName; $dialog.FileName = $item.Name } }",
    'try { $result = $dialog.ShowDialog($owner); if ($result -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.FileName) } } finally { $dialog.Dispose(); $owner.Dispose() }',
  ].join('; ')
}

function buildMacPickerScript(kind: PathPickerKind): string {
  const prompt = kind === 'directory'
    ? '选择输出目录'
    : kind === 'python-script'
      ? '选择 Python 爬虫文件'
      : '选择 Python 解释器'
  const chooser = kind === 'directory' ? 'choose folder' : 'choose file'
  return [
    'set initialPath to system attribute "PA_PICKER_INITIAL_PATH"',
    'try',
    `if initialPath is "" then`,
    `set selectedPath to ${chooser} with prompt "${prompt}"`,
    'else',
    `set selectedPath to ${chooser} with prompt "${prompt}" default location (POSIX file initialPath as alias)`,
    'end if',
    'return POSIX path of selectedPath',
    'on error errorMessage number errorNumber',
    'if errorNumber is -128 then return ""',
    'error errorMessage number errorNumber',
    'end try',
  ].join('\n')
}
