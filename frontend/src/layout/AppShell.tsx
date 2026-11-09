/**
 * 墨记三栏外壳：几何/拖拽移植 DSH AppFrame（rAF 节流、拖动暂停过渡、双击复位）。
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import {
  CENTER_MIN, RIGHTBAR_DEFAULT_RATIO, RIGHTBAR_MAX_RATIO, RIGHTBAR_MIN,
  SIDEBAR_AUTO_COLLAPSE, SIDEBAR_DEFAULT, SIDEBAR_MAX, SIDEBAR_MIN,
  clampWidth, computeColumns,
} from './columns'
import { set, setCurrentProject, useStore } from '../store'
import { Icon } from '../components/Icon'
import { Sidebar } from '../views/Sidebar'
import { ChatView } from '../views/ChatView'
import { FulltextProgress, PaperViewSwitch, PaperWorkspace } from '../views/PaperWorkspace'

function DragHandle(props: { left: number; side: 'sidebar' | 'rightbar'; onStart: () => void; onDrag: (dx: number) => void; onEnd: () => void; onReset?: () => void }) {
  const [dragging, setDragging] = useState(false)
  const origin = useRef(0)
  const latest = useRef(0)
  const frame = useRef<number | null>(null)
  const capture = useRef<{ element: HTMLDivElement; id: number } | null>(null)
  const callbacks = useRef({ onStart: props.onStart, onDrag: props.onDrag, onEnd: props.onEnd, onReset: props.onReset })
  callbacks.current = { onStart: props.onStart, onDrag: props.onDrag, onEnd: props.onEnd, onReset: props.onReset }

  const endDrag = useCallback(() => {
    const active = capture.current
    if (active === null) return
    capture.current = null
    if (frame.current !== null) { cancelAnimationFrame(frame.current); frame.current = null }
    if (active.element.hasPointerCapture(active.id)) active.element.releasePointerCapture(active.id)
    setDragging(false)
    callbacks.current.onEnd()
  }, [])
  useEffect(() => endDrag, [endDrag])

  const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 || capture.current !== null) return
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    capture.current = { element: e.currentTarget, id: e.pointerId }
    origin.current = e.clientX
    latest.current = e.clientX
    callbacks.current.onStart()
    setDragging(true)
  }, [])
  const onPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (capture.current?.id !== e.pointerId) return
    latest.current = e.clientX
    frame.current ??= requestAnimationFrame(() => {
      frame.current = null
      callbacks.current.onDrag(latest.current - origin.current)
    })
  }, [])
  const onPointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (capture.current?.id !== e.pointerId) return
    callbacks.current.onDrag(e.clientX - origin.current)
    endDrag()
  }, [endDrag])

  return (
    <div
      className={`col-handle${dragging ? ' dragging' : ''}`}
      style={{ left: props.left }}
      data-side={props.side}
      title="拖动调整宽度 · 双击复位"
      onDoubleClick={() => { callbacks.current.onReset?.() }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onLostPointerCapture={onPointerUp}
    />
  )
}

export function AppShell() {
  const frameRef = useRef<HTMLDivElement | null>(null)
  const sidebarCollapsed = useStore(s => s.sidebarCollapsed)
  const sidebarWidth = useStore(s => s.sidebarWidth)
  const rightbarWidth = useStore(s => s.rightbarWidth)
  const project = useStore(s => s.currentProject)
  const settingsOpen = useStore(s => s.settingsOpen)
  const [viewport, setViewport] = useState(0)
  const [dragging, setDragging] = useState(false)

  useLayoutEffect(() => {
    const el = frameRef.current
    if (el === null) return
    let raf: number | null = null
    let disposed = false
    const measure = () => { const w = el.getBoundingClientRect().width; if (w > 0) setViewport(w) }
    measure()
    const observer = new ResizeObserver(() => {
      if (disposed) return
      raf ??= requestAnimationFrame(() => { raf = null; measure() })
    })
    observer.observe(el)
    return () => { disposed = true; observer.disconnect(); if (raf !== null) cancelAnimationFrame(raf) }
  }, [])

  const narrow = viewport < SIDEBAR_AUTO_COLLAPSE
  const effectiveCollapsed = narrow || sidebarCollapsed
  const sidebarPref = effectiveCollapsed ? 0 : (sidebarWidth || SIDEBAR_DEFAULT)
  const rightbarPref = rightbarWidth ?? viewport * RIGHTBAR_DEFAULT_RATIO
  const cols = useMemo(
    () => computeColumns(viewport, sidebarPref, rightbarPref),
    [viewport, sidebarPref, rightbarPref],
  )
  const colsRef = useRef(cols)
  colsRef.current = cols
  const rightbarRef = useRef(rightbarPref)
  rightbarRef.current = rightbarPref
  const sidebarBase = useRef(0)
  const rightbarBase = useRef(0)

  const onSidebarStart = useCallback(() => { sidebarBase.current = colsRef.current.sidebar; setDragging(true) }, [])
  const onSidebarDrag = useCallback((dx: number) => {
    set({ sidebarWidth: clampWidth(sidebarBase.current + dx, SIDEBAR_MIN, SIDEBAR_MAX), sidebarCollapsed: false })
  }, [])
  const onRightbarStart = useCallback(() => { rightbarBase.current = rightbarRef.current; setDragging(true) }, [])
  const onRightbarDrag = useCallback((dx: number) => {
    set({ rightbarWidth: clampWidth(rightbarBase.current - dx, RIGHTBAR_MIN, viewport * RIGHTBAR_MAX_RATIO) })
  }, [viewport])
  const onDragEnd = useCallback(() => setDragging(false), [])

  const rightbarMax = cols.rightbar === 0 ? 0 : clampWidth(rightbarPref, RIGHTBAR_MIN, viewport * RIGHTBAR_MAX_RATIO)

  return (
    <div
      ref={frameRef}
      className="app-frame"
      data-sidebar-collapsed={effectiveCollapsed || undefined}
      data-dragging={dragging || undefined}
      style={{ gridTemplateColumns: `${cols.sidebar}px minmax(${cols.rightbar === 0 ? 0 : CENTER_MIN}px, 1fr) minmax(0px, ${rightbarMax}px)` }}
    >
      <div className="sidebar-col"><Sidebar collapsed={effectiveCollapsed} width={cols.sidebar} /></div>
      <header className="main-topbar">
        <span className={`mtb-project${project ? ' with-x' : ''}`} title={project ?? '未确定工作区'}>
          <Icon name="folder" />
          <span className="mtb-text">{project ?? '未确定工作区'}</span>
          {project && (
            <button className="mtb-x" title="退出当前项目"
              onClick={() => setCurrentProject(null)}><Icon name="close" size={10} /></button>
          )}
        </span>
        <span className="grow" />
        {project && cols.rightbar > 0 && (
          <>
            <FulltextProgress />
            <PaperViewSwitch />
          </>
        )}
      </header>
      <div className="center-col"><ChatView /></div>
      <div className="rightbar-col">{settingsOpen ? null : <PaperWorkspace />}</div>
      {!effectiveCollapsed && <DragHandle side="sidebar" left={cols.sidebar} onStart={onSidebarStart} onDrag={onSidebarDrag} onEnd={onDragEnd} onReset={() => set({ sidebarWidth: null })} />}
      {cols.rightbar > 0 && (
        <DragHandle side="rightbar" left={viewport - cols.rightbar} onStart={onRightbarStart} onDrag={onRightbarDrag} onEnd={onDragEnd} onReset={() => set({ rightbarWidth: null })} />
      )}
      <div className="status-row"><Statusbar /></div>
    </div>
  )
}

function Statusbar() {
  const project = useStore(s => s.currentProject)
  const user = useStore(s => s.user)
  const fulltext = useStore(s => s.fulltext)
  return (
    <div className="statusbar">
      <span className="sb-brand-mini">墨记</span>
      <span className="sep">·</span>
      <span>{project ? `项目：${project}` : '未确定工作区'}</span>
      {fulltext?.active && (
        <>
          <span className="sep">·</span>
          <span className="ft-chip"><Icon name="play" size={12} />全文 {fulltext.current ?? 0}/{fulltext.total ?? 0}</span>
        </>
      )}
      <span className="grow" />
      {user?.username && <span className="user-chip">{user.username}{user.role === 'admin' ? ' · 管理员' : ''}</span>}
    </div>
  )
}
