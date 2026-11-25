"""Zones of a presence sensor (E-215, B-75).

Some presence sensors (mmWave) report zones: one sensor in a kitchen can tell "someone at the
counter" from "someone at the dining table". HA shows each zone as its own binary sensor on
the device. One sorting for this input (B-68), from what HA reports – no brand or model:

- a zone is a presence, occupancy or motion sensor on a device with more than one of them,
  whose own name has a zone word (language files) or is exactly a room's name or alias;
- its room: the room HA gives the zone entity itself when that differs from its device's
  (the administrator's choice in HA); else a room on the same floor whose name or alias is
  exactly the zone's name ("Zone: Dining room" on the kitchen sensor counts for the dining
  room); else the sensor's own room;
- in its own room a zone is a part of the room ("Bed", "Wardrobe", "Zone 1"), unless its
  name is (part of) the room's name – then it is the room itself;
- lamps in that room with the part's name in their own name belong to the part and follow
  it; a part with a bed word never switches a light on;
- the device's other presence sensors ("someone in range") count for their room only while
  none of its zones sees someone (world/occupancy.py).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from housemind.core.model import Area, Entity, HouseModel
from housemind.knowledge.words import plain as words_plain

ZONE_CLASSES = frozenset({"presence", "occupancy", "motion"})


@dataclass(frozen=True, slots=True)
class Zone:
    entity_id: str
    device_id: str
    name: str  # its own name: "Dining room", "Bed", "Zone 1"
    area_id: str  # the room it counts for
    sensor_area: str  # the room the sensor itself is in
    part: bool  # a part of its room, not the room itself
    bed: bool
    lamps: tuple[str, ...] = ()  # lamps (and their group) that follow this part


@dataclass(slots=True)
class Zones:
    zones: dict[str, Zone] = field(default_factory=dict)
    # The other presence sensors of a device with zones ("someone in range") → device id.
    general: dict[str, str] = field(default_factory=dict)
    lamp_zone: dict[str, str] = field(default_factory=dict)  # lamp → the zone it follows

    def of_device(self, device_id: str) -> list[Zone]:
        return [z for z in self.zones.values() if z.device_id == device_id]

    def in_area(self, area_id: str) -> list[Zone]:
        return sorted(
            (z for z in self.zones.values() if z.area_id == area_id), key=lambda z: z.name
        )


def plain(text: str) -> str:
    """Lower case, no accents, words only: "Walk-in Closet" → "walk in closet" (one rule for
    every language, code QA 3, punt 4)."""
    return words_plain(text)


def _has(haystack: str, needle: str) -> bool:
    """The words of ``needle`` appear together, as whole words, in ``haystack``."""
    return bool(needle) and f" {needle} " in f" {haystack} "


def _own(entity: Entity, model: HouseModel, area: Area | None) -> str:
    """An entity's own name, without the name of its device and its room."""
    name = plain(entity.name)
    device = model.devices.get(entity.device_id or "")
    for other in (device.name if device else "", area.name if area else ""):
        prefix = plain(other)
        if prefix and _has(name, prefix) and name != prefix:
            name = f" {name} ".replace(f" {prefix} ", " ").strip()
    return name


def _zone_name(entity: Entity, model: HouseModel, area: Area | None, presence: list[str]) -> str:
    """The zone's name for people: "Zone: Dining room" → "Dining room", "Zone 1 Presence" →
    "Zone 1". In the original spelling where possible."""
    raw = entity.name
    device = model.devices.get(entity.device_id or "")
    for other in (device.name if device else "", area.name if area else ""):
        if other and raw.lower().startswith(other.lower()) and len(raw) > len(other):
            raw = raw[len(other) :]
    raw = re.sub(r"^\W*\w+\s*:\s*", "", raw) if ":" in raw else raw  # "Zone: Kitchen"
    for word in presence:
        raw = re.sub(rf"(?i)(?<!\w){re.escape(word)}(?!\w)", " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" -–:_")
    return raw or entity.name


def sort_zones(
    model: HouseModel,
    zone_words: Iterable[str],
    presence_words: Iterable[str],
    bed_words: Iterable[str],
    excluded: Iterable[str] = (),
) -> Zones:
    words = [plain(w) for w in zone_words if plain(w)]
    presence = sorted({w for w in presence_words if w}, key=len, reverse=True)
    beds = [plain(w) for w in bed_words if plain(w)]
    skip = set(excluded)
    by_device: dict[str, list[Entity]] = defaultdict(list)
    for entity in model.entities.values():
        if (
            entity.domain == "binary_sensor"
            and entity.device_class in ZONE_CLASSES
            and entity.device_id is not None
            and not (entity.hidden or entity.disabled or entity.entity_category)
            and entity.entity_id not in skip
        ):
            by_device[entity.device_id].append(entity)
    out = Zones()
    for device_id, sensors in by_device.items():
        if len(sensors) < 2:
            continue
        device = model.devices.get(device_id)
        sensor_area = model.areas.get(device.area_id or "") if device else None
        found: list[Zone] = []
        for entity in sensors:
            own_area = model.area_of(entity.entity_id)
            home = sensor_area or own_area
            if home is None:
                continue
            own = _own(entity, model, home)
            name = _zone_name(entity, model, home, presence)
            named = _named_room(model, plain(name), home)
            if not (any(_has(own, w) for w in words) or named is not None):
                continue
            # Placed in another room in HA: the administrator's choice comes first.
            moved = own_area is not None and own_area.id != home.id
            room = own_area if moved and own_area is not None else named or home
            part = room.id == home.id and not _has(plain(home.name), plain(name))
            found.append(
                Zone(
                    entity.entity_id,
                    device_id,
                    name,
                    room.id,
                    home.id,
                    part,
                    part and any(_has(plain(name), b) for b in beds),
                )
            )
        if not found:
            continue
        zone_ids = {z.entity_id for z in found}
        for entity in sensors:
            if entity.entity_id not in zone_ids:
                out.general[entity.entity_id] = device_id
        for zone in found:
            lamps = _lamps(model, zone) if zone.part and not zone.bed else ()
            zone = replace(zone, lamps=lamps)
            out.zones[zone.entity_id] = zone
            for lamp in lamps:
                out.lamp_zone.setdefault(lamp, zone.entity_id)
    return out


def _named_room(model: HouseModel, own: str, home: Area) -> Area | None:
    """Another room on the same floor whose name or alias is exactly the zone's name."""
    if not own:
        return None
    for area in model.areas.values():
        if area.id == home.id:
            continue
        if home.floor_id and area.floor_id and area.floor_id != home.floor_id:
            continue
        if own in {plain(n) for n in (area.name, *area.aliases)} - {""}:
            return area
    return None


def _lamps(model: HouseModel, zone: Zone) -> tuple[str, ...]:
    """Lamps in the zone's room with the zone's name in their name or their device's name:
    "Wardrobe lamp 1"."""
    wanted = plain(zone.name)
    if not wanted or wanted.isdigit():
        return ()
    found = []
    for entity in model.entities.values():
        if entity.domain != "light" or entity.hidden or entity.disabled:
            continue
        lamp_area = model.area_of(entity.entity_id)
        if lamp_area is None or lamp_area.id != zone.area_id:
            continue
        device = model.devices.get(entity.device_id or "")
        if _has(plain(entity.name), wanted) or (device and _has(plain(device.name), wanted)):
            found.append(entity.entity_id)
    return tuple(sorted(found))


def open_spaces(zones: Zones, model: HouseModel) -> dict[str, set[str]]:
    """Rooms that share one presence sensor – a zone of it counts for another room on the same
    floor, like the dining part of an open kitchen – are one open space for light (E-268): a
    lamp on in one is light in the other."""
    out: dict[str, set[str]] = defaultdict(set)
    for zone in zones.zones.values():
        here, there = model.areas.get(zone.sensor_area), model.areas.get(zone.area_id)
        if here is None or there is None or here.id == there.id:
            continue
        if (
            here.floor_id is not None
            and there.floor_id is not None
            and here.floor_id != there.floor_id
        ):
            continue
        out[here.id].add(there.id)
        out[there.id].add(here.id)
    return dict(out)
