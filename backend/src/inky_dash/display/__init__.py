from __future__ import annotations

import logging
import os

from inky_dash.config import DisplayConfig

from .base import Display
from .file_display import FileDisplay

log = logging.getLogger(__name__)


def create_display(config: DisplayConfig) -> Display:
    """Build a display from config; INKY_DASH_DISPLAY overrides the configured kind."""
    kind = os.environ.get("INKY_DASH_DISPLAY", config.kind)
    if kind == "file":
        return FileDisplay(config.resolution)
    if kind == "inky":
        from .inky_display import InkyDisplay

        return InkyDisplay(saturation=config.saturation)
    # auto: hardware if present, simulator otherwise
    try:
        from .inky_display import InkyDisplay

        display = InkyDisplay(saturation=config.saturation)
        log.info("using Inky hardware display %s", display.resolution)
        return display
    except Exception as exc:
        log.info("no Inky hardware (%s); falling back to file simulator", exc)
        return FileDisplay(config.resolution)
