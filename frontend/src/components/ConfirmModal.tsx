/**
 * 通用确认弹窗：基于 DSH Modal（遮罩虚化由 --dsw-mask-blur 令牌控制）。
 * 替代浏览器原生 confirm()：原生弹窗无法定制样式，且标题带浏览器名。
 * 按钮行与 GenerateOutlineModal 一致：ghost 取消 + primary 确认。
 */
import type { ReactNode } from 'react'
import { Button, Modal } from '@dsh/primitives'

interface ConfirmModalProps {
  open: boolean
  title: string
  description?: string
  confirmLabel?: string
  cancelLabel?: string
  busy?: boolean
  busyLabel?: string
  children?: ReactNode
  onConfirm: () => void
  onClose: () => void
}

export function ConfirmModal(props: ConfirmModalProps) {
  return (
    <Modal open={props.open} onClose={props.busy ? () => {} : props.onClose}
      title={props.title} closeLabel="关闭" description={props.description}
      footer={(
        <>
          <Button variant="ghost" disabled={props.busy} onClick={props.onClose}>
            {props.cancelLabel ?? '取消'}
          </Button>
          <Button variant="primary" disabled={props.busy} onClick={props.onConfirm}
            data-modal-autofocus>
            {props.busy ? (props.busyLabel ?? '请稍候…') : (props.confirmLabel ?? '确定')}
          </Button>
        </>
      )}>
      {props.children}
    </Modal>
  )
}
