/**
 * 用户头像 + 个人资料弹窗。
 * 头像存 MySQL（users.avatar，base64 data URL）：上传时前端 canvas 居中裁剪
 * 压缩到 160×160 JPEG（约 10–30KB），即传即存、免额外文件服务与静态路由。
 */
import { useEffect, useRef, useState } from 'react'
import { Button, Input, Modal } from '@dsh/primitives'
import type { AuthUser } from '../api'
import { renameUsername, saveProfile, toast } from '../store'

/** 圆形头像：有图显示图，无图显示用户名首字符（中性底色） */
export function Avatar(props: { user: AuthUser | null; size?: number; className?: string }) {
  const size = props.size ?? 28
  const name = props.user?.username || ''
  const style = { width: size, height: size, borderRadius: '50%' } as const
  if (props.user?.avatar) {
    return <img className={`avatar${props.className ? ` ${props.className}` : ''}`}
      style={style} src={props.user.avatar} alt={name} />
  }
  return (
    <span className={`avatar avatar-fallback${props.className ? ` ${props.className}` : ''}`}
      style={{ ...style, fontSize: Math.round(size * 0.42) }} aria-hidden>
      {(name || '?').slice(0, 1).toUpperCase()}
    </span>
  )
}

/** 文件 → 160×160 JPEG data URL（居中裁剪，质量 0.85；大图先等比缩放） */
async function fileToAvatar(file: File): Promise<string> {
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error('图片读取失败'))
      el.src = url
    })
    const side = Math.min(img.naturalWidth, img.naturalHeight)
    const sx = (img.naturalWidth - side) / 2
    const sy = (img.naturalHeight - side) / 2
    const out = 160
    const canvas = document.createElement('canvas')
    canvas.width = out; canvas.height = out
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('当前浏览器不支持画布')
    ctx.drawImage(img, sx, sy, side, side, 0, 0, out, out)
    return canvas.toDataURL('image/jpeg', 0.85)
  } finally { URL.revokeObjectURL(url) }
}

export function ProfileModal(props: { open: boolean; user: AuthUser | null; onClose: () => void }) {
  const user = props.user
  const [name, setName] = useState('')
  const [avatar, setAvatar] = useState<string | null>(null)   // null=未改动；''=删除头像
  const [busy, setBusy] = useState(false)
  const [nameErr, setNameErr] = useState('')   // 用户名格式不合规时的红字提示
  const fileRef = useRef<HTMLInputElement | null>(null)

  const NAME_RE = /^[\w\u4e00-\u9fff]{3,32}$/

  // 打开时以服务端当前值为准重置草稿
  useEffect(() => {
    if (!props.open) return
    setName(user?.username || '')
    setAvatar(null)
    setNameErr('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.open])

  function editName(v: string) {
    setName(v)
    // 只有不合规才提示（合规或未改动时保持安静）
    setNameErr(v && !NAME_RE.test(v) ? '用户名需 3-32 位字母/数字/下划线/中文' : '')
  }

  const preview: AuthUser | null = user
    ? { ...user, username: name, avatar: avatar === null ? user.avatar : avatar }
    : null

  async function pick(file: File | null) {
    if (!file) return
    if (!file.type.startsWith('image/')) { toast('请选择图片文件', 'err'); return }
    try { setAvatar(await fileToAvatar(file)) }
    catch (e) { toast((e as Error).message, 'err') }
  }

  async function save() {
    const newName = name.trim()
    if (!newName) { setNameErr('用户名不能为空'); return }
    if (!NAME_RE.test(newName)) { setNameErr('用户名需 3-32 位字母/数字/下划线/中文'); return }
    setBusy(true)
    try {
      const renamed = !!user && newName !== user.username
      if (renamed) {
        await renameUsername(newName)   // 服务端吊销全部会话
        toast(`用户名已改为 ${newName}，请重新登录`, 'ok')
        AUTH.clear()
        setTimeout(() => location.reload(), 600)
        return
      }
      if (avatar !== null) await saveProfile({ avatar })
      toast('个人资料已保存', 'ok')
      props.onClose()
    } catch (e) { toast(`保存失败：${(e as Error).message}`, 'err') }
    finally { setBusy(false) }
  }

  return (
    <Modal open={props.open} onClose={props.onClose} title="个人资料" closeLabel="关闭"
      description="头像与用户名保存到账号数据库。"
      footer={(
        <>
          <Button variant="ghost" disabled={busy} onClick={props.onClose}>取消</Button>
          <Button variant="primary" disabled={busy} onClick={() => void save()}>
            {busy ? '保存中…' : '保存'}
          </Button>
        </>
      )}>
      <div className="profile-form">
        <div className="profile-avatar-row">
          <Avatar user={preview} size={56} />
          <div className="profile-avatar-actions">
            <Button variant="ghost" onClick={() => fileRef.current?.click()}>
              上传图片
            </Button>
            {(avatar !== null ? avatar !== '' : !!user?.avatar) && (
              <Button variant="ghost" disabled={busy} onClick={() => setAvatar('')}>移除头像</Button>
            )}
          </div>
          <input ref={fileRef} type="file" accept="image/*" hidden
            onChange={e => { void pick(e.target.files?.[0] ?? null); e.target.value = '' }} />
        </div>
        <label className="field">
          <span>用户名</span>
          <Input value={name} maxLength={32} placeholder={user?.username ?? ''}
            onChange={e => editName(e.target.value)} />
          {nameErr && <span className="field-error">{nameErr}</span>}
        </label>
      </div>
    </Modal>
  )
}
