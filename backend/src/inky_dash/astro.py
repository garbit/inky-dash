"""Sun and moon positions, moon phase, and moonrise/moonset — computed locally.

No ephemeris service and no extra dependency: the moon is the one panel whose
data is a pure function of the clock, so fetching it over the network would be
strictly worse (an outage, a rate limit, and a fetch cadence, all for numbers
that a hundred lines of trigonometry give exactly).

The series are the truncated ones from Meeus, *Astronomical Algorithms* (ch. 25
sun, ch. 47 moon, ch. 48 illumination, ch. 15 rise/set), which agree with the
full theory to about 10 arcseconds in lunar longitude — a couple of minutes in
phase timing and well inside what a panel that refreshes every ten minutes can
show. Times are UT throughout: the TD-UT difference (~a minute this century)
moves the moon by 0.01°, which is below the truncation error anyway.

Angles are degrees at the boundaries and radians only inside the maths.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import NamedTuple

SYNODIC_MONTH = 29.530588853  # mean days between new moons
J2000 = 2451545.0
UNIX_EPOCH_JD = 2440587.5
EARTH_RADIUS_KM = 6378.14
AU_KM = 149597870.7
# Refraction at the horizon, subtracted from the moon's parallax to get the
# altitude its centre has when its limb sits on the horizon (Meeus ch. 15).
REFRACTION = 0.5667

# Periodic terms for the moon's longitude and distance (Meeus table 47.A):
# multiples of (D, M, M', F) and the coefficients of sin (1e-6 degrees) and
# cos (1e-3 km).
_LON_DIST: tuple[tuple[int, int, int, int, int, int], ...] = (
    (0, 0, 1, 0, 6288774, -20905355),
    (2, 0, -1, 0, 1274027, -3699111),
    (2, 0, 0, 0, 658314, -2955968),
    (0, 0, 2, 0, 213618, -569925),
    (0, 1, 0, 0, -185116, 48888),
    (0, 0, 0, 2, -114332, -3149),
    (2, 0, -2, 0, 58793, 246158),
    (2, -1, -1, 0, 57066, -152138),
    (2, 0, 1, 0, 53322, -170733),
    (2, -1, 0, 0, 45758, -204586),
    (0, 1, -1, 0, -40923, -129620),
    (1, 0, 0, 0, -34720, 108743),
    (0, 1, 1, 0, -30383, 104755),
    (2, 0, 0, -2, 15327, 10321),
    (0, 0, 1, 2, -12528, 0),
    (0, 0, 1, -2, 10980, 79661),
    (4, 0, -1, 0, 10675, -34782),
    (0, 0, 3, 0, 10034, -23210),
    (4, 0, -2, 0, 8548, -21636),
    (2, 1, -1, 0, -7888, 24208),
    (2, 1, 0, 0, -6766, 30824),
    (1, 0, -1, 0, -5163, -8379),
    (1, 1, 0, 0, 4987, -16675),
    (2, -1, 1, 0, 4036, -12831),
    (2, 0, 2, 0, 3994, -10445),
    (4, 0, 0, 0, 3861, -11650),
    (2, 0, -3, 0, 3665, 14403),
    (0, 1, -2, 0, -2689, -7003),
    (2, 0, -1, 2, -2602, 0),
    (2, -1, -2, 0, 2390, 10056),
    (1, 0, 1, 0, -2348, 6322),
    (2, -2, 0, 0, 2236, -9884),
    (0, 1, 2, 0, -2120, 5751),
    (0, 2, 0, 0, -2069, 0),
    (2, -2, -1, 0, 2048, -4950),
    (2, 0, 1, -2, -1773, 4130),
    (2, 0, 0, 2, -1595, 0),
    (4, -1, -1, 0, 1215, -3958),
    (0, 0, 2, 2, -1110, 0),
    (3, 0, -1, 0, -892, 3258),
    (2, 1, 1, 0, -810, 2616),
    (4, -1, -2, 0, 759, -1897),
    (0, 2, -1, 0, -713, -2117),
    (2, 2, -1, 0, -700, 2354),
    (2, 1, -2, 0, 691, 0),
    (2, -1, 0, -2, 596, 0),
    (4, 0, 1, 0, 549, -1423),
    (0, 0, 4, 0, 537, -1117),
    (4, -1, 0, 0, 520, -1571),
    (1, 0, -2, 0, -487, -1739),
    (2, 1, 0, -2, -399, 0),
    (0, 0, 2, -2, -381, -4421),
    (1, 1, 1, 0, 351, 0),
    (3, 0, -2, 0, -340, 0),
    (4, 0, -3, 0, 330, 0),
    (2, -1, 2, 0, 327, 0),
    (0, 2, 1, 0, -323, 1165),
    (1, 1, -1, 0, 299, 0),
    (2, 0, 3, 0, 294, 0),
    (2, 0, -1, -2, 0, 8752),
)

# Periodic terms for the moon's ecliptic latitude (Meeus table 47.B), sin
# coefficients in 1e-6 degrees.
_LAT: tuple[tuple[int, int, int, int, int], ...] = (
    (0, 0, 0, 1, 5128122),
    (0, 0, 1, 1, 280602),
    (0, 0, 1, -1, 277693),
    (2, 0, 0, -1, 173237),
    (2, 0, -1, 1, 55413),
    (2, 0, -1, -1, 46271),
    (2, 0, 0, 1, 32573),
    (0, 0, 2, 1, 17198),
    (2, 0, 1, -1, 9266),
    (0, 0, 2, -1, 8822),
    (2, -1, 0, -1, 8216),
    (2, 0, -2, -1, 4324),
    (2, 0, 1, 1, 4200),
    (2, 1, 0, -1, -3359),
    (2, -1, -1, 1, 2463),
    (2, -1, 0, 1, 2211),
    (2, -1, -1, -1, 2065),
    (0, 1, -1, -1, -1870),
    (4, 0, -1, -1, 1828),
    (0, 1, 0, 1, -1794),
    (0, 0, 0, 3, -1749),
    (0, 1, -1, 1, -1565),
    (1, 0, 0, 1, -1491),
    (0, 1, 1, 1, -1475),
    (0, 1, 1, -1, -1410),
    (0, 1, 0, -1, -1344),
    (1, 0, 0, -1, -1335),
    (0, 0, 3, 1, 1107),
    (4, 0, 0, -1, 1021),
    (4, 0, -1, 1, 833),
    (0, 0, 1, -3, 777),
    (4, 0, -2, 1, 671),
    (2, 0, 0, -3, 607),
    (2, 0, 2, -1, 596),
    (2, -1, 1, -1, 491),
    (2, 0, -2, 1, -451),
    (0, 0, 3, -1, 439),
    (2, 0, 2, 1, 422),
    (2, 0, -3, -1, 421),
    (2, 1, -1, 1, -366),
    (2, 1, 0, 1, -351),
    (4, 0, 0, 1, 331),
    (2, -1, 1, 1, 315),
    (2, -2, 0, -1, 302),
    (0, 0, 1, 3, -283),
    (2, 1, 1, -1, -229),
    (1, 1, 0, -1, 223),
    (1, 1, 0, 1, 223),
    (0, 1, -2, -1, -220),
    (2, 1, -1, -1, -220),
    (1, 0, 1, 1, -185),
    (2, -1, -2, -1, 181),
    (0, 1, 2, 1, -177),
    (4, 0, -2, -1, 176),
    (4, -1, -1, -1, 166),
    (1, 0, 1, -1, -164),
    (4, 0, 1, -1, 132),
    (1, 0, -1, -1, -119),
    (4, -1, 0, -1, 115),
    (2, -2, 0, 1, 107),
)


def julian_day(moment: datetime) -> float:
    """Julian day for ``moment``. A naive datetime is local time, as everywhere
    else in Python — ``timestamp()`` resolves both, DST included."""
    return moment.timestamp() / 86400.0 + UNIX_EPOCH_JD


def from_julian(jd: float) -> datetime:
    """The local (aware) datetime for a Julian day."""
    return datetime.fromtimestamp((jd - UNIX_EPOCH_JD) * 86400.0).astimezone()


def _centuries(jd: float) -> float:
    return (jd - J2000) / 36525.0


def sun_position(jd: float) -> tuple[float, float]:
    """Apparent geocentric longitude (degrees) and distance (km) of the sun."""
    t = _centuries(jd)
    mean_long = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    anomaly = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
    eccentricity = 0.016708634 - 0.000042037 * t - 0.0000001267 * t * t
    m = math.radians(anomaly)
    centre = (
        (1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(m)
        + (0.019993 - 0.000101 * t) * math.sin(2 * m)
        + 0.000289 * math.sin(3 * m)
    )
    true_anomaly = math.radians(anomaly + centre)
    radius_au = 1.000001018 * (1 - eccentricity**2) / (1 + eccentricity * math.cos(true_anomaly))
    # Apparent longitude: nutation in longitude plus aberration.
    node = math.radians(125.04 - 1934.136 * t)
    apparent = mean_long + centre - 0.00569 - 0.00478 * math.sin(node)
    return apparent % 360, radius_au * AU_KM


def _moon_arguments(t: float) -> tuple[float, ...]:
    """The moon's fundamental arguments at century ``t``, in degrees, plus the
    eccentricity correction E applied to terms involving the sun's anomaly."""
    mean_long = (
        218.3164477 + 481267.88123421 * t - 0.0015786 * t**2 + t**3 / 538841 - t**4 / 65194000
    )
    elongation = (
        297.8501921 + 445267.1114034 * t - 0.0018819 * t**2 + t**3 / 545868 - t**4 / 113065000
    )
    sun_anomaly = 357.5291092 + 35999.0502909 * t - 0.0001536 * t**2 + t**3 / 24490000
    moon_anomaly = (
        134.9633964 + 477198.8675055 * t + 0.0087414 * t**2 + t**3 / 69699 - t**4 / 14712000
    )
    argument_of_latitude = (
        93.2720950 - 0.0036539 * t**2 + 483202.0175233 * t - t**3 / 3526000 + t**4 / 863310000
    )
    eccentricity = 1 - 0.002516 * t - 0.0000074 * t * t
    return (
        mean_long,
        elongation,
        sun_anomaly,
        moon_anomaly,
        argument_of_latitude,
        eccentricity,
    )


def _additive(t: float) -> tuple[float, float, float]:
    """Venus, Jupiter and flattening terms (Meeus 47), in degrees."""
    return (
        119.75 + 131.849 * t,
        53.09 + 479264.290 * t,
        313.45 + 481266.484 * t,
    )


def moon_longitude(jd: float) -> float:
    """Geocentric ecliptic longitude of the moon in degrees.

    Split out from :func:`moon_position` because phase solving evaluates this
    hundreds of times per render and has no use for the latitude or distance
    series — on a Pi Zero those cost real milliseconds.
    """
    t = _centuries(jd)
    mean_long, d, m, mp, f, eccentricity = _moon_arguments(t)
    a1, a2, _ = _additive(t)
    total = 0.0
    for cd, cm, cmp_, cf, sin_coeff, _ in _LON_DIST:
        if not sin_coeff:
            continue
        argument = math.radians(cd * d + cm * m + cmp_ * mp + cf * f)
        total += sin_coeff * math.sin(argument) * eccentricity ** abs(cm)
    total += (
        3958 * math.sin(math.radians(a1))
        + 1962 * math.sin(math.radians(mean_long - f))
        + 318 * math.sin(math.radians(a2))
    )
    return (mean_long + total / 1e6) % 360


class MoonPosition(NamedTuple):
    longitude: float  # geocentric ecliptic longitude, degrees
    latitude: float  # geocentric ecliptic latitude, degrees
    distance_km: float  # centre to centre


def moon_position(jd: float) -> MoonPosition:
    t = _centuries(jd)
    mean_long, d, m, mp, f, eccentricity = _moon_arguments(t)
    a1, a2, a3 = _additive(t)
    sum_l = sum_r = sum_b = 0.0
    for cd, cm, cmp_, cf, sin_coeff, cos_coeff in _LON_DIST:
        argument = math.radians(cd * d + cm * m + cmp_ * mp + cf * f)
        damping = eccentricity ** abs(cm)
        sum_l += sin_coeff * math.sin(argument) * damping
        sum_r += cos_coeff * math.cos(argument) * damping
    for cd, cm, cmp_, cf, sin_coeff in _LAT:
        argument = math.radians(cd * d + cm * m + cmp_ * mp + cf * f)
        sum_b += sin_coeff * math.sin(argument) * eccentricity ** abs(cm)
    sum_l += (
        3958 * math.sin(math.radians(a1))
        + 1962 * math.sin(math.radians(mean_long - f))
        + 318 * math.sin(math.radians(a2))
    )
    sum_b += (
        -2235 * math.sin(math.radians(mean_long))
        + 382 * math.sin(math.radians(a3))
        + 175 * math.sin(math.radians(a1 - f))
        + 175 * math.sin(math.radians(a1 + f))
        + 127 * math.sin(math.radians(mean_long - mp))
        - 115 * math.sin(math.radians(mean_long + mp))
    )
    return MoonPosition(
        longitude=(mean_long + sum_l / 1e6) % 360,
        latitude=sum_b / 1e6,
        distance_km=385000.56 + sum_r / 1e3,
    )


NEW_MOON = "New moon"
FIRST_QUARTER = "First quarter"
FULL_MOON = "Full moon"
LAST_QUARTER = "Last quarter"

# The four principal phases, as the moon-minus-sun longitude that defines each.
PRINCIPAL_PHASES: tuple[tuple[float, str], ...] = (
    (0.0, NEW_MOON),
    (90.0, FIRST_QUARTER),
    (180.0, FULL_MOON),
    (270.0, LAST_QUARTER),
)
# How far from an exact principal phase still gets its name. The moon covers
# ~12.2° a day, so ±6° means "full moon" is what the panel says for about a day
# either side of the instant — not the ±22.5° an eighth-of-the-cycle split gives.
PRINCIPAL_WINDOW = 6.0


def phase_name(elongation: float) -> str:
    for target, name in PRINCIPAL_PHASES:
        if abs(((elongation - target + 180) % 360) - 180) <= PRINCIPAL_WINDOW:
            return name
    if elongation < 90:
        return "Waxing crescent"
    if elongation < 180:
        return "Waxing gibbous"
    if elongation < 270:
        return "Waning gibbous"
    return "Waning crescent"


class Phase(NamedTuple):
    illumination: float  # lit fraction of the disc, 0..1
    elongation: float  # moon longitude minus sun longitude, 0..360
    waxing: bool
    distance_km: float
    name: str


def phase_at(jd: float) -> Phase:
    moon = moon_position(jd)
    sun_longitude, sun_distance = sun_position(jd)
    separation = math.acos(
        math.cos(math.radians(moon.latitude))
        * math.cos(math.radians(moon.longitude - sun_longitude))
    )
    # Phase angle at the moon: the sun is far enough away that this is a small
    # correction to the elongation, but it is what makes the terminator right.
    phase_angle = math.atan2(
        sun_distance * math.sin(separation),
        moon.distance_km - sun_distance * math.cos(separation),
    )
    elongation = (moon.longitude - sun_longitude) % 360
    return Phase(
        illumination=(1 + math.cos(phase_angle)) / 2,
        elongation=elongation,
        waxing=elongation < 180,
        distance_km=moon.distance_km,
        name=phase_name(elongation),
    )


def _elongation(jd: float) -> float:
    return (moon_longitude(jd) - sun_position(jd)[0]) % 360


def _offset(jd: float, target: float) -> float:
    """Signed degrees from ``target``, in (-180, 180]: zero at the phase."""
    return ((_elongation(jd) - target + 180) % 360) - 180


def phase_after(jd: float, target: float) -> float:
    """Julian day of the first time after ``jd`` that the elongation reaches
    ``target``.

    The elongation only ever increases (10.9°–14.8° a day — the moon never
    retrogrades in this coordinate), so a one-day scan cannot step over a
    crossing, and the sign change it finds always brackets exactly one root.
    """
    step = 1.0
    lo, low_offset = jd, _offset(jd, target)
    for _ in range(int(SYNODIC_MONTH / step) + 2):
        hi = lo + step
        high_offset = _offset(hi, target)
        if low_offset < 0 <= high_offset and high_offset - low_offset < 180:
            while hi - lo > 1e-5:  # ~1 second
                mid = (lo + hi) / 2
                if _offset(mid, target) < 0:
                    lo = mid
                else:
                    hi = mid
            return (lo + hi) / 2
        lo, low_offset = hi, high_offset
    raise ValueError(f"no phase at {target}° within a lunation of JD {jd}")


def last_new_moon(jd: float) -> float:
    """Julian day of the most recent new moon at or before ``jd``."""
    found = phase_after(jd - SYNODIC_MONTH - 1.5, 0.0)
    while True:
        following = phase_after(found + 1, 0.0)
        if following > jd:
            return found
        found = following


def upcoming_phases(jd: float, count: int = 4) -> list[tuple[str, float]]:
    """The next ``count`` principal phases in the order they happen, as
    ``(name, julian day)``."""
    events = sorted((phase_after(jd, target), name) for target, name in PRINCIPAL_PHASES)
    return [(name, when) for when, name in events[:count]]


def _obliquity(t: float) -> float:
    return 23.439291 - 0.0130042 * t - 1.64e-7 * t**2 + 5.04e-7 * t**3


def _greenwich_sidereal(jd: float) -> float:
    """Mean sidereal time at Greenwich, in degrees (Meeus 12.4)."""
    t = _centuries(jd)
    return (
        280.46061837 + 360.98564736629 * (jd - J2000) + 0.000387933 * t * t - t**3 / 38710000.0
    ) % 360


def _equatorial(position: MoonPosition, t: float) -> tuple[float, float]:
    """Ecliptic to equatorial: right ascension and declination in degrees."""
    obliquity = math.radians(_obliquity(t))
    longitude = math.radians(position.longitude)
    latitude = math.radians(position.latitude)
    right_ascension = math.atan2(
        math.sin(longitude) * math.cos(obliquity) - math.tan(latitude) * math.sin(obliquity),
        math.cos(longitude),
    )
    declination = math.asin(
        math.sin(latitude) * math.cos(obliquity)
        + math.cos(latitude) * math.sin(obliquity) * math.sin(longitude)
    )
    return math.degrees(right_ascension) % 360, math.degrees(declination)


def standard_altitude(distance_km: float) -> float:
    """Altitude of the moon's *centre* when its upper limb is on the horizon —
    its parallax, mostly cancelled by refraction (Meeus ch. 15)."""
    parallax = math.degrees(math.asin(EARTH_RADIUS_KM / distance_km))
    return 0.7275 * parallax - REFRACTION


def altitude(jd: float, latitude: float, longitude: float) -> float:
    """Geocentric altitude of the moon above the horizon, in degrees.
    ``longitude`` is positive east."""
    position = moon_position(jd)
    right_ascension, declination = _equatorial(position, _centuries(jd))
    hour_angle = math.radians((_greenwich_sidereal(jd) + longitude - right_ascension) % 360)
    phi, delta = math.radians(latitude), math.radians(declination)
    return math.degrees(
        math.asin(
            math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.cos(hour_angle)
        )
    )


def _horizon_offset(jd: float, latitude: float, longitude: float) -> float:
    """Height above the rise/set threshold: negative below, positive above."""
    position = moon_position(jd)
    right_ascension, declination = _equatorial(position, _centuries(jd))
    hour_angle = math.radians((_greenwich_sidereal(jd) + longitude - right_ascension) % 360)
    phi, delta = math.radians(latitude), math.radians(declination)
    above = math.degrees(
        math.asin(
            math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.cos(hour_angle)
        )
    )
    return above - standard_altitude(position.distance_km)


RISE_SET_STEP = 1 / 72  # 20 minutes: fine enough not to skip a brief appearance


def rise_set(day: date, latitude: float, longitude: float) -> tuple[float | None, float | None]:
    """Julian days of moonrise and moonset during the local ``day``.

    Either can be None, and often is: the moon rises ~50 minutes later each day,
    so roughly one local day in thirty genuinely has no moonrise (or no moonset)
    in it. That is a fact to render, not an error.
    """
    start = julian_day(datetime.combine(day, time.min))
    end = julian_day(datetime.combine(day + timedelta(days=1), time.min))
    rise: float | None = None
    sets: float | None = None
    lo = start
    low = _horizon_offset(lo, latitude, longitude)
    while lo < end:
        hi = min(lo + RISE_SET_STEP, end)
        high = _horizon_offset(hi, latitude, longitude)
        if low < 0 <= high and rise is None:
            rise = _bisect_horizon(lo, hi, latitude, longitude, rising=True)
        elif low >= 0 > high and sets is None:
            sets = _bisect_horizon(lo, hi, latitude, longitude, rising=False)
        lo, low = hi, high
    return rise, sets


def _bisect_horizon(
    lo: float, hi: float, latitude: float, longitude: float, *, rising: bool
) -> float:
    while hi - lo > 1e-5:  # ~1 second
        mid = (lo + hi) / 2
        below = _horizon_offset(mid, latitude, longitude) < 0
        if below == rising:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


class MoonSnapshot(NamedTuple):
    """Everything the moon panel draws, for one moment and one observer."""

    illumination: float  # 0..1
    age_days: float  # since the last new moon
    phase: str
    waxing: bool
    distance_km: float
    rise: datetime | None  # local time
    sets: datetime | None  # local time
    upcoming: list[tuple[str, datetime]]  # principal phases, soonest first


def snapshot(
    moment: datetime, latitude: float, longitude: float, phase_count: int = 4
) -> MoonSnapshot:
    """Assemble the moon's current state. ``moment`` is local time (aware or
    naive), and the rise/set times are for its calendar day."""
    jd = julian_day(moment)
    phase = phase_at(jd)
    rise, sets = rise_set(moment.date(), latitude, longitude)
    return MoonSnapshot(
        illumination=phase.illumination,
        age_days=jd - last_new_moon(jd),
        phase=phase.name,
        waxing=phase.waxing,
        distance_km=phase.distance_km,
        rise=from_julian(rise) if rise is not None else None,
        sets=from_julian(sets) if sets is not None else None,
        upcoming=[(name, from_julian(when)) for name, when in upcoming_phases(jd, phase_count)],
    )
