"""Date-first clock faces for the clock module.

The panel refreshes only every ~10 minutes, so a live time would always be stale.
Each face shows the date prominently; time is at most a small MUTED reference drawn
when ``ClockConfig.show_time`` is set. Modelled on ``weather_icons.py``: a dict
registry of uniform-signature drawing functions, sizes as fractions of the rect.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PIL import ImageDraw

from inky_dash import palette
from inky_dash.config import ClockConfig
from inky_dash.layout import Rect
from inky_dash.modules.common import draw_text_centered, fit_font

FaceFn = Callable[[ImageDraw.ImageDraw, Rect, datetime, ClockConfig], None]


def format_time(now: datetime, twenty_four_hour: bool) -> str:
    """Format ``now`` as a clock string.

    24h uses ``%H:%M``. 12h strips the leading zero from the hour and derives the
    am/pm suffix from ``now.hour`` rather than ``%p`` (which is locale-dependent and
    can be an empty string).
    """
    if twenty_four_hour:
        return now.strftime("%H:%M")
    hour = now.hour % 12 or 12
    suffix = "am" if now.hour < 12 else "pm"
    return f"{hour}:{now.strftime('%M')} {suffix}"


def face_calendar(draw: ImageDraw.ImageDraw, rect: Rect, now: datetime, cfg: ClockConfig) -> None:
    """Tear-off calendar block: accent month banner, giant day number, weekday."""
    x, y, w, h = rect
    card_w = int(min(w, h * 0.9))
    card_h = h
    cx = x + w // 2
    left = cx - card_w // 2
    top = y
    border = max(2, h // 120)
    draw.rectangle(
        [left, top, left + card_w - 1, top + card_h - 1], outline=palette.BLACK, width=border
    )

    band_h = int(card_h * 0.22)
    draw.rectangle([left, top, left + card_w - 1, top + band_h - 1], fill=palette.ACCENT)
    month = now.strftime("%B").upper()
    month_font = fit_font(draw, month, int(card_w * 0.86), int(band_h * 0.62), bold=True)
    draw_text_centered(draw, cx, top + band_h // 2, month, month_font, palette.WHITE)

    time_text = format_time(now, cfg.twenty_four_hour) if cfg.show_time else ""
    time_band = int(card_h * 0.16) if cfg.show_time else 0

    weekday = now.strftime("%A")
    weekday_band = int(card_h * 0.18)
    day_top = top + band_h
    day_bottom = top + card_h - weekday_band - time_band
    day_text = str(now.day)
    day_font = fit_font(
        draw, day_text, int(card_w * 0.8), int((day_bottom - day_top) * 0.9), bold=True
    )
    draw_text_centered(draw, cx, (day_top + day_bottom) // 2, day_text, day_font, palette.BLACK)

    weekday_font = fit_font(draw, weekday, int(card_w * 0.86), int(weekday_band * 0.7))
    draw_text_centered(
        draw, cx, day_bottom + weekday_band // 2, weekday, weekday_font, palette.BLACK
    )

    if cfg.show_time:
        time_font = fit_font(draw, time_text, int(card_w * 0.7), int(time_band * 0.6))
        draw_text_centered(
            draw, cx, top + card_h - time_band // 2, time_text, time_font, palette.MUTED
        )


def face_big(draw: ImageDraw.ImageDraw, rect: Rect, now: datetime, cfg: ClockConfig) -> None:
    """Typographic date: huge bold weekday, accent rule, day + month below."""
    x, y, w, h = rect
    cx = x + w // 2

    weekday = now.strftime("%A")
    date_text = f"{now.day} {now.strftime('%B')}"
    weekday_font = fit_font(draw, weekday, w, int(h * 0.45), bold=True)
    date_font = fit_font(draw, date_text, w, int(h * 0.25))

    ww, wh = draw.textbbox((0, 0), weekday, font=weekday_font)[2:]
    dw, dh = draw.textbbox((0, 0), date_text, font=date_font)[2:]
    rule_h = max(3, h // 60)
    gap = int(h * 0.06)
    block_h = wh + gap + rule_h + gap + dh
    top = y + (h - block_h) // 2

    draw_text_centered(draw, cx, top + wh // 2, weekday, weekday_font, palette.BLACK)
    rule_y = top + wh + gap + rule_h // 2
    rule_w = int(ww * 0.6)
    draw.line(
        [(cx - rule_w // 2, rule_y), (cx + rule_w // 2, rule_y)],
        fill=palette.ACCENT,
        width=rule_h,
    )
    date_cy = top + wh + gap + rule_h + gap + dh // 2
    draw_text_centered(draw, cx, date_cy, date_text, date_font, palette.ACCENT)

    if cfg.show_time:
        time_text = format_time(now, cfg.twenty_four_hour)
        foot_top = top + block_h
        foot_h = y + h - foot_top
        if foot_h > 12:
            time_font = fit_font(draw, time_text, int(w * 0.6), int(foot_h * 0.7))
            draw_text_centered(
                draw, cx, foot_top + foot_h // 2, time_text, time_font, palette.MUTED
            )


FACES: dict[str, FaceFn] = {
    "calendar": face_calendar,
    "big": face_big,
}

# Labels for the config UI's face picker (served via /api/meta).
FACE_LABELS: dict[str, str] = {
    "calendar": "Calendar block",
    "big": "Big date",
}
