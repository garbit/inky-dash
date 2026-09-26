import asyncio
from datetime import date, timedelta

import httpx
import pytest
from PIL import Image

from inky_dash import palette
from inky_dash.config import AppConfig
from inky_dash.modules.vox import (
    BODY_MAX,
    BODY_MIN,
    VoxModule,
    body_size,
    fetch_synonyms,
    parse_entry,
    strip_html,
    word_for_day,
)
from inky_dash.modules.vox_words import WORDS

# Shaped like a real Wiktionary /page/definition response: keyed by language,
# definitions carrying rendered HTML, and a second language section that must
# never leak into an English panel.
ENTRY = {
    "en": [
        {
            "partOfSpeech": "Noun",
            "definitions": [
                {"definition": "A fever which recurs every day."},
                {"definition": "Commonplace things regarded as a class."},
            ],
        },
        {
            "partOfSpeech": "Adjective",
            "definitions": [
                {
                    "definition": (
                        'Happening every day; <a rel="mw:WikiLink" href="/wiki/daily"'
                        ' title="daily">daily</a>.'
                        ' <style data-mw-deduplicate="TemplateStyles:r90144991">'
                        ".mw-parser-output .defdate{font-size:smaller}</style>"
                    ),
                    "parsedExamples": [{"example": "the <b>quotidian</b> rhythms of the office"}],
                }
            ],
        },
    ],
    "la": [
        {
            "partOfSpeech": "Verb",
            "definitions": [{"definition": "A Latin sense that must not be shown."}],
        }
    ],
}

SYNONYMS = [{"word": "mundane"}, {"word": "everyday"}]


def parsed() -> dict:
    return parse_entry(ENTRY, "quotidian")


def test_word_for_day_is_stable_and_varies_by_date():
    day = date(2026, 7, 27)
    assert word_for_day(day) == word_for_day(day)
    assert word_for_day(day) in WORDS
    picks = {word_for_day(date(2026, 7, d)) for d in range(1, 29)}
    assert len(picks) > 20  # a month of near-unique words, not a stuck index


def test_word_for_day_attempt_moves_to_another_word():
    day = date(2026, 7, 27)
    assert word_for_day(day, 1) != word_for_day(day, 0)


def test_strip_html_drops_style_blocks_with_their_css_text():
    """The CSS inside <style> is the trap: strip only the tags and the rule text
    survives into the definition and is drawn on the panel."""
    markup = 'Dark. <style data-mw="x">.mw-parser-output .defdate{font-size:smaller}</style>'
    assert strip_html(markup) == "Dark."
    assert "font-size" not in strip_html(markup)


def test_strip_html_keeps_link_text_and_unescapes_entities():
    markup = 'Having a <a rel="mw:WikiLink" href="/wiki/loud">loud</a> sound &amp; more'
    assert strip_html(markup) == "Having a loud sound & more"


def test_strip_html_collapses_whitespace_left_by_empty_spans():
    markup = '<span class="usage-label-sense" about="#mwt18"></span> <a href="/wiki/dark">Dark</a>'
    assert strip_html(markup) == "Dark"


def test_parse_entry_takes_one_sense_per_part_of_speech_first():
    data = parsed()
    assert data["word"] == "quotidian"
    assert [s["part_of_speech"] for s in data["senses"]] == ["noun", "adjective", "noun"]
    assert data["senses"][1]["definition"] == "Happening every day; daily."
    assert data["senses"][1]["example"] == "the quotidian rhythms of the office"


def test_parse_entry_ignores_other_languages():
    for sense in parsed()["senses"]:
        assert "Latin" not in sense["definition"]


@pytest.mark.parametrize(
    "payload",
    [
        [{"word": "x"}],  # the old dictionaryapi.dev list shape
        {},
        {"en": []},
        {"la": [{"partOfSpeech": "Verb", "definitions": [{"definition": "d"}]}]},
        {"en": [{"partOfSpeech": "Noun", "definitions": [{"definition": "   "}]}]},
    ],
)
def test_parse_entry_rejects_unusable_payloads(payload):
    assert parse_entry(payload, "x") is None


def stub_client(monkeypatch, handler):
    """Point the module's httpx.AsyncClient at a mock transport. The real class is
    captured first — the patched name is what the factory would otherwise call."""
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler))
    )


def route(request: httpx.Request, *, definition: httpx.Response) -> httpx.Response:
    if request.url.host == "api.datamuse.com":
        return httpx.Response(200, json=SYNONYMS)
    return definition


def test_fetch_skips_words_the_dictionary_does_not_know(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.datamuse.com":
            return httpx.Response(200, json=SYNONYMS)
        seen.append(request.url.path.rsplit("/", 1)[-1])
        if len(seen) == 1:
            return httpx.Response(404, json={"title": "Not found."})
        return httpx.Response(200, json=ENTRY)

    stub_client(monkeypatch, handler)
    data = asyncio.run(VoxModule().fetch(AppConfig()))
    assert len(seen) == 2 and seen[0] != seen[1]
    assert data["word"] == seen[1]


def test_fetch_moves_past_a_server_error_to_another_word(monkeypatch):
    """The regression that stopped the panel: dictionaryapi.dev began answering
    522 for every uncommon word, and a 5xx aborted the whole fetch instead of
    falling through to the next candidate."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.datamuse.com":
            return httpx.Response(200, json=SYNONYMS)
        seen.append(request.url.path.rsplit("/", 1)[-1])
        if len(seen) == 1:
            return httpx.Response(522, text="")
        return httpx.Response(200, json=ENTRY)

    stub_client(monkeypatch, handler)
    data = asyncio.run(VoxModule().fetch(AppConfig()))
    assert len(seen) == 2
    assert data["word"] == seen[1]


def test_fetch_moves_past_a_transport_error(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.datamuse.com":
            return httpx.Response(200, json=SYNONYMS)
        seen.append(request.url.path.rsplit("/", 1)[-1])
        if len(seen) == 1:
            raise httpx.ConnectTimeout("boom", request=request)
        return httpx.Response(200, json=ENTRY)

    stub_client(monkeypatch, handler)
    assert asyncio.run(VoxModule().fetch(AppConfig()))["word"] == seen[1]


def test_fetch_raises_when_every_word_errors(monkeypatch):
    stub_client(monkeypatch, lambda request: httpx.Response(522, text=""))
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(VoxModule().fetch(AppConfig()))
    assert "522" in str(excinfo.value)  # the reason must reach /api/status


def test_fetch_raises_when_nothing_resolves(monkeypatch):
    stub_client(monkeypatch, lambda request: httpx.Response(404, json={}))
    with pytest.raises(RuntimeError):
        asyncio.run(VoxModule().fetch(AppConfig()))


def test_fetch_attaches_synonyms_only_when_the_panel_shows_them(monkeypatch):
    stub_client(monkeypatch, lambda r: route(r, definition=httpx.Response(200, json=ENTRY)))
    config = AppConfig()
    config.vox.show_synonyms = True
    data = asyncio.run(VoxModule().fetch(config))
    assert data["synonyms"] == ["mundane", "everyday"]

    config.vox.show_synonyms = False
    assert asyncio.run(VoxModule().fetch(config))["synonyms"] == []


def test_fetch_synonyms_drops_the_headword_and_duplicates():
    """Datamuse happily returns the word itself and repeats; neither belongs on
    a line that reads \"syn. ...\"."""
    payload = [
        {"word": "Mundane"},  # the headword back again, differently cased
        {"word": "everyday"},
        {"word": "everyday"},
        {"word": "routine"},
        {"word": "humdrum"},
        {"word": "banal"},
        {"word": "commonplace"},  # past MAX_SYNONYMS
    ]

    async def run():
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
        async with httpx.AsyncClient(transport=transport) as client:
            return await fetch_synonyms(client, "mundane")

    assert asyncio.run(run()) == ["everyday", "routine", "humdrum", "banal"]


def test_synonym_failure_does_not_fail_the_panel(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.datamuse.com":
            return httpx.Response(500, text="")
        return httpx.Response(200, json=ENTRY)

    stub_client(monkeypatch, handler)
    config = AppConfig()
    config.vox.show_synonyms = True
    data = asyncio.run(VoxModule().fetch(config))
    assert data["word"] and data["synonyms"] == []


def test_fetch_stamps_the_day_it_ran(monkeypatch):
    stub_client(monkeypatch, lambda r: route(r, definition=httpx.Response(200, json=ENTRY)))
    data = asyncio.run(VoxModule().fetch(AppConfig()))
    assert data["fetched_on"] == date.today().isoformat()


def test_render_with_data_and_without():
    module = VoxModule()
    config = AppConfig()
    config.vox.show_synonyms = True
    data = parsed()
    for size in [(400, 300), (200, 120), (800, 480)]:
        image = Image.new("RGB", size, (255, 255, 255))
        module.render(image, (0, 0, *size), data, config)
        module.render(image, (0, 0, *size), None, config)


def test_body_size_scales_with_region_and_stays_bounded():
    assert body_size(120) == BODY_MIN  # sidebar panel
    assert BODY_MIN < body_size(400) <= BODY_MAX
    assert body_size(2000) == BODY_MAX


def accent_pixels(data, config, size=(400, 300)) -> int:
    image = Image.new("RGB", size, (255, 255, 255))
    VoxModule().render(image, (0, 0, *size), data, config)
    return sum(count for count, colour in image.getcolors(1 << 16) if colour == palette.ACCENT)


def muted_pixels(data, config, size=(400, 300)) -> int:
    image = Image.new("RGB", size, (255, 255, 255))
    VoxModule().render(image, (0, 0, *size), data, config)
    return sum(count for count, colour in image.getcolors(1 << 16) if colour == palette.MUTED)


def test_part_of_speech_is_labelled_once_per_run():
    """Two senses sharing a part of speech get one heading; two parts of speech
    get two — so the run of accent-coloured pixels is strictly larger."""
    config = AppConfig()
    two_nouns = {
        **parsed(),
        "senses": [
            {"part_of_speech": "noun", "definition": "A first sense.", "example": ""},
            {"part_of_speech": "noun", "definition": "A second sense.", "example": ""},
        ],
    }
    noun_and_verb = {
        **two_nouns,
        "senses": [
            two_nouns["senses"][0],
            {**two_nouns["senses"][1], "part_of_speech": "verb"},
        ],
    }
    assert accent_pixels(two_nouns, config) < accent_pixels(noun_and_verb, config)


def test_no_part_of_speech_heading_without_room_for_its_definition():
    config = AppConfig()
    tall = accent_pixels(parsed(), config, (300, 300))
    # Only the title rule fits under the heading-height cut-off, so the panel
    # must not strand a lone "NOUN" at its foot.
    cramped = accent_pixels(parsed(), config, (300, 84))
    assert cramped < tall


def test_stale_daily_word_is_marked_and_fresh_one_is_not():
    module, config = VoxModule(), AppConfig()
    fresh = {**parsed(), "fetched_on": date.today().isoformat()}
    stale = {**parsed(), "fetched_on": (date.today() - timedelta(days=3)).isoformat()}
    assert not module.is_stale(fresh, config)
    assert module.is_stale(stale, config)
    assert muted_pixels(stale, config) > muted_pixels(fresh, config)


def test_random_mode_and_unstamped_data_are_never_stale():
    module, config = VoxModule(), AppConfig()
    old = (date.today() - timedelta(days=3)).isoformat()
    config.vox.mode = "random"
    assert not module.is_stale({**parsed(), "fetched_on": old}, config)
    config.vox.mode = "daily"
    assert not module.is_stale(parsed(), config)  # pre-upgrade cache: don't cry wolf


def test_render_stays_inside_its_region():
    module = VoxModule()
    config = AppConfig()
    config.vox.max_definitions = 4
    config.vox.show_synonyms = True
    image = Image.new("RGB", (400, 300), (255, 255, 255))
    data = {**parsed(), "synonyms": ["mundane", "everyday"], "fetched_on": "2000-01-01"}
    module.render(image, (0, 0, 400, 150), data, config)
    below = image.crop((0, 150, 400, 300))
    assert below.getcolors() == [(400 * 150, (255, 255, 255))]
