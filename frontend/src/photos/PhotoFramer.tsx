import { useEffect, useRef, useState } from 'react'
import { photoPreviewUrl } from '../api'
import type { PhotoEntry } from '../types'

/** The default: dead centre. A photo with no stored focus is framed like this,
 * and "Centre" puts one back here. */
export const CENTRE: [number, number] = [0.5, 0.5]

interface Props {
  photo: PhotoEntry
  /** Pixel size of the photos panel on the display — the shape being cropped to. */
  frame: [number, number]
  /** The panel's framing setting: "contain" shows the whole photo, so there is
   * nothing to position and the drag surface is inert. */
  fit: 'cover' | 'contain'
  onSave: (focus: [number, number] | null) => Promise<void>
  onClose: () => void
}

const clamp = (value: number) => Math.min(Math.max(value, 0), 1)

export function focusOf(photo: PhotoEntry): [number, number] {
  const stored = photo.focus
  if (!stored || stored.length !== 2) return CENTRE
  return [clamp(stored[0]), clamp(stored[1])]
}

export function isCentred(focus: [number, number]): boolean {
  return Math.abs(focus[0] - 0.5) < 0.005 && Math.abs(focus[1] - 0.5) < 0.005
}

/** Positions one photo inside the panel's crop.
 *
 * The live drag is done in CSS — `object-fit: cover` with `object-position` set
 * from the focus is exactly the crop `ImageOps.fit(centering=…)` performs on the
 * device, so dragging is instant and needs no server round trip. The "show as
 * e-ink" view swaps in the real render from /api/photos/preview, which is the
 * only honest picture of a photograph on a six-colour panel.
 */
export default function PhotoFramer({ photo, frame, fit, onSave, onClose }: Props) {
  const [focus, setFocus] = useState<[number, number]>(() => focusOf(photo))
  const [eink, setEink] = useState(false)
  const [saving, setSaving] = useState(false)
  const [dragging, setDragging] = useState(false)
  const frameRef = useRef<HTMLDivElement>(null)
  const drag = useRef<{ x: number; y: number; from: [number, number] } | null>(null)

  const positionable = fit === 'cover'
  const dirty = focus[0] !== focusOf(photo)[0] || focus[1] !== focusOf(photo)[1]

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!positionable || eink) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, y: e.clientY, from: focus }
    setDragging(true)
  }

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const start = drag.current
    const box = frameRef.current?.getBoundingClientRect()
    if (!start || !box) return
    // How much of the photo is hidden by the crop, on screen. Dragging right
    // reveals what is off to the left, so the anchor moves the other way; an
    // axis with nothing hidden (the photo already fits) simply doesn't move.
    const scale = Math.max(box.width / photo.width, box.height / photo.height)
    const hiddenX = photo.width * scale - box.width
    const hiddenY = photo.height * scale - box.height
    setFocus([
      hiddenX > 0 ? clamp(start.from[0] - (e.clientX - start.x) / hiddenX) : start.from[0],
      hiddenY > 0 ? clamp(start.from[1] - (e.clientY - start.y) / hiddenY) : start.from[1],
    ])
  }

  const endDrag = () => {
    drag.current = null
    setDragging(false)
  }

  const nudge = (e: React.KeyboardEvent) => {
    if (!positionable) return
    const step = e.shiftKey ? 0.1 : 0.02
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    }
    const move = moves[e.key]
    if (!move) return
    e.preventDefault()
    setFocus(([x, y]) => [clamp(x + move[0]), clamp(y + move[1])])
  }

  const save = async () => {
    setSaving(true)
    try {
      await onSave(isCentred(focus) ? null : focus)
    } finally {
      setSaving(false)
    }
  }

  const aspect = `${frame[0]} / ${frame[1]}`

  return (
    <div className="framer-backdrop" onMouseDown={onClose}>
      <div
        className="framer"
        role="dialog"
        aria-label={`Position ${photo.filename || 'photo'}`}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="row framer-head">
          <strong>{photo.filename || 'Photo'}</strong>
          <span className="spacer" />
          <label className="checkbox" title="Dither to the panel's six colours">
            <input type="checkbox" checked={eink} onChange={(e) => setEink(e.target.checked)} />
            Show as e-ink
          </label>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>

        <div
          ref={frameRef}
          className={`framer-frame${positionable && !eink ? ' draggable' : ''}${
            dragging ? ' dragging' : ''
          }`}
          style={{ aspectRatio: aspect }}
          tabIndex={positionable ? 0 : -1}
          onKeyDown={nudge}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endDrag}
          onPointerCancel={endDrag}
        >
          {eink ? (
            // The focus is in the URL, so moving it re-requests the render.
            <img
              className="framer-render"
              src={photoPreviewUrl(photo.id, focus, true)}
              alt="the photo as the panel will show it"
            />
          ) : (
            <img
              className="framer-photo"
              src={`/api/photos/full/${photo.id}`}
              alt={photo.filename || 'photo'}
              draggable={false}
              style={{
                objectFit: positionable ? 'cover' : 'contain',
                objectPosition: `${focus[0] * 100}% ${focus[1] * 100}%`,
              }}
            />
          )}
        </div>

        <p className="muted">
          {positionable
            ? `Drag the photo to choose what survives the crop — arrow keys nudge it. The frame is
               the photos panel at ${frame[0]}×${frame[1]} on the display.`
            : 'Framing is set to “Fit”, so the whole photo is shown and there is nothing to crop. Switch to “Fill” to position it.'}
          {eink &&
            ' Approximate: the panel applies its own colour curve, so treat this as a guide to how much detail survives.'}
        </p>

        <div className="row">
          <button
            type="button"
            className="primary"
            onClick={save}
            disabled={saving || !dirty || !positionable}
          >
            {saving ? 'Saving…' : 'Save position'}
          </button>
          <button
            type="button"
            onClick={() => setFocus(CENTRE)}
            disabled={saving || !positionable || isCentred(focus)}
          >
            Centre
          </button>
          <span className="muted">
            {isCentred(focus)
              ? 'Centred — the default.'
              : `Anchored at ${Math.round(focus[0] * 100)}%, ${Math.round(focus[1] * 100)}%.`}
          </span>
        </div>
      </div>
    </div>
  )
}
