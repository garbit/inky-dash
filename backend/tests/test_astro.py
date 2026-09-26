"""The moon maths is checked against Meeus's own worked examples.

Those examples are the published output of the full theory, so agreeing with
them to a fraction of a minute is the only real evidence that the truncated
series here were transcribed correctly — a single mistyped coefficient shows up
as arcminutes of longitude, which these tolerances catch.
"""

import contextlib
import os
import time
from datetime import UTC, date, datetime, timedelta

import pytest

from inky_dash import astro

LONDON = (51.5072, -0.1276)


@contextlib.contextmanager
def local_timezone(name: str):
    """Pin the process timezone: rise/set are bounded by the *local* calendar
    day, so anything asserting a specific day has to say which zone it means."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def test_julian_day_matches_known_epoch():
    assert astro.julian_day(datetime(1992, 4, 12, tzinfo=UTC)) == 2448724.5
    assert astro.julian_day(datetime(2000, 1, 1, 12, tzinfo=UTC)) == astro.J2000


def test_moon_position_matches_meeus_example_47a():
    position = astro.moon_position(2448724.5)
    assert position.longitude == pytest.approx(133.162655, abs=0.001)
    assert position.latitude == pytest.approx(-3.229126, abs=0.001)
    assert position.distance_km == pytest.approx(368409.7, abs=1.0)


def test_moon_longitude_shortcut_agrees_with_the_full_position():
    """Phase solving uses the longitude-only series; it must not drift from the
    full one, or the phase times would disagree with the drawn disc."""
    for jd in (2448724.5, 2451545.0, 2461000.25):
        full = astro.moon_position(jd).longitude
        assert astro.moon_longitude(jd) == pytest.approx(full, abs=1e-9)


def test_sun_longitude_matches_meeus_example_25b():
    longitude, distance = astro.sun_position(astro.julian_day(datetime(1992, 10, 13, tzinfo=UTC)))
    assert longitude == pytest.approx(199.90895, abs=0.001)
    assert distance == pytest.approx(0.99760775 * astro.AU_KM, rel=1e-4)


def test_illuminated_fraction_matches_meeus_example_48a():
    assert astro.phase_at(2448724.5).illumination == pytest.approx(0.6786, abs=0.0005)


def test_phase_times_match_meeus_examples_49a_and_49b():
    new_moon = astro.phase_after(astro.julian_day(datetime(1977, 2, 14, tzinfo=UTC)), 0.0)
    last_quarter = astro.phase_after(astro.julian_day(datetime(2044, 1, 18, tzinfo=UTC)), 270.0)
    assert new_moon == pytest.approx(2443192.65118, abs=0.002)  # ~3 minutes
    assert last_quarter == pytest.approx(2467636.49186, abs=0.002)


def test_illumination_at_the_solved_phases():
    jd = astro.julian_day(datetime(2026, 3, 1, tzinfo=UTC))
    assert astro.phase_at(astro.phase_after(jd, 0.0)).illumination < 0.005
    assert astro.phase_at(astro.phase_after(jd, 180.0)).illumination > 0.995
    for target in (90.0, 270.0):
        assert astro.phase_at(astro.phase_after(jd, target)).illumination == pytest.approx(
            0.5, abs=0.005
        )


def test_successive_new_moons_are_a_lunation_apart():
    starts = [astro.phase_after(astro.julian_day(datetime(2026, 1, 1, tzinfo=UTC)), 0.0)]
    for _ in range(12):
        starts.append(astro.phase_after(starts[-1] + 1, 0.0))
    intervals = [b - a for a, b in zip(starts, starts[1:], strict=False)]
    # Real lunations run 29.27-29.83 days; a solver that skipped a root or found
    # the same one twice would fall outside that immediately.
    assert all(29.2 < gap < 29.9 for gap in intervals)
    assert sum(intervals) / len(intervals) == pytest.approx(astro.SYNODIC_MONTH, abs=0.05)


def test_upcoming_phases_are_ordered_and_within_a_lunation():
    jd = astro.julian_day(datetime(2026, 7, 27, tzinfo=UTC))
    upcoming = astro.upcoming_phases(jd, 4)
    assert [name for name, _ in upcoming] == [
        astro.FULL_MOON,
        astro.LAST_QUARTER,
        astro.NEW_MOON,
        astro.FIRST_QUARTER,
    ]
    times = [when for _, when in upcoming]
    assert times == sorted(times)
    assert jd < times[0] and times[-1] - jd < astro.SYNODIC_MONTH + 1


def test_last_new_moon_is_the_most_recent_one():
    jd = astro.julian_day(datetime(2026, 7, 27, tzinfo=UTC))
    previous = astro.last_new_moon(jd)
    assert 0 <= jd - previous < astro.SYNODIC_MONTH
    # and there is no newer one hiding between it and now
    assert astro.phase_after(previous + 1, 0.0) > jd


def test_phase_names_track_the_cycle():
    assert astro.phase_name(0.0) == astro.NEW_MOON
    assert astro.phase_name(90.0) == astro.FIRST_QUARTER
    assert astro.phase_name(180.0) == astro.FULL_MOON
    assert astro.phase_name(270.0) == astro.LAST_QUARTER
    assert astro.phase_name(45.0) == "Waxing crescent"
    assert astro.phase_name(135.0) == "Waxing gibbous"
    assert astro.phase_name(225.0) == "Waning gibbous"
    assert astro.phase_name(315.0) == "Waning crescent"
    # a principal name is worth about a day either side, not an eighth of the cycle
    assert astro.phase_name(180 + astro.PRINCIPAL_WINDOW - 0.1) == astro.FULL_MOON
    assert astro.phase_name(180 + astro.PRINCIPAL_WINDOW + 0.1) == "Waning gibbous"
    assert astro.phase_name(359.9) == astro.NEW_MOON  # the wrap is a phase, not a gap


def test_rise_and_set_put_the_moon_on_the_horizon():
    with local_timezone("Europe/London"):
        rise, sets = astro.rise_set(date(2026, 7, 27), *LONDON)
    assert rise is not None and sets is not None
    for moment, climbing in ((rise, True), (sets, False)):
        threshold = astro.standard_altitude(astro.moon_position(moment).distance_km)
        assert astro.altitude(moment, *LONDON) == pytest.approx(threshold, abs=0.01)
        later = astro.altitude(moment + 0.01, *LONDON)
        assert (later > threshold) is climbing


def test_rise_and_set_fall_inside_the_local_day():
    with local_timezone("America/Los_Angeles"):
        day = date(2026, 7, 27)
        rise, sets = astro.rise_set(day, *LONDON)
        for moment in (rise, sets):
            if moment is not None:
                assert astro.from_julian(moment).date() == day


def test_a_day_without_a_moonrise_is_reported_as_such():
    """The moon rises ~50 minutes later each day, so a day in every lunation has
    no moonrise at all (and another none set). That is data, not a failure."""
    with local_timezone("Europe/London"):
        results = [
            astro.rise_set(date(2026, 1, 1) + timedelta(days=offset), *LONDON)
            for offset in range(60)
        ]
    assert any(rise is None for rise, _ in results)
    assert any(sets is None for _, sets in results)
    assert all(not (rise is None and sets is None) for rise, sets in results)


def test_snapshot_is_self_consistent():
    with local_timezone("Europe/London"):
        moment = datetime(2026, 7, 27, 18, 0).astimezone()
        snapshot = astro.snapshot(moment, *LONDON, phase_count=4)
    assert snapshot.phase == "Waxing gibbous"
    assert snapshot.waxing is True
    assert 0.9 < snapshot.illumination < 1.0
    assert 12 < snapshot.age_days < 15
    assert 356000 < snapshot.distance_km < 407000  # perigee to apogee
    assert len(snapshot.upcoming) == 4
    assert snapshot.upcoming[0][0] == astro.FULL_MOON
    assert all(when > moment for _, when in snapshot.upcoming)


def test_snapshot_phase_count_limits_the_lookahead():
    snapshot = astro.snapshot(datetime(2026, 7, 27, 18, 0).astimezone(), *LONDON, phase_count=1)
    assert len(snapshot.upcoming) == 1
