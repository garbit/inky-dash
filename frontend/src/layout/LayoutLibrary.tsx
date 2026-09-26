import type { Layout } from '../types'

interface Props {
  presets: Layout[]
  customs: Layout[]
  active: string
  editingId: string
  /** A running playlist supersedes the active layout as what the panel shows. */
  playlistRunning: boolean
  onSetActive: (id: string) => void
  onEdit: (id: string) => void
  onNew: () => void
  onDuplicate: (layout: Layout) => void
  onDelete: (id: string) => void
}

export default function LayoutLibrary({
  presets,
  customs,
  active,
  editingId,
  playlistRunning,
  onSetActive,
  onEdit,
  onNew,
  onDuplicate,
  onDelete,
}: Props) {
  const row = (layout: Layout, isPreset: boolean) => (
    <li key={layout.id} className={layout.id === editingId ? 'editing' : ''}>
      <label
        className="checkbox"
        title={playlistRunning ? 'Shown when the playlist is off' : 'Shown on the display'}
      >
        <input
          type="radio"
          name="active-layout"
          checked={layout.id === active}
          onChange={() => onSetActive(layout.id)}
        />
      </label>
      <button type="button" className="layout-name" onClick={() => onEdit(layout.id)}>
        {layout.name}
        {isPreset && <span className="badge">preset</span>}
      </button>
      <button type="button" title="Duplicate" onClick={() => onDuplicate(layout)}>
        ⧉
      </button>
      {!isPreset && (
        <button
          type="button"
          title="Delete"
          onClick={() => {
            if (window.confirm(`Delete layout “${layout.name}”?`)) onDelete(layout.id)
          }}
        >
          ✕
        </button>
      )}
    </li>
  )

  return (
    <div className="layout-library">
      <ul>
        {customs.map((l) => row(l, false))}
        {presets.map((l) => row(l, true))}
      </ul>
      <button type="button" onClick={onNew}>
        New blank layout
      </button>
      {playlistRunning && (
        <p className="muted">
          The playlist is cycling screens, so it — not the selected layout — decides what the
          display shows.
        </p>
      )}
    </div>
  )
}
