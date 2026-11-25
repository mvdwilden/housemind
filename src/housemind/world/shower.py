"""Someone in the shower (E-531, design 92).

Behind a shower curtain or glass, in the steam, a motion or presence sensor often loses the
person: the bathroom looks empty and its light goes out in the middle of a shower. The air says
it: in a bathroom where someone just was, humidity that climbs fast is a shower. From then on
someone is there – until the humidity has stopped climbing for five minutes and fell back a
little (the water is off), no new reading comes for a while, or after an hour at most. Then
the room's own sensors say it again. In the real house (21 and 23 November 2026) a shower
raised the bathroom's humidity by 4 to 11 points in a few minutes, jumping up and down by a
point on the way; washing hands or a quick wash, by 2 to 3.

Generic, no brand or model: every room whose role is a bathroom, with a humidity sensor that
measures the room's air – not one inside a humidifier, an airco or a boiler, and not one an
administrator excluded. The numbers are start values.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from housemind.core.model import HouseModel

RISE = 4.0  # percentage points of humidity …
WITHIN = timedelta(minutes=10)  # … within this long: a shower
FALL = 2.0  # this far below the highest since …
PEAK_QUIET = timedelta(minutes=5)  # … and no new highest for this long: the water is off
LONGEST = timedelta(minutes=60)  # a shower that never seems to end is not one
QUIET = timedelta(minutes=20)  # no new reading this long: over
SOMEONE_WITHIN = timedelta(minutes=15)  # someone was in the bathroom shortly before
# A sensor in one of these measures the device, not the room.


def _value(raw: str) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if 1.0 <= value <= 99.0 else None  # a broken or stuck sensor (E-43)


def shower_sensors(
    model: HouseModel, bathrooms: Iterable[str], excluded: Iterable[str] = ()
) -> dict[str, str]:
    """The humidity sensors that measure the air of a bathroom: sensor → its room."""
    rooms = set(bathrooms)
    sensors = model.air_sensors(("humidity",), excluded)
    return {entity_id: area_id for entity_id, area_id in sensors.items() if area_id in rooms}


@dataclass(slots=True)
class ShowerWatch:
    sensors: dict[str, str] = field(default_factory=dict)  # humidity sensor → its bathroom
    readings: dict[str, deque[tuple[datetime, float]]] = field(default_factory=dict)
    since: dict[str, datetime] = field(default_factory=dict)  # bathroom → a shower since
    peak: dict[str, float] = field(default_factory=dict)
    peak_at: dict[str, datetime] = field(default_factory=dict)
    value: dict[str, float] = field(default_factory=dict)  # its last reading in the shower …
    last: dict[str, datetime] = field(default_factory=dict)  # … and when

    def index(self, sensors: dict[str, str]) -> None:
        self.sensors = dict(sensors)
        for gone in set(self.readings) - set(sensors):
            del self.readings[gone]
        rooms = set(sensors.values())
        for area_id in set(self.since) - rooms:
            self._end(area_id)

    def see(self, entity_id: str, raw: str, now: datetime, someone: bool) -> bool:
        """A new reading. ``someone``: someone is, or just was, in that bathroom. True when a
        shower began or ended."""
        area_id = self.sensors.get(entity_id)
        value = _value(raw)
        if area_id is None or value is None:
            return False
        kept = self.readings.setdefault(entity_id, deque())
        kept.append((now, value))
        # The readings of the last ten minutes, and the one in force when they began: a sensor
        # reports a change, so the humidity stayed at that one until the next.
        while len(kept) > 1 and now - kept[1][0] >= WITHIN:
            kept.popleft()
        if area_id in self.since:
            self.last[area_id], self.value[area_id] = now, value
            if value > self.peak.get(area_id, 0.0):
                self.peak[area_id], self.peak_at[area_id] = value, now
            return not self.showering(area_id, now)
        low = min(v for _, v in kept)
        if someone and value - low >= RISE:
            self.since[area_id] = now
            self.peak[area_id], self.peak_at[area_id] = value, now
            self.value[area_id], self.last[area_id] = value, now
            return True
        return False

    def _end(self, area_id: str) -> None:
        for kept in (self.since, self.peak, self.peak_at, self.value, self.last):
            kept.pop(area_id, None)

    def showering(self, area_id: str, now: datetime) -> bool:
        start = self.since.get(area_id)
        if start is None:
            return False
        peak = self.peak.get(area_id, 0.0)
        water_off = (
            now - self.peak_at.get(area_id, start) >= PEAK_QUIET
            and self.value.get(area_id, peak) <= peak - FALL
        )
        if now - start >= LONGEST or now - self.last.get(area_id, start) >= QUIET or water_off:
            self._end(area_id)
            return False
        return True

    def rooms(self, now: datetime) -> frozenset[str]:
        return frozenset(a for a in list(self.since) if self.showering(a, now))
