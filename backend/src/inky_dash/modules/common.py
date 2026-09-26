"""Shared Pillow drawing helpers for panel renderers."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from inky_dash import palette
from inky_dash.fonts import font
from inky_dash.layout import Rect

PAD = 12


def panel_draw(image: Image.Image) -> ImageDraw.ImageDraw:
    """All renderers must draw through this: anti-aliased glyph edges dither
    into colour speckle on the six-colour panel, so text is drawn bilevel."""
    draw = ImageDraw.Draw(image)
    draw.fontmode = "1"
    return draw


def inset(rect: Rect, pad: int = PAD) -> Rect:
    x, y, w, h = rect
    return (x + pad, y + pad, max(0, w - 2 * pad), max(0, h - 2 * pad))


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=fnt)
    return right - left, bottom - top


def fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    *,
    bold: bool = False,
    min_size: int = 10,
    max_size: int = 480,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Largest font whose rendered ``text`` fits within the box, by binary search.

    Never estimate from point size — glyph metrics differ between the Pi (DejaVu)
    and macOS (Arial), so measure the actual bounding box each probe. ``font()`` is
    lru-cached, so the ~9 probes are cheap.
    """
    lo, hi = min_size, max_size
    best = font(min_size, bold=bold)
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = font(mid, bold=bold)
        w, h = text_size(draw, text, candidate)
        if w <= max_width and h <= max_height:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def draw_text_centered(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    text: str,
    fnt: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> tuple[int, int]:
    """Draw ``text`` optically centred on ``(cx, cy)``; return its (width, height).

    Subtracts the raw ``textbbox`` left/top bearing so the ink — not the nominal
    glyph box — is centred; ``text_size`` discards that bearing and mis-centres
    large digits.
    """
    left, top, right, bottom = draw.textbbox((0, 0), text, font=fnt)
    w, h = right - left, bottom - top
    draw.text((cx - w // 2 - left, cy - h // 2 - top), text, font=fnt, fill=fill)
    return w, h


def title_bar(draw: ImageDraw.ImageDraw, rect: Rect, title: str) -> Rect:
    """Draw a panel title with an accent rule; return the remaining content rect."""
    x, y, w, h = inset(rect)
    fnt = font(20, bold=True)
    draw.text((x, y), title.upper(), font=fnt, fill=palette.BLACK)
    _, th = text_size(draw, title.upper(), fnt)
    rule_y = y + th + 6
    draw.line([(x, rule_y), (x + w, rule_y)], fill=palette.ACCENT, width=3)
    content_top = rule_y + 8
    return (x, content_top, w, max(0, y + h - content_top))


def wrap_text(
    draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont, max_width: int
) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if text_size(draw, candidate, fnt)[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def placeholder(draw: ImageDraw.ImageDraw, rect: Rect, title: str, message: str) -> None:
    content = title_bar(draw, rect, title)
    x, y, w, _ = content
    fnt = font(16)
    for i, line in enumerate(wrap_text(draw, message, fnt, w)):
        draw.text((x, y + i * 22), line, font=fnt, fill=palette.MUTED)
