"""Even luchten – stale air gets a word (E-532, design 92).

Where someone is, CO₂ that stays at or above 1,200 ppm for a quarter of an hour gets one
heads-up for that room: "The air in the bedroom is stuffy (1,250 ppm CO₂). Open a window or the
door for a while." At most once per room per day; it goes away by itself once the air is fresh
again (below 1,000 ppm), and "Niet meer tonen" ends it for that room. While the house sleeps no
message: the highest CO₂ of the night goes into the morning briefing instead – "Last night the
CO₂ in the bedroom rose to 1,600 ppm." No fan or window sensor is needed; HouseMind only says
it, it opens nothing.

Generic, no brand or model: every CO₂ sensor that measures a room's air – not one inside a
humidifier, an airco or a boiler, and not one an administrator excluded. The numbers are start
values.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from housemind.core.model import HouseModel, State

CO2_HIGH = 1200.0  # ppm: stale …
CO2_FOR = timedelta(minutes=15)  # … for this long, with someone there
CO2_FRESH = 1000.0  # below this the heads-up goes away
CO2_CLASSES = frozenset({"carbon_dioxide", "co2"})


def co2_by_room(
    model: HouseModel, states: Mapping[str, State], excluded: Iterable[str] = ()
) -> dict[str, float]:
    """The CO₂ of each room that measures it: the highest of its room sensors, in ppm."""
    out: dict[str, float] = {}
    for entity_id, area_id in model.air_sensors(CO2_CLASSES, excluded).items():
        state = states.get(entity_id)
        if state is None:
            continue
        try:
            value = float(state.state)
        except ValueError:
            continue
        if not 350.0 <= value <= 5000.0:
            continue  # a broken or stuck sensor (E-43)
        out[area_id] = max(out.get(area_id, 0.0), value)
    return out


def ppm_text(value: float, language: str) -> str:
    """1600 as people read it: "1.600" in Dutch and German, "1,600" in English, "1 600" in
    French."""
    whole = f"{round(value):,}"
    sep = {"nl": ".", "de": ".", "fr": " "}.get(language.split("-")[0], ",")
    return whole.replace(",", sep)


@dataclass(slots=True)
class AirWatch:
    """Per room: since when its air is stale with someone there, the day it was told, and the
    highest CO₂ of the last night. ``told`` and ``night`` are kept in the setting ``air_watch``
    (so a restart neither tells twice nor forgets the night); ``high_since`` is not."""

    high_since: dict[str, datetime] = field(default_factory=dict)
    told: dict[str, str] = field(default_factory=dict)  # room → the day (local date)
    night: dict[str, float] = field(default_factory=dict)  # room → highest ppm while asleep
    night_day: str = ""  # the day (local date) that night began
    asleep: bool = False
    changed: bool = False

    @classmethod
    def load(cls, raw: Any) -> AirWatch:
        watch = cls()
        if isinstance(raw, dict):
            told, night = raw.get("told"), raw.get("night")
            if isinstance(told, dict):
                watch.told = {str(k): str(v) for k, v in told.items()}
            if isinstance(night, dict):
                watch.night = {
                    str(k): float(v) for k, v in night.items() if isinstance(v, int | float)
                }
            watch.asleep = bool(raw.get("asleep"))
            watch.night_day = str(raw.get("night_day") or "")
        return watch

    def dump(self) -> dict[str, Any]:
        return {
            "told": dict(self.told),
            "night": dict(self.night),
            "night_day": self.night_day,
            "asleep": self.asleep,
        }

    def see(
        self,
        co2: Mapping[str, float],
        there: set[str],
        asleep: bool,
        day: str,
        now: datetime,
    ) -> tuple[list[tuple[str, float]], set[str]]:
        """One look (every quarter of an hour). ``there``: the rooms where someone is.
        Returns the rooms to tell now, and the rooms whose heads-up stays."""
        if asleep and not self.asleep:
            self.night, self.night_day = {}, day  # a new night
        if asleep != self.asleep:
            self.asleep = asleep
            self.changed = True
        tell: list[tuple[str, float]] = []
        keep: set[str] = set()
        for area_id, ppm in sorted(co2.items()):
            if self.told.get(area_id) == day and ppm >= CO2_FRESH:
                keep.add(area_id)
            if ppm < CO2_HIGH or area_id not in there:
                self.high_since.pop(area_id, None)
                continue
            if asleep:
                if ppm > self.night.get(area_id, 0.0):
                    self.night[area_id] = round(ppm)
                    self.changed = True
                self.high_since.pop(area_id, None)
                continue
            since = self.high_since.setdefault(area_id, now)
            if now - since < CO2_FOR or self.told.get(area_id) == day:
                continue
            self.told[area_id] = day
            self.changed = True
            tell.append((area_id, ppm))
            keep.add(area_id)
        for area_id in [a for a, d in self.told.items() if d < day]:
            del self.told[area_id]  # only today's are needed
            self.changed = True
        return tell, keep

    def last_night(self, today: date) -> tuple[str, float] | None:
        """The room whose air was worst last night, once the house is awake again – only the
        night that ended this morning."""
        if self.asleep or not self.night:
            return None
        if self.night_day not in (today.isoformat(), (today - timedelta(days=1)).isoformat()):
            return None
        area_id = max(self.night, key=lambda a: (self.night[a], a))
        return area_id, self.night[area_id]
