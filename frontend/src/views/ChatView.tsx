/**
 * 对话视图：SSE 流式 + DSH MarkdownText 增量渲染。
 * 玻璃卡欢迎页（问候 + 项目感知提示卡）、身份化消息流（徽标 + hover 操作组）、
 * 卡片 composer（自动增高 + 发送/停止双态）。入场动画统一 --moji-ease-out 快起轻收。
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, MouseEvent as ReactMouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { Button, MarkdownText, UserMessageBubble } from '@dsh/primitives'
import type { MarkdownLabels } from '@dsh/primitives'
import { api, sse } from '../api'
import { Icon } from '../components/Icon'
import {
  appendToSession, currentSession, newSession, openSettings, patchLastAssistant,
  scheduleSave, set, toast, useStore,
} from '../store'

const MD_LABELS: MarkdownLabels = { code: { copyLabel: '复制', copiedLabel: '已复制' }, footnotes: '脚注' }

/** 对话模式（完全访问/仅读取/联网搜索）与推理档位——1:1 composer 的两个下拉 */
const CHAT_MODES = [
  { id: 'full', label: '完全访问' },
  { id: 'read', label: '仅读取' },
  { id: 'web', label: '联网搜索' },
] as const
const EFFORTS = ['最低', '中', '最高'] as const

/** 模型菜单供应商头：按当前模型名推断（BigModel / DeepSeek） */
function providerOf(model: string | undefined): string {
  if (model && /glm|bigmodel|chatglm/i.test(model)) return 'BigModel'
  if (model && /deepseek/i.test(model)) return 'DeepSeek'
  return '模型服务'
}

/** BigModel 四角星徽标（渐变蓝） */
function BigModelMark() {
  return (
    <svg viewBox="0 0 24 24" width={18} height={18} aria-hidden="true">
      <defs>
        <linearGradient id="msel-bm-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#4E8DF7" />
          <stop offset="1" stopColor="#1D5EF3" />
        </linearGradient>
      </defs>
      <path fill="url(#msel-bm-g)" d="M12 1.6l2.9 7.5 7.5 2.9-7.5 2.9-2.9 7.5-2.9-7.5-7.5-2.9 7.5-2.9z" />
    </svg>
  )
}

/** 思考强度渐变滑轨上的装饰光点（左起位置% / 纵向位置% / 直径 / 不透明度） */
const SPARKS = [
  { l: 7, t: 62, s: 4, o: 0.5 }, { l: 15, t: 30, s: 3, o: 0.35 }, { l: 27, t: 68, s: 5, o: 0.4 },
  { l: 40, t: 38, s: 3, o: 0.5 }, { l: 53, t: 60, s: 4, o: 0.35 }, { l: 64, t: 32, s: 6, o: 0.45 },
  { l: 76, t: 66, s: 3, o: 0.4 }, { l: 87, t: 42, s: 4, o: 0.5 },
] as const
const THUMB_PX = 30   // 与 CSS --msel-thumb 保持一致

/** 问候语（按当前时段） */
function greeting(): string {
  const h = new Date().getHours()
  if (h < 5) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 14) return '中午好'
  if (h < 18) return '下午好'
  return '晚上好'
}

/** 提示卡：选了项目给项目感知动作，否则给通用学术写作卡。点击填入输入框。 */
function suggestCards(project: string | null): { t: string; d: string; q: string }[] {
  return project ? [
    { t: '生成论文大纲', d: '按期刊字数模板分章展开', q: '请为当前项目生成论文大纲' },
    { t: '查询项目知识库', d: '针对项目文献与数据提问', q: '项目知识库里有哪些可以使用的文献和数据？' },
    { t: '解读论文图表', d: '给出图5-1这类图表的解读', q: '帮我解读论文中图5-1想要说明什么' },
    { t: '段落去 AI 味', d: '把指定段落改写得更自然', q: '帮我把前言第一段改写得更自然，去掉 AI 味' },
  ] : [
    { t: '拟定研究选题', d: '从方向到可行题目的打磨', q: '我在做论文选题，帮我梳理几个可行的研究方向并说明理由' },
    { t: '搭建论文框架', d: '章节结构与字数分配建议', q: '一篇三万字左右的中文期刊论文，章节结构应该怎么安排？' },
    { t: '文献综述方法', d: '检索、筛选与评述的套路', q: '文献综述应该怎么组织与评述？给我一套可执行的方法' },
    { t: '学术语言润色', d: '常见表达问题与改法', q: '学术论文的语言表达有哪些常见问题？逐条给出润色建议' },
  ]
}

/** 行内标记不切一半：** / ` / ~~ 未闭合时把显现点扩展到闭合处 */
function healSlice(s: string, n: number): number {
  let end = Math.min(n, s.length)
  for (const mark of ['**', '`', '~~']) {
    const cnt = s.slice(0, end).split(mark).length - 1
    if (cnt % 2 === 1) {
      const close = s.indexOf(mark, end)
      if (close >= 0) end = Math.min(s.length, close + mark.length)
    }
  }
  return end
}

/** 最深最后一个文本节点（尾段包装的落点） */
function lastTextNode(root: Node): Text | null {
  let cur: Node = root
  while (cur.lastElementChild) cur = cur.lastElementChild
  let n: Node | null = cur.lastChild
  while (n && n.nodeType !== Node.TEXT_NODE) n = n.previousSibling
  return n instanceof Text ? n : null
}

/**
 * 流式按词淡入：显现节奏 150ms/段（每段 3 字起步，积压自适应加速追帧），
 * 刚显现的 2-4 字段在 layout effect 中包成 .seg-live（透明+4px 模糊→清晰，
 * 动画时长≈段节拍，落定即被 React 正文文本接管——无重复无闪跳）；
 * 末尾闪烁光标由 CSS ::after 挂在最后一个块上跟随。高度随显现平滑长高。
 */
function StreamText(props: { text: string; streaming: boolean }) {
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [shown, setShown] = useState(0)
  const prevLenRef = useRef(0)        // 上一次已包段的 healed 长度
  const textRef = useRef(props.text)
  textRef.current = props.text

  // 显现节奏：单 interval 读 ref（text 高频变化不重建节拍器）；流结束立即补齐
  useEffect(() => {
    if (!props.streaming) {
      setShown(textRef.current.length)
      return
    }
    const iv = window.setInterval(() => {
      setShown(s => {
        const full = textRef.current.length
        if (s >= full) return s
        return Math.min(full, s + Math.max(3, Math.ceil((full - s) / 18)))
      })
    }, 150)
    return () => window.clearInterval(iv)
  }, [props.streaming])

  const cut = Math.min(shown, props.text.length)
  const healed = healSlice(props.text, cut)

  // 尾段包装：先移除上一段 live span（其文字已被 React 正文接管，删除不丢字），
  // 再把本次新增的尾部字符包进 .seg-live
  useLayoutEffect(() => {
    const wrap = wrapRef.current
    if (!wrap || !props.streaming) return
    wrap.querySelectorAll('.seg-live').forEach(el => el.remove())
    const added = healed - prevLenRef.current
    prevLenRef.current = healed
    if (added <= 0) return
    const node = lastTextNode(wrap)
    if (!node) return
    const txt = node.nodeValue ?? ''
    const k = Math.min(added, txt.length)
    if (!k) return
    node.nodeValue = txt.slice(0, txt.length - k)
    const span = document.createElement('span')
    span.className = 'seg-live'
    span.textContent = txt.slice(txt.length - k)
    node.parentNode?.appendChild(span)
  })

  return (
    <div ref={wrapRef}>
      <MarkdownText text={props.text.slice(0, healed)} streaming={props.streaming} labels={MD_LABELS} />
    </div>
  )
}

export function ChatView() {
  const sessionId = useStore(s => s.activeSessionId)
  const sessions = useStore(s => s.sessions)
  const generating = useStore(s => s.generating)
  const project = useStore(s => s.currentProject)
  const user = useStore(s => s.user)
  const quoteMsg = useStore(s => s.quoteToComposer)
  const session = sessions.find(x => x.id === sessionId) ?? null
  const [draft, setDraft] = useState('')
  const [liveStatus, setLiveStatus] = useState('')
  const [entering, setEntering] = useState(false)   // 会话切换后 1.2s 内：行错开入场
  const [selPop, setSelPop] = useState<{ text: string; x: number; y: number } | null>(null)
  const [quotes, setQuotes] = useState<{ label: string; text: string }[]>([])   // 引用附件：chip 展示，发送时携带全文
  const [mode, setMode] = useState(() => localStorage.getItem('moji_chat_mode') ?? 'full')
  const [effort, setEffort] = useState<string>(() => localStorage.getItem('moji_chat_effort') ?? '最高')
  const [files, setFiles] = useState<string[]>([])   // 附件文件名（count 展示 + 发送时随问题带上）
  const [modelInfo, setModelInfo] = useState<{ cur: string; list: string[] } | null>(null)
  const [menu, setMenu] = useState<{ kind: 'mode' | 'model'; x: number; y: number; w: number; up: boolean; maxH: number } | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)
  const sliderRef = useRef<HTMLDivElement | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  const followRef = useRef(true)   // 用户手动上滑即暂停自动跟随，滑回底部再恢复
  const abortRef = useRef<AbortController | null>(null)
  const effortIdx = Math.max(0, EFFORTS.indexOf(effort as (typeof EFFORTS)[number]))

  const turns = useMemo(() => {
    const msgs = session?.messages ?? []
    const t: { role: string; content: string; attachments?: string[] }[] = []
    for (let i = 0; i < msgs.length; i++) {
      const m = msgs[i]
      if (m.role === 'user') t.push({ role: 'user', content: m.content, attachments: m.attachments })
      // assistant：空内容也保留（流式等待时渲染「正在思考」三点气泡）
      else if (m.content || i === msgs.length - 1) t.push({ role: 'assistant', content: m.content })
    }
    return t
  }, [session?.messages])

  // 会话切换：跟随复位 + 行错开入场（像传送带接上），1.2s 后恢复正常入场节奏
  useEffect(() => {
    followRef.current = true
    setEntering(true)
    const t = window.setTimeout(() => setEntering(false), 1200)
    return () => window.clearTimeout(t)
  }, [session?.id])

  useEffect(() => {
    const el = scrollRef.current
    if (!el || !followRef.current) return
    // 新消息出现用平滑滚动跟随；流式增量直接贴底（连续 smooth 会互相打架）
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turns.length, liveStatus])

  useEffect(() => {
    const el = scrollRef.current
    if (el && followRef.current) el.scrollTop = el.scrollHeight
  }, [turns[turns.length - 1]?.content])

  // composer 自动增高：1 行起步，最多约 6 行后内滚
  useEffect(() => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [draft])

  // 其他组件（如自查报告）投递引用进来：转成引用 chip（发送时自动携带全文）
  const lastQuoteNonce = useRef(0)
  useEffect(() => {
    if (!quoteMsg || quoteMsg.nonce === lastQuoteNonce.current) return
    lastQuoteNonce.current = quoteMsg.nonce
    setQuotes(qs => [...qs, { label: '全文自查报告', text: quoteMsg.text }])
    taRef.current?.focus()
    set({ quoteToComposer: null })
  }, [quoteMsg])

  // 模型清单/当前模型（/api/settings 只读，所有登录用户可读）
  useEffect(() => {
    void api<{ models: { llm: string }; options: { llm: string[] } }>('/api/settings')
      .then(r => setModelInfo({ cur: r.models.llm, list: r.options.llm }))
      .catch(() => { /* 设置不可达时模型选择器只展示当前值 */ })
  }, [])

  // composer 下拉浮层：点击外部关闭（触发器自身由 data-cmp-menu 标记豁免）
  useEffect(() => {
    if (!menu) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Element
      if (t.closest('.sel-menu') || t.closest('[data-cmp-menu]')) return
      setMenu(null)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menu])

  function openCmpMenu(kind: 'mode' | 'model', e: ReactMouseEvent<HTMLButtonElement>) {
    const r = e.currentTarget.getBoundingClientRect()
    // composer 贴近屏幕底边：下方放不下就向上翻（translateY(-100%)）
    const estH = (kind === 'model'
      ? (modelInfo?.list.length ?? 2) * 44 + 244   // 供应商头+分区标签+思考强度滑轨的固定开销
      : CHAT_MODES.length * 34 + 12)
    const openUp = window.innerHeight - r.bottom < estH + 12 && r.top > estH
    const maxH = openUp ? r.top - 16 : window.innerHeight - r.bottom - 12
    setMenu(m => (m?.kind === kind ? null
      : { kind, x: r.left, y: openUp ? r.top - 4 : r.bottom + 4, w: kind === 'model' ? 340 : Math.max(176, r.width), up: openUp, maxH }))
  }

  async function pickModel(m: string) {
    setMenu(null)
    if (!modelInfo || m === modelInfo.cur) return
    if (user?.role !== 'admin') { toast('模型为全局配置，切换需要管理员账号', 'err'); return }
    try {
      await api('/api/settings', { method: 'POST', body: JSON.stringify({ models: { llm: m } }) })
      setModelInfo(i => (i ? { ...i, cur: m } : i))
      toast(`模型已切换为 ${m}（全局，即时生效）`, 'ok')
    } catch (e) { toast(`切换失败：${(e as Error).message}`, 'err') }
  }

  function pickMode(id: string) {
    setMode(id)
    localStorage.setItem('moji_chat_mode', id)
    setMenu(null)
  }

  /** 思考强度档位（滑轨/快捷键）：持久化但不关菜单 */
  function setEffortLocal(v: string) {
    setEffort(v)
    localStorage.setItem('moji_chat_effort', v)
  }

  // 思考强度滑轨：按指针横坐标吸附到档位（thumb 半径内映射 0~1）
  function applySlider(clientX: number) {
    const el = sliderRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const t = Math.min(1, Math.max(0, (clientX - r.left - THUMB_PX / 2) / Math.max(1, r.width - THUMB_PX)))
    setEffortLocal(EFFORTS[Math.round(t * (EFFORTS.length - 1))])
  }

  // 思考强度快捷键（模型菜单打开时）：Ctrl+T 循环档位
  useEffect(() => {
    if (menu?.kind !== 'model') return
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 't') return
      e.preventDefault()
      setEffortLocal(EFFORTS[(effortIdx + 1) % EFFORTS.length])
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [menu?.kind, effort])

  // 划选 AI 回复片段：浮出工具条；点击浮层外/滚动即隐藏
  useEffect(() => {
    if (!selPop) return
    const onDown = (e: MouseEvent) => {
      if ((e.target as Element).closest('.sel-pop')) return
      setSelPop(null)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [selPop])

  function onMouseUpSel() {
    const sel = window.getSelection()
    const text = sel?.toString() ?? ''
    if (!sel || sel.isCollapsed || text.replace(/\s+/g, '').length < 2) { setSelPop(null); return }
    const anchor = sel.anchorNode
    const el = anchor instanceof Element ? anchor : anchor?.parentElement
    if (!el?.closest('.chat-row.assistant')) { setSelPop(null); return }
    const rect = sel.getRangeAt(0).getBoundingClientRect()
    setSelPop({ text, x: rect.left + rect.width / 2, y: rect.top })
  }

  /** 选中片段 → 加入引用 chip（发送时自动携带全文） */
  function quoteSelection(text: string) {
    const t = text.trim()
    setQuotes(qs => [...qs, { label: `「${t.slice(0, 16)}${t.length > 16 ? '…' : ''}」`, text: t }])
    setSelPop(null)
    taRef.current?.focus()
  }

  /** 发起一轮对话（发送与重新生成共用）。visible/attachments：气泡展示的正文与引用标记（缺省=question 本身） */
  async function ask(question: string, visible?: string, attachments?: string[]) {
    if (generating || !session) return
    set({ generating: true })
    followRef.current = true   // 发送即回到跟随模式
    appendToSession(session.id, { role: 'user', content: visible ?? question, attachments: attachments ?? [] })
    appendToSession(session.id, { role: 'assistant', content: '' })
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      await sse('/api/chat/stream', {
        question, session_id: session.id, project_name: project ?? undefined,
        mode, effort,   // 预留：后端后续可按模式/档位路由
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
      }, ctrl.signal)
      const cur = currentSession()
      if (cur?.messages.length && cur.messages.at(-1)?.content === '') {
        patchLastAssistant(session.id, () => '（无回复）')
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        patchLastAssistant(session.id, c => c || '（已停止）')   // 手动停止：保留已生成部分
        toast('已停止生成')
      } else {
        patchLastAssistant(session.id, c => c || (e as Error).message)
        toast('发送失败', 'err')
      }
    } finally {
      abortRef.current = null
      setLiveStatus('')
      set({ generating: false })
      scheduleSave()
    }
  }

  async function send() {
    const text = draft.trim()
    if ((!text && !quotes.length && !files.length) || generating || !session) return
    // 引用全文 + 附件名作为上下文先发，用户输入紧随其后；气泡只展示用户输入 + 引用 chip
    const attLine = files.length ? `[附件：${files.join('、')}]\n\n` : ''
    const question = (attLine + (quotes.length
      ? quotes.map(q => q.text).join('\n\n') + (text ? `\n\n${text}` : '')
      : text)).trim()
    const labels = quotes.map(q => q.label)
    setDraft('')
    setQuotes([])
    setFiles([])
    await ask(question, text, labels)
  }

  function stop() { abortRef.current?.abort() }

  /** 重新生成最后一条回复：删掉尾部 assistant 消息，用最后一条用户提问重发 */
  async function regenerate() {
    if (generating || !session) return
    const lastUser = [...session.messages].reverse().find(m => m.role === 'user')
    if (!lastUser) return
    set(s => ({ sessions: s.sessions.map(x => {
      if (x.id !== session.id) return x
      const m = [...x.messages]
      while (m.length && m[m.length - 1].role === 'assistant') m.pop()
      return { ...x, messages: m }
    }) }))
    await ask(lastUser.content)
  }

  async function copyTurn(text: string) {
    try { await navigator.clipboard.writeText(text); toast('已复制到剪贴板') }
    catch { toast('复制失败：浏览器未授权剪贴板', 'err') }
  }

  function quoteTurn(text: string) {
    setQuotes(qs => [...qs, { label: `「${text.slice(0, 16)}${text.length > 16 ? '…' : ''}」`, text }])
    taRef.current?.focus()
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

  const cards = suggestCards(project)

  return (
    <div className="chat-view">
      <div className={`chat-scroll${entering ? ' chat-enter' : ''}`} ref={scrollRef}
        onMouseUp={onMouseUpSel}
        onScroll={e => {
          const el = e.currentTarget
          followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
          setSelPop(null)   // 滚动后选区定位失效，浮层随之隐藏
        }}>
        <div className="chat-col">
          {turns.length === 0 && (
            <div className="chat-guide">
              <div className="cg-badge"><Icon name="feather" size={22} /></div>
              <div className="cg-title">{greeting()}{user?.username ? `，${user.username}` : ''}</div>
              <div className="cg-sub">{project
                ? `正在基于「${project}」项目对话，试试这些：`
                : '我是墨记，你的论文写作智能体。试试这些：'}</div>
              <div className="cg-cards">
                {cards.map(c => (
                  <button type="button" key={c.t} className="cg-card"
                    onClick={() => { setDraft(c.q); taRef.current?.focus() }}>
                    <b>{c.t}</b>
                    <span>{c.d}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t, i) => {
            const isLast = i === turns.length - 1
            const waiting = isLast && generating && t.role === 'assistant' && !t.content
            return t.role === 'user' ? (
              <div className="chat-row user" key={i} style={{ '--i': i } as CSSProperties}>
                <UserMessageBubble>
                  {t.attachments?.length ? (
                    <div className="bubble-quote">❝ {t.attachments.length} 条对话引用</div>
                  ) : null}
                  <span className="chat-plain">{t.content}</span>
                </UserMessageBubble>
              </div>
            ) : (
              <div className={`chat-row assistant${isLast && generating ? ' chat-streaming' : ''}`} key={i}
                style={{ '--i': i } as CSSProperties}>
                <div className="ai-badge"><Icon name="feather" size={13} /></div>
                <div className="ai-main">
                  <div className="ai-name">墨记</div>
                  {waiting
                    ? <div className="typing" aria-label="正在思考"><i /><i /><i /></div>
                    : (isLast && generating
                      ? <StreamText text={t.content} streaming />
                      : <MarkdownText text={t.content} labels={MD_LABELS} />)}
                  {!waiting && t.content && (
                    <div className="ai-actions">
                      <button type="button" className="ai-act" onClick={() => void copyTurn(t.content)}>复制</button>
                      {isLast && (
                        <button type="button" className="ai-act" disabled={generating}
                          onClick={() => void regenerate()}>重新生成</button>
                      )}
                      <button type="button" className="ai-act" onClick={() => quoteTurn(t.content)}>引用</button>
                    </div>
                  )}
                </div>
              </div>
            )
          })}
          {generating && liveStatus && <div className="chat-status">{liveStatus}</div>}
        </div>
      </div>
      {selPop && (
        <div className="sel-pop" role="toolbar" aria-label="选中片段操作"
          style={{ left: selPop.x, top: selPop.y }}
          onMouseDown={e => e.preventDefault()}>
          <button type="button" className="sel-act primary"
            onClick={() => quoteSelection(selPop.text)}>添加到对话框</button>
          <button type="button" className="sel-act"
            onClick={() => { void navigator.clipboard.writeText(selPop.text.trim()); setSelPop(null); toast('已复制到剪贴板') }}>复制</button>
        </div>
      )}
      <div className="composer">
        {quotes.length > 0 && (
          <div className="quote-chip" title={quotes.map(q => q.label).join('\n')}>
            <span className="qc-mark">❝</span>
            <span>{quotes.length} 条对话引用</span>
            <button type="button" className="qc-x" title="清除引用"
              onClick={() => setQuotes([])}>×</button>
          </div>
        )}
        <textarea
          ref={taRef}
          value={draft}
          placeholder={generating ? '继续输入以排队后续修改'
            : project ? `基于项目「${project}」讨论…` : '输入消息'}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            // Enter 发送（IME 组合中不触发）；Shift+Enter 换行；生成中仅预输入不发送
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              if (!generating) void send()
            }
          }}
        />
        <div className="composer-bar">
          <div className="cmp-group">
            <button type="button" className="cmp-btn" aria-label="添加附件" title="添加附件"
              onClick={() => fileRef.current?.click()}><Icon name="plus" size={18} /></button>
            <input ref={fileRef} type="file" multiple hidden
              onChange={e => { const fs = Array.from(e.target.files ?? []).map(f => f.name); if (fs.length) setFiles(x => [...x, ...fs]); e.target.value = '' }} />
            <button type="button" data-cmp-menu className={`cmp-btn cmp-mode`} aria-haspopup="listbox"
              onClick={e => openCmpMenu('mode', e)}>
              <Icon name="shield" size={14} />
              <span className="cmp-mode-label">{CHAT_MODES.find(m => m.id === mode)?.label ?? '完全访问'}</span>
              <Icon name="chevron-down" size={12} />
            </button>
            {files.length > 0 && (
              <button type="button" className="cmp-btn" title={`附件：${files.join('、')}（点击清空）`}
                onClick={() => setFiles([])}>
                <Icon name="file-text" size={14} />{files.length}
              </button>
            )}
          </div>
          <div className="cmp-group right">
            {generating && <span className="cmp-spin" role="status" aria-label="生成中" />}
            <button type="button" data-cmp-menu className="cmp-btn cmp-model" aria-haspopup="listbox"
              title="切换模型（全局设置，管理员）" onClick={e => openCmpMenu('model', e)}>
              {modelInfo?.cur ?? '…'}
              <span className="effort">{effort}</span>
              <Icon name="chevron-down" size={12} />
            </button>
            {generating ? (
              <button type="button" className="cmp-send" aria-label="停止生成" title="停止生成" onClick={stop}>
                <span className="stop-sq" />
              </button>
            ) : (
              <button type="button" className="cmp-send" aria-label="发送" title="发送"
                disabled={!draft.trim() && !quotes.length && !files.length}
                onClick={() => void send()}>
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
                  strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 19V5" /><path d="M5 12l7-7 7 7" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>
      {menu && createPortal(
        <div className={`sel-menu${menu.up ? ' up' : ''}${menu.kind === 'model' ? ' msel' : ''}`} role="listbox"
          style={{ position: 'fixed', left: menu.x, top: menu.y, width: menu.w, minWidth: 176,
            transform: menu.up ? 'translateY(-100%)' : undefined,
            maxHeight: menu.maxH, overflowY: 'auto' }}>
          {menu.kind === 'mode' ? CHAT_MODES.map(m => (
            <button type="button" key={m.id} role="option" aria-selected={mode === m.id}
              className={`sel-opt${mode === m.id ? ' active' : ''}`}
              onClick={() => pickMode(m.id)}>
              <span className="sel-opt-label">{m.label}</span>
              {mode === m.id && <Icon name="check" size={12} />}
            </button>
          )) : (
            <>
              <button type="button" className="msel-provider" title="模型供应商设置"
                onClick={() => { setMenu(null); openSettings('model') }}>
                <span className="msel-logo" aria-hidden="true">
                  {/deepseek/i.test(modelInfo?.cur ?? '') ? (
                    <svg viewBox="0 0 24 24" width={18} height={18}>
                      <circle cx="12" cy="12" r="10" fill="#4D6BFE" />
                      <text x="12" y="16.6" textAnchor="middle" fill="#fff" fontSize="12" fontWeight="700">D</text>
                    </svg>
                  ) : providerOf(modelInfo?.cur) === 'BigModel' ? <BigModelMark /> : (
                    <svg viewBox="0 0 24 24" width={18} height={18}>
                      <circle cx="12" cy="12" r="10" fill="#98A2B3" />
                    </svg>
                  )}
                </span>
                <span className="msel-pname">{providerOf(modelInfo?.cur)}</span>
                <span className="msel-badge">个人</span>
                <span className="grow" />
                <span className="msel-chev"><Icon name="chevron-right" size={14} /></span>
              </button>
              <div className="msel-sep" />
              <div className="msel-label">模型</div>
              {(modelInfo?.list ?? []).map(m => (
                <button type="button" key={m} role="option" aria-selected={modelInfo?.cur === m}
                  className={`msel-opt${modelInfo?.cur === m ? ' active' : ''}`}
                  onClick={() => void pickModel(m)}>
                  <span className="msel-opt-name">{m}</span>
                  {/flash/i.test(m) && <span className="msel-badge">视觉</span>}
                  <span className="grow" />
                  {modelInfo?.cur === m && <span className="msel-check"><Icon name="check" size={15} /></span>}
                </button>
              ))}
              <div className="msel-sep" />
              <div className="msel-effort-head">
                <span className="msel-elabel">思考强度</span>
                <span className="msel-evalue">{effort}</span>
                <span className="grow" />
                <kbd className="msel-kbd">Ctrl+T</kbd>
              </div>
              <div ref={sliderRef} className="msel-slider" role="slider" tabIndex={0}
                aria-label="思考强度" aria-valuemin={0} aria-valuemax={EFFORTS.length - 1}
                aria-valuenow={effortIdx} aria-valuetext={effort}
                onPointerDown={e => {
                  e.preventDefault()
                  e.currentTarget.setPointerCapture(e.pointerId)
                  applySlider(e.clientX)
                }}
                onPointerMove={e => { if (e.currentTarget.hasPointerCapture(e.pointerId)) applySlider(e.clientX) }}
                onKeyDown={e => {
                  if (e.key === 'ArrowLeft' && effortIdx > 0) setEffortLocal(EFFORTS[effortIdx - 1])
                  if (e.key === 'ArrowRight' && effortIdx < EFFORTS.length - 1) setEffortLocal(EFFORTS[effortIdx + 1])
                }}>
                {SPARKS.map((p, i) => (
                  <span key={i} className="msel-spark" aria-hidden="true"
                    style={{ left: `${p.l}%`, top: `${p.t}%`, width: p.s, height: p.s, opacity: p.o }} />
                ))}
                <span className="msel-thumb" aria-hidden="true"
                  style={{ left: `calc((100% - ${THUMB_PX}px) * ${effortIdx / (EFFORTS.length - 1)})` }} />
              </div>
            </>
          )}
        </div>,
        document.body,
      )}
    </div>
  )
}
