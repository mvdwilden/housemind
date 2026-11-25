"""The demo houses (E-48, B-41): invented houses in the app's language, never the real one."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from importlib import resources
from typing import Any

import yaml

from housemind.core.clock import Clock
from housemind.core.model import Area, Device, Entity, Floor, HouseModel, State
from housemind.ha.adapter import HAConfig, HAConfigEntry, HAUser
from housemind.ha.fake import FakeHomeAssistant
from housemind.ha.translate import parse_light_rhythm

# The three houses the App options offer (the administrator's wish, 20-10-2026): simple (twelve
# devices), average (almost forty) and extended (every kind of device HouseMind knows). Every room
# has a movement or presence sensor, so someone can be played there (E-463).
SHOWN_HOUSES = ("simple", "average", "extended")
# The houses the tests are built on; they stay as they are.
TEST_HOUSES = ("apartment", "family_home", "energy_home")
DEMO_HOUSES = TEST_HOUSES + SHOWN_HOUSES


@dataclass(frozen=True, slots=True)
class DemoHouse:
    key: str
    name: str
    ha: FakeHomeAssistant
    residents: int
    pets: bool
    pet_areas: tuple[str, ...]


def _read(name: str) -> Any:
    package = resources.files("housemind.demo") / "houses" / f"{name}.yaml"
    return yaml.safe_load(package.read_text(encoding="utf-8"))


def _labels(language: str) -> dict[str, str]:
    labels: dict[str, dict[str, str]] = _read("labels")
    short = language.split("-", 1)[0].lower()
    return {**labels["en"], **labels.get(short, {})}


def _title(text: str) -> str:
    return text[:1].upper() + text[1:]


def load_demo_house(key: str, clock: Clock, language: str = "en") -> DemoHouse:
    if key not in DEMO_HOUSES:
        raise ValueError(f"unknown demo house {key!r}")
    raw = _read(key)
    words = _labels(language)
    now = clock.now()
    model = HouseModel()
    for f in raw["floors"]:
        model.floors[f["id"]] = Floor(f["id"], words[f["id"]], f.get("level"))
    for a in raw["areas"]:
        model.areas[a["id"]] = Area(a["id"], words[a["id"]], a.get("floor"))
    states: dict[str, State] = {}
    for e in raw["entities"]:
        entity_id: str = e["id"]
        area = e.get("area")  # a phone has no room
        name = f"{words[area]} {words[e['label']]}" if area else words[e["label"]]
        # Entities of one physical device share it (meter, battery, charger …), like in HA.
        device_id = f"device.{e['device']}" if "device" in e else entity_id
        if device_id not in model.devices:
            device_name = words[e["device"]] if "device" in e else name
            model.devices[device_id] = Device(device_id, device_name, area, "demo")
        attrs: dict[str, Any] = {"friendly_name": name, **(e.get("attrs") or {})}
        capabilities: dict[str, Any] = {}
        if "modes" in e:
            attrs["supported_color_modes"] = list(e["modes"])
            capabilities["supported_color_modes"] = list(e["modes"])
        if "class" in e:
            attrs["device_class"] = e["class"]
        if "unit" in e:
            attrs["unit_of_measurement"] = e["unit"]
        if e.get("restored"):
            attrs["restored"] = True  # a place its integration no longer fills (E-352)
        model.entities[entity_id] = Entity(
            entity_id=entity_id,
            registry_id=f"demo-{entity_id}",
            name=name,
            device_id=device_id,
            area_id=area,
            device_class=e.get("class"),
            unit=e.get("unit"),
            platform=e.get("platform", "demo"),  # "mqtt" for a Zigbee2MQTT group (E-312)
            capabilities=capabilities,
            unique_id=e.get("unique_id"),
            config_entry_id=e.get("integration"),
            original_name=name,  # what the integration calls it: no person named it (E-429)
        )
        # Since when Home Assistant says it is like this (Home Assistant netjes, E-352).
        changed = now - timedelta(hours=float(e["away_hours"])) if "away_hours" in e else now
        states[entity_id] = State(entity_id, str(e["state"]), attrs, changed)
    users: list[HAUser] = []
    for i, p in enumerate(raw.get("persons", []), start=1):
        name = f"{words['resident']} {i}"
        user_id = f"demo-user-{i}"
        users.append(HAUser(user_id, name, is_admin=i == 1))
        model.entities[p["id"]] = Entity(p["id"], f"demo-{p['id']}", name, platform="demo")
        attrs = {
            "friendly_name": name,
            "user_id": user_id,
            "device_trackers": [f"device_tracker.phone_{i}"],
        }
        states[p["id"]] = State(p["id"], p["state"], attrs, now)
    automations: dict[str, dict[str, Any]] = {}
    for a in raw.get("automations", []):
        # Made by someone in Home Assistant earlier – HouseMind never makes one (E-03).
        name = words[a["label"]]
        model.entities[a["id"]] = Entity(a["id"], f"demo-{a['id']}", name, platform="automation")
        attrs = {"friendly_name": name, "id": a["config"]["id"]}
        if "ran_days" in a:  # when it last ran (B-240)
            attrs["last_triggered"] = (now - timedelta(days=float(a["ran_days"]))).isoformat()
        states[a["id"]] = State(a["id"], a.get("state", "on"), attrs, now)
        automations[a["id"]] = dict(a["config"])
    config = HAConfig(
        version="2026.10.0",
        language=language,
        country=raw.get("country"),
        time_zone="Europe/Amsterdam",
    )
    ha = FakeHomeAssistant(clock, model, states, config, energy=raw.get("energy"))
    ha.users_list = users
    ha.automation_configs = automations
    ha.energy_validation = dict(raw.get("energy_validation") or {})  # E-371
    ha.health = dict(raw.get("system_health") or {})  # B-241
    if "backups" in raw:  # a house that says nothing about backups: unknown, no message
        ha.backup_list = [
            {
                "backup_id": f"demo{n}",
                "name": words.get(b["label"], b["label"]),
                "date": (now - timedelta(days=float(b["days"]))).isoformat(),
                "homeassistant_included": bool(b.get("homeassistant_included")),
                "with_automatic_settings": bool(b.get("automatic")),
                "agents": {"hassio.local": {"size": int(float(b.get("gb") or 1) * 2**30)}},
            }
            for n, b in enumerate(raw["backups"])
        ]
    if "backup_settings" in raw:  # what Home Assistant keeps (E-397)
        ha.backup_settings = dict(raw["backup_settings"])
    ha.light_rhythm_list = [
        parse_light_rhythm(r["id"], words[r["title"]], dict(r["options"]))
        for r in raw.get("light_rhythms", [])
    ]
    ha.config_entries_list = [
        HAConfigEntry(i["id"], i["domain"], _title(words.get(i["title"], i["title"])), i["state"])
        for i in raw.get("integrations", [])
    ]
    ha.notify_services = {f"mobile_app_phone_{i}" for i in range(1, len(users) + 1)}
    return DemoHouse(
        key=key,
        name=words[raw["name"]],
        ha=ha,
        residents=int(raw.get("residents", 1)),
        pets=bool(raw.get("pets", False)),
        pet_areas=tuple(raw.get("pet_areas", ())),
    )
