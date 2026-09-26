import type { FC } from 'react'
import type {
  CalendarInfo,
  FieldOption,
  ModuleSection,
  OptionItem,
  VisibleIf,
} from '../types'
import PhotosPanel from '../photos/PhotosPanel'
import { CalendarPicker, LocationWidget } from './widgets'
import type { WidgetProps } from './widgets'

// Custom widgets a module's schema can reference by name (kind: "custom").
const WIDGETS: Record<string, FC<WidgetProps>> = {
  location: LocationWidget,
  calendar_picker: CalendarPicker,
  // Fetches its own data rather than taking it as a prop — see PhotosPanel.
  photos_manager: PhotosPanel,
}

/** Renders a module's declarative options schema (from /api/meta) against its
 * config section. The frontend has no per-module form code: adding a module —
 * or an option — is purely a backend change, unless it needs a genuinely new
 * control (then extend Field or register a widget in widgets.tsx). */
interface Props {
  items: OptionItem[]
  values: ModuleSection
  onPatch: (patch: ModuleSection) => void
  refreshSeconds: number
  calendars: CalendarInfo[] | null
}

function isVisible(cond: VisibleIf | undefined, values: ModuleSection): boolean {
  if (!cond) return true
  const value = values[cond.key]
  if (cond.op === 'eq') return value === cond.value
  if (cond.op === 'neq') return value !== cond.value
  return Boolean(value)
}

/** Fill {key} placeholders in note text from the section values, plus
 * {refresh_minutes} for the display refresh cadence. */
function interpolate(text: string, values: ModuleSection, refreshSeconds: number): string {
  const context: ModuleSection = {
    ...values,
    refresh_minutes: Math.round(refreshSeconds / 60),
  }
  return text.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in context ? String(context[key]) : match,
  )
}

function Field({
  field,
  values,
  onPatch,
}: {
  field: FieldOption
  values: ModuleSection
  onPatch: (patch: ModuleSection) => void
}) {
  const value = values[field.key]
  switch (field.control) {
    case 'checkbox':
      return (
        <label className="checkbox">
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => onPatch({ [field.key]: e.target.checked })}
          />
          {field.label}
        </label>
      )
    case 'select':
      return (
        <label>
          {field.label}
          <select
            value={String(value)}
            onChange={(e) => {
              const choice = field.choices?.find((c) => String(c.value) === e.target.value)
              if (choice) onPatch({ [field.key]: choice.value })
            }}
          >
            {field.choices?.map((c) => (
              <option key={String(c.value)} value={String(c.value)}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
      )
    case 'number':
      return (
        <label>
          {field.label}
          <input
            type="number"
            min={field.min}
            max={field.max}
            step={field.step}
            value={value as number}
            onChange={(e) => onPatch({ [field.key]: Number(e.target.value) })}
          />
        </label>
      )
    case 'list':
      return (
        <label>
          {field.label}
          <input
            value={((value as string[]) ?? []).join(', ')}
            onChange={(e) =>
              onPatch({
                [field.key]: e.target.value
                  .split(',')
                  .map((s) => s.trim())
                  .filter(Boolean),
              })
            }
          />
        </label>
      )
    default:
      return (
        <label>
          {field.label}
          <input
            value={(value as string | null) ?? ''}
            placeholder={field.placeholder}
            onChange={(e) =>
              onPatch({ [field.key]: e.target.value || (field.nullable ? null : '') })
            }
          />
        </label>
      )
  }
}

export default function OptionsForm({ items, values, onPatch, refreshSeconds, calendars }: Props) {
  return (
    <>
      {items.map((item, i) => {
        if (item.kind === 'row') {
          const shown = item.items.filter((f) => isVisible(f.visible_if, values))
          if (shown.length === 0) return null
          return (
            <div className="row" key={i}>
              {shown.map((f) => (
                <Field key={f.key} field={f} values={values} onPatch={onPatch} />
              ))}
            </div>
          )
        }
        if (!isVisible(item.visible_if, values)) return null
        if (item.kind === 'note') {
          return (
            <p className="muted" key={i}>
              {interpolate(item.text, values, refreshSeconds)}
            </p>
          )
        }
        if (item.kind === 'custom') {
          const Widget = WIDGETS[item.widget]
          const body = Widget ? (
            <Widget values={values} onPatch={onPatch} calendars={calendars} />
          ) : (
            <p className="muted">Unknown widget “{item.widget}” — update the frontend.</p>
          )
          return item.label ? (
            <label key={i}>
              {item.label}
              {body}
            </label>
          ) : (
            <div key={i}>{body}</div>
          )
        }
        return <Field key={item.key} field={item} values={values} onPatch={onPatch} />
      })}
    </>
  )
}
