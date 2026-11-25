"""The energy setup of a house, found without any manual work (E-65).

Home Assistant's energy settings say which meters exist (grid, solar, battery, gas, water and
devices). From there HouseMind looks at the *devices* of those meters for live power sensors and
for what can be controlled (the export limit). The home battery and the car are only read
(E-363). Chargers, cars, heat pumps and
appliances are recognised by their HA data and by words from the language files
(``energy_words.*``), so a new language adds words without code.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from housemind.core.model import Entity, HouseModel, State
from housemind.energy.prices import parse_series
from housemind.i18n.catalog import Languages
from housemind.knowledge.words import has_word as words_have

WORD_KEYS = (
    "home_battery",
    "car",
    "charger",
    "export_limit",
    "heat_pump",
    "remote_start",
    "appliance",
    "start",
    "not_start",
    "plug",
    "grid",
    "solar",
    "battery",
    "consumption",
    "legionella",
)


def energy_words(languages: Languages) -> dict[str, list[str]]:
    """All words for every energy role, from every language file."""
    words: dict[str, list[str]] = {key: [] for key in WORD_KEYS}
    for texts in languages.languages.values():
        for key in WORD_KEYS:
            raw = texts.get(f"energy_words.{key}")
            if raw:
                words[key].extend(w.strip().lower() for w in raw.split(",") if w.strip())
    return words


def _has_word(text: str, words: Iterable[str]) -> bool:
    """A word at the start of a word ("laadpaal" in "laadpaalstroom"); a short word only as a
    whole word, so "car" never matches "carbon monoxide" nor "zon" "zonwering"."""
    return words_have(text, words, whole_up_to=4)  # one rule (code QA 3, punt 4)


# One way to read a live power: one sensor, or the phases of one meter added up.
Reading = tuple[str, ...]


@dataclass(slots=True)
class Appliance:
    """A device people prepare and HouseMind may start at the best moment (E-64)."""

    name: str
    start: str  # button or switch that starts it
    ready: str  # binary_sensor: the device allows a remote start
    power: str | None = None


@dataclass(slots=True)
class EnergySetup:
    grid_import: list[str] = field(default_factory=list)  # energy meters (kWh)
    grid_export: list[str] = field(default_factory=list)
    price: str | None = None  # price entity per kWh (from the energy settings)
    fixed_price: float | None = None
    export_price: str | None = None
    solar_energy: list[str] = field(default_factory=list)
    solar_forecast_entries: list[str] = field(default_factory=list)
    battery_in: list[str] = field(default_factory=list)
    battery_out: list[str] = field(default_factory=list)
    gas: list[str] = field(default_factory=list)
    water: list[str] = field(default_factory=list)
    device_meters: list[str] = field(default_factory=list)
    grid_power: str | None = None  # W, positive = taking from the grid
    solar_power: str | None = None
    battery_soc: str | None = None
    battery_power: str | None = None  # W, positive = charging
    charger_power: str | None = None  # W, what the car charges with
    car_soc: str | None = None
    car_plugged: str | None = None
    export_limit: str | None = None  # number
    boiler: str | None = None  # water_heater
    # A switch, choice or setting on the boiler's own device for its legionella programme:
    # then the boiler does it itself (E-377).
    boiler_legionella: str | None = None
    heat_pump: str | None = None  # climate
    appliances: list[Appliance] = field(default_factory=list)
    users: list[str] = field(default_factory=list)  # power sensors of devices that use energy
    managed: list[str] = field(default_factory=list)  # "charger" / "battery": another system steers
    # Every home battery, each on its own (E-364); the single fields above are the first one.
    batteries: list[Any] = field(default_factory=list)
    manager_entities: set[str] = field(default_factory=set)
    # Every way to read the grid, the sun and the home battery live, best first: the first
    # that has a value when it is read counts. The single fields above are the first of each.
    grid_readings: list[Reading] = field(default_factory=list)
    solar_readings: list[Reading] = field(default_factory=list)
    battery_readings: list[Reading] = field(default_factory=list)

    def readings(self, part: str) -> list[Reading]:
        """The ways to read "grid", "solar" or "battery"; a setup made by hand with only the
        single field reads that one."""
        found, single = {
            "grid": (self.grid_readings, self.grid_power),
            "solar": (self.solar_readings, self.solar_power),
            "battery": (self.battery_readings, self.battery_power),
        }[part]
        return found or ([(single,)] if single else [])

    @property
    def has_any(self) -> bool:
        return bool(self.grid_import or self.grid_power or self.solar_energy or self.price)

    def controllable(self) -> list[str]:
        """What the energy plan may steer (each still through the safety gate)."""
        found = [
            e
            for e in (
                self.export_limit,
                self.boiler,
                self.heat_pump,
            )
            if e
        ]
        return found + [a.start for a in self.appliances]

    def summary(self) -> dict[str, Any]:
        return {
            "grid": bool(self.grid_import or self.grid_power),
            "solar": bool(self.solar_energy or self.solar_power),
            "battery": bool(self.battery_soc or self.battery_in),
            "car": bool(self.car_plugged or self.charger_power or self.car_soc),
            "boiler": bool(self.boiler),
            "heatPump": bool(self.heat_pump),
            "prices": bool(self.price or self.fixed_price),
            "appliances": len(self.appliances),
            "managedCharger": "charger" in self.managed,
            "managedBattery": "battery" in self.managed,
        }


def _flows(source: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = source.get(key)
    return [f for f in value if isinstance(f, dict)] if isinstance(value, list) else []


def _siblings(model: HouseModel, entity_ids: Iterable[str]) -> list[Entity]:
    devices = {
        model.entities[e].device_id
        for e in entity_ids
        if e in model.entities and model.entities[e].device_id
    }
    return [e for e in model.entities.values() if e.device_id in devices]


def _is_car(model: HouseModel, entity: Entity) -> bool:
    """A car's battery is never on a smoke, CO, gas or leak detector: such a detector never
    becomes "the car", so its low battery is always reported. Short words match whole words
    only; this is the second safeguard."""
    if entity.device_id is None:
        return True  # a sensor made in HA (a car's app as a template): its name decides
    return not any(
        e.domain == "binary_sensor" and e.device_class in SAFETY
        for e in model.on_device(entity.device_id)
    )


SAFETY = frozenset({"smoke", "carbon_monoxide", "gas", "moisture"})


def _label(model: HouseModel, entity: Entity) -> str:
    return model.words_of(entity)


def _power(entities: Iterable[Entity]) -> str | None:
    for entity in entities:
        if entity.domain == "sensor" and entity.device_class == "power":
            return entity.entity_id
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _own(model: HouseModel, entity: Entity) -> str:
    """What a sensor says it is itself, without its device's name: "solaredge_grid_power"
    on the device "solaredge" says "grid_power", so a brand in a device name decides nothing."""
    object_id = entity.entity_id.split(".", 1)[1]
    name = entity.name
    device = model.devices.get(entity.device_id or "")
    if device is not None and device.name:
        slug = _slug(device.name)
        if slug and object_id.startswith(f"{slug}_"):
            object_id = object_id[len(slug) + 1 :]
        if name.lower().startswith(f"{device.name.lower()} "):
            name = name[len(device.name) + 1 :]
    return f"{object_id} {name}"


# The words that say what a power sensor measures, per part.
_SAYS = {
    "grid": ("grid",),
    "solar": ("solar",),
    "battery": ("home_battery", "battery"),
    "car": ("charger", "car"),
    "consumption": ("consumption",),
    "limit": ("export_limit",),
}
# What a power sensor of each part may never also say it is.
_NOT = {
    "grid": ("solar", "battery", "car", "limit"),
    "solar": ("grid", "battery", "car", "consumption"),
    "battery": ("grid", "solar", "car", "consumption"),
}


def _says(text: str, words: dict[str, list[str]], part: str) -> bool:
    return any(_has_word(text, words[key]) for key in _SAYS[part])


def _readings(
    model: HouseModel,
    entities: Iterable[Entity],
    words: dict[str, list[str]],
    part: str,
    other: tuple[str, ...],
    said_only: bool = False,
) -> list[Reading]:
    """Every way to read one live power from these sensors, best first. A sensor whose own
    name says it is this part goes first, then one whose device's name says so, then one that
    says nothing; one total goes before the phases of a meter added up. A sensor whose own
    name says it is something else never counts: the grid's power on the solar panels' device
    is not the sun. Which one has a value is asked each time it is read, so one that is
    "unknown" or offline gives way to the next."""
    ranked: list[tuple[int, int, Reading]] = []
    phases: dict[tuple[int, str | None, str], list[str]] = {}
    for entity in entities:
        if entity.domain != "sensor" or entity.device_class != "power" or entity.disabled:
            continue
        own = _own(model, entity)
        if any(_says(own, words, o) for o in other):
            continue
        device = model.devices.get(entity.device_id or "")
        rank = (
            0
            if _says(own, words, part)
            else 1
            if device is not None and _says(device.name, words, part)
            else 2
        )
        if said_only and rank == 2:
            continue
        own = own.lower().replace("_", " ")
        if _PHASE.search(own):
            phases.setdefault((rank, entity.device_id, _PHASE.sub("", own)), []).append(
                entity.entity_id
            )
        else:
            ranked.append((0, rank, (entity.entity_id,)))
    ranked += [(1, key[0], tuple(ids)) for key, ids in phases.items()]
    return [reading for _, _, reading in sorted(ranked, key=lambda r: r[:2])]


def _first(readings: list[Reading]) -> str | None:
    return readings[0][0] if readings else None


def battery_powers(
    model: HouseModel, entities: Iterable[Entity], words: dict[str, list[str]]
) -> list[str]:
    """The power sensors of a battery's own device: the ones that say "battery" (by their own
    name or the device's), else any that says nothing – never the sun's power of an inverter
    the battery is part of."""
    said = _readings(model, entities, words, "battery", _NOT["battery"], said_only=True)
    found = said or _readings(model, entities, words, "battery", _NOT["battery"])
    return [r[0] for r in found if len(r) == 1]


_CURRENCY = (
    "€",
    "eur",
    "ct",
    "cent",
    "$",
    "usd",
    "£",
    "gbp",
    "kr",
    "sek",
    "nok",
    "dkk",
    "chf",
    "pln",
    "zł",
)


def find_price_series(states: dict[str, State]) -> str | None:
    """A sensor with a price per kWh (or MWh) and today's and tomorrow's prices in its
    attributes – whatever integration made it, also when it is not in the energy settings.
    The one that looks furthest ahead wins; everything else that has a price (costs today, a
    session price, a tariff without a series) is ignored."""
    best: tuple[datetime, int, str] | None = None
    for entity_id, state in states.items():
        if not entity_id.startswith("sensor."):
            continue
        unit = str(state.attributes.get("unit_of_measurement") or "").lower()
        if not ("kwh" in unit or "mwh" in unit) or not any(c in unit for c in _CURRENCY):
            continue
        slots = parse_series(state)
        if len(slots) < 12:
            continue  # not a series of hours or quarters
        key = (slots[-1].end, len(slots), entity_id)
        if best is None or key[:2] > best[:2]:
            best = key
    return best[2] if best else None


def load_managers(directories: Iterable[Path]) -> set[str]:
    """Integration names of energy managers from the knowledge layers (E-68)."""
    found: set[str] = set()
    for directory in directories:
        path = directory / "managers.yaml"
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            found |= {str(p).lower() for p in raw.get("platforms") or []}
    return found


def detect(
    model: HouseModel,
    states: dict[str, State],
    prefs: dict[str, Any],
    words: dict[str, list[str]],
    managers: set[str] | None = None,
) -> EnergySetup:
    setup = EnergySetup()
    # Another system that steers energy devices itself (E-68): its own controls are never
    # used, and what it steers is only watched.
    managed = {
        e.entity_id
        for e in model.entities.values()
        if (e.platform or "").lower() in (managers or set())
        or (
            model.devices.get(e.device_id or "")
            and (model.devices[e.device_id or ""].integration or "").lower() in (managers or set())
        )
    }
    setup.manager_entities = managed
    managed_devices = {
        model.entities[e].device_id or "" for e in managed if model.entities[e].device_id
    }
    labels = [_label(model, model.entities[e]) for e in managed]
    if any(_has_word(label, words["charger"]) for label in labels):
        setup.managed.append("charger")
    if any(_has_word(label, words["home_battery"]) for label in labels):
        setup.managed.append("battery")
    model = HouseModel(
        floors=model.floors,
        areas=model.areas,
        devices=model.devices,
        entities={k: v for k, v in model.entities.items() if k not in managed},
    )
    for source in prefs.get("energy_sources") or []:
        if not isinstance(source, dict):
            continue
        kind = source.get("type")
        if kind == "grid":
            for flow in _flows(source, "flow_from"):
                if flow.get("stat_energy_from"):
                    setup.grid_import.append(str(flow["stat_energy_from"]))
                if flow.get("entity_energy_price") and not setup.price:
                    setup.price = str(flow["entity_energy_price"])
                if isinstance(flow.get("number_energy_price"), int | float):
                    setup.fixed_price = float(flow["number_energy_price"])
            for flow in _flows(source, "flow_to"):
                if flow.get("stat_energy_to"):
                    setup.grid_export.append(str(flow["stat_energy_to"]))
                if flow.get("entity_energy_price"):
                    setup.export_price = str(flow["entity_energy_price"])
            if source.get("stat_rate"):
                setup.grid_power = str(source["stat_rate"])
        elif kind == "solar":
            if source.get("stat_energy_from"):
                setup.solar_energy.append(str(source["stat_energy_from"]))
            setup.solar_forecast_entries += [
                str(e) for e in source.get("config_entry_solar_forecast") or []
            ]
            if source.get("stat_rate"):
                setup.solar_power = str(source["stat_rate"])
        elif kind == "battery":
            if source.get("stat_energy_to"):
                setup.battery_in.append(str(source["stat_energy_to"]))
            if source.get("stat_energy_from"):
                setup.battery_out.append(str(source["stat_energy_from"]))
            if source.get("stat_rate"):
                setup.battery_power = str(source["stat_rate"])
        elif kind == "gas" and source.get("stat_energy_from"):
            setup.gas.append(str(source["stat_energy_from"]))
        elif kind == "water" and source.get("stat_energy_from"):
            setup.water.append(str(source["stat_energy_from"]))
    for device in prefs.get("device_consumption") or []:
        if isinstance(device, dict) and device.get("stat_consumption"):
            setup.device_meters.append(str(device["stat_consumption"]))

    # No price in HA's energy settings: look for a price series that is already in HA (E-72).
    if not setup.price and setup.fixed_price is None:
        setup.price = find_price_series(states)

    # Live power: what the energy settings name and the sensors on the meters' devices; only
    # without those, any sensor whose own name says it is the grid or the sun (never a
    # forecast's device that happens to say "solar" next to a real meter).
    sensors = list(model.entities.values())

    def live(
        part: str, first: str | None, meters: list[str], other: tuple[str, ...]
    ) -> list[Reading]:
        readings: list[Reading] = [(first,)] if first else []
        readings += _readings(model, _siblings(model, meters), words, part, other)
        if not readings:
            readings = _readings(model, sensors, words, part, other, said_only=True)
        return list(dict.fromkeys(readings))

    setup.grid_readings = live("grid", setup.grid_power, setup.grid_import, _NOT["grid"])
    setup.solar_readings = live("solar", setup.solar_power, setup.solar_energy, _NOT["solar"])
    setup.grid_power = _first(setup.grid_readings)
    setup.solar_power = _first(setup.solar_readings)

    # The home battery: its device from the energy settings, else by words.
    battery = _siblings(model, setup.battery_in + setup.battery_out)
    if not battery:
        battery = [
            e
            for e in model.entities.values()
            if _has_word(_label(model, e), words["home_battery"])
            and not _has_word(_label(model, e), words["car"])
        ]
    for entity in battery:
        if entity.domain == "sensor" and entity.device_class == "battery" and not setup.battery_soc:
            setup.battery_soc = entity.entity_id
    setup.battery_readings = [(setup.battery_power,)] if setup.battery_power else []
    setup.battery_readings += [(e,) for e in battery_powers(model, battery, words)]
    setup.battery_readings = list(dict.fromkeys(setup.battery_readings))
    setup.battery_power = _first(setup.battery_readings)

    # Charger and car, only read (E-363). Never a setting or a hidden entity, such as the
    # cable lock or the LED.
    for entity in model.entities.values():
        if entity.entity_category or entity.hidden or entity.disabled:
            continue
        label = _label(model, entity)
        if _has_word(label, words["charger"]):
            if entity.domain == "sensor" and entity.device_class == "power":
                setup.charger_power = setup.charger_power or entity.entity_id
            elif entity.domain == "binary_sensor" and (
                entity.device_class == "plug" or _has_word(label, words["plug"])
            ):
                setup.car_plugged = setup.car_plugged or entity.entity_id
        if (
            entity.domain == "sensor"
            and entity.device_class == "battery"
            and _has_word(label, words["car"])
            and _is_car(model, entity)
        ):
            setup.car_soc = setup.car_soc or entity.entity_id
        if entity.domain == "number" and _has_word(label, words["export_limit"]):
            setup.export_limit = setup.export_limit or entity.entity_id
        if entity.domain == "water_heater" and not setup.boiler:
            setup.boiler = entity.entity_id
        if (
            entity.domain == "climate"
            and _has_word(label, words["heat_pump"])
            and not setup.heat_pump
        ):
            setup.heat_pump = entity.entity_id

    # Appliances that may be started remotely (E-64): only a washing machine, dryer or
    # dishwasher – never a cooking appliance – and only with a control that really starts it
    # (never pause, stop or resume). One per device.
    seen: set[str] = set()
    for ready in model.entities.values():
        if (
            ready.domain != "binary_sensor"
            or not ready.device_id
            or ready.device_id in seen
            or not _has_word(_label(model, ready), words["remote_start"])
            or not _has_word(_label(model, ready), words["appliance"])
        ):
            continue
        siblings = [e for e in _siblings(model, [ready.entity_id]) if e is not ready]
        start = next((e for e in siblings if _starts(e, words)), None)
        if start is not None:
            seen.add(ready.device_id)
            device = model.devices.get(ready.device_id)
            setup.appliances.append(
                Appliance(
                    name=device.name if device else start.name,
                    start=start.entity_id,
                    ready=ready.entity_id,
                    power=_power(siblings),
                )
            )

    if setup.boiler and setup.boiler in model.entities:
        setup.boiler_legionella = next(
            (
                e.entity_id
                for e in _siblings(model, [setup.boiler])
                if e.domain in ("switch", "select", "number", "binary_sensor", "sensor")
                and _has_word(_label(model, e), words["legionella"])
            ),
            None,
        )

    setup.users = _users(model, setup, managed_devices, words["appliance"])
    return setup


def _starts(entity: Entity, words: dict[str, list[str]]) -> bool:
    """A button or switch whose own name says it starts the program."""
    if entity.domain not in ("button", "switch"):
        return False
    own = f"{entity.entity_id.split('.', 1)[1]} {entity.name}"
    return _has_word(own, words["start"]) and not _has_word(
        own, words["not_start"] + words["remote_start"]
    )


# Domains of devices that use energy themselves: a plug, a lamp, a heater, a fan.
CONSUMER_DOMAINS = frozenset(
    {"switch", "light", "fan", "climate", "water_heater", "humidifier", "vacuum", "media_player"}
)
_PHASE = re.compile(r"(?<![a-z0-9])l[1-3](?![0-9])")


def _users(
    model: HouseModel, setup: EnergySetup, managed_devices: set[str], appliance: list[str]
) -> list[str]:
    """One power sensor per device that uses energy – not the meters of the energy system."""
    system = managed_devices | {
        model.entities[e].device_id or ""
        for e in (
            setup.grid_power,
            setup.solar_power,
            setup.battery_power,
            setup.battery_soc,
            setup.export_limit,
        )
        if e and e in model.entities
    }
    per_device: dict[str, list[Entity]] = {}
    for entity in model.entities.values():
        if entity.device_id and entity.device_id not in system:
            per_device.setdefault(entity.device_id, []).append(entity)
    found: list[str] = []
    for device_id, entities in per_device.items():
        device = model.devices.get(device_id)
        if not any(e.domain in CONSUMER_DOMAINS for e in entities) and not (
            device and _has_word(device.name, appliance)
        ):
            continue
        power = [
            e
            for e in entities
            if e.domain == "sensor"
            and e.device_class == "power"
            and not _PHASE.search(f"{e.entity_id} {e.name}".lower().replace("_", " "))
        ]
        if power:
            found.append(power[0].entity_id)
    return found
