"""Google Drive folder as a photo source.

Point the frame at a Drive folder and it syncs periodically: drop photos in from
a phone and they appear on the panel without touching the config UI. This is the
closest thing left to "show me an album", since the Google Photos APIs can no
longer enumerate one (see google_photos.py).

**Scope note.** This needs ``drive.readonly`` — read access to the whole Drive —
because Google's narrow ``drive.file`` scope explicitly grants *no* access to the
contents of a folder the user selects, only to individually picked files. There
is no middle ground on offer. It is a restricted scope, exempt from Google's
security-assessment requirement for personal use, and it lives in its own token
file so it can be revoked without disturbing Calendar or Photos. Every call here
is confined to the configured folder and to image MIME types.

Blocking; call from a thread.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from inky_dash import google, photos_store

log = logging.getLogger(__name__)

PAGE_SIZE = 100
FIELDS = "nextPageToken, files(id, name, mimeType, createdTime, imageMediaMetadata(time))"


class DriveError(Exception):
    """Reported to the UI as data; never a 500."""


def parse_folder_id(value: str) -> str:
    """Accept either a bare folder id or the URL people actually copy out of
    Drive's address bar / share dialog."""
    value = (value or "").strip()
    if not value:
        return ""
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    return value.split("?")[0].strip("/")


def _service():
    if not google.is_connected(google.DRIVE):
        raise DriveError("Google Drive is not connected yet.")
    from googleapiclient.discovery import build

    try:
        return build(
            "drive", "v3", credentials=google.credentials(google.DRIVE), cache_discovery=False
        )
    except Exception as exc:
        raise DriveError(f"Google sign-in needs renewing: {exc}") from None


def list_images(folder_id: str) -> list[dict[str, Any]]:
    """Every image directly inside ``folder_id`` (no recursion — a frame album is
    a flat folder, and recursing would quietly widen what gets pulled in)."""
    if not folder_id:
        raise DriveError("No Drive folder set.")
    service = _service()
    query = f"'{folder_id}' in parents and trashed = false and mimeType contains 'image/'"
    files: list[dict[str, Any]] = []
    page = None
    try:
        while True:
            response = (
                service.files()
                .list(
                    q=query,
                    fields=FIELDS,
                    pageSize=PAGE_SIZE,
                    pageToken=page,
                    orderBy="createdTime",
                    # Shared drives are a normal place to keep a family album.
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                )
                .execute()
            )
            files.extend(response.get("files", []))
            page = response.get("nextPageToken")
            if not page:
                return files
    except DriveError:
        raise
    except Exception as exc:
        raise DriveError(_explain(exc)) from None


def _explain(exc: Exception) -> str:
    """Turn the common Drive failures into something a user can act on."""
    text = str(exc)
    if "404" in text or "notFound" in text:
        return "That folder does not exist, or this Google account cannot see it."
    if "403" in text:
        return "Google refused access — check the Drive API is enabled on the project."
    return f"Google Drive error: {text}"


def download(file_id: str) -> bytes:
    return _service().files().get_media(fileId=file_id, supportsAllDrives=True).execute()


def sync_folder(folder_id: str, max_photos: int) -> dict[str, Any]:
    """Pull anything new from the folder into the pool.

    Only files we have never seen are downloaded — the id check happens before
    the request, so a routine sync of an unchanged folder costs one list call.
    Photos deleted from Drive are left alone: the pool is the frame's own
    library, not a mirror.
    """
    files = list_images(folder_id)
    added, skipped, errors = 0, 0, []

    for item in files:
        if photos_store.has_source_id(item["id"]):
            skipped += 1
            continue
        try:
            entry = photos_store.add_bytes(
                download(item["id"]),
                source="drive",
                filename=item.get("name", ""),
                taken=(item.get("imageMediaMetadata") or {}).get("time") or item.get("createdTime"),
                source_id=item["id"],
            )
            if entry is None:  # same bytes already in the pool under another id
                skipped += 1
            else:
                added += 1
        except (photos_store.PhotoError, OSError) as exc:
            skipped += 1
            errors.append(f"{item.get('name', item['id'])}: {exc}")
            log.warning("skipped a Drive photo: %s", exc)

    pruned = photos_store.prune(max_photos)
    return {
        "added": added,
        "skipped": skipped,
        "pruned": pruned,
        "found": len(files),
        "errors": errors[:5],
    }
