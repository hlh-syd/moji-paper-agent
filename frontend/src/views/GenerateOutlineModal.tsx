/**
 * 生成大纲向导：模板（/api/outline/templates）+ 字数估算（estimate）+ 领域（/api/domains）
 * + 参考论文（/api/reference/list）+ 附加要求 → POST /api/outline/generate。
 */
import { useEffect, useState } from 'react'
import { Button, Input, Modal } from '@dsh/primitives'
import { api } from '../api'
import { getState, refreshOutline, toast } from '../store'
import { confirmDialog } from '../components/confirm'
import { Select } from '../components/Select'

interface TemplateInfo { key: string; name: string; total_words?: number; [k: string]: unknown }

export function GenerateOutlineModal(props: { open: boolean; onClose: () => void; project: string }) {
  const [templates, setTemplates] = useState<TemplateInfo[]>([])
  const [domains, setDomains] = useState<{ key: string; name: string }[]>([])
  const [refs, setRefs] = useState<{ name: string }[]>([])
  const [template, setTemplate] = useState('')
  const [domain, setDomain] = useState('')
  const [words, setWords] = useState(30000)
  const [refPaper, setRefPaper] = useState('')
  const [requirement, setRequirement] = useState('')
  const [estimate, setEstimate] = useState<Record<string, number> | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!props.open) return
    void api<Record<string, TemplateInfo>>('/api/outline/templates').then(r => {
      // 后端返回字典 {key: {name, total_words, sections}}
      const list = Object.entries(r).map(([key, v]) => ({ key, ...(v as TemplateInfo) }))
      setTemplates(list)
      if (list.length) setTemplate(prev => prev || list[0].key)
    }).catch(() => setTemplates([]))
    void api<{ domains?: { key: string; name: string }[] }>('/api/domains')
      .then(r => setDomains(r.domains ?? [])).catch(() => setDomains([]))
    void api<{ papers?: { name: string }[] }>('/api/reference/list')
      .then(r => setRefs(r.papers ?? [])).catch(() => setRefs([]))
  }, [props.open])

  useEffect(() => {
    if (!template) return
    const t = setTimeout(() => {
      // 后端为 Query 参数（POST 无 body）
      void api<{ targets: Record<string, number> }>(
        `/api/outline/estimate?template=${encodeURIComponent(template)}&total_words=${words}`,
        { method: 'POST' },
      ).then(r => setEstimate(r.targets)).catch(() => setEstimate(null))
    }, 300)
    return () => clearTimeout(t)
  }, [template, words])

  async function generate() {
    const cur = getState().outline
    if (cur?.sections?.length) {
      const hasContent = cur.sections.some(s => (s.content ?? '').trim())
      const ok = await confirmDialog({
        title: '重新生成大纲',
        description: `当前已有大纲（${cur.sections.length} 章${hasContent ? '，含已写正文' : ''}）。重新生成将覆盖现有结构与正文。`,
        confirmLabel: '重新生成',
      })
      if (!ok) return
    }
    setBusy(true)
    try {
      await api('/api/outline/generate', {
        method: 'POST',
        body: JSON.stringify({
          project_name: props.project,
          template,
          total_words: words || undefined,
          user_requirements: requirement,
          reference_paper: refPaper || undefined,
          domain: domain || undefined,
        }),
      })
      toast('大纲已生成', 'ok')
      await refreshOutline()
      props.onClose()
    } catch (e) { toast(`生成失败：${(e as Error).message}`, 'err') }
    finally { setBusy(false) }
  }

  return (
    <Modal open={props.open} onClose={props.onClose} title="生成大纲" closeLabel="关闭"
      description={`${props.project} · 结构模板 / 目标字数 / 参考论文 / 领域`}>
      <div className="gen-form">
        <div className="field">
          <span>结构模板</span>
          <Select value={template} onChange={setTemplate}
            options={templates.map(t => ({
              value: t.key,
              label: `${t.name ?? t.key}${t.total_words ? `（默认 ${t.total_words} 字）` : ''}`,
            }))} />
        </div>
        <div className="field">
          <span>论文领域</span>
          <Select value={domain} onChange={setDomain}
            options={[{ value: '', label: '自动检测' },
              ...domains.map(d => ({ value: d.key, label: d.name ?? d.key }))]} />
        </div>
        <label className="field">
          <span>总目标字数（3万~8万）</span>
          <Input type="number" min={30000} max={80000} step={5000} value={words}
            onChange={e => setWords(Number(e.target.value))} />
        </label>
        <div className="field">
          <span>参考论文（借鉴章节结构，可选）</span>
          <Select value={refPaper} onChange={setRefPaper}
            options={[{ value: '', label: '不使用参考论文' },
              ...refs.map(r => ({ value: r.name, label: r.name }))]} />
        </div>
        <label className="field">
          <span>附加要求（可选）</span>
          <textarea rows={3} value={requirement} onChange={e => setRequirement(e.target.value)}
            placeholder="例如：方法章节需覆盖消融实验；讨论部分对比近三年文献" />
        </label>
        {estimate && (
          <div className="estimate">
            {Object.entries(estimate).map(([k, v]) => (
              <span key={k} className="est-chip">{k} ≈ {v} 字</span>
            ))}
          </div>
        )}
      </div>
      <div className="modal-actions">
        <Button variant="ghost" onClick={props.onClose}>取消</Button>
        <Button variant="primary" disabled={busy || !template} onClick={() => void generate()}>
          {busy ? '生成中（1-3 分钟）…' : '生成大纲'}
        </Button>
      </div>
    </Modal>
  )
}
