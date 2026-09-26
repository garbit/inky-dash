"""The local photo pool: images on disk plus a JSON index.

Google's Picker API hands back URLs that live for an hour, which is useless to a
panel that redraws every ten minutes for months — so every photo, however it
arrives, is downscaled and copied into ``data/photos/`` and rendered from there.
The pool works with the network down and survives deploys (``data/`` is
preserved).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import uuid
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from inky_dash.config import data_dir

log = logging.getLogger(__name__)

# Twice the panel's 800x480, so a crop still has pixels to spare. ~250KB per
# JPEG, which keeps a full 500-photo pool comfortably inside a small SD card.
MAX_SIZE = (1600, 960)
JPEG_QUALITY = 88
THUMB_SIZE = (240, 240)


class PhotoError(ValueError):
    """A photo could not be decoded — surfaced to the UI, never raised at render."""


def photos_dir() -> Path:
    path = data_dir() / "photos"
    path.mkdir(parents=True, exist_ok=True)
    return path


def index_path() -> Path:
    return photos_dir() / "index.json"


def path_for(photo_id: str) -> Path:
    # Ids are ours (hex), but they arrive back as URL path params — anything else
    # is refused rather than resolved, so no request can escape the pool dir.
    if not photo_id or not all(c in "0123456789abcdef" for c in photo_id):
        raise PhotoError(f"invalid photo id {photo_id!r}")
    return photos_dir() / f"{photo_id}.jpg"


def load_index() -> list[dict[str, Any]]:
    """Every pool entry, oldest first. A missing or corrupt index reads as empty
    rather than raising: the render loop must never die over a bad file."""
    path = index_path()
    if not path.exists():
        return []
    try:
        entries = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        log.exception("photo index unreadable; treating the pool as empty")
        return []
    if not isinstance(entries, list):
        return []
    # Drop malformed entries and any whose image has gone missing underneath us.
    return [e for e in entries if isinstance(e, dict) and exists(e.get("id", ""))]


def find(photo_id: str) -> dict[str, Any] | None:
    return next((e for e in load_index() if e.get("id") == photo_id), None)


def exists(photo_id: Any) -> bool:
    if not isinstance(photo_id, str):
        return False
    try:
        return path_for(photo_id).exists()
    except PhotoError:
        return False


def save_index(entries: list[dict[str, Any]]) -> None:
    """Atomic write, matching config.py::save_config — a crash mid-write must not
    corrupt the index."""
    path = index_path()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".index-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(entries, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _exif_taken(image: Image.Image) -> str | None:
    """DateTimeOriginal as an ISO date, for the 'on this day' rotation."""
    try:
        exif = image.getexif()
    except Exception:  # pragma: no cover - Pillow raises assorted things here
        return None
    raw = exif.get(0x9003) or exif.get(0x0132)  # DateTimeOriginal, DateTime
    if not isinstance(raw, str):
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y:%m:%d %H:%M:%S").isoformat()
    except ValueError:
        return None


def has_source_id(source_id: str) -> bool:
    """Whether a Google Photos media item / Drive file is already in the pool —
    checked *before* downloading, so a repeated sync costs nothing."""
    return any(e.get("source_id") == source_id for e in load_index())


def add_bytes(
    raw: bytes,
    *,
    source: str = "upload",
    filename: str = "",
    taken: str | None = None,
    source_id: str | None = None,
) -> dict[str, Any] | None:
    """Decode, orient, downscale and store one image; return its index entry, or
    None if the pool already holds it.

    Duplicates are caught two ways: by ``source_id`` where the origin gives us a
    stable one (Drive file ids are; Picker media item ids may not survive a new
    session), and otherwise by a hash of the original bytes — which is what
    actually saves you from re-importing the same album twice.
    """
    digest = hashlib.sha256(raw).hexdigest()
    index = load_index()
    if any(
        e.get("sha256") == digest or (source_id and e.get("source_id") == source_id) for e in index
    ):
        return None

    try:
        original = Image.open(BytesIO(raw))
        original.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise PhotoError(f"{filename or 'image'}: not a readable image") from exc

    # EXIF orientation must be baked in now — the renderer sees only the JPEG.
    image = ImageOps.exif_transpose(original) or original
    taken = taken or _exif_taken(original)
    image = image.convert("RGB")
    image.thumbnail(MAX_SIZE, Image.LANCZOS)

    photo_id = uuid.uuid4().hex[:16]
    image.save(path_for(photo_id), format="JPEG", quality=JPEG_QUALITY)

    entry = {
        "id": photo_id,
        "source": source,
        "source_id": source_id,
        "sha256": digest,
        "filename": filename,
        "taken": taken,
        "added": datetime.now(UTC).isoformat(),
        "width": image.width,
        "height": image.height,
    }
    # Re-read rather than reusing `index`: decoding a large photo takes long
    # enough that a concurrent import may have appended in the meantime.
    save_index([*load_index(), entry])
    return entry


def set_focus(photo_id: str, focus: tuple[float, float] | None) -> dict[str, Any] | None:
    """Remember where a photo should be anchored when it is cropped to fill a
    region — 0..1 in each axis, (0.5, 0.5) being the centre. ``None`` drops the
    key, so an unpositioned photo carries nothing and reads as centred.

    Returns the updated entry, or None if the pool has no such photo.
    """
    entries = load_index()
    for entry in entries:
        if entry.get("id") != photo_id:
            continue
        if focus is None:
            entry.pop("focus", None)
        else:
            entry["focus"] = [_clamp01(focus[0]), _clamp01(focus[1])]
        save_index(entries)
        return entry
    return None


def _clamp01(value: float) -> float:
    return round(min(max(float(value), 0.0), 1.0), 4)


def remove(photo_id: str) -> bool:
    entries = load_index()
    remaining = [e for e in entries if e["id"] != photo_id]
    if len(remaining) == len(entries):
        return False
    path_for(photo_id).unlink(missing_ok=True)
    save_index(remaining)
    return True


def clear() -> int:
    entries = load_index()
    for entry in entries:
        path_for(entry["id"]).unlink(missing_ok=True)
    save_index([])
    return len(entries)


def prune(max_items: int) -> int:
    """Drop the oldest entries beyond ``max_items``; return how many went."""
    entries = load_index()
    excess = len(entries) - max_items
    if excess <= 0:
        return 0
    for entry in entries[:excess]:
        path_for(entry["id"]).unlink(missing_ok=True)
    save_index(entries[excess:])
    return excess


def total_bytes() -> int:
    return sum(path_for(e["id"]).stat().st_size for e in load_index())


def thumbnail(photo_id: str) -> bytes:
    """Small JPEG for the config UI's grid."""
    image = Image.open(path_for(photo_id))
    image.thumbnail(THUMB_SIZE, Image.LANCZOS)
    buf = BytesIO()
    image.convert("RGB").save(buf, format="JPEG", quality=80)
    return buf.getvalue()
