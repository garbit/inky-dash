"""Application configuration: Pydantic models persisted to a JSON file."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

RESOLUTIONS: dict[str, tuple[int, int]] = {
    "spectra_7_3": (800, 480),
}

# Grid constants shared with the frontend via GET /api/meta.
# 16px cells: 16 divides both 800 and 480 exactly, so cell->pixel maths is drift-free.
DEFAULT_COLS = 50
DEFAULT_ROWS = 30
MIN_REGION_CELLS = 2  # 2x2 cells = 32x32px; anything smaller is unrenderable
MAX_REGIONS = 12
MAX_PLAYLIST_ITEMS = 12


class DisplayConfig(BaseModel):
    kind: Literal["auto", "inky", "file"] = "auto"
    model: Literal["spectra_7_3"] = "spectra_7_3"
    # Passed to inky's set_image(); 0.0 = muted, 1.0 = fully saturated colours.
    saturation: float = Field(default=0.5, ge=0.0, le=1.0)

    @property
    def resolution(self) -> tuple[int, int]:
        return RESOLUTIONS[self.model]


class Region(BaseModel):
    """A rectangular area of the layout grid, optionally hosting a module."""

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=MIN_REGION_CELLS)
    h: int = Field(ge=MIN_REGION_CELLS)
    module: str | None = None  # None = intentionally blank


class Layout(BaseModel):
    """A named arrangement of regions on a cell grid."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    name: str = Field(min_length=1, max_length=60)
    cols: int = Field(default=DEFAULT_COLS, ge=4, le=100)
    rows: int = Field(default=DEFAULT_ROWS, ge=4, le=100)
    regions: list[Region] = Field(default_factory=list, max_length=MAX_REGIONS)

    @model_validator(mode="after")
    def _check_regions(self) -> Layout:
        for r in self.regions:
            if r.x + r.w > self.cols or r.y + r.h > self.rows:
                raise ValueError(
                    f"region at ({r.x},{r.y}) size {r.w}x{r.h} exceeds "
                    f"the {self.cols}x{self.rows} grid"
                )
        for i, a in enumerate(self.regions):
            for b in self.regions[i + 1 :]:
                if a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h:
                    raise ValueError(f"regions at ({a.x},{a.y}) and ({b.x},{b.y}) overlap")
        placed = [r.module for r in self.regions if r.module]
        duplicates = sorted({m for m in placed if placed.count(m) > 1})
        if duplicates:
            raise ValueError(f"module placed in more than one region: {', '.join(duplicates)}")
        return self


class LayoutConfig(BaseModel):
    active: str = "dashboard_clock"  # a preset id or the id of a custom layout
    layouts: list[Layout] = Field(default_factory=list)  # custom layouts only

    @model_validator(mode="after")
    def _check_unique_ids(self) -> LayoutConfig:
        ids = [layout.id for layout in self.layouts]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate layout id: {', '.join(duplicates)}")
        return self


class PlaylistItem(BaseModel):
    """One screen in the rotation: a layout and how long it stays on the panel."""

    layout: str  # a preset id or the id of a custom layout
    # A full Spectra refresh is ~30s of flashing, so screens are measured in
    # minutes — a one-minute dwell already means half a minute of flashing per hour.
    minutes: int = Field(default=15, ge=1, le=720)
    enabled: bool = True


class PlaylistConfig(BaseModel):
    """An ordered rotation of screens. While enabled (and holding at least one
    enabled item with a layout that still exists) it supersedes layout.active as
    the source of what the display shows."""

    enabled: bool = False
    items: list[PlaylistItem] = Field(default_factory=list, max_length=MAX_PLAYLIST_ITEMS)


class FetchConfig(BaseModel):
    """Minutes between upstream fetches for the network-backed modules (the
    local-read modules — clock, govee — have nothing worth configuring). The
    loop only fetches while composing a frame, so the effective cadence rounds
    up to the next multiple of refresh_seconds. Defaults match the historical
    per-module TTLs."""

    weather_minutes: int = Field(default=15, ge=5, le=240)
    calendar_minutes: int = Field(default=15, ge=5, le=240)
    economist_minutes: int = Field(default=30, ge=5, le=240)
    xkcd_minutes: int = Field(default=180, ge=5, le=240)
    vox_minutes: int = Field(default=180, ge=5, le=240)


class WeatherConfig(BaseModel):
    latitude: float = 51.5072
    longitude: float = -0.1276
    location_name: str = "London"
    forecast_days: int = Field(default=4, ge=1, le=7)
    # Strip of upcoming hours drawn between the current conditions and the daily
    # row. Hourly data is always fetched, so this is a pure render-time toggle
    # (the preview reflects it without waiting for the module cache to expire).
    show_hourly: bool = False
    hourly_hours: int = Field(default=8, ge=2, le=12)


class GoveeConfig(BaseModel):
    # Optional address filter; None accepts any H5075 advertisement heard.
    mac: str | None = None
    # Minimum seconds between rows written to the sensor log.
    log_interval_seconds: int = Field(default=120, ge=10)
    trend_hours: int = Field(default=24, ge=1, le=168)
    # 0 shows the live latest reading (panel redraws almost every cycle). N > 0
    # shows averages over completed N-minute windows instead, so the panel's
    # output only changes when a window completes — far fewer e-ink refreshes.
    average_minutes: int = Field(default=0, ge=0, le=180)


class EconomistConfig(BaseModel):
    sections: list[str] = Field(default_factory=lambda: ["the-world-this-week", "leaders"])
    max_items: int = Field(default=5, ge=1, le=15)
    # 0 = auto-fit column count to the panel width; 1-4 pins an explicit count.
    columns: int = Field(default=0, ge=0, le=4)
    # Max wrapped lines rendered per headline before it's truncated.
    max_lines: int = Field(default=2, ge=1, le=4)
    # Show the source section name above each headline.
    show_sections: bool = False


class CalendarConfig(BaseModel):
    # Empty list = every calendar selected (checked) in the Google Calendar UI,
    # including shared ones. Set explicit ids to pin the set instead.
    calendar_ids: list[str] = Field(default_factory=list)
    max_events: int = Field(default=6, ge=1, le=20)


class XkcdConfig(BaseModel):
    # "random" picks a fresh comic on every fetch, so the panel redraws each cycle.
    mode: Literal["latest", "random"] = "latest"
    # "threshold" keeps line art crisp pure black/white; "dither" trades crisp
    # edges for grey shading rendered as black/white speckle. Both produce only
    # palette colours, so the inky driver's quantisation passes them through.
    style: Literal["threshold", "dither"] = "threshold"
    show_title: bool = True
    show_alt: bool = False


class VoxConfig(BaseModel):
    """Word of the day. The word is drawn from a curated list bundled with the
    app; the definition comes from Wiktionary and the synonyms from Datamuse.

    ``show_phonetic`` is retained so existing configs still load, but Wiktionary's
    definition endpoint carries no IPA, so nothing reads it and the options panel
    no longer offers it."""

    # "daily" is a pure function of the date, so the frame is identical all day
    # and the loop skips the refresh; "random" picks a fresh word every fetch.
    mode: Literal["daily", "random"] = "daily"
    max_definitions: int = Field(default=2, ge=1, le=4)
    show_phonetic: bool = True
    show_part_of_speech: bool = True
    show_example: bool = True
    show_synonyms: bool = False


class PhotosConfig(BaseModel):
    """The photo frame. Images live in data/photos/ (see photos_store.py); this
    section only says which one to show and how to treat it."""

    # Which photo is up is a pure function of the clock, so the rendered frame is
    # identical between rotation boundaries and the loop skips the ~30s refresh.
    mode: Literal["shuffle", "sequential", "on_this_day"] = "shuffle"
    rotate_minutes: int = Field(default=60, ge=5, le=1440)
    # "cover" crops to fill the region; "contain" letterboxes the whole frame.
    fit: Literal["cover", "contain"] = "cover"
    # "mono" reduces to black/white speckle, which often reads better on a
    # six-colour panel than a dithered colour photo. "colour" is passed through
    # as plain RGB for the inky driver to quantise.
    style: Literal["colour", "mono"] = "colour"
    contrast: float = Field(default=1.15, ge=0.5, le=2.0)
    saturation_boost: float = Field(default=1.3, ge=0.5, le=2.0)
    # Off by default: this panel is meant to be full-bleed, edge to edge.
    show_title: bool = False
    show_caption: bool = False
    max_photos: int = Field(default=200, ge=1, le=500)
    # A Google Drive folder to mirror into the pool: the one source that keeps
    # itself up to date (drop photos in from a phone and they turn up). Accepts
    # a folder id or the URL from Drive's address bar; empty disables the sync.
    drive_folder: str = ""
    drive_sync_minutes: int = Field(default=60, ge=5, le=1440)


class MoonConfig(BaseModel):
    """The lunar panel. Everything it shows is computed locally from the clock
    (see astro.py), so there is no fetch interval to configure — and the observer
    is the weather panel's location rather than a second one to keep in sync."""

    show_phases: bool = True  # strip of the upcoming principal phases
    phase_count: int = Field(default=4, ge=1, le=4)
    show_rise_set: bool = True
    show_distance: bool = False
    twenty_four_hour: bool = True
    # Which way round the terminator goes: the southern hemisphere sees the lit
    # limb mirrored. "auto" takes it from the sign of the weather latitude.
    hemisphere: Literal["auto", "north", "south"] = "auto"


ClockFace = Literal["calendar", "big"]


class ClockConfig(BaseModel):
    # The display only refreshes every ~10 min, so this panel is date-first: a face
    # shows weekday/day/month, and time is an optional rough reference at most.
    face: ClockFace = "calendar"
    show_time: bool = False
    twenty_four_hour: bool = True  # only relevant when show_time


class AppConfig(BaseModel):
    refresh_seconds: int = Field(default=600, ge=60)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    layout: LayoutConfig = Field(default_factory=LayoutConfig)
    playlist: PlaylistConfig = Field(default_factory=PlaylistConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    govee: GoveeConfig = Field(default_factory=GoveeConfig)
    economist: EconomistConfig = Field(default_factory=EconomistConfig)
    calendar: CalendarConfig = Field(default_factory=CalendarConfig)
    clock: ClockConfig = Field(default_factory=ClockConfig)
    moon: MoonConfig = Field(default_factory=MoonConfig)
    xkcd: XkcdConfig = Field(default_factory=XkcdConfig)
    vox: VoxConfig = Field(default_factory=VoxConfig)
    photos: PhotosConfig = Field(default_factory=PhotosConfig)


def validate_config(config: AppConfig) -> None:
    """Cross-checks needing the module registry / presets, kept out of the models
    to avoid a config<->modules import cycle. Raises ValueError on failure."""
    from inky_dash.layout import PRESETS
    from inky_dash.modules import MODULES

    shadowed = sorted({layout.id for layout in config.layout.layouts} & set(PRESETS))
    if shadowed:
        raise ValueError(f"layout id clashes with a built-in preset: {', '.join(shadowed)}")
    known = {layout.id for layout in config.layout.layouts} | set(PRESETS)
    if config.layout.active not in known:
        raise ValueError(f"active layout {config.layout.active!r} does not exist")
    for position, item in enumerate(config.playlist.items, start=1):
        if item.layout not in known:
            raise ValueError(
                f"playlist screen {position} uses layout {item.layout!r}, which does not exist"
            )
    for layout in config.layout.layouts:
        for region in layout.regions:
            if region.module is not None and region.module not in MODULES:
                raise ValueError(f"unknown module {region.module!r} in layout {layout.id!r}")


def _migrate(data: dict) -> dict:
    """Convert the pre-grid schema ({template, panels}) in place; no version field —
    the old shape is detected structurally by the presence of layout.template."""
    layout = data.get("layout")
    if not isinstance(layout, dict) or "template" not in layout:
        return data
    from inky_dash.layout import PRESETS

    preset = PRESETS.get(layout.get("template")) or PRESETS["dashboard_clock"]
    # Old slot order matches preset region order. The old UI allowed one module in
    # several slots; the new schema forbids it, so keep the first occurrence only.
    assignment: dict[int, str] = {}
    seen: set[str] = set()
    for panel in sorted(layout.get("panels", []), key=lambda p: p.get("slot", 0)):
        slot, module = panel.get("slot"), panel.get("module")
        if isinstance(slot, int) and isinstance(module, str) and module not in seen:
            seen.add(module)
            assignment[slot] = module
    defaults = {i: r.module for i, r in enumerate(preset.regions) if r.module}
    if assignment == defaults:
        data["layout"] = {"active": preset.id, "layouts": []}
    else:
        regions = [
            r.model_copy(update={"module": assignment.get(i)}) for i, r in enumerate(preset.regions)
        ]
        migrated = Layout(id="migrated", name="Migrated layout", regions=regions)
        data["layout"] = {"active": "migrated", "layouts": [migrated.model_dump()]}
    return data


def config_path() -> Path:
    return Path(os.environ.get("INKY_DASH_CONFIG", "config.json"))


def data_dir() -> Path:
    path = Path(os.environ.get("INKY_DASH_DATA", "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config(path: Path | None = None) -> AppConfig:
    path = path or config_path()
    if not path.exists():
        return AppConfig()
    return AppConfig.model_validate(_migrate(json.loads(path.read_text())))


def save_config(config: AppConfig, path: Path | None = None) -> None:
    """Atomic write: a crash mid-write must never corrupt the config file."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(config.model_dump_json(indent=2))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
