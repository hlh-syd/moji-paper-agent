/**
 * 侧栏：DSH ui-sidebar 结构（12/6 边距、专属底色、56px 图标轨）。
 */
import { useMemo, useState } from 'react'
import { Button, Pill } from '@dsh/primitives'
import { api, AUTH } from '../api'
import { ConfirmModal } from '../components/ConfirmModal'
import { Icon } from '../components/Icon'
import { Avatar, ProfileModal } from '../components/ProfileModal'
import {
  newSession, refreshKb, removeSession, renameSession,
  set, setActiveSession, toast, useStore,
} from '../store'

export function Sidebar(props: { collapsed: boolean; width: number }) {
  const sessions = useStore(s => s.sessions)
  const activeId = useStore(s => s.activeSessionId)
  const user = useStore(s => s.user)
  const kb = useStore(s => s.kb)
  const project = useStore(s => s.currentProject)
  const [query, setQuery] = useState('')
  const [pane, setPane] = useState<'sessions' | 'kb'>('sessions')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')
  const [logoutOpen, setLogoutOpen] = useState(false)
  const [logoutBusy, setLogoutBusy] = useState(false)
  const [profileOpen, setProfileOpen] = useState(false)

  function doLogout() {
    setLogoutBusy(true)
    // 服务端会话注销（Redis 删键），再清本地凭证；网络失败也继续本地登出
    void fetch('/api/auth/logout', { method: 'POST', headers: { Authorization: `Bearer ${AUTH.token}` } })
      .catch(() => { /* 网络失败也继续本地登出 */ })
      .finally(() => { AUTH.clear(); location.reload() })
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return sessions
    return sessions.filter(s =>
      s.name.toLowerCase().includes(q) ||
      s.messages.some(m => (m.content || '').toLowerCase().includes(q)))
  }, [sessions, query])

  if (props.collapsed) {
    return (
      <aside className="sidebar-root collapsed">
        <button className="rail-btn" title="展开侧栏" onClick={() => set({ sidebarCollapsed: false })}><Icon name="chevron-right" /></button>
        <button className="rail-btn" title="新建会话" onClick={() => newSession()}><Icon name="plus" /></button>
        <button className="rail-btn" title="知识库" onClick={() => { setPane('kb'); set({ sidebarCollapsed: false }) }}><Icon name="database" /></button>
        <button className="rail-btn" title="项目与文件（设置）" onClick={() => set({ settingsOpen: true, settingsSection: 'projects' })}><Icon name="folder" /></button>
        <div className="grow" />
        <button className="rail-btn" title="设置" onClick={() => set({ settingsOpen: true })}><Icon name="settings" /></button>
      </aside>
    )
  }

  return (
    <aside className="sidebar-root">
      <div className="sb-brand">
        <span className="sb-logo" aria-hidden>
          <svg viewBox="0 0 32 32" width="16" height="16">
            <path d="M16 6.2 C13.1 11.0 10.4 14.2 10.4 17.7 a5.6 5.6 0 0 0 11.2 0 C21.6 14.2 18.9 11.0 16 6.2 Z" fill="var(--dsw-static-deepseek-400)"/>
            <path d="M10.8 26.2 h10.4" stroke="var(--dsw-static-deepseek-400)" stroke-width="1.9" stroke-linecap="round" opacity="0.8"/>
          </svg>
        </span>
        <span className="sb-brand-name">墨记</span>
        <button className="sb-collapse-btn" title="折叠侧栏" onClick={() => set({ sidebarCollapsed: true })}><Icon name="chevron-left" /></button>
      </div>

      <div className="sb-actions">
        <Button variant="ghost" className="sb-new" onClick={() => newSession()}><Icon name="plus" /> 新建会话</Button>
        <div className="sb-search">
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索会话与消息" />
        </div>
        <div className="sb-panetabs">
          <Pill className="pill-shape" active={pane === 'sessions'} onClick={() => setPane('sessions')}>会话</Pill>
          <Pill className="pill-shape" active={pane === 'kb'} onClick={() => { setPane('kb'); void refreshKb() }}>知识库</Pill>
        </div>
      </div>

      {pane === 'sessions' ? (
        <div className="sb-list">
          {filtered.length === 0 && <div className="sb-empty">暂无会话</div>}
          {filtered.map(sess => (
            <div
              key={sess.id}
              className={`sb-item${sess.id === activeId ? ' active' : ''}`}
              onClick={() => setActiveSession(sess.id)}
            >
              {editingId === sess.id ? (
                <input
                  className="sb-rename-input" value={editingName} autoFocus
                  onClick={e => e.stopPropagation()}
                  onChange={e => setEditingName(e.target.value)}
                  onBlur={() => { renameSession(sess.id, editingName.trim() || sess.name); setEditingId(null) }}
                  onKeyDown={e => {
                    if (e.key === 'Enter') { renameSession(sess.id, editingName.trim() || sess.name); setEditingId(null) }
                    if (e.key === 'Escape') setEditingId(null)
                  }}
                />
              ) : (
                <span className="sb-item-name"
                  onDoubleClick={e => { e.stopPropagation(); setEditingId(sess.id); setEditingName(sess.name) }}
                >{sess.name}</span>
              )}
              <button className="sb-item-x" title="删除会话"
                onClick={e => { e.stopPropagation(); removeSession(sess.id) }}
              ><Icon name="close" size={12} /></button>
            </div>
          ))}
        </div>
      ) : (
        <div className="sb-list">
          <div className="kb-row"><span className="kb-name">方法原论文</span>
            <span className="kb-count">{kb?.method_papers?.document_count ?? '-'}</span>
            <button className="kb-build" onClick={() => void buildKb('methods')}>更新</button></div>
          <div className="kb-row"><span className="kb-name">写作教学库</span>
            <span className="kb-count">{kb?.writing_methodology?.document_count ?? '-'}</span>
            <button className="kb-build" onClick={() => void buildKb('writing')}>更新</button></div>
          <div className="kb-row"><span className="kb-name">多模态文献库</span>
            <span className="kb-count">RAG</span>
            <button className="kb-build" onClick={() => set({ settingsOpen: true, settingsSection: 'kb' })}>管理</button></div>
          <div className="kb-hint"><Icon name="check" size={12} /><span>已持久化保存，重启自动加载{project ? ` · 当前项目：${project}` : ''}</span></div>
        </div>
      )}

      <div className="sb-foot">
        <button type="button" className="sb-user" title="个人资料（头像与显示名）"
          onClick={() => { if (user?.username) setProfileOpen(true) }}>
          <Avatar user={user} size={28} />
          <span className="sb-user-name">{user?.username || '单用户模式'}</span>
        </button>
        <div className="sb-foot-actions">
          <button className="sb-icon-btn" title={user?.username ? '退出登录' : '单用户模式'}
            onClick={() => { if (user?.username) setLogoutOpen(true) }}>
            <Icon name="logout" />
          </button>
          <button className="sb-icon-btn" title="设置" onClick={() => set({ settingsOpen: true })}>
            <Icon name="settings" />
          </button>
        </div>
      </div>

      <ConfirmModal
        open={logoutOpen} busy={logoutBusy}
        title="退出登录"
        description={`将退出当前账号 ${user?.username ?? ''}，再次使用需重新登录。`}
        confirmLabel="退出登录" busyLabel="正在退出…"
        onClose={() => setLogoutOpen(false)} onConfirm={doLogout}
      />
      <ProfileModal open={profileOpen} user={user} onClose={() => setProfileOpen(false)} />
    </aside>
  )
}

async function buildKb(which: string) {
  toast(`正在构建 ${which} 知识库…`)
  try {
    await api(`/api/kb/build/${which}`, { method: 'POST' })
    await refreshKb()
    toast('知识库已更新', 'ok')
  } catch (e) { toast(`构建失败：${(e as Error).message}`, 'err') }
}
