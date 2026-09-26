import type { AppConfig, Layout, Meta, PlaylistConfig, PlaylistItem } from '../types'

interface Props {
  config: AppConfig
  meta: Meta
  onConfigChange: (config: AppConfig) => void
  /** Which screen the preview pane is showing, so the list can mark it. */
  previewLayout: string | null
  onPreviewLayout: (layoutId: string) => void
}

/** Ordered rotation of screens: while it is on, the display cycles through these
 * layouts instead of sitting on the single active one. */
export default function PlaylistSection({
  config,
  meta,
  onConfigChange,
  previewLayout,
  onPreviewLayout,
}: Props) {
  const playlist = config.playlist
  const onChange = (next: PlaylistConfig) => onConfigChange({ ...config, playlist: next })
  const setItems = (items: PlaylistItem[]) => onChange({ ...playlist, items })

  const choices: Layout[] = [...config.layout.layouts, ...meta.presets]
  const layoutName = (id: string) => choices.find((l) => l.id === id)?.name ?? id
  const known = (id: string) => choices.some((l) => l.id === id)

  const patch = (index: number, changes: Partial<PlaylistItem>) =>
    setItems(playlist.items.map((item, i) => (i === index ? { ...item, ...changes } : item)))

  const move = (index: number, delta: number) => {
    const target = index + delta
    if (target < 0 || target >= playlist.items.length) return
    const items = [...playlist.items]
    ;[items[index], items[target]] = [items[target], items[index]]
    setItems(items)
  }

  const add = () => {
    const last = playlist.items[playlist.items.length - 1]
    const layout = last?.layout ?? config.layout.active
    setItems([...playlist.items, { layout, minutes: last?.minutes ?? 15, enabled: true }])
    onPreviewLayout(layout)
  }

  const running = playlist.items.filter((i) => i.enabled && known(i.layout))
  const cycleMinutes = running.reduce((total, item) => total + item.minutes, 0)
  const atMax = playlist.items.length >= meta.limits.max_playlist_items

  return (
    <fieldset>
      <legend>Playlist</legend>

      <label className="checkbox playlist-toggle">
        <input
          type="checkbox"
          checked={playlist.enabled}
          onChange={(e) => onChange({ ...playlist, enabled: e.target.checked })}
        />
        Cycle through screens
      </label>
      <p className="muted">
        {playlist.enabled
          ? 'The display works down this list, showing each screen for its dwell time, then starts again.'
          : 'Off — the display stays on the layout selected in Layout & widgets.'}
      </p>

      {playlist.items.length === 0 ? (
        <p className="muted">No screens yet — add one to start building the rotation.</p>
      ) : (
        <ol className="playlist">
          {playlist.items.map((item, index) => {
            const missing = !known(item.layout)
            const classes = [
              item.layout === previewLayout ? 'editing' : '',
              item.enabled && !missing ? '' : 'skipped',
            ]
              .filter(Boolean)
              .join(' ')
            return (
              // Rows carry no id of their own; the whole list re-renders on any
              // reorder, so the index is a stable enough key here.
              <li
                key={index}
                className={classes}
                // Touching a row previews its screen. Focus covers the controls
                // (a click on the select never reaches the row's own handler).
                onClick={() => onPreviewLayout(item.layout)}
                onFocusCapture={() => onPreviewLayout(item.layout)}
              >
                <label className="checkbox" title="Include this screen in the rotation">
                  <input
                    type="checkbox"
                    checked={item.enabled}
                    onChange={(e) => patch(index, { enabled: e.target.checked })}
                  />
                </label>
                <select
                  value={item.layout}
                  // a narrow config column truncates the name in the closed select
                  title={layoutName(item.layout)}
                  // the row's click handler would otherwise fire with the
                  // pre-change layout and undo the preview this sets
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => {
                    patch(index, { layout: e.target.value })
                    onPreviewLayout(e.target.value)
                  }}
                >
                  {missing && <option value={item.layout}>{item.layout} (deleted)</option>}
                  {choices.map((layout) => (
                    <option key={layout.id} value={layout.id}>
                      {layout.name}
                    </option>
                  ))}
                </select>
                <label className="playlist-minutes" title="Minutes on screen before moving on">
                  <input
                    type="number"
                    min={1}
                    max={720}
                    value={item.minutes}
                    onChange={(e) => patch(index, { minutes: Number(e.target.value) })}
                  />
                </label>
                <span className="muted">min</span>
                <button type="button" title="Move up" onClick={() => move(index, -1)}>
                  ↑
                </button>
                <button type="button" title="Move down" onClick={() => move(index, 1)}>
                  ↓
                </button>
                <button
                  type="button"
                  title="Remove"
                  onClick={() => setItems(playlist.items.filter((_, i) => i !== index))}
                >
                  ✕
                </button>
              </li>
            )
          })}
        </ol>
      )}

      <div className="row">
        <button type="button" onClick={add} disabled={atMax}>
          Add screen
        </button>
        {atMax && <span className="muted">Limit of {meta.limits.max_playlist_items} screens</span>}
      </div>

      {running.length > 0 && (
        <p className="muted">
          Full cycle: {formatDuration(cycleMinutes)} across {running.length}{' '}
          {running.length === 1 ? 'screen' : 'screens'}. Each change flashes the panel for ~30
          seconds while it refreshes, so short dwell times mean a lot of flashing.
        </p>
      )}
      {playlist.enabled && running.length === 0 && (
        <p className="muted">
          Nothing to cycle — the display stays on “{layoutName(config.layout.active)}” until at
          least one screen here is enabled.
        </p>
      )}
    </fieldset>
  )
}

function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} h ${rest} min` : `${hours} h`
}
