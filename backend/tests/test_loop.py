import asyncio

import pytest

from inky_dash.config import PlaylistConfig, PlaylistItem
from inky_dash.loop import Dashboard


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("INKY_DASH_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("INKY_DASH_OUTPUT", str(tmp_path / "display.png"))
    monkeypatch.setenv("INKY_DASH_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("INKY_DASH_DISPLAY", "file")
    return Dashboard()


def test_unchanged_frame_skips_display_refresh(dashboard, monkeypatch):
    """A Spectra refresh is 30s of flashing; identical frames must not re-show."""
    shows: list[object] = []
    monkeypatch.setattr(dashboard.display, "show", shows.append)

    async def no_fetch() -> None:  # keep frames deterministic: no live module data
        return None

    monkeypatch.setattr(dashboard, "refresh_data", no_fetch)

    async def scenario() -> None:
        for _ in range(3):
            await dashboard._tick()
        assert len(shows) == 1  # first frame shown, identical repeats skipped
        dashboard.force_refresh()  # the refresh-now button still flashes the panel
        await dashboard._tick()
        assert len(shows) == 2
        assert dashboard._force_show is False  # consumed, not sticky

    asyncio.run(scenario())


def test_fetch_interval_comes_from_config(dashboard, monkeypatch):
    fetches: list[str] = []

    class FakeWeather:
        ttl = 900  # module default, overridden by config.fetch

        async def fetch(self, config):
            fetches.append("weather")
            return {"fake": True}

    monkeypatch.setattr("inky_dash.loop.MODULES", {"weather": FakeWeather()})
    monkeypatch.setattr("inky_dash.loop.time.monotonic", lambda: 10_000.0)
    dashboard.data["weather"] = {"fake": True}
    dashboard._fetched_at["weather"] = 10_000.0 - 20 * 60  # fetched 20 min ago

    dashboard.config.fetch.weather_minutes = 30
    asyncio.run(dashboard.ensure_data(dashboard.config, ["weather"]))
    assert fetches == []  # 20 min old, 30 min interval: still fresh

    dashboard.config.fetch.weather_minutes = 15
    asyncio.run(dashboard.ensure_data(dashboard.config, ["weather"]))
    assert fetches == ["weather"]  # same age is stale under a 15 min interval


def test_fetch_interval_change_keeps_data_cache(dashboard):
    dashboard._fetched_at = {"weather": 123.0}
    updated = dashboard.config.model_copy(deep=True)
    updated.fetch.weather_minutes = 60
    dashboard.update_config(updated)
    assert dashboard._fetched_at == {"weather": 123.0}


def test_layout_only_save_preserves_fetch_cache(dashboard):
    dashboard._fetched_at = {"weather": 123.0}
    updated = dashboard.config.model_copy(deep=True)
    updated.layout.active = "grid"
    dashboard.update_config(updated)
    assert dashboard._fetched_at == {"weather": 123.0}


def test_module_setting_change_clears_fetch_cache(dashboard):
    dashboard._fetched_at = {"weather": 123.0}
    updated = dashboard.config.model_copy(deep=True)
    updated.weather.location_name = "Newcastle"
    dashboard.update_config(updated)
    assert dashboard._fetched_at == {}


def test_active_module_ids_follow_active_layout(dashboard):
    assert dashboard.active_module_ids() == ["clock", "weather", "govee", "calendar", "economist"]
    updated = dashboard.config.model_copy(deep=True)
    updated.layout.active = "full"
    dashboard.update_config(updated)
    assert dashboard.active_module_ids() == ["weather"]


# ---- playlist rotation ----


def set_playlist(dashboard, *items, enabled=True):
    updated = dashboard.config.model_copy(deep=True)
    updated.playlist = PlaylistConfig(enabled=enabled, items=list(items))
    dashboard.update_config(updated)


def test_playlist_advances_when_the_screen_time_is_up(dashboard, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("inky_dash.loop.time.monotonic", lambda: clock[0])
    set_playlist(
        dashboard,
        PlaylistItem(layout="full", minutes=5),
        PlaylistItem(layout="grid", minutes=10),
    )

    dashboard._advance_if_due()  # first tick starts the first screen's dwell
    assert dashboard.current_layout_id() == "full"

    clock[0] += 4 * 60
    dashboard._advance_if_due()
    assert dashboard.current_layout_id() == "full"  # 4 of its 5 minutes spent

    clock[0] += 60
    dashboard._advance_if_due()
    assert dashboard.current_layout_id() == "grid"

    clock[0] += 10 * 60  # each screen keeps its own dwell, then it wraps
    dashboard._advance_if_due()
    assert dashboard.current_layout_id() == "full"


def test_playlist_skips_disabled_and_dangling_screens(dashboard):
    set_playlist(
        dashboard,
        PlaylistItem(layout="full", minutes=5, enabled=False),
        PlaylistItem(layout="gone", minutes=5),  # layout deleted out from under it
        PlaylistItem(layout="grid", minutes=5),
    )
    assert [i.layout for i in dashboard.playlist_screens()] == ["grid"]
    assert dashboard.current_layout_id() == "grid"


def test_playlist_falls_back_to_the_active_layout_when_it_has_no_screens(dashboard):
    """A playlist that is off, empty or entirely dangling must never blank the panel."""
    set_playlist(dashboard, PlaylistItem(layout="full", minutes=5), enabled=False)
    assert dashboard.current_layout_id() == "dashboard_clock"
    assert dashboard.playlist_status() is None

    set_playlist(dashboard, PlaylistItem(layout="gone", minutes=5))
    assert dashboard.current_layout_id() == "dashboard_clock"


def test_playlist_screen_shortens_the_loop_wait(dashboard, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("inky_dash.loop.time.monotonic", lambda: clock[0])
    assert dashboard._wait_seconds() == 600  # refresh_seconds, no playlist

    set_playlist(dashboard, PlaylistItem(layout="full", minutes=2))
    dashboard._advance_if_due()
    assert dashboard._wait_seconds() == 120  # wake when the screen is up, not in 10 min

    clock[0] += 119.5
    assert dashboard._wait_seconds() == 1.0  # floored, never a busy loop

    # A dwell longer than the refresh cadence still ticks for fresh data.
    set_playlist(dashboard, PlaylistItem(layout="full", minutes=60))
    dashboard._advance_if_due()
    assert dashboard._wait_seconds() == 600


def test_next_screen_skips_ahead_and_forces_a_show(dashboard):
    set_playlist(
        dashboard,
        PlaylistItem(layout="full", minutes=60),
        PlaylistItem(layout="grid", minutes=60),
    )
    dashboard._advance_if_due()
    dashboard.next_screen()
    assert dashboard.current_layout_id() == "grid"
    assert dashboard._force_show is True
    # the skip restarts the dwell, so the next tick must not advance again
    dashboard._advance_if_due()
    assert dashboard.current_layout_id() == "grid"


def test_module_ids_and_frames_follow_the_current_screen(dashboard):
    set_playlist(
        dashboard,
        PlaylistItem(layout="full", minutes=5),
        PlaylistItem(layout="split", minutes=5),
    )
    assert dashboard.active_module_ids() == ["weather"]
    first = dashboard.render_frame()
    dashboard.next_screen()
    assert dashboard.active_module_ids() == ["weather", "calendar"]
    assert dashboard.render_frame().tobytes() != first.tobytes()


def test_playlist_edit_restarts_the_rotation(dashboard):
    set_playlist(
        dashboard,
        PlaylistItem(layout="full", minutes=5),
        PlaylistItem(layout="grid", minutes=5),
    )
    dashboard.next_screen()
    assert dashboard.current_layout_id() == "grid"
    set_playlist(
        dashboard,
        PlaylistItem(layout="full", minutes=8),
        PlaylistItem(layout="grid", minutes=5),
    )
    assert dashboard.current_layout_id() == "full"


def test_playlist_edit_keeps_the_fetch_cache(dashboard):
    dashboard._fetched_at = {"weather": 123.0}
    set_playlist(dashboard, PlaylistItem(layout="full", minutes=5))
    assert dashboard._fetched_at == {"weather": 123.0}
