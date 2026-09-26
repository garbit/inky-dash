from __future__ import annotations

from datetime import date
from typing import Any

import httpx
from PIL import Image, ImageDraw

import inky_dash.modules.options as opts
from inky_dash import palette
from inky_dash.config import AppConfig
from inky_dash.fonts import font
from inky_dash.layout import Rect
from inky_dash.modules.common import panel_draw, text_size, title_bar, wrap_text
from inky_dash.modules.weather_icons import draw_weather_icon

API_URL = "https://api.open-meteo.com/v1/forecast"

# Vertical budgets for the forecast strips, in pixels. Height is the scarce
# dimension — a strip that stacks label, icon and values costs ~75px, so both
# strips have flatter forms that trade the panel's spare width for height, and
# the renderer picks the richest form the space actually allows.
HOURLY_HEIGHT = 42  # hour beside its icon, temperature (and rain chance) beneath
STRIP_GAP = 8  # keeps the hourly columns from reading as part of the daily row
DAYS_STACKED_HEIGHT = 72  # weekday / icon / temperatures (rain chance cut)
# Stacked's rain-chance row needs icon size + this much: 22px weekday row, 8px
# gap, ~20px temperatures, 22px rain line and its text height.
STACKED_PRECIP_EXTRA = 74
DAYS_BARS_HEIGHT = 46  # weekday + icon over a low-high temperature range bar
DAYS_LINE_HEIGHT = 26  # weekday, icon and temperatures on one line
DAYS_PAIR_HEIGHT = 40  # weekday over its temperatures, for narrow columns
BAR_TRACK_MIN = 24  # a range bar shorter than this can't show where a day sits
BAR_H = 6  # thickness of the filled low-high segment
DAYS_TEXT_HEIGHT = 20  # weekday and temperatures on one line
DAYS_LINE_ICON = 22  # icon size in the one-line daily form
MIN_HOUR_COL = 52  # hour label beside a 22px icon, with a gutter before the next
ICON_RATIO = 1.1  # current-conditions icon size relative to the temperature font
HEADER_EXTRA = 46  # everything under that icon before the strips: gap + metadata line
MIN_BIG = 40  # floor the strips may shrink the current temperature to
MIN_SMALL = 10  # absolute floor for it on a panel with barely any room at all
MAX_BIG = 72

# Hours parsed ahead of now; the render slices this down to hourly_hours.
MAX_HOURS = 24

# The metadata line's numbers move at almost every fetch — humidity and wind
# especially, which drift a point or two every fifteen minutes. At full precision
# they change the frame ~88 times a day, and each change costs ~30s of flashing
# for a figure nobody reads to the unit. Rounding them to these steps keeps the
# line honest while letting the panel sit still between real changes.
HUMIDITY_STEP = 5  # percent
WIND_STEP = 5  # km/h


def to_step(value: float, step: int) -> int:
    return round(value / step) * step


# WMO weather interpretation codes
_CODES: dict[range | tuple, str] = {
    (0,): "Clear",
    (1, 2): "Partly cloudy",
    (3,): "Overcast",
    (45, 48): "Fog",
    (51, 53, 55, 56, 57): "Drizzle",
    (61, 63, 65, 66, 67): "Rain",
    (71, 73, 75, 77): "Snow",
    (80, 81, 82): "Showers",
    (85, 86): "Snow showers",
    (95, 96, 99): "Thunderstorm",
}


def describe_code(code: int) -> str:
    for codes, label in _CODES.items():
        if code in codes:
            return label
    return "—"


def parse_hours(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The hours still to come, starting with the one currently in progress.

    The cut-off comes from the payload's own ``current.time`` rather than the
    local clock: it is local to the forecast location (``timezone=auto``), which
    a Pi in another timezone — or with a skewed clock — would not agree with.
    """
    hourly = payload.get("hourly")
    if not hourly:
        return []
    times: list[str] = hourly["time"]
    now = payload.get("current", {}).get("time")
    # The entry before the first future one is the hour we are part-way through.
    start = max(0, next((i for i, t in enumerate(times) if t > now), len(times)) - 1) if now else 0
    return [
        {
            "time": times[i],
            "hour": times[i][11:13],
            "code": hourly["weather_code"][i],
            "temp": hourly["temperature_2m"][i],
            "precip_pct": hourly["precipitation_probability"][i],
        }
        for i in range(start, min(start + MAX_HOURS, len(times)))
    ]


def parse_forecast(payload: dict[str, Any]) -> dict[str, Any]:
    current = payload["current"]
    daily = payload["daily"]
    days = [
        {
            "date": daily["time"][i],
            "weekday": date.fromisoformat(daily["time"][i]).strftime("%a"),
            "code": daily["weather_code"][i],
            "max": daily["temperature_2m_max"][i],
            "min": daily["temperature_2m_min"][i],
            "precip_pct": daily["precipitation_probability_max"][i],
        }
        for i in range(len(daily["time"]))
    ]
    return {
        "temp": current["temperature_2m"],
        "feels_like": current["apparent_temperature"],
        "humidity": current["relative_humidity_2m"],
        "wind_kmh": current["wind_speed_10m"],
        "code": current["weather_code"],
        "days": days,
        "hours": parse_hours(payload),
    }


class WeatherModule:
    id = "weather"
    name = "Weather"
    ttl = 900
    config_key = "weather"
    fetch_key = "weather_minutes"
    options = [
        opts.custom("location", label="Location"),
        opts.row(
            opts.number("latitude", "Latitude", step=0.0001),
            opts.number("longitude", "Longitude", step=0.0001),
        ),
        opts.number("forecast_days", "Forecast days"),
        opts.checkbox("show_hourly", "Show hourly forecast for today"),
        opts.number("hourly_hours", "Hours ahead", visible_if=opts.when("show_hourly")),
        opts.note(
            "Drawn between the current conditions and the daily forecast, starting with "
            "the hour in progress. Times are local to the weather location. A smaller "
            "panel fits fewer columns than asked for, and flattens the daily forecast to "
            "make room.",
            visible_if=opts.when("show_hourly"),
        ),
    ]

    async def fetch(self, config: AppConfig) -> dict[str, Any]:
        params = {
            "latitude": config.weather.latitude,
            "longitude": config.weather.longitude,
            "current": (
                "temperature_2m,apparent_temperature,relative_humidity_2m,"
                "weather_code,wind_speed_10m"
            ),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            ),
            "hourly": "weather_code,temperature_2m,precipitation_probability",
            "forecast_days": config.weather.forecast_days,
            "timezone": "auto",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(API_URL, params=params)
            response.raise_for_status()
        return parse_forecast(response.json())

    def render(
        self, image: Image.Image, rect: Rect, data: dict[str, Any] | None, config: AppConfig
    ) -> None:
        draw = panel_draw(image)
        x, y, w, h = title_bar(draw, rect, f"Weather · {config.weather.location_name}")
        if not data:
            draw.text((x, y), "waiting for data…", font=font(16), fill=palette.MUTED)
            return
        if h < MIN_SMALL * 3:  # a 2-cell region is all title bar; nothing fits under it
            return

        # Lay the strips out first: they have fixed heights, the current-conditions
        # block is the elastic part, and the whole panel is usually height-bound.
        hours = data.get("hours") or []
        show_hourly = config.weather.show_hourly and bool(hours) and w >= 2 * MIN_HOUR_COL
        # The smallest the current-conditions block can get, so the strips are
        # budgeted against the space they can actually claim from it.
        min_header = round(min(MIN_BIG, h // 3) * ICON_RATIO) + HEADER_EXTRA
        if show_hourly and min_header + HOURLY_HEIGHT + STRIP_GAP > h:
            show_hourly = False  # no room for it at all; don't shrink the header for it
        hourly_h = HOURLY_HEIGHT + STRIP_GAP if show_hourly else 0
        days_form, days_h = self._days_form(draw, data["days"], w, h - min_header - hourly_h)

        room = h - (hourly_h + days_h + HEADER_EXTRA)
        big_size = max(MIN_SMALL, min(MAX_BIG, h // 3, max(MIN_BIG, round(room / ICON_RATIO))))
        big = font(big_size, bold=True)
        icon_s = round(big_size * ICON_RATIO)
        draw_weather_icon(draw, data["code"], x, y, icon_s)
        temp_x = x + icon_s + 16
        temp_text = f"{round(data['temp'])}°"
        draw.text((temp_x, y), temp_text, font=big, fill=palette.BLACK)
        temp_bbox = draw.textbbox((temp_x, y), temp_text, font=big)
        tw = temp_bbox[2] - x

        desc_font = font(22, bold=True)
        desc_lines = wrap_text(draw, describe_code(data["code"]), desc_font, w - tw - 20)
        for i, line in enumerate(desc_lines[:2]):
            draw.text((x + tw + 20, y + 8 + i * 28), line, font=desc_font, fill=palette.BLUE)

        meta_y = max(temp_bbox[3], y + icon_s) + 10
        meta = (
            f"Feels {round(data['feels_like'])}°  ·  "
            f"{to_step(data['humidity'], HUMIDITY_STEP)}%  ·  "
            f"{to_step(data['wind_kmh'], WIND_STEP)} km/h"
        )
        draw.text((x, meta_y), meta, font=font(17), fill=palette.MUTED)

        # Re-budget against the header's actual height — it can outgrow the estimate
        # when the temperature hits its MIN_BIG floor on a very short panel.
        top = meta_y + 34
        avail = y + h - top
        if hourly_h > avail:
            show_hourly, hourly_h = False, 0
        if hourly_h + days_h > avail:
            days_form, days_h = self._days_form(draw, data["days"], w, avail - hourly_h)
        # Centre what's left: on a tall panel the temperature caps out at MAX_BIG,
        # and splitting the slack reads better than one gap above or below.
        strips_y = top + (avail - hourly_h - days_h) // 2
        if show_hourly:
            self._render_hours(
                draw, hours[: config.weather.hourly_hours], (x, strips_y, w, HOURLY_HEIGHT)
            )
        if days_h:
            self._render_days(draw, data["days"], (x, strips_y + hourly_h, w, days_h), days_form)

    def _render_hours(self, draw: ImageDraw.ImageDraw, hours: list[dict], area: Rect) -> None:
        """Upcoming hours, two lines per column: hour beside its icon, values below."""
        x, y, w, _ = area
        cols = min(len(hours), max(1, w // MIN_HOUR_COL))
        if cols < 2:
            return
        hours = hours[:cols]
        col_w = w // cols
        icon_s = max(16, min(22, col_w // 2))
        hour_font = font(15, bold=True)
        val_font = font(15)
        temps = [f"{round(hour['temp'])}°" for hour in hours]
        precips = [
            f"{hour['precip_pct']}%" if hour.get("precip_pct") is not None else "" for hour in hours
        ]
        temp_w = max(text_size(draw, t, val_font)[0] for t in temps)
        # The rain chance sits beside the temperature; in a tight column it drops
        # to a smaller size rather than disappearing — losing the number the user
        # asked the strip for is worse than a size mismatch.
        precip_font = val_font
        precip_w = max(text_size(draw, p, precip_font)[0] for p in precips)
        gap = 8
        if temp_w + gap + precip_w > col_w - 6:
            precip_font = font(13)
            precip_w = max(text_size(draw, p, precip_font)[0] for p in precips)
            gap = 4
        with_precip = temp_w + gap + precip_w <= col_w - 6

        label_h = text_size(draw, "00", hour_font)[1]
        values_y = y + max(icon_s, label_h) + 4
        for i, hour in enumerate(hours):
            cx = x + i * col_w
            draw.text(
                (cx, y + (icon_s - label_h) // 2), hour["hour"], font=hour_font, fill=palette.MUTED
            )
            icon_x = cx + text_size(draw, hour["hour"], hour_font)[0] + 5
            draw_weather_icon(draw, hour["code"], icon_x, y, icon_s)
            draw.text((cx, values_y), temps[i], font=val_font, fill=palette.BLACK)
            if with_precip and precips[i]:
                draw.text(
                    (cx + temp_w + gap, values_y), precips[i], font=precip_font, fill=palette.BLUE
                )

    def _days_form(
        self, draw: ImageDraw.ImageDraw, days: list[dict], w: int, avail: int
    ) -> tuple[str, int]:
        """The richest daily form that fits the space, as (form, height).

        Richest first, and each form needs both its height and its column width —
        a wide panel flattens a day onto one line, a narrow one stacks the weekday
        over its temperatures instead. Widths are measured rather than assumed: the
        same point size renders wider on the Pi (DejaVu) than on macOS (Arial), and
        a column that overflows on the panel is worse than one that dropped a detail.
        """
        if not days:
            return ("none", 0)
        col_w = w // len(days)
        label_font, val_font = font(15, bold=True), font(15)
        label_w = max(text_size(draw, day["weekday"], label_font)[0] for day in days)
        stacked_w = max(
            text_size(draw, f"{round(day['max'])}° / {round(day['min'])}°", font(16))[0]
            for day in days
        )
        temps_w = max(
            text_size(draw, f"{round(day['max'])}°/{round(day['min'])}°", val_font)[0]
            for day in days
        )
        low_w = max(text_size(draw, f"{round(day['min'])}°", val_font)[0] for day in days)
        high_w = max(text_size(draw, f"{round(day['max'])}°", val_font)[0] for day in days)
        # Stacked prefers to also claim its rain-chance row: full-size icon if the
        # space allows, a smaller icon if that's what buys the row, plain 72px only
        # when the rain row can't fit at all.
        icon_est = max(20, min(32, col_w - 12))
        if avail >= icon_est + STACKED_PRECIP_EXTRA:
            stacked_h = icon_est + STACKED_PRECIP_EXTRA
        elif avail >= 20 + STACKED_PRECIP_EXTRA:
            stacked_h = avail
        else:
            stacked_h = DAYS_STACKED_HEIGHT
        forms = (
            ("stacked", stacked_h, stacked_w + 8),
            # low° [====] high° on a shared scale — reads faster than two numbers
            (
                "bars",
                DAYS_BARS_HEIGHT,
                max(label_w + 5 + DAYS_LINE_ICON, low_w + BAR_TRACK_MIN + high_w) + 18,
            ),
            ("line", DAYS_LINE_HEIGHT, label_w + DAYS_LINE_ICON + temps_w + 20),
            ("pair", DAYS_PAIR_HEIGHT, max(label_w, temps_w) + 8),
            ("text", DAYS_TEXT_HEIGHT, label_w + temps_w + 14),
        )
        for form, height, needed in forms:
            if height <= avail and needed <= col_w:
                return (form, height)
        return ("none", 0)

    def _render_days(
        self, draw: ImageDraw.ImageDraw, days: list[dict], area: Rect, form: str
    ) -> None:
        x, y, w, h = area
        col_w = w // len(days)
        if form == "stacked":
            self._render_days_stacked(draw, days, area, col_w)
            return
        if form == "bars":
            self._render_days_bars(draw, days, area, col_w)
            return
        label_font, val_font = font(15, bold=True), font(15)
        label_h = text_size(draw, "Mon", label_font)[1]

        # One rain-chance font for the whole row, chosen so even the widest day
        # fits: a single bare column reads as a glitch, so it's all one size, a
        # smaller size, or (only then) none at all.
        def precip_slack(day: dict) -> int:
            used = text_size(draw, f"{round(day['max'])}°/{round(day['min'])}°", val_font)[0]
            if form != "pair":
                used += text_size(draw, day["weekday"], label_font)[0] + 6
                if form == "line":
                    used += DAYS_LINE_ICON + 6
            return col_w - 6 - used - 7

        precip_font = next(
            (
                candidate
                for candidate in (val_font, font(13))
                if all(
                    text_size(draw, f"{day['precip_pct']}%", candidate)[0] <= precip_slack(day)
                    for day in days
                    if day.get("precip_pct") is not None
                )
            ),
            None,
        )

        def precip_after(px: int, py: int, day: dict) -> None:
            if precip_font is not None and day.get("precip_pct") is not None:
                draw.text((px, py), f"{day['precip_pct']}%", font=precip_font, fill=palette.BLUE)

        for i, day in enumerate(days):
            cx = x + i * col_w
            temps = f"{round(day['max'])}°/{round(day['min'])}°"
            if form == "pair":  # weekday over its temperatures, no room for an icon
                draw.text((cx, y), day["weekday"], font=label_font, fill=palette.BLACK)
                draw.text((cx, y + 19), temps, font=val_font, fill=palette.BLACK)
                precip_after(cx + text_size(draw, temps, val_font)[0] + 7, y + 19, day)
                continue
            # One line per day, with the icon between the weekday and its
            # temperatures when the column is wide enough to hold it.
            text_y = y + (DAYS_LINE_ICON - label_h) // 2 if form == "line" else y
            draw.text((cx, text_y), day["weekday"], font=label_font, fill=palette.BLACK)
            vx = cx + text_size(draw, day["weekday"], label_font)[0] + 6
            if form == "line":
                draw_weather_icon(draw, day["code"], vx, y, DAYS_LINE_ICON)
                vx += DAYS_LINE_ICON + 6
            draw.text((vx, text_y), temps, font=val_font, fill=palette.BLACK)
            precip_after(vx + text_size(draw, temps, val_font)[0] + 7, text_y, day)

    def _render_days_bars(
        self, draw: ImageDraw.ImageDraw, days: list[dict], area: Rect, col_w: int
    ) -> None:
        """Weekday + icon over a low-high range bar (low° left, high° right).

        Every column shares one temperature scale — the week's overall min/max —
        so the bars' positions compare across days, which is the whole point of
        the form (Apple/Dark Sky/Carrot all converge on it).
        """
        x, y, _, _ = area
        week_lo = min(day["min"] for day in days)
        week_hi = max(day["max"] for day in days)
        span = max(week_hi - week_lo, 1)
        label_font, val_font = font(15, bold=True), font(15)
        label_h = text_size(draw, "Mon", label_font)[1]
        # Shared text-slot widths keep every column's track identical, else the
        # bars sit on different scales and can't be compared.
        low_w = max(text_size(draw, f"{round(day['min'])}°", val_font)[0] for day in days)
        high_w = max(text_size(draw, f"{round(day['max'])}°", val_font)[0] for day in days)
        row2_y = y + DAYS_LINE_ICON + 6
        val_h = text_size(draw, "0°", val_font)[1]
        bar_mid = row2_y + val_h // 2 + 1
        # Rain chance rides the first row after the icon — one font for the whole
        # row, sized down (or dropped) only if the widest day demands it.
        precip_font = next(
            (
                candidate
                for candidate in (val_font, font(13))
                if all(
                    text_size(draw, day["weekday"], label_font)[0]
                    + 5
                    + DAYS_LINE_ICON
                    + 6
                    + text_size(draw, f"{day['precip_pct']}%", candidate)[0]
                    <= col_w - 8
                    for day in days
                    if day.get("precip_pct") is not None
                )
            ),
            None,
        )
        for i, day in enumerate(days):
            cx = x + i * col_w
            draw.text(
                (cx, y + (DAYS_LINE_ICON - label_h) // 2),
                day["weekday"],
                font=label_font,
                fill=palette.BLACK,
            )
            icon_x = cx + text_size(draw, day["weekday"], label_font)[0] + 5
            draw_weather_icon(draw, day["code"], icon_x, y, DAYS_LINE_ICON)
            if precip_font is not None and day.get("precip_pct") is not None:
                draw.text(
                    (icon_x + DAYS_LINE_ICON + 6, y + (DAYS_LINE_ICON - label_h) // 2),
                    f"{day['precip_pct']}%",
                    font=precip_font,
                    fill=palette.BLUE,
                )

            low_s, high_s = f"{round(day['min'])}°", f"{round(day['max'])}°"
            draw.text((cx, row2_y), low_s, font=val_font, fill=palette.MUTED)
            track_x0 = cx + low_w + 5
            track_x1 = cx + col_w - high_w - 13
            draw.text((track_x1 + 5, row2_y), high_s, font=val_font, fill=palette.BLACK)
            draw.line([(track_x0, bar_mid), (track_x1, bar_mid)], fill=palette.MUTED, width=1)
            track = track_x1 - track_x0
            seg_x0 = track_x0 + round((day["min"] - week_lo) / span * track)
            seg_x1 = track_x0 + round((day["max"] - week_lo) / span * track)
            seg_x1 = max(seg_x1, seg_x0 + 4)  # a flat day still shows a nub
            draw.rectangle(
                [seg_x0, bar_mid - BAR_H // 2, seg_x1, bar_mid + BAR_H // 2],
                fill=palette.YELLOW,
                outline=palette.BLACK,
            )

    def _render_days_stacked(
        self, draw: ImageDraw.ImageDraw, days: list[dict], area: Rect, col_w: int
    ) -> None:
        x, y, _, h = area
        icon_s = max(20, min(32, col_w - 12))
        if h >= 20 + STACKED_PRECIP_EXTRA:
            # Shrink the icon if that's what fits the rain-chance row in.
            icon_s = max(20, min(icon_s, h - STACKED_PRECIP_EXTRA))
        day_font = font(17, bold=True)
        val_font = font(16)
        for i, day in enumerate(days):
            cx = x + i * col_w
            draw.text((cx, y), day["weekday"], font=day_font, fill=palette.BLACK)
            draw_weather_icon(draw, day["code"], cx, y + 22, icon_s)
            temps_y = y + 22 + icon_s + 8
            draw.text(
                (cx, temps_y),
                f"{round(day['max'])}° / {round(day['min'])}°",
                font=val_font,
                fill=palette.BLACK,
            )
            precip = day.get("precip_pct")
            if precip is not None and temps_y + 44 <= y + h:
                draw.text((cx, temps_y + 22), f"{precip}%", font=val_font, fill=palette.BLUE)
