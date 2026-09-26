from datetime import datetime

import pytest
from PIL import Image

from inky_dash import palette
from inky_dash.config import AppConfig, ClockConfig, load_config
from inky_dash.modules.clock import ClockModule, strip_fonts
from inky_dash.modules.clock_faces import FACES, format_time
from inky_dash.modules.common import panel_draw

NOW = datetime(2026, 7, 26, 10, 8)

PALETTE = {
    palette.BLACK,
    palette.WHITE,
    palette.RED,
    palette.YELLOW,
    palette.GREEN,
    palette.BLUE,
}


@pytest.mark.parametrize(
    "hour,minute,twenty_four,expected",
    [
        (9, 5, True, "09:05"),
        (9, 5, False, "9:05 am"),
        (0, 0, False, "12:00 am"),
        (12, 0, False, "12:00 pm"),
        (23, 59, True, "23:59"),
        (13, 7, False, "1:07 pm"),
    ],
)
def test_format_time(hour, minute, twenty_four, expected):
    assert format_time(datetime(2026, 7, 26, hour, minute), twenty_four) == expected


def test_clock_config_defaults():
    clock = AppConfig().clock
    assert clock.face == "calendar"
    assert clock.show_time is False
    assert clock.twenty_four_hour is True


def test_config_without_clock_key_loads_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"refresh_seconds": 600}')
    config = load_config(path)
    assert config.clock == ClockConfig()


def test_strip_fonts_fit_side_by_side():
    draw = panel_draw(Image.new("RGB", (800, 480), palette.WHITE))
    date_text = "Sunday 26 July"
    _, _, (dw, _), (tw, _) = strip_fonts(draw, date_text, "10:08", 776, 40)
    assert dw + tw <= 776
    # date-only: the whole width is available to the date
    _, _, (dw2, _), (tw2, _) = strip_fonts(draw, date_text, "", 776, 40)
    assert tw2 == 0
    assert dw2 <= 776


@pytest.mark.parametrize("face", list(FACES))
@pytest.mark.parametrize("size", [(150, 150), (300, 200), (200, 400), (400, 480), (800, 480)])
@pytest.mark.parametrize("show_time", [False, True])
def test_faces_render(face, size, show_time):
    w, h = size
    image = Image.new("RGB", (w, h), palette.WHITE)
    draw = panel_draw(image)
    FACES[face](draw, (0, 0, w, h), NOW, ClockConfig(face=face, show_time=show_time))
    pixels = set(image.get_flattened_data())
    assert pixels != {palette.WHITE}, f"{face} at {size} drew nothing"
    unexpected = {p for p in pixels if p not in PALETTE}
    assert not unexpected, f"{face} at {size} used non-palette colours: {unexpected}"


def test_dispatch_slim_strip_vs_face():
    module = ClockModule()

    strip = Image.new("RGB", (800, 64), palette.WHITE)
    module.render(strip, (0, 0, 800, 64), None, AppConfig())
    assert set(strip.get_flattened_data()) != {palette.WHITE}

    face = Image.new("RGB", (400, 400), palette.WHITE)
    module.render(face, (0, 0, 400, 400), None, AppConfig())
    assert set(face.get_flattened_data()) != {palette.WHITE}


def test_strip_show_time_changes_render():
    module = ClockModule()
    without = Image.new("RGB", (800, 64), palette.WHITE)
    module.render(without, (0, 0, 800, 64), None, AppConfig(clock=ClockConfig(show_time=False)))
    with_time = Image.new("RGB", (800, 64), palette.WHITE)
    module.render(with_time, (0, 0, 800, 64), None, AppConfig(clock=ClockConfig(show_time=True)))
    assert list(without.get_flattened_data()) != list(with_time.get_flattened_data())


def test_unknown_face_falls_back_without_crashing():
    module = ClockModule()
    image = Image.new("RGB", (400, 400), palette.WHITE)
    config = AppConfig()
    # bypass model validation to simulate a stale/corrupt config value
    object.__setattr__(config.clock, "face", "does-not-exist")
    module.render(image, (0, 0, 400, 400), None, config)
    assert set(image.get_flattened_data()) != {palette.WHITE}
