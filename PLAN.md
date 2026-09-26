# inky-dash build plan

Target: Raspberry Pi Zero 2 W + Pimoroni Inky Impression Spectra 7.3" (800x480,
6-colour). Python/FastAPI backend renders the display and serves the API; React web
app configures it. Architecture lives in [CLAUDE.md](CLAUDE.md); setup, deployment,
and operations in [README.md](README.md).

## Status

Phases 1–4 are **done and deployed**: core render pipeline with hardware/simulator
display abstraction, all four data modules (weather, Govee BLE → SQLite, Economist
RSS, Google Calendar with OAuth), the web config UI, and the Pi running everything
under systemd at http://inky (port 80). Deploy updates with `./deploy.sh`.

Added since (2026-07): **xkcd module** — latest or random comic, scaled to its
region and reduced to pure black/white (threshold for crisp line art, or
Floyd–Steinberg dither to keep shading), optional title/alt text. **Playlist**
(2026-07-27) — an ordered rotation of screens (layout + dwell minutes) that the
display cycles through, TRMNL-style, with a Playlist tab in the web UI and a
"next screen" control. **Moon module** (2026-07-27) — today's moon drawn with a
true terminator, phase name, percent lit, position in the cycle, moonrise/moonset
and the dates of the coming principal phases, all computed on-device from
`astro.py` (no service, no fetch interval, works offline). **Photos** (2026-07-27) — a photo-frame panel drawing from
a local pool (`data/photos/`) filled from Google Photos via the Picker API or by
uploading images in the config UI, with shuffle / in-order / on-this-day rotation,
two new presets (`photo_full`, `photo_clock`), in-browser Google sign-in, and an
opt-in "show as e-ink" preview that dithers to the panel's six colours.
**Vox** (2026-07-27) — a word-of-the-day panel (after TRMNL's Vox): a curated word
with its senses and example from Wiktionary and its synonyms from Datamuse
(definitions moved off dictionaryapi.dev on 2026-09-08, see the decisions table).

## Backlog (Phase 5 — decide before building)

- Trend rendering options (24h vs 7d), min/max annotations
- Time-of-day conditions on playlist screens (a breakfast screen 07:00–09:00) and
  night mode (no refreshes overnight) — the rotation is the natural place to hang
  both, as a per-item active window plus a "blank/skip" rule
- Economist authenticated access **if** headlines-only proves insufficient — needs an
  explicit decision (credential storage + scraping brittleness + ToS)
- Sensor DB retention/pruning policy

## Decisions log

| Decision | Outcome |
|---|---|
| Economist access | Public per-section RSS, headlines only — no credentials stored, no scraping. Revisit only via the backlog item above. |
| Google Calendar auth | OAuth Desktop-app client; browser flow run once on the dev machine (`python -m inky_dash.google_auth`), token copied to the Pi. Calendar API must be enabled on the Cloud project. |
| Sensor storage | SQLite (`data/inky-dash.db`), readings throttled to one per `log_interval_seconds` (default 120s). |
| Default layout | `dashboard_clock` preset: clock header, weather main, Govee + calendar sidebar, Economist bottom strip — all reconfigurable in the UI. |
| Layout model (2026-07) | Grid-based: regions on a 50×30 cell grid (16px cells — divides 800×480 exactly), snap-to-grid editor with drag-and-drop module assignment. Six former templates are immutable code presets; custom layouts live in `config.layout.layouts` with one `active` id (single switching point for future time-of-day scheduling). One module per layout max (settings are per-module, not per-region). Old `{template, panels}` configs migrate at load. |
| Candidate preview (2026-07) | `POST /api/preview.png` renders unsaved config server-side, fetching missing module data on demand through the shared TTL cache — preview is truthful without extra API load. Save remains the only path to the physical display. |
| Playlist (2026-07) | Rotation of whole layouts, not per-panel: one `{layout, minutes, enabled}` list in `config.playlist`, superseding `layout.active` only while it has something to cycle (never blanks the panel). Dwell is in minutes — a ~30s flashing refresh per switch rules out anything shorter. The render loop shortens its own sleep to hit each switch, so `refresh_seconds` stays the *data* cadence. Rotation state is runtime-only (restarts at screen 1 on save or restart) — nothing worth persisting across a reboot. |
| E-ink text rendering | No grey, no anti-aliasing: solid palette colours only, bilevel glyphs via `panel_draw()` (mid-tones dither into speckle on the six-colour panel). |
| Google Photos access (2026-07-27) | **Picker API only.** Google withdrew `photoslibrary.readonly`/`.sharing`/`photoslibrary` on 2025-03-31 (403 PERMISSION_DENIED since); the surviving Library scopes reach app-created data only, so listing a user's albums or shuffling their whole gallery is no longer possible for any app. We use `photospicker.mediaitems.readonly`: a picking session, then an immediate download into a local pool, because picked `baseUrl`s expire in ~60 min and the session with them. "Pick an album" is therefore "search the album name in the picker and select its contents"; "random from the gallery" is "shuffle the pool". Uploads from the config UI are a first-class second source, so the panel works with no Google account at all. |
| In-browser Google sign-in (2026-07-27) | Paste-bridge OAuth. Google accepts only HTTPS or loopback redirect URIs and the Pi serves plain HTTP at `http://inky:8000`, which can never be registered; the device flow (which would suit a headless box) supports only Drive/YouTube/profile scopes. So the UI opens consent in a tab, Google redirects to `http://localhost:8000/`, and the user pastes that address back — except on the dev machine, where the backend *is* localhost:8000 and the frontend completes the flow itself. PKCE keeps the verifier on the device, so a pasted code is useless to anyone else. One shared OAuth client, **one token file per service** (`google_token.json`, `google_photos_token.json`) — widening calendar's scopes would have invalidated a working token. |
| Photo pool on disk (2026-07-27) | Photos are copied into `data/photos/` (JPEG, ≤1600×960 ≈ 2× the panel, q88) with a JSON index, capped by `photos.max_photos` and pruned oldest-first. Hour-long Google URLs are useless to a panel that redraws for months; the pool also renders with the network down and survives deploys. Which photo is up is a pure function of the wall clock, so the frame is identical between rotation boundaries and the loop skips the ~30s refresh. |
| Vox word source (2026-07-27) | Curated word list bundled in `modules/vox_words.py`, definitions from dictionaryapi.dev (free, no key, Wiktionary-derived). A "random word" API returns `the` as readily as `quotidian`, and the point of the panel is words worth learning; bundling also means the choice is offline and stable. The daily word is `sha256(date)` over the list, so every fetch through the day composes an identical frame and the loop skips the ~30s refresh; words the dictionary doesn't know are skipped at fetch time (up to 4 attempts). |
| Vox definition source (2026-09-08) | Moved off dictionaryapi.dev to **Wiktionary's REST API** (`/api/rest_v1/page/definition/`) for definitions and **Datamuse** (`rel_syn`) for synonyms — both free and keyless. dictionaryapi.dev's origin server went dark: Cloudflare returned 522 after ~19.5s for every word not in its edge cache (a `hello` lookup came back `cf-cache-status: STALE`, `age` 42 days), which is every word on a list curated precisely for *uncommon* vocabulary. The panel had been stuck since 2026-09-07 09:37. Wiktionary is the corpus dictionaryapi.dev was derived from, so definitions are equivalent, served by the WMF rather than one unattended host, and ~85x faster (0.22s vs 19.5s). Costs: definitions arrive as rendered HTML and must be flattened (`strip_html` — `<style>` blocks are dropped *with their CSS text*, which would otherwise be drawn on the panel), the payload is keyed by language so only `en` is read, and there is no IPA — `show_phonetic` is now a dead config field and is gone from the options panel. Examples survive for the minority of words that have them. |
| Vox failure handling (2026-09-08) | A 5xx and a transport error now fall through to the next candidate word exactly as a 404 always did; previously only 404 was retryable, so a single server error aborted the fetch and `MAX_ATTEMPTS` never engaged. The loop's keep-stale-data-on-failure policy is right for a 30s-refresh panel but silently passed a week-old word off as today's, so `fetch` stamps `fetched_on` and the renderer marks a stale daily word `OFFLINE` — constant text, so the frame still hashes identically between refreshes and does not re-flash while the feed is down. |
| Moon data source (2026-07-27) | Computed on the Pi (`astro.py`, truncated Meeus series), not fetched. Every moon API is a network dependency and a rate limit for numbers that are a pure function of the clock; ~200 lines of trigonometry match Meeus's published examples to the arcsecond and keep the panel working offline. Consequences: no `fetch_key` and no fetch interval to configure, and the observer position is `weather.latitude/longitude` — moonrise and the terminator's orientation need a location, and a second one would only drift out of sync with the first. Values are rendered at whole-unit precision (integer percent, whole days of age) so the frame changes about once a day rather than every refresh. |
| No incremental OAuth (2026-07-27) | `include_granted_scopes` removed from the consent URL after it produced a Calendar-scoped token when connecting Photos on an account that already had Calendar — incremental authorisation returns the union of everything the client was ever granted, which is precisely wrong for a per-service-token design. `complete()` now also verifies the *granted* scopes (from `credentials.granted_scopes`, not `credentials.scopes`) and refuses a token missing what the service needs, naming the Cloud API to enable rather than letting it 403 later from somewhere unrelated. |
| Google Drive as a photo source (2026-07-27) | Added alongside the Picker: a folder id/URL in `photos.drive_folder`, mirrored into the pool every `drive_sync_minutes`. It is the only source that stays current on its own — drop photos in from a phone and the frame follows — and so it, not the Picker, is the real answer to "point it at an album". Costs `drive.readonly`, a **restricted** scope covering the whole Drive: `drive.file` grants no access to the contents of a folder the user selects, so there is no narrower option. Personal use is exempt from Google's security assessment. Mitigations: opt-in, own token file (revocable alone), every call confined to the folder and to image MIME types. Note Photos↔Drive sync ended in 2019, so a Drive folder is a deliberate "frame album", not a mirror of Google Photos. |
| Background photo jobs (2026-07-27) | Picking and Drive syncing run in `photos_sync.py::PhotoJobs`, started in the app lifespan; the UI only starts them and polls `/api/photos/jobs`. Picking was originally driven from the browser, which meant a closed tab or a sleeping laptop left the selection unimported until Google's ~60-minute URLs expired — silently. Server-side, a reload rejoins the running job, and picking on a phone works properly. One job of each kind at a time. |
| Photo dedupe (2026-07-27) | `add_bytes()` returns None instead of storing a duplicate: `source_id` first (checked before downloading, so an unchanged Drive folder costs one list call), sha256 of the source bytes as the backstop — Picker media-item ids are not documented as stable across sessions, and re-picking the same album is the common case. |
| Preview fidelity for photos (2026-07-27) | Opt-in `?simulate=1` on the preview endpoints dithers the frame to the six panel colours. Text panels are already palette-exact, but a photograph in the browser looks nothing like what the panel shows. Preview-only: the display path still hands the driver plain un-quantised RGB so `set_image(saturation=…)` can apply its own curve. |
| Per-photo framing (2026-07-28) | Cropping to "Fill" is what makes a photo fit an 800×480 panel, and centred is right for most pictures but beheads portraits. Each pool entry may carry `focus: [x, y]` (0..1) anchoring `ImageOps.fit(centering=…)`; **absent means centred**, so nothing is written for a photo left alone and the default costs no state. It lives in the photo index rather than `config.photos` because it belongs to the picture, not to a layout or a draft — so it saves immediately (`PUT /api/photos/{id}/focus`) instead of with the config, and survives every layout change. The editor drags in CSS (`object-fit: cover` + `object-position` is precisely Pillow's crop) for an instant response, and calls `/api/photos/preview/{id}.png` — the panel's own renderer at the region's size — only for the honest dithered view. The frame's aspect comes from the server (`/api/photos`'s `frame`), taken from the layout actually hosting the panel, so the editor never invents a shape the display doesn't have. |
