/**
 * 统一选择框：替代原生 <select>（展开面板由浏览器/系统绘制，无法贴合
 * DSH 设计体系——蓝底高亮、无圆角、系统字体）。
 * 触发按钮 + portal 浮层选项列表：10px 圆角、中性阴影、线性图标、
 * 选中项打勾；支持键盘（↑↓ 移动、Enter 选中、Esc 关闭）与点击外部关闭。
 * 浮层 portal 到 body 并用 fixed 定位，避免被 Modal 的 overflow:hidden 裁剪。
 */
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './Icon'

export interface SelectOption { value: string; label: string }

interface SelectProps {
  value: string
  options: SelectOption[]
  onChange: (value: string) => void
  disabled?: boolean
  className?: string
  placeholder?: string
}

export function Select(props: SelectProps) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [pos, setPos] = useState<{ x: number; y: number; w: number } | null>(null)
  const btnRef = useRef<HTMLButtonElement | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const current = props.options.find(o => o.value === props.value)

  // 点击外部 / 滚动 / 尺寸变化 → 关闭（fixed 定位在滚动后会脱位）
  useEffect(() => {
    if (!open) return
    const onDoc = (e: PointerEvent) => {
      const t = e.target as Node
      if (btnRef.current?.contains(t) || menuRef.current?.contains(t)) return
      setOpen(false)
    }
    const close = () => setOpen(false)
    document.addEventListener('pointerdown', onDoc)
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    return () => {
      document.removeEventListener('pointerdown', onDoc)
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
    }
  }, [open])

  function openMenu() {
    if (props.disabled) return
    const r = btnRef.current?.getBoundingClientRect()
    if (r) setPos({ x: r.left, y: r.bottom + 4, w: r.width })
    const idx = props.options.findIndex(o => o.value === props.value)
    setActive(idx >= 0 ? idx : 0)
    setOpen(true)
  }

  function pick(v: string) {
    props.onChange(v)
    setOpen(false)
  }

  return (
    <div className={`sel${props.className ? ` ${props.className}` : ''}${props.disabled ? ' disabled' : ''}`}>
      <button ref={btnRef} type="button" className="sel-btn" disabled={props.disabled}
        aria-haspopup="listbox" aria-expanded={open}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={e => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault()
            if (!open) { openMenu(); return }
            const n = props.options.length
            if (!n) return
            setActive(i => (e.key === 'ArrowDown' ? (i + 1) % n : (i - 1 + n) % n))
          } else if (e.key === 'Enter' && open) {
            e.preventDefault()
            const o = props.options[active]
            if (o) pick(o.value)
          } else if (e.key === 'Escape' && open) {
            e.preventDefault(); setOpen(false)
          }
        }}>
        <span className="sel-label">{current?.label ?? props.placeholder ?? '请选择'}</span>
        <Icon name="chevron-down" size={14} />
      </button>
      {open && pos && createPortal(
        <div ref={menuRef} className="sel-menu" role="listbox"
          style={{ position: 'fixed', left: pos.x, top: pos.y, width: pos.w }}>
          {props.options.length === 0 && <div className="sel-empty">无可选项</div>}
          {props.options.map((o, i) => (
            <button type="button" key={o.value} role="option" aria-selected={o.value === props.value}
              className={`sel-opt${i === active ? ' active' : ''}`} title={o.label}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(o.value)}>
              <span className="sel-opt-label">{o.label}</span>
              {o.value === props.value && <Icon name="check" size={12} />}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  )
}
