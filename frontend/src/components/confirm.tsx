/**
 * Promise 版系统确认框：替代原生 confirm()（原生弹窗样式不可控、
 * 标题带浏览器名）。内部复用 ConfirmModal，调用处可一行 await。
 */
import type { ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { ConfirmModal } from './ConfirmModal'

interface ConfirmOptions {
  title: string
  description?: string
  confirmLabel?: string
  cancelLabel?: string
  children?: ReactNode
}

export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  return new Promise(resolve => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    const close = (ok: boolean) => {
      root.unmount()
      host.remove()
      resolve(ok)
    }
    root.render(
      <ConfirmModal
        open
        title={opts.title}
        description={opts.description}
        confirmLabel={opts.confirmLabel}
        cancelLabel={opts.cancelLabel}
        onClose={() => close(false)}
        onConfirm={() => close(true)}
      >
        {opts.children}
      </ConfirmModal>,
    )
  })
}
