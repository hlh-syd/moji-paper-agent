/**
 * 墨记 FastAPI 数据层：认证、JSON、SSE 流、文件上传。
 * 覆盖后端全部 75 个路由中前端所用的接口（其余为内部/管理端点）。
 */

export interface SessionMsg { role: string; content: string; attachments?: string[] }
export interface AuthUser {
  username: string; role: string
  display_name?: string; avatar?: string   // 资料（MySQL users 表）
  legacy?: boolean
}
export interface Session {
  id: string; name: string; messages: SessionMsg[]; ts?: number
  pinned?: boolean; archived?: boolean; project?: string | null
}
export interface OutlineSection {
  id?: string; title: string; points?: string[]; content?: string
  actual_words?: number; target_words?: number; status?: string
  ai_score?: number
  subsections?: OutlineSection[]
}
export interface Outline {
  paper_title?: string; sections: OutlineSection[]
  total_target_words?: number; domain?: string; template?: string
  [k: string]: unknown
}

const TOKEN_KEY = 'paper_agent_token'
// 启动阶段(boot)标志:401 时不刷新页面而是抛错,让 boot 走 authRequired 显示登录页;
// 运行中会话失效(被踢下线/过期)则刷新重走 boot → 登录页。store.boot 成功后置 true。
;(window as unknown as { __moji_booted?: boolean }).__moji_booted = false
export function markBooted() {
  (window as unknown as { __moji_booted?: boolean }).__moji_booted = true
}
export const AUTH = {
  token: localStorage.getItem(TOKEN_KEY) || '',
  user: null as null | AuthUser,
  set(token: string, user: AuthUser) {
    AUTH.token = token; AUTH.user = user
    localStorage.setItem(TOKEN_KEY, token)
  },
  clear() { AUTH.token = ''; AUTH.user = null; localStorage.removeItem(TOKEN_KEY) },
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra }
  if (AUTH.token) h.Authorization = `Bearer ${AUTH.token}`
  return h
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) { super(message) }
}

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: headers({ ...(init?.body ? { 'Content-Type': 'application/json' } : {}), ...init?.headers as Record<string, string> }),
  })
  if (resp.status === 401) {
    AUTH.clear()
    const w = window as unknown as { __moji_booted?: boolean }
    if (w.__moji_booted !== false) location.reload()   // 运行中失效:重走 boot
    throw new ApiError(401, '未登录')                    // boot 期间:交由 boot 显示登录页
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const j = await resp.json()
      const d = j.detail
      detail = typeof d === 'string'
        ? d
        : Array.isArray(d)
          ? d.map((x: { loc?: unknown[]; msg?: string }) => `${(x.loc ?? []).slice(1).join('.')}: ${x.msg ?? ''}`).join('；')
          : (d ? JSON.stringify(d).slice(0, 200) : JSON.stringify(j).slice(0, 200))
    } catch { /* keep */ }
    throw new ApiError(resp.status, detail)
  }
  return resp.json() as Promise<T>
}

export async function upload<T = unknown>(path: string, file: File, query = ''): Promise<T> {
  const fd = new FormData()
  fd.append('file', file)
  const resp = await fetch(path + query, { method: 'POST', headers: headers(), body: fd })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try { const j = await resp.json(); detail = j.detail || detail } catch { /* keep */ }
    throw new ApiError(resp.status, detail)
  }
  return resp.json() as Promise<T>
}

/** 读取 SSE 流（data: {...}\n\n / data: [DONE]），逐事件回调。 */
export async function sse(path: string, body: unknown, onEvent: (ev: Record<string, unknown>) => void, signal?: AbortSignal): Promise<void> {
  const resp = await fetch(path, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
    signal,
  })
  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`
    try { const j = await resp.json(); detail = j.detail || detail } catch { /* keep */ }
    throw new ApiError(resp.status, detail)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, idx); buf = buf.slice(idx + 2)
      for (const line of raw.split('\n')) {
        if (!line.startsWith('data: ')) continue
        const payload = line.slice(6)
        if (payload === '[DONE]') return
        try { onEvent(JSON.parse(payload)) } catch { /* 跳过坏行 */ }
      }
    }
  }
}

export function download(path: string) {
  const a = document.createElement('a')
  a.href = path + (AUTH.token ? `${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(AUTH.token)}` : '')
  a.download = ''
  a.click()
}

/** 前端生成文本文件直接下载（后端无对应导出路由时使用，如会话导出） */
export function downloadText(name: string, text: string, mime = 'text/markdown;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([text], { type: mime }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}
