from io import BytesIO

from PIL import Image

from inky_dash.config import AppConfig
from inky_dash.modules.xkcd import XkcdModule, pick_random, prepare_image


class FakeRng:
    def __init__(self, values):
        self.values = list(values)

    def randint(self, a, b):
        return self.values.pop(0)


def comic_png(size=(40, 20)) -> bytes:
    comic = Image.new("L", size, 255)
    for px in range(size[0]):
        comic.putpixel((px, size[1] // 2), 0)
    buf = BytesIO()
    comic.save(buf, format="PNG")
    return buf.getvalue()


def test_pick_random_skips_comic_404():
    assert pick_random(1000, FakeRng([404, 7])) == 7


def test_prepare_image_fits_box_and_is_bilevel():
    result = prepare_image(comic_png((40, 20)), (30, 30), "threshold")
    assert result.width <= 30 and result.height <= 30
    assert {colour for _, colour in result.getcolors()} <= {(0, 0, 0), (255, 255, 255)}


def test_prepare_image_dither_is_bilevel():
    result = prepare_image(comic_png(), (30, 30), "dither")
    assert {colour for _, colour in result.getcolors()} <= {(0, 0, 0), (255, 255, 255)}


def test_prepare_image_caps_upscale_at_2x():
    result = prepare_image(comic_png((10, 10)), (500, 500), "threshold")
    assert result.size == (20, 20)


def test_render_with_data_and_without():
    module = XkcdModule()
    config = AppConfig()
    config.xkcd.show_alt = True
    image = Image.new("RGB", (400, 300), (255, 255, 255))
    data = {"num": 353, "title": "Python", "alt": "I wrote 20 short programs", "image": comic_png()}
    module.render(image, (0, 0, 400, 300), data, config)
    module.render(image, (0, 0, 400, 300), None, config)
