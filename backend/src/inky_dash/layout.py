"""Grid-based layouts: built-in presets plus geometry helpers.

A layout divides the panel into rectangular regions on a coarse cell grid
(default 50x30 = 16px square cells at 800x480); each region optionally hosts a
module. The presets below are the former fixed templates expressed as data, with
their historical default module assignments baked in so any preset is renderable
the moment it becomes active.
"""

from __future__ import annotations

import logging

from inky_dash.config import Layout, LayoutConfig, Region

log = logging.getLogger(__name__)

Rect = tuple[int, int, int, int]  # x, y, width, height


def _preset(id: str, name: str, regions: list[tuple[int, int, int, int, str | None]]) -> Layout:
    return Layout(
        id=id,
        name=name,
        regions=[Region(x=x, y=y, w=w, h=h, module=module) for x, y, w, h, module in regions],
    )


# Region order matches the old slots() order — config migration maps old slot
# indices onto these positions. On the 50x30 grid one cell is 16px, so e.g. the
# 64px clock header is 4 rows and the 496px (62%) main column is 31 cols.
PRESETS: dict[str, Layout] = {
    p.id: p
    for p in [
        _preset("full", "Full screen", [(0, 0, 50, 30, "weather")]),
        _preset(
            "split",
            "Split",
            [(0, 0, 25, 30, "weather"), (25, 0, 25, 30, "calendar")],
        ),
        _preset(
            "main_sidebar",
            "Main + sidebar",
            [
                (0, 0, 31, 30, "weather"),
                (31, 0, 19, 15, "govee"),
                (31, 15, 19, 15, "calendar"),
            ],
        ),
        _preset(
            "grid",
            "Grid",
            [
                (0, 0, 25, 15, "weather"),
                (25, 0, 25, 15, "govee"),
                (0, 15, 25, 15, "calendar"),
                (25, 15, 25, 15, "economist"),
            ],
        ),
        _preset(
            "dashboard",
            "Dashboard",
            [
                (0, 0, 31, 21, "weather"),
                (31, 0, 19, 10, "govee"),
                (31, 10, 19, 11, "calendar"),
                (0, 21, 50, 9, "economist"),
            ],
        ),
        _preset(
            "dashboard_clock",
            "Dashboard + clock",
            [
                (0, 0, 50, 4, "clock"),
                (0, 4, 31, 17, "weather"),
                (31, 4, 19, 8, "govee"),
                (31, 12, 19, 9, "calendar"),
                (0, 21, 50, 9, "economist"),
            ],
        ),
        # Photo frame presets. The photos panel draws edge to edge (no title bar
        # by default), so "photo_full" turns the whole panel into a picture.
        _preset("photo_full", "Photo frame", [(0, 0, 50, 30, "photos")]),
        _preset(
            "photo_clock",
            "Photo + clock",
            [(0, 0, 50, 26, "photos"), (0, 26, 50, 4, "clock")],
        ),
    ]
}

FALLBACK_PRESET = "dashboard_clock"


def region_rect(region: Region, cols: int, rows: int, size: tuple[int, int]) -> Rect:
    """Convert grid cells to a pixel rect. Edges are computed independently so
    adjacent regions stay seam-free even when cells don't divide the panel evenly."""
    w, h = size
    x0 = region.x * w // cols
    y0 = region.y * h // rows
    x1 = (region.x + region.w) * w // cols
    y1 = (region.y + region.h) * h // rows
    return (x0, y0, x1 - x0, y1 - y0)


def find_layout(config: LayoutConfig, layout_id: str) -> Layout | None:
    """Look an id up across custom layouts and presets (preset ids are reserved,
    so custom layouts shadow nothing). None if the id dangles."""
    for layout in config.layouts:
        if layout.id == layout_id:
            return layout
    return PRESETS.get(layout_id)


def resolve_layout(config: LayoutConfig, layout_id: str | None = None) -> Layout:
    """The layout to render — config.active, or an explicit id (a playlist screen,
    or the layout a preview asks for). A dangling id falls back rather than
    raising: the render loop must never die over a config edit."""
    layout_id = layout_id or config.active
    layout = find_layout(config, layout_id)
    if layout is not None:
        return layout
    log.warning("layout %r not found; falling back to %s", layout_id, FALLBACK_PRESET)
    return PRESETS[FALLBACK_PRESET]
