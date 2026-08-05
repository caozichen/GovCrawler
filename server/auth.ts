import { randomBytes, timingSafeEqual } from 'node:crypto'
import type { NextFunction, Request, Response } from 'express'
import { AppError } from './errors.js'

const COOKIE_NAME = 'pa_session'
const SESSION_TTL_MS = 8 * 60 * 60 * 1000

interface Session {
  username: string
  expiresAt: number
}

function parseCookieHeader(header?: string): Record<string, string> {
  if (!header) return {}
  return Object.fromEntries(
    header.split(';').map((item) => {
      const [key, ...rest] = item.trim().split('=')
      return [key, decodeURIComponent(rest.join('='))]
    }),
  )
}

function matchesSecret(actual: string, expected: string): boolean {
  const actualBuffer = Buffer.from(actual)
  const expectedBuffer = Buffer.from(expected)
  return actualBuffer.length === expectedBuffer.length && timingSafeEqual(actualBuffer, expectedBuffer)
}

export class AuthService {
  private readonly sessions = new Map<string, Session>()
  private readonly username = process.env.PA_ADMIN_USER || 'admin'
  private readonly password = process.env.PA_ADMIN_PASSWORD || 'admin123'

  login(username: string, password: string): string {
    if (!matchesSecret(username, this.username) || !matchesSecret(password, this.password)) {
      throw new AppError(401, 'INVALID_CREDENTIALS', '用户名或密码错误。')
    }

    const token = randomBytes(32).toString('hex')
    this.sessions.set(token, { username: this.username, expiresAt: Date.now() + SESSION_TTL_MS })
    return token
  }

  setSessionCookie(response: Response, token: string): void {
    response.setHeader(
      'Set-Cookie',
      `${COOKIE_NAME}=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${SESSION_TTL_MS / 1000}`,
    )
  }

  clearSession(request: Request, response: Response): void {
    const token = parseCookieHeader(request.headers.cookie)[COOKIE_NAME]
    if (token) this.sessions.delete(token)
    response.setHeader('Set-Cookie', `${COOKIE_NAME}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0`)
  }

  requireSession = (request: Request, _response: Response, next: NextFunction): void => {
    const token = parseCookieHeader(request.headers.cookie)[COOKIE_NAME]
    const session = token ? this.sessions.get(token) : undefined
    if (!token || !session || session.expiresAt <= Date.now()) {
      if (token) this.sessions.delete(token)
      next(new AppError(401, 'AUTH_REQUIRED', '登录已失效，请重新登录。'))
      return
    }
    next()
  }

  currentUser(request: Request): { username: string } {
    const token = parseCookieHeader(request.headers.cookie)[COOKIE_NAME]
    const session = token ? this.sessions.get(token) : undefined
    if (!session) throw new AppError(401, 'AUTH_REQUIRED', '登录已失效，请重新登录。')
    return { username: session.username }
  }
}
