"""Who still sleeps in a bedroom (E-482, E-483, design 71.3).

Each bedroom gets the best sign the house has, from what Home Assistant reports – no brand:

- **bed**: a zone of a presence sensor whose name has a bed word (world/zones.py), or a
  presence or occupancy sensor in the room with a bed word in its own name (a sensor in or
  under the bed). Out of bed: that sign is off.
- **room**: no bed sign, but a sensor that also sees someone lying still (presence or
  occupancy, or a zone of the room). Out of bed: the room is empty.
- **none**: only a motion sensor, or nothing: HouseMind cannot see whether someone sleeps.

Per room, not per person: HouseMind does not know who sleeps where. A sensor with a bed word
that measures no presence (the plug of an electric blanket) is no sign.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from housemind.core.model import HouseModel, State
from housemind.world.zones import Zones, plain

PRESENCE = frozenset({"presence", "occupancy"})
SLEPT = timedelta(minutes=30)  # in bed this long, at night, is a night's sleep
OFFLINE = frozenset({"unavailable", "unknown"})


@dataclass(frozen=True, slots=True)
class SleepView:
    signal: str  # "bed", "room" or "none"
    asleep: bool  # someone is in bed (bed) or in the room (room) now
    slept: bool  # someone slept here last night
    empty_since: datetime | None  # since when nobody is in bed (bed) or in the room (room)
    sensors: tuple[str, ...] = ()  # what it looks at, for Expert


def night_of(local: datetime) -> date:
    """The night a moment belongs to, named after its evening: noon to noon."""
    return (local - timedelta(hours=12)).date()


def _still(model: HouseModel, entity_id: str) -> bool:
    """A sensor that also sees someone lying still: presence or occupancy, not motion."""
    entity = model.entities.get(entity_id)
    return entity is not None and entity.device_class in PRESENCE


def _bed_word(name: str, beds: Iterable[str]) -> bool:
    text = f" {plain(name)} "
    return any(f" {b} " in text for b in beds if b)


@dataclass(slots=True)
class SleepWatch:
    """Per bedroom the sign it has, since when it is on or off, and the night someone slept
    there. Fed with the picture of the house each round of the brain."""

    bed_words: tuple[str, ...] = ()
    signals: dict[str, tuple[str, tuple[str, ...]]] = field(default_factory=dict)
    on_since: dict[str, datetime] = field(default_factory=dict)
    empty_since: dict[str, datetime] = field(default_factory=dict)
    night_seen: set[str] = field(default_factory=set)  # asleep while the house slept
    slept: dict[str, date] = field(default_factory=dict)  # room → the night someone slept
    _first: dict[str, datetime] = field(default_factory=dict)  # on when HouseMind first looked

    def index(
        self,
        model: HouseModel,
        zones: Zones,
        bedrooms: Iterable[str],
        presence_ids: Mapping[str, set[str]],
        ignored: set[str],
    ) -> None:
        """Which sign each bedroom has – again after the house changed in HA."""
        beds = [plain(w) for w in self.bed_words]
        self.signals = {}
        for area_id in bedrooms:
            bed: list[str] = []
            for zone in zones.in_area(area_id):
                entity = model.entities.get(zone.entity_id)
                if (
                    zone.bed
                    and zone.entity_id not in ignored
                    and entity is not None
                    and entity.device_class in PRESENCE
                ):
                    bed.append(zone.entity_id)
            for entity in model.entities.values():
                if (
                    entity.domain != "binary_sensor"
                    or entity.device_class not in PRESENCE
                    or entity.entity_id in ignored
                    or entity.entity_id in zones.zones
                    or entity.entity_id in zones.general
                ):
                    continue
                area = model.area_of(entity.entity_id)
                if area is not None and area.id == area_id and _bed_word(entity.name, beds):
                    bed.append(entity.entity_id)
            room = sorted(
                e for e in presence_ids.get(area_id, set()) if e not in ignored and _still(model, e)
            )
            if bed:
                self.signals[area_id] = ("bed", tuple(sorted(bed)))
            elif room:
                self.signals[area_id] = ("room", tuple(room))
            else:
                self.signals[area_id] = ("none", ())

    def update(
        self, states: Mapping[str, State], now: datetime, local: datetime, night: bool
    ) -> None:
        """One look: ``night`` – the house sleeps, or it is its night. A stretch in bed of at
        least half an hour that saw the house's night is a night's sleep; so is someone already
        in bed when HouseMind started, if they get up before noon."""
        for area_id, (signal, sensors) in self.signals.items():
            if signal == "none":
                continue
            seen = [states.get(e) for e in sensors]
            if all(s is None or s.state in OFFLINE for s in seen):
                continue  # the sensor is away: nothing changes
            if any(s is not None and s.state == "on" for s in seen):
                if area_id not in self.on_since:
                    self.on_since[area_id] = now
                    if area_id not in self.empty_since and area_id not in self.slept:
                        self._first[area_id] = now  # already on at the first look
                self.empty_since.pop(area_id, None)
                if night:
                    self.night_seen.add(area_id)
                continue
            start = self.on_since.pop(area_id, None)
            if start is None:
                self.empty_since.setdefault(area_id, now)
                continue
            self.empty_since[area_id] = now
            first = self._first.pop(area_id, None) == start
            if (area_id in self.night_seen and now - start >= SLEPT) or (first and local.hour < 12):
                self.slept[area_id] = night_of(local)
            self.night_seen.discard(area_id)

    def view(self, area_id: str, local: datetime) -> SleepView | None:
        found = self.signals.get(area_id)
        if found is None:
            return None
        signal, sensors = found
        asleep = area_id in self.on_since
        slept = self.slept.get(area_id) == night_of(local) or (
            asleep and area_id in self.night_seen
        )
        return SleepView(signal, asleep, slept, self.empty_since.get(area_id), sensors)
