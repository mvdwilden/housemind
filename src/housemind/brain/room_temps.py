"""Temperaturen per ruimte (E-583): Thuis, Slapen and Weg for heating and for cooling, and how
long nobody is home before Weg, as an administrator set them in Brein › Apparaten. They belong
to the room, so that all thermostats of a room get the same. What a room has of its own goes
before what HouseMind learned and the start values; without it nothing changes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

SETTING = "room_temperatures"
STEP = 0.5  # every slider goes in halves: 16.0, 16.5, 17.0 (the administrator's wish)
# "Pas na": how long nobody is home before Weg, in hours. A day is what HouseMind did before,
# so it is where the slider starts; on vacation Weg comes at once (E-315).
AFTER_HOURS = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 12.0, 24.0)
START_AFTER = 24.0
HEAT_LIMITS = (5.0, 30.0)
COOL_LIMITS = (16.0, 32.0)
MODES = ("heat", "cool")
MOMENTS = ("home", "sleep", "away")


@dataclass(frozen=True, slots=True)
class Temps:
    """The three temperatures of one way of working, in degrees."""

    home: float
    sleep: float
    away: float


@dataclass(frozen=True, slots=True)
class RoomTemps:
    """What one room has of its own: for heating, for cooling, and the wait before Weg."""

    heat: Temps | None = None
    cool: Temps | None = None
    after_h: float | None = None


def half(value: float) -> float:
    """The nearest half degree."""
    return round(value * 2) / 2


def limits(lows: list[float], highs: list[float], mode: str) -> tuple[float, float]:
    """How far the sliders go for a room: what every thermostat in it can, within what makes
    sense for heating or cooling, on whole halves inside those limits."""
    low, high = HEAT_LIMITS if mode == "heat" else COOL_LIMITS
    low = max([low, *lows])
    high = min([high, *highs])
    low, high = math.ceil(low * 2) / 2, math.floor(high * 2) / 2
    return (low, high) if low <= high else (high, high)


def _number(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    value = float(raw)
    return value if math.isfinite(value) else None


def temps_from(raw: Any, mode: str, low: float, high: float) -> Temps | None:
    """Three temperatures as the window sends them, or None when one is missing, not on a half
    degree, outside the room's limits, or out of order: Slapen and Weg are never warmer than
    Thuis for heating, never colder for cooling."""
    if not isinstance(raw, dict):
        return None
    found = [_number(raw.get(moment)) for moment in MOMENTS]
    values = [v for v in found if v is not None]
    if len(values) != len(MOMENTS):
        return None
    if any(half(v) != v or not low <= v <= high for v in values):
        return None
    home, sleep, away = values
    if mode == "heat" and (sleep > home or away > home):
        return None
    if mode == "cool" and (sleep < home or away < home):
        return None
    return Temps(home, sleep, away)


def after_from(raw: Any) -> float | None:
    """One of the steps of "Pas na", in hours; anything else is no answer."""
    value = _number(raw)
    return value if value is not None and value in AFTER_HOURS else None


def parse(raw: Any) -> dict[str, RoomTemps]:
    """The saved setting, read leniently: what does not fit is left out."""
    rooms: dict[str, RoomTemps] = {}
    if not isinstance(raw, dict):
        return rooms
    for area_id, own in raw.items():
        if not isinstance(own, dict):
            continue
        heat = temps_from(own.get("heat"), "heat", *HEAT_LIMITS)
        cool = temps_from(own.get("cool"), "cool", *COOL_LIMITS)
        after = after_from(own.get("after_h"))
        if heat is not None or cool is not None or after is not None:
            rooms[str(area_id)] = RoomTemps(heat, cool, after)
    return rooms


def dump(rooms: dict[str, RoomTemps]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for area_id, own in sorted(rooms.items()):
        entry: dict[str, Any] = {}
        for mode in MODES:
            temps = getattr(own, mode)
            if temps is not None:
                entry[mode] = {m: getattr(temps, m) for m in MOMENTS}
        if own.after_h is not None:
            entry["after_h"] = own.after_h
        out[area_id] = entry
    return out
