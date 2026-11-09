/**
 * 创建项目（按参考图）：项目名称 / 设备 / 源文件夹（点击弹系统目录选择框）。
 * 创建后项目记录（名称 + 目录绝对地址）写入 MySQL user_projects（带 Redis 缓存），
 * 并构建研究画像。
 */
import { useState } from 'react'
import { Button, Input, Modal } from '@dsh/primitives'
import { api } from '../api'
import { toast } from '../store'
import { Icon } from './Icon'
import { Select } from './Select'

interface CreateProjectModalProps {
  open: boolean
  onClose: () => void
  onCreated: (project: { name: string; path: string }) => void
}

export function CreateProjectModal(props: CreateProjectModalProps) {
  const [name, setName] = useState('')
  const [path, setPath] = useState('')
  const [picking, setPicking] = useState(false)
  const [busy, setBusy] = useState(false)

  function reset() { setName(''); setPath(''); setBusy(false); setPicking(false) }

  async function pickFolder() {
    setPicking(true)
    try {
      const r = await api<{ path: string }>('/api/projects/pick-folder', { method: 'POST' })
      if (r.path) setPath(r.path)
    } catch (e) { toast(`打开文件夹选择框失败：${(e as Error).message}`, 'err') }
    finally { setPicking(false) }
  }

  async function create() {
    if (!path.trim()) { toast('请选择项目文件夹', 'err'); return }
    setBusy(true)
    try {
      const r = await api<{ ok: boolean; project: { name: string; path: string } }>('/api/projects/register', {
        method: 'POST',
        body: JSON.stringify({ path: path.trim(), name: name.trim() || undefined }),
      })
      toast(`项目「${r.project.name}」已创建`, 'ok')
      props.onCreated(r.project)
      reset()
      props.onClose()
    } catch (e) { toast(`创建失败：${(e as Error).message}`, 'err') }
    finally { setBusy(false) }
  }

  function close() { if (!busy) { reset(); props.onClose() } }

  return (
    <Modal open={props.open} onClose={close} title="创建项目" closeLabel="关闭"
      className="cp-dialog"
      footer={(
        <>
          <Button variant="ghost" disabled={busy} onClick={close}>取消</Button>
          <Button variant="primary" disabled={busy || picking} onClick={() => void create()}
            data-modal-autofocus>
            {busy ? '创建中…' : '创建项目'}
          </Button>
        </>
      )}>
      <div className="cp-form">
        <label className="field">
          <span>项目名称</span>
          <Input value={name} maxLength={64} placeholder="新项目"
            onChange={e => setName(e.target.value)} />
        </label>
        <div className="field">
          <span>设备</span>
          <Select value="local" options={[{ value: 'local', label: '此电脑' }]}
            onChange={() => { /* 本地部署固定此电脑 */ }} disabled />
        </div>
        <div className="field">
          <span>源文件夹</span>
          <button type="button" className={`pick-folder${path ? ' picked' : ''}`}
            disabled={picking || busy} onClick={() => void pickFolder()}>
            {picking ? (
              <span className="pick-desc">等待选择文件夹…</span>
            ) : path ? (
              <>
                <span className="pick-icon"><Icon name="folder" size={18} /></span>
                <span className="pick-path" title={path}>{path}</span>
                <span className="pick-desc">点击可重新选择</span>
              </>
            ) : (
              <>
                <span className="pick-icon"><Icon name="folder" size={18} /></span>
                <span className="pick-title">选择文件夹</span>
                <span className="pick-desc">墨记可读取和编辑这些文件夹</span>
              </>
            )}
          </button>
        </div>
      </div>
    </Modal>
  )
}
