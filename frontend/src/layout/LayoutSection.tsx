import { useState } from 'react'
import type { AppConfig, CalendarInfo, Layout, LayoutConfig, Meta } from '../types'
import ConfigInspector from '../config/ConfigInspector'
import LayoutEditor from './LayoutEditor'
import LayoutLibrary from './LayoutLibrary'
import ModulePalette from './ModulePalette'
import { uniqueId } from './gridMath'

const FALLBACK = 'dashboard_clock'

interface Props {
  config: AppConfig
  meta: Meta
  onConfigChange: (config: AppConfig) => void
  calendars: CalendarInfo[] | null
  /** Which layout is being edited. Owned by App so the preview pane can follow
   * it, and so switching tabs and coming back resumes where the user left off. */
  editingId: string
  onEditLayout: (layoutId: string) => void
}

export default function LayoutSection({
  config,
  meta,
  onConfigChange,
  calendars,
  editingId,
  onEditLayout,
}: Props) {
  const layoutConfig = config.layout
  const onChange = (layout: LayoutConfig) => onConfigChange({ ...config, layout })
  const [selected, setSelected] = useState<number | null>(null)

  const presetIds = new Set(meta.presets.map((p) => p.id))
  const findLayout = (id: string): Layout | undefined =>
    layoutConfig.layouts.find((l) => l.id === id) ?? meta.presets.find((p) => p.id === id)

  // Fall back gracefully if the edited layout vanished (deleted, or draft discarded)
  const editing = findLayout(editingId) ?? findLayout(layoutConfig.active) ?? meta.presets[0]
  const isPreset = presetIds.has(editing.id)

  const takenIds = () => new Set([...presetIds, ...layoutConfig.layouts.map((l) => l.id)])

  // While a playlist is cycling it, not layout.active, decides what's on the panel.
  const playlistRunning =
    config.playlist.enabled &&
    config.playlist.items.some((item) => item.enabled && findLayout(item.layout))

  // Presets are immutable server-side: editing one forks it into a custom copy.
  const applyEdit = (updated: Layout) => {
    if (isPreset) {
      const id = uniqueId(`${editing.id}-copy`, takenIds())
      const fork = { ...updated, id, name: `Copy of ${editing.name}` }
      onEditLayout(id)
      onChange({
        active: layoutConfig.active === editing.id ? id : layoutConfig.active,
        layouts: [...layoutConfig.layouts, fork],
      })
    } else {
      onChange({
        ...layoutConfig,
        layouts: layoutConfig.layouts.map((l) => (l.id === editing.id ? updated : l)),
      })
    }
  }

  // A module lives in at most one region: placing it clears its previous spot.
  const assignModule = (module: string | null, target: number) => {
    applyEdit({
      ...editing,
      regions: editing.regions.map((r, i) => {
        let m = r.module === module ? null : r.module
        if (i === target) m = module
        return { ...r, module: m }
      }),
    })
  }

  const addLayout = (layout: Layout) => {
    onChange({ ...layoutConfig, layouts: [...layoutConfig.layouts, layout] })
    onEditLayout(layout.id)
    setSelected(null)
  }

  return (
    <fieldset>
      <legend>Layout</legend>
      <LayoutLibrary
        presets={meta.presets}
        customs={layoutConfig.layouts}
        active={layoutConfig.active}
        editingId={editing.id}
        playlistRunning={playlistRunning}
        onSetActive={(id) => onChange({ ...layoutConfig, active: id })}
        onEdit={(id) => {
          onEditLayout(id)
          setSelected(null)
        }}
        onNew={() =>
          addLayout({
            id: uniqueId('custom', takenIds()),
            name: 'New layout',
            cols: meta.limits.default_cols,
            rows: meta.limits.default_rows,
            regions: [],
          })
        }
        onDuplicate={(layout) =>
          addLayout({
            ...layout,
            id: uniqueId(`${layout.id}-copy`, takenIds()),
            name: `Copy of ${layout.name}`,
            regions: layout.regions.map((r) => ({ ...r })),
          })
        }
        onDelete={(id) => {
          const active = layoutConfig.active === id ? FALLBACK : layoutConfig.active
          if (editingId === id) {
            onEditLayout(active)
            setSelected(null)
          }
          // Playlist screens pointing at the deleted layout go with it — the
          // backend rejects a config that references a layout that isn't there.
          onConfigChange({
            ...config,
            layout: { active, layouts: layoutConfig.layouts.filter((l) => l.id !== id) },
            playlist: {
              ...config.playlist,
              items: config.playlist.items.filter((item) => item.layout !== id),
            },
          })
        }}
      />

      {isPreset ? (
        <p className="muted">Editing preset “{editing.name}” — your first change creates a copy.</p>
      ) : (
        <label>
          Layout name
          <input
            value={editing.name}
            maxLength={60}
            onChange={(e) => applyEdit({ ...editing, name: e.target.value })}
          />
        </label>
      )}

      <LayoutEditor
        layout={editing}
        resolution={meta.resolution}
        minRegion={meta.limits.min_region}
        maxRegions={meta.limits.max_regions}
        moduleNames={Object.fromEntries(meta.modules.map((m) => [m.id, m.name]))}
        selected={selected}
        onSelect={setSelected}
        onRegionsChange={(regions) => applyEdit({ ...editing, regions })}
      />

      <ModulePalette
        modules={meta.modules}
        layout={editing}
        selected={selected}
        onAssign={assignModule}
      />

      <ConfigInspector
        layout={editing}
        selected={selected}
        onSelect={setSelected}
        modules={meta.modules}
        config={config}
        onChange={onConfigChange}
        calendars={calendars}
      />
    </fieldset>
  )
}
