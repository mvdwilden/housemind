"""Water (domain 11): what the house uses, and a leak seen from water that keeps running.

The meters come from HA's energy settings (water sources) or are water sensors that only go up;
a flow sensor helps too. A leak shows as water that runs in every quarter of an hour for a
long time while nobody uses it: the house sleeps (two hours) or nobody is home (half an hour).
Then HouseMind reports it at once and – with a main water valve – asks or, when it may, closes
it: closing is the only direction it may ever take (safe direction, E-58).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from housemind.core.model import HouseModel, State

QUARTER = timedelta(minutes=15)
TO_LITRES = {"m³": 1000.0, "m3": 1000.0, "l": 1.0, "L": 1.0, "gal": 3.785, "ft³": 28.317}
FLOW_TO_L_MIN = {"L/min": 1.0, "l/min": 1.0, "m³/h": 1000.0 / 60, "gal/min": 3.785}


def meters(model: HouseModel, states: dict[str, State], configured: list[str]) -> list[str]:
    """Water meters: the ones in HA's energy settings, else water sensors that only go up."""
    if configured:
        return [m for m in configured if m in states]
    found = []
    for entity in model.entities.values():
        state = states.get(entity.entity_id)
        if (
            state is None
            or entity.domain != "sensor"
            or entity.device_class != "water"
            or (entity.unit or "") not in TO_LITRES
        ):
            continue
        if str(state.attributes.get("state_class", "total_increasing")) in (
            "total",
            "total_increasing",
        ):
            found.append(entity.entity_id)
    return sorted(found)


# Liquid flow units. m³/h is also what ventilation reports, so it only counts on a water meter.
LIQUID_FLOW = frozenset({"L/min", "l/min", "L/h", "l/h", "gal/min", "mL/s"})
# A device that moves air or heats water: its flow is no tap running.
NOT_TAP_WATER = frozenset({"climate", "fan", "water_heater", "humidifier"})


def flow_sensors(model: HouseModel, meters: list[str] | None = None) -> list[str]:
    """Flow sensors that measure tap water, so a ventilation unit's air flow is never a leak.
    On a water meter's own device every flow counts; elsewhere only a liquid unit, and never on
    a device that moves air or heats water."""
    meter_devices = {model.entities[m].device_id for m in meters or () if m in model.entities} - {
        None
    }
    found = []
    for entity in model.entities.values():
        if entity.domain != "sensor" or entity.device_class != "volume_flow_rate":
            continue
        if entity.hidden or entity.disabled or entity.entity_category:
            continue
        if entity.device_id in meter_devices:
            found.append(entity.entity_id)
            continue
        family = (
            {e.domain for e in model.on_device(entity.device_id)} if entity.device_id else set()
        )
        if (entity.unit or "") in LIQUID_FLOW and not family & NOT_TAP_WATER:
            found.append(entity.entity_id)
    return sorted(found)


def litres(state: State | None) -> float | None:
    if state is None or not state.available:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = str(state.attributes.get("unit_of_measurement") or "m³")
    return value * TO_LITRES.get(unit, 1000.0)


def flow_l_min(state: State | None) -> float | None:
    if state is None or not state.available:
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = str(state.attributes.get("unit_of_measurement") or "L/min")
    return value * FLOW_TO_L_MIN.get(unit, 1.0)


@dataclass(slots=True)
class WaterWatch:
    """Follows the meters every tick; remembers each quarter whether water ran."""

    meters: list[str] = field(default_factory=list)
    flows: list[str] = field(default_factory=list)
    quarters: list[bool] = field(default_factory=list)  # newest last: did water run?
    _quarter_start: datetime | None = None
    _start_total: float | None = None
    _ran: bool = False
    day: str | None = None
    day_start: float | None = None
    today_l: float = 0.0
    days: dict[str, float] = field(default_factory=dict)  # day → litres (last 35 days)

    def total(self, states: dict[str, State]) -> float | None:
        values = [litres(states.get(m)) for m in self.meters]
        known = [v for v in values if v is not None]
        return sum(known) if known else None

    def update(self, states: dict[str, State], now: datetime, local_day: str) -> None:
        total = self.total(states)
        flowing = any((flow_l_min(states.get(f)) or 0.0) > 0.05 for f in self.flows)
        if self._quarter_start is None:
            self._quarter_start, self._start_total = now, total
        if flowing:
            self._ran = True
        if now - self._quarter_start >= QUARTER:
            ran = self._ran or (
                total is not None and self._start_total is not None and total > self._start_total
            )
            self.quarters.append(ran)
            del self.quarters[:-48]
            self._quarter_start, self._start_total, self._ran = now, total, False
        if total is not None:
            if self.day != local_day:
                if self.day is not None and self.day_start is not None:
                    self.days[self.day] = round(max(0.0, self.today_l), 1)
                    for old in sorted(self.days)[:-35]:
                        del self.days[old]
                self.day, self.day_start = local_day, total
            self.today_l = total - (self.day_start or total)

    def running_for(self) -> timedelta:
        """How long water has run in every quarter, without a break."""
        count = 0
        for ran in reversed(self.quarters):
            if not ran:
                break
            count += 1
        return QUARTER * count

    def suspect(self, mode: str, params: dict[str, Any]) -> bool:
        """Water that keeps running while nobody uses it."""
        running = self.running_for()
        if mode == "sleep":
            return running >= timedelta(hours=float(params.get("sleep_hours", 2)))
        if mode in ("away", "vacation"):
            return running >= timedelta(minutes=float(params.get("away_minutes", 30)))
        return running >= timedelta(hours=float(params.get("home_hours", 6)))

    def week_litres(self, today: str) -> float | None:
        recent = [v for d, v in sorted(self.days.items()) if d < today][-7:]
        return round(sum(recent)) if recent else None

    def to_json(self) -> dict[str, Any]:
        return {"days": self.days}

    def load(self, raw: Any) -> None:
        if isinstance(raw, dict) and isinstance(raw.get("days"), dict):
            self.days = {
                str(k): float(v) for k, v in raw["days"].items() if isinstance(v, int | float)
            }
