from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from PIL import Image, ImageDraw

import inky_dash.modules.options as opts
from inky_dash import palette
from inky_dash.config import AppConfig
from inky_dash.fonts import font
from inky_dash.layout import Rect
from inky_dash.modules.common import panel_draw, text_size, title_bar
from inky_dash.storage import Reading, SensorStore


def averaged_view(
    readings: list[Reading],
    latest: Reading | None,
    minutes: int,
    trend_hours: int,
    now_ts: float,
) -> dict[str, Any]:
    """The panel's data as averages over completed clock-aligned N-minute windows.

    Everything is derived from the last *completed* window boundary, never from
    ``now`` itself, so the returned payload is byte-identical between boundaries —
    which is what lets the render loop skip the ~30s e-ink refresh. The current
    partial window is deliberately excluded: its mean shifts with every reading.
    """
    step = minutes * 60
    end = now_ts - now_ts % step
    start = end - trend_hours * 3600
    samples = [(r.ts.timestamp(), r) for r in readings if start <= r.ts.timestamp() < end]

    buckets: dict[float, list[Reading]] = {}
    for ts, reading in samples:
        buckets.setdefault(ts - ts % step, []).append(reading)
    trend = [
        (key + step / 2, sum(r.temp_c for r in group) / len(group))
        for key, group in sorted(buckets.items())
    ]

    window = buckets.get(end - step)
    if window:
        shown = {
            "temp_c": sum(r.temp_c for r in window) / len(window),
            "humidity": sum(r.humidity for r in window) / len(window),
            "battery": window[-1].battery,
            "ts": datetime.fromtimestamp(end, tz=UTC).isoformat(),
        }
    elif latest:  # no completed window yet (fresh sensor): fall back to live
        shown = {
            "temp_c": latest.temp_c,
            "humidity": latest.humidity,
            "battery": latest.battery,
            "ts": latest.ts.isoformat(),
        }
    else:
        shown = None
    return {"latest": shown, "trend": trend}


class GoveeModule:
    """Renders the latest logged H5075 reading plus a temperature trend sparkline.

    Data acquisition happens in the background GoveeListener; fetch only reads the DB.
    """

    id = "govee"
    name = "Govee"
    ttl = 120
    config_key = "govee"
    fetch_key = None  # reads the local sensor log, nothing upstream to fetch
    options = [
        opts.text(
            "mac",
            "MAC / address filter (blank = any H5075)",
            placeholder="e.g. A4:C1:38:AA:BB:CC",
            nullable=True,
        ),
        opts.number("trend_hours", "Trend window (hours)"),
        opts.select(
            "average_minutes",
            "Reading smoothing",
            [
                (0, "Live (latest reading)"),
                (15, "15-minute average"),
                (30, "30-minute average"),
                (60, "Hourly average"),
            ],
        ),
        opts.note(
            "Shows the average over the last completed {average_minutes}-minute window, "
            "so this panel only changes — and only triggers an e-ink refresh — when a "
            "window completes.",
            visible_if=opts.when("average_minutes", "neq", 0),
        ),
        opts.note(
            "Live readings change nearly every cycle, so this panel triggers an e-ink "
            "refresh almost every {refresh_minutes} minutes.",
            visible_if=opts.when("average_minutes", "eq", 0),
        ),
    ]

    async def fetch(self, config: AppConfig) -> dict[str, Any]:
        store = SensorStore()
        latest = store.latest()
        minutes = config.govee.average_minutes
        if minutes:
            # Over-fetch by a window so the query's sliding start can't shave
            # readings off the oldest bucket between renders.
            history = store.history(config.govee.trend_hours + math.ceil(minutes / 60))
            return averaged_view(
                history,
                latest,
                minutes,
                config.govee.trend_hours,
                datetime.now(UTC).timestamp(),
            )
        history = store.history(config.govee.trend_hours)
        return {
            "latest": (
                {
                    "temp_c": latest.temp_c,
                    "humidity": latest.humidity,
                    "battery": latest.battery,
                    "ts": latest.ts.isoformat(),
                }
                if latest
                else None
            ),
            "trend": [(r.ts.timestamp(), r.temp_c) for r in history],
        }

    def render(
        self, image: Image.Image, rect: Rect, data: dict[str, Any] | None, config: AppConfig
    ) -> None:
        draw = panel_draw(image)
        x, y, w, h = title_bar(draw, rect, "Indoor")
        latest = (data or {}).get("latest")
        if not latest:
            draw.text((x, y), "listening for sensor…", font=font(16), fill=palette.MUTED)
            return

        big = font(min(40, h // 3), bold=True)
        temp_text = f"{latest['temp_c']:.1f}°"
        tw, th = text_size(draw, temp_text, big)
        draw.text((x, y), temp_text, font=big, fill=palette.BLACK)
        hum_font = font(min(26, h // 4), bold=True)
        draw.text(
            (x + tw + 14, y + th - 26),
            f"{latest['humidity']:.0f}%",
            font=hum_font,
            fill=palette.BLUE,
        )
        if latest.get("battery") is not None:
            batt = f"{latest['battery']}%"
            bw, _ = text_size(draw, batt, font(13))
            draw.text((x + w - bw, y), batt, font=font(13), fill=palette.MUTED)

        trend = (data or {}).get("trend") or []
        spark_top = y + th + 12
        spark_h = y + h - spark_top
        if len(trend) >= 2 and spark_h >= 30:
            self._sparkline(draw, trend, (x, spark_top, w, min(spark_h, 60)))

    def _sparkline(self, draw: ImageDraw.ImageDraw, trend: list, area: Rect) -> None:
        x, y, w, h = area
        temps = [t for _, t in trend]
        lo, hi = min(temps), max(temps)
        span = (hi - lo) or 1.0
        t0, t1 = trend[0][0], trend[-1][0]
        t_span = (t1 - t0) or 1.0
        points = [
            (x + (ts - t0) / t_span * w, y + (1 - (temp - lo) / span) * (h - 14) + 2)
            for ts, temp in trend
        ]
        draw.line(points, fill=palette.ACCENT, width=2)
        label_font = font(12)
        draw.text((x, y + h - 12), f"{lo:.1f}°", font=label_font, fill=palette.MUTED)
        hi_text = f"{hi:.1f}°"
        hw, _ = text_size(draw, hi_text, label_font)
        draw.text((x + w - hw, y + h - 12), hi_text, font=label_font, fill=palette.MUTED)
