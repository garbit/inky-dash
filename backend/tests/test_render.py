from inky_dash.config import AppConfig, Layout, LayoutConfig, Region
from inky_dash.render import compose

SIZE = (800, 480)


def test_compose_skips_blank_regions():
    config = AppConfig()
    config.layout = LayoutConfig(
        active="b",
        layouts=[
            Layout(
                id="b",
                name="Blanks",
                regions=[
                    Region(x=0, y=0, w=25, h=30, module="clock"),
                    Region(x=25, y=0, w=25, h=30, module=None),
                ],
            )
        ],
    )
    image = compose(config, {}, SIZE)
    assert image.size == SIZE


def test_compose_unknown_module_renders_placeholder():
    config = AppConfig()
    config.layout = LayoutConfig(
        active="u",
        layouts=[
            Layout(id="u", name="U", regions=[Region(x=0, y=0, w=50, h=30, module="mystery")])
        ],
    )
    image = compose(config, {}, SIZE)  # must not raise
    assert image.size == SIZE
