"""Vox: a word a day, to expand the vocabulary of whoever walks past the panel.

The word comes from the bundled curated list (``vox_words.py``); the definition
from Wiktionary's REST API, and the synonyms from Datamuse. Both are free and
keyless. In ``daily`` mode the choice is a pure function of the date, so every
fetch through the day composes the identical frame and the loop skips the ~30s
refresh.

This module used to read dictionaryapi.dev, whose origin server went dark in
2026 — Cloudflare answered 522 for every word not already in its edge cache,
which meant every *uncommon* word, i.e. the whole curated list. Wiktionary is
the same underlying corpus dictionaryapi.dev was derived from, served by the
Wikimedia Foundation instead of one unattended host.
"""

from __future__ import annotations

import hashlib
import html
import random
import re
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx
from PIL import Image

import inky_dash.modules.options as opts
from inky_dash import palette
from inky_dash.config import AppConfig
from inky_dash.fonts import font
from inky_dash.layout import Rect
from inky_dash.modules.common import (
    fit_font,
    inset,
    panel_draw,
    text_size,
    title_bar,
    wrap_text,
)
from inky_dash.modules.vox_words import WORDS

LOOKUP_URL = "https://en.wiktionary.org/api/rest_v1/page/definition/{word}"
SYNONYM_URL = "https://api.datamuse.com/words"
USER_AGENT = "inky-dash/0.1 (personal e-ink dashboard)"

# Curated words occasionally have no dictionary entry; try a few before giving up.
MAX_ATTEMPTS = 4
MAX_SENSES = 4
MAX_SYNONYMS = 4
# Wiktionary answers in well under a second; a long ceiling here only lets a sick
# endpoint stall the render loop, which is exactly how the old source failed.
TIMEOUT = 15
# The word itself is the panel: big, but never so tall it crowds out the meaning.
WORD_HEIGHT_FRACTION = 0.32
WORD_MAX_SIZE = 120
# Body type grows with the region — a full-screen vox screen read from across the
# room needs bigger text than the same panel tucked into a sidebar.
BODY_MIN, BODY_MAX = 15, 24
# ...but not longer lines: past ~34 characters per em the eye loses the next line.
MEASURE_EMS = 34
STALE_TAG = "OFFLINE"

# Wiktionary returns rendered HTML. <style>/<link> blocks carry CSS *text* that
# would otherwise survive tag-stripping and land on the panel as gibberish, so
# they are removed with their contents before any other markup is touched.
_DROP_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_VOID_RE = re.compile(r"<(?:link|meta|br)\b[^>]*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def strip_html(markup: str) -> str:
    """Flatten one Wiktionary definition to the plain text the panel can draw."""
    text = _DROP_RE.sub(" ", markup)
    text = _VOID_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)  # inline <a>/<b> wrap whole words: no space added
    text = html.unescape(text)
    return _SPACE_RE.sub(" ", text).strip()


def body_size(height: int) -> int:
    return max(BODY_MIN, min(BODY_MAX, height // 22))


def word_for_day(day: date, attempt: int = 0, words: tuple[str, ...] = WORDS) -> str:
    """The word of the day: stable for a given date (so the panel doesn't reflash
    mid-day) but unordered, so consecutive days aren't alphabetical neighbours.
    ``attempt`` walks to a different word when the dictionary has no entry."""
    digest = hashlib.sha256(f"{day.isoformat()}:{attempt}".encode()).digest()
    return words[int.from_bytes(digest[:8], "big") % len(words)]


def _examples(definition: dict[str, Any]) -> str:
    """The first usage example, if this sense has one. Wiktionary offers them as
    ``parsedExamples`` (dicts) or bare ``examples`` (HTML), and most words have
    neither — an absent example is normal, not a failure."""
    for parsed in definition.get("parsedExamples") or []:
        if isinstance(parsed, dict) and (text := strip_html(parsed.get("example") or "")):
            return text
    for raw in definition.get("examples") or []:
        if isinstance(raw, str) and (text := strip_html(raw)):
            return text
    return ""


def parse_entry(payload: Any, word: str) -> dict[str, Any] | None:
    """Normalise a Wiktionary definition response into panel data, or None when
    the word has no usable English entry.

    The payload is keyed by language — a word like ``plangent`` also carries a
    Latin section — so only ``en`` is read; anything else would put a foreign
    definition under an English headword.
    """
    if not isinstance(payload, dict):
        return None
    groups = [g for g in (payload.get("en") or []) if isinstance(g, dict)]
    if not groups:
        return None

    # First the leading definition of each part of speech, then the runners-up:
    # a noun and a verb sense teach more than two shades of the same noun.
    senses: list[dict[str, str]] = []
    for rank in range(MAX_SENSES):
        for group in groups:
            definitions = group.get("definitions") or []
            if rank >= len(definitions) or len(senses) >= MAX_SENSES:
                continue
            definition = strip_html(definitions[rank].get("definition") or "")
            if not definition:
                continue
            senses.append(
                {
                    "part_of_speech": (group.get("partOfSpeech") or "").strip().lower(),
                    "definition": definition,
                    "example": _examples(definitions[rank]),
                }
            )
    if not senses:
        return None
    return {"word": word, "phonetic": "", "senses": senses, "synonyms": []}


async def fetch_synonyms(client: httpx.AsyncClient, word: str) -> list[str]:
    """Datamuse synonyms — decoration, never a reason to fail the panel, so any
    error here degrades to no synonyms rather than propagating."""
    try:
        response = await client.get(SYNONYM_URL, params={"rel_syn": word, "max": MAX_SYNONYMS})
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    found: list[str] = []
    for item in payload:
        synonym = (item.get("word") or "").strip() if isinstance(item, dict) else ""
        if synonym and synonym.lower() != word.lower() and synonym not in found:
            found.append(synonym)
    return found[:MAX_SYNONYMS]


class VoxModule:
    id = "vox"
    name = "Vox (word of the day)"
    ttl = 10800
    config_key = "vox"
    fetch_key = "vox_minutes"
    options = [
        opts.select(
            "mode",
            "Word",
            [("daily", "Word of the day"), ("random", "Random each fetch")],
        ),
        opts.number("max_definitions", "Definitions shown"),
        opts.row(
            opts.checkbox("show_part_of_speech", "Part of speech"),
            opts.checkbox("show_synonyms", "Synonyms"),
        ),
        opts.checkbox("show_example", "Example sentence"),
        opts.note(
            "The daily word changes at midnight and is the same on every device — "
            "the panel only reflashes once a day."
        ),
        opts.note(
            "Definitions come from Wiktionary and synonyms from Datamuse. Wiktionary "
            "carries an example sentence for only some words."
        ),
        opts.note(
            "Random picks a new word every fetch, so the panel re-flashes each time.",
            visible_if=opts.when("mode", "eq", "random"),
        ),
    ]

    async def fetch(self, config: AppConfig) -> dict[str, Any]:
        today = date.today()
        tried: list[str] = []
        failures: list[str] = []
        async with httpx.AsyncClient(
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            for attempt in range(MAX_ATTEMPTS):
                if config.vox.mode == "random":
                    word = random.choice([w for w in WORDS if w not in tried])
                else:
                    word = word_for_day(today, attempt)
                    if word in tried:
                        continue
                tried.append(word)
                # A word the dictionary lacks and a dictionary that is down both
                # mean "try another word" — the old code only retried the former,
                # so one 5xx took the whole panel out until the service returned.
                try:
                    response = await client.get(LOOKUP_URL.format(word=quote(word)))
                except httpx.RequestError as exc:
                    failures.append(f"{word}: {type(exc).__name__}")
                    continue
                if response.status_code == httpx.codes.NOT_FOUND:
                    continue
                if response.is_server_error:
                    failures.append(f"{word}: HTTP {response.status_code}")
                    continue
                response.raise_for_status()
                parsed = parse_entry(response.json(), word)
                if parsed is not None:
                    if config.vox.show_synonyms:
                        parsed["synonyms"] = await fetch_synonyms(client, word)
                    parsed["fetched_on"] = today.isoformat()
                    return parsed
        detail = "; ".join(failures) if failures else "no entry"
        raise RuntimeError(f"no definition for {', '.join(tried)} ({detail})")

    def is_stale(self, data: dict[str, Any], config: AppConfig) -> bool:
        """True when the daily word on screen is not today's. The loop keeps the
        last good data when a fetch fails, which is the right call for a panel
        that refreshes in 30 seconds — but it must not pass a week-old word off
        as the word of the day."""
        if config.vox.mode != "daily":
            return False
        fetched_on = data.get("fetched_on")
        if not isinstance(fetched_on, str) or not fetched_on:
            return False  # older cache entry: say nothing rather than cry wolf
        return fetched_on != date.today().isoformat()

    def render(
        self, image: Image.Image, rect: Rect, data: dict[str, Any] | None, config: AppConfig
    ) -> None:
        draw = panel_draw(image)
        inset_x, inset_y, inset_w, _ = inset(rect)
        x, y, w, h = title_bar(draw, rect, "Word of the day")
        if not data or not data.get("word"):
            draw.text((x, y), "waiting for a word…", font=font(16), fill=palette.MUTED)
            return

        if self.is_stale(data, config):
            # Constant text, so a stale panel still hashes identically between
            # refreshes and does not re-flash while the feed is down.
            tag_font = font(12, bold=True)
            tag_w, _ = text_size(draw, STALE_TAG, tag_font)
            draw.text(
                (inset_x + inset_w - tag_w, inset_y + 4),
                STALE_TAG,
                font=tag_font,
                fill=palette.MUTED,
            )

        vox = config.vox
        bottom = y + h
        word_font = fit_font(
            draw,
            data["word"],
            w,
            max(16, int(h * WORD_HEIGHT_FRACTION)),
            bold=True,
            max_size=WORD_MAX_SIZE,
        )
        # Advance by the drawn box, not the point size: load_default's fallback
        # font has no .size, and large glyphs sit well below their nominal top.
        drawn = draw.textbbox((x, y), data["word"], font=word_font)
        draw.text((x, y), data["word"], font=word_font, fill=palette.BLACK)
        y = drawn[3] + 8

        size = body_size(h)
        body_font = font(size)
        pos_font = font(max(11, size - 3), bold=True)
        example_font = font(max(13, size - 2))
        line_h = round(size * 1.28)
        pos_h = round(size * 1.1)
        example_h = round(size * 1.15)
        measure = min(w, size * MEASURE_EMS)

        if vox.show_phonetic and data.get("phonetic") and y < bottom:
            draw.text((x, y), data["phonetic"], font=body_font, fill=palette.MUTED)
            y += line_h + 4

        senses = data.get("senses") or []
        numbered = min(vox.max_definitions, len(senses)) > 1
        last_pos = ""
        for index, sense in enumerate(senses[: vox.max_definitions], start=1):
            pos = sense["part_of_speech"].upper() if vox.show_part_of_speech else ""
            # Only label a part of speech once per run, and never without room for
            # a line of the definition it heads — a lone "NOUN" at the foot of a
            # short panel is noise.
            heading = pos_h if pos and pos != last_pos else 0
            if y + heading + line_h > bottom:
                break
            if heading:
                draw.text((x, y), pos, font=pos_font, fill=palette.ACCENT)
                y += heading
                last_pos = pos
            prefix = f"{index}. " if numbered else ""
            lines = wrap_text(draw, prefix + sense["definition"], body_font, measure)
            room = max(1, (bottom - y) // line_h)
            if len(lines) > room:  # cut it short rather than mid-sentence
                lines = lines[:room]
                lines[-1] += "…"
            for line in lines:
                draw.text((x, y), line, font=body_font, fill=palette.BLACK)
                y += line_h
            if vox.show_example and sense.get("example"):
                for line in wrap_text(draw, f"“{sense['example']}”", example_font, measure - 12):
                    if y + example_h > bottom:
                        break
                    draw.text((x + 12, y), line, font=example_font, fill=palette.MUTED)
                    y += example_h
            y += 8

        if vox.show_synonyms and data.get("synonyms") and y + example_h <= bottom:
            line = wrap_text(draw, "syn. " + ", ".join(data["synonyms"]), example_font, measure)[0]
            draw.text((x, y), line, font=example_font, fill=palette.MUTED)
