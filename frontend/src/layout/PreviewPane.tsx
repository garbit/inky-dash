import { useEffect, useRef, useState } from 'react'
import { getStatus, postPlaylistNext, postPreview } from '../api'
import type { AppConfig, Status } from '../types'

interface Props {
  config: AppConfig
  /** The layout to render — the one being edited or a playlist screen. */
  layout: string | null
  dirty: boolean
  status: Status | null
  onRefreshNow: () => void
  onStatusChange: (status: Status) => void
}

/** Server-rendered preview of the draft config — exactly what the panel will
 * show, fetched (debounced) whenever the draft changes. */
export default function PreviewPane({
  config,
  layout,
  dirty,
  status,
  onRefreshNow,
  onStatusChange,
}: Props) {
  const [url, setUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Dither the preview down to the panel's six colours. Text panels look much the
  // same either way; a photograph does not.
  const [simulate, setSimulate] = useState(false)
  const urlRef = useRef<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    const timer = setTimeout(async () => {
      setLoading(true)
      try {
        const blob = await postPreview(config, layout, controller.signal, simulate)
        const next = URL.createObjectURL(blob)
        if (urlRef.current) URL.revokeObjectURL(urlRef.current)
        urlRef.current = next
        setUrl(next)
        setError(null)
      } catch (err) {
        if (!controller.signal.aborted) setError(String(err instanceof Error ? err.message : err))
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }, 600)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [config, layout, simulate])

  useEffect(
    () => () => {
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
    },
    [],
  )

  const playlist = status?.playlist ?? null

  const nextScreen = async () => {
    await postPlaylistNext()
    // the display takes ~30s to redraw; re-read once the switch has been kicked off
    setTimeout(() => {
      getStatus().then(onStatusChange).catch(() => {})
    }, 1500)
  }

  return (
    <>
      <div className={`preview-frame${loading ? ' loading' : ''}`}>
        {url ? (
          <img className="preview" src={url} alt="Dashboard preview" />
        ) : (
          <p className="muted">{error ?? 'Rendering preview…'}</p>
        )}
      </div>
      {error && url && <p className="muted">Preview failed: {error}</p>}
      <div className="row">
        <button onClick={onRefreshNow} disabled={dirty} title={dirty ? 'Save first' : undefined}>
          Refresh display now
        </button>
        {playlist && (
          <button onClick={nextScreen} disabled={dirty} title={dirty ? 'Save first' : undefined}>
            Next screen
          </button>
        )}
        {dirty && <span className="preview-tag">unsaved preview</span>}
        <label className="checkbox" title="Dither to the panel's six colours">
          <input
            type="checkbox"
            checked={simulate}
            onChange={(e) => setSimulate(e.target.checked)}
          />
          Show as e-ink
        </label>
        <span className="muted">
          last render:{' '}
          {status?.last_render ? new Date(status.last_render).toLocaleString() : 'never'}
        </span>
      </div>
      {simulate && (
        <p className="muted">
          Approximate: the panel applies its own colour curve, so treat this as a guide to how
          much detail survives rather than an exact match.
        </p>
      )}
      {playlist && (
        <p className="muted">
          On the display: screen {playlist.position} of {playlist.count} — “{playlist.name}”,{' '}
          {Math.max(1, Math.round(playlist.seconds_remaining / 60))} min left of{' '}
          {playlist.minutes}.
        </p>
      )}
    </>
  )
}
