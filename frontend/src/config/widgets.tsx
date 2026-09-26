import LocationSearch from '../LocationSearch'
import type { CalendarInfo, ModuleSection } from '../types'

/** Custom widgets for controls too bespoke for the declarative schema. A module
 * references one by name (`kind: "custom"` in its options); the widget reads and
 * patches the module's whole config section. Everything else should be a plain
 * schema field — only add a widget here when a generic control genuinely can't
 * express it. New widgets register in the WIDGETS map in OptionsForm.tsx. */
export interface WidgetProps {
  values: ModuleSection
  onPatch: (patch: ModuleSection) => void
  calendars: CalendarInfo[] | null
}

export function LocationWidget({ values, onPatch }: WidgetProps) {
  return (
    <LocationSearch
      value={(values.location_name as string) ?? ''}
      onNameChange={(name) => onPatch({ location_name: name })}
      onSelect={(name, latitude, longitude) =>
        onPatch({ location_name: name, latitude, longitude })
      }
    />
  )
}

export function CalendarPicker({ values, onPatch, calendars }: WidgetProps) {
  const explicit = (values.calendar_ids as string[]) ?? []

  // an empty calendar_ids means "follow the Google Calendar selection", so
  // materialise that selection before applying the first override
  const toggleCalendar = (id: string) => {
    if (!calendars) return
    const effective = explicit.length
      ? explicit
      : calendars.filter((c) => c.selected).map((c) => c.id)
    const next = effective.includes(id)
      ? effective.filter((x) => x !== id)
      : [...effective, id]
    onPatch({ calendar_ids: next })
  }

  if (calendars === null) return <p className="muted">Loading calendars…</p>
  if (calendars.length === 0) {
    return (
      <p className="muted">
        No calendars available — run the Google auth setup on the backend first.
      </p>
    )
  }
  return (
    <div className="calendar-list">
      {calendars.map((c) => {
        const checked = explicit.length > 0 ? explicit.includes(c.id) : c.selected
        return (
          <label key={c.id} className="checkbox">
            <input type="checkbox" checked={checked} onChange={() => toggleCalendar(c.id)} />
            {c.name}
          </label>
        )
      })}
      {explicit.length > 0 ? (
        <button type="button" onClick={() => onPatch({ calendar_ids: [] })}>
          Reset to Google selection
        </button>
      ) : (
        <p className="muted">Following your Google Calendar sidebar selection.</p>
      )}
    </div>
  )
}
