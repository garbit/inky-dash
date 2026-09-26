import type {
  AppConfig,
  CalendarInfo,
  GoogleStatus,
  Meta,
  PhotoEntry,
  PhotoJobs,
  PhotoPool,
  SensorReading,
  Status,
} from './types'

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url} failed (${res.status})`)
  return res.json()
}

export const getConfig = () => getJson<AppConfig>('/api/config')
export const getStatus = () => getJson<Status>('/api/status')
export const getMeta = () => getJson<Meta>('/api/meta')
export const getSensorHistory = (hours: number) =>
  getJson<{ readings: SensorReading[] }>(`/api/sensor/history?hours=${hours}`)
export const getCalendars = () =>
  getJson<{ configured: boolean; calendars: CalendarInfo[] }>('/api/calendars')

function errorMessage(detail: unknown, fallback: string): string {
  // FastAPI 422s carry either our validate_config string or Pydantic's error list
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg)
  return fallback
}

export async function putConfig(config: AppConfig): Promise<void> {
  const res = await fetch('/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(errorMessage(body?.detail, `save failed (${res.status})`))
  }
}

export const postRefresh = () => fetch('/api/refresh', { method: 'POST' })
export const postPlaylistNext = () => fetch('/api/playlist/next', { method: 'POST' })

async function postJson<T>(url: string, body?: unknown, method = 'POST'): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(errorMessage(detail?.detail, `${url} failed (${res.status})`))
  }
  return res.json()
}

// --- Google account ---------------------------------------------------------
// These endpoints report failures as data ({error}) rather than HTTP errors, so
// the UI can show a hint instead of a stack trace.

export const getGoogleStatus = () => getJson<GoogleStatus>('/api/google/status')
export const postGoogleConnect = (service: string) =>
  postJson<{ auth_url?: string; state?: string; error?: string }>('/api/google/connect', {
    service,
  })
export const postGoogleComplete = (state: string, response: string) =>
  postJson<{
    connected: boolean
    account?: string | null
    error?: string
    /** The code belongs to a sign-in another instance started. */
    unknown_state?: boolean
  }>(
    '/api/google/complete',
    { state, response },
  )
export const deleteGoogle = (service: string) =>
  fetch(`/api/google/${service}`, { method: 'DELETE' })

// --- Photo pool -------------------------------------------------------------

export const getPhotos = () => getJson<PhotoPool>('/api/photos')
export const deletePhoto = (id: string) => fetch(`/api/photos/${id}`, { method: 'DELETE' })
export const clearPhotos = () => fetch('/api/photos', { method: 'DELETE' })

/** Anchor one photo's crop, 0..1 in each axis; null restores the centre. */
export const putPhotoFocus = (id: string, focus: [number, number] | null) =>
  postJson<PhotoEntry>(`/api/photos/${id}/focus`, { focus }, 'PUT')

/** The photo as the panel will draw it, at the size of its region on the
 * display. `focus` previews a position that has not been saved yet. */
export function photoPreviewUrl(id: string, focus: [number, number], simulate: boolean): string {
  const query = new URLSearchParams({ fx: focus[0].toFixed(4), fy: focus[1].toFixed(4) })
  if (simulate) query.set('simulate', '1')
  return `/api/photos/preview/${id}.png?${query}`
}

export async function uploadPhotos(files: File[]) {
  const form = new FormData()
  for (const file of files) form.append('files', file)
  const res = await fetch('/api/photos/upload', { method: 'POST', body: form })
  if (!res.ok) throw new Error(`upload failed (${res.status})`)
  return res.json() as Promise<{
    added: number
    duplicates: number
    pruned: number
    errors: string[]
  }>
}

// --- Background photo jobs --------------------------------------------------
// Picking and Drive syncing run server-side, so these only start and observe
// them: closing the tab (or picking on a phone) doesn't interrupt anything.

export const getPhotoJobs = () => getJson<PhotoJobs>('/api/photos/jobs')
export const postPickerSession = () =>
  postJson<{ picker_uri?: string; state?: string; error?: string }>('/api/photos/picker/session')
export const postPickerCancel = () => postJson<{ ok: boolean }>('/api/photos/picker/cancel')
export const postDriveSync = () =>
  postJson<{ added?: number; skipped?: number; found?: number; error?: string }>(
    '/api/photos/drive/sync',
  )

/** Render the draft config server-side. `layout` picks which layout to draw —
 * the one being edited, or a playlist screen — instead of the active one. */
export async function postPreview(
  config: AppConfig,
  layout?: string | null,
  signal?: AbortSignal,
  simulate = false,
): Promise<Blob> {
  const params = new URLSearchParams()
  if (layout) params.set('layout', layout)
  if (simulate) params.set('simulate', '1')
  const query = params.toString()
  const url = query ? `/api/preview.png?${query}` : '/api/preview.png'
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config),
    signal,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(errorMessage(body?.detail, `preview failed (${res.status})`))
  }
  return res.blob()
}
