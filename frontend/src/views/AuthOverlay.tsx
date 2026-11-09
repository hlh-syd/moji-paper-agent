/**
 * 登录/注册（RBAC）。token 键 paper_agent_token 与历代前端一致。
 * 输入为非受控（ref 读取）：浏览器自动填充不触发 onChange，受控组件会
 * 把 state 空串写回输入框——表现为「框里有字、点登录却提示为空」。
 */
import { useRef, useState } from 'react'
import { Button, Input } from '@dsh/primitives'
import { api, AUTH, type AuthUser } from '../api'
import { enterApp, toast } from '../store'

export function AuthOverlay() {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const userRef = useRef<HTMLInputElement | null>(null)
  const passRef = useRef<HTMLInputElement | null>(null)

  async function submit() {
    const user = (userRef.current?.value ?? '').trim()
    const pass = passRef.current?.value ?? ''
    if (!user || !pass) { setMsg('请输入用户名与密码'); return }
    setBusy(true); setMsg('')
    try {
      const resp = await api<{ token: string; user?: AuthUser }>(`/api/auth/${mode}`, {
        method: 'POST',
        body: JSON.stringify({ username: user, password: pass }),
      })
      AUTH.set(resp.token, resp.user ?? { username: user, role: 'user' })
      toast(mode === 'login' ? '登录成功' : '注册成功', 'ok')
      await enterApp()
    } catch (e) { setMsg((e as Error).message) }
    finally { setBusy(false) }
  }

  return (
    <div className="auth-overlay">
      <div className="auth-card">
        <div className="auth-logo"><span className="auth-glyph" aria-hidden>墨</span><span>墨记 · 论文智能体</span></div>
        <div className="auth-title">{mode === 'login' ? '登录' : '注册账号'}</div>
        {mode === 'register' && <div className="auth-hint">第一个注册的账号自动成为管理员</div>}
        <div key={mode} className="auth-fields">
          <Input ref={userRef} placeholder="用户名" defaultValue="" autoComplete="username" />
          <Input ref={passRef} type="password" placeholder="密码（至少6位）" defaultValue=""
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            onKeyDown={e => { if (e.key === 'Enter') void submit() }} />
        </div>
        <Button variant="primary" disabled={busy} onClick={() => void submit()} className="auth-submit">
          {busy ? '请稍候…' : mode === 'login' ? '登 录' : '创建账号'}
        </Button>
        <div className="auth-switch">
          {mode === 'login' ? '没有账号？' : '已有账号？'}
          <a onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setMsg('') }}>
            {mode === 'login' ? '注册' : '去登录'}
          </a>
        </div>
        {msg && <div className="auth-msg">{msg}</div>}
      </div>
    </div>
  )
}
