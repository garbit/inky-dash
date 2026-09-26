export interface Region {
  x: number
  y: number
  w: number
  h: number
  module: string | null
}

export interface Layout {
  id: string
  name: string
  cols: number
  rows: number
  regions: Region[]
}

export interface LayoutConfig {
  active: string
  layouts: Layout[]
}

/** One screen in the rotation: a layout and how long it stays on the panel. */
export interface PlaylistItem {
  layout: string
  minutes: number
  enabled: boolean
}

export interface PlaylistConfig {
  enabled: boolean
  items: PlaylistItem[]
}

/** A module's config section: schema-driven, so the frontend never knows the
 * shape — controls are rendered from the module's OptionItem list instead. */
export type ModuleSection = Record<string, unknown>

export interface AppConfig {
  refresh_seconds: number
  fetch: Record<string, number>
  display: {
    kind: 'auto' | 'inky' | 'file'
    model: string
    saturation: number
  }
  layout: LayoutConfig
  playlist: PlaylistConfig
  // Per-module config sections, keyed by each module's config_key.
  [section: string]: unknown
}

// --- Declarative module options (served by /api/meta, rendered by OptionsForm) ---

export interface VisibleIf {
  key: string
  op: 'truthy' | 'eq' | 'neq'
  value?: unknown
}

export interface OptionChoice {
  value: string | number
  label: string
}

export interface FieldOption {
  kind: 'field'
  key: string
  label: string
  control: 'number' | 'text' | 'checkbox' | 'select' | 'list'
  min?: number
  max?: number
  step?: number
  placeholder?: string
  nullable?: boolean
  choices?: OptionChoice[]
  visible_if?: VisibleIf
}

export interface RowOption {
  kind: 'row'
  items: FieldOption[]
}

export interface NoteOption {
  kind: 'note'
  text: string
  visible_if?: VisibleIf
}

export interface CustomOption {
  kind: 'custom'
  widget: string
  label?: string
  visible_if?: VisibleIf
}

export type OptionItem = FieldOption | RowOption | NoteOption | CustomOption

export interface ModuleInfo {
  id: string
  name: string
  config_key: string
  fetch_key: string | null
  options: OptionItem[]
}

export interface Meta {
  resolution: [number, number]
  modules: ModuleInfo[]
  presets: Layout[]
  limits: {
    min_region: number
    max_regions: number
    default_cols: number
    default_rows: number
    max_playlist_items: number
  }
}

export interface CalendarInfo {
  id: string
  name: string
  selected: boolean
  primary: boolean
}

export interface ModuleStatus {
  ok: boolean
  error?: string
  fetched_at?: string
  failed_at?: string
}

/** Where the rotation has got to; null when the display is on a single layout. */
export interface PlaylistStatus {
  position: number
  count: number
  layout: string
  name: string
  minutes: number
  seconds_remaining: number
}

export interface Status {
  last_render: string | null
  display: string
  resolution: [number, number]
  modules: Record<string, ModuleStatus>
  layout: string
  playlist: PlaylistStatus | null
}

/** Per-service Google connection state, for the connect cards. */
export interface GoogleServiceStatus {
  connected: boolean
  account?: string | null
  /** The token exists but no longer works — almost always a refresh token that
   * lapsed while the OAuth consent screen sat in "Testing" status. */
  expired?: boolean
  error?: string
}

export interface GoogleStatus {
  /** Whether data/google_credentials.json (the OAuth client) is in place at all. */
  credentials: boolean
  /** The Cloud project that client belongs to — the one whose enabled APIs apply. */
  project: string | null
  services: Record<string, GoogleServiceStatus>
}

export interface PhotoEntry {
  id: string
  source: 'google' | 'upload'
  filename: string
  taken: string | null
  added: string
  width: number
  height: number
  /** Where the crop is anchored, 0..1 in each axis. Absent means centred. */
  focus?: [number, number] | null
}

export interface PhotoPool {
  photos: PhotoEntry[]
  bytes_used: number
  max_photos: number
  /** Pixel size of the photos panel on the display — the shape a photo is
   * cropped to, and so the shape the framing editor previews. */
  frame: [number, number]
}

/** Progress of the server-side picking session. Because the wait lives on the
 * backend, reloading the config UI rejoins whatever is already running. */
export interface PickerJob {
  state: 'idle' | 'waiting' | 'importing' | 'done' | 'error' | 'timeout' | 'cancelled'
  picker_uri?: string
  imported?: number
  skipped?: number
  duplicates?: number
  total?: number
  error?: string
  at?: string
}

export interface DriveJob {
  state: 'idle' | 'syncing' | 'done' | 'error'
  added?: number
  skipped?: number
  found?: number
  pruned?: number
  error?: string
  at?: string
}

export interface PhotoJobs {
  picker: PickerJob
  drive: DriveJob
}

export interface SensorReading {
  ts: string
  temp_c: number
  humidity: number
  battery: number | null
}
