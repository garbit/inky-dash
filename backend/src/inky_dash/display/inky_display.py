"""Real Inky Impression hardware driver.

The `inky` package only installs on the Pi (behind the `pi` extra) and must never be
imported at module level anywhere reachable from tests or macOS dev — hence the lazy
import inside __init__.
"""

from __future__ import annotations

from PIL import Image


class InkyDisplay:
    def __init__(self, saturation: float = 0.5):
        from inky.auto import auto  # deliberate lazy import; see module docstring

        self._inky = auto()
        self._saturation = saturation

    @property
    def resolution(self) -> tuple[int, int]:
        return (self._inky.width, self._inky.height)

    def show(self, image: Image.Image) -> None:
        if image.size != self.resolution:
            image = image.resize(self.resolution)
        self._inky.set_image(image, saturation=self._saturation)
        self._inky.show()
