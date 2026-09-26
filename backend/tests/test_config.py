import json

import pytest

from inky_dash.config import (
    AppConfig,
    Layout,
    LayoutConfig,
    PlaylistConfig,
    PlaylistItem,
    Region,
    load_config,
    save_config,
    validate_config,
)

DEFAULT_PANELS = [
    {"slot": 0, "module": "clock"},
    {"slot": 1, "module": "weather"},
    {"slot": 2, "module": "govee"},
    {"slot": 3, "module": "calendar"},
    {"slot": 4, "module": "economist"},
]


def test_defaults():
    config = AppConfig()
    assert config.display.resolution == (800, 480)
    assert config.refresh_seconds == 600
    assert config.layout.active == "dashboard_clock"
    assert config.layout.layouts == []


def test_roundtrip(tmp_path):
    path = tmp_path / "nested" / "config.json"
    config = AppConfig()
    config.refresh_seconds = 900
    config.weather.location_name = "Newcastle"
    config.layout = LayoutConfig(
        active="mine",
        layouts=[
            Layout(id="mine", name="Mine", regions=[Region(x=0, y=0, w=25, h=30, module="clock")])
        ],
    )
    save_config(config, path)
    assert load_config(path) == config


def test_load_missing_returns_defaults(tmp_path):
    assert load_config(tmp_path / "absent.json") == AppConfig()


def _write_old_config(path, template, panels):
    data = AppConfig().model_dump(mode="json")
    data["layout"] = {"template": template, "panels": panels}
    path.write_text(json.dumps(data))


def test_migrates_default_panels_to_bare_preset(tmp_path):
    path = tmp_path / "config.json"
    _write_old_config(path, "dashboard_clock", DEFAULT_PANELS)
    config = load_config(path)
    assert config.layout.active == "dashboard_clock"
    assert config.layout.layouts == []


def test_migrates_custom_panels_to_custom_layout(tmp_path):
    path = tmp_path / "config.json"
    _write_old_config(path, "full", [{"slot": 0, "module": "clock"}])
    config = load_config(path)
    assert config.layout.active == "migrated"
    (layout,) = config.layout.layouts
    assert [r.module for r in layout.regions] == ["clock"]
    validate_config(config)


def test_migration_dedupes_repeated_module(tmp_path):
    path = tmp_path / "config.json"
    _write_old_config(
        path, "split", [{"slot": 0, "module": "clock"}, {"slot": 1, "module": "clock"}]
    )
    config = load_config(path)
    (layout,) = config.layout.layouts
    assert [r.module for r in layout.regions] == ["clock", None]


def test_migration_unknown_template_falls_back(tmp_path):
    path = tmp_path / "config.json"
    _write_old_config(path, "nope", DEFAULT_PANELS)
    config = load_config(path)
    assert config.layout.active == "dashboard_clock"
    assert config.layout.layouts == []


def test_validate_config_rejects_unknown_module():
    config = AppConfig()
    config.layout = LayoutConfig(
        active="x",
        layouts=[Layout(id="x", name="X", regions=[Region(x=0, y=0, w=10, h=10, module="nope")])],
    )
    with pytest.raises(ValueError, match="unknown module"):
        validate_config(config)


def test_validate_config_rejects_dangling_active():
    config = AppConfig(layout=LayoutConfig(active="missing"))
    with pytest.raises(ValueError, match="does not exist"):
        validate_config(config)


def test_playlist_defaults_to_off():
    config = AppConfig()
    assert config.playlist.enabled is False
    assert config.playlist.items == []


def test_validate_config_rejects_dangling_playlist_layout():
    config = AppConfig(
        playlist=PlaylistConfig(
            enabled=True,
            items=[PlaylistItem(layout="full"), PlaylistItem(layout="missing")],
        )
    )
    with pytest.raises(ValueError, match="playlist screen 2"):
        validate_config(config)


def test_validate_config_accepts_playlist_of_presets_and_custom_layouts():
    config = AppConfig(
        layout=LayoutConfig(layouts=[Layout(id="mine", name="Mine")]),
        playlist=PlaylistConfig(
            enabled=True,
            items=[PlaylistItem(layout="mine"), PlaylistItem(layout="grid", minutes=30)],
        ),
    )
    validate_config(config)


def test_validate_config_rejects_preset_shadowing():
    config = AppConfig(
        layout=LayoutConfig(active="grid", layouts=[Layout(id="grid", name="Fake grid")])
    )
    with pytest.raises(ValueError, match="clashes with a built-in preset"):
        validate_config(config)
