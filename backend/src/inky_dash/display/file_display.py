"""Simulator display: writes frames to a PNG so development needs no hardware."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image


class FileDisplay:
    def __init__(self, resolution: tuple[int, int], path: Path | None = None):
        self._resolution = resolution
        self.path = path or Path(os.environ.get("INKY_DASH_OUTPUT", "output/display.png"))

    @property
    def resolution(self) -> tuple[int, int]:
        return self._resolution

    def show(self, image: Image.Image) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        image.save(self.path)
