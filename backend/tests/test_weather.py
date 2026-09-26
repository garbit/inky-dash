from PIL import Image

from inky_dash import palette
from inky_dash.config import AppConfig
from inky_dash.modules.common import panel_draw
from inky_dash.modules.weather import (
    HUMIDITY_STEP,
    WIND_STEP,
    WeatherModule,
    describe_code,
    parse_forecast,
    parse_hours,
    to_step,
)
from inky_dash.modules.weather_icons import draw_weather_icon

HOURS = [f"2026-07-25T{h:02d}:00" for h in range(24)]

PAYLOAD = {
    "current": {
        "time": "2026-07-25T09:30",
        "temperature_2m": 18.4,
        "apparent_temperature": 17.1,
        "relative_humidity_2m": 62,
        "weather_code": 61,
        "wind_speed_10m": 14.2,
    },
    "daily": {
        "time": ["2026-07-25", "2026-07-26"],
        "weather_code": [61, 3],
        "temperature_2m_max": [19.5, 22.0],
        "temperature_2m_min": [12.1, 13.4],
        "precipitation_probability_max": [80, 20],
    },
    "hourly": {
        "time": HOURS,
        "weather_code": [3] * 24,
        "temperature_2m": [10.0 + i * 0.5 for i in range(24)],
        "precipitation_probability": [i * 2 for i in range(24)],
    },
}


def test_parse_forecast():
    data = parse_forecast(PAYLOAD)
    assert data["temp"] == 18.4
    assert data["code"] == 61
    assert len(data["days"]) == 2
    assert data["days"][0]["weekday"] == "Sat"
    assert data["days"][1]["max"] == 22.0
    assert data["days"][0]["precip_pct"] == 80


def test_parse_hours_starts_with_the_hour_in_progress():
    hours = parse_hours(PAYLOAD)  # the payload's current time is 09:30
    assert hours[0]["hour"] == "09"
    assert hours[0]["temp"] == 14.5
    assert hours[0]["precip_pct"] == 18
    assert len(hours) == 15  # 09:00 to 23:00


def test_parse_hours_on_the_hour_keeps_that_hour():
    payload = {**PAYLOAD, "current": {**PAYLOAD["current"], "time": "2026-07-25T09:00"}}
    assert parse_hours(payload)[0]["hour"] == "09"


def test_parse_hours_without_hourly_data():
    assert parse_hours({"current": PAYLOAD["current"]}) == []
    assert parse_forecast({k: v for k, v in PAYLOAD.items() if k != "hourly"})["hours"] == []


def test_render_hourly_strip_keeps_the_daily_row():
    """The default layout gives weather a short panel; both strips must survive."""
    config = AppConfig()
    config.weather.show_hourly = True
    data = parse_forecast(PAYLOAD)
    rect = (0, 64, 496, 272)  # weather region of the dashboard_clock preset

    def render(**overrides) -> Image.Image:
        for key, value in overrides.items():
            setattr(config.weather, key, value)
        image = Image.new("RGB", (800, 480), palette.WHITE)
        WeatherModule().render(image, rect, data, config)
        return image

    with_hourly = render(show_hourly=True)
    without_hourly = render(show_hourly=False)
    assert with_hourly.tobytes() != without_hourly.tobytes()
    # the weekday labels of the daily row are still drawn in the bottom of the panel
    bottom = (0, 64 + 272 - 48, 496, 48)
    assert set(with_hourly.crop(_box(bottom)).get_flattened_data()) != {palette.WHITE}


def test_days_form_picks_the_richest_that_fits():
    """Height and column width are independent constraints; each form covers a case."""
    draw = panel_draw(Image.new("RGB", (800, 480), palette.WHITE))
    days = parse_forecast(PAYLOAD)["days"] * 3  # six columns, as a week-ish forecast
    form = WeatherModule()._days_form

    assert form(draw, days, 776, 100)[0] == "stacked"  # wide and tall
    assert form(draw, days, 776, 50)[0] == "bars"  # too short to stack: range bars
    assert form(draw, days, 776, 30)[0] == "line"  # wide but short: flatten onto a line
    assert form(draw, days, 376, 45)[0] == "pair"  # too narrow for a line, tall enough to stack
    assert form(draw, days, 776, 20)[0] == "text"  # only room for one line of text
    assert form(draw, days, 376, 20)[0] == "none"  # neither fits
    assert form(draw, [], 776, 100) == ("none", 0)


def test_render_days_bars_draws_comparable_bars():
    """Bar geometry must sit on one shared scale: the hottest day's segment ends at
    the track's right edge, the coldest day's starts at its left edge."""
    module = WeatherModule()
    days = parse_forecast(PAYLOAD)["days"]  # maxes 19.5 / 22.0, mins 12.1 / 13.4
    image = Image.new("RGB", (800, 480), palette.WHITE)
    draw = panel_draw(image)
    module._render_days_bars(draw, days, (0, 0, 400, 46), 200)
    pixels = set(image.get_flattened_data())
    assert palette.YELLOW in pixels  # the filled range segments
    assert palette.MUTED in pixels  # the tracks and low temps

    # day 2 spans the full range, so its segment is wider than day 1's
    def seg_width(x0: int, x1: int) -> int:
        row = [image.getpixel((px, 33)) for px in range(x0, x1)]
        return sum(1 for p in row if p in (palette.YELLOW, palette.BLACK))

    assert seg_width(200, 400) > seg_width(0, 200)


def test_hourly_rain_chance_survives_narrow_columns():
    """12 hours across 776px leaves ~64px columns — too tight for rain chance at
    full size. It must drop to the smaller font, not disappear."""
    module = WeatherModule()
    hours = parse_forecast(PAYLOAD)["hours"][:12]

    def render(hour_list: list[dict]) -> bytes:
        image = Image.new("RGB", (800, 480), palette.WHITE)
        module._render_hours(panel_draw(image), hour_list, (0, 0, 776, 42))
        return image.tobytes()

    stripped = [dict(hour, precip_pct=None) for hour in hours]
    assert render(hours) != render(stripped)  # the % column is actually drawn


def test_render_hourly_ignored_on_a_panel_too_small_for_it():
    """Too small to draw the strip must also mean the header isn't shrunk for it."""
    data = parse_forecast(PAYLOAD)
    rect = (0, 0, 200, 160)

    def render(show_hourly: bool) -> Image.Image:
        config = AppConfig()
        config.weather.show_hourly = show_hourly
        image = Image.new("RGB", (800, 480), palette.WHITE)
        WeatherModule().render(image, rect, data, config)
        return image

    assert render(True).tobytes() == render(False).tobytes()


def _box(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x, y, w, h = rect
    return (x, y, x + w, y + h)


def test_metadata_numbers_are_rounded_to_a_step():
    """Humidity and wind drift a point or two every fetch; at full precision they
    alone re-flash the panel all day. 62% and 14.2 km/h must land on the steps."""
    assert to_step(62, HUMIDITY_STEP) == 60
    assert to_step(14.2, WIND_STEP) == 15
    # values inside one step render identically, so the frame hash skips the show
    module = WeatherModule()
    config = AppConfig()
    config.weather.show_hourly = False

    def render(humidity: int, wind: float) -> bytes:
        data = parse_forecast(PAYLOAD) | {"humidity": humidity, "wind_kmh": wind}
        image = Image.new("RGB", (800, 480), palette.WHITE)
        module.render(image, (0, 64, 496, 272), data, config)
        return image.tobytes()

    assert render(62, 14.2) == render(59, 13.0)  # both round to 60% / 15 km/h
    assert render(62, 14.2) != render(52, 14.2)  # a real change still shows


def test_describe_code():
    assert describe_code(0) == "Clear"
    assert describe_code(61) == "Rain"
    assert describe_code(95) == "Thunderstorm"
    assert describe_code(42) == "—"


def test_weather_icons_draw_for_all_codes():
    codes = [0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67]
    codes += [71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99, 42]  # 42 = unknown
    palette_colours = {
        palette.BLACK,
        palette.WHITE,
        palette.RED,
        palette.YELLOW,
        palette.GREEN,
        palette.BLUE,
    }
    for size in (20, 32, 80):
        for code in codes:
            image = Image.new("RGB", (size, size), palette.WHITE)
            draw_weather_icon(panel_draw(image), code, 0, 0, size)
            pixels = set(image.get_flattened_data())
            assert pixels != {palette.WHITE}, f"code {code} at {size}px drew nothing"
            unexpected = {p for p in pixels if p not in palette_colours}
            assert not unexpected, f"code {code} used non-palette colours: {unexpected}"
