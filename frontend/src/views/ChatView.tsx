/**
 * 对话视图：SSE 流式 + DSH MarkdownText 增量渲染；会话导出/清空（对接后端 conversation 端点）。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, MarkdownText, UserMessageBubble } from '@dsh/primitives'
import type { MarkdownLabels } from '@dsh/primitives'
import { api, downloadText, sse } from '../api'
import { Icon } from '../components/Icon'
import { confirmDialog } from '../components/confirm'
import {
  appendToSession, currentSession, newSession, patchLastAssistant,
  scheduleSave, set, toast, useStore,
} from '../store'

const MD_LABELS: MarkdownLabels = { code: { copyLabel: '复制', copiedLabel: '已复制' }, footnotes: '脚注' }

export function ChatView() {
  const sessionId = useStore(s => s.activeSessionId)
  const sessions = useStore(s => s.sessions)
  const generating = useStore(s => s.generating)
  const project = useStore(s => s.currentProject)
  const session = sessions.find(x => x.id === sessionId) ?? null
  const [draft, setDraft] = useState('')
  const [liveStatus, setLiveStatus] = useState('')
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const turns = useMemo(() => {
    const t: { role: string; content: string }[] = []
    for (const m of session?.messages ?? []) {
      if (m.role === 'user') t.push({ role: 'user', content: m.content })
      else if (m.content) t.push({ role: 'assistant', content: m.content })
    }
    return t
  }, [session?.messages])

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [turns.length, turns[turns.length - 1]?.content, liveStatus])

  async function send() {
    const text = draft.trim()
    if (!text || generating || !session) return
    setDraft('')
    set({ generating: true })
    appendToSession(session.id, { role: 'user', content: text, attachments: [] })
    appendToSession(session.id, { role: 'assistant', content: '' })
    setLiveStatus('思考中…')
    try {
      await sse('/api/chat/stream', {
        question: text, session_id: session.id, project_name: project ?? undefined,
      }, ev => {
        const type = ev.type as string
        if (type === 'status') { setLiveStatus(String(ev.message ?? ev.content ?? '')); return }
        if (type === 'chunk') {
          setLiveStatus('')
          patchLastAssistant(session.id, c => c + String(ev.content ?? ev.text ?? ''))
          return
        }
        if (type === 'done') {
          const full = String(ev.content ?? '')
          if (full) patchLastAssistant(session.id, () => full)
          return
        }
        if (type === 'error') {
          patchLastAssistant(session.id, c => c || String(ev.content ?? '模型调用失败'))
          return
        }
        if (type === 'action_start') setLiveStatus(`执行动作：${String(ev.action ?? '')}`)
        if (type === 'action_result') setLiveStatus(`动作完成：${String(ev.summary ?? '')}`)
      })
      const cur = currentSession()
      if (cur?.messages.length && cur.messages.at(-1)?.content === '') {
        patchLastAssistant(session.id, () => '（无回复）')
      }
      scheduleSave()
    } catch (e) {
      patchLastAssistant(session.id, c => c || (e as Error).message)
      toast('发送失败', 'err')
    } finally {
      setLiveStatus('')
      set({ generating: false })
    }
  }

  function exportSession() {
    if (!session) return
    const lines = [`# ${session.name}`, '']
    for (const m of session.messages) {
      const content = (m.content ?? '').trim()
      if (!content) continue
      lines.push(m.role === 'user' ? '## 我' : '## 墨记', '', content, '')
    }
    const day = new Date().toISOString().slice(0, 10)
    downloadText(`${(session.name || '会话').replace(/[\\/:*?"<>|]/g, '_')}_${day}.md`, lines.join('\n'))
    toast('会话已导出（Markdown）', 'ok')
  }

  async function clearConversation() {
    if (!session) return
    const ok = await confirmDialog({
      title: '清空会话',
      description: '清空当前会话的全部消息与附件，后端对话上下文一并清除。',
      confirmLabel: '清空',
    })
    if (!ok) return
    try {
      await api(`/api/conversation/${encodeURIComponent(session.id)}/clear`, { method: 'POST' })
      set(s => ({ sessions: s.sessions.map(x => x.id === session.id ? { ...x, messages: [] } : x) }))
      toast('会话已清空', 'ok')
    } catch (e) { toast((e as Error).message, 'err') }
  }

  if (!session) {
    return (
      <div className="chat-view">
        <div className="chat-empty">
          <p>没有会话</p>
          <Button variant="primary" onClick={() => newSession()}>新建会话</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="chat-view">
      <div className="chat-scroll" ref={scrollRef}>
        {turns.length === 0 && (
          <div className="chat-guide">
            <div className="cg-title">开始对话</div>
            <p className="cg-line">1. 未选项目可直接提问；选定项目后墨记可操控论文</p>
            <p className="cg-line">2. 右侧论文工作区实时展示生成结果</p>
            <p className="cg-line">3. Ctrl+Enter 发送</p>
          </div>
        )}
        {turns.map((t, i) => t.role === 'user' ? (
          <div className="chat-row user" key={i}>
            <UserMessageBubble><span className="chat-plain">{t.content}</span></UserMessageBubble>
          </div>
        ) : (
          <div className="chat-row assistant" key={i}>
            <MarkdownText text={t.content} streaming={i === turns.length - 1 && generating} labels={MD_LABELS} />
          </div>
        ))}
        {generating && liveStatus && <div className="chat-status">{liveStatus}</div>}
      </div>
      <div className="composer">
        <textarea
          value={draft}
          placeholder={project ? `基于项目「${project}」讨论…` : '输入消息，Ctrl+Enter 发送'}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void send() } }}
          disabled={generating}
        />
        <div className="composer-bar">
          <button className="link-btn" title="导出会话（Markdown）"
            onClick={() => exportSession()}><Icon name="download" size={12} />导出</button>
          <button className="link-btn" title="清空会话" onClick={() => void clearConversation()}><Icon name="trash" size={12} />清空</button>
          <span className="hint">Ctrl+Enter 发送</span>
          <span className="grow" />
          <Button variant="primary" disabled={generating || !draft.trim()} onClick={() => void send()}>发送</Button>
        </div>
      </div>
    </div>
  )
}
