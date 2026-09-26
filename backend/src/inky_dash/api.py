from __future__ import annotations

import io
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from inky_dash.config import (
    DEFAULT_COLS,
    DEFAULT_ROWS,
    MAX_PLAYLIST_ITEMS,
    MAX_REGIONS,
    MIN_REGION_CELLS,
    AppConfig,
    validate_config,
)
from inky_dash.layout import PRESETS, find_layout, region_rect, resolve_layout
from inky_dash.loop import Dashboard
from inky_dash.modules import MODULES
from inky_dash.modules.options import module_meta
from inky_dash.render import compose

router = APIRouter(prefix="/api")

# The Google services a user can connect from the UI, by their public name.
SERVICES = {"calendar": "google", "photos": "google_photos", "drive": "google_drive"}


class ConnectRequest(BaseModel):
    service: str


class CompleteRequest(BaseModel):
    # The state handed out by /google/connect, and whatever the user pasted back:
    # the whole redirect URL, or just the code.
    state: str = ""
    response: str


class PhotoFocus(BaseModel):
    """Where a photo is anchored when it is cropped to fill its region, 0..1 in
    each axis. None (or an omitted field) means the default: dead centre."""

    focus: tuple[float, float] | None = None


def _dashboard(request: Request) -> Dashboard:
    return request.app.state.dashboard


def _jobs(request: Request):
    return request.app.state.photo_jobs


def _validate_or_422(config: AppConfig) -> None:
    try:
        validate_config(config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/config")
def get_config(request: Request) -> AppConfig:
    return _dashboard(request).config


@router.put("/config")
async def put_config(request: Request, config: AppConfig) -> AppConfig:
    # async so wake() runs on the event loop: asyncio.Event.set() from a
    # threadpool thread would not rouse the render loop's waiter
    _validate_or_422(config)
    _dashboard(request).update_config(config)
    # A newly pointed Drive folder should sync now, not up to a minute later.
    _jobs(request).wake_drive()
    return config


@router.get("/meta")
def get_meta(request: Request) -> dict:
    """Static facts the config UI needs: single source of truth for the module
    registry (including each module's declarative options schema), built-in
    presets, and layout validation limits."""
    return {
        "resolution": _dashboard(request).display.resolution,
        "modules": [module_meta(module) for module in MODULES.values()],
        "presets": [layout.model_dump() for layout in PRESETS.values()],
        "limits": {
            "min_region": MIN_REGION_CELLS,
            "max_regions": MAX_REGIONS,
            "default_cols": DEFAULT_COLS,
            "default_rows": DEFAULT_ROWS,
            "max_playlist_items": MAX_PLAYLIST_ITEMS,
        },
    }


@router.get("/status")
def get_status(request: Request) -> dict:
    dashboard = _dashboard(request)
    return {
        "last_render": dashboard.last_render.isoformat() if dashboard.last_render else None,
        "display": type(dashboard.display).__name__,
        "resolution": dashboard.display.resolution,
        "modules": dashboard.status,
        # Which layout is on the panel, and — while a playlist is running — where
        # the rotation has got to. None when the display is on a single layout.
        "layout": dashboard.current_layout_id(),
        "playlist": dashboard.playlist_status(),
    }


@router.post("/refresh")
async def refresh(request: Request) -> dict:
    # async for the same reason as put_config: the wake must happen on the loop.
    # Forced: an explicit button press always flashes the panel, even if the
    # frame is unchanged — it doubles as the ghosting-clear control.
    _dashboard(request).force_refresh()
    return {"ok": True}


@router.post("/playlist/next")
async def playlist_next(request: Request) -> dict:
    """Skip to the next screen in the rotation. No-op (beyond a forced refresh)
    when no playlist is running. async so the wake happens on the event loop."""
    dashboard = _dashboard(request)
    dashboard.next_screen()
    return {"ok": True, "playlist": dashboard.playlist_status()}


@router.get("/calendars")
async def calendars() -> dict:
    """Calendars available to the configured Google account, for the config UI."""
    import asyncio

    from inky_dash.modules.calendar import list_calendars, token_path

    if not token_path().exists():
        return {"configured": False, "calendars": []}
    try:
        return {"configured": True, "calendars": await asyncio.to_thread(list_calendars)}
    except Exception as exc:  # surface as data: the UI shows a hint, never a 500
        return {"configured": False, "calendars": [], "error": str(exc)}


@router.get("/google/status")
def google_status() -> dict:
    """Which Google services are connected, for the config UI's connect cards."""
    from inky_dash import google, google_oauth

    return {
        "credentials": google.credentials_path().exists(),
        # The Cloud project every consent screen comes from, and whose enabled
        # APIs are the ones that count. Decided solely by google_credentials.json,
        # which is easy to get wrong and impossible to see otherwise.
        "project": google.project_id(),
        "services": {name: google_oauth.status(s) for name, s in SERVICES.items()},
    }


@router.post("/google/connect")
def google_connect(body: ConnectRequest) -> dict:
    """Begin browser sign-in; the UI opens auth_url and posts the result back."""
    from inky_dash import google_oauth

    service = SERVICES.get(body.service)
    if service is None:
        raise HTTPException(status_code=422, detail=f"unknown service {body.service!r}")
    try:
        return google_oauth.start(service)
    except google_oauth.OAuthError as exc:  # surfaced as a hint, never a 500
        return {"error": str(exc)}


@router.post("/google/complete")
def google_complete(body: CompleteRequest) -> dict:
    from inky_dash import google_oauth

    try:
        return google_oauth.complete(body.state, body.response)
    except google_oauth.UnknownFlow as exc:
        # Flagged so the UI can offer the address for pasting into the instance
        # that actually started the sign-in, instead of reporting a dead end.
        return {"connected": False, "error": str(exc), "unknown_state": True}
    except google_oauth.OAuthError as exc:
        return {"connected": False, "error": str(exc)}


@router.delete("/google/{service}")
def google_disconnect(service: str) -> dict:
    from inky_dash import google

    known = SERVICES.get(service)
    if known is None:
        raise HTTPException(status_code=422, detail=f"unknown service {service!r}")
    return {"disconnected": google.disconnect(known)}


@router.get("/photos/jobs")
def photo_jobs(request: Request) -> dict:
    """Progress of the background photo work. The UI polls this while something
    is running; because the work lives on the server, reloading the page (or
    picking on a phone and coming back later) rejoins the same job."""
    return _jobs(request).status()


@router.post("/photos/picker/session")
async def picker_start(request: Request) -> dict:
    """Open a Google Photos picking session and wait for it **server-side**: the
    user can close the tab, or pick on their phone, and the import still happens.
    It has to — Google's download URLs expire about an hour after picking."""
    return await _jobs(request).start_picker()


@router.post("/photos/picker/cancel")
async def picker_cancel(request: Request) -> dict:
    _jobs(request).cancel_picker()
    return {"ok": True}


@router.post("/photos/drive/sync")
async def drive_sync(request: Request) -> dict:
    """Sync the configured Drive folder now, rather than waiting for the next
    scheduled pass. Uses the *saved* config, so save a changed folder first."""
    return await _jobs(request).sync_drive_now()


@router.get("/photos")
def photos_list(request: Request) -> dict:
    """The local photo pool the photos panel draws from."""
    from inky_dash import photos_store

    return {
        "photos": photos_store.load_index(),
        "bytes_used": photos_store.total_bytes(),
        "max_photos": _dashboard(request).config.photos.max_photos,
        # So the framing editor can show the shape a photo is actually cropped to.
        "frame": list(_photo_frame(_dashboard(request))),
    }


def _photo_frame(dashboard: Dashboard) -> tuple[int, int]:
    """The pixel size of the photos panel on the display — the layout on the
    panel now, else any layout that hosts the module, else the whole display."""
    size = dashboard.display.resolution
    layouts = [
        resolve_layout(dashboard.config.layout, dashboard.current_layout_id()),
        *dashboard.config.layout.layouts,
        *PRESETS.values(),
    ]
    for layout in layouts:
        for region in layout.regions:
            if region.module == "photos":
                _, _, width, height = region_rect(region, layout.cols, layout.rows, size)
                return (width, height)
    return size


@router.post("/photos/upload")
async def photos_upload(request: Request, files: Annotated[list[UploadFile], File()]) -> dict:
    """Add images straight from the config UI — the source that needs no Google
    account at all. Unreadable files are reported, never fatal."""
    from inky_dash import photos_store

    added, duplicates, errors = 0, 0, []
    for upload in files:
        try:
            entry = photos_store.add_bytes(
                await upload.read(), source="upload", filename=upload.filename or ""
            )
            if entry is None:  # same bytes already in the pool
                duplicates += 1
            else:
                added += 1
        except photos_store.PhotoError as exc:
            errors.append(str(exc))
    dashboard = _dashboard(request)
    pruned = photos_store.prune(dashboard.config.photos.max_photos)
    dashboard.invalidate("photos")
    return {"added": added, "duplicates": duplicates, "pruned": pruned, "errors": errors}


@router.delete("/photos")
async def photos_clear(request: Request) -> dict:
    from inky_dash import photos_store

    removed = photos_store.clear()
    _dashboard(request).invalidate("photos")
    return {"removed": removed}


@router.delete("/photos/{photo_id}")
async def photos_delete(request: Request, photo_id: str) -> dict:
    from inky_dash import photos_store

    removed = photos_store.remove(photo_id)
    if not removed:
        raise HTTPException(status_code=404, detail="no such photo")
    _dashboard(request).invalidate("photos")
    return {"ok": True}


@router.get("/photos/thumb/{photo_id}")
def photos_thumb(photo_id: str) -> Response:
    from inky_dash import photos_store

    if not photos_store.exists(photo_id):
        raise HTTPException(status_code=404, detail="no such photo")
    return Response(content=photos_store.thumbnail(photo_id), media_type="image/jpeg")


@router.get("/photos/full/{photo_id}")
def photos_full(photo_id: str) -> Response:
    """The stored photo itself, for the framing editor to drag around — the grid
    thumbnail is far too small to position against."""
    from inky_dash import photos_store

    if not photos_store.exists(photo_id):
        raise HTTPException(status_code=404, detail="no such photo")
    return Response(
        content=photos_store.path_for(photo_id).read_bytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.put("/photos/{photo_id}/focus")
async def photos_set_focus(request: Request, photo_id: str, body: PhotoFocus) -> dict:
    """Anchor one photo's crop. `focus` is 0..1 in each axis; omitting it (or
    sending null) restores the default centre."""
    from inky_dash import photos_store

    entry = photos_store.set_focus(photo_id, body.focus)
    if entry is None:
        raise HTTPException(status_code=404, detail="no such photo")
    # The panel caches its payload, which carries the focus; make it re-read.
    _dashboard(request).invalidate("photos")
    return entry


@router.get("/photos/preview/{photo_id}.png")
def photos_preview(
    request: Request,
    photo_id: str,
    fx: float | None = None,
    fy: float | None = None,
    simulate: bool = False,
) -> Response:
    """One photo drawn exactly as the panel would draw it, at the size of the
    photos region in the current layout. `fx`/`fy` override the stored focus so
    the editor can preview a position before it is saved."""
    from PIL import Image

    from inky_dash import palette, photos_store
    from inky_dash.modules.photos import payload_for

    entry = photos_store.find(photo_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="no such photo")
    if fx is not None and fy is not None:
        entry = {**entry, "focus": [fx, fy]}

    dashboard = _dashboard(request)
    size = _photo_frame(dashboard)
    frame = Image.new("RGB", size, palette.WHITE)
    data = payload_for(entry, len(photos_store.load_index()))
    MODULES["photos"].render(frame, (0, 0, *size), data, dashboard.config)
    return _png_response(frame, simulate)


@router.get("/sensor/history")
def sensor_history(hours: int = 24) -> dict:
    from inky_dash.storage import SensorStore

    readings = SensorStore().history(min(hours, 168))
    return {
        "readings": [
            {
                "ts": r.ts.isoformat(),
                "temp_c": r.temp_c,
                "humidity": r.humidity,
                "battery": r.battery,
            }
            for r in readings
        ]
    }


def _png_response(frame, simulate: bool = False) -> Response:
    if simulate:
        from inky_dash import palette

        frame = palette.simulate(frame)
    buf = io.BytesIO()
    frame.save(buf, format="PNG")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/preview.png")
def preview(request: Request, simulate: bool = False) -> Response:
    """Render the saved config from cached data in-memory; never touches hardware.
    `simulate` dithers the result to the panel's six colours."""
    return _png_response(_dashboard(request).render_frame(), simulate)


@router.post("/preview.png")
async def preview_candidate(
    request: Request, config: AppConfig, layout: str | None = None, simulate: bool = False
) -> Response:
    """Render a candidate (unsaved) config exactly as the panel would show it.
    `layout` picks which layout to draw — the one being edited, or a playlist
    screen — defaulting to the config's active layout. Missing/stale module data
    is fetched on demand through the shared TTL cache, so the preview is
    truthful; nothing is saved and the hardware is untouched."""
    _validate_or_422(config)
    if layout is not None and find_layout(config.layout, layout) is None:
        raise HTTPException(status_code=422, detail=f"layout {layout!r} does not exist")
    dashboard = _dashboard(request)
    resolved = resolve_layout(config.layout, layout)
    module_ids = [region.module for region in resolved.regions if region.module]
    await dashboard.ensure_data(config, module_ids)
    return _png_response(
        compose(config, dashboard.data, dashboard.display.resolution, layout), simulate
    )
