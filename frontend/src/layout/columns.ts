/**
 * 三栏几何：移植 DSH ui-layout/src/client/columns.ts。
 * rightbar 默认 54% = 墨记「左对话右论文」工作流。
 */

export interface Columns { sidebar: number; center: number; rightbar: number }

export const CENTER_MIN = 400
export const SIDEBAR_MIN = 264
export const SIDEBAR_MAX = 420
export const SIDEBAR_DEFAULT = 280
export const SIDEBAR_COLLAPSED = 56
export const SIDEBAR_AUTO_COLLAPSE = 1024
export const RIGHTBAR_MIN = 300
export const RIGHTBAR_MAX_RATIO = 0.7
export const RIGHTBAR_DEFAULT_RATIO = 0.54

export function clampWidth(px: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(px)))
}

export function computeColumns(viewport: number, sidebar: number, rightbar: number, collapsedWidth = SIDEBAR_COLLAPSED): Columns {
  const s = sidebar === 0 ? collapsedWidth : clampWidth(sidebar, SIDEBAR_MIN, SIDEBAR_MAX)
  const available = viewport - s - CENTER_MIN
  const r = rightbar === 0 || available < RIGHTBAR_MIN
    ? 0
    : Math.min(available, clampWidth(rightbar, RIGHTBAR_MIN, viewport * RIGHTBAR_MAX_RATIO))
  return { sidebar: s, center: Math.max(0, viewport - s - r), rightbar: r }
}
