"""The day's rhythm of the light (E-465, E-466, B-381 – B-384): what the integration that lets
light follow the day says, in HouseMind's own brain.

Bright by day and soft in the evening, white around noon and warm at night, per group of lamps,
worked out from the sun. Home Assistant holds the settings; HouseMind reads them and never
changes them, so there is no second place to set them. While that integration is on, HouseMind
only says "on" and it picks the colour; once HouseMind has switched it off (Autonomous, E-468),
HouseMind works it out and sends it. What someone chooses by hand stays until the lamp goes out
(E-467): after each round HouseMind remembers where it left a lamp, and a lamp that is no longer
there is someone's choice.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import TYPE_CHECKING, Any

from housemind.core.model import HouseModel, State
from housemind.ha.adapter import HALightRhythm

if TYPE_CHECKING:
    from housemind.brain.levels import LightLevels
from housemind.world.sun import horizon_and_noon

SUNRISE, NOON, SUNSET, MIDNIGHT = "sunrise", "noon", "sunset", "midnight"
# A lamp this far from where HouseMind left it was set by someone else (B-383). Wide enough for
# what lamps report back and for a group's average, narrow enough to see a real choice.
OFF_BY_PCT = 8.0
OFF_BY_KELVIN = 200.0
# A round that changes less than this sends nothing: no radio for what nobody can see.
SAME_PCT = 1.0
SAME_KELVIN = 25.0
OWN = "own:"  # a rhythm of HouseMind's own, in a house without that integration (E-471)
READ_EVERY = timedelta(hours=1)  # settings changed in Home Assistant count within this (E-465)
Level = tuple[float | None, float | None]  # brightness in %, colour in kelvin


@dataclass(frozen=True, slots=True)
class SunDay:
    """The four moments of one day that the rhythm turns on."""

    sunrise: datetime
    noon: datetime
    sunset: datetime
    midnight: datetime  # the one before this day's noon

    def events(self) -> list[tuple[str, datetime]]:
        return [
            (MIDNIGHT, self.midnight),
            (SUNRISE, self.sunrise),
            (NOON, self.noon),
            (SUNSET, self.sunset),
        ]


def _at(day: date, clock: str | None, zone: tzinfo) -> datetime | None:
    """ "07:30" or "07:30:00" on this day; "None" is how the settings say there is none."""
    if not clock or str(clock).lower() == "none":
        return None
    try:
        return datetime.combine(day, time.fromisoformat(str(clock)), zone)
    except ValueError:
        return None


def sun_day(
    rhythm: HALightRhythm, latitude: float, longitude: float, day: date, zone: tzinfo
) -> SunDay:
    """This group's day: the sun's, or the times its settings fix, with their limits and
    offsets."""
    rise, noon, sets, midnight = horizon_and_noon(latitude, longitude, day, zone)
    fixed_rise = _at(day, rhythm.sunrise_time, zone)
    fixed_set = _at(day, rhythm.sunset_time, zone)
    moved = fixed_rise is not None or fixed_set is not None
    rise = fixed_rise or rise
    sets = fixed_set or sets
    if (earliest := _at(day, rhythm.min_sunrise_time, zone)) is not None:
        rise = max(rise, earliest)
    if (latest := _at(day, rhythm.max_sunrise_time, zone)) is not None:
        rise = min(rise, latest)
    if (earliest := _at(day, rhythm.min_sunset_time, zone)) is not None:
        sets = max(sets, earliest)
    if (latest := _at(day, rhythm.max_sunset_time, zone)) is not None:
        sets = min(sets, latest)
    rise += timedelta(seconds=rhythm.sunrise_offset_s)
    sets += timedelta(seconds=rhythm.sunset_offset_s)
    if moved:  # noon and midnight follow the day its settings make
        noon = rise + (sets - rise) / 2
        midnight = noon - timedelta(hours=12)
    return SunDay(rise, noon, sets, midnight)


def _around(
    rhythm: HALightRhythm, latitude: float, longitude: float, now: datetime, zone: tzinfo
) -> tuple[tuple[str, datetime], tuple[str, datetime]]:
    """The moment before and the moment after now."""
    today = now.astimezone(zone).date()
    events = sorted(
        (
            event
            for days in (-1, 0, 1, 2)
            for event in sun_day(
                rhythm, latitude, longitude, today + timedelta(days=days), zone
            ).events()
        ),
        key=lambda e: e[1],
    )
    for index, event in enumerate(events):
        if event[1] > now:
            return events[index - 1], event
    return events[-2], events[-1]  # not reached: the days around now hold a later moment


def sun_position(
    rhythm: HALightRhythm, latitude: float, longitude: float, now: datetime, zone: tzinfo
) -> float:
    """From −1 (the middle of the night) through 0 (sunrise, sunset) to 1 (noon), on a curve."""
    (_, before), (name, after) = _around(rhythm, latitude, longitude, now, zone)
    # A quarter of a parabola between two moments, its top at noon or midnight.
    top, edge = (before, after) if name in (SUNSET, SUNRISE) else (after, before)
    above = name in (SUNSET, NOON)
    share = (now - top) / (top - edge)
    return (1 if above else -1) * (1 - share**2)


def kelvin(rhythm: HALightRhythm, position: float) -> int:
    """Warmest all night, whitest at noon."""
    if position <= 0:
        return round(rhythm.min_kelvin)
    return 5 * round(((rhythm.max_kelvin - rhythm.min_kelvin) * position + rhythm.min_kelvin) / 5)


def _between(now: float, x1: float, x2: float, y1: float, y2: float) -> float:
    return y1 + (now - x1) * (y2 - y1) / (x2 - x1)


def _soft(share: float, x1: float, x2: float, y1: float, y2: float) -> float:
    """An S-curve that is at ``y1`` of the way at x1, ``y2`` at x2."""
    a = (math.atanh(2 * y2 - 1) - math.atanh(2 * y1 - 1)) / (x2 - x1)
    b = x1 - math.atanh(2 * y1 - 1) / a
    return 0.5 * (math.tanh(a * (share - b)) + 1)


def brightness(
    rhythm: HALightRhythm,
    latitude: float,
    longitude: float,
    now: datetime,
    zone: tzinfo,
) -> float:
    """At the most by day, at the least by night, and the way between the two as it is set."""
    low, high = rhythm.min_brightness, rhythm.max_brightness
    if rhythm.brightness_mode not in ("linear", "tanh"):
        position = sun_position(rhythm, latitude, longitude, now, zone)
        return high if position > 0 else (high - low) * (1 + position) + low
    (first, before), (second, after) = _around(rhythm, latitude, longitude, now, zone)
    morning = SUNRISE in (first, second)
    if morning:
        moment = before if first == SUNRISE else after
    else:
        moment = before if first == SUNSET else after
    dark, light = rhythm.time_dark_s, rhythm.time_light_s
    since = (now - moment).total_seconds()
    if rhythm.brightness_mode == "linear":
        level = (
            _between(since, -dark, light, low, high)
            if morning
            else _between(since, -light, dark, high, low)
        )
    else:
        share = (
            _soft(since, -dark, light, 0.05, 0.95)
            if morning
            else _soft(since, -light, dark, 0.95, 0.05)
        )
        level = low + (high - low) * share
    return max(low, min(high, level))


@dataclass(slots=True)
class Following:
    """A lamp on the rhythm: where HouseMind left it, and when it last looked."""

    pct: float | None
    kelvin: float | None
    at: datetime


@dataclass(slots=True)
class DaylightBook:
    """The groups as read, which lamps belong to them, and who does the work now."""

    model: HouseModel
    rhythms: dict[str, HALightRhythm] = field(default_factory=dict)
    # The entities of each group: its own switch, and the ones for sleep, colour and brightness.
    switches: dict[str, dict[str, str]] = field(default_factory=dict)
    taken: set[str] = field(default_factory=set)  # groups HouseMind switched off (E-468)
    kept_on: set[str] = field(default_factory=set)  # someone put it back on: it stays
    following: dict[str, Following] = field(default_factory=dict)
    # Where a lamp last was, seen or sent: a plain "on" comes back there (E-467).
    last_seen: dict[str, Level] = field(default_factory=dict)
    read_at: datetime | None = None
    known: bool = False  # the settings could be read: no group then means no integration
    own_for: Callable[[str], HALightRhythm | None] | None = None  # E-471
    stands: LightLevels | None = None  # each lamp its own percentage (E-494, B-409)
    states: Mapping[str, State] = field(default_factory=dict)  # the world's, kept up to date
    place: tuple[float, float] | None = None  # latitude, longitude
    zone: tzinfo = UTC
    _lamps: dict[str, str] = field(default_factory=dict)  # a lamp or group as listed -> group
    missing: dict[str, tuple[str, ...]] = field(default_factory=dict)  # group -> lamps not there

    def read(self, rhythms: Iterable[HALightRhythm], now: datetime) -> None:
        self.rhythms = {r.entry_id: r for r in rhythms}
        self.read_at = now
        self.known = True
        self.index()

    def index(self) -> None:
        self._lamps = {}
        self.missing = {}
        self.switches = {}
        for entity in self.model.entities.values():
            if entity.domain != "switch" or entity.config_entry_id not in self.rhythms:
                continue
            kinds = self.switches.setdefault(entity.config_entry_id, {})
            eid = entity.entity_id
            for word, kind in (
                ("sleep_mode", "sleep"),
                ("adapt_color", "color"),
                ("adapt_brightness", "brightness"),
            ):
                if word in eid:
                    kinds[kind] = eid
                    break
            else:
                kinds["main"] = eid
        for entry_id, rhythm in sorted(self.rhythms.items(), key=lambda r: r[1].title):
            gone = tuple(e for e in rhythm.lights if e not in self.model.entities)
            if gone:
                self.missing[entry_id] = gone
            for lamp in rhythm.lights:
                if lamp in self.model.entities:
                    self._lamps.setdefault(lamp, entry_id)

    def group_of(self, entity_id: str) -> HALightRhythm | None:
        """The group this lamp or group of lamps belongs to: itself listed, one of its lamps,
        or a group of lamps it is part of."""
        entry = self._lamps.get(entity_id)
        if entry is None:
            for member in self.model.groups.get(entity_id, ()):
                if (entry := self._lamps.get(member)) is not None:
                    break
        if entry is None:
            for group, members in self.model.groups.items():
                if entity_id in members and (entry := self._lamps.get(group)) is not None:
                    break
        if entry is not None:
            return self.rhythms.get(entry)
        if self.known and not self.rhythms and self.own_for is not None:
            return self.own_for(entity_id)  # no such integration here (E-471, B-385)
        return None

    def main_switch(self, rhythm: HALightRhythm) -> str | None:
        return self.switches.get(rhythm.entry_id, {}).get("main")

    def is_on(self, rhythm: HALightRhythm) -> bool:
        switch = self.main_switch(rhythm)
        state = self.states.get(switch) if switch else None
        return state is not None and state.state == "on"

    def ours(self, rhythm: HALightRhythm) -> bool:
        return rhythm.entry_id in self.taken or is_own(rhythm)

    def lamps_of(self, rhythm: HALightRhythm) -> list[str]:
        return [e for e in rhythm.lights if e in self.model.entities]

    def level(
        self,
        rhythm: HALightRhythm,
        now: datetime,
        shift: Callable[[str], float | None] = lambda _: None,
    ) -> tuple[float | None, int | None]:
        """What the group says now, plus what people here showed they prefer (E-467). None for
        what its settings leave alone, or everything when the house has no place."""
        kinds = self.switches.get(rhythm.entry_id, {})

        def on(kind: str, default: bool) -> bool:
            state = self.states.get(kinds[kind]) if kind in kinds else None
            return default if state is None else state.state == "on"

        if self.place is None:
            return None, None
        if on("sleep", False):
            return rhythm.sleep_brightness, round(rhythm.sleep_kelvin)
        latitude, longitude = self.place
        pct = brightness(rhythm, latitude, longitude, now, self.zone)
        warm = kelvin(rhythm, sun_position(rhythm, latitude, longitude, now, self.zone))
        pct += shift("brightness_pct") or 0.0
        warm += round(shift("color_temp_kelvin") or 0.0)
        return (
            max(1.0, min(100.0, pct)) if on("brightness", True) else None,
            warm if on("color", True) else None,
        )

    def on_data(
        self,
        entity_id: str,
        allowed: Collection[str],
        now: datetime,
        shift: Callable[[str], float | None] = lambda _: None,
        skill: str = "light_on_entry",
        part: str = "",
    ) -> dict[str, Any] | None:
        """What "on" says for this lamp, or None when it is not on a rhythm: nothing while the
        integration itself does it (B-382), so it does not take the lamp for someone's choice;
        the day's values once HouseMind does. A group someone else switched off is left out."""
        rhythm = self.group_of(entity_id)
        if rhythm is None:
            return None
        if is_own(rhythm):
            if skill != "light_on_entry" or part == "night":
                return None  # the night, and other kinds of light, keep what they learned
            shift = _no_shift  # what people choose already moves its limits
        if self.is_on(rhythm):
            return {}
        if not self.ours(rhythm):
            return None
        pct, warm = self.level(rhythm, now, shift)
        if pct is None and warm is None:
            return None
        if pct is not None and self.stands is not None:
            pct = float(self.stands.real(entity_id, pct))  # its own standen (B-409)
        return request_data(
            allowed, pct, fitted(entity_id, self.states, warm), rhythm.initial_transition_s
        )

    def still_there(self, lamp: str, state: State) -> bool:
        """Is this lamp still where HouseMind left it? If not, someone chose (B-383)."""
        left = self.following.get(lamp)
        if left is None:
            return False
        return not chosen(lamp_now(state), (left.pct, left.kelvin))


def _no_shift(_: str) -> float | None:
    return None


def is_own(rhythm: HALightRhythm) -> bool:
    return rhythm.entry_id.startswith(OWN)


def chosen(now: Level, left: Level) -> bool:
    """Is a lamp somewhere else than this, more than lamps round off by themselves (B-383)?"""
    return (now[0] is not None and left[0] is not None and abs(now[0] - left[0]) > OFF_BY_PCT) or (
        now[1] is not None and left[1] is not None and abs(now[1] - left[1]) > OFF_BY_KELVIN
    )


def lamp_now(state: State) -> Level:
    """The brightness in % and the colour in kelvin a lamp reports, as far as it does."""
    raw = state.attributes.get("brightness")
    warm = state.attributes.get("color_temp_kelvin")
    return (
        raw * 100 / 255 if isinstance(raw, int | float) else None,
        float(warm) if isinstance(warm, int | float) else None,
    )


def fitted(entity_id: str, states: Mapping[str, State], warm: int | None) -> int | None:
    """A colour within what this lamp can do, which is what it will report back."""
    state = states.get(entity_id)
    if warm is None or state is None:
        return warm
    low = state.attributes.get("min_color_temp_kelvin")
    high = state.attributes.get("max_color_temp_kelvin")
    if isinstance(low, int | float):
        warm = max(warm, round(low))
    if isinstance(high, int | float):
        warm = min(warm, round(high))
    return warm


def request_data(
    allowed: Collection[str], pct: float | None, warm: int | None, transition: float
) -> dict[str, Any]:
    """Only what this kind of lamp may be told."""
    data: dict[str, Any] = {}
    if pct is not None and "brightness_pct" in allowed:
        data["brightness_pct"] = max(1, round(pct))
    if warm is not None and "color_temp_kelvin" in allowed:
        data["color_temp_kelvin"] = warm
    if data and "transition" in allowed:
        data["transition"] = transition
    return data
