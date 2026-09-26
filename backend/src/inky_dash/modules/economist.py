from __future__ import annotations

from typing import Any

import feedparser
import httpx
from PIL import Image

import inky_dash.modules.options as opts
from inky_dash import palette
from inky_dash.config import AppConfig
from inky_dash.fonts import font
from inky_dash.layout import Rect
from inky_dash.modules.common import panel_draw, title_bar, wrap_text

FEED_URL = "https://www.economist.com/{section}/rss.xml"
USER_AGENT = "inky-dash/0.1 (personal e-ink dashboard)"


def parse_feed(content: bytes, section: str) -> list[dict[str, str]]:
    feed = feedparser.parse(content)
    return [
        {"title": entry.get("title", "").strip(), "section": section}
        for entry in feed.entries
        if entry.get("title")
    ]


def interleave(per_section: list[list[dict]], limit: int) -> list[dict]:
    """Round-robin across sections so one feed can't crowd out the others."""
    items: list[dict] = []
    index = 0
    while len(items) < limit and any(index < len(section) for section in per_section):
        for section in per_section:
            if index < len(section) and len(items) < limit:
                items.append(section[index])
        index += 1
    return items


def section_label(slug: str) -> str:
    """Turn a feed slug (``the-world-this-week``) into a display label."""
    return slug.replace("-", " ").title()


# Below this width a column can't hold a readable headline, so auto-fit targets it.
MIN_COLUMN_WIDTH = 240


class EconomistModule:
    id = "economist"
    name = "Economist"
    ttl = 1800
    config_key = "economist"
    fetch_key = "economist_minutes"
    options = [
        opts.string_list("sections", "Sections (comma separated)"),
        opts.number("max_items", "Max headlines"),
        opts.select(
            "columns",
            "Columns",
            [(0, "Auto (fit to width)"), (1, "1"), (2, "2"), (3, "3"), (4, "4")],
        ),
        opts.number("max_lines", "Lines per headline"),
        opts.checkbox("show_sections", "Show section labels"),
    ]

    async def fetch(self, config: AppConfig) -> dict[str, Any]:
        per_section: list[list[dict]] = []
        async with httpx.AsyncClient(
            timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            for section in config.economist.sections:
                response = await client.get(FEED_URL.format(section=section))
                response.raise_for_status()
                per_section.append(parse_feed(response.content, section))
        return {"items": interleave(per_section, config.economist.max_items)}

    def render(
        self, image: Image.Image, rect: Rect, data: dict[str, Any] | None, config: AppConfig
    ) -> None:
        draw = panel_draw(image)
        x, y, w, h = title_bar(draw, rect, "The Economist")
        items = (data or {}).get("items") or []
        if not items:
            draw.text((x, y), "waiting for headlines…", font=font(16), fill=palette.MUTED)
            return

        econ = config.economist
        gutter = 24
        if econ.columns > 0:
            columns = min(econ.columns, len(items))
        else:
            columns = max(1, (w + gutter) // (MIN_COLUMN_WIDTH + gutter))
        columns = max(1, columns)
        col_w = (w - gutter * (columns - 1)) // columns

        headline_font = font(16)
        section_font = font(11, bold=True)
        line_h = 21
        section_h = 16
        col, cursor_y = 0, y
        for item in items:
            lines = wrap_text(draw, item["title"], headline_font, col_w - 14)[: econ.max_lines]
            needed = len(lines) * line_h + 8
            if econ.show_sections:
                needed += section_h
            if cursor_y + needed > y + h and cursor_y > y:
                col += 1
                cursor_y = y
                if col >= columns:
                    break
            cx = x + col * (col_w + gutter)
            top = cursor_y
            if econ.show_sections:
                draw.text(
                    (cx + 14, top),
                    section_label(item["section"]),
                    font=section_font,
                    fill=palette.ACCENT,
                )
                top += section_h
            draw.text((cx, top + 2), "■", font=font(11), fill=palette.ACCENT)
            for i, line in enumerate(lines):
                draw.text((cx + 14, top + i * line_h), line, font=headline_font, fill=palette.BLACK)
            cursor_y += needed
