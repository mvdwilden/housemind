"""The icon of a device or a room (E-508, E-509, design 79): one key from HouseMind's own set,
chosen in one place, from what HouseMind already knows – nothing is guessed and nobody is asked.

In this order (B-435): the icon a person chose in Home Assistant, when the set has a drawing for
it (``ha``); what the device does – its function (E-427), and behind a plug or a switch the
appliance its name speaks of, in every loaded language, without brands (``function``); what
Home Assistant says of its kind, the device class (``class``); its domain (``domain``). What is
left gets the neutral ``device`` (``none``). A room gets the icon of its role (E-105).

The screen adds what the icon shows of the state: a lamp's brightness, a cover that is closed,
a lock that is open (``with_state``).
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from typing import Any

from housemind.core.model import Area, Entity
from housemind.i18n.catalog import Languages

# The set (79.2): the same keys as frontend/src/lib/deviceicons.ts.
ICONS = frozenset(
    {
        "lamp", "plug", "switch", "button", "battery", "solar", "grid", "energy", "charger",
        "car", "oven", "fridge", "washer", "dishwasher", "vacuum", "heat_pump", "radiator",
        "thermostat", "thermometer", "boiler", "water", "fan", "air", "alarm", "siren", "smoke",
        "water_leak", "doorbell", "camera", "lock", "unlock", "person", "pet", "motion",
        "presence", "phone", "door", "door_open", "window", "curtains", "curtains_closed",
        "shutter", "garage_door", "tv", "speaker", "radio", "sun", "moon", "sunrise", "away",
        "alarm_clock", "home", "bed", "child", "bath", "toilet", "kitchen", "dining", "sofa",
        "hall", "office", "utility", "garage", "outside", "room", "sensor", "device",
    }
)  # fmt: skip

# Step 1: the icons of Home Assistant (mdi) a person may choose, on the set. A name not listed
# loses its last part and is tried again ("smoke-detector-variant" → "smoke-detector").
HA_ICONS = {
    "lightbulb": "lamp", "lamp": "lamp", "ceiling-light": "lamp", "floor-lamp": "lamp",
    "desk-lamp": "lamp", "led-strip": "lamp", "wall-sconce": "lamp", "string-lights": "lamp",
    "power-plug": "plug", "power-socket": "plug", "power-socket-eu": "plug",
    "toggle-switch": "switch", "light-switch": "button", "gesture-tap-button": "button",
    "battery": "battery", "home-battery": "battery", "solar-panel": "solar",
    "solar-power": "solar", "transmission-tower": "grid", "flash": "energy",
    "lightning-bolt": "energy", "ev-station": "charger", "ev-plug": "charger", "car": "car",
    "stove": "oven", "toaster-oven": "oven", "microwave": "oven", "fridge": "fridge",
    "washing-machine": "washer", "tumble-dryer": "washer", "dishwasher": "dishwasher",
    "robot-vacuum": "vacuum", "vacuum": "vacuum", "heat-pump": "heat_pump",
    "radiator": "radiator", "thermostat": "thermostat", "thermometer": "thermometer",
    "water-boiler": "boiler", "water": "water", "water-percent": "water", "fan": "fan",
    "air-filter": "air", "air-purifier": "air", "shield-home": "alarm", "shield-lock": "alarm",
    "alarm-light": "siren", "smoke-detector": "smoke", "fire": "smoke", "water-alert": "water_leak",
    "pipe-leak": "water_leak", "doorbell": "doorbell", "doorbell-video": "doorbell",
    "cctv": "camera", "camera": "camera", "webcam": "camera", "lock": "lock",
    "lock-open": "unlock", "account": "person", "paw": "pet", "dog": "pet", "cat": "pet",
    "motion-sensor": "motion", "walk": "motion", "run": "motion", "home-account": "presence",
    "cellphone": "phone", "door": "door", "door-open": "door_open", "window-closed": "window",
    "window-open": "window", "curtains": "curtains", "curtains-closed": "curtains_closed",
    "blinds": "shutter", "window-shutter": "shutter", "roller-shade": "shutter",
    "garage": "garage_door", "garage-open": "garage_door", "television": "tv", "speaker": "speaker",
    "radio": "radio", "weather-sunny": "sun", "sofa": "sofa", "bed": "bed", "shower": "bath",
    "bathtub": "bath", "toilet": "toilet", "silverware-fork-knife": "dining", "chef-hat": "kitchen",
    "pot-steam": "kitchen", "desk": "office", "laptop": "office", "tree": "outside",
    "flower": "outside", "home": "home", "coat-rack": "hall", "hanger": "hall",
}  # fmt: skip

# Step 2: what the device does (E-427), as an icon; the class makes it finer where it can.
FUNCTION_ICONS = {
    "lamp": "lamp", "cover": "curtains", "room_climate": "thermostat", "fan": "fan",
    "player": "speaker", "lock": "lock", "air_temperature": "thermometer",
    "outside_temperature": "thermometer", "humidity": "water", "co2": "air",
    "air_quality": "air", "light_level": "sun", "outside_light": "sun", "weather": "sun",
    "presence": "presence", "motion": "motion", "opening": "door", "safety": "smoke",
    "battery": "battery", "water_temperature": "boiler", "appliance_power": "plug",
}  # fmt: skip
FINER = {
    "cover": {"blind": "shutter", "shade": "shutter", "shutter": "shutter", "awning": "shutter",
              "damper": "air",
              "garage": "garage_door", "gate": "garage_door", "window": "window", "door": "door"},
    "opening": {"window": "window", "garage_door": "garage_door"},
    "safety": {"moisture": "water_leak", "tamper": "alarm", "problem": "alarm"},
    "player": {"tv": "tv", "receiver": "radio"},
}  # fmt: skip
# Where the name may say which appliance it is: behind a plug or a switch, a device's own
# measurements, a heat pump or a boiler, and people's trackers (a pet).
BY_NAME = frozenset(
    {
        "appliance_power", "appliance_start", "device_control", "device_temperature",
        "device_power", "device_energy", "device_state", "water_temperature", "other_control",
        "room_climate", "fan", "player", "setting", "fault", "signal", "update", "connectivity",
        "status_light",
    }
)  # fmt: skip
NAMED_DOMAINS = frozenset(
    {
        "switch", "sensor", "binary_sensor", "climate", "water_heater", "person", "device_tracker",
        "select", "number", "button", "event",
    }
)  # fmt: skip
# The appliances the words name, in this order: a dishwasher before a washer ("dishwasher").
KINDS = (
    "dishwasher", "washer", "fridge", "oven", "heat_pump", "boiler", "charger", "car", "vacuum",
    "tv", "radio", "solar", "battery", "grid", "air", "fan", "radiator", "pet",
)  # fmt: skip

# Step 3: the device class.
CLASS_ICONS = {
    "motion": "motion", "occupancy": "presence", "presence": "presence", "door": "door",
    "garage_door": "garage_door", "window": "window", "opening": "door", "smoke": "smoke",
    "gas": "smoke", "carbon_monoxide": "smoke", "moisture": "water_leak", "battery": "battery",
    "temperature": "thermometer", "humidity": "water", "power": "energy", "energy": "energy",
    "carbon_dioxide": "air", "pm25": "air", "illuminance": "sun", "outlet": "plug",
    "curtain": "curtains", "blind": "shutter", "shutter": "shutter", "shade": "shutter",
    "awning": "shutter", "garage": "garage_door", "gate": "garage_door", "tv": "tv",
    "speaker": "speaker", "receiver": "radio", "doorbell": "doorbell", "lock": "lock",
}  # fmt: skip

# A measured quantity says something else than an alarm of the same name: a gas meter.
SENSOR_CLASS_ICONS = {"gas": "energy", "water": "water", "volume": "water"}
# Some domains are what they are, whatever their function says.
OWN_DOMAINS = {"siren": "siren", "alarm_control_panel": "alarm", "camera": "camera"}

# Step 4: the domain.
DOMAIN_ICONS = {
    "light": "lamp", "switch": "switch", "climate": "thermostat", "water_heater": "boiler",
    "cover": "curtains", "fan": "fan", "media_player": "speaker", "vacuum": "vacuum",
    "camera": "camera", "lock": "lock", "alarm_control_panel": "alarm", "siren": "siren",
    "person": "person", "device_tracker": "phone", "button": "button", "humidifier": "water",
    "valve": "water", "remote": "button", "event": "button", "binary_sensor": "sensor",
    "sensor": "sensor",
}  # fmt: skip

# A room's role (E-105) as an icon.
ROLE_ICONS = {
    "child_bedroom": "child", "guest_room": "bed", "bedroom": "bed", "bathroom": "bath",
    "toilet": "toilet", "kitchen": "kitchen", "dining": "dining", "living": "sofa",
    "hall": "hall", "office": "office", "utility": "utility", "garage": "garage",
    "outside": "outside",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Choice:
    """An icon and where it came from: ``ha``, ``function``, ``class``, ``domain`` or ``none``."""

    icon: str
    source: str


def from_ha(icon: str | None) -> str | None:
    """A Home Assistant icon ("mdi:fridge-outline") on the set, or ``None``."""
    if not icon or ":" not in icon:
        return None
    name = icon.split(":", 1)[1].lower()
    while name:
        if name in HA_ICONS:
            return HA_ICONS[name]
        if "-" not in name:
            return None
        name = name.rsplit("-", 1)[0]
    return None


def _pattern(word: str) -> str:
    """A short word only as a whole word ("tv", "auto" – never "automation"); a longer one
    also at the start or the end of a compound ("Hoofdkoelkast", "Koelkastje")."""
    escaped = re.escape(word)
    if len(word) < 6:
        return rf"\b{escaped}\b"
    return rf"\b{escaped}|{escaped}\b"


class Icons:
    """Chooses icons; the words per kind come from the language files (``device_kinds.*``),
    compiled once. Choices are kept per entity until what they depend on changes."""

    def __init__(self, languages: Languages) -> None:
        self.words: dict[str, re.Pattern[str]] = {}
        for kind in KINDS:
            found: list[str] = []
            for texts in languages.languages.values():
                raw = texts.get(f"device_kinds.{kind}")
                if isinstance(raw, str):
                    found += [w.strip().lower() for w in raw.split(",") if w.strip()]
            if found:
                self.words[kind] = re.compile("|".join(_pattern(w) for w in sorted(set(found))))
        self._kept: dict[tuple[Any, ...], Choice] = {}

    def by_name(self, *names: str | None) -> str | None:
        """The appliance a name speaks of, or ``None``."""
        text = " ".join(n.replace("_", " ") for n in names if n).lower()
        if not text:
            return None
        for kind in KINDS:
            pattern = self.words.get(kind)
            if pattern is not None and pattern.search(text):
                return kind
        return None

    def of(self, entity: Entity, function: str | None, device_name: str | None = None) -> Choice:
        """The icon of one entity (E-509)."""
        key = (
            entity.entity_id,
            entity.name,
            entity.icon,
            entity.device_class,
            function,
            device_name,
        )
        kept = self._kept.get(key)
        if kept is None:
            kept = self._choose(entity, function, device_name)
            if len(self._kept) > 20000:
                self._kept.clear()
            self._kept[key] = kept
        return kept

    def _choose(self, entity: Entity, function: str | None, device_name: str | None) -> Choice:
        chosen = from_ha(entity.icon)
        if chosen is not None:
            return Choice(chosen, "ha")
        domain, kind = entity.domain, entity.device_class
        named = None
        if (function in BY_NAME) or (function in (None, "unknown") and domain in NAMED_DOMAINS):
            named = self.by_name(entity.name, device_name, entity.entity_id.split(".", 1)[1])
            if named == "pet" and domain not in ("person", "device_tracker"):
                named = None  # a pet's feeder is no pet
        if named is not None:
            return Choice(named, "function")
        if domain in OWN_DOMAINS:
            return Choice(OWN_DOMAINS[domain], "domain")
        if function in FUNCTION_ICONS:
            finer = FINER.get(function, {}).get(kind or "")
            return Choice(finer or FUNCTION_ICONS[function], "function")
        if domain in ("sensor", "valve") and kind in SENSOR_CLASS_ICONS:
            return Choice(SENSOR_CLASS_ICONS[kind], "class")
        if kind in CLASS_ICONS:
            return Choice(CLASS_ICONS[kind], "class")
        if domain in DOMAIN_ICONS:
            return Choice(DOMAIN_ICONS[domain], "domain")
        return Choice("device", "none")


def area_icon(area: Area, role: str | None) -> str:
    """A room's icon: the one a person chose in Home Assistant, else its role's, else
    'another room'."""
    return from_ha(area.icon) or ROLE_ICONS.get(role or "", "room")


def with_state(icon: str, entity: Entity, state: Any) -> dict[str, Any]:
    """What the icon shows of the state now: a lamp's brightness in percent, a closed
    curtain, a shutter's part closed, an open lock or door."""
    out: dict[str, Any] = {"icon": icon}
    if state is None:
        return out
    value, attrs = state.state, state.attributes
    if icon == "lamp" and value == "on":
        brightness = attrs.get("brightness")
        out["pct"] = round(brightness * 100 / 255) if isinstance(brightness, int | float) else 100
    elif icon == "curtains" and value == "closed":
        out["icon"] = "curtains_closed"
    elif icon == "shutter":
        position = attrs.get("current_position")
        if isinstance(position, int | float):
            out["closed"] = round(100 - position)
        elif value in ("open", "closed"):
            out["closed"] = 100 if value == "closed" else 0
    elif icon == "lock" and value == "unlocked":
        out["icon"] = "unlock"
    elif icon == "door" and value == "on":
        out["icon"] = "door_open"
    elif icon == "battery" and entity.domain == "sensor":
        with contextlib.suppress(TypeError, ValueError):
            out["level"] = max(0, min(100, round(float(value))))
    return out
