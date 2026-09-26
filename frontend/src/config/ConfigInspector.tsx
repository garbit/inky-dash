import type { AppConfig, CalendarInfo, Layout, ModuleInfo, ModuleSection } from '../types'
import OptionsForm from './OptionsForm'

interface Props {
  layout: Layout
  selected: number | null
  onSelect: (index: number | null) => void
  modules: ModuleInfo[]
  config: AppConfig
  onChange: (config: AppConfig) => void
  calendars: CalendarInfo[] | null
}

/** Settings inspector for the widgets placed on the layout being edited.
 * Selecting a region on the grid (or a chip here) shows that widget's options —
 * rendered generically from the module's schema in /api/meta; widgets absent
 * from the layout stay hidden so the panel only ever shows what the display
 * will actually render. */
export default function ConfigInspector({
  layout,
  selected,
  onSelect,
  modules,
  config,
  onChange,
  calendars,
}: Props) {
  const byId = new Map(modules.map((m) => [m.id, m]))
  const configurable = (id: string | null): ModuleInfo | undefined => {
    const m = id ? byId.get(id) : undefined
    return m && m.options.length > 0 ? m : undefined
  }

  // configurable modules placed on this layout, in region order, deduped —
  // remember the first region hosting each so a chip can select it
  const firstRegion = new Map<string, number>()
  layout.regions.forEach((r, i) => {
    if (r.module && configurable(r.module) && !firstRegion.has(r.module)) {
      firstRegion.set(r.module, i)
    }
  })
  const placed = [...firstRegion.keys()]

  const selectedModule =
    selected !== null ? (layout.regions[selected]?.module ?? null) : null
  const active = configurable(selectedModule)

  return (
    <fieldset className="config-inspector">
      <legend>Widget settings</legend>
      {placed.length === 0 ? (
        <p className="muted">Place a widget on the grid above to configure it.</p>
      ) : (
        <>
          <div className="inspector-tabs">
            {placed.map((id) => (
              <button
                key={id}
                type="button"
                className={`inspector-tab${id === active?.id ? ' active' : ''}`}
                onClick={() => onSelect(firstRegion.get(id)!)}
              >
                {byId.get(id)?.name ?? id}
              </button>
            ))}
          </div>
          {active ? (
            <div className="inspector-body">
              <OptionsForm
                items={active.options}
                values={(config[active.config_key] as ModuleSection) ?? {}}
                onPatch={(patch) =>
                  onChange({
                    ...config,
                    [active.config_key]: {
                      ...(config[active.config_key] as ModuleSection),
                      ...patch,
                    },
                  })
                }
                refreshSeconds={config.refresh_seconds}
                calendars={calendars}
              />
            </div>
          ) : (
            <p className="muted">Select a widget above to edit its settings.</p>
          )}
        </>
      )}
    </fieldset>
  )
}
