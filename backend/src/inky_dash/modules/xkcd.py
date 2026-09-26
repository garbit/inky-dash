from __future__ import annotations

import random
from io import BytesIO
from typing import Any

import httpx
from PIL import Image

import inky_dash.modules.options as opts
from inky_dash import palette
from inky_dash.config import AppConfig
from inky_dash.fonts import font
from inky_dash.layout import Rect
from inky_dash.modules.common import panel_draw, title_bar, wrap_text

LATEST_URL = "https://xkcd.com/info.0.json"
COMIC_URL = "https://xkcd.com/{num}/info.0.json"
USER_AGENT = "inky-dash/0.1 (personal e-ink dashboard)"

# Comic 404 is xkcd's joke: the URL genuinely returns HTTP 404.
MISSING_COMICS = {404}

THRESHOLD = 190
# Upscaling line art beyond 2x turns strokes to mush; below that the panel is
# forgiving.
MAX_SCALE = 2.0
ALT_MAX_LINES = 3


def pick_random(latest: int, rng: random.Random | None = None) -> int:
    generator = rng or random
    while True:
        num = generator.randint(1, latest)
        if num not in MISSING_COMICS:
            return num


def prepare_image(raw: bytes, box: tuple[int, int], style: str) -> Image.Image:
    """Decode a comic, scale it to fit ``box``, and reduce it to pure
    black/white — resizing anti-aliases edges into greys, which the panel would
    otherwise dither into colour speckle."""
    comic = Image.open(BytesIO(raw)).convert("L")
    scale = min(box[0] / comic.width, box[1] / comic.height, MAX_SCALE)
    size = (max(1, round(comic.width * scale)), max(1, round(comic.height * scale)))
    comic = comic.resize(size, Image.LANCZOS)
    if style == "dither":
        comic = comic.convert("1")  # Floyd-Steinberg: shading survives as speckle
    else:
        comic = comic.point(lambda p: 255 if p >= THRESHOLD else 0)
    return comic.convert("RGB")


class XkcdModule:
    id = "xkcd"
    name = "xkcd"
    ttl = 10800
    config_key = "xkcd"
    fetch_key = "xkcd_minutes"
    options = [
        opts.select("mode", "Comic", [("latest", "Latest"), ("random", "Random")]),
        opts.select(
            "style",
            "Rendering",
            [
                ("threshold", "Crisp lines (pure black/white)"),
                ("dither", "Dithered (keeps shading)"),
            ],
        ),
        opts.checkbox("show_title", "Show comic title"),
        opts.checkbox("show_alt", "Show alt text"),
        opts.note(
            "Random picks a new comic every fetch, so the panel re-flashes each time.",
            visible_if=opts.when("mode", "eq", "random"),
        ),
    ]

    async def fetch(self, config: AppConfig) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            response = await client.get(LATEST_URL)
            response.raise_for_status()
            info = response.json()
            if config.xkcd.mode == "random":
                num = pick_random(info["num"])
                response = await client.get(COMIC_URL.format(num=num))
                response.raise_for_status()
                info = response.json()
            image = await client.get(info["img"])
            image.raise_for_status()
        return {
            "num": info["num"],
            "title": info.get("safe_title") or info.get("title") or "",
            "alt": info.get("alt") or "",
            "image": image.content,
        }

    def render(
        self, image: Image.Image, rect: Rect, data: dict[str, Any] | None, config: AppConfig
    ) -> None:
        draw = panel_draw(image)
        x, y, w, h = title_bar(draw, rect, "xkcd")
        if not data or not data.get("image"):
            draw.text((x, y), "waiting for a comic…", font=font(16), fill=palette.MUTED)
            return

        xkcd = config.xkcd
        if xkcd.show_title and data.get("title"):
            caption = f"#{data['num']}: {data['title']}"
            line = wrap_text(draw, caption, font(16, bold=True), w)[0]
            draw.text((x, y), line, font=font(16, bold=True), fill=palette.BLACK)
            y += 24
            h -= 24

        alt_lines: list[str] = []
        if xkcd.show_alt and data.get("alt"):
            alt_lines = wrap_text(draw, data["alt"], font(13), w)[:ALT_MAX_LINES]
            h -= len(alt_lines) * 18 + 6

        if w <= 0 or h <= 0:
            return
        comic = prepare_image(data["image"], (w, h), xkcd.style)
        image.paste(comic, (x + (w - comic.width) // 2, y + (h - comic.height) // 2))

        for i, line in enumerate(alt_lines):
            draw.text((x, y + h + 6 + i * 18), line, font=font(13), fill=palette.MUTED)
