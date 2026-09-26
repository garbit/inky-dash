import type { Layout, Region } from '../types'

export interface CellRect {
  x: number
  y: number
  w: number
  h: number
}

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

export const overlaps = (a: CellRect, b: CellRect) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h

export const overlapsAny = (rect: CellRect, regions: Region[], skipIndex = -1) =>
  regions.some((r, i) => i !== skipIndex && overlaps(rect, r))

/** Pointer position (px within the editor canvas) to the cell it falls in. */
export function pointToCell(
  px: number,
  py: number,
  canvasW: number,
  canvasH: number,
  cols: number,
  rows: number,
) {
  return {
    col: clamp(Math.floor((px / canvasW) * cols), 0, cols - 1),
    row: clamp(Math.floor((py / canvasH) * rows), 0, rows - 1),
  }
}

/** Index of the region containing a cell, or -1. */
export const regionAt = (regions: Region[], col: number, row: number) =>
  regions.findIndex((r) => col >= r.x && col < r.x + r.w && row >= r.y && row < r.y + r.h)

/** Normalised rect spanning two cells (inclusive). */
export function spanRect(aCol: number, aRow: number, bCol: number, bRow: number): CellRect {
  const x = Math.min(aCol, bCol)
  const y = Math.min(aRow, bRow)
  return { x, y, w: Math.abs(aCol - bCol) + 1, h: Math.abs(aRow - bRow) + 1 }
}

/** Move a region to a new origin, clamped to the grid; null if it would overlap. */
export function movedRect(
  region: Region,
  col: number,
  row: number,
  layout: Layout,
  index: number,
): CellRect | null {
  const rect = {
    x: clamp(col, 0, layout.cols - region.w),
    y: clamp(row, 0, layout.rows - region.h),
    w: region.w,
    h: region.h,
  }
  return overlapsAny(rect, layout.regions, index) ? null : rect
}

/**
 * Resize a region by a handle. dx/dy say which edges the handle controls
 * (-1 = left/top, 1 = right/bottom, 0 = untouched); the pointer cell becomes
 * the new position of those edges. Null if invalid (too small or overlapping).
 */
export function resizedRect(
  region: Region,
  handleDx: -1 | 0 | 1,
  handleDy: -1 | 0 | 1,
  col: number,
  row: number,
  layout: Layout,
  index: number,
  minCells: number,
): CellRect | null {
  let { x, y } = region
  let x2 = region.x + region.w
  let y2 = region.y + region.h
  if (handleDx === -1) x = clamp(col, 0, x2 - minCells)
  if (handleDx === 1) x2 = clamp(col + 1, x + minCells, layout.cols)
  if (handleDy === -1) y = clamp(row, 0, y2 - minCells)
  if (handleDy === 1) y2 = clamp(row + 1, y + minCells, layout.rows)
  const rect = { x, y, w: x2 - x, h: y2 - y }
  return overlapsAny(rect, layout.regions, index) ? null : rect
}

/** The rect's size in physical panel pixels (matches the backend's region_rect). */
export function pixelSize(rect: CellRect, layout: Layout, resolution: [number, number]) {
  const [w, h] = resolution
  const x0 = Math.floor((rect.x * w) / layout.cols)
  const y0 = Math.floor((rect.y * h) / layout.rows)
  const x1 = Math.floor(((rect.x + rect.w) * w) / layout.cols)
  const y1 = Math.floor(((rect.y + rect.h) * h) / layout.rows)
  return { w: x1 - x0, h: y1 - y0 }
}

/** Percentage-based CSS position for a cell rect within the editor canvas. */
export function rectStyle(rect: CellRect, cols: number, rows: number) {
  return {
    left: `${(rect.x / cols) * 100}%`,
    top: `${(rect.y / rows) * 100}%`,
    width: `${(rect.w / cols) * 100}%`,
    height: `${(rect.h / rows) * 100}%`,
  }
}

/** A layout id not already taken, derived from a base slug. */
export function uniqueId(base: string, taken: Set<string>): string {
  const slug = base.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'layout'
  if (!taken.has(slug)) return slug
  for (let n = 2; ; n++) {
    const candidate = `${slug}-${n}`
    if (!taken.has(candidate)) return candidate
  }
}
