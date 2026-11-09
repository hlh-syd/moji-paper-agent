/**
 * 设置页（DSH 文档式布局）：常规/外观/模型/关于 + 服务面板（ServicePanels）。
 * 按需求：主页未承载的后端能力全部收拢于此。
 */
import { useEffect, useState } from 'react'
import { Button, Input, Switch } from '@dsh/primitives'
import { api } from '../api'
import { applyFontSize, applyTheme, openSettings, set, useStore } from '../store'
import { Icon } from '../components/Icon'
import { Select } from '../components/Select'
import { AicPanel, FiguresPanel, KbPanel, ProjectsPanel, ReferencePanel, StylePanel } from './ServicePanels'
import { FulltextProgress, PaperWorkspace } from './PaperWorkspace'

type Section = 'general' | 'appearance' | 'projects' | 'model' | 'aic' | 'kb' | 'reference' | 'style' | 'figures' | 'about'

export function SettingsPage() {
  const section = useStore(s => s.settingsSection) as Section
  const project = useStore(s => s.currentProject)
  const rightbarWidth = useStore(s => s.rightbarWidth)
  const [viewport, setViewport] = useState(() => window.innerWidth)
  const [closing, setClosing] = useState(false)   // 退场：先播淡出+模糊，动画完再卸载
  useEffect(() => {
    const onResize = () => setViewport(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  // 右侧论文区宽度与工作区一致：沿用用户拖拽值，否则 54%；同时给设置内容留 520px
  const paperWidth = Math.min(rightbarWidth ?? Math.round(viewport * 0.54), Math.max(0, viewport - 210 - 640))
  const showPaper = !!project && paperWidth >= 360
  const nav = (v: Section) => set({ settingsSection: v })
  function closePage() {
    if (closing) return
    setClosing(true)
    window.setTimeout(() => set({ settingsOpen: false }), 200)   // 与 pageOut 时长一致
  }
  return (
    <div className={`settings-page${closing ? ' closing' : ''}`} style={showPaper ? { gridTemplateColumns: `210px minmax(0, 1fr) ${paperWidth}px` } : undefined}>
      <aside className="sp-nav">
        <button className="sp-back" onClick={closePage}><Icon name="arrow-left" />返回工作区</button>
        <div className="sp-group">基础</div>
        <button className={`sp-nav-item${section === 'general' ? ' active' : ''}`} onClick={() => nav('general')}><Icon name="settings" />常规</button>
        <button className={`sp-nav-item${section === 'appearance' ? ' active' : ''}`} onClick={() => nav('appearance')}><Icon name="contrast" />外观</button>
        <div className="sp-group">论文服务</div>
        <button className={`sp-nav-item${section === 'projects' ? ' active' : ''}`} onClick={() => nav('projects')}><Icon name="folder" />项目与理解</button>
        <button className={`sp-nav-item${section === 'reference' ? ' active' : ''}`} onClick={() => nav('reference')}><Icon name="book-open" />参考论文</button>
        <button className={`sp-nav-item${section === 'style' ? ' active' : ''}`} onClick={() => nav('style')}><Icon name="ruler" />排版样式</button>
        <button className={`sp-nav-item${section === 'figures' ? ' active' : ''}`} onClick={() => nav('figures')}><Icon name="image" />图表管理</button>
        <div className="sp-group">检测与知识</div>
        <button className={`sp-nav-item${section === 'aic' ? ' active' : ''}`} onClick={() => nav('aic')}><Icon name="shield" />AI 检测中心</button>
        <button className={`sp-nav-item${section === 'kb' ? ' active' : ''}`} onClick={() => nav('kb')}><Icon name="database" />知识库</button>
        <div className="sp-group">系统</div>
        <button className={`sp-nav-item${section === 'model' ? ' active' : ''}`} onClick={() => nav('model')}><Icon name="cpu" />模型设置</button>
        <button className={`sp-nav-item${section === 'about' ? ' active' : ''}`} onClick={() => nav('about')}><Icon name="info" />关于</button>
      </aside>
      <div className="sp-content">
        {section === 'general' && <GeneralSection />}
        {section === 'appearance' && <AppearanceSection />}
        {section === 'projects' && <ProjectsPanel />}
        {section === 'reference' && <ReferencePanel />}
        {section === 'style' && <StylePanel />}
        {section === 'figures' && <FiguresPanel />}
        {section === 'aic' && <AicPanel />}
        {section === 'kb' && <KbPanel />}
        {section === 'model' && <ModelSection />}
        {section === 'about' && <AboutSection />}
      </div>
      {showPaper && (
        <aside className="sp-paper">
          <header className="sp-paper-top">
            <span className="mtb-project" title={project ?? ''}><Icon name="folder" /><span className="mtb-text">{project}</span></span>
            <span className="grow" />
            <FulltextProgress />
          </header>
          <div className="sp-paper-body"><PaperWorkspace /></div>
        </aside>
      )}
    </div>
  )
}

function GeneralSection() {
  const user = useStore(s => s.user)

  return (
    <section className="sp-section">
      <h2 className="sp-h1">常规</h2>
      <div className="set-card">
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">账号</div>
            <div className="sp-desc">多用户 RBAC：用户仅可访问自己的项目/文档/会话</div></div>
          <div className="sp-ctl"><span className="sp-chip">{useStore(s => s.user) ? `${user!.username}（${user!.role === 'admin' ? '管理员' : '用户'}）` : '单用户模式'}</span></div>
        </div>
      </div>
    </section>
  )
}

function AppearanceSection() {
  const theme = useStore(s => s.theme)
  const fontSize = useStore(s => s.fontSize)
  const [fontDraft, setFontDraft] = useState(String(fontSize))
  useEffect(() => { setFontDraft(String(fontSize)) }, [fontSize])
  return (
    <section className="sp-section">
      <h2 className="sp-h1">外观</h2>
      <div className="sp-h2sub">墨记前端基于 DeepSeek Harness (DSH) 设计体系构建（dsh-frontend/）。</div>
      <div className="set-card">
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">界面主题</div>
            <div className="sp-desc">深色 / 浅色（DSH 暗亮双板），跟随账号保存，跨浏览器一致</div></div>
          <div className="sp-ctl">
            <Switch checked={theme === 'light'} onChange={next => applyTheme(next ? 'light' : 'dark')} label="浅色主题" />
            <span className="sp-chip">{theme === 'light' ? '浅色' : '深色'}</span>
          </div>
        </div>
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">界面字号</div>
            <div className="sp-desc">调整应用界面的文字大小（12–24），图标和布局尺寸不受影响</div></div>
          <div className="sp-ctl">
            <Input className="font-size-input" type="number" min={12} max={24} step={1}
              value={fontDraft}
              onChange={e => {
                setFontDraft(e.target.value)
                const v = Number(e.target.value)
                if (v >= 12 && v <= 24) applyFontSize(v)
              }}
              onBlur={() => { if (!(Number(fontDraft) >= 12 && Number(fontDraft) <= 24)) setFontDraft(String(fontSize)) }} />
            <span className="sp-chip">px</span>
          </div>
        </div>
      </div>
    </section>
  )
}

interface SettingsData {
  models: Record<string, string | number>
  options: { llm: string[] }
  zhuque?: { configured?: boolean; masked?: string }
  glm?: { configured?: boolean; masked?: string }
  deepseek?: { configured?: boolean; masked?: string }
}

function ModelSection() {
  const [data, setData] = useState<SettingsData | null>(null)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    void api<SettingsData>('/api/settings').then(d => { setData(d); setDraft({}) }).catch(() => toast2('设置读取失败', 'err'))
  }, [])
  if (!data) return <section className="sp-section"><div className="ws-loading">载入中…</div></section>

  const field = (key: string, label: string, desc: string, opts?: string[], type: 'text' | 'password' | 'number' = 'text') => (
    <div className="sp-row" key={key}>
      <div className="sp-txt"><div className="sp-label">{label}</div><div className="sp-desc">{desc}</div></div>
      <div className="sp-ctl">
        {opts ? (
          <Select className="sel-w" value={draft[key] ?? String(data.models[key] ?? '')}
            options={opts.map(o => ({ value: o, label: o }))}
            onChange={v => setDraft(d => ({ ...d, [key]: v }))} />
        ) : (
          <Input type={type} className="sp-input" value={draft[key] ?? ''} placeholder={String(data.models[key] ?? '')}
            onChange={e => setDraft(d => ({ ...d, [key]: e.target.value }))} />
        )}
      </div>
    </div>
  )

  async function save() {
    setBusy(true)
    try {
      const models: Record<string, string | number> = {}
      for (const k of ['llm', 'analyst_llm', 'small_llm']) if (draft[k] !== undefined) models[k] = draft[k]
      if (draft.temperature !== undefined) models.temperature = Number(draft.temperature)
      if (draft.max_tokens !== undefined) models.max_tokens = Number(draft.max_tokens)
      const payload: Record<string, unknown> = {}
      if (Object.keys(models).length) payload.models = models
      const ds: Record<string, string> = {}
      if (draft.deepseek_key) ds.api_key = draft.deepseek_key
      if (Object.keys(ds).length) payload.deepseek = ds
      const glm: Record<string, string> = {}
      if (draft.glm_key) glm.api_key = draft.glm_key
      if (Object.keys(glm).length) payload.glm = glm
      const zq: Record<string, string> = {}
      if (draft.zhuque_key) zq.api_key = draft.zhuque_key
      if (Object.keys(zq).length) payload.zhuque = zq
      if (!Object.keys(payload).length) { toast2('没有修改', 'info'); return }
      await api('/api/settings', { method: 'POST', body: JSON.stringify(payload) })
      toast2('已保存并生效', 'ok')
      setDraft({})
      setData(await api<SettingsData>('/api/settings'))
    } catch (e) { toast2(`保存失败：${(e as Error).message}`, 'err') }
    finally { setBusy(false) }
  }

  return (
    <section className="sp-section">
      <h2 className="sp-h1">模型设置</h2>
      <div className="sp-h2sub">保存即写入 config.yaml / .env 并热生效 · 系统级配置仅管理员可改</div>
      <div className="set-card">
        {field('llm', '主写作模型', '大纲 / 正文 / 自查等重任务', data.options?.llm)}
        {field('analyst_llm', '项目理解模型', '「理解项目」Agent 扫描分析', data.options?.analyst_llm)}
        {field('temperature', '温度', '学术写作建议 0.3 ~ 0.5', undefined, 'number')}
        {field('max_tokens', '最大 tokens', '单次响应上限，长文建议 ≥ 8192', undefined, 'number')}
        {field('small_llm', '本地小模型', 'Ollama 模型名（意图路由），保存后重启生效')}
        {field('glm_key', 'GLM API Key', data.glm?.configured ? `已配置（${data.glm.masked ?? ''}）· 留空不修改` : '未配置 · 智谱 open.bigmodel.cn，写入 .env 即时生效', undefined, 'password')}
        {field('deepseek_key', 'DeepSeek API Key', data.deepseek?.configured ? `已配置（${data.deepseek.masked ?? ''}）· 留空不修改` : '未配置 · 写入 .env 即时生效', undefined, 'password')}
        {field('zhuque_key', '朱雀 API Key', data.zhuque?.configured ? `已配置（${data.zhuque.masked ?? ''}）` : '未配置 · AI 检测降级为本地引擎', undefined, 'password')}
      </div>
      <div className="sp-actions">
        <Button variant="primary" disabled={busy} onClick={() => void save()}>保存并生效</Button>
      </div>
    </section>
  )
}

function AboutSection() {
  return (
    <section className="sp-section">
      <h2 className="sp-h1">关于</h2>
      <div className="set-card">
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">墨记 · 论文智能体 v5.0</div>
            <div className="sp-desc">前端基于 DeepSeek Harness (DSH) 设计体系构建（仓库根 dsh-frontend/，ui-theme 令牌 + ui-primitives 组件 + AppFrame 几何），遵循 DSH 品牌规范。</div></div>
        </div>
        <div className="sp-row">
          <div className="sp-txt"><div className="sp-label">接口覆盖</div>
            <div className="sp-desc">对话/工作区/大纲/逐节/全文/导出/检测/知识库/参考论文/排版/图表/项目理解 全量对接；接口文档见 /api/docs</div></div>
          <div className="sp-ctl"><button className="link-btn" onClick={() => open('/api/docs')}>打开 Swagger</button></div>
        </div>
      </div>
    </section>
  )
}

/** 独立于 store 的轻量提示（设置页内使用，避免与主界面 toast 层混流）。 */
function toast2(text: string, kind: 'ok' | 'err' | 'info' = 'info') {
  import('../store').then(m => m.toast(text, kind))
}
