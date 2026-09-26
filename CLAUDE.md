# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An e-ink dashboard driven by a Raspberry Pi Zero 2 W rendering to a **Pimoroni Inky
Impression Spectra 7.3"** display (800x480, 6-colour: black/white/red/yellow/green/blue),
plus a web app served from the Pi for configuring what the display shows.

Dashboard panels: weather (current + optional hourly strip + daily forecast, Open-Meteo),
temperature/humidity from a
**Govee H5075** sensor (read passively from BLE advertisements, logged to SQLite for
on-screen trend graphs), headlines from The Economist (RSS), Google Calendar events
(today + upcoming week, multiple calendars merged), a photo frame (local pool filled
from Google Photos or by upload), the moon (phase, cycle position, rise/set — computed
on-device, see `astro.py`), and a clock.

Development happens on macOS with **no Inky hardware** — the display layer has a
file-based simulator that writes PNGs instead. See `PLAN.md` for the build roadmap and
current status.

## Commands

### Backend (Python 3.11, uv, FastAPI) — run from `backend/`

```bash
uv sync                          # install deps (dev machine; no inky)
uv sync --extra pi               # on the Raspberry Pi (adds the inky driver)
INKY_DASH_DISPLAY=file uv run uvicorn inky_dash.main:app --reload   # dev server :8000, renders to output/display.png
uv run pytest                    # all tests
uv run pytest tests/test_api.py::test_get_config   # single test
uv run ruff check . && uv run ruff format .        # lint + format
```

### Frontend (Vite + React + TypeScript) — run from `frontend/`

```bash
npm install
npm run dev        # dev server :5173, proxies /api to :8000
npm run build      # tsc + vite build -> frontend/dist (backend serves this at /)
npm run lint
```

Never run `npm install`/`npm run build` on the Pi Zero (512MB RAM) — build on the dev
machine and copy `frontend/dist/` across with the repo.

### Deployment

The Pi is reachable at `andy@inky` (passwordless SSH + sudo) and runs the app as the
`inky-dash` systemd unit (`/etc/systemd/system/inky-dash.service`), serving
http://inky on **port 80** — uvicorn binds it directly, with the unit granting
`AmbientCapabilities=CAP_NET_BIND_SERVICE` so the service user stays unprivileged
(no reverse proxy; the Pi Zero has no RAM to spare). The Pi runs `ufw`, which
allows port 80 from the LAN subnet only — the dashboard has no auth. Local dev
still uses :8000. To ship an update run `./deploy.sh` from the repo root (tests +
frontend build + rsync + `uv sync --extra pi` + restart; `--skip-build` /
`--skip-tests` to shorten; target read from gitignored `.deploy.env`, `PI_HOST` env
overrides). First-time provisioning of a fresh Pi is `./setup.sh` — interactive and
re-runnable (SSH keys, hostname, SPI/I2C/BLE, service install, ufw rule, panel choice, Google
OAuth). Logs: `journalctl -u inky-dash`.

## Architecture

Monorepo: `backend/` (Python package, src layout: `backend/src/inky_dash/`) and
`frontend/` (React config UI). One FastAPI process on the Pi does everything: drives the
display on a render loop, listens for BLE advertisements, serves the JSON config API,
and serves the built frontend.

### Data flow

```
BLE adverts ──> ble listener task ──> SQLite (data/inky-dash.db) ─┐
HTTP APIs (weather/RSS/calendar) ──> module fetch (cached) ───────┤
                                                                  v
config.json <──> Pydantic AppConfig <── /api/config (web UI) ──> render loop
                                                                  v
                                              Pillow image (800x480 RGB)
                                                                  v
                                     Display protocol: InkyDisplay | FileDisplay
```

### Key concepts

- **Display abstraction** (`display/`): a `Display` Protocol with `resolution` and
  `show(image)`. Two implementations: `InkyDisplay` (real hardware) and `FileDisplay`
  (writes `output/display.png`). `create_display()` picks by config/env:
  `auto` tries hardware then falls back to file.
  **The `inky` package must only ever be imported lazily inside `InkyDisplay`** — it
  does not install on macOS (GPIO deps), which is why it lives behind the `pi` optional
  extra. Nothing importable by tests may import it at module level.
- **Modules** (`modules/`): each dashboard panel is one module pairing a
  `fetch() -> dict` (data acquisition, with per-module TTL cache) and a
  `render(image, rect, data, config)` (Pillow drawing into its layout slot). A registry
  in `modules/__init__.py` maps module ids (`weather`, `govee`, `economist`,
  `calendar`, `clock`, `xkcd`, `vox`, `moon`, `photos`) to implementations; adding a panel = adding one module file and
  registering it — including its settings UI: each module declares a declarative
  `options` schema (built with the `modules/options.py` helpers, published via
  `/api/meta`) that the frontend renders generically (`frontend/src/config/
  OptionsForm.tsx`). There is no per-module frontend code; numeric min/max come from
  the Pydantic constraints of the module's config section automatically, and only
  genuinely bespoke controls (location search, calendar picker, photo manager) are named custom
  widgets registered in `OptionsForm.tsx`. `tests/test_options.py` keeps schemas and
  config models in sync. New custom widgets should **fetch their own data** (as
  `PhotosPanel` and `LocationSearch` do) rather than adding another prop to the
  `App → LayoutSection → ConfigInspector → OptionsForm → widget` chain that
  `calendars` uses.
- **Astronomy** (`astro.py`): the moon panel's data — position, phase,
  illumination, the principal-phase dates, moonrise/moonset — computed from
  truncated Meeus series rather than fetched, so the module has `fetch_key = None`
  and works offline (like `clock`, its state comes from the clock at render time).
  `tests/test_astro.py` pins the series to Meeus's own worked examples; keep those
  tolerances if you touch the tables. The observer position is
  `config.weather.latitude/longitude` — deliberately not a second location to keep
  in sync. Panel values are rendered at whole-unit precision (integer percent,
  whole days) so the frame changes about once a day rather than every refresh.
- **Layout** (`layout.py` + `config.py`): a `Layout` is a named set of rectangular
  `Region`s on a coarse cell grid (default 50x30 = 16px cells at 800x480), each region
  optionally hosting one module (a module may appear in at most one region; overlaps
  are rejected by the model validator). Six immutable code-defined presets (`PRESETS`,
  the former templates) plus user-created custom layouts stored in
  `config.layout.layouts`; `config.layout.active` names the one the display renders
  when no playlist is running, and `resolve_layout(layout_config, id=None)` falls back
  to `dashboard_clock` if an id dangles. Old
  `{template, panels}` configs migrate transparently at load
  (`config.py::_migrate`). The web UI edits layouts with a drag-and-drop grid editor
  (`frontend/src/layout/`).
- **Playlist** (`config.playlist` + `loop.py`): an ordered rotation of screens, each a
  `{layout, minutes, enabled}` item, that supersedes `layout.active` while
  `playlist.enabled` and at least one item is enabled with a layout that still exists
  (`Dashboard.playlist_screens()`); anything less falls back to the single active
  layout rather than blanking the panel. `Dashboard.current_layout_id()` is the single
  answer to "what is on the display", used by the render loop, `active_module_ids()`
  (only the current screen's modules are fetched), `/api/status` and `GET
  /api/preview.png`. `_advance_if_due()` rotates at the top of each tick and
  `_wait_seconds()` shortens the loop's sleep so a tick lands when a screen's dwell
  expires — the loop still ticks at `refresh_seconds` for fresh data on longer dwells.
  `POST /api/playlist/next` skips ahead (forced, so the panel always updates). The UI
  is `frontend/src/playlist/PlaylistSection.tsx`; deleting a layout must also drop the
  playlist items pointing at it, or `validate_config` rejects the save.
- **Render loop** (`loop.py`): asyncio task started in the FastAPI lifespan. Every
  `refresh_seconds` (default 600 — a full Spectra refresh takes ~30s, don't refresh
  aggressively) it fetches module data, composes the frame, and calls `display.show()`
  in an executor — unless the frame is pixel-identical to the one already on the
  panel, in which case the show is skipped (the EL673 driver has no partial refresh;
  skipping unchanged frames is the only way to cut flashing). Saving config via the
  API wakes the loop immediately; `POST /api/refresh` forces a show even when
  unchanged (doubles as the ghosting-clear control).
- **BLE listener**: a separate long-running asyncio task using `bleak`. It never
  connects to the sensor — the H5075 encodes temp/humidity/battery in manufacturer
  data (id `0xEC88`) of its advertisements. Parsed readings are throttled and inserted
  into SQLite; the govee panel reads the latest value plus a configurable window
  (`govee.trend_hours`) for the trend sparkline.
- **Config**: single `config.json` (gitignored) in the working directory (`backend/`)
  by default, overridden by `INKY_DASH_CONFIG`. Loaded/saved through the Pydantic `AppConfig` model
  (`config.py`); writes are atomic (tmp file + `os.replace`). The web UI edits it via
  `GET/PUT /api/config`.
- **API** (`api.py`): `/api/config`, `/api/status` (per-module fetch health, last
  render, current layout + playlist position), `/api/meta` (module registry, layout
  presets, grid limits — the frontend's
  single source of truth, never duplicate these client-side), `GET /api/preview.png`
  (renders the saved config from cached data in-memory — works identically on Mac and
  Pi, no hardware touched), `POST /api/preview.png?layout=<id>` (same but for a
  candidate unsaved config in the request body, with `layout` choosing which layout to
  draw so the pane can follow the layout or playlist screen being edited; fetches
  missing module data on demand through the TTL cache), `/api/refresh` (force
  re-render), `/api/playlist/next` (skip a screen), `/api/sensor/history` (trend data
  for the UI). Layout- and playlist-only config saves keep the module data cache
  (`loop.py::update_config`). Both preview endpoints take `?simulate=1`, which dithers
  the frame to the panel's six colours for the UI's "show as e-ink" toggle — preview
  only, the display path stays un-quantised. Photo/Google routes: `/api/google/status`,
  `/api/google/connect`, `/api/google/complete`, `DELETE /api/google/{service}`,
  `/api/photos` (pool index, plus `frame`: the pixel size of the photos region so the
  framing editor crops to the right shape), `/api/photos/upload`,
  `/api/photos/thumb/{id}`, `/api/photos/full/{id}`,
  `PUT /api/photos/{id}/focus`, `/api/photos/preview/{id}.png?fx=&fy=` (one photo drawn
  by the panel's own renderer at the region's size, `fx`/`fy` overriding the stored
  focus so a position can be seen before it is saved),
  `DELETE /api/photos[/{id}]`, `/api/photos/jobs` (background job progress),
  `/api/photos/picker/session` + `/cancel`, and `/api/photos/drive/sync`. These
  report failures as data (`{error: …}`) rather than 5xx, like `/api/calendars` — the
  UI shows a hint, never a stack trace.
- **Serving**: the `/api` router must be mounted **before** the `StaticFiles` mount of
  `frontend/dist` at `/`; the static mount is guarded with `if dist_dir.exists()` so
  the backend runs before the frontend is ever built.

### Runtime state (all gitignored)

- `config.json` — user configuration
- `data/inky-dash.db` — SQLite sensor log
- `data/google_credentials.json` — the shared OAuth client (see below)
- `data/google_token.json` / `data/google_photos_token.json` /
  `data/google_drive_token.json` — one token per service;
  scopes are never merged, so connecting Photos can't invalidate Calendar
- `data/photos/` — the photo pool: `<id>.jpg` plus `index.json`
- `output/display.png` — simulator output

### Env vars

- `INKY_DASH_CONFIG` — config file path (default `./config.json`)
- `INKY_DASH_DISPLAY` — override display kind: `inky` | `file` | `auto`
- `INKY_DASH_OUTPUT` — simulator PNG path (default `./output/display.png`)
- `INKY_DASH_DATA` — data directory for the SQLite DB and Google tokens (default `./data`)

## External services

- **Weather**: Open-Meteo — free, no API key; lat/lon set in config.
- **The Economist**: public per-section RSS feeds (`https://www.economist.com/<section>/rss.xml`)
  — headlines require no login. Do not add credential-based scraping without an explicit
  decision recorded in PLAN.md.
- **Google Calendar**: `google-api-python-client` with an OAuth "installed app" flow.
  One-time setup: the user creates an OAuth client in Google Cloud Console, saves it as
  `data/google_credentials.json`, and runs `uv run python -m inky_dash.google_auth` on
  the dev machine (browser flow), which writes `data/google_token.json`; both files are
  then copied to the Pi.
  Sign-in can also be done from the config UI (see Google Photos below) — the same
  paste-bridge flow works for either service.
  The module reports `configured: false` (and the panel renders a setup hint) until
  the token exists — never crash on missing credentials.
- **Google Photos**: **Picker API only** (`photospicker.mediaitems.readonly`). The broad
  Library scopes (`photoslibrary.readonly` and friends) were withdrawn on 2025-03-31 and
  now 403 — no app can list a user's albums or read their gallery any more. Do not try to
  reinstate library access; the surviving Library scopes reach app-created data only.
  Flow (`google_photos.py`): create a session → user picks in Google's UI (albums aren't
  browsable, they *search* the album name) → poll `mediaItemsSet` → download each
  `baseUrl` **with the bearer token** and a mandatory `=w…-h…` size → store locally.
  Picked URLs die in ~60 min, so the import must run straight after picking; everything
  the panel draws comes from `data/photos/` afterwards. **The wait and the import run
  server-side** (`photos_sync.py::PhotoJobs`, started in the app lifespan) — the client
  only starts a session and polls `/api/photos/jobs` to watch. Never move that loop back
  into the browser: a closed tab used to mean the selection expired unimported.
  Sign-in from the UI is a **paste bridge** (`google_oauth.py`): Google only redirects to
  HTTPS or loopback, and the Pi is plain HTTP on a LAN name, so the redirect goes to
  `http://localhost:8000/` and the user pastes that address back. On the dev machine the
  backend answers that address, so `App.tsx` completes the flow automatically. PKCE means
  the verifier never leaves the device. The device flow is not an option — it supports
  only Drive/YouTube/profile scopes.
  **Never pass `include_granted_scopes`** to `authorization_url()`: incremental
  authorisation returns a token covering every scope the OAuth client was ever granted,
  so connecting Photos on an account that already had Calendar came back as a *Calendar*
  token. Each service requests only its own scopes. `complete()` then verifies the
  granted scopes before saving — read them from `credentials.granted_scopes` (or the
  session's token dict), **never `credentials.scopes`**, which is just the requested
  list copied off the session and would make the check vacuous.
  Operational trap worth surfacing to users: while the OAuth consent screen is in
  **"Testing"** publishing status, Google expires refresh tokens after **7 days**. Set it
  to "In production" (unverified is fine for personal use) or expect to reconnect weekly;
  `/api/google/status` reports `expired: true` so the UI can say so.
- **Google Drive** (`google_drive.py`): the *other* photo source — a folder mirrored into
  the pool every `photos.drive_sync_minutes`, so photos dropped in from a phone appear
  unattended. This is the only source that keeps itself current, and the closest thing
  left to "show me an album".
  It needs **`drive.readonly`**, a restricted scope covering the whole Drive, because
  `drive.file` explicitly grants *no* access to the contents of a folder the user picks.
  There is no narrower option; personal use is exempt from Google's security assessment.
  Keep every call confined to the configured folder and to `mimeType contains 'image/'`,
  and keep the token in its own file so it can be revoked alone.
  `parse_folder_id()` accepts a folder URL or a bare id. The sync is not a mirror:
  photos deleted from Drive stay in the pool, which is the frame's own library.
- **Photo framing**: "Fill" crops each photo to the panel's aspect ratio, which beheads
  a portrait as often as not, so each pool entry may carry a `focus` — `[x, y]` in 0..1
  — that anchors the crop (`ImageOps.fit(centering=…)`). It lives in the photo index,
  not the config: it belongs to the picture, so it survives layout changes and saves
  immediately rather than with the config draft. **Absent means centred** — the default
  for every photo, and what `set_focus(id, None)` restores by dropping the key.
  `modules/photos.py::focus_of()` treats anything missing or malformed as the centre;
  framing must never be able to break a render. The editor
  (`frontend/src/photos/PhotoFramer.tsx`) drags in CSS — `object-fit: cover` with
  `object-position` is exactly the crop Pillow performs — and only asks the server for
  the truthful dithered render behind its "show as e-ink" toggle.
- **Photo dedupe**: `photos_store.add_bytes()` returns `None` rather than storing a
  duplicate, matched on `source_id` (checked *before* download, so a routine Drive sync
  costs one list call) or on a sha256 of the original bytes. Picker media-item ids are
  not guaranteed stable across sessions, which is exactly why the hash exists. Callers
  must handle the `None`.

## Hardware notes

- Spectra 7.3": 800x480. Renderers produce plain RGB images using the 6-colour palette
  (`palette.py`); the inky driver's `set_image(image, saturation=...)` does its own
  dithering/quantisation — do not pre-quantise.
- **No grey, no anti-aliasing.** The panel dithers mid-tones into colour speckle:
  secondary text uses a solid palette colour (`palette.MUTED`), and all drawing goes
  through `modules/common.py: panel_draw()` (sets Pillow `fontmode = "1"` for bilevel
  glyphs). Never call `ImageDraw.Draw()` directly in a renderer.
- Full refresh ~30s with visible flashing; design for infrequent updates. Anything that
  rotates content (photos, xkcd random, govee averages) should derive *what* it shows
  from a clock-aligned bucket, so the composed frame is byte-identical between
  boundaries and `loop.py`'s frame-hash check skips the refresh.
- Photographs are the one exception to "solid palette colours": they are pasted as plain
  RGB for the driver to dither. The web preview therefore lies about them — use the UI's
  "show as e-ink" toggle (`?simulate=1`) to see what will actually appear.
- Fonts: `fonts.py` resolves a usable TrueType font per-platform (DejaVu on Pi, system
  fonts on macOS) with a `load_default(size)` fallback — always go through it, never
  hardcode font paths in renderers.

