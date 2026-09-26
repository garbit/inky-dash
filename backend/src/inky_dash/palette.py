"""The six colours of the Inky Impression Spectra panel.

Renderers draw plain RGB with these; the inky driver quantises/dithers on show().
"""

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (200, 30, 30)
YELLOW = (220, 180, 0)
GREEN = (30, 120, 60)
BLUE = (30, 60, 180)

ACCENT = RED
# Secondary text. The panel has no grey — a mid-grey RGB dithers into speckle and
# reads as blur, so muted text must be a solid palette colour.
MUTED = BLUE

COLOURS = (BLACK, WHITE, RED, YELLOW, GREEN, BLUE)


def simulate(image):
    """Approximate what the panel will make of a frame, for the web preview only.

    Panels of text are already palette-exact, but a photograph looks nothing like
    itself once the driver has dithered it into six colours — the browser preview
    would otherwise flatter it badly. This is an approximation: the real driver
    applies its own saturation LUT inside set_image(), which is why the display
    path still hands over plain un-quantised RGB.
    """
    from PIL import Image

    reference = Image.new("P", (1, 1))
    flat = [channel for colour in COLOURS for channel in colour]
    reference.putpalette(flat + [0] * (768 - len(flat)))
    return image.quantize(
        colors=len(COLOURS), palette=reference, dither=Image.Dither.FLOYDSTEINBERG
    ).convert("RGB")
