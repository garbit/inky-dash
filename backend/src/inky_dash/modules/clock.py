from __future__ import annotations

from datetime import datetime
from typing import Any

from PIL import Image

import inky_dash.modules.options as opts
from inky_dash import palette
from inky_dash.config import AppConfig, ClockConfig
from inky_dash.fonts import font
from inky_dash.layout import Rect
from inky_dash.modules.clock_faces import FACE_LABELS, FACES, face_calendar, format_time
from inky_dash.modules.common import inset, panel_draw, text_size

WIDE_GAP = 16  # minimum space between the date and the time in the slim strip


def strip_fonts(draw, date_text: str, time_text: str, w: int, h: int):
    """Largest date/time font pair that fits side by side in a slim strip.

    Sizing from the strip height alone overflows once a region is tall as well as
    wide — which custom layouts allow — so shrink until the pair fits. ``time_text``
    may be empty (date-only), in which case only the date has to fit.
    """
    size = h
    while True:
        date_font = font(size, bold=True)
        time_font = font(max(12, round(size * 0.6)))
        dw, dh = text_size(draw, date_text, date_font)
        tw, th = text_size(draw, time_text, time_font) if time_text else (0, 0)
        gap = WIDE_GAP if time_text else 0
        if dw + gap + tw <= w or size <= 12:
            return date_font, time_font, (dw, dh), (tw, th)
        size = max(12, round(size * 0.85))


def _render_strip(draw, rect: Rect, now: datetime, cfg: ClockConfig) -> None:
    """Slim header strip: weekday + date big on the left, optional MUTED time right."""
    x, y, w, h = rect
    date_text = now.strftime("%A %d %B")
    time_text = format_time(now, cfg.twenty_four_hour) if cfg.show_time else ""
    date_font, time_font, (_, dh), (tw, th) = strip_fonts(draw, date_text, time_text, w, h)
    draw.text((x, y + (h - dh) // 2), date_text, font=date_font, fill=palette.BLACK)
    if time_text:
        draw.text((x + w - tw, y + (h - th) // 2), time_text, font=time_font, fill=palette.MUTED)


class ClockModule:
    id = "clock"
    name = "Clock"
    ttl = 0  # always "fresh"; time comes from render, not fetch
    config_key = "clock"
    fetch_key = None
    options = [
        opts.select("face", "Face", [(face, FACE_LABELS[face]) for face in FACES]),
        opts.checkbox("show_time", "Show time as well as the date"),
        opts.checkbox("twenty_four_hour", "24-hour time", visible_if=opts.when("show_time")),
        opts.note(
            "The display refreshes every {refresh_minutes} minutes, so any time shown "
            "is approximate."
        ),
    ]

    async def fetch(self, config: AppConfig) -> dict[str, Any]:
        return {}

    def render(
        self, image: Image.Image, rect: Rect, data: dict[str, Any] | None, config: AppConfig
    ) -> None:
        draw = panel_draw(image)
        content = inset(rect)
        now = datetime.now()
        _, _, w, h = content

        if w >= 4 * h or h < 96:
            # Slim strip (e.g. the dashboard_clock header): date-first, face-independent.
            _render_strip(draw, content, now, config.clock)
            return

        # Unknown face value must never crash the render loop — fall back to calendar.
        FACES.get(config.clock.face, face_calendar)(draw, content, now, config.clock)
