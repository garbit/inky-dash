"""Pillow-drawn weather icons for the WMO weather codes.

Icons are built from solid shapes in the six-colour Spectra palette — no grey,
no anti-aliasing (mid-tones dither into speckle on the panel). Outlined shapes
are drawn as a filled silhouette in the outline colour with a slightly smaller
fill on top, so overlapping parts merge without internal seams.
"""

from __future__ import annotations

import math

from PIL import ImageDraw

from inky_dash import palette


def _stroke(s: float) -> int:
    return max(2, round(s / 16))


def _sun(draw: ImageDraw.ImageDraw, x: float, y: float, s: float, *, rays: bool = True) -> None:
    cx, cy = x + s / 2, y + s / 2
    r = s * 0.28
    t = _stroke(s)
    if rays:
        for i in range(8):
            a = math.radians(i * 45)
            inner, outer = r + s * 0.08, r + s * 0.21
            draw.line(
                [
                    (cx + math.cos(a) * inner, cy + math.sin(a) * inner),
                    (cx + math.cos(a) * outer, cy + math.sin(a) * outer),
                ],
                fill=palette.YELLOW,
                width=t,
            )
    draw.ellipse(
        [cx - r, cy - r, cx + r, cy + r],
        fill=palette.YELLOW,
        outline=palette.BLACK,
        width=max(1, round(s / 28)),
    )


def _cloud(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    w: float,
    *,
    fill: tuple[int, int, int] = palette.WHITE,
    outline: tuple[int, int, int] = palette.BLACK,
) -> None:
    """Cloud in a box of width w (height ~0.62*w) with its top-left at (x, y)."""
    t = _stroke(w * 0.8)
    circles = [  # cx, cy, r as fractions of w
        (0.30, 0.38, 0.20),
        (0.58, 0.30, 0.26),
        (0.78, 0.42, 0.16),
    ]
    base = (0.06, 0.36, 0.94, 0.60)  # x0, y0, x1, y1 fractions of w
    radius = 0.12 * w

    def shapes(shrink: float) -> None:
        for cx, cy, r in circles:
            rr = max(1.0, r * w - shrink)
            draw.ellipse(
                [x + cx * w - rr, y + cy * w - rr, x + cx * w + rr, y + cy * w + rr],
                fill=outline if not shrink else fill,
            )
        x0, y0, x1, y1 = (v * w for v in base)
        sh = min(shrink, (y1 - y0) / 2 - 0.5, (x1 - x0) / 2 - 0.5)
        draw.rounded_rectangle(
            [x + x0 + sh, y + y0 + sh, x + x1 - sh, y + y1 - sh],
            radius=max(1, radius - sh),
            fill=outline if not shrink else fill,
        )

    shapes(0)
    shapes(t)


def _rain_lines(draw: ImageDraw.ImageDraw, x: float, y: float, s: float, top: float) -> None:
    t = _stroke(s)
    for i in range(3):
        x0 = x + s * (0.30 + 0.22 * i)
        draw.line(
            [(x0, y + s * top), (x0 - s * 0.07, y + s * (top + 0.22))],
            fill=palette.BLUE,
            width=t,
        )


def _snow_flakes(draw: ImageDraw.ImageDraw, x: float, y: float, s: float, top: float) -> None:
    t = max(1, _stroke(s) - 1)
    r = max(2.0, s * 0.07)
    for fx, dy in [(0.28, 0.0), (0.52, 0.08), (0.76, 0.0)]:
        cx, cy = x + s * fx, y + s * (top + 0.10 + dy)
        for a in (90, 30, 150):
            dx = math.cos(math.radians(a)) * r
            dyy = math.sin(math.radians(a)) * r
            draw.line([(cx - dx, cy - dyy), (cx + dx, cy + dyy)], fill=palette.BLUE, width=t)


def _bolt(draw: ImageDraw.ImageDraw, x: float, y: float, s: float, top: float) -> None:
    pts = [
        (0.50, top),
        (0.34, top + 0.25),
        (0.46, top + 0.25),
        (0.38, top + 0.44),
        (0.66, top + 0.16),
        (0.52, top + 0.16),
        (0.62, top),
    ]
    draw.polygon(
        [(x + fx * s, y + fy * s) for fx, fy in pts],
        fill=palette.YELLOW,
        outline=palette.BLACK,
    )


def _icon_clear(d: ImageDraw.ImageDraw, x: float, y: float, s: float) -> None:
    _sun(d, x + s * 0.08, y + s * 0.08, s * 0.84)


def _icon_partly(d: ImageDraw.ImageDraw, x: float, y: float, s: float) -> None:
    _sun(d, x + s * 0.40, y + s * 0.02, s * 0.52)
    _cloud(d, x + s * 0.02, y + s * 0.30, s * 0.88)


def _icon_overcast(d: ImageDraw.ImageDraw, x: float, y: float, s: float) -> None:
    _cloud(d, x + s * 0.16, y + s * 0.06, s * 0.78)
    _cloud(d, x, y + s * 0.32, s * 0.90)


def _icon_cloud(d: ImageDraw.ImageDraw, x: float, y: float, s: float) -> None:
    _cloud(d, x + s * 0.04, y + s * 0.18, s * 0.92)


def _icon_fog(d: ImageDraw.ImageDraw, x: float, y: float, s: float) -> None:
    _cloud(d, x + s * 0.08, y, s * 0.84)
    t = _stroke(s)
    for i, (x0, x1) in enumerate([(0.12, 0.88), (0.20, 0.96), (0.12, 0.80)]):
        ly = y + s * (0.66 + 0.13 * i)
        d.line([(x + s * x0, ly), (x + s * x1, ly)], fill=palette.BLUE, width=t)


def _icon_drizzle(d: ImageDraw.ImageDraw, x: float, y: float, s: float) -> None:
    _cloud(d, x + s * 0.04, y, s * 0.92)
    r = max(2.0, s * 0.05)
    for fx, fy in [(0.30, 0.74), (0.52, 0.84), (0.74, 0.74)]:
        cx, cy = x + s * fx, y + s * fy
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=palette.BLUE)


def _icon_rain(d: ImageDraw.ImageDraw, x: float, y: float, s: float) -> None:
    _cloud(d, x + s * 0.04, y, s * 0.92)
    _rain_lines(d, x, y, s, 0.70)


def _icon_showers(d: ImageDraw.ImageDraw, x: float, y: float, s: float) -> None:
    _sun(d, x + s * 0.46, y, s * 0.46, rays=False)
    _cloud(d, x + s * 0.02, y + s * 0.14, s * 0.88)
    _rain_lines(d, x, y, s, 0.74)


def _icon_snow(d: ImageDraw.ImageDraw, x: float, y: float, s: float) -> None:
    _cloud(d, x + s * 0.04, y, s * 0.92)
    _snow_flakes(d, x, y, s, 0.70)


def _icon_thunder(d: ImageDraw.ImageDraw, x: float, y: float, s: float) -> None:
    _cloud(d, x + s * 0.04, y, s * 0.92)
    _bolt(d, x, y, s, 0.56)


_ICONS = {
    (0,): _icon_clear,
    (1, 2): _icon_partly,
    (3,): _icon_overcast,
    (45, 48): _icon_fog,
    (51, 53, 55, 56, 57): _icon_drizzle,
    (61, 63, 65, 66, 67): _icon_rain,
    (71, 73, 75, 77): _icon_snow,
    (80, 81, 82): _icon_showers,
    (85, 86): _icon_snow,
    (95, 96, 99): _icon_thunder,
}


def draw_weather_icon(draw: ImageDraw.ImageDraw, code: int, x: int, y: int, size: int) -> None:
    """Draw the icon for a WMO weather code into the (x, y, size, size) box."""
    for codes, icon in _ICONS.items():
        if code in codes:
            icon(draw, x, y, size)
            return
    _icon_cloud(draw, x, y, size)
