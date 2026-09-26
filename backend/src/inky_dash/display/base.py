from __future__ import annotations

from typing import Protocol, runtime_checkable

from PIL import Image


@runtime_checkable
class Display(Protocol):
    @property
    def resolution(self) -> tuple[int, int]: ...

    def show(self, image: Image.Image) -> None:
        """Push a full-resolution RGB image to the display. May block for ~30s on hardware."""
        ...
