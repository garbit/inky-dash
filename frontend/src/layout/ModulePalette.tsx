import { useRef, useState } from 'react'
import type { Layout, ModuleInfo } from '../types'

interface Props {
  modules: ModuleInfo[]
  layout: Layout
  selected: number | null
  onAssign: (module: string | null, regionIndex: number) => void
}

/** Drag a chip onto a region to place a module (moving it if already placed),
 * or select a region and click a chip. */
export default function ModulePalette({ modules, layout, selected, onAssign }: Props) {
  const [ghost, setGhost] = useState<{ x: number; y: number; label: string } | null>(null)
  const dragModule = useRef<string | null>(null)
  const dragMoved = useRef(false)

  const dropTarget = (x: number, y: number): number | null => {
    const el = document.elementFromPoint(x, y)?.closest<HTMLElement>('[data-region-index]')
    return el ? Number(el.dataset.regionIndex) : null
  }

  const onPointerDown = (e: React.PointerEvent, module: ModuleInfo) => {
    if (e.button !== 0) return
    dragModule.current = module.id
    dragMoved.current = false
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    setGhost({ x: e.clientX, y: e.clientY, label: module.name })
  }

  const onPointerMove = (e: React.PointerEvent) => {
    if (dragModule.current === null) return
    dragMoved.current = true
    setGhost((g) => (g ? { ...g, x: e.clientX, y: e.clientY } : g))
  }

  const onPointerUp = (e: React.PointerEvent) => {
    const module = dragModule.current
    dragModule.current = null
    setGhost(null)
    if (module === null) return
    if (dragMoved.current) {
      const target = dropTarget(e.clientX, e.clientY)
      if (target !== null) onAssign(module, target)
    } else if (selected !== null) {
      onAssign(module, selected) // click-to-assign fallback
    }
  }

  const placed = new Set(layout.regions.map((r) => r.module).filter(Boolean))

  return (
    <div className="module-palette">
      {modules.map((m) => (
        <button
          key={m.id}
          type="button"
          className={`module-chip${placed.has(m.id) ? ' placed' : ''}`}
          title={
            placed.has(m.id)
              ? `${m.name} is placed — drag to move it to another region`
              : `Drag onto a region${selected !== null ? ' or click to assign' : ''}`
          }
          onPointerDown={(e) => onPointerDown(e, m)}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
        >
          {m.name}
        </button>
      ))}
      {ghost && (
        <span className="module-chip drag-ghost" style={{ left: ghost.x, top: ghost.y }}>
          {ghost.label}
        </span>
      )}
    </div>
  )
}
