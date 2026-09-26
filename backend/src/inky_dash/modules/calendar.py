from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

import inky_dash.modules.options as opts
from inky_dash import google, palette
from inky_dash.config import AppConfig
from inky_dash.fonts import font
from inky_dash.layout import Rect
from inky_dash.modules.common import panel_draw, text_size, title_bar, wrap_text

SCOPES = google.SCOPES[google.CALENDAR]

log = logging.getLogger(__name__)


def token_path() -> Path:
    return google.token_path(google.CALENDAR)


def credentials_path() -> Path:
    return google.credentials_path()


# Emoji and pictographs — DejaVu on the Pi has no glyphs for these, so they
# would draw as tofu boxes on the panel.
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF\u2300-\u27BF\u2B00-\u2BFF\uFE0F\u200D]+")


def clean_summary(text: str) -> str:
    return _EMOJI.sub("", text).strip() or "(untitled)"


def calendar_display_name(name: str) -> str:
    """Shared personal calendars are often named by bare email address —
    'jane.doe42@gmail.com' should label events as just 'Jane'."""
    if "@" not in name:
        return name
    local = name.split("@", 1)[0]
    first = re.match(r"[A-Za-z]+", local)
    return first.group(0).capitalize() if first else local


def _service():
    from googleapiclient.discovery import build

    creds = google.credentials(google.CALENDAR)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _display_name(cal: dict[str, Any]) -> str:
    if cal.get("primary"):
        return "Personal"
    return calendar_display_name(cal.get("summaryOverride") or cal.get("summary") or cal["id"])


def list_calendars() -> list[dict[str, Any]]:
    """Every calendar visible to the account, for the config UI's picker."""
    items = _service().calendarList().list().execute().get("items", [])
    return [
        {
            "id": c["id"],
            "name": _display_name(c),
            "selected": bool(c.get("selected")),
            "primary": bool(c.get("primary")),
        }
        for c in items
    ]


def _start_dt(event: dict[str, Any], local_tz: tzinfo) -> datetime:
    start = event.get("start", {})
    if "dateTime" in start:
        return datetime.fromisoformat(start["dateTime"]).astimezone(local_tz)
    day = date.fromisoformat(start.get("date", "1970-01-01"))
    return datetime.combine(day, time.min, tzinfo=local_tz)


def merge_events(
    per_calendar: list[list[dict[str, Any]]], local_tz: tzinfo
) -> list[dict[str, Any]]:
    """Merge event lists from several calendars into one start-ordered list.

    The same event can appear in multiple calendars (invitations, shared
    calendars); iCalUID is stable across calendars, so use it to dedupe.
    """
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for items in per_calendar:
        for event in items:
            uid = event.get("iCalUID") or event.get("id")
            if uid:
                if uid in seen:
                    continue
                seen.add(uid)
            merged.append(event)
    merged.sort(key=lambda e: _start_dt(e, local_tz))
    return merged


def bucket_events(
    items: list[dict[str, Any]], today: date, local_tz: tzinfo, max_events: int
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split API events into (today, upcoming-week), capped at max_events each.

    Multi-day events already underway count as today.
    """
    todays: list[dict[str, str]] = []
    upcoming: list[dict[str, str]] = []
    for event in items:
        start_raw = event.get("start", {})
        if "dateTime" in start_raw:
            begins = datetime.fromisoformat(start_raw["dateTime"]).astimezone(local_tz)
            event_date, when = begins.date(), begins.strftime("%H:%M")
        else:
            event_date = date.fromisoformat(start_raw.get("date", today.isoformat()))
            when = "all day"
        entry = {
            "when": when,
            "summary": clean_summary(event.get("summary", "")),
            "calendar": event.get("_cal_name", ""),
        }
        if event_date <= today:
            if len(todays) < max_events:
                todays.append(entry)
        elif len(upcoming) < max_events:
            upcoming.append({**entry, "day": event_date.strftime("%a %d")})
    return todays, upcoming


class CalendarModule:
    """Today's Google Calendar events.

    Requires one-time OAuth setup (see google_auth.py); until then it reports
    configured=False and the panel renders a setup hint instead of failing.
    """

    id = "calendar"
    name = "Calendar"
    ttl = 900
    config_key = "calendar"
    fetch_key = "calendar_minutes"
    options = [
        opts.custom("calendar_picker"),
        opts.number("max_events", "Max events"),
    ]

    async def fetch(self, config: AppConfig) -> dict[str, Any]:
        if not token_path().exists():
            return {"configured": False}
        return await asyncio.to_thread(self._fetch_sync, config)

    def _fetch_sync(self, config: AppConfig) -> dict[str, Any]:
        local_tz = datetime.now().astimezone().tzinfo
        today = datetime.now(local_tz).date()
        start = datetime.combine(today, time.min, tzinfo=local_tz)
        end = start + timedelta(days=8)
        service = _service()

        cal_list = service.calendarList().list().execute().get("items", [])
        name_by_id = {c["id"]: _display_name(c) for c in cal_list}
        cal_ids = config.calendar.calendar_ids
        if not cal_ids:
            # 'selected' mirrors the checkboxes in the Google Calendar sidebar
            cal_ids = [c["id"] for c in cal_list if c.get("selected")] or [
                c["id"] for c in cal_list
            ]

        per_calendar: list[list[dict[str, Any]]] = []
        for cal_id in cal_ids:
            try:
                result = (
                    service.events()
                    .list(
                        calendarId=cal_id,
                        timeMin=start.isoformat(),
                        timeMax=end.isoformat(),
                        singleEvents=True,
                        orderBy="startTime",
                        maxResults=50,
                    )
                    .execute()
                )
            except Exception:
                log.exception("event fetch failed for calendar %s", cal_id)
                continue
            items = result.get("items", [])
            for item in items:
                item["_cal_name"] = name_by_id.get(cal_id, cal_id)
            per_calendar.append(items)

        events, upcoming = bucket_events(
            merge_events(per_calendar, local_tz), today, local_tz, config.calendar.max_events
        )
        calendars = list(dict.fromkeys(name_by_id.get(cid, cid) for cid in cal_ids))
        return {"configured": True, "events": events, "upcoming": upcoming, "calendars": calendars}

    def render(
        self, image: Image.Image, rect: Rect, data: dict[str, Any] | None, config: AppConfig
    ) -> None:
        draw = panel_draw(image)
        x, y, w, h = title_bar(draw, rect, "Today")
        if not data:
            draw.text((x, y), "waiting for data…", font=font(16), fill=palette.MUTED)
            return
        if not data.get("configured"):
            hint = "Google Calendar not set up - run: uv run python -m inky_dash.google_auth"
            for i, line in enumerate(wrap_text(draw, hint, font(15), w)):
                draw.text((x, y + i * 20), line, font=font(15), fill=palette.MUTED)
            return
        events = data.get("events") or []
        upcoming = data.get("upcoming") or []
        # colour-code by calendar, but only the calendars visible in this view —
        # a legend of every subscribed calendar wouldn't fit the slot
        shown = events if events else upcoming
        active = list(dict.fromkeys(e["calendar"] for e in shown if e.get("calendar")))
        cal_color = self._calendar_colors(active)
        legend_h = 18 if len(cal_color) > 1 else 0
        bottom = y + h - legend_h

        if events:
            self._render_rows(
                draw,
                x,
                y,
                w,
                bottom,
                events,
                key="when",
                key_width=58,
                key_font=font(14, bold=True),
                cal_color=cal_color,
            )
        else:
            draw.text((x, y), "No events today", font=font(15), fill=palette.MUTED)
            if upcoming:
                draw.text((x, y + 22), "Next 7 days", font=font(12, bold=True), fill=palette.MUTED)
                self._render_rows(
                    draw,
                    x,
                    y + 40,
                    w,
                    bottom,
                    upcoming,
                    key="day",
                    key_width=58,
                    key_font=font(14, bold=True),
                    cal_color=cal_color,
                )
        if legend_h:
            self._render_legend(draw, x, y + h - 13, w, cal_color)

    @staticmethod
    def _calendar_colors(calendars: list[str]) -> dict[str, tuple[int, int, int]]:
        cycle = [palette.BLUE, palette.GREEN, palette.YELLOW, palette.BLACK]
        return {name: cycle[i % len(cycle)] for i, name in enumerate(calendars)}

    def _render_rows(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        top: int,
        w: int,
        bottom: int,
        rows: list[dict],
        *,
        key: str,
        key_width: int,
        key_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        cal_color: dict[str, tuple[int, int, int]],
    ) -> None:
        """Rows of `HH:MM/day  ▪ summary`, summary wrapped to two full-width lines."""
        summary_font = font(15)
        line_h = 18
        text_x = x + key_width + 14
        cursor = top
        for row in rows:
            summary = row["summary"]
            if key == "day" and row["when"] != "all day":
                summary = f"{row['when']} {summary}"
            lines = wrap_text(draw, summary, summary_font, w - (text_x - x))[:2]
            needed = len(lines) * line_h + 4
            if cursor + needed > bottom:
                # not enough room for the full row: fall back to one truncated
                # line rather than dropping the event entirely
                if len(lines) < 2 or cursor + line_h + 4 > bottom:
                    break
                lines = [lines[0] + "…"]
                needed = line_h + 4
            draw.text((x, cursor + 1), row[key], font=key_font, fill=palette.ACCENT)
            marker = cal_color.get(row.get("calendar", ""), palette.BLACK)
            draw.rectangle(
                (x + key_width + 2, cursor + 5, x + key_width + 9, cursor + 12), fill=marker
            )
            for i, line in enumerate(lines):
                draw.text(
                    (text_x, cursor + i * line_h), line, font=summary_font, fill=palette.BLACK
                )
            cursor += needed

    def _render_legend(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        w: int,
        cal_color: dict[str, tuple[int, int, int]],
    ) -> None:
        legend_font = font(11)
        cursor = x
        for name, color in cal_color.items():
            name_w = text_size(draw, name, legend_font)[0]
            if cursor + 12 + name_w > x + w:
                break
            draw.rectangle((cursor, y + 3, cursor + 7, y + 10), fill=color)
            draw.text((cursor + 11, y), name, font=legend_font, fill=palette.MUTED)
            cursor += 11 + name_w + 12
