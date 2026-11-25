"""Maintenance and reminders (domain 16): a timely word, never an action.

- a battery that is almost empty (not phones, the home battery or the car);
- a smoke, CO, gas or leak sensor that has not answered for a day, for everyone, because safety
  depends on it – only when its whole device is silent, not one function of a device that
  answers (a camera with its smoke detection switched off); other devices are in Home
  Assistant netjes, for the administrators (E-354); gone for 14 days, a device counts as
  removed on purpose;
- a filter that needs replacing (a sensor that counts down);
- putting the bins out: the evening before a pickup, from a calendar or a waste sensor;
- a boiler that has not heated for more than a day and is far below its own setting (E-526):
  found in the house, a heat pump that stopped heating while nobody noticed until the shower
  was cold;
- water that runs away quietly in the night (E-535): a water meter that counts on in every
  hour between one and five, a little each hour – a dripping tap or a toilet that keeps
  running. In the real house the meter stood still in those hours, but for a single toilet;
- a device that reports a fault itself (E-537): a binary sensor of the class "problem" that
  stays on – overheating, overcurrent or overload is important, the rest information.
HA's own updates are counted for the house report only; HA already shows them itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from housemind.core.model import HouseModel, State
from housemind.knowledge.words import has_word as words_have

BATTERY_LOW = 15.0
OFFLINE_AFTER = timedelta(hours=24)
OFFLINE_FORGOTTEN = timedelta(days=14)  # gone this long: probably removed on purpose
PER_DAY = 5  # new maintenance words a day, safety sensors not counted
SAFETY_CLASSES = {"smoke", "carbon_monoxide", "gas", "moisture"}
WATCHED_DOMAINS = {"light", "cover", "climate", "lock", "fan", "valve", "binary_sensor"}
NO_VALUE = frozenset({"unavailable", "unknown"})
# A boiler that does not heat (E-526, B-450). 26 hours, not 24: a boiler that heats once a day
# at a fixed time never gets there, even when it starts a little later one day.
BOILER_NOT_HEATED = timedelta(hours=26)
BOILER_BELOW = 10.0  # degrees below its own setting (°C) …
BOILER_BELOW_F = 18.0  # … the same in °F
BOILER_RISE = 2.0  # this much above the lowest since: it heats (sensors step by 0.5 or 1)
# Water in the night (E-535): a reading at 1, 2, 3, 4 and 5 o'clock; every hour at least
# LEAK_MIN litres and at most LEAK_MAX (more is someone using water, or water that keeps running,
# which has its own message).
NIGHT_HOURS = (1, 2, 3, 4, 5)
LEAK_MIN_L = 0.5
LEAK_MAX_L = 60.0
LITRES = {"l": 1.0, "L": 1.0, "m³": 1000.0, "m3": 1000.0, "gal": 3.785, "ft³": 28.317}
# A device's own fault (E-537): on this long, so a blink is nothing.
PROBLEM_AFTER = timedelta(minutes=2)
DANGER_WORDS = (
    "overheat",
    "overtemperature",
    "overcurrent",
    "overpower",
    "overvoltage",
    "overload",
    "oververhit",
    "overbelast",
    "overspanning",
    "overstroom",
)


@dataclass(frozen=True, slots=True)
class Finding:
    topic: str
    kind: str  # "battery", "offline", "offline_safety", "filter", "waste", "boiler_cold"
    device: str
    value: str = ""
    target: str = ""  # a boiler's own setting


def _has_word(text: str, words: list[str]) -> bool:
    return words_have(text, words)  # one rule for every language (code QA 3, punt 4)


def _phone_devices(model: HouseModel) -> set[str]:
    return {
        e.device_id for e in model.entities.values() if e.domain == "device_tracker" and e.device_id
    }


def _energy_devices(model: HouseModel) -> set[str]:
    """Devices that measure power or energy: a home battery, a car, a charger, an inverter.
    Their "battery" is a state of charge, not a battery to replace."""
    return {
        e.device_id
        for e in model.entities.values()
        if e.device_id and e.domain == "sensor" and e.device_class in ("power", "energy")
    }


def batteries(model: HouseModel, states: dict[str, State], skip: set[str]) -> list[Finding]:
    phones = _phone_devices(model) | _energy_devices(model)
    found = []
    for entity in model.entities.values():
        if entity.entity_id in skip or entity.device_class != "battery":
            continue
        if entity.device_id and entity.device_id in phones:
            continue  # a phone tells its owner itself
        state = states.get(entity.entity_id)
        if state is None or not state.available:
            continue
        device = model.devices.get(entity.device_id or "")
        name = device.name if device else entity.name
        if entity.domain == "binary_sensor" and state.state == "on":
            found.append(Finding(f"maintenance:battery:{entity.entity_id}", "battery", name))
        elif entity.domain == "sensor":
            try:
                level = float(state.state)
            except ValueError:
                continue
            if level < BATTERY_LOW:
                found.append(
                    Finding(
                        f"maintenance:battery:{entity.entity_id}", "battery", name, f"{level:.0f}"
                    )
                )
    return found


def _device_answers(model: HouseModel, states: dict[str, State], entity_id: str) -> bool:
    """Another part of the same device still gives a value: the device itself answers, and
    only this one function is off – a camera whose smoke detection is switched off in its own
    settings is no silent smoke detector. Diagnostic and configuration parts do not count:
    some keep a value of their own when the device is gone."""
    device_id = model.entities[entity_id].device_id
    if not device_id:
        return False
    for other in model.entities.values():
        if (
            other.device_id != device_id
            or other.entity_id == entity_id
            or other.disabled
            or other.entity_category is not None
        ):
            continue
        state = states.get(other.entity_id)
        if state is not None and state.state not in NO_VALUE:
            return True
    return False


def offline(model: HouseModel, states: dict[str, State], now: datetime) -> list[Finding]:
    """Devices HouseMind relies on that have not answered for a day. A safety sensor counts
    only when its whole device is silent, not when the device answers and one function is
    off."""
    found = []
    seen_devices: set[str] = set()
    for entity in sorted(model.entities.values(), key=lambda e: e.entity_id):
        if entity.domain not in WATCHED_DOMAINS or entity.hidden or entity.disabled:
            continue
        if entity.domain == "binary_sensor" and entity.device_class not in SAFETY_CLASSES:
            continue
        state = states.get(entity.entity_id)
        if state is None or state.state != "unavailable":
            continue
        if not OFFLINE_AFTER <= now - state.last_changed < OFFLINE_FORGOTTEN:
            continue
        if _device_answers(model, states, entity.entity_id):
            continue
        key = entity.device_id or entity.entity_id
        if key in seen_devices:
            continue
        seen_devices.add(key)
        device = model.devices.get(entity.device_id or "")
        safety = entity.device_class in SAFETY_CLASSES
        found.append(
            Finding(
                f"maintenance:offline:{key}",
                "offline_safety" if safety else "offline",
                device.name if device else entity.name,
            )
        )
    return found


def filters(model: HouseModel, states: dict[str, State], words: list[str]) -> list[Finding]:
    """A filter sensor counting down: at most 10% or 7 days left."""
    found = []
    for entity in model.entities.values():
        if entity.domain != "sensor" or not _has_word(f"{entity.entity_id} {entity.name}", words):
            continue
        state = states.get(entity.entity_id)
        if state is None or not state.available:
            continue
        try:
            value = float(state.state)
        except ValueError:
            continue
        unit = str(state.attributes.get("unit_of_measurement") or "")
        if (unit == "%" and value <= 10) or (unit in ("d", "days", "dagen") and value <= 7):
            device = model.devices.get(entity.device_id or "")
            found.append(
                Finding(
                    f"maintenance:filter:{entity.entity_id}",
                    "filter",
                    device.name if device else entity.name,
                )
            )
    return found


def waste_from_sensors(
    model: HouseModel, states: dict[str, State], words: list[str], tomorrow: date
) -> list[str]:
    """Waste sensors (one per kind) whose next pickup date is tomorrow."""
    kinds = []
    for entity in model.entities.values():
        if entity.domain != "sensor" or not _has_word(f"{entity.entity_id} {entity.name}", words):
            continue
        state = states.get(entity.entity_id)
        if state is None:
            continue
        if state.state[:10] == tomorrow.isoformat():
            kinds.append(entity.name)
    return kinds


def waste_from_events(events: list[dict[str, str]], words: list[str]) -> list[str]:
    return [
        str(e.get("summary", "")).strip()
        for e in events
        if _has_word(str(e.get("summary", "")), words) and str(e.get("summary", "")).strip()
    ]


def _degrees(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class BoilerWatch:
    """Since when each boiler (``water_heater``) has not heated (E-526, B-450).

    ``since`` is kept in the setting ``boiler_watch`` and only written when it changes;
    ``low``, the lowest temperature since then, is not: after a restart it counts on from the
    temperature of that moment. A boiler that is off, on away, or a house on vacation is cold
    on purpose: its clock starts again once it is back."""

    since: dict[str, datetime] = field(default_factory=dict)
    low: dict[str, float] = field(default_factory=dict)
    changed: bool = False

    @classmethod
    def load(cls, raw: Any) -> BoilerWatch:
        watch = cls()
        if isinstance(raw, dict):
            for entity_id, when in raw.items():
                try:
                    watch.since[str(entity_id)] = datetime.fromisoformat(str(when))
                except ValueError:
                    continue
        return watch

    def dump(self) -> dict[str, str]:
        return {e: t.isoformat() for e, t in sorted(self.since.items())}

    def _restart(self, entity_id: str, now: datetime, temp: float | None) -> None:
        if self.since.get(entity_id) != now:
            self.since[entity_id] = now
            self.changed = True
        if temp is None:
            self.low.pop(entity_id, None)
        else:
            self.low[entity_id] = temp

    def _forget(self, entity_id: str) -> None:
        if self.since.pop(entity_id, None) is not None:
            self.changed = True
        self.low.pop(entity_id, None)

    def see(
        self,
        model: HouseModel,
        states: dict[str, State],
        now: datetime,
        fahrenheit: bool = False,
        away: bool = False,
    ) -> list[Finding]:
        """One look per quarter: the boilers that have not heated for more than a day and are
        far below their own setting."""
        below = BOILER_BELOW_F if fahrenheit else BOILER_BELOW
        rise = BOILER_RISE * (1.8 if fahrenheit else 1.0)
        found: list[Finding] = []
        seen: set[str] = set()
        for entity in sorted(model.entities.values(), key=lambda e: e.entity_id):
            if entity.domain != "water_heater" or entity.hidden or entity.disabled:
                continue
            state = states.get(entity.entity_id)
            if state is None or not state.available:
                continue  # silent: keep the clock, a device that went is for the Check-up
            entity_id = entity.entity_id
            seen.add(entity_id)
            temp = _degrees(state.attributes.get("current_temperature"))
            target = _degrees(state.attributes.get("temperature"))
            if (
                away
                or state.state == "off"
                or str(state.attributes.get("away_mode", "")).lower() == "on"
            ):
                self._forget(entity_id)  # the clock starts again once it is back on
                continue
            if temp is None:
                continue
            if entity_id not in self.since:
                self._restart(entity_id, now, temp)
                continue
            low = self.low.get(entity_id)
            if low is None or temp < low:
                self.low[entity_id] = low = temp
            if temp >= low + rise:
                self._restart(entity_id, now, temp)  # it heats
                continue
            if target is None or temp > target - below:
                continue
            if now - self.since[entity_id] < BOILER_NOT_HEATED:
                continue
            device = model.devices.get(entity.device_id or "")
            found.append(
                Finding(
                    f"maintenance:boiler:{entity_id}",
                    "boiler_cold",
                    device.name if device else entity.name,
                    f"{temp:.0f}",
                    f"{target:.0f}",
                )
            )
        boilers = {e.entity_id for e in model.entities.values() if e.domain == "water_heater"}
        for gone in set(self.since) - seen - boilers:
            self._forget(gone)  # no longer in the house
        return found


# -- water that runs away quietly in the night (E-535) ----------------------------------------


def water_meters(model: HouseModel, states: dict[str, State]) -> dict[str, float]:
    """Every water meter that counts up (``total`` or ``total_increasing``), in litres."""
    out: dict[str, float] = {}
    for entity in model.entities.values():
        if entity.domain != "sensor" or entity.device_class != "water":
            continue
        if entity.hidden or entity.disabled:
            continue
        state = states.get(entity.entity_id)
        counts_up = state is not None and state.attributes.get("state_class") in (
            "total",
            "total_increasing",
        )
        if state is None or not counts_up:
            continue
        factor = LITRES.get(str(state.attributes.get("unit_of_measurement", "")))
        try:
            value = float(state.state)
        except ValueError:
            continue
        if factor is not None:
            out[entity.entity_id] = value * factor
    return out


@dataclass(slots=True)
class WaterNight:
    """The meters at 1, 2, 3, 4 and 5 o'clock of one night, and what the morning concluded.
    Kept in memory only: a restart in the night skips that night."""

    readings: dict[str, dict[int, float]] = field(default_factory=dict)
    day: str = ""  # the local date these readings are of
    judged: str = ""  # the morning it was judged
    leaking: bool = False  # what that morning found
    litres: float = 0.0

    def see(self, meters: dict[str, float], local: datetime) -> bool | None:
        """One look (every quarter of an hour). Returns, once a morning after five, whether
        water ran away in every hour of the night; None at every other look."""
        day = local.date().isoformat()
        if self.day != day:
            self.readings, self.day = {}, day
        if local.hour in NIGHT_HOURS:
            for meter, litres in meters.items():
                self.readings.setdefault(meter, {}).setdefault(local.hour, litres)
        if local.hour < NIGHT_HOURS[-1] or self.judged == day:
            return None
        self.judged = day
        found: list[float] = []
        for hours in self.readings.values():
            if not all(h in hours for h in NIGHT_HOURS):
                continue  # a meter that came or went in the night, or a restart
            steps = [hours[h + 1] - hours[h] for h in NIGHT_HOURS[:-1]]
            if all(LEAK_MIN_L <= step <= LEAK_MAX_L for step in steps):
                found.append(hours[NIGHT_HOURS[-1]] - hours[NIGHT_HOURS[0]])
        self.leaking = bool(found)
        self.litres = max(found) if found else 0.0
        return self.leaking


# -- a device that reports a fault itself (E-537) ---------------------------------------------


def problems(model: HouseModel, states: dict[str, State], now: datetime) -> list[Finding]:
    """The faults devices report themselves: a "problem" sensor that has been on for two
    minutes. Overheating, overcurrent or overload is important ("problem_danger")."""
    found: list[Finding] = []
    for entity in sorted(model.entities.values(), key=lambda e: e.entity_id):
        if entity.domain != "binary_sensor" or entity.device_class != "problem":
            continue
        if entity.hidden or entity.disabled:
            continue
        state = states.get(entity.entity_id)
        if state is None or state.state != "on" or now - state.last_changed < PROBLEM_AFTER:
            continue
        device = model.devices.get(entity.device_id or "")
        label = " ".join(
            str(x).lower()
            for x in (entity.entity_id, entity.name, entity.original_name, entity.translation_key)
            if x
        )
        danger = any(word in label for word in DANGER_WORDS)
        what = entity.original_name or entity.name
        found.append(
            Finding(
                f"maintenance:problem:{entity.entity_id}",
                "problem_danger" if danger else "problem",
                device.name if device else entity.name,
                what,
            )
        )
    return found
