"""Photo frame panel: shows one image from the local pool, rotating on a clock.

Photos reach the pool either from Google Photos (via the Picker API — see
google_photos.py) or by upload from the config UI; either way this module only
ever reads ``data/photos/``, so it renders with the network down.
"""

from __future__ import annotations

import random
import time
from datetime import datetime
from typing import Any

from PIL import Image, ImageEnhance, ImageOps

import inky_dash.modules.options as opts
from inky_dash import palette, photos_store
from inky_dash.config import AppConfig
from inky_dash.fonts import font
from inky_dash.layout import Rect
from inky_dash.modules.common import PAD, inset, panel_draw, title_bar, wrap_text

CAPTION_HEIGHT = 22
# Where a cropped photo is anchored when nothing says otherwise: the middle.
CENTRE = (0.5, 0.5)


def pick_photo(
    entries: list[dict[str, Any]], mode: str, now: float, rotate_minutes: int
) -> dict[str, Any] | None:
    """Which photo is up right now — a pure function of the wall clock, so the
    payload is identical between rotation boundaries and the render loop can skip
    the ~30s panel refresh (the same trick as govee's averaged view)."""
    if not entries:
        return None
    bucket = int(now) // max(1, rotate_minutes * 60)

    if mode == "on_this_day":
        today = datetime.fromtimestamp(now)
        matches = [e for e in entries if _taken_on(e, today.month, today.day)]
        if matches:  # fall through to shuffle when nothing was taken today
            entries = matches
    if mode == "sequential":
        return entries[bucket % len(entries)]
    # Shuffle: a fresh permutation each time the pool has been fully cycled, so a
    # photo never repeats before the others have had a turn.
    order = random.Random(bucket // len(entries)).sample(entries, len(entries))
    return order[bucket % len(entries)]


def _taken_on(entry: dict[str, Any], month: int, day: int) -> bool:
    taken = entry.get("taken")
    if not isinstance(taken, str):
        return False
    try:
        stamp = datetime.fromisoformat(taken)
    except ValueError:
        return False
    return (stamp.month, stamp.day) == (month, day)


def focus_of(entry: Any) -> tuple[float, float]:
    """The photo's stored anchor point, 0..1 in each axis. Absent, malformed or
    out-of-range values all read as the centre — framing is a nicety and must
    never be able to break a render."""
    raw = entry.get("focus") if isinstance(entry, dict) else None
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        return CENTRE
    try:
        x, y = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return CENTRE
    return (min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0))


def payload_for(entry: dict[str, Any], pool: int) -> dict[str, Any]:
    """The render payload for one pooled photo — shared by fetch() and the
    per-photo preview endpoint, so what the framing editor shows is drawn by
    exactly the code that draws the panel."""
    return {
        "pool": pool,
        "id": entry["id"],
        "path": str(photos_store.path_for(entry["id"])),
        "taken": entry.get("taken"),
        "filename": entry.get("filename") or "",
        "focus": list(focus_of(entry)),
    }


def prepare_image(
    path: str, box: tuple[int, int], config: Any, focus: tuple[float, float] = CENTRE
) -> Image.Image:  # config: PhotosConfig
    """Load a pooled photo and fit it to ``box``.

    ``focus`` anchors the crop in "cover" framing — the photo's own setting, so a
    face at the edge survives a panel with a different aspect ratio than the
    camera's. It does nothing in "contain", where the whole frame is kept.

    Colour photos stay plain RGB — the inky driver does its own dithering on
    show(), and pre-quantising here would fight it. "mono" is the exception: like
    xkcd's line art, a deliberate bilevel reduction reads better than letting a
    greyscale image dither into colour speckle.
    """
    photo = Image.open(path)
    photo = (ImageOps.exif_transpose(photo) or photo).convert("RGB")
    width, height = max(1, box[0]), max(1, box[1])
    if config.fit == "cover":
        photo = ImageOps.fit(photo, (width, height), Image.LANCZOS, centering=focus)
    else:
        photo = ImageOps.contain(photo, (width, height), Image.LANCZOS)

    # The panel's six colours flatten a photograph badly; a nudge of contrast and
    # saturation before quantisation buys back some of the separation.
    if config.contrast != 1.0:
        photo = ImageEnhance.Contrast(photo).enhance(config.contrast)
    if config.style == "mono":
        return photo.convert("L").convert("1").convert("RGB")
    if config.saturation_boost != 1.0:
        photo = ImageEnhance.Color(photo).enhance(config.saturation_boost)
    return photo


class PhotosModule:
    """One photo from the local pool, rotated on a fixed cadence."""

    id = "photos"
    name = "Photos"
    # Nothing is fetched over the network: fetch() is a cheap local index read
    # whose only job is to re-evaluate the rotation each cycle.
    ttl = 30
    config_key = "photos"
    fetch_key = None
    options = [
        opts.custom("photos_manager"),
        opts.row(
            opts.select(
                "mode",
                "Order",
                [
                    ("shuffle", "Shuffle"),
                    ("sequential", "In order"),
                    ("on_this_day", "On this day"),
                ],
            ),
            opts.number("rotate_minutes", "Change photo every (minutes)"),
        ),
        opts.row(
            opts.select(
                "fit", "Framing", [("cover", "Fill (crop)"), ("contain", "Fit (letterbox)")]
            ),
            opts.select("style", "Rendering", [("colour", "Colour"), ("mono", "Black & white")]),
        ),
        opts.note(
            "Filling crops each photo to the panel's shape. Click a photo above to see "
            "how it lands on the display and drag it into place; photos you leave alone "
            "are centred.",
            visible_if=opts.when("fit", "eq", "cover"),
        ),
        opts.row(
            opts.number("contrast", "Contrast", step=0.05),
            opts.number(
                "saturation_boost",
                "Saturation",
                step=0.05,
                visible_if=opts.when("style", "eq", "colour"),
            ),
        ),
        opts.checkbox("show_title", "Show panel heading"),
        opts.checkbox("show_caption", "Show date under the photo"),
        opts.row(
            opts.number("max_photos", "Keep at most (photos)"),
            opts.number("drive_sync_minutes", "Check Drive every (minutes)"),
        ),
        opts.note(
            "The display only redraws every {refresh_minutes} minutes, so a rotation "
            "shorter than that just waits for the next cycle.",
        ),
    ]

    async def fetch(self, config: AppConfig) -> dict[str, Any]:
        entries = photos_store.load_index()
        photo = pick_photo(entries, config.photos.mode, time.time(), config.photos.rotate_minutes)
        if photo is None:
            return {"pool": 0}
        return payload_for(photo, len(entries))

    def render(
        self, image: Image.Image, rect: Rect, data: dict[str, Any] | None, config: AppConfig
    ) -> None:
        photos = config.photos
        draw = panel_draw(image)

        if photos.show_title:
            x, y, w, h = title_bar(draw, rect, "Photos")
        else:
            # Full-bleed: no inset, so the photo reaches the region's edges.
            x, y, w, h = rect

        if not data or not data.get("path"):
            # The hint is text, so it wants the usual padding even when the photo
            # itself would have been full-bleed.
            hx, hy, hw, _ = (x, y, w, h) if photos.show_title else inset(rect)
            message = "No photos yet — add some in the config UI."
            for i, line in enumerate(wrap_text(draw, message, font(16), hw)):
                draw.text((hx, hy + i * 22), line, font=font(16), fill=palette.MUTED)
            return

        caption = _caption(data) if photos.show_caption else ""
        if caption:
            h -= CAPTION_HEIGHT
        if w <= 0 or h <= 0:
            return

        try:
            photo = prepare_image(data["path"], (w, h), photos, focus_of(data))
        except (OSError, ValueError):
            # A pooled file that has gone bad must not take the whole frame down.
            draw.text((x + PAD, y + PAD), "photo unreadable", font=font(16), fill=palette.MUTED)
            return
        image.paste(photo, (x + (w - photo.width) // 2, y + (h - photo.height) // 2))

        if caption:
            draw.text((x + PAD, y + h + 3), caption, font=font(14), fill=palette.MUTED)


def _caption(data: dict[str, Any]) -> str:
    taken = data.get("taken")
    if isinstance(taken, str):
        try:
            return datetime.fromisoformat(taken).strftime("%-d %B %Y")
        except ValueError:
            pass
    return str(data.get("filename") or "")
