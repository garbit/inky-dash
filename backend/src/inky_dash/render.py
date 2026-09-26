"""Compose a full dashboard frame from module data."""

from __future__ import annotations

import logging
from typing import Any

from PIL import Image

from inky_dash import palette
from inky_dash.config import AppConfig
from inky_dash.layout import region_rect, resolve_layout
from inky_dash.modules import MODULES
from inky_dash.modules.common import panel_draw, placeholder

log = logging.getLogger(__name__)


def compose(
    config: AppConfig,
    data: dict[str, dict[str, Any]],
    size: tuple[int, int],
    layout_id: str | None = None,
) -> Image.Image:
    """Draw one frame. layout_id names the layout to render (a playlist screen or
    a preview target); the configured active layout is used when it is omitted."""
    image = Image.new("RGB", size, palette.WHITE)
    draw = panel_draw(image)
    layout = resolve_layout(config.layout, layout_id)
    for region in layout.regions:
        if region.module is None:
            continue
        rect = region_rect(region, layout.cols, layout.rows, size)
        module = MODULES.get(region.module)
        if module is None:
            placeholder(draw, rect, region.module, "module not available yet")
            continue
        try:
            module.render(image, rect, data.get(region.module), config)
        except Exception:
            log.exception("render failed for module %s", region.module)
            placeholder(draw, rect, region.module, "render error - see server logs")
    return image
