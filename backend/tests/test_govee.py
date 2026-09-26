from datetime import UTC, datetime, timedelta

import pytest

from inky_dash.govee_ble import parse_h5075
from inky_dash.modules.govee import averaged_view
from inky_dash.storage import Reading, SensorStore


def _payload(temp_c: float, humidity: float, battery: int) -> bytes:
    packed = round(temp_c * 10) * 1000 + round(humidity * 10)
    if temp_c < 0:
        packed = (round(-temp_c * 10) * 1000 + round(humidity * 10)) | 0x800000
    return bytes([0x00]) + packed.to_bytes(3, "big") + bytes([battery, 0x00])


def test_parse_positive_temperature():
    assert parse_h5075(_payload(21.5, 45.6, 87)) == (21.5, 45.6, 87)


def test_parse_negative_temperature():
    assert parse_h5075(_payload(-3.2, 60.0, 55)) == (-3.2, 60.0, 55)


def test_parse_rejects_garbage():
    assert parse_h5075(b"\x00") is None
    assert parse_h5075(bytes([0x00, 0x7F, 0xFF, 0xFF, 0x64])) is None  # implausible values


def _readings_every_2min(start: datetime, hours: float) -> list[Reading]:
    return [
        Reading(
            ts=start + timedelta(minutes=2 * i),
            temp_c=20.0 + i * 0.1,
            humidity=50.0 + i * 0.1,
            battery=90,
        )
        for i in range(int(hours * 30))
    ]


def test_averaged_view_is_stable_within_a_window():
    """New readings mid-window must not change the payload — that stability is
    what lets the render loop skip the e-ink refresh."""
    base = datetime(2026, 7, 27, 6, 0, tzinfo=UTC)
    readings = _readings_every_2min(base, hours=3)  # 06:00 .. 08:58
    at_0905 = averaged_view(readings, readings[-1], 60, 24, base.timestamp() + 3 * 3600 + 300)
    at_0955 = averaged_view(readings, readings[-1], 60, 24, base.timestamp() + 3 * 3600 + 3300)
    assert at_0905 == at_0955  # same completed window: 08:00-09:00

    at_1005 = averaged_view(readings, readings[-1], 60, 24, base.timestamp() + 4 * 3600 + 300)
    assert at_1005 != at_0905  # boundary passed: new window, new payload


def test_averaged_view_means_and_buckets():
    base = datetime(2026, 7, 27, 6, 0, tzinfo=UTC)
    readings = _readings_every_2min(base, hours=2)  # 06:00 .. 07:58
    view = averaged_view(readings, readings[-1], 60, 24, base.timestamp() + 2 * 3600 + 600)
    # last completed window is 07:00-08:00: readings 30..59 -> temps 23.0..25.9
    assert view["latest"]["temp_c"] == pytest.approx((23.0 + 25.9) / 2)
    assert view["latest"]["battery"] == 90
    assert [temp for _, temp in view["trend"]] == [
        pytest.approx((20.0 + 22.9) / 2),
        pytest.approx((23.0 + 25.9) / 2),
    ]
    # bucket points sit at window midpoints, not at "now"
    assert view["trend"][0][0] == base.timestamp() + 1800


def test_averaged_view_falls_back_to_live_before_first_window_completes():
    base = datetime(2026, 7, 27, 6, 0, tzinfo=UTC)
    readings = _readings_every_2min(base, hours=0.2)  # a few minutes of data
    live = readings[-1]
    view = averaged_view(readings, live, 60, 24, base.timestamp() + 600)
    assert view["latest"]["temp_c"] == live.temp_c
    assert averaged_view([], None, 60, 24, base.timestamp())["latest"] is None


def test_store_roundtrip_and_history(tmp_path):
    store = SensorStore(path=tmp_path / "test.db")
    now = datetime.now(UTC)
    store.insert(Reading(ts=now, temp_c=21.5, humidity=45.6, battery=87, address="AA:BB"))
    latest = store.latest()
    assert latest is not None
    assert latest.temp_c == 21.5
    assert latest.humidity == 45.6
    history = store.history(hours=1)
    assert len(history) == 1
    assert history[0].address == "AA:BB"
    assert store.history(hours=0) == []
