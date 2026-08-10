import { z } from 'zod'

export const loginSchema = z.object({
  username: z.string().min(1),
  password: z.string().min(1),
})

export const pathPickerSchema = z.object({
  kind: z.enum(['python-script', 'python-executable', 'directory']),
  initialPath: z.string().max(2048).optional().default(''),
})

export const taskInputSchema = z.object({
  categoryId: z.string().trim().min(1, '请选择任务分类'),
  name: z.string().trim().min(1, '请输入任务名称').max(80),
  url: z.string().url('请输入有效网址').refine((value) => /^https?:\/\//i.test(value), '仅支持 HTTP/HTTPS'),
  scriptPath: z.string().trim().min(1, '请选择 Python 爬虫文件'),
  pythonPath: z.string().trim().optional().default(''),
  enabled: z.boolean(),
  intervalMinutes: z.number().int().min(1).max(525600),
  timeoutSeconds: z.number().int().min(5).max(86400),
  extraArgs: z
    .array(z.string().max(500))
    .max(30)
    .refine(
      (items) => !items.some((item) => ['--url', '--output-dir', '--run-id'].some((name) => item === name || item.startsWith(`${name}=`))),
      '额外参数不能覆盖系统保留参数',
    )
    .optional()
    .default([]),
})

export const categoryInputSchema = z.object({
  name: z.string().trim().min(1, '请输入分类名称').max(20, '分类名称不能超过 20 个字'),
  color: z.string().regex(/^#[0-9a-f]{6}$/i, '请选择有效的分类颜色'),
})

export const taskDetailsSchema = z.object({
  details: z.string().trim().max(500, '脚本详情不能超过 500 字'),
})

export const runTaskSchema = z.object({
  mode: z.enum(['single', 'batch']).optional().default('single'),
})

export const settingsSchema = z.object({
  pythonPath: z.string().trim().min(1),
  outputRoot: z.string().trim().min(1),
  maxConcurrency: z.number().int().min(1).max(8),
  defaultTimeoutSeconds: z.number().int().min(5).max(86400),
  retentionDays: z.number().int().min(1).max(3650),
})
