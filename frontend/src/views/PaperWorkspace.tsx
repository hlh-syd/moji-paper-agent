/**
 * 论文工作区（主页右栏）：论文/大纲【全量】展示 + 全链路操作。
 * 全量 = 所有章节与小节完整渲染（MarkdownText），无截断。
 * 操作：生成大纲（模板/字数/参考/领域）、一键全文+进度、逐节（重生成 SSE/润色/去AI味/体检/编辑/改名/恢复）、
 *       导出 md|docx|pdf、标题改名、自查报告、清空。
 */
import { useEffect, useMemo, useState } from 'react'
import { Button, Modal, SegmentedTabs, StateDot } from '@dsh/primitives'
import type { MarkdownLabels } from '@dsh/primitives'
import { MarkdownText } from '@dsh/primitives'
import { api, AUTH, download, sse } from '../api'
import type { Outline, OutlineSection } from '../api'
import { flatten, openSettings, pollFulltext, refreshOutline, set, toast, useStore } from '../store'
import { Icon } from '../components/Icon'
import { confirmDialog } from '../components/confirm'
import { GenerateOutlineModal } from './GenerateOutlineModal'

const MD_LABELS: MarkdownLabels = { code: { copyLabel: '复制', copiedLabel: '已复制' }, footnotes: '脚注' }

/** 正文里的相对路径图片引用 → /api/file/raw?path=<项目根/相对路径>（真图直显；RBAC 下带 token）。
 *  必须输出绝对 URL：DSH MarkdownText 的安全过滤只放行 http(s)。 */
function withImages(md: string | undefined, root?: string): string {
  if (!md || !root) return md ?? ''
  const base = root.replace(/[\\/]+$/, '')
  const token = AUTH.token ? `&token=${encodeURIComponent(AUTH.token)}` : ''
  return md.replace(/(!\[[^\]]*\]\()\s*([^)\s]+)\s*(\))/g, (whole, head: string, src: string, tail: string) => {
    if (/^(https?:|data:)/i.test(src)) return whole
    const abs = /^[a-zA-Z]:[\\/]/.test(src) || src.startsWith('/') ? src : `${base}/${src}`
    return `${head}${location.origin}/api/file/raw?path=${encodeURIComponent(abs)}${token}${tail}`
  })
}

/** 论文/大纲视图切换条：工作区顶栏与设置页右栏共用同一状态。 */
export function PaperViewSwitch() {
  const paperTab = useStore(s => s.paperTab)
  return (
    <SegmentedTabs
      size="sm"
      label="论文工作区视图"
      items={[
        { value: 'paper', label: <span className="tab-label"><Icon name="file-text" size={12} />论文</span>, id: 't-paper', panelId: 'p-paper' },
        { value: 'outline', label: <span className="tab-label"><Icon name="list" size={12} />大纲</span>, id: 't-outline', panelId: 'p-outline' },
      ]}
      value={paperTab}
      onChange={v => set({ paperTab: v as 'paper' | 'outline' })}
    />
  )
}

/** 全文生成进度（顶栏紧凑版）：进行中显示在「论文/大纲」切换条左侧。 */
export function FulltextProgress() {
  const ft = useStore(s => s.fulltext)
  if (!ft?.active) return null
  const done = ft.current ?? 0
  const total = ft.total ?? 0
  const pct = total ? Math.round((done / total) * 100) : 0
  return (
    <div className="ftp" title={`全文生成中：${ft.current_title ?? ''}${ft.words ? `（已 ${ft.words} 字）` : ''}`}>
      <span className="ftp-label"><Icon name="play" size={12} />全文</span>
      <div className="ftp-bar"><div className="ftp-fill" style={{ width: `${pct}%` }} /></div>
      <span className="ftp-count">{done}/{total}</span>
    </div>
  )
}

export function PaperWorkspace() {
  const project = useStore(s => s.currentProject)
  const outline = useStore(s => s.outline)
  const loading = useStore(s => s.outlineLoading)
  const fulltext = useStore(s => s.fulltext)
  const tab = useStore(s => s.paperTab)
  const [genOpen, setGenOpen] = useState(false)
  const [review, setReview] = useState<string | null>(null)

  useEffect(() => { if (project) void refreshOutline() }, [project])

  const flat = useMemo(() => flatten(outline?.sections ?? []), [outline])
  const doneCount = flat.filter(s => (s.content ?? '').trim()).length

  async function startFulltext(force: boolean) {
    if (!project) return
    const ok = await confirmDialog({
      title: force ? '强制重做全部章节' : '断点续写',
      description: force ? '已完成正文将被覆盖。' : '从未完成章节继续生成正文。',
      confirmLabel: '开始',
    })
    if (!ok) return
    try {
      await api('/api/fulltext/start', { method: 'POST', body: JSON.stringify({ project_name: project, force }) })
      toast('全文任务已启动', 'ok')
      void pollFulltext()
    } catch (e) { toast(`启动失败：${(e as Error).message}`, 'err') }
  }

  async function cancelFulltext() {
    try { await api('/api/fulltext/cancel', { method: 'POST' }); toast('已请求取消', 'ok') }
    catch (e) { toast((e as Error).message, 'err') }
  }

  async function selfReview() {
    if (!project) return
    toast('自查进行中（约 1-3 分钟）…')
    try {
      const r = await api<{ report: string }>('/api/sections/self_review', {
        method: 'POST', body: JSON.stringify({ project_name: project }),
      })
      setReview(r.report)
    } catch (e) { toast(`自查失败：${(e as Error).message}`, 'err') }
  }

  async function clearOutline(mode: 'content' | 'full') {
    if (!project) return
    const ok = await confirmDialog({
      title: mode === 'full' ? '删除大纲' : '清空正文',
      description: mode === 'full' ? '大纲将回到未生成状态，全部结构一并删除。' : '仅清空全部正文，大纲结构保留。',
      confirmLabel: mode === 'full' ? '删除' : '清空',
    })
    if (!ok) return
    try {
      await api(`/api/outline/${encodeURIComponent(project)}?mode=${mode}`, { method: 'DELETE' })
      toast('已清空', 'ok')
      void refreshOutline()
    } catch (e) { toast((e as Error).message, 'err') }
  }

  if (!project) {
    return (
      <div className="paper-ws">
        <div className="ws-empty">
          <div className="ws-empty-ico"><Icon name="file-text" size={32} /></div>
          <div className="ws-empty-title">未确定工作区</div>
          <div className="ws-empty-sub">在对话区让墨记注册并选择项目，或到设置 → 项目与文件 管理</div>
          <Button variant="ghost" size="sm" onClick={() => openSettings('projects')}>打开项目设置</Button>
        </div>
      </div>
    )
  }

  const done = fulltext?.current ?? 0
  const total = fulltext?.total ?? flat.length

  return (
    <div className="paper-ws">
      <header className="ws-head">
        <span className="grow" />
        <Button variant="ghost" size="sm" title="选择/生成大纲参数"
          onClick={() => setGenOpen(true)}><Icon name="wand" />生成大纲</Button>
        <Button variant="primary" size="sm"
          disabled={!!fulltext?.active || !outline?.sections?.length}
          title={outline?.sections?.length ? '逐节生成全部章节，每节完成自动体检并按阈值去AI味' : '请先生成大纲'}
          onClick={() => void startFulltext(false)}
        ><Icon name="play" />一键全文</Button>
        {outline?.sections?.length ? (
          <>
            <Button variant="ghost" size="sm" title="覆盖重做全部" disabled={!!fulltext?.active} onClick={() => void startFulltext(true)}><Icon name="refresh" /></Button>
            <Button variant="ghost" size="sm" title="导出论文（Word）" onClick={() => download(`/api/outline/export/docx?project_name=${encodeURIComponent(project)}`)}><Icon name="download" />Word</Button>
            <Button variant="ghost" size="sm" title="导出 PDF" onClick={() => download(`/api/outline/export/pdf?project_name=${encodeURIComponent(project)}`)}>PDF</Button>
            <Button variant="ghost" size="sm" title="导出 Markdown" onClick={() => download(`/api/outline/export/md?project_name=${encodeURIComponent(project)}`)}>MD</Button>
            <Button variant="ghost" size="sm" title="全文自查报告" onClick={() => void selfReview()}><Icon name="activity" /></Button>
            <Button variant="ghost" size="sm" title="清空正文（保留大纲）" onClick={() => void clearOutline('content')}><Icon name="trash" /></Button>
          </>
        ) : null}
      </header>

      {fulltext?.active && (
        <div className="ft-strip">
          <div className="ft-head">
            <StateDot state="ongoing" />
            <span className="ft-count">{done}/{total} 节</span>
            <span className="ft-words">{fulltext.words ?? 0} 字</span>
            <span className="ft-now">{fulltext.current_title ?? ''}</span>
            <span className="grow" />
            <Button size="sm" variant="ghost" onClick={() => void cancelFulltext()}>取消</Button>
          </div>
          <div className="ft-bar">
            <div className="ft-fill" style={{ width: `${total ? Math.round((done / total) * 100) : 0}%` }} />
          </div>
          {Array.isArray(fulltext.log) && fulltext.log.length > 0 && (
            <div className="ft-log">{fulltext.log.slice(-4).map((l, i) => <div key={i}>{l}</div>)}</div>
          )}
        </div>
      )}

      <div className="ws-body" role="tabpanel">
        {tab === 'paper' ? (
          loading ? <div className="ws-loading">载入中…</div> : (
            outline?.sections?.length
              ? <PaperDoc outline={outline} />
              : (
                <div className="ws-empty">
                  <div className="ws-empty-title">尚无大纲</div>
                  <div className="ws-empty-sub">点击上方「生成大纲」开始；或在对话中让墨记生成</div>
                  <Button variant="primary" size="sm" onClick={() => setGenOpen(true)}>生成大纲</Button>
                </div>
              )
          )
        ) : (
          <OutlineTree sections={outline?.sections ?? []} />
        )}
      </div>

      <GenerateOutlineModal open={genOpen} onClose={() => setGenOpen(false)} project={project} />
      <Modal open={review !== null} onClose={() => setReview(null)} title="全文自查报告" closeLabel="关闭">
        <div className="review-body">
          <MarkdownText text={review ?? ''} labels={MD_LABELS} />
        </div>
      </Modal>
    </div>
  )
}

/** 论文全量视图：标题 + 每章（含小节）完整正文。 */
function PaperDoc(props: { outline: Outline }) {
  const project = useStore(s => s.currentProject)!
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')

  async function saveTitle() {
    const t = titleDraft.trim()
    setEditingTitle(false)
    if (!t || t === props.outline.paper_title) return
    try {
      await api('/api/outline/meta', { method: 'POST', body: JSON.stringify({ project_name: project, paper_title: t }) })
      toast('标题已更新', 'ok')
      void refreshOutline()
    } catch (e) { toast((e as Error).message, 'err') }
  }

  return (
    <article className="paper-doc">
      {editingTitle ? (
        <input className="doc-title-input" value={titleDraft} autoFocus
          onChange={e => setTitleDraft(e.target.value)}
          onBlur={() => void saveTitle()}
          onKeyDown={e => { if (e.key === 'Enter') void saveTitle(); if (e.key === 'Escape') setEditingTitle(false) }} />
      ) : (
        <h1 className="doc-title" title="双击改名"
          onDoubleClick={() => { setTitleDraft(props.outline.paper_title || ''); setEditingTitle(true) }}>
          {props.outline.paper_title || '未命名论文'}
        </h1>
      )}
      {props.outline.sections.map((sec, i) => (
        <SectionBlock key={sec.id ?? i} section={sec} order={i + 1}
          root={typeof props.outline.project_path === 'string' ? props.outline.project_path : undefined} />
      ))}
      {props.outline.sections.length === 0 && <div className="ws-loading">大纲为空</div>}
    </article>
  )
}

/** 一章（含小节 + 操作条）。小节跟随章渲染，保证全量。 */
function SectionBlock(props: { section: OutlineSection; order: number; root?: string }) {
  const project = useStore(s => s.currentProject)!
  const busyId = useStore(s => s.sectionBusy)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [aiReport, setAiReport] = useState<Record<string, unknown> | null>(null)
  const sec = props.section
  const id = sec.id ?? ''
  const busy = busyId === id
  const [status, setStatus] = useState('')

  async function streamSection(endpoint: string, body: Record<string, unknown>, label: string) {
    set({ sectionBusy: id })
    setStatus(`${label}中…`)
    try {
      await sse(endpoint, body, ev => {
        const type = ev.type as string
        if (type === 'status') { setStatus(String(ev.message ?? ev.content ?? label)); return }
        if (type === 'chunk' || type === 'delta') {
          setStatus('')
          patchContent(project, id, c => c + String(ev.content ?? ev.text ?? ''))
          return
        }
        if (type === 'done') {
          const full = typeof ev.content === 'string' ? ev.content : undefined
          if (full !== undefined) patchContent(project, id, () => full)
          else void refreshOutline()
          return
        }
        if (type === 'error') { toast(String(ev.content ?? '生成失败'), 'err') }
      })
      await refreshOutline()
      toast(`${label}完成`, 'ok')
    } catch (e) {
      toast(`${label}失败：${(e as Error).message}`, 'err')
    } finally {
      set({ sectionBusy: null })
      setStatus('')
    }
  }

  function patchContent(project: string, id: string, mutate: (c: string) => string) {
    set(s => {
      if (!s.outline) return {}
      const patchSec = (list: OutlineSection[]): OutlineSection[] => list.map(x => {
        if (x.id === id) return { ...x, content: mutate(x.content ?? '') }
        if (x.subsections?.length) return { ...x, subsections: patchSec(x.subsections) }
        return x
      })
      return { outline: { ...s.outline, sections: patchSec(s.outline.sections) } }
    })
  }

  async function aiCheck() {
    setStatus('AI 体检中…')
    try {
      const r = await api<Record<string, unknown>>(
        `/api/sections/ai_check?project_name=${encodeURIComponent(project)}&section_id=${encodeURIComponent(id)}`)
      setAiReport(r)
    } catch (e) { toast((e as Error).message, 'err') }
    finally { setStatus('') }
  }

  async function saveEdit() {
    setEditing(false)
    try {
      await api('/api/sections/update', {
        method: 'POST',
        body: JSON.stringify({ project_name: project, section_id: id, content: draft }),
      })
      toast('正文已保存', 'ok')
      void refreshOutline()
    } catch (e) { toast((e as Error).message, 'err') }
  }

  async function renameSection() {
    const t = prompt('新章节标题', sec.title)
    if (!t || t === sec.title) return
    try {
      await api('/api/sections/rename', { method: 'POST', body: JSON.stringify({ project_name: project, section_id: id, title: t }) })
      void refreshOutline()
    } catch (e) { toast((e as Error).message, 'err') }
  }

  return (
    <section className={`doc-sec${busy ? ' busy' : ''}`} id={`sec-${id}`}>
      <h2>
        <span className="sec-title-text" onDoubleClick={() => void renameSection()} title="双击改名">
          {props.order}. {sec.title}
        </span>
        <span className="sec-words">{sec.actual_words ?? 0}/{sec.target_words ?? '-'} 字</span>
      </h2>

      {!editing ? (
        <div className="doc-content">
          {sec.content ? <MarkdownText text={withImages(sec.content, props.root)} streaming={busy} labels={MD_LABELS} /> : (
            <p className="sec-pending">待生成{sec.points?.length ? ` · 要点：${sec.points.slice(0, 3).join('／')}` : ''}</p>
          )}
        </div>
      ) : (
        <div className="sec-edit">
          <textarea value={draft} onChange={e => setDraft(e.target.value)} rows={Math.min(24, 4 + draft.length / 40)} />
          <div className="sec-edit-bar">
            <Button size="sm" variant="primary" onClick={() => void saveEdit()}>保存正文</Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>取消</Button>
          </div>
        </div>
      )}

      {sec.subsections?.length ? (
        <div className="subsecs">
          {sec.subsections.map((sub, j) => (
            <div className="subsec" key={sub.id ?? j}>
              <h3>{sub.title} <span className="sec-words">{sub.actual_words ?? 0} 字</span></h3>
              <div className="doc-content">
                {sub.content ? <MarkdownText text={withImages(sub.content, props.root)} labels={MD_LABELS} /> : <p className="sec-pending">待生成</p>}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <div className="sec-acts">
        <button className="link-btn" disabled={busyId !== null}
          onClick={() => void streamSection('/api/sections/regenerate',
            { project_name: project, section_id: id }, '重生成')}><Icon name="refresh" size={12} />重生成</button>
        <button className="link-btn" disabled={busyId !== null}
          onClick={() => void streamSection('/api/sections/enhance',
            { project_name: project, section_id: id, mode: 'polish', strength: 'standard' }, '润色')}><Icon name="feather" size={12} />润色</button>
        <button className="link-btn" disabled={busyId !== null}
          onClick={() => void streamSection('/api/sections/enhance',
            { project_name: project, section_id: id, mode: 'humanize', strength: 'standard' }, '去AI味')}><Icon name="wand" size={12} />去AI味</button>
        <button className="link-btn" disabled={busyId !== null}
          onClick={() => void streamSection('/api/sections/enhance',
            { project_name: project, section_id: id, mode: 'humanize', strength: 'deep' }, '深度去AI味')}><Icon name="layers" size={12} />深度</button>
        <button className="link-btn" disabled={busyId !== null} onClick={() => void aiCheck()}><Icon name="shield" size={12} />体检</button>
        <button className="link-btn" disabled={busyId !== null}
          onClick={() => { setDraft(sec.content ?? ''); setEditing(true) }}><Icon name="pencil" size={12} />编辑</button>
        <button className="link-btn" disabled={busyId !== null}
          onClick={() => {
            void (async () => {
              const ok = await confirmDialog({
                title: '恢复版本',
                description: '将本节内容恢复到本次增强前的版本。',
                confirmLabel: '恢复',
              })
              if (!ok) return
              try {
                await api('/api/sections/restore', { method: 'POST', body: JSON.stringify({ project_name: project, section_id: id }) })
                toast('已恢复', 'ok'); void refreshOutline()
              } catch (e) { toast((e as Error).message, 'err') }
            })()
          }}><Icon name="corner-up-left" size={12} />恢复</button>
        {status && <span className="sec-status">{status}</span>}
      </div>

      <Modal open={aiReport !== null} onClose={() => setAiReport(null)} title={`AI 体检 · ${sec.title}`} closeLabel="关闭">
        <div className="review-body"><MarkdownText text={aiText(aiReport)} labels={MD_LABELS} /></div>
      </Modal>
    </section>
  )
}

function aiText(r: Record<string, unknown> | null): string {
  if (!r) return ''
  const lines: string[] = ['### 体检结果', '']
  const fmt = (v: unknown): string => {
    if (v === undefined || v === null) return ''
    if (typeof v === 'object') return JSON.stringify(v, null, 1)
    return String(v)
  }
  const push = (k: string, v: unknown) => { const s = fmt(v); if (s) lines.push(`- **${k}**：${s}`) }
  push('启发式评分', r.score)
  if (Array.isArray(r.markers) && r.markers.length) {
    lines.push('- **问题标记**：')
    for (const m of (r.markers as unknown[]).slice(0, 8)) {
      if (typeof m === 'object' && m !== null) {
        const o = m as Record<string, unknown>
        lines.push(`  - ${fmt(o.type ?? o.kind ?? o.label)}：${fmt(o.text ?? o.detail ?? o.count ?? '')}`.replace(/：$/, ''))
      } else lines.push(`  - ${fmt(m)}`)
    }
  }
  const mc = r.model_check as Record<string, unknown> | null | undefined
  if (mc) push('本地 BERT', mc.ai_probability ?? mc.score ?? mc.label ?? fmt(mc).slice(0, 120))
  const zq = r.zhuque_check as Record<string, unknown> | null | undefined
  if (zq) push('朱雀 AIGC', zq.aigc_score ?? zq.score ?? fmt(zq).slice(0, 160))
  if (r.zhuque_available === false) lines.push('- 朱雀：未配置 Key（设置 → 模型设置 填写后可用）')
  if (lines.length === 2) lines.push('（无异常标记）')
  return lines.join('\n')
}

/** 大纲树全量卡片。 */
function OutlineTree(props: { sections: OutlineSection[] }) {
  if (!props.sections.length) {
    return <div className="ws-empty"><div className="ws-empty-title">大纲为空</div>
      <div className="ws-empty-sub">点击上方「生成大纲」</div></div>
  }
  return (
    <div className="outline-list">
      {props.sections.map((sec, i) => (
        <div className="sec-card" key={sec.id ?? i}>
          <div className="sec-head">
            <StateDot state={(sec.content ?? '').trim() ? 'done' : 'idle'} />
            <span className="sec-title">{i + 1}. {sec.title}</span>
            <span className="sec-meta">{sec.actual_words ?? 0}/{sec.target_words ?? '-'} 字</span>
          </div>
          {sec.points?.length ? (
            <ul className="sec-points">{sec.points.map((p, j) => <li key={j}>{p}</li>)}</ul>
          ) : null}
          {sec.subsections?.length ? (
            <div className="subsec-rows">
              {sec.subsections.map((sub, j) => (
                <div className="subsec-row" key={sub.id ?? j}>
                  <StateDot state={(sub.content ?? '').trim() ? 'done' : 'idle'} />
                  <span className="subsec-title">{sub.title}</span>
                  <span className="sec-meta">{sub.actual_words ?? 0} 字</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  )
}
