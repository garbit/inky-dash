import pytest
from pydantic import ValidationError

from inky_dash.config import Layout, LayoutConfig, Region
from inky_dash.layout import PRESETS, region_rect, resolve_layout

SIZE = (800, 480)


@pytest.mark.parametrize("preset", PRESETS.values(), ids=lambda p: p.id)
def test_preset_regions_convert_to_onscreen_rects(preset):
    # Bounds, overlap, and duplicate-module rules are enforced by Layout's own
    # validator at construction, so building PRESETS already proves those.
    for region in preset.regions:
        x, y, w, h = region_rect(region, preset.cols, preset.rows, SIZE)
        assert w > 0 and h > 0
        assert 0 <= x and x + w <= SIZE[0]
        assert 0 <= y and y + h <= SIZE[1]


def test_preset_slot_counts_match_old_templates():
    # Config migration maps old slot indices onto these presets' region order, so
    # their shapes are frozen. Presets added since (the photo frames) are not.
    old_templates = {
        "full": 1,
        "split": 2,
        "main_sidebar": 3,
        "grid": 4,
        "dashboard": 4,
        "dashboard_clock": 5,
    }
    counts = {p.id: len(p.regions) for p in PRESETS.values()}
    assert {k: counts[k] for k in old_templates} == old_templates


def test_dashboard_clock_exact_geometry():
    preset = PRESETS["dashboard_clock"]
    header, main = preset.regions[0], preset.regions[1]
    assert region_rect(header, preset.cols, preset.rows, SIZE) == (0, 0, 800, 64)
    assert region_rect(main, preset.cols, preset.rows, SIZE) == (0, 64, 496, 272)


def test_region_rect_seam_free_on_uneven_grid():
    # 7 columns don't divide 800 evenly; adjacent regions must still share an edge
    left = region_rect(Region(x=0, y=0, w=3, h=7), 7, 7, SIZE)
    right = region_rect(Region(x=3, y=0, w=4, h=7), 7, 7, SIZE)
    assert left[0] + left[2] == right[0]
    assert left[2] + right[2] == SIZE[0]


def test_resolve_layout_prefers_custom():
    custom = Layout(id="mine", name="Mine", regions=[Region(x=0, y=0, w=10, h=10, module="clock")])
    assert resolve_layout(LayoutConfig(active="mine", layouts=[custom])) is custom


def test_resolve_layout_preset_and_fallback():
    assert resolve_layout(LayoutConfig(active="grid")).id == "grid"
    assert resolve_layout(LayoutConfig(active="gone")).id == "dashboard_clock"


def test_layout_rejects_region_out_of_bounds():
    with pytest.raises(ValidationError, match="exceeds"):
        Layout(id="bad", name="Bad", regions=[Region(x=45, y=0, w=10, h=10)])


def test_layout_rejects_overlapping_regions():
    with pytest.raises(ValidationError, match="overlap"):
        Layout(
            id="bad",
            name="Bad",
            regions=[Region(x=0, y=0, w=10, h=10), Region(x=5, y=5, w=10, h=10)],
        )


def test_layout_rejects_duplicate_module():
    with pytest.raises(ValidationError, match="more than one region"):
        Layout(
            id="bad",
            name="Bad",
            regions=[
                Region(x=0, y=0, w=10, h=10, module="clock"),
                Region(x=10, y=0, w=10, h=10, module="clock"),
            ],
        )


def test_region_rejects_sub_minimum_size():
    with pytest.raises(ValidationError):
        Region(x=0, y=0, w=1, h=2)


def test_layout_config_rejects_duplicate_ids():
    a = Layout(id="same", name="A")
    b = Layout(id="same", name="B")
    with pytest.raises(ValidationError, match="duplicate layout id"):
        LayoutConfig(active="same", layouts=[a, b])
