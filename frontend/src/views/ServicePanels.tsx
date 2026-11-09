/**
 * 服务面板：项目与理解 / 参考论文 / 排版样式 / 图表管理 / AI 检测中心 / 知识库。
 * 承载主页之外的全部后端能力。
 */
import { useEffect, useRef, useState } from 'react'
import { Button, Input, MarkdownText } from '@dsh/primitives'
import type { MarkdownLabels } from '@dsh/primitives'
import { api, download, sse, upload } from '../api'
import { refreshKb, setCurrentProject, toast, useStore } from '../store'
import { Icon } from '../components/Icon'
import { confirmDialog } from '../components/confirm'
import { Select } from '../components/Select'
import { CreateProjectModal } from '../components/CreateProjectModal'

const MD_LABELS: MarkdownLabels = { code: { copyLabel: '复制', copiedLabel: '已复制' }, footnotes: '脚注' }

interface UnderstandQuestion { id: string; type?: string; title?: string; detail?: string; options?: string[] }

const UNDERSTAND_LABELS: Record<string, string> = {
  idle: '未开始', analyzing: '通读分析中…', awaiting_answers: '待确认',
  finalizing: '生成《研究项目主体》中…', analyzed: '分析完成', done: '已完成', failed: '失败',
}
const ENRICH_LABELS: Record<string, string> = { none: '未开始', running: '进行中', done: '已完成', failed: '失败' }

// ---------- 项目与理解 ----------
export function ProjectsPanel() {
  const active = useStore(s => s.currentProject)
  const [registered, setRegistered] = useState<{ name: string; path?: string; methods_count?: number; has_outline?: boolean }[]>([])
  const [createOpen, setCreateOpen] = useState(false)
  const [ust, setUst] = useState<Record<string, unknown> | null>(null)
  const [est, setEst] = useState<Record<string, unknown> | null>(null)
  const [tree, setTree] = useState<string>('')
  const [body, setBody] = useState('')
  const [showBody, setShowBody] = useState(false)
  const [ansText, setAnsText] = useState<Record<string, string>>({})
  const [ansPick, setAnsPick] = useState<Record<string, string[]>>({})
  const pollRef = useRef<number | null>(null)
  const enrichPollRef = useRef<number | null>(null)
  const sel = active ?? ''
  const selPath = registered.find(p => p.name === sel)?.path ?? ''
  const ustate = String(ust?.state ?? '')
  const questions: UnderstandQuestion[] = Array.isArray(ust?.questions) ? ust!.questions as UnderstandQuestion[] : []

  function stopPoll() {
    if (pollRef.current !== null) { window.clearInterval(pollRef.current); pollRef.current = null }
    if (enrichPollRef.current !== null) { window.clearInterval(enrichPollRef.current); enrichPollRef.current = null }
  }
  useEffect(() => () => stopPoll(), [])

  async function refresh() {
    try {
      const r = await api<{ registered: { name: string; path?: string; methods_count?: number; has_outline?: boolean }[] }>('/api/projects')
      setRegistered(r.registered ?? [])
    } catch (e) { toast((e as Error).message, 'err') }
  }
  useEffect(() => { void refresh() }, [])

  useEffect(() => {
    stopPoll()
    setUst(null); setBody(''); setShowBody(false); setAnsText({}); setAnsPick({})
    if (!sel) return
    void understandStatus()
    void loadBody()
  }, [sel])

  async function onProjectCreated(project: { name: string; path: string }) {
    await refresh()
    setCurrentProject(project.name)   // 新项目直接切换为当前项目
  }

  async function understandStart() {
    if (!sel) return
    if (!selPath) { toast('未找到项目路径，请先点「刷新状态」或重新注册项目', 'err'); return }
    try {
      setUst({ state: 'analyzing' })
      await api('/api/project/understand/start', {
        method: 'POST', body: JSON.stringify({ name: sel, path: selPath }),
      })
      toast('项目理解已启动（约 1-3 分钟）', 'ok')
      startPoll()
    } catch (e) {
      setUst(null)
      toast(`启动失败：${(e as Error).message}`, 'err')
    }
  }
  async function understandStatus(): Promise<Record<string, unknown> | null> {
    if (!sel) return null
    try {
      const st = await api<Record<string, unknown>>(`/api/project/understand/status?project_name=${encodeURIComponent(sel)}`)
      setUst(st)
      return st
    } catch (e) {
      setUst({ state: 'failed', error: (e as Error).message })
      return null
    }
  }
  async function loadBody(autoShow = false) {
    if (!sel) return
    try {
      const r = await api<{ exists?: boolean; body?: string }>(`/api/project/body?project_name=${encodeURIComponent(sel)}`)
      setBody(r.body ?? '')
      if (autoShow && r.body) setShowBody(true)
    } catch { setBody('') }
  }
  function startPoll() {
    stopPoll()
    pollRef.current = window.setInterval(async () => {
      const st = await understandStatus()
      const state = String(st?.state ?? '')
      if (state === '' || ['idle', 'analyzing', 'finalizing'].includes(state)) return
      stopPoll()
      if (state === 'analyzed' || state === 'done') void loadBody(true)
    }, 2500)
  }
  async function submitAnswers(skip: boolean) {
    if (!sel) return
    const answers = questions.map(q => q.type === 'text'
      ? { id: q.id, text: skip ? '' : (ansText[q.id] ?? '') }
      : { id: q.id, choices: skip ? [] : (ansPick[q.id] ?? []) })
    try {
      await api('/api/project/understand/answers', {
        method: 'POST', body: JSON.stringify({ project_name: sel, answers }),
      })
      toast('已提交，正在生成《研究项目主体》…', 'ok')
      setUst({ state: 'finalizing' })
      startPoll()
    } catch (e) { toast(`提交失败：${(e as Error).message}`, 'err') }
  }
  function startEnrichPoll() {
    if (enrichPollRef.current !== null) window.clearInterval(enrichPollRef.current)
    enrichPollRef.current = window.setInterval(async () => {
      try {
        const st = await api<Record<string, unknown>>('/api/profile/enrich/status')
        setEst(st)
        if (String(st['state'] ?? '') !== 'running') {
          if (enrichPollRef.current !== null) { window.clearInterval(enrichPollRef.current); enrichPollRef.current = null }
          if (String(st['state'] ?? '') === 'done') toast('深化画像完成', 'ok')
        }
      } catch { /* 轮询失败忽略，下轮再试 */ }
    }, 2500)
  }
  async function enrich() {
    if (!sel) return
    try {
      const r = await api<{ ok?: boolean; state?: string; message?: string; total?: number }>(
        `/api/profile/enrich?project_name=${encodeURIComponent(sel)}`, { method: 'POST' })
      if (r.state === 'done' && !r.total) { toast(r.message ?? '全部图片已有描述，无需深化', 'ok'); return }
      toast('深化画像已启动（后台并发执行）', 'ok')
      setEst({ state: 'running', done: 0, total: r.total ?? 0 })
      startEnrichPoll()
    } catch (e) { toast((e as Error).message, 'err') }
  }
  async function loadTree() {
    if (!sel) return
    try {
      const r = await api<{ tree?: unknown }>(`/api/projects/${encodeURIComponent(sel)}/tree`)
      setTree(JSON.stringify(r.tree ?? r, null, 2).slice(0, 8000))
    } catch (e) { toast((e as Error).message, 'err') }
  }

  return (
    <section className="sp-section">
      <h2 className="sp-h1">项目与理解</h2>
      <div className="sp-h2sub">当前工作区：{sel || '未确定工作区'}</div>

      <div className="set-card">
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">研究项目</div>
            <div className="sp-desc">选择本地文件夹创建项目，名称与目录地址保存在账号中</div></div>
          <div className="sp-ctl"><Button variant="primary" onClick={() => setCreateOpen(true)}>创建项目</Button></div>
        </div>
      </div>

      <div className="set-card">
        <div className="sp-row column"><div className="sp-label">已注册项目（点击切换为当前项目）</div></div>
        {registered.map(p => (
          <div key={p.name} className={`sp-row pick proj-item${p.name === active ? ' on' : ''}`} onClick={() => setCurrentProject(p.name)}>
            <div className="sp-txt"><div className="sp-label">{p.name}{p.name === active && <span className="on-mark"><Icon name="check" size={12} />当前</span>}</div>
              <div className="sp-desc">{p.path}{p.has_outline ? ' · 有大纲' : ''}</div></div>
            {p.name === active && (
              <button className="proj-x" title="退出当前项目"
                onClick={e => { e.stopPropagation(); setCurrentProject(null) }}><Icon name="close" size={12} /></button>
            )}
          </div>
        ))}
        {registered.length === 0 && <div className="sp-row"><span className="sp-desc">暂无项目（点击「创建项目」或对话中让墨记创建）</span></div>}
      </div>

      <CreateProjectModal open={createOpen} onClose={() => setCreateOpen(false)} onCreated={p => void onProjectCreated(p)} />

      {sel && (
        <div className="set-card">
          <div className="sp-row column">
            <div className="sp-label">项目理解 Agent（通读项目 → 理清研究顺序 → 生成《研究项目主体》注入写作）</div>
            <div className="inline-form">
              <Button size="sm" variant="primary" disabled={ustate === 'analyzing' || ustate === 'finalizing'}
                onClick={() => void understandStart()}>
                {ustate === 'analyzing' || ustate === 'finalizing' ? '理解进行中…' : (body ? '重新理解' : '理解项目')}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void understandStatus()}>刷新状态</Button>
              <Button size="sm" variant="ghost" onClick={() => void enrich()}><Icon name="flask" />深化画像</Button>
              <Button size="sm" variant="ghost" onClick={() => void loadTree()}><Icon name="folder-tree" />文件树</Button>
              <Button size="sm" variant="ghost" onClick={() => void api('/api/profile/enrich/status').then(setEst).catch(() => setEst(null))}>画像进度</Button>
              {body && (
                <Button size="sm" variant="ghost" onClick={() => setShowBody(v => !v)}>
                  {showBody ? '收起项目主体' : '查看项目主体'}
                </Button>
              )}
            </div>

            {ust && (
              <div className="und-status">
                <span className={`sp-chip${ustate === 'failed' ? ' und-bad' : ''}`}>
                  状态：{UNDERSTAND_LABELS[ustate] ?? (ustate || '未知')}
                </span>
                {(ustate === 'analyzing' || ustate === 'finalizing') && (
                  <div className="und-progress">
                    <div className="und-progress-bar">
                      <div className="und-progress-fill"
                        style={{ width: `${Math.min(100, Math.max(3, Number(ust['progress'] ?? 0)))}%` }} />
                    </div>
                    <span className="und-progress-text">
                      {String(ust['stage_label'] ?? '')} {Number(ust['progress'] ?? 0)}%
                    </span>
                  </div>
                )}
                {ust['error'] ? <span className="und-err">{String(ust['error'])}</span> : null}
                {est && (
                  <span className={`sp-chip${String(est['state']) === 'failed' ? ' und-bad' : ''}`}>
                    深化画像：{ENRICH_LABELS[String(est['state'] ?? '')] ?? String(est['state'] ?? '')}
                    {String(est['state']) === 'running' ? ` ${Number(est['done'] ?? 0)}/${Number(est['total'] ?? 0)}` : ''}
                  </span>
                )}
              </div>
            )}

            {ustate === 'awaiting_answers' && (
              <div className="und-questions">
                <div className="sp-desc">项目已通读完毕，以下 {questions.length} 处需要你拍板；回答后生成《研究项目主体》。</div>
                {questions.map((q, i) => (
                  <div className="und-q" key={q.id || i}>
                    <div className="und-q-title">{i + 1}. {q.title || q.id}</div>
                    {q.detail ? <div className="sp-desc">{q.detail}</div> : null}
                    {q.type === 'text' ? (
                      <textarea className="und-text" rows={3} placeholder="补充说明（可留空）"
                        value={ansText[q.id] ?? ''}
                        onChange={e => setAnsText(m => ({ ...m, [q.id]: e.target.value }))} />
                    ) : (
                      <div className="und-opts">
                        {(q.options ?? []).map(o => {
                          const picked = (ansPick[q.id] ?? []).includes(o)
                          return (
                            <label className="und-opt" key={o}>
                              <input type={q.type === 'multi' ? 'checkbox' : 'radio'} name={`uq-${q.id}`}
                                checked={picked}
                                onChange={e => setAnsPick(m => {
                                  const cur = m[q.id] ?? []
                                  const next = q.type === 'multi'
                                    ? (e.target.checked ? [...cur, o] : cur.filter(x => x !== o))
                                    : [o]
                                  return { ...m, [q.id]: next }
                                })} />
                              <span>{o}</span>
                            </label>
                          )
                        })}
                      </div>
                    )}
                  </div>
                ))}
                <div className="inline-form">
                  <Button size="sm" variant="primary" onClick={() => void submitAnswers(false)}>提交并生成</Button>
                  <Button size="sm" variant="ghost" onClick={() => void submitAnswers(true)}>跳过（按推测生成）</Button>
                </div>
              </div>
            )}

            {showBody && body && (
              <div className="und-body-doc"><MarkdownText text={body} labels={MD_LABELS} /></div>
            )}
            {tree && <pre className="json-view">{tree}</pre>}
          </div>
        </div>
      )}
    </section>
  )
}

// ---------- 参考论文 ----------
export function ReferencePanel() {
  const active = useStore(s => s.currentProject)
  const [papers, setPapers] = useState<{ name: string; parsed?: boolean }[]>([])
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement | null>(null)

  async function refresh() {
    try { setPapers((await api<{ papers: { name: string; parsed?: boolean }[] }>('/api/reference/list')).papers ?? []) }
    catch { setPapers([]) }
  }
  useEffect(() => { void refresh() }, [])

  async function doUpload(file: File) {
    setBusy(true)
    toast(`上传解析中（${file.name}，约 10-30 秒）…`)
    try {
      await upload('/api/reference/upload', file)
      toast('参考论文已上传并解析', 'ok')
      void refresh()
    } catch (e) { toast((e as Error).message, 'err') }
    finally { setBusy(false) }
  }

  return (
    <section className="sp-section">
      <h2 className="sp-h1">参考论文</h2>
      <div className="sp-h2sub">上传他人发表论文（pdf/docx/txt）自动解析章节结构与写作骨架，供生成大纲借鉴</div>
      <div className="set-card">
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">上传参考论文</div>
            <div className="sp-desc">走主模型解析；上传后自动提取格式模板与骨架</div></div>
          <div className="sp-ctl">
            <input ref={fileRef} type="file" accept=".pdf,.docx,.txt" hidden
              onChange={e => { const f = e.target.files?.[0]; if (f) void doUpload(f); e.target.value = '' }} />
            <Button size="sm" variant="primary" disabled={busy} onClick={() => fileRef.current?.click()}><Icon name="upload" />上传</Button>
          </div>
        </div>
        {papers.map(p => (
          <div key={p.name} className="sp-row">
            <div className="sp-txt"><div className="sp-label">{p.name}</div>
              <div className="sp-desc">{p.parsed ? '已解析（可在生成大纲时选用）' : '未解析'}</div></div>
            <div className="sp-ctl">
              <button className="link-btn" onClick={() => {
                void api(`/api/reference/${encodeURIComponent(p.name)}/skeleton/extract`, { method: 'POST' })
                  .then(() => { toast('骨架提取完成', 'ok'); void refresh() })
                  .catch(e => toast((e as Error).message, 'err'))
              }}>提取骨架</button>
            </div>
          </div>
        ))}
        {papers.length === 0 && <div className="sp-row"><span className="sp-desc">暂无参考论文</span></div>}
      </div>
    </section>
  )
}

// ---------- 排版样式 ----------
export function StylePanel() {
  const active = useStore(s => s.currentProject)
  const [style, setStyle] = useState<Record<string, unknown> | null>(null)
  const [refs, setRefs] = useState<{ name: string }[]>([])
  const [refName, setRefName] = useState('')

  async function refresh() {
    try { setStyle(await api<Record<string, unknown>>('/api/export/style')) } catch { setStyle(null) }
    try {
      const papers = (await api<{ papers: { name: string }[] }>('/api/reference/list')).papers ?? []
      setRefs(papers)
      setRefName(prev => prev || papers[0]?.name || '')
    } catch { /* */ }
  }
  useEffect(() => { void refresh() }, [])

  return (
    <section className="sp-section">
      <h2 className="sp-h1">排版样式</h2>
      <div className="sp-h2sub">把参考论文的排版参数编译为导出样式（白名单制）· 当前工作区：{active || '未确定工作区'}</div>
      <div className="set-card">
        {style && <div className="sp-row column">
          <div className="sp-label">当前导出样式</div>
          <pre className="json-view">{JSON.stringify(style, null, 2)}</pre>
        </div>}
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">套入参考论文排版</div>
            <div className="sp-desc">从已解析参考论文提取字体/字号/行距/图表/页面参数，导出 docx/pdf 自动生效</div></div>
          <div className="sp-ctl">
            <Select className="sel-w" value={refName} disabled={refs.length === 0}
              placeholder="暂无参考论文（请先到「参考论文」上传）"
              options={refs.map(r => ({ value: r.name, label: r.name }))}
              onChange={setRefName} />
            <Button size="sm" variant="primary" disabled={!active || refs.length === 0} onClick={() => {
              const rn = refName
              if (!active || !rn) return
              void api('/api/export/style/apply', { method: 'POST', body: JSON.stringify({ project_name: active, reference_name: rn }) })
                .then(() => { toast('排版已套入', 'ok'); void refresh() })
                .catch(e => toast((e as Error).message, 'err'))
            }}>一键套入</Button>
            <Button size="sm" variant="ghost" disabled={!active} onClick={() => {
              void api('/api/export/style/reset', { method: 'POST', body: JSON.stringify({ project_name: active }) })
                .then(() => { toast('已恢复默认排版', 'ok'); void refresh() })
                .catch(e => toast((e as Error).message, 'err'))
            }}>撤销</Button>
          </div>
        </div>
      </div>
    </section>
  )
}

// ---------- 图表管理 ----------
export function FiguresPanel() {
  const active = useStore(s => s.currentProject)
  const [figures, setFigures] = useState<{ path?: string; section?: string; old_path?: string; section_id?: string }[]>([])
  const fileRef = useRef<HTMLInputElement | null>(null)
  const [target, setTarget] = useState<{ old_path?: string; section_id?: string } | null>(null)

  async function refresh() {
    if (!active) return
    try {
      const r = await api<{ figures?: { path?: string; section?: string; old_path?: string; section_id?: string }[] }>(
        `/api/figures/list?project_name=${encodeURIComponent(active)}`)
      setFigures(r.figures ?? [])
    } catch { setFigures([]) }
  }
  useEffect(() => { void refresh() }, [active])

  return (
    <section className="sp-section">
      <h2 className="sp-h1">图表管理</h2>
      <div className="sp-h2sub">论文正文引用的图片清单 · 上传新图替换（正文路径自动改写）· 项目：{active || '未确定工作区'}</div>
      <div className="set-card">
        {figures.length === 0 && <div className="sp-row"><span className="sp-desc">当前项目暂无图表引用（生成正文后自动收集）</span></div>}
        {figures.map((f, i) => (
          <div key={i} className="sp-row">
            <div className="sp-txt"><div className="sp-label">{f.path ?? f.old_path}</div>
              <div className="sp-desc">所属：{f.section ?? f.section_id ?? '—'}</div></div>
            <div className="sp-ctl">
              <input ref={target === f ? fileRef : undefined} type="file" accept="image/*" hidden
                onChange={e => {
                  const file = e.target.files?.[0]
                  e.target.value = ''
                  if (!file || !active || !f.old_path || !f.section_id) return
                  const fd = new FormData()
                  fd.append('file', file)
                  fd.append('project_name', active)
                  fd.append('section_id', f.section_id)
                  fd.append('old_path', f.old_path)
                  void fetch('/api/figures/upload', { method: 'POST', body: fd }).then(async r => {
                    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`)
                    toast('图片已替换', 'ok'); void refresh()
                  }).catch(err => toast((err as Error).message, 'err'))
                }} />
              <Button size="sm" variant="ghost" disabled={!f.old_path || !f.section_id}
                onClick={() => { setTarget(f); setTimeout(() => fileRef.current?.click(), 50) }}>上传替换</Button>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

// ---------- AI 检测中心 ----------
interface DocInfo {
  id: string; filename: string; paras: number; chars: number
  previews: string[]; para_stats: { chars: number; rewritable: boolean }[]
  marks?: { para?: number }[]; history?: unknown[]
}
interface DetectReport { overall?: Record<string, unknown>; paras?: Record<string, unknown>[]; [k: string]: unknown }

export function AicPanel() {
  const active = useStore(s => s.currentProject)
  const [fullReport, setFullReport] = useState<Record<string, unknown> | null>(null)
  const [doc, setDoc] = useState<DocInfo | null>(null)
  const [report, setReport] = useState<DetectReport | null>(null)
  const [verify, setVerify] = useState<Record<string, unknown> | null>(null)
  const [picked, setPicked] = useState<number[]>([])
  const [busy, setBusy] = useState('')
  const uploadRef = useRef<HTMLInputElement | null>(null)
  const reportRef = useRef<HTMLInputElement | null>(null)

  async function fullCheck() {
    if (!active) { toast('未确定工作区', 'err'); return }
    setBusy('full')
    try { setFullReport(await api(`/api/aicenter/full?project_name=${encodeURIComponent(active)}`)) }
    catch (e) { toast((e as Error).message, 'err') }
    finally { setBusy('') }
  }

  async function doUpload(file: File, mode: 'doc' | 'report') {
    setBusy(mode)
    try {
      const q = mode === 'report' && active ? `?project_name=${encodeURIComponent(active)}` : ''
      const r = await upload<{ doc: DocInfo; verify?: Record<string, unknown> }>(
        mode === 'doc' ? '/api/doccheck/upload' : '/api/doccheck/import-report', file, q)
      setDoc(r.doc)
      setReport(null)
      setPicked([])
      setVerify(r.verify ?? null)
      if (mode === 'report') toast('报告已导入（已做论文归属核验）', 'ok')
    } catch (e) { toast((e as Error).message, 'err') }
    finally { setBusy('') }
  }

  async function detect() {
    if (!doc) return
    setBusy('detect')
    try {
      const r = await api<{ report: DetectReport; doc: DocInfo }>('/api/doccheck/detect',
        { method: 'POST', body: JSON.stringify({ doc_id: doc.id }) })
      setReport(r.report); setDoc(r.doc)
      toast('检测完成', 'ok')
    } catch (e) { toast((e as Error).message, 'err') }
    finally { setBusy('') }
  }

  async function rewrite() {
    if (!doc || !picked.length) { toast('请先勾选要改写的段落', 'err'); return }
    setBusy('rewrite')
    let status = '改写中…'
    const tick = setInterval(() => toast(status), 8000)
    try {
      await sse('/api/doccheck/rewrite', { doc_id: doc.id, paras: picked }, ev => {
        const type = ev.type as string
        if (type === 'status') status = String(ev.content ?? '')
        if (type === 'recheck') { setReport(ev.report as DetectReport); status = '复检完成' }
        if (type === 'done') { setDoc(ev.doc as DocInfo); setPicked([]) }
        if (type === 'error') toast(String(ev.content ?? '改写失败'), 'err')
      })
      toast('改写完成并已复检', 'ok')
    } catch (e) { toast((e as Error).message, 'err') }
    finally { clearInterval(tick); setBusy('') }
  }

  async function applyToPaper() {
    if (!doc || !active) return
    const ok = await confirmDialog({
      title: '回写论文',
      description: '把改写后的段落回写当前论文对应位置。',
      confirmLabel: '回写',
    })
    if (!ok) return
    setBusy('apply')
    try {
      await api('/api/doccheck/apply-to-paper', { method: 'POST', body: JSON.stringify({ doc_id: doc.id, project_name: active }) })
      toast('已回写论文', 'ok')
    } catch (e) { toast((e as Error).message, 'err') }
    finally { setBusy('') }
  }

  const marked = new Set((doc?.marks ?? []).map(m => m.para).filter(p => p !== undefined) as number[])

  return (
    <section className="sp-section">
      <h2 className="sp-h1">AI 检测中心</h2>
      <div className="sp-h2sub">朱雀官方 API + 本地 BERT 双引擎 · 9B 改写复检 · 报告导入回写 · 项目：{active || '未确定工作区'}</div>

      <div className="set-card">
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">整篇检测</div>
            <div className="sp-desc">当前论文全文送检（约 20-90 秒），三引擎合并报告</div></div>
          <div className="sp-ctl">
            <Button size="sm" variant="primary" disabled={!active || busy !== ''} onClick={() => void fullCheck()}>{busy === 'full' ? '检测中…' : '整篇检测'}</Button>
          </div>
        </div>
        {fullReport && (
          <div className="sp-row column"><div className="sp-label">整篇报告</div>
            <pre className="json-view">{JSON.stringify(fullReport, null, 2)}</pre></div>
        )}
      </div>

      <div className="set-card">
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">上传文档检测</div>
            <div className="sp-desc">word/txt/md/pdf 上传 → 三引擎查重 → 勾选段落 9B 改写 → 下载/回写</div></div>
          <div className="sp-ctl">
            <input ref={uploadRef} type="file" accept=".docx,.txt,.md,.pdf" hidden
              onChange={e => { const f = e.target.files?.[0]; if (f) void doUpload(f, 'doc'); e.target.value = '' }} />
            <Button size="sm" variant="primary" disabled={busy !== ''} onClick={() => uploadRef.current?.click()}>{busy === 'doc' ? '上传中…' : '上传文档'}</Button>
          </div>
        </div>
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">导入检测报告</div>
            <div className="sp-desc">外部 AI 检测报告（含朱雀 PDF）：先核验论文归属，再提取 AI 段落改写回写</div></div>
          <div className="sp-ctl">
            <input ref={reportRef} type="file" accept=".docx,.txt,.md,.pdf" hidden
              onChange={e => { const f = e.target.files?.[0]; if (f) void doUpload(f, 'report'); e.target.value = '' }} />
            <Button size="sm" variant="primary" disabled={busy !== ''} onClick={() => reportRef.current?.click()}>{busy === 'report' ? '导入中…' : '导入报告'}</Button>
          </div>
        </div>
      </div>

      {doc && (
        <div className="set-card">
          <div className="sp-row column">
            <div className="sp-label">{doc.filename} · {doc.paras} 段 / {doc.chars} 字
              {verify && <span className="sp-chip">核验 {String((verify as { score?: number }).score ?? '?')}</span>}</div>
            <div className="inline-form">
              <Button size="sm" variant="primary" disabled={busy !== ''} onClick={() => void detect()}>{busy === 'detect' ? '检测中（约1分钟）…' : '检测'}</Button>
              <Button size="sm" variant="primary" disabled={busy !== '' || !picked.length} onClick={() => void rewrite()}>{busy === 'rewrite' ? '改写中…' : `改写选中 ${picked.length} 段`}</Button>
              <button className="link-btn" onClick={() => download(`/api/doccheck/doc/${doc.id}/download`)}><Icon name="download" size={12} />下载改写文档</button>
              {active && <button className="link-btn" disabled={busy !== ''} onClick={() => void applyToPaper()}><Icon name="corner-up-left" size={12} />回写论文</button>}
            </div>
          </div>
          {report && (
            <div className="sp-row column"><div className="sp-label">检测报告</div>
              <pre className="json-view">{JSON.stringify(report, null, 2).slice(0, 4000)}</pre></div>
          )}
          <div className="para-list">
            {doc.previews.map((p, i) => (
              <label key={i} className={`para-row${marked.has(i) ? ' marked' : ''}${picked.includes(i) ? ' picked' : ''}`}>
                <input type="checkbox" checked={picked.includes(i)}
                  disabled={!doc.para_stats[i]?.rewritable}
                  onChange={e => setPicked(p => e.target.checked ? [...p, i] : p.filter(x => x !== i))} />
                <span className="para-idx">{i + 1}</span>
                <span className="para-text">{p}</span>
                {marked.has(i) && <span className="para-mark">AI 标记</span>}
              </label>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

// ---------- 知识库 ----------
export function KbPanel() {
  const kb = useStore(s => s.kb)
  const [q, setQ] = useState('')
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => { void refreshKb() }, [])

  async function build(which: string) {
    toast(`构建 ${which} 中…（后台执行）`)
    try { await api(`/api/kb/build/${which}`, { method: 'POST' }); await refreshKb(); toast('已更新', 'ok') }
    catch (e) { toast((e as Error).message, 'err') }
  }
  async function ingest() {
    setBusy(true)
    try { await api('/api/rag/ingest', { method: 'POST', body: JSON.stringify({}) }); toast('批量入库已启动', 'ok') }
    catch (e) { toast((e as Error).message, 'err') }
    finally { setBusy(false) }
  }
  async function query() {
    if (!q.trim()) return
    setBusy(true); setAnswer('')
    try {
      const r = await api<{ answer?: string; chunks?: unknown[] }>('/api/rag/query', {
        method: 'POST', body: JSON.stringify({ question: q.trim() }),
      })
      setAnswer(r.answer ?? JSON.stringify(r).slice(0, 2000))
    } catch (e) { toast((e as Error).message, 'err') }
    finally { setBusy(false) }
  }

  return (
    <section className="sp-section">
      <h2 className="sp-h1">知识库</h2>
      <div className="sp-h2sub">方法库 / 写作教学库（启动自动加载）· RAG-Anything 多模态文献库</div>
      <div className="set-card">
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">方法原论文库</div>
            <div className="sp-desc">注册表新增方法/文献后更新</div></div>
          <div className="sp-ctl"><span className="sp-chip">{kb?.method_papers?.document_count ?? '-'} 条</span>
            <Button size="sm" variant="ghost" onClick={() => void build('methods')}>更新</Button></div>
        </div>
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">写作教学库</div>
            <div className="sp-desc">补充新教材/字幕后更新</div></div>
          <div className="sp-ctl"><span className="sp-chip">{kb?.writing_methodology?.document_count ?? '-'} 条</span>
            <Button size="sm" variant="ghost" onClick={() => void build('writing')}>更新</Button></div>
        </div>
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">多模态文献库（RAG）</div>
            <div className="sp-desc">reference_papers 下 PDF 的图/表/公式解析入库；上传参考文献时已自动入库</div></div>
          <div className="sp-ctl">
            <Button size="sm" variant="primary" disabled={busy} onClick={() => void ingest()}>批量入库</Button>
          </div>
        </div>
        <div className="sp-row column">
          <div className="sp-label">知识库问答（RAG 检索测试）</div>
          <div className="inline-form">
            <Input className="sp-input wide" placeholder="例如：DAG 先验是怎么构建的？" value={q} onChange={e => setQ(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') void query() }} />
            <Button size="sm" variant="primary" disabled={busy || !q.trim()} onClick={() => void query()}>检索</Button>
          </div>
          {answer && <div className="rag-answer">{answer}</div>}
        </div>
      </div>
    </section>
  )
}
