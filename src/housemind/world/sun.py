"""The height of the sun, worked out from the place of the house and the time (E-268, B-118).

Home Assistant keeps no height of the sun in its history (only the name of ``sun.sun``), so
HouseMind works it out itself – the same live and when replaying – from the latitude and
longitude Home Assistant knows. The place stays in the house. Accurate to about half a degree
(the usual low-precision solar formulas); with the usual refraction near the horizon, so
sunset is at about −0.83°.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta, tzinfo


def elevation(latitude: float, longitude: float, when: datetime) -> float:
    """Degrees above the horizon (negative: below) at that moment."""
    height = _height(latitude, longitude, when)
    return height + _refraction(height)


def _height(latitude: float, longitude: float, when: datetime) -> float:
    """The true height of the middle of the sun, without what the atmosphere does to it."""
    moment = when.astimezone(UTC)
    days = (moment - datetime(2000, 1, 1, 12, tzinfo=UTC)).total_seconds() / 86400.0
    mean_longitude = (280.460 + 0.9856474 * days) % 360
    anomaly = math.radians((357.528 + 0.9856003 * days) % 360)
    ecliptic = math.radians(
        mean_longitude + 1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly)
    )
    obliquity = math.radians(23.439 - 0.0000004 * days)
    declination = math.asin(math.sin(obliquity) * math.sin(ecliptic))
    right_ascension = math.degrees(
        math.atan2(math.cos(obliquity) * math.sin(ecliptic), math.cos(ecliptic))
    )
    sidereal = (280.46061837 + 360.98564736629 * days + longitude) % 360
    hour_angle = math.radians((sidereal - right_ascension) % 360)
    lat = math.radians(latitude)
    sine = math.sin(lat) * math.sin(declination) + math.cos(lat) * math.cos(declination) * math.cos(
        hour_angle
    )
    return math.degrees(math.asin(max(-1.0, min(1.0, sine))))


def _refraction(height: float) -> float:
    """The atmosphere lifts the sun a little near the horizon (Bennett's formula)."""
    if height < -2.0:
        return 0.0
    return 1.02 / math.tan(math.radians(height + 10.3 / (height + 5.11))) / 60.0


# Sunrise and sunset as an almanac gives them: the top edge on the horizon, with refraction.
_HORIZON = -0.833


def _crossing(latitude: float, longitude: float, low: datetime, high: datetime) -> datetime | None:
    """When the sun passes the horizon between two moments, to the second; None if it does not."""
    below = _height(latitude, longitude, low) < _HORIZON
    if (_height(latitude, longitude, high) < _HORIZON) == below:
        return None
    while high - low > timedelta(seconds=1):
        middle = low + (high - low) / 2
        if (_height(latitude, longitude, middle) < _HORIZON) == below:
            low = middle
        else:
            high = middle
    return low + (high - low) / 2


def _top(latitude: float, longitude: float, low: datetime, high: datetime, sign: int) -> datetime:
    """When the sun is highest (sign 1) or lowest (sign −1) between two moments."""
    while high - low > timedelta(seconds=20):
        third = (high - low) / 3
        one, two = low + third, high - third
        if sign * _height(latitude, longitude, one) < sign * _height(latitude, longitude, two):
            low = one
        else:
            high = two
    return low + (high - low) / 2


def horizon_and_noon(
    latitude: float, longitude: float, day: date, zone: tzinfo
) -> tuple[datetime, datetime, datetime, datetime]:
    """Sunrise, solar noon, sunset and the solar midnight before them, on this local day (E-466).
    Where the sun does not cross the horizon that day: six hours around noon."""
    start = datetime.combine(day, time(0), zone)
    noon = _top(latitude, longitude, start, start + timedelta(hours=24), 1)
    midnight = _top(latitude, longitude, noon - timedelta(hours=16), noon - timedelta(hours=8), -1)
    rise = _crossing(latitude, longitude, midnight, noon)
    sets = _crossing(latitude, longitude, noon, noon + timedelta(hours=12))
    return (
        rise or noon - timedelta(hours=6),
        noon,
        sets or noon + timedelta(hours=6),
        midnight,
    )
