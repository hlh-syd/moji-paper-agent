/**
 * 全局状态（手写 store + useSyncExternalStore）。
 * 工作区保存：全量 PUT + rev 乐观锁，409 时整页刷新（后端唯一真源）——与历代前端语义一致。
 */
import { useSyncExternalStore } from 'react'
import { api, AUTH, markBooted, type AuthUser, type Outline, type Session } from './api'

export interface ToastMsg { id: number; text: string; kind: 'ok' | 'err' | 'info' }
export interface KbCollectionInfo { document_count?: number; collection_name?: string }
export interface FulltextStatus {
  active?: boolean; state?: string
  current?: number; total?: number; words?: number
  current_title?: string; log?: string[]
  [k: string]: unknown
}

export interface MojiState {
  booted: boolean
  authRequired: boolean
  user: AuthUser | null
  wsRev: number
  sessions: Session[]
  activeSessionId: string | null
  currentProject: string | null
  outline: Outline | null
  outlineLoading: boolean
  generating: boolean          // 对话流式进行中
  sectionBusy: string | null   // 正在流式重生成/润色的节 id
  paperTab: 'paper' | 'outline' // 右栏视图：论文正文 / 大纲（顶部切换条控制）
  fulltext: FulltextStatus | null
  theme: 'dark' | 'light'
  fontSize: number          // 界面字号 px（12-24，仅文字缩放）
  sidebarCollapsed: boolean
  sidebarWidth: number | null
  rightbarWidth: number | null
  settingsOpen: boolean
  settingsSection: string
  toasts: ToastMsg[]
  kb: Record<string, KbCollectionInfo> | null
  wsState: Record<string, unknown>
  /** 跨组件引用投递：nonce 变化即把 text 追加进对话输入框（自查报告等） */
  quoteToComposer: { text: string; nonce: number } | null
}

let state: MojiState = {
  booted: false,
  authRequired: false,
  user: null,
  wsRev: 0,
  sessions: [],
  activeSessionId: null,
  currentProject: null,
  outline: null,
  outlineLoading: false,
  generating: false,
  sectionBusy: null,
  paperTab: 'paper',
  fulltext: null,
  theme: localStorage.getItem('moji_theme') === 'light' ? 'light' : 'dark',
  fontSize: 16,
  sidebarCollapsed: false,
  sidebarWidth: null,
  rightbarWidth: null,
  settingsOpen: false,
  settingsSection: 'general',
  toasts: [],
  kb: null,
  wsState: {},
  quoteToComposer: null,
}

const listeners = new Set<() => void>()
function emit() { for (const l of listeners) l() }
export function set(patch: Partial<MojiState> | ((s: MojiState) => Partial<MojiState>)) {
  const p = typeof patch === 'function' ? patch(state) : patch
  state = { ...state, ...p }
  emit()
}
export function getState(): MojiState { return state }
function subscribe(l: () => void) { listeners.add(l); return () => { listeners.delete(l) } }
export function useStore<T>(selector: (s: MojiState) => T): T {
  return useSyncExternalStore(subscribe, () => selector(state))
}

let toastSeq = 1
export function toast(text: string, kind: ToastMsg['kind'] = 'info') {
  const id = toastSeq++
  set(s => ({ toasts: [...s.toasts, { id, text, kind }] }))
  setTimeout(() => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })), kind === 'err' ? 6000 : 3200)
}

export function applyTheme(theme: 'dark' | 'light') {
  localStorage.setItem('moji_theme', theme)
  document.body.toggleAttribute('data-ds-dark-theme', theme === 'dark')
  document.documentElement.style.colorScheme = theme
  set({ theme })
  if (getState().booted) scheduleSave()
}

/** 界面字号（12-24px）：按 16px 基准缩放所有 font-size，图标与布局不变 */
export function applyFontSize(size: number) {
  const px = Math.min(24, Math.max(12, Math.round(size || 16)))
  document.documentElement.style.setProperty('--moji-font-scale', String(px / 16))
  document.documentElement.style.setProperty('--dsh-content-font-size', `${px}px`)
  set({ fontSize: px })
  if (getState().booted) scheduleSave()
}

// ---------- 工作区持久化 ----------
let saveTimer: ReturnType<typeof setTimeout> | null = null
export function scheduleSave() {
  if (saveTimer !== null) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => { void saveWorkspaceNow() }, 800)
}

export async function saveWorkspaceNow(keepalive = false) {
  const s = getState()
  const payload = {
    ...s.wsState,
    rev: s.wsRev,
    active_project: s.currentProject,
    active_session: s.activeSessionId,
    sessions: s.sessions.map(x => ({
      id: x.id, name: x.name, messages: x.messages.slice(-60), attachments: [],
      ts: x.ts, pinned: !!x.pinned, pinnedAt: null, archived: !!x.archived, archivedAt: null,
      project: x.project ?? null,
    })),
    theme: s.theme,
    font_size: s.fontSize,
    chat_open: true,
  }
  try {
    const resp = await api<{ ok: boolean; rev: number }>('/api/workspace', {
      method: 'PUT', body: JSON.stringify(payload), keepalive,
    })
    if (resp.ok) set({ wsRev: resp.rev })
  } catch (e: unknown) {
    if ((e as { status?: number }).status === 409) { location.reload(); return }
    console.error('工作区保存失败', e)
  }
}

// ---------- 会话 ----------
export function newSession(): Session {
  const sess: Session = {
    id: `chat_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
    name: '新会话', messages: [], ts: Date.now(), project: getState().currentProject,
  }
  set(s => ({ sessions: [sess, ...s.sessions], activeSessionId: sess.id }))
  scheduleSave()
  return sess
}
export function setActiveSession(id: string) { set({ activeSessionId: id }); scheduleSave() }
export function renameSession(id: string, name: string) {
  set(s => ({ sessions: s.sessions.map(x => x.id === id ? { ...x, name } : x) }))
  scheduleSave()
}
export function removeSession(id: string) {
  set(s => {
    const sessions = s.sessions.filter(x => x.id !== id)
    return { sessions, activeSessionId: s.activeSessionId === id ? (sessions[0]?.id ?? null) : s.activeSessionId }
  })
  scheduleSave()
}
export function currentSession(): Session | null {
  const s = getState()
  return s.sessions.find(x => x.id === s.activeSessionId) ?? null
}
export function appendToSession(id: string, msg: { role: string; content: string; attachments?: string[] }) {
  set(s => ({
    sessions: s.sessions.map(x => x.id === id
      ? { ...x, messages: [...x.messages, msg], name: x.messages.length === 0 && msg.role === 'user' ? msg.content.slice(0, 24) : x.name }
      : x),
  }))
}
export function patchLastAssistant(id: string, mutate: (c: string) => string) {
  set(s => ({
    sessions: s.sessions.map(x => {
      if (x.id !== id) return x
      const messages = x.messages.slice()
      for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === 'assistant') { messages[i] = { ...messages[i], content: mutate(messages[i].content) }; break }
      }
      return { ...x, messages }
    }),
  }))
}

// ---------- 大纲/论文 ----------
export function flatten(sections: { sections?: unknown }[] | OutlineSection[]): OutlineSection[] {
  const out: OutlineSection[] = []
  const walk = (list: OutlineSection[]) => { for (const s of list) { out.push(s); if (s.subsections?.length) walk(s.subsections) } }
  walk(sections as OutlineSection[])
  return out
}

export async function refreshOutline() {
  const p = getState().currentProject
  if (!p) { set({ outline: null }); return }
  set({ outlineLoading: true })
  try {
    const outline = await api<Outline>(`/api/outline/${encodeURIComponent(p)}`)
    set({ outline, outlineLoading: false })
  } catch {
    set({ outline: null, outlineLoading: false })
  }
}

export function setCurrentProject(project: string | null) {
  set({ currentProject: project, outline: null })
  scheduleSave()
  if (project) void refreshOutline()
}

/** 流式重生成/润色共用：SSE chunk → 直接更新大纲内该节 content（主页实时可见）。 */
export function patchSectionContent(project: string, sectionId: string, mutate: (c: string) => string) {
  set(s => {
    if (s.currentProject !== project || !s.outline) return {}
    const patchSec = (list: OutlineSection[]): OutlineSection[] => list.map(sec => {
      if (sec.id === sectionId) return { ...sec, content: mutate(sec.content ?? '') }
      if (sec.subsections?.length) return { ...sec, subsections: patchSec(sec.subsections) }
      return sec
    })
    return { outline: { ...s.outline, sections: patchSec(s.outline.sections) } }
  })
}

export function openSettings(section = 'general') { set({ settingsOpen: true, settingsSection: section }) }

// ---------- 启动 ----------
export async function boot() {
  try {
    const st = await api<{ enabled?: boolean; users?: number }>('/api/auth/status')
    if (st && st.enabled && (st.users ?? 0) > 0) {
      const me = await api<{ user?: AuthUser }>('/api/auth/me').catch(() => null)
      if (me?.user && !me.user.legacy) { AUTH.user = me.user; set({ user: me.user }) }
      else { set({ authRequired: true }); return }
    }
  } catch (e: unknown) {
    if ((e as { status?: number }).status === 401) { set({ authRequired: true }); return }
  }
  await enterApp()
}

export async function enterApp() {
  try {
    const me = await api<{ user?: AuthUser }>('/api/auth/me').catch(() => null)
    if (me?.user && me.user.username && !me.user.legacy) { AUTH.user = me.user; set({ user: me.user }) }
  } catch { /* 单用户模式无妨 */ }
  const ws = await api<{ state: Record<string, unknown> }>('/api/workspace')
  const st = ws.state ?? {}
  const sessions = (st.sessions as Session[] | undefined) ?? []
  const savedTheme = st.theme as string | undefined
  const theme: 'dark' | 'light' = savedTheme === 'light' || savedTheme === 'mint' ? 'light' : 'dark'
  if (savedTheme && savedTheme !== getState().theme) applyTheme(theme)
  const savedFont = Number(st.font_size)
  if (savedFont) applyFontSize(savedFont)
  markBooted()   // 之后运行中的 401 才走「刷新 → 登录页」；boot 期间只抛错
  set({
    booted: true,
    authRequired: false,   // 登录成功进入主界面（否则 App 优先渲染登录页，点击登录像是没反应）
    wsRev: (st.rev as number) || 0,
    sessions,
    activeSessionId: (st.active_session as string) || sessions[0]?.id || null,
    currentProject: (st.active_project as string) || null,
    wsState: st,
  })
  if (!sessions.length) newSession()
  if (getState().currentProject) void refreshOutline()
  void refreshKb()
  void pollFulltext(true)
  // 空闲探测：全文任务也可能由对话中的动作启动（不经「一键全文」按钮），
  // 每 10s 轻查一次，发现 active 即交给 pollFulltext 高频轮询
  window.setInterval(() => {
    const s = getState()
    if (!s.booted || s.fulltext?.active) return
    void api<Record<string, unknown>>('/api/fulltext/status')
      .then(raw => { if (normalizeFulltext(raw)?.active) void pollFulltext() })
      .catch(() => { /* 无妨 */ })
  }, 10000)
}

export async function refreshKb() {
  try { set({ kb: await api<Record<string, KbCollectionInfo>>('/api/kb/status') }) }
  catch { /* 无妨 */ }
}

/** 改用户名（登录名本身）：成功后服务端已吊销全部会话，需重新登录 */
export async function renameUsername(username: string): Promise<void> {
  await api('/api/auth/username', { method: 'PUT', body: JSON.stringify({ username }) })
}

/** 保存个人资料（头像）到 MySQL，并同步内存与 AUTH */
export async function saveProfile(patch: { avatar?: string }): Promise<AuthUser> {
  const resp = await api<{ ok: boolean; user: AuthUser }>('/api/auth/profile', {
    method: 'PUT', body: JSON.stringify(patch),
  })
  AUTH.user = resp.user
  set({ user: resp.user })
  return resp.user
}

// ---------- 全文任务轮询 ----------
let polling = false
/** 后端 /api/fulltext/status → 前端视图模型（后端字段：state/finished/total_words/events[].title） */
function normalizeFulltext(ft: Record<string, unknown> | null): FulltextStatus | null {
  if (!ft) return null
  const state = String(ft['state'] ?? 'none')
  const sections = Array.isArray(ft['sections']) ? ft['sections'] as Record<string, unknown>[] : []
  const cur = sections.find(s => s['id'] === ft['current'])
  const events = Array.isArray(ft['events']) ? ft['events'] as Record<string, unknown>[] : []
  return {
    ...ft,
    active: state === 'running',
    state,
    current: Number(ft['finished'] ?? 0),
    total: Number(ft['total'] ?? 0),
    words: Number(ft['total_words'] ?? 0),
    current_title: String(cur?.['title'] ?? ''),
    log: events.slice(-6).map(e => {
      const t = String(e['type'] ?? '')
      const title = e['title'] ? ` ${String(e['title'])}` : ''
      const score = e['ai_score'] !== undefined ? ` AI ${e['ai_score']}分` : ''
      return `${String(e['time'] ?? '').slice(11)} ${t}${title}${score}`
    }).filter(Boolean),
  }
}
export async function pollFulltext(start = false) {
  if (polling) return
  polling = true
  try {
    for (;;) {
      const s = getState()
      if (!s.booted) break
      const raw = await api<Record<string, unknown>>('/api/fulltext/status').catch(() => null)
      const ft = normalizeFulltext(raw)
      const wasActive = !!s.fulltext?.active
      set({ fulltext: ft })
      if (!ft?.active) { if (wasActive || start) { if (wasActive) toast('全文生成已结束', 'ok'); void refreshOutline() } break }
      await new Promise(r => setTimeout(r, 1500))
    }
  } finally { polling = false }
}
