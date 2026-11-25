"""Speel het na (E-475, E-518, E-519, design 70.2, 85): a situation laid over the picture of
now, for one room.

Nothing here touches the house: it makes a changed copy of the picture (``WorldState``) and of
the states the skills read, and the test panel runs a quiet copy of the skills on it. The
choices come from what the room has (E-474): no 'raam open' without a window sensor. A part of
the day brings the light of that moment today, unless Donker or Licht is chosen, and choices
that contradict each other do not go together (E-519).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta

from housemind.core.model import Area, Entity, HouseModel, State
from housemind.safety.emergency import Emergency
from housemind.world.calendar import time_bucket
from housemind.world.house_mode import HouseMode
from housemind.world.occupancy import AreaOccupancy
from housemind.world.sleep import SleepView
from housemind.world.world import WorldState

PEOPLE = ("someone", "nobody")
BED = ("in_bed", "out_of_bed")  # a bedroom that sees who sleeps (E-483)
LIGHT = ("dark", "light")
OPENING = ("window",)
AIR = ("humid",)
WARMTH = ("warmer", "colder")
HOUSE = ("away", "home", "sleep")
PARTS = ("morning", "day", "evening", "night")
EMERGENCIES = {
    "smoke": Emergency.SMOKE,
    "co": Emergency.CARBON_MONOXIDE,
    "gas": Emergency.GAS,
    "leak": Emergency.LEAK,
    "frost": Emergency.FROST,
}
ALARM = "alarm"  # the alarm in use goes off (E-541): every room, when the house has one
# Only one of each group at a time: choosing one takes the other away.
GROUPS: tuple[tuple[str, ...], ...] = (PEOPLE, BED, LIGHT, WARMTH, ("away", "home"), PARTS)
# Choices that contradict each other (E-519): choosing one takes the others away, both ways.
CONFLICTS: dict[str, tuple[str, ...]] = {
    "away": ("someone", "in_bed", "out_of_bed", "sleep"),
    "someone": ("away",),
    "in_bed": ("away",),
    "out_of_bed": ("away",),
    "sleep": ("away",),
    # The alarm going off and an emergency are each their own test (E-541).
    ALARM: tuple(EMERGENCIES),
    **{key: (ALARM,) for key in EMERGENCIES},
}
# A moment that belongs to each part of the day: its own half hour counts for what was learned.
AT = {"morning": time(7, 30), "day": time(13, 0), "evening": time(20, 0), "night": time(23, 30)}
WARMER_C = 1.5
HUMID_PCT = 85.0
PRESENCE = ("motion", "presence", "occupancy")
OPENINGS = ("window", "door", "opening", "garage_door")


@dataclass(frozen=True, slots=True)
class Room:
    """What a room has that a situation can act on."""

    presence: tuple[str, ...] = ()  # motion, presence and occupancy sensors
    openings: tuple[str, ...] = ()  # windows and doors
    humidity: tuple[str, ...] = ()
    temperature: tuple[str, ...] = ()  # sensors and thermostats that measure the room
    emergencies: dict[str, str] = field(default_factory=dict)  # key → its sensor
    sleep: str = "none"  # a bedroom's sign of someone sleeping: "bed", "room" or "none"


def _usable(entity: Entity) -> bool:
    return not (entity.hidden or entity.disabled or entity.entity_category)


def room_of(
    model: HouseModel,
    area_id: str,
    emergency_for: Callable[[str], Emergency | None],
    sleep: str = "none",
    area_for: Callable[[str], Area | None] | None = None,
) -> Room:
    """What this room has, from Home Assistant's own classes – never from names.
    ``area_for``: the room a sensor senses – a zone of a presence sensor counts for its own
    room, also when HA puts it in the room of the sensor (E-530: the dining room's zone on the
    kitchen sensor is the dining room's, not the kitchen's)."""
    presence: list[str] = []
    openings: list[str] = []
    humidity: list[str] = []
    temperature: list[str] = []
    emergencies: dict[str, str] = {}
    for entity_id in sorted(model.entities):
        entity = model.entities[entity_id]
        area = area_for(entity_id) if area_for is not None else model.area_of(entity_id)
        if area is None or area.id != area_id or not _usable(entity):
            continue
        if entity.domain == "binary_sensor":
            found = emergency_for(entity_id)
            key = next((k for k, e in EMERGENCIES.items() if e is found), None)
            if key is not None:
                emergencies.setdefault(key, entity_id)
            elif entity.device_class in PRESENCE:
                presence.append(entity_id)
            elif entity.device_class in OPENINGS:
                openings.append(entity_id)
        elif entity.domain == "sensor" and entity.device_class == "humidity":
            humidity.append(entity_id)
        elif (entity.domain == "sensor" and entity.device_class == "temperature") or (
            entity.domain == "climate"
        ):
            temperature.append(entity_id)
    return Room(
        tuple(presence), tuple(openings), tuple(humidity), tuple(temperature), emergencies, sleep
    )


def available(room: Room, persons: bool, alarm: bool = False) -> list[str]:
    """The situations that can be chosen for this room, in the order of the panel. ``alarm``:
    the house has an alarm in use – it can go off in every room (E-541)."""
    out: list[str] = []
    if room.presence:
        out += PEOPLE
    if room.sleep != "none":
        out += BED
    out += LIGHT  # the sun or the room's own light: every house knows dark from light
    if room.openings:
        out += OPENING
    if room.humidity:
        out += AIR
    if room.temperature:
        out += WARMTH
    out += HOUSE if persons else ("sleep",)
    out += PARTS
    out += [ALARM] if alarm else []
    out += [key for key in EMERGENCIES if key in room.emergencies]
    return out


def choose(chosen: Iterable[str], key: str, on: bool, allowed: Collection[str]) -> list[str]:
    """The situations after a tap: one of a group takes the others of that group away."""
    out = [k for k in chosen if k in allowed]
    if key not in allowed:
        return out
    if on:
        for group in GROUPS:
            if key in group:
                out = [k for k in out if k not in group]
        out = [k for k in out if k not in CONFLICTS.get(key, ())]
        out.append(key)
    else:
        out = [k for k in out if k != key]
    return [k for k in allowed if k in out]  # the panel's own order


def moment(world: WorldState, chosen: Collection[str]) -> datetime | None:
    """The moment a chosen part of the day stands for, today in the house's own time."""
    part = next((k for k in PARTS if k in chosen), None)
    if part is None:
        return None
    return world.local.replace(hour=AT[part].hour, minute=AT[part].minute, second=0, microsecond=0)


def implied(
    world: WorldState,
    chosen: Collection[str],
    dark_at: Callable[[datetime], tuple[bool, float | None]] | None,
) -> str | None:
    """'dark' or 'light' when a chosen part of the day brings it and neither is chosen (E-519)."""
    when = moment(world, chosen)
    if when is None or dark_at is None or any(k in chosen for k in LIGHT):
        return None
    return "dark" if dark_at(when)[0] else "light"


def _state(states: dict[str, State], entity_id: str, now: datetime) -> State:
    return states.get(entity_id) or State(entity_id, "unknown", {}, now)


def _number(value: object) -> float | None:
    try:
        return float(str(value))
    except ValueError:
        return None


def apply(
    world: WorldState,
    states: dict[str, State],
    area_id: str,
    chosen: Collection[str],
    room: Room,
    persons: Iterable[str],
    dark_at: Callable[[datetime], tuple[bool, float | None]] | None = None,
) -> tuple[WorldState, dict[str, State]]:
    """The picture and the states as they would be in this situation – copies; the real
    ones stay as they are. ``dark_at``: whether it is dark outside at another moment, and the
    sun's height then – a part of the day brings its own light (E-519)."""
    now = world.now
    out = dict(states)
    changes: dict[str, object] = {}
    occupancy = dict(world.occupancy)
    info = occupancy.get(area_id) or AreaOccupancy(area_id, 0.0, False, False, None)
    if "someone" in chosen:
        occupancy[area_id] = replace(
            info, probability=1.0, occupied=True, still=False, last_activity=now, for_lamps=True
        )
        for sensor in room.presence:
            out[sensor] = replace(_state(states, sensor, now), state="on", last_changed=now)
    elif "nobody" in chosen or "sleep" in chosen:
        # 'Het huis slaapt' is everyone in bed: nobody up in this room any more, unless someone
        # comes in (E-517) – also right after a lamp was put on here by hand. Just gone to bed,
        # so the room is empty since now: what the house does then is Welterusten's, not 'nobody
        # there for a while' as well – one line per lamp. 'Niemand' is empty for a while.
        gone = now - timedelta(hours=2) if "nobody" in chosen else now
        occupancy[area_id] = replace(
            info,
            probability=0.0,
            occupied=False,
            still=False,
            last_activity=gone,
            for_lamps=False,
            zones=tuple(replace(z, on=False) for z in info.zones),
            # Asleep is no sensor seeing an empty room: a room without one keeps its lamps
            # from 'nobody there' (E-462); only Welterusten speaks for it (E-517).
            sensed=True if "nobody" in chosen else info.sensed,
        )
        for sensor in room.presence:
            out[sensor] = replace(_state(states, sensor, now), state="off", last_changed=gone)
    changes["occupancy"] = occupancy
    seen = world.sleep.get(area_id)
    signal = seen.signal if seen is not None else room.sleep
    if "in_bed" in chosen:
        changes["sleep"] = {**world.sleep, area_id: SleepView(signal, True, True, None)}
    elif "out_of_bed" in chosen:  # up half an hour ago, after a night here
        up = SleepView(signal, False, True, now - timedelta(minutes=30))
        changes["sleep"] = {**world.sleep, area_id: up}
    for key, dark in (("dark", True), ("light", False)):
        if key in chosen:
            changes["dark"] = {**world.dark, area_id: dark}
            changes["lux"] = {**world.lux, area_id: 1.0 if dark else 500.0}
            changes["outside_dark"] = dark
            changes["sun"] = -10.0 if dark else 30.0
    brought = implied(world, chosen, dark_at)
    when = moment(world, chosen)
    if brought is not None and when is not None and dark_at is not None:
        # Avond in November is dark, in June light: as the sun says at that moment today.
        dark, sun = dark_at(when)
        changes["dark"] = {**world.dark, area_id: dark}
        changes["lux"] = {**world.lux, area_id: 1.0 if dark else 500.0}
        changes["outside_dark"] = dark
        changes["sun"] = sun if sun is not None else (-10.0 if dark else 30.0)
    if "window" in chosen:
        for sensor in room.openings:
            out[sensor] = replace(_state(states, sensor, now), state="on", last_changed=now)
    if "humid" in chosen:
        for sensor in room.humidity:
            old = _state(states, sensor, now)
            value = max(HUMID_PCT, (_number(old.state) or 0.0) + 25.0)
            out[sensor] = replace(old, state=f"{value:.0f}", last_changed=now)
    for key, delta in (("warmer", WARMER_C), ("colder", -WARMER_C)):
        if key not in chosen:
            continue
        for entity_id in room.temperature:
            old = _state(states, entity_id, now)
            if entity_id.startswith("climate."):
                measured = _number(old.attributes.get("current_temperature"))
                if measured is not None:
                    attributes = {**old.attributes, "current_temperature": measured + delta}
                    out[entity_id] = replace(old, attributes=attributes)
            elif (measured := _number(old.state)) is not None:
                out[entity_id] = replace(old, state=f"{measured + delta:.1f}", last_changed=now)
    residents = world.residents_home
    mode = world.mode
    if "away" in chosen:
        residents, mode = 0, HouseMode.AWAY
        for person in persons:
            out[person] = replace(_state(states, person, now), state="not_home", last_changed=now)
    elif "home" in chosen or ("someone" in chosen and mode in (HouseMode.AWAY, HouseMode.VACATION)):
        # Someone coming in counts as being home – a test of a room in an empty house too.
        residents, mode = max(1, residents), HouseMode.HOME
        if "home" in chosen:
            for person in persons:
                out[person] = replace(_state(states, person, now), state="home", last_changed=now)
    if "sleep" in chosen:
        # The house goes to sleep now – also when it already sleeps: Welterusten is about this
        # moment (E-517).
        mode = HouseMode.SLEEP
        changes.update(
            part="night", part_since=now, part_how="pretend", bedtime=True, mode_since=now
        )
    if mode is not world.mode:
        changes.update(mode=mode, mode_since=now)
    changes["residents_home"] = residents
    for part in PARTS:
        if part in chosen:
            local = world.local.replace(
                hour=AT[part].hour, minute=AT[part].minute, second=0, microsecond=0
            )
            changes.update(
                local=local,
                bucket=time_bucket(local),
                part=part,
                part_since=local,
                part_how="pretend",
                bedtime=part == "night",
            )
    return replace(world, **changes), out  # type: ignore[arg-type]
