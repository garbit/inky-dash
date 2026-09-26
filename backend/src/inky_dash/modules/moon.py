"""The lunar panel: today's moon, where it is in the cycle, and when it is up.

Nothing here is fetched — ``astro.py`` computes the lot from the clock, so the
module has no TTL and no fetch key, like the clock panel. The observer position
comes from the weather panel's coordinates: moonrise and the side the terminator
falls on both need a location, and a second one to keep in sync would only ever
drift out of it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PIL import Image, ImageDraw, ImageFont

import inky_dash.modules.options as opts
from inky_dash import astro, palette
from inky_dash.config import AppConfig, MoonConfig
from inky_dash.fonts import font
from inky_dash.layout import Rect
from inky_dash.modules.clock_faces import format_time
from inky_dash.modules.common import (
    draw_text_centered,
    fit_font,
    inset,
    panel_draw,
    text_size,
    title_bar,
)

GAP = 14  # between the disc and the text column
MIN_DISC = 30  # a disc smaller than this shows no recognisable terminator
MAX_DISC = 320
MIN_TEXT_W = 112  # narrower than this and the text goes under the disc, not beside it
LINE_PAD = 6
# The panel is mostly one drawing and a few numbers, so both scale with the slot
# rather than sitting at one size — a full-screen moon should not be captioned in
# the same 16px type as a sidebar one.
NAME_MIN, NAME_MAX = 16, 40
NAME_RATIO = 0.14  # phase-name font as a fraction of the main area's height
BODY_RATIO = 0.68  # everything else, relative to the phase name
BODY_MIN, BODY_MAX = 14, 26
STRIP_MIN_H, STRIP_MAX_H = 62, 96  # mini disc + name + date
STRIP_RATIO = 0.2
STRIP_GAP = 8
STRIP_COL_MIN = 66
# A slot this shallow and this wide is a header/footer band, not a panel.
BAND_MAX_H = 72
BAND_MAX_DISC = 44
BAND_MAX_TEXT = 22

# What each principal phase looks like, as (lit fraction, waxing).
PHASE_APPEARANCE: dict[str, tuple[float, bool]] = {
    astro.NEW_MOON: (0.0, True),
    astro.FIRST_QUARTER: (0.5, True),
    astro.FULL_MOON: (1.0, True),
    astro.LAST_QUARTER: (0.5, False),
}
SHORT_NAMES: dict[str, str] = {
    astro.NEW_MOON: "New",
    astro.FIRST_QUARTER: "First qtr",
    astro.FULL_MOON: "Full",
    astro.LAST_QUARTER: "Last qtr",
}


def draw_moon(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    radius: int,
    illumination: float,
    waxing: bool,
    *,
    southern: bool = False,
) -> None:
    """A moon disc with a true terminator: the lit limb plus the elliptical
    shadow edge whose width is the disc's, scaled by how far from half-lit the
    moon is.

    Two colours only — a lit yellow and the panel's black — because the
    six-colour display has no greys to shade a limb with, and dithering one
    would read as speckle rather than as a moon.
    """
    box = [cx - radius, cy - radius, cx + radius, cy + radius]
    lit = max(0.0, min(1.0, illumination))
    # North of the equator the waxing moon is lit on the right; south of it the
    # whole disc is seen upside down, so the lit limb swaps sides.
    lit_right = waxing != southern

    draw.ellipse(box, fill=palette.BLACK)
    draw.pieslice(box, *((-90, 90) if lit_right else (90, 270)), fill=palette.YELLOW)
    # The terminator projects as a half-ellipse: it bulges across the centre into
    # the dark half when gibbous, and eats into the lit half when crescent.
    waist = radius * abs(1 - 2 * lit)
    if waist >= 1:
        draw.ellipse(
            [cx - waist, cy - radius, cx + waist, cy + radius],
            fill=palette.YELLOW if lit > 0.5 else palette.BLACK,
        )
    draw.ellipse(box, outline=palette.BLACK)


def southern_view(cfg: MoonConfig, latitude: float) -> bool:
    """Whether the disc is seen mirrored — south of the equator the moon appears
    upside down, so the lit limb is on the opposite side."""
    return cfg.hemisphere == "south" or (cfg.hemisphere == "auto" and latitude < 0)


class MoonModule:
    id = "moon"
    name = "Moon"
    ttl = 0  # computed at render time from the clock, like the clock panel
    config_key = "moon"
    fetch_key = None
    options = [
        opts.checkbox("show_phases", "Show the upcoming phases"),
        opts.number("phase_count", "Phases shown", visible_if=opts.when("show_phases")),
        opts.checkbox("show_rise_set", "Show moonrise and moonset"),
        opts.checkbox("twenty_four_hour", "24-hour times", visible_if=opts.when("show_rise_set")),
        opts.checkbox("show_distance", "Show distance from Earth"),
        opts.select(
            "hemisphere",
            "Terminator",
            [
                ("auto", "Match the weather location"),
                ("north", "Northern hemisphere"),
                ("south", "Southern hemisphere"),
            ],
        ),
        opts.note(
            "The moon is worked out on the Pi — nothing is fetched, so this panel keeps "
            "working offline. Moonrise, moonset and which side of the disc is lit use "
            "the weather panel's location."
        ),
    ]

    async def fetch(self, config: AppConfig) -> dict[str, Any]:
        return {}

    def render(
        self, image: Image.Image, rect: Rect, data: dict[str, Any] | None, config: AppConfig
    ) -> None:
        draw = panel_draw(image)
        cfg = config.moon
        slot = inset(rect)
        if slot[2] < 24 or slot[3] < 16:
            return

        snapshot = astro.snapshot(
            datetime.now().astimezone(),
            config.weather.latitude,
            config.weather.longitude,
            cfg.phase_count if cfg.show_phases else 1,
        )
        southern = southern_view(cfg, config.weather.latitude)

        # A header or footer band gets the clock module's treatment: no title
        # bar, everything on one line — a title rule would eat the whole slot.
        if slot[3] < BAND_MAX_H and slot[2] >= 3 * slot[3]:
            self._render_band(draw, slot, snapshot, cfg, southern)
            return

        x, y, w, h = title_bar(draw, rect, "Moon")
        if w < MIN_DISC or h < 24:
            return

        columns = min(len(snapshot.upcoming), w // STRIP_COL_MIN) if cfg.show_phases else 0
        strip_h = min(max(STRIP_MIN_H, round(h * STRIP_RATIO)), STRIP_MAX_H)
        if h < strip_h + STRIP_GAP + MIN_DISC:
            columns = 0
        if not columns:
            strip_h = 0

        main_h = h - (strip_h + STRIP_GAP if strip_h else 0)
        self._render_main(draw, (x, y, w, main_h), snapshot, cfg, southern, bool(columns))
        if columns:
            self._render_strip(
                draw, (x, y + h - strip_h, w, strip_h), snapshot.upcoming[:columns], southern
            )

    def _render_band(
        self,
        draw: ImageDraw.ImageDraw,
        area: Rect,
        snapshot: astro.MoonSnapshot,
        cfg: MoonConfig,
        southern: bool,
    ) -> None:
        """The slim-strip form: the moon inline with as many of its facts as the
        width takes, most important first."""
        x, y, w, h = area
        radius = max(6, min(h, BAND_MAX_DISC)) // 2
        draw_moon(
            draw,
            x + radius,
            y + h // 2,
            radius,
            snapshot.illumination,
            snapshot.waxing,
            southern=southern,
        )
        size = max(12, min(BAND_MAX_TEXT, h - 4))
        name_font, body = font(size, bold=True), font(max(12, size - 2))
        lit = f"{round(snapshot.illumination * 100)}% lit"
        facts = [lit]
        if cfg.show_rise_set:
            facts.append(
                f"Rise {self._clock(snapshot.rise, cfg)} · Set {self._clock(snapshot.sets, cfg)}"
            )
        if cfg.show_distance:
            facts.append(f"{round(snapshot.distance_km):,} km")
        if snapshot.upcoming:
            name, when = snapshot.upcoming[0]
            facts.append(f"{SHORT_NAMES[name]} {when.day} {when.strftime('%b')}")

        text_x = x + 2 * radius + 10
        available = x + w - text_x
        if text_size(draw, snapshot.phase, name_font)[0] > available:
            # Not even the phase name fits beside the disc; the percentage is the
            # one thing worth keeping.
            if text_size(draw, lit, body)[0] <= available:
                self._draw_middle(draw, text_x, y, h, lit, body, palette.BLACK)
            return
        self._draw_middle(draw, text_x, y, h, snapshot.phase, name_font, palette.BLACK)
        rest_x = text_x + text_size(draw, snapshot.phase, name_font)[0] + 14
        rest = ""
        for fact in facts:
            candidate = f"{rest}  ·  {fact}" if rest else fact
            if text_size(draw, candidate, body)[0] > x + w - rest_x:
                break
            rest = candidate
        if rest:
            self._draw_middle(draw, rest_x, y, h, rest, body, palette.MUTED)

    def _draw_middle(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        h: int,
        text: str,
        fnt: ImageFont.ImageFont,
        colour: tuple[int, int, int],
    ) -> None:
        draw.text((x, y + (h - text_size(draw, text, fnt)[1]) // 2), text, font=fnt, fill=colour)

    def _render_main(
        self,
        draw: ImageDraw.ImageDraw,
        area: Rect,
        snapshot: astro.MoonSnapshot,
        cfg: MoonConfig,
        southern: bool,
        strip_shown: bool,
    ) -> None:
        x, y, w, h = area
        radius = min(h, round(w * 0.46), MAX_DISC) // 2
        text_w = w - 2 * radius - GAP
        if 2 * radius >= MIN_DISC and text_w >= MIN_TEXT_W:
            draw_moon(
                draw,
                x + radius,
                y + h // 2,
                radius,
                snapshot.illumination,
                snapshot.waxing,
                southern=southern,
            )
            lines = self._lines(draw, text_w, h, snapshot, cfg, strip_shown)
            self._render_lines(draw, (x + 2 * radius + GAP, y, text_w, h), lines)
            return

        # Too narrow to set text beside the moon: draw the disc as large as the
        # slot allows, with the one number worth keeping.
        label = f"{round(snapshot.illumination * 100)}% lit"
        label_h = 18 if h >= MIN_DISC + 18 and w >= 60 else 0
        radius = max(4, min(h - label_h, w) // 2)
        cx = x + w // 2
        draw_moon(
            draw, cx, y + radius, radius, snapshot.illumination, snapshot.waxing, southern=southern
        )
        if label_h:
            draw_text_centered(
                draw, cx, y + 2 * radius + label_h // 2, label, font(14), palette.BLACK
            )

    def _lines(
        self,
        draw: ImageDraw.ImageDraw,
        width: int,
        height: int,
        snapshot: astro.MoonSnapshot,
        cfg: MoonConfig,
        strip_shown: bool,
    ) -> list[tuple[str, ImageFont.ImageFont, tuple[int, int, int]]]:
        """The text column, most important first — ``_render_lines`` drops from
        the end whatever the height can't take."""
        name_size = max(NAME_MIN, min(NAME_MAX, round(height * NAME_RATIO)))
        body_size = max(BODY_MIN, min(BODY_MAX, round(name_size * BODY_RATIO)))
        body, muted = font(body_size), font(max(BODY_MIN - 1, body_size - 1))
        # Wrapping a phase name reads worse than setting it smaller, so the name
        # shrinks to the column rather than breaking.
        name_font = fit_font(
            draw, snapshot.phase, width, name_size, bold=True, min_size=11, max_size=name_size
        )
        cycle_days = round(astro.SYNODIC_MONTH, 1)
        lines = [
            (snapshot.phase, name_font, palette.BLACK),
            (f"{round(snapshot.illumination * 100)}% lit", body, palette.BLACK),
        ]
        if cfg.show_rise_set:
            rise = self._clock(snapshot.rise, cfg)
            sets = self._clock(snapshot.sets, cfg)
            together = f"Rise {rise} · Set {sets}"
            if text_size(draw, together, muted)[0] <= width:
                lines.append((together, muted, palette.MUTED))
            else:
                lines.append((f"Rise {rise}", muted, palette.MUTED))
                lines.append((f"Set {sets}", muted, palette.MUTED))
        # Whole days: a decimal would tick over between refreshes, and every
        # change to this panel costs ~30s of flashing.
        lines.append((f"Day {round(snapshot.age_days)} of {cycle_days}", muted, palette.MUTED))
        if cfg.show_distance:
            lines.append((f"{round(snapshot.distance_km):,} km", muted, palette.MUTED))
        if not strip_shown and snapshot.upcoming:
            name, when = snapshot.upcoming[0]
            full = f"{name} {when.day} {when.strftime('%b')}"
            short = f"{SHORT_NAMES[name]} {when.day} {when.strftime('%b')}"
            fits = text_size(draw, full, muted)[0] <= width
            lines.append((full if fits else short, muted, palette.BLUE))
        return lines

    def _clock(self, moment: datetime | None, cfg: MoonConfig) -> str:
        """A rise/set time, or an em dash — on roughly one day in thirty the moon
        genuinely doesn't rise (or doesn't set) within the calendar day."""
        return format_time(moment, cfg.twenty_four_hour) if moment else "—"

    def _render_lines(
        self,
        draw: ImageDraw.ImageDraw,
        area: Rect,
        lines: list[tuple[str, ImageFont.ImageFont, tuple[int, int, int]]],
    ) -> None:
        x, y, _, h = area
        heights = [text_size(draw, "Ag", fnt)[1] + LINE_PAD for _, fnt, _ in lines]
        used = 0
        fitted = 0
        for height in heights:
            if used + height > h and fitted:
                break
            used += height
            fitted += 1
        top = y + max(0, (h - used) // 2)
        for (text, fnt, colour), height in zip(lines[:fitted], heights[:fitted], strict=True):
            draw.text((x, top), text, font=fnt, fill=colour)
            top += height

    def _render_strip(
        self,
        draw: ImageDraw.ImageDraw,
        area: Rect,
        upcoming: list[tuple[str, datetime]],
        southern: bool,
    ) -> None:
        """The cycle ahead: a mini moon per principal phase, with its date."""
        x, y, w, h = area
        col_w = w // len(upcoming)
        size = 15 if h >= 74 else 13
        label_font, date_font = font(size, bold=True), font(size)
        line_h = text_size(draw, "Ag", label_font)[1] + 4
        radius = max(5, min((h - 2 * line_h) // 2, (col_w - 14) // 2, 26))
        for index, (name, when) in enumerate(upcoming):
            cx = x + col_w * index + col_w // 2
            illumination, waxing = PHASE_APPEARANCE[name]
            draw_moon(draw, cx, y + radius, radius, illumination, waxing, southern=southern)
            label = SHORT_NAMES[name]
            if text_size(draw, label, label_font)[0] > col_w - 4:
                label = label.split(" ")[0]  # "First qtr" -> "First" in a tight column
            text_y = y + 2 * radius + 4
            draw_text_centered(draw, cx, text_y + line_h // 2, label, label_font, palette.BLACK)
            date_text = f"{when.day} {when.strftime('%b')}"
            draw_text_centered(
                draw, cx, text_y + line_h + line_h // 2, date_text, date_font, palette.MUTED
            )
