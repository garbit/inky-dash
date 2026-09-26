import { useEffect, useRef, useState } from 'react'

// Open-Meteo's free geocoding API — same provider the backend uses for forecasts.
const GEOCODE_URL = 'https://geocoding-api.open-meteo.com/v1/search'

interface GeoResult {
  id: number
  name: string
  latitude: number
  longitude: number
  country?: string
  admin1?: string
}

interface Props {
  value: string
  onNameChange: (name: string) => void
  onSelect: (name: string, latitude: number, longitude: number) => void
}

function describe(r: GeoResult): string {
  return [r.admin1, r.country].filter(Boolean).join(', ')
}

export default function LocationSearch({ value, onNameChange, onSelect }: Props) {
  const [results, setResults] = useState<GeoResult[]>([])
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const [searching, setSearching] = useState(false)
  const [failed, setFailed] = useState(false)
  // Only search after the user types — not when the saved name is loaded into the input.
  const dirty = useRef(false)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!dirty.current) return
    const query = value.trim()
    if (query.length < 2) {
      setResults([])
      setOpen(false)
      return
    }
    const timer = setTimeout(async () => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
      setSearching(true)
      setFailed(false)
      try {
        const params = new URLSearchParams({ name: query, count: '8', language: 'en' })
        const res = await fetch(`${GEOCODE_URL}?${params}`, { signal: controller.signal })
        if (!res.ok) throw new Error(`geocoding failed (${res.status})`)
        const data = await res.json()
        setResults(data.results ?? [])
        setHighlight(0)
        setOpen(true)
      } catch (err) {
        if (!(err instanceof DOMException && err.name === 'AbortError')) {
          setResults([])
          setFailed(true)
          setOpen(true)
        }
      } finally {
        setSearching(false)
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [value])

  const choose = (r: GeoResult) => {
    dirty.current = false
    setOpen(false)
    setResults([])
    onSelect(r.name, r.latitude, r.longitude)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open || results.length === 0) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlight((h) => Math.min(h + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      choose(results[highlight])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div className="location-search">
      <input
        value={value}
        placeholder="Search for a town or city…"
        // every hint we can give browsers and password managers not to overlay
        // their own autofill dropdown on top of ours
        name="geo-query"
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="none"
        spellCheck={false}
        role="combobox"
        aria-expanded={open}
        aria-controls="location-results"
        aria-autocomplete="list"
        data-1p-ignore="true"
        data-lpignore="true"
        data-form-type="other"
        onChange={(e) => {
          dirty.current = true
          onNameChange(e.target.value)
        }}
        onKeyDown={onKeyDown}
        onFocus={() => results.length > 0 && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {searching && <span className="location-spinner muted">searching…</span>}
      {open && (
        <ul className="location-results" id="location-results" role="listbox">
          {failed && <li className="muted">Search failed — check the internet connection.</li>}
          {!failed && results.length === 0 && <li className="muted">No matches.</li>}
          {results.map((r, i) => (
            <li
              key={r.id}
              role="option"
              aria-selected={i === highlight}
              className={i === highlight ? 'active' : ''}
              // mousedown fires before the input's blur closes the list
              onMouseDown={(e) => {
                e.preventDefault()
                choose(r)
              }}
              onMouseEnter={() => setHighlight(i)}
            >
              <strong>{r.name}</strong>
              {describe(r) && <span className="muted"> — {describe(r)}</span>}
              <span className="location-coords muted">
                {r.latitude.toFixed(2)}, {r.longitude.toFixed(2)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
