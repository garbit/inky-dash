from PIL import Image

from inky_dash import palette
from inky_dash.config import AppConfig
from inky_dash.modules.common import panel_draw
from inky_dash.modules.moon import MoonModule, draw_moon, southern_view

SIZES = [
    (800, 480),
    (400, 300),
    (304, 240),
    (300, 190),
    (200, 300),
    (160, 120),
    (60, 40),
    (800, 64),  # header band
    (240, 50),  # band too narrow for the phase name
]


def disc(illumination: float, waxing: bool, *, southern: bool = False) -> Image.Image:
    image = Image.new("RGB", (80, 80), palette.WHITE)
    draw_moon(panel_draw(image), 40, 40, 36, illumination, waxing, southern=southern)
    return image


def lit_columns(image: Image.Image) -> tuple[int, int]:
    """Lit pixels either side of the disc's centre line."""
    left = right = 0
    for x in range(image.width):
        for y in range(image.height):
            if image.getpixel((x, y)) == palette.YELLOW:
                if x < image.width // 2:
                    left += 1
                else:
                    right += 1
    return left, right


def test_new_moon_is_dark_and_full_moon_is_lit():
    assert lit_columns(disc(0.0, True)) == (0, 0)
    left, right = lit_columns(disc(1.0, True))
    assert left > 0 and right > 0


def test_waxing_moon_is_lit_on_the_right_and_waning_on_the_left():
    left, right = lit_columns(disc(0.25, True))
    assert right > left * 10
    left, right = lit_columns(disc(0.25, False))
    assert left > right * 10


def test_southern_hemisphere_mirrors_the_terminator():
    northern = lit_columns(disc(0.25, True))
    southern = lit_columns(disc(0.25, True, southern=True))
    assert southern == (northern[1], northern[0])


def test_crescent_is_smaller_than_gibbous():
    crescent = sum(lit_columns(disc(0.2, True)))
    half = sum(lit_columns(disc(0.5, True)))
    gibbous = sum(lit_columns(disc(0.8, True)))
    assert crescent < half < gibbous


def test_render_at_every_size():
    module, config = MoonModule(), AppConfig()
    config.moon.show_distance = True
    for size in SIZES:
        image = Image.new("RGB", size, palette.WHITE)
        module.render(image, (0, 0, *size), None, config)


def test_render_with_options_off():
    module, config = MoonModule(), AppConfig()
    config.moon.show_phases = False
    config.moon.show_rise_set = False
    config.moon.twenty_four_hour = False
    for size in SIZES:
        image = Image.new("RGB", size, palette.WHITE)
        module.render(image, (0, 0, *size), None, config)


def test_render_stays_inside_its_region():
    module, config = MoonModule(), AppConfig()
    config.moon.show_distance = True
    image = Image.new("RGB", (400, 400), palette.WHITE)
    module.render(image, (0, 0, 400, 240), None, config)
    below = image.crop((0, 240, 400, 400))
    assert below.getcolors() == [(400 * 160, palette.WHITE)]


def test_a_slim_band_skips_the_title_bar_and_stays_in_its_region():
    """A header/footer strip has no room for a title rule and a panel under it,
    so it gets the one-line form instead — and must not spill into its neighbour."""
    module, config = MoonModule(), AppConfig()
    config.moon.show_distance = True
    image = Image.new("RGB", (800, 200), palette.WHITE)
    module.render(image, (0, 0, 800, 64), None, config)
    assert palette.RED not in {colour for _, colour in image.getcolors(maxcolors=1 << 16)}
    below = image.crop((0, 64, 800, 200))
    assert below.getcolors() == [(800 * 136, palette.WHITE)]


def test_render_uses_only_palette_colours():
    module, config = MoonModule(), AppConfig()
    image = Image.new("RGB", (400, 300), palette.WHITE)
    module.render(image, (0, 0, 400, 300), None, config)
    assert {colour for _, colour in image.getcolors(maxcolors=1 << 16)} <= set(palette.COLOURS)


def test_hemisphere_auto_follows_the_weather_location():
    """A user who set their coordinates and never thought about the moon again
    still gets the disc the right way round."""
    config = AppConfig()
    assert southern_view(config.moon, 51.5072) is False
    assert southern_view(config.moon, -33.8688) is True
    config.moon.hemisphere = "north"
    assert southern_view(config.moon, -33.8688) is False
    config.moon.hemisphere = "south"
    assert southern_view(config.moon, 51.5072) is True
