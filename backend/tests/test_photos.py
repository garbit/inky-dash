import asyncio
from io import BytesIO

import pytest
from PIL import Image

from inky_dash import photos_store
from inky_dash.config import AppConfig
from inky_dash.modules.photos import PhotosModule, focus_of, pick_photo, prepare_image


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("INKY_DASH_DATA", str(tmp_path))
    return tmp_path


def photo_jpeg(size=(120, 80), colour=(200, 40, 40)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, colour).save(buf, format="JPEG")
    return buf.getvalue()


def entries(n: int) -> list[dict]:
    return [{"id": f"{i:016x}", "taken": None} for i in range(n)]


# ---------------------------------------------------------------- the pool


def test_add_list_and_remove():
    entry = photos_store.add_bytes(photo_jpeg(), filename="holiday.jpg")
    assert entry["source"] == "upload"
    assert [e["id"] for e in photos_store.load_index()] == [entry["id"]]
    assert photos_store.total_bytes() > 0
    assert photos_store.remove(entry["id"]) is True
    assert photos_store.load_index() == []
    assert photos_store.remove(entry["id"]) is False


def test_large_photo_is_downscaled():
    entry = photos_store.add_bytes(photo_jpeg((4000, 3000)))
    assert (entry["width"], entry["height"]) <= photos_store.MAX_SIZE


def test_unreadable_upload_raises_photo_error():
    with pytest.raises(photos_store.PhotoError):
        photos_store.add_bytes(b"this is not an image")


def test_prune_drops_the_oldest():
    # well-separated colours: identical bytes would be deduped rather than
    # stored, and JPEG flattens near-identical flat fills to the same bytes
    ids = [photos_store.add_bytes(photo_jpeg(colour=(c, 40, 40)))["id"] for c in (0, 90, 180)]
    assert photos_store.prune(2) == 1
    assert [e["id"] for e in photos_store.load_index()] == ids[1:]


def test_identical_bytes_are_not_stored_twice():
    first = photos_store.add_bytes(photo_jpeg(), filename="a.jpg")
    assert photos_store.add_bytes(photo_jpeg(), filename="a-copy.jpg") is None
    assert [e["id"] for e in photos_store.load_index()] == [first["id"]]


def test_same_source_id_is_not_stored_twice():
    """Drive and Picker re-sync the same items constantly; the id check runs
    before the download, so a repeat costs nothing."""
    photos_store.add_bytes(photo_jpeg((10, 10)), source="drive", source_id="drive-1")
    assert photos_store.has_source_id("drive-1") is True
    assert photos_store.add_bytes(photo_jpeg((99, 99)), source="drive", source_id="drive-1") is None
    assert photos_store.has_source_id("drive-2") is False


def test_different_photos_are_both_kept():
    a = photos_store.add_bytes(photo_jpeg(colour=(10, 20, 30)))
    b = photos_store.add_bytes(photo_jpeg(colour=(200, 100, 50)))
    assert a is not None and b is not None and a["id"] != b["id"]


def test_entries_predating_dedupe_do_not_break_it():
    """An index written before sha256/source_id existed must still load, and must
    not swallow new photos by matching a missing hash."""
    photos_store.save_index([{"id": "a" * 16, "filename": "old.jpg"}])
    photos_store.path_for("a" * 16).write_bytes(photo_jpeg())
    assert photos_store.add_bytes(photo_jpeg(colour=(1, 2, 3))) is not None


def test_path_for_refuses_ids_that_would_escape_the_pool():
    with pytest.raises(photos_store.PhotoError):
        photos_store.path_for("../../etc/passwd")
    assert photos_store.exists("../../etc/passwd") is False


def test_index_survives_a_missing_image_file():
    entry = photos_store.add_bytes(photo_jpeg())
    photos_store.path_for(entry["id"]).unlink()
    assert photos_store.load_index() == []


def test_corrupt_index_reads_as_empty():
    photos_store.index_path().write_text("{not json")
    assert photos_store.load_index() == []


# ------------------------------------------------------------- rotation


def test_pick_photo_is_stable_within_a_rotation_window():
    pool = entries(5)
    start = 7200.0  # aligned to a 60-minute bucket boundary
    first = pick_photo(pool, "shuffle", start, 60)
    assert pick_photo(pool, "shuffle", start + 3599, 60) == first


def test_pick_photo_changes_at_the_boundary():
    pool = entries(5)
    before = pick_photo(pool, "sequential", 0.0, 60)
    after = pick_photo(pool, "sequential", 3600.0, 60)
    assert before != after


def test_shuffle_cycles_the_whole_pool_before_repeating():
    pool = entries(4)
    picks = [pick_photo(pool, "shuffle", i * 3600.0, 60)["id"] for i in range(4)]
    assert sorted(picks) == sorted(e["id"] for e in pool)


def test_on_this_day_prefers_matching_dates():
    pool = [
        {"id": "a" * 16, "taken": "2019-06-15T10:00:00"},
        {"id": "b" * 16, "taken": "2020-01-02T10:00:00"},
    ]
    now = 1_560_600_000.0  # 15 June 2019, local time
    from datetime import datetime

    today = datetime.fromtimestamp(now)
    matching = [e for e in pool if e["taken"].startswith(f"2019-{today.month:02d}")]
    if matching:  # guard against the fixture drifting with the runner's timezone
        assert pick_photo(pool, "on_this_day", now, 60)["id"] == matching[0]["id"]


def test_on_this_day_falls_back_when_nothing_matches():
    pool = [{"id": "c" * 16, "taken": "1999-12-25T10:00:00"}]
    assert pick_photo(pool, "on_this_day", 10_000.0, 60) is not None


def test_pick_photo_on_empty_pool():
    assert pick_photo([], "shuffle", 0.0, 60) is None


# --------------------------------------------------------------- render


def test_prepare_image_cover_fills_the_box(tmp_path):
    entry = photos_store.add_bytes(photo_jpeg((400, 100)))
    config = AppConfig().photos
    result = prepare_image(str(photos_store.path_for(entry["id"])), (200, 200), config)
    assert result.size == (200, 200)


def test_prepare_image_contain_letterboxes():
    entry = photos_store.add_bytes(photo_jpeg((400, 100)))
    config = AppConfig().photos
    config.fit = "contain"
    result = prepare_image(str(photos_store.path_for(entry["id"])), (200, 200), config)
    assert result.width <= 200 and result.height < 200


def test_prepare_image_crops_around_the_focus():
    """A wide photo cropped to a square keeps the part the focus points at: the
    left edge at 0.0, the right edge at 1.0, the middle by default."""
    strip = Image.new("RGB", (300, 100))
    strip.paste(Image.new("RGB", (100, 100), (255, 0, 0)), (0, 0))
    strip.paste(Image.new("RGB", (100, 100), (0, 255, 0)), (100, 0))
    strip.paste(Image.new("RGB", (100, 100), (0, 0, 255)), (200, 0))
    buf = BytesIO()
    strip.save(buf, format="PNG")  # lossless, so the assertions are on exact pixels
    entry = photos_store.add_bytes(buf.getvalue())
    path, config = str(photos_store.path_for(entry["id"])), AppConfig().photos
    config.contrast = 1.0
    config.saturation_boost = 1.0

    def middle(focus):
        return prepare_image(path, (100, 100), config, focus).getpixel((50, 50))

    assert middle((0.0, 0.5))[0] > 200  # red band
    assert middle((0.5, 0.5))[1] > 200  # green band — also the default
    assert middle((1.0, 0.5))[2] > 200  # blue band
    assert prepare_image(path, (100, 100), config).getpixel((50, 50))[1] > 200


def test_focus_of_defaults_to_the_centre():
    assert focus_of({}) == (0.5, 0.5)
    assert focus_of({"focus": [0.2, 0.8]}) == (0.2, 0.8)
    # anything malformed or out of range reads as centred rather than raising
    assert focus_of({"focus": "left"}) == (0.5, 0.5)
    assert focus_of({"focus": [1, 2, 3]}) == (0.5, 0.5)
    assert focus_of({"focus": [None, 0.5]}) == (0.5, 0.5)
    assert focus_of({"focus": [-1.0, 4.0]}) == (0.0, 1.0)


def test_set_focus_stores_clamped_values_and_clears():
    entry = photos_store.add_bytes(photo_jpeg())
    assert photos_store.set_focus(entry["id"], (0.25, 2.0))["focus"] == [0.25, 1.0]
    assert photos_store.load_index()[0]["focus"] == [0.25, 1.0]
    assert "focus" not in photos_store.set_focus(entry["id"], None)
    assert photos_store.set_focus("deadbeef", None) is None


def test_fetch_carries_the_stored_focus():
    entry = photos_store.add_bytes(photo_jpeg(), filename="one.jpg")
    photos_store.set_focus(entry["id"], (0.1, 0.9))
    assert asyncio.run(PhotosModule().fetch(AppConfig()))["focus"] == [0.1, 0.9]


def test_mono_style_is_bilevel():
    entry = photos_store.add_bytes(photo_jpeg())
    config = AppConfig().photos
    config.style = "mono"
    result = prepare_image(str(photos_store.path_for(entry["id"])), (60, 60), config)
    assert {colour for _, colour in result.getcolors()} <= {(0, 0, 0), (255, 255, 255)}


def test_fetch_reports_an_empty_pool():
    assert asyncio.run(PhotosModule().fetch(AppConfig())) == {"pool": 0}


def test_fetch_returns_the_current_photo():
    photos_store.add_bytes(photo_jpeg(), filename="one.jpg")
    data = asyncio.run(PhotosModule().fetch(AppConfig()))
    assert data["pool"] == 1 and data["path"].endswith(".jpg")


def test_render_with_data_and_without():
    module, config = PhotosModule(), AppConfig()
    config.photos.show_caption = True
    entry = photos_store.add_bytes(photo_jpeg(), filename="one.jpg")
    image = Image.new("RGB", (400, 300), (255, 255, 255))
    data = {
        "pool": 1,
        "id": entry["id"],
        "path": str(photos_store.path_for(entry["id"])),
        "taken": "2024-05-04T09:00:00",
        "filename": "one.jpg",
    }
    module.render(image, (0, 0, 400, 300), data, config)
    # a photograph is full colour by design, so no palette assertion here
    assert set(image.get_flattened_data()) != {(255, 255, 255)}

    blank = Image.new("RGB", (400, 300), (255, 255, 255))
    module.render(blank, (0, 0, 400, 300), None, config)
    assert set(blank.get_flattened_data()) != {(255, 255, 255)}


def test_render_survives_a_photo_that_has_gone_bad(tmp_path):
    module, config = PhotosModule(), AppConfig()
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not an image")
    image = Image.new("RGB", (400, 300), (255, 255, 255))
    module.render(image, (0, 0, 400, 300), {"pool": 1, "path": str(bad)}, config)


def test_render_with_title_bar():
    module, config = PhotosModule(), AppConfig()
    config.photos.show_title = True
    entry = photos_store.add_bytes(photo_jpeg())
    image = Image.new("RGB", (400, 300), (255, 255, 255))
    module.render(
        image,
        (0, 0, 400, 300),
        {"pool": 1, "path": str(photos_store.path_for(entry["id"]))},
        config,
    )
