import { useRef, useState } from 'react'
import type { Layout, Region } from '../types'
import {
  clamp,
  movedRect,
  overlapsAny,
  pixelSize,
  pointToCell,
  rectStyle,
  regionAt,
  resizedRect,
  spanRect,
  type CellRect,
} from './gridMath'

type HandleDir = -1 | 0 | 1

type Drag =
  | { kind: 'draw'; anchorCol: number; anchorRow: number }
  | { kind: 'move'; index: number; offsetCol: number; offsetRow: number }
  | { kind: 'resize'; index: number; dx: HandleDir; dy: HandleDir; offCol: number; offRow: number }

const HANDLE_PX = 10

const handleOffset = (dir: HandleDir) =>
  dir === -1 ? '0px' : dir === 1 ? `calc(100% - ${HANDLE_PX}px)` : `calc(50% - ${HANDLE_PX / 2}px)`

const HANDLES: Array<{ dx: HandleDir; dy: HandleDir; cursor: string }> = [
  { dx: -1, dy: -1, cursor: 'nwse-resize' },
  { dx: 0, dy: -1, cursor: 'ns-resize' },
  { dx: 1, dy: -1, cursor: 'nesw-resize' },
  { dx: -1, dy: 0, cursor: 'ew-resize' },
  { dx: 1, dy: 0, cursor: 'ew-resize' },
  { dx: -1, dy: 1, cursor: 'nesw-resize' },
  { dx: 0, dy: 1, cursor: 'ns-resize' },
  { dx: 1, dy: 1, cursor: 'nwse-resize' },
]

interface Props {
  layout: Layout
  resolution: [number, number]
  minRegion: number
  maxRegions: number
  moduleNames: Record<string, string>
  selected: number | null
  onSelect: (index: number | null) => void
  onRegionsChange: (regions: Region[]) => void
}

export default function LayoutEditor({
  layout,
  resolution,
  minRegion,
  maxRegions,
  moduleNames,
  selected,
  onSelect,
  onRegionsChange,
}: Props) {
  const canvasRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<Drag | null>(null)
  const [drawRect, setDrawRect] = useState<CellRect | null>(null)

  const toCell = (e: React.PointerEvent) => {
    const box = canvasRef.current!.getBoundingClientRect()
    return pointToCell(
      e.clientX - box.left,
      e.clientY - box.top,
      box.width,
      box.height,
      layout.cols,
      layout.rows,
    )
  }

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    const target = e.target as HTMLElement
    const { col, row } = toCell(e)
    const handle = target.closest<HTMLElement>('[data-handle]')
    if (handle && selected !== null) {
      const [dx, dy] = handle.dataset.handle!.split(',').map(Number) as [HandleDir, HandleDir]
      const region = layout.regions[selected]
      // handles are inset, so remember where the edge sits relative to the grab
      // point — otherwise the region jumps a cell the moment you start dragging
      dragRef.current = {
        kind: 'resize',
        index: selected,
        dx,
        dy,
        offCol: dx === -1 ? region.x - col : dx === 1 ? region.x + region.w - col - 1 : 0,
        offRow: dy === -1 ? region.y - row : dy === 1 ? region.y + region.h - row - 1 : 0,
      }
    } else {
      const index = regionAt(layout.regions, col, row)
      if (index >= 0) {
        const region = layout.regions[index]
        dragRef.current = { kind: 'move', index, offsetCol: col - region.x, offsetRow: row - region.y }
        onSelect(index)
      } else if (layout.regions.length < maxRegions) {
        dragRef.current = { kind: 'draw', anchorCol: col, anchorRow: row }
        onSelect(null)
      } else {
        onSelect(null)
        return
      }
    }
    canvasRef.current!.setPointerCapture(e.pointerId)
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const drag = dragRef.current
    if (!drag) return
    const { col, row } = toCell(e)
    if (drag.kind === 'draw') {
      const rect = spanRect(drag.anchorCol, drag.anchorRow, col, row)
      // never show an invalid rect: keep the last valid span while the pointer
      // is over another region
      if (!overlapsAny(rect, layout.regions)) setDrawRect(rect)
    } else if (drag.kind === 'move') {
      const rect = movedRect(
        layout.regions[drag.index],
        col - drag.offsetCol,
        row - drag.offsetRow,
        layout,
        drag.index,
      )
      if (rect) applyRect(drag.index, rect)
    } else {
      const rect = resizedRect(
        layout.regions[drag.index],
        drag.dx,
        drag.dy,
        col + drag.offCol,
        row + drag.offRow,
        layout,
        drag.index,
        minRegion,
      )
      if (rect) applyRect(drag.index, rect)
    }
  }

  const applyRect = (index: number, rect: CellRect) => {
    const current = layout.regions[index]
    if (
      current.x === rect.x &&
      current.y === rect.y &&
      current.w === rect.w &&
      current.h === rect.h
    ) {
      return
    }
    onRegionsChange(layout.regions.map((r, i) => (i === index ? { ...r, ...rect } : r)))
  }

  const onPointerUp = () => {
    const drag = dragRef.current
    dragRef.current = null
    if (drag?.kind === 'draw' && drawRect) {
      // grow a too-small sketch up to the minimum where the grid allows it
      const rect = {
        x: clamp(drawRect.x, 0, layout.cols - Math.max(drawRect.w, minRegion)),
        y: clamp(drawRect.y, 0, layout.rows - Math.max(drawRect.h, minRegion)),
        w: Math.max(drawRect.w, minRegion),
        h: Math.max(drawRect.h, minRegion),
      }
      if (!overlapsAny(rect, layout.regions)) {
        onRegionsChange([...layout.regions, { ...rect, module: null }])
        onSelect(layout.regions.length)
      }
    }
    setDrawRect(null)
  }

  const selectedRegion = selected !== null ? layout.regions[selected] : null
  const selectedPx = selectedRegion ? pixelSize(selectedRegion, layout, resolution) : null

  return (
    <div className="layout-editor">
      <div
        ref={canvasRef}
        className="layout-canvas"
        style={{
          aspectRatio: `${resolution[0]} / ${resolution[1]}`,
          backgroundSize: `${100 / layout.cols}% ${100 / layout.rows}%`,
        }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {layout.regions.map((region, i) => (
          <div
            key={i}
            data-region-index={i}
            className={[
              'layout-region',
              region.module ? '' : 'blank',
              i === selected ? 'selected' : '',
            ].join(' ')}
            style={rectStyle(region, layout.cols, layout.rows)}
          >
            <span className="region-label">
              {region.module ? (moduleNames[region.module] ?? region.module) : 'empty'}
            </span>
            {i === selected &&
              HANDLES.map(({ dx, dy, cursor }) => (
                <span
                  key={`${dx},${dy}`}
                  data-handle={`${dx},${dy}`}
                  className="region-handle"
                  style={{
                    cursor,
                    // inset rather than centred on the edge: regions sit flush
                    // against the canvas, where a centred handle is clipped away
                    left: handleOffset(dx),
                    top: handleOffset(dy),
                  }}
                />
              ))}
          </div>
        ))}
        {drawRect && (
          <div
            className="layout-region drawing"
            style={rectStyle(drawRect, layout.cols, layout.rows)}
          />
        )}
      </div>
      <div className="row editor-toolbar">
        {selectedRegion && selectedPx ? (
          <>
            <span className="muted">
              {selectedPx.w}×{selectedPx.h} px
            </span>
            {selectedRegion.module && (
              <button
                type="button"
                onClick={() =>
                  onRegionsChange(
                    layout.regions.map((r, i) => (i === selected ? { ...r, module: null } : r)),
                  )
                }
              >
                Clear module
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                onSelect(null)
                onRegionsChange(layout.regions.filter((_, i) => i !== selected))
              }}
            >
              Remove region
            </button>
          </>
        ) : (
          <span className="muted">
            {layout.regions.length < maxRegions
              ? 'Drag on empty space to add a region; drag regions to move, edges to resize.'
              : `Region limit reached (${maxRegions}).`}
          </span>
        )}
      </div>
    </div>
  )
}
