import { useCallback, useEffect, useState } from 'react'
import TrendChart from './TrendChart'
import LayoutSection from './layout/LayoutSection'
import PlaylistSection from './playlist/PlaylistSection'
import PreviewPane from './layout/PreviewPane'
import {
  getConfig,
  getMeta,
  getSensorHistory,
  getStatus,
  postGoogleComplete,
  postRefresh,
  putConfig,
} from './api'
import type { AppConfig, CalendarInfo, Meta, SensorReading, Status } from './types'

export default function App() {
  const [config, setConfig] = useState<AppConfig | null>(null) // draft being edited
  const [saved, setSaved] = useState<AppConfig | null>(null) // last saved state
  const [meta, setMeta] = useState<Meta | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [readings, setReadings] = useState<SensorReading[]>([])
  const [calendars, setCalendars] = useState<CalendarInfo[] | null>(null)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  // A sign-in that landed here but belongs to another instance: we show the
  // address so it can be pasted where it was started.
  const [handoff, setHandoff] = useState<string | null>(null)
  const [tab, setTab] = useState<'layout' | 'playlist' | 'general'>('layout')
  const [leftTab, setLeftTab] = useState<'preview' | 'sensor'>('preview')
  // The preview follows whichever screen the open tab is about: the layout being
  // edited, or the playlist screen that was clicked. null = the active layout.
  const [editingId, setEditingId] = useState<string | null>(null)
  const [playlistPick, setPlaylistPick] = useState<string | null>(null)

  const loadAll = useCallback(async () => {
    const [configData, metaData, statusData, history] = await Promise.all([
      getConfig(),
      getMeta(),
      getStatus(),
      getSensorHistory(24),
    ])
    setConfig(configData)
    setSaved(configData)
    setMeta(metaData)
    setStatus(statusData)
    setReadings(history.readings)
  }, [])

  useEffect(() => {
    loadAll().catch(() => setMessage('Could not reach the backend — is it running?'))
    fetch('/api/calendars')
      .then((r) => r.json())
      .then((d) => setCalendars(d.configured ? d.calendars : []))
      .catch(() => setCalendars([]))
  }, [loadAll])

  // Google sends the browser back to http://localhost:8000/ after sign-in, so we
  // may land here holding a code. It belongs to us only if this backend started
  // the flow — a sign-in begun on the Pi lands on a dev server running the same
  // app, which must hand the address back rather than swallow it.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const code = params.get('code')
    if (!code) return
    const href = window.location.href
    window.history.replaceState({}, '', window.location.pathname)
    postGoogleComplete(params.get('state') ?? '', href)
      .then((body) => {
        if (body.connected) {
          setMessage(`Google connected${body.account ? ` as ${body.account}` : ''}.`)
        } else if (body.unknown_state) {
          setHandoff(href)
        } else {
          setMessage(body.error ?? 'Google sign-in did not complete.')
        }
      })
      .catch(() => setMessage('Google sign-in did not complete.'))
  }, [])

  // Keep the panel's own state (last render, playlist position) roughly current.
  useEffect(() => {
    const timer = setInterval(() => {
      getStatus().then(setStatus).catch(() => {})
    }, 30_000)
    return () => clearInterval(timer)
  }, [])

  const save = async () => {
    if (!config) return
    setSaving(true)
    setMessage(null)
    try {
      await putConfig(config)
      setSaved(config)
      setMessage('Saved — display is refreshing.')
      setTimeout(() => getStatus().then(setStatus).catch(() => {}), 1500)
    } catch (err) {
      setMessage(String(err instanceof Error ? err.message : err))
    } finally {
      setSaving(false)
    }
  }

  const refreshNow = async () => {
    await postRefresh()
    setTimeout(() => getStatus().then(setStatus).catch(() => {}), 1500)
  }

  if (!config || !meta) {
    return <main className="page">{message ?? 'Loading…'}</main>
  }

  const dirty = saved !== null && JSON.stringify(config) !== JSON.stringify(saved)

  const findLayout = (id: string) =>
    config.layout.layouts.find((l) => l.id === id) ?? meta.presets.find((p) => p.id === id)

  const playlistRunning =
    config.playlist.enabled &&
    config.playlist.items.some((item) => item.enabled && findLayout(item.layout))
  const firstScreen = config.playlist.items.find((item) => findLayout(item.layout))?.layout
  const editingLayout = editingId ?? config.layout.active
  // The playlist tab previews its first screen until the user picks another.
  const wanted = tab === 'playlist' ? (playlistPick ?? firstScreen ?? editingLayout) : editingLayout
  // A layout deleted out from under the preview (or a discarded draft) must not
  // leave the pane asking the backend for something that no longer exists.
  const previewTarget = findLayout(wanted) ? wanted : null
  // Modules on the layout the preview is showing, so the status list matches the
  // preview rather than the last saved render.
  const previewedLayout = findLayout(previewTarget ?? config.layout.active) ?? meta.presets[0]
  const moduleName = (id: string) => meta.modules.find((m) => m.id === id)?.name ?? id
  const previewModules = previewedLayout
    ? [...new Set(previewedLayout.regions.map((r) => r.module).filter((m): m is string => !!m))]
    : []

  return (
    <main className="page">
      <header>
        <h1>inky-dash</h1>
        <span className="muted">
          {status ? `${status.display} · ${status.resolution.join('×')}` : ''}
        </span>
      </header>

      {handoff && (
        <section className="card handoff">
          <strong>This Google sign-in belongs to another device</strong>
          <p className="muted">
            It finished here because this address answers <code>localhost:8000</code>, but the
            sign-in was started elsewhere — most likely the Inky itself. Copy the address below,
            open the config UI where you pressed Connect, and paste it into the box in its Photos
            panel. It is single-use and expires shortly.
          </p>
          <div className="row">
            <input readOnly value={handoff} onFocus={(e) => e.currentTarget.select()} />
            <button
              type="button"
              className="primary"
              onClick={() => navigator.clipboard?.writeText(handoff)}
            >
              Copy
            </button>
            <button type="button" onClick={() => setHandoff(null)}>
              Dismiss
            </button>
          </div>
        </section>
      )}

      <div className="columns">
        <div className="left-column">
          <section className="card preview-card">
            <div className="config-tabs">
              <button
                type="button"
                className={`config-tab${leftTab === 'preview' ? ' active' : ''}`}
                onClick={() => setLeftTab('preview')}
              >
                Preview
              </button>
              <button
                type="button"
                className={`config-tab${leftTab === 'sensor' ? ' active' : ''}`}
                onClick={() => setLeftTab('sensor')}
              >
                Sensor history
              </button>
            </div>

            <div hidden={leftTab !== 'preview'}>
              <PreviewPane
                config={config}
                layout={previewTarget}
                dirty={dirty}
                status={status}
                onRefreshNow={refreshNow}
                onStatusChange={setStatus}
              />
              {previewedLayout && (
                <p className="muted preview-caption">
                  Showing “{previewedLayout.name}”
                  {status?.playlist && status.playlist.layout !== previewedLayout.id
                    ? ` — the display is on “${status.playlist.name}”`
                    : ''}
                </p>
              )}
              {previewModules.length > 0 && (
                <ul className="module-status">
                  {previewModules.map((id) => {
                    const s = status?.modules[id]
                    const state = s ? (s.ok ? 'ok' : 'err') : 'pending'
                    return (
                      <li key={id}>
                        <span className={`dot ${state}`} />
                        {moduleName(id)}
                        {s && !s.ok && s.error ? ` — ${s.error}` : ''}
                        {!s && <span className="muted"> — not yet rendered</span>}
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>

            <div hidden={leftTab !== 'sensor'}>
              {readings.length >= 2 ? (
                <>
                  <TrendChart
                    title="Temperature (°C)"
                    unit="°C"
                    color="#c33"
                    points={readings.map((r) => ({ ts: Date.parse(r.ts), value: r.temp_c }))}
                  />
                  <TrendChart
                    title="Humidity (%)"
                    unit="%"
                    color="#2563b0"
                    points={readings.map((r) => ({ ts: Date.parse(r.ts), value: r.humidity }))}
                  />
                </>
              ) : (
                <p className="muted">
                  No sensor readings logged yet — the backend listens for the Govee H5075
                  automatically while it runs.
                </p>
              )}
            </div>
          </section>
        </div>

        <section className="card">
          <div className="config-tabs">
            <button
              type="button"
              className={`config-tab${tab === 'layout' ? ' active' : ''}`}
              onClick={() => setTab('layout')}
            >
              Layout &amp; widgets
            </button>
            <button
              type="button"
              className={`config-tab${tab === 'playlist' ? ' active' : ''}`}
              onClick={() => setTab('playlist')}
            >
              Playlist
              {playlistRunning && <span className="dot ok" />}
            </button>
            <button
              type="button"
              className={`config-tab${tab === 'general' ? ' active' : ''}`}
              onClick={() => setTab('general')}
            >
              General
            </button>
          </div>

          {tab === 'layout' && (
            <LayoutSection
              config={config}
              meta={meta}
              onConfigChange={setConfig}
              calendars={calendars}
              editingId={editingLayout}
              onEditLayout={setEditingId}
            />
          )}
          {tab === 'playlist' && (
            <PlaylistSection
              config={config}
              meta={meta}
              onConfigChange={setConfig}
              previewLayout={previewTarget}
              onPreviewLayout={setPlaylistPick}
            />
          )}
          {tab === 'general' && (
            <fieldset>
              <legend>General</legend>
              <label>
                Refresh every (seconds)
                <input
                  type="number"
                  min={60}
                  value={config.refresh_seconds}
                  onChange={(e) =>
                    setConfig({ ...config, refresh_seconds: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Colour saturation
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={config.display.saturation}
                  onChange={(e) =>
                    setConfig({
                      ...config,
                      display: { ...config.display, saturation: Number(e.target.value) },
                    })
                  }
                />
              </label>
            </fieldset>
          )}
          {tab === 'general' && (
            <fieldset>
              <legend>Data fetch intervals</legend>
              <div className="row">
                {meta.modules
                  .filter((m) => m.fetch_key)
                  .map((m) => (
                    <label key={m.fetch_key}>
                      {m.name} (minutes)
                      <input
                        type="number"
                        min={5}
                        max={240}
                        value={config.fetch[m.fetch_key!]}
                        onChange={(e) =>
                          setConfig({
                            ...config,
                            fetch: { ...config.fetch, [m.fetch_key!]: Number(e.target.value) },
                          })
                        }
                      />
                    </label>
                  ))}
              </div>
              <p className="muted">
                How often each source is re-fetched. Fetches only happen when the display cycle
                runs (every {Math.round(config.refresh_seconds / 60)} minutes), so the effective
                interval rounds up to the next cycle. The display itself only flashes when
                fetched data actually changes what's drawn.
              </p>
            </fieldset>
          )}

          <div className="row save-row">
            <button className="primary" onClick={save} disabled={saving || !dirty}>
              {saving ? 'Saving…' : 'Save & refresh display'}
            </button>
            {dirty && !saving && (
              <button type="button" onClick={() => setConfig(saved)}>
                Discard changes
              </button>
            )}
            {dirty && !message && <span className="muted">Unsaved changes</span>}
            {message && <span className="muted">{message}</span>}
          </div>
        </section>
      </div>
    </main>
  )
}
