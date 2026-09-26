"""Google Photos Picker API client.

Google withdrew the broad Library API scopes on 31 March 2025 — an app can no
longer list a user's albums or read arbitrary photos. The Picker API replaced
them: we create a session, the user picks items in Google's own UI (searching an
album's name to select its contents), we poll until they're done, then download
what they chose.

Picked ``baseUrl``s live about an hour and the session expires too, so the bytes
are copied into the local pool immediately (see photos_store.py). After that the
panel never needs Google again.

There is no discovery document for this API, so it's plain REST over an
authorised session. Everything here blocks — call it from a thread.
"""

from __future__ import annotations

import logging
from typing import Any

from inky_dash import google, photos_store

log = logging.getLogger(__name__)

BASE = "https://photospicker.googleapis.com/v1"
TIMEOUT = 30
# Downloaded at twice the panel's resolution, matching photos_store.MAX_SIZE, so
# a crop has pixels to spare without hauling full-size originals over the Pi's wifi.
DOWNLOAD_SIZE = photos_store.MAX_SIZE


class PickerError(Exception):
    """Reported to the UI as data; never a 500."""


def _session() -> Any:
    if not google.is_connected(google.PHOTOS):
        raise PickerError("Google Photos is not connected yet.")
    try:
        return google.session(google.PHOTOS)
    except Exception as exc:
        raise PickerError(f"Google sign-in needs renewing: {exc}") from None


def _json(response: Any) -> dict[str, Any]:
    if not response.ok:
        raise PickerError(f"Google Photos said {response.status_code}: {response.text[:200]}")
    return response.json()


def create_session() -> dict[str, Any]:
    """Start a picking session; returns the URI to open and how often to poll."""
    body = _json(_session().post(f"{BASE}/sessions", json={}, timeout=TIMEOUT))
    polling = body.get("pollingConfig") or {}
    return {
        "session_id": body["id"],
        "picker_uri": body.get("pickerUri", ""),
        # Durations arrive as e.g. "5s"; the UI wants a number of seconds.
        "poll_seconds": _seconds(polling.get("pollInterval"), 5.0),
        "timeout_seconds": _seconds(polling.get("timeoutIn"), 900.0),
        "ready": bool(body.get("mediaItemsSet")),
    }


def get_session(session_id: str) -> dict[str, Any]:
    body = _json(_session().get(f"{BASE}/sessions/{session_id}", timeout=TIMEOUT))
    return {"ready": bool(body.get("mediaItemsSet")), "session_id": body.get("id", session_id)}


def delete_session(session_id: str) -> None:
    """Best effort: an orphaned session expires on its own, and failing to tidy up
    must not lose photos we have already imported."""
    try:
        _session().delete(f"{BASE}/sessions/{session_id}", timeout=TIMEOUT)
    except Exception:
        log.debug("could not delete picker session %s", session_id, exc_info=True)


def list_media_items(session_id: str) -> list[dict[str, Any]]:
    session, items, page = _session(), [], None
    while True:
        params: dict[str, Any] = {"sessionId": session_id, "pageSize": 100}
        if page:
            params["pageToken"] = page
        body = _json(session.get(f"{BASE}/mediaItems", params=params, timeout=TIMEOUT))
        items.extend(body.get("mediaItems", []))
        page = body.get("nextPageToken")
        if not page:
            return items


def import_session(session_id: str, max_photos: int) -> dict[str, Any]:
    """Download everything the user picked into the local pool.

    Runs promptly after picking: the URLs expire in about an hour. Videos and
    anything that fails to download are counted and skipped rather than aborting
    the import — a part-imported batch is far better than none.
    """
    session = _session()
    items = list_media_items(session_id)
    imported, skipped, duplicates, errors = 0, 0, 0, []

    for item in items:
        media = item.get("mediaFile") or {}
        mime = media.get("mimeType", "")
        if not mime.startswith("image/"):
            skipped += 1  # the picker offers videos too; the panel can't show them
            continue
        base_url = media.get("baseUrl")
        if not base_url:
            skipped += 1
            continue
        if item.get("id") and photos_store.has_source_id(item["id"]):
            duplicates += 1
            continue
        try:
            # The size parameter is mandatory, and the download needs the bearer
            # token — these URLs are not public.
            url = f"{base_url}=w{DOWNLOAD_SIZE[0]}-h{DOWNLOAD_SIZE[1]}"
            response = session.get(url, timeout=TIMEOUT)
            if not response.ok:
                raise PickerError(f"download failed ({response.status_code})")
            entry = photos_store.add_bytes(
                response.content,
                source="google",
                filename=media.get("filename", ""),
                taken=(
                    item.get("createTime") or media.get("mediaFileMetadata", {}).get("creationTime")
                ),
                source_id=item.get("id"),
            )
            # Picker ids are not guaranteed stable between sessions, so the same
            # photo picked twice is caught by its content hash instead.
            if entry is None:
                duplicates += 1
            else:
                imported += 1
        except (PickerError, photos_store.PhotoError, OSError) as exc:
            skipped += 1
            errors.append(str(exc))
            log.warning("skipped a picked photo: %s", exc)

    pruned = photos_store.prune(max_photos)
    delete_session(session_id)
    return {
        "imported": imported,
        "skipped": skipped,
        "duplicates": duplicates,
        "pruned": pruned,
        "total": len(items),
        "errors": errors[:5],
    }


def _seconds(duration: Any, default: float) -> float:
    """Protobuf durations serialise as strings like '5s' or '3.5s'."""
    if isinstance(duration, int | float):
        return float(duration)
    if isinstance(duration, str) and duration.endswith("s"):
        try:
            return float(duration[:-1])
        except ValueError:
            return default
    return default
