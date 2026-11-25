"""Day types (E-89) and daylight (H3.1)."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from functools import lru_cache

import holidays


class DayType(StrEnum):
    WORKDAY = "workday"
    FREE = "free"  # weekend
    HOLIDAY = "holiday"
    DEVIANT = "deviant"  # e.g. working from home or ill: not learned as the normal pattern


@lru_cache(maxsize=32)
def _holidays(country: str, year: int) -> holidays.HolidayBase:
    try:
        return holidays.country_holidays(country, years=year)
    except NotImplementedError:
        return holidays.HolidayBase()


def day_type(day: date, country: str | None) -> DayType:
    if country and day in _holidays(country.upper(), day.year):
        return DayType.HOLIDAY
    return DayType.FREE if day.weekday() >= 5 else DayType.WORKDAY


def time_bucket(moment: datetime, minutes: int = 30) -> str:
    """The 30-minute time slot used for learning (B-08), e.g. ``"18:30"``."""
    slot = (moment.hour * 60 + moment.minute) // minutes * minutes
    return f"{slot // 60:02d}:{slot % 60:02d}"


def is_dark(sun_elevation: float | None, lux: float | None, lux_threshold: float = 40.0) -> bool:
    """Dark enough for light: a light sensor wins over the sun position."""
    if lux is not None:
        return lux < lux_threshold
    if sun_elevation is not None:
        return sun_elevation < 3.0
    return False
