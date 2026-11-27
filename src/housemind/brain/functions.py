"""Every entity's function (E-427, E-428, design 51): what it is, and what it is for.

One function per entity from a fixed list, the same in every language. Where it comes from, in
order (51.3): what Home Assistant says itself (a setting or a diagnostic, its class and unit),
the catalogue and the words of the language files, behaviour, AI for what is left (51.4), and
a person, who always wins. This module is the rules' part: plain data in, a function or
``None`` (the rules cannot tell) out – fast, without AI, for the core.

What HouseMind does with it (E-430, 51.5): a function that makes it do less – a setting, a
status, the device itself, outside – counts at once, also from AI; one that makes it do more – a
control, a measurement of the room – counts at once for watching and learning, and only after
Klopt for proposing and acting.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from housemind.core.model import Entity, HouseModel
from housemind.knowledge.words import has_word as words_have

# The fixed list (51.2), per group.
CONTROLS = frozenset(
    {
        "lamp",
        "cover",
        "room_climate",
        "fan",
        "player",
        "appliance_power",
        "appliance_start",
        "lock",
        "other_control",
    }
)
ROOM = frozenset(
    {
        "air_temperature",
        "humidity",
        "co2",
        "air_quality",
        "light_level",
        "presence",
        "motion",
        "opening",
        "safety",
    }
)
OUTSIDE = frozenset({"outside_temperature", "weather", "outside_light"})
DEVICE = frozenset(
    {
        "device_control",
        "device_temperature",
        "water_temperature",
        "device_power",
        "device_energy",
        "device_state",
    }
)
STATUS = frozenset({"status_light", "fault", "battery", "signal", "update", "connectivity"})
SETTING = frozenset({"setting"})
UNKNOWN = "unknown"
FUNCTIONS = CONTROLS | ROOM | OUTSIDE | DEVICE | STATUS | SETTING | {UNKNOWN}
# What makes HouseMind do less counts at once, also from AI; what makes it do more only after
# Klopt for proposing and acting (E-430).
LESS = OUTSIDE | DEVICE | STATUS | SETTING
MORE = CONTROLS | ROOM

# What is no device in a room: HouseMind's own world, people and services.
NOT_A_DEVICE = frozenset(
    {
        "automation",
        "script",
        "scene",
        "person",
        "zone",
        "sun",
        "tts",
        "stt",
        "conversation",
        "notify",
        "ai_task",
        "wake_word",
        "device_tracker",
        "image",
        "camera",
        "assist_satellite",
        "calendar",
        "todo",
    }
)
SETTING_DOMAINS = frozenset({"number", "select", "text", "button", "time", "date", "datetime"})
CONTROL_DOMAINS = {
    "cover": "cover",
    "fan": "fan",
    "media_player": "player",
    "lock": "lock",
    "vacuum": "appliance_start",
    "lawn_mower": "appliance_start",
    "water_heater": "device_control",
    "humidifier": "other_control",
    "valve": "other_control",
    "remote": "other_control",
    "siren": "safety",
    "alarm_control_panel": "other_control",
}
# A switch's role (the catalogue's, a label's, behaviour's) as a function.
SWITCH_ROLES = {"light": "lamp", "appliance_start": "appliance_start"}
# What an administrator can say a switch drives in Wat ik weet (E-580): the catalogue's roles,
# or another device – then it is no lamp and no other role, an ordinary switch.
OTHER_DEVICE = "other"
SWITCH_CHOICES = ("light", "appliance_start", "cooking_off", OTHER_DEVICE)
STATUS_CLASSES = {
    "battery": "battery",
    "signal_strength": "signal",
    "problem": "fault",
    "tamper": "fault",
    "connectivity": "connectivity",
    "update": "update",
}
AIR_QUALITY = frozenset(
    {
        "pm1",
        "pm25",
        "pm10",
        "volatile_organic_compounds",
        "volatile_organic_compounds_parts",
        "aqi",
        "nitrogen_dioxide",
        "ozone",
        "carbon_monoxide",
    }
)
SAFETY = frozenset({"smoke", "gas", "carbon_monoxide", "moisture", "safety"})
OPENINGS = frozenset({"door", "window", "opening", "garage_door"})


@dataclass(frozen=True, slots=True)
class Context:
    """What the rules know of the house besides the entity itself, read in memory."""

    status_leds: set[str] = field(default_factory=set)  # E-426
    roles: Mapping[str, str] = field(default_factory=dict)  # a switch's role (gate)
    room_temperatures: set[str] = field(default_factory=set)  # sensors that measure a room
    device_temperatures: set[str] = field(default_factory=set)  # a socket's own warmth
    room_climates: set[str] = field(default_factory=set)  # climate entities of a room
    openings: set[str] = field(default_factory=set)  # door and window contacts of the house
    outside_areas: set[str] = field(default_factory=set)
    camera_devices: set[str] = field(default_factory=set)
    # Devices with a switch or light that is the device itself – HA gives that one no name of
    # its own; a second switch there with a name ("Charging protection") is a setting (E-458).
    main_devices: set[str] = field(default_factory=set)
    water_words: tuple[str, ...] = ()  # daily_words.water_temperature
    outside_words: tuple[str, ...] = ()  # daily_words.outside
    not_room_words: tuple[str, ...] = ()  # daily_words.temperature_not_room


def main_devices(model: HouseModel) -> set[str]:
    """Devices whose own switch or lamp HA shows without a name of its own (E-458)."""
    return {
        entity.device_id
        for entity in model.entities.values()
        if entity.device_id is not None
        and entity.domain in ("switch", "light")
        and entity.original_name is None
        and not (entity.entity_category or entity.hidden or entity.disabled)
    }


def has_function(entity: Entity) -> bool:
    """A device's entity in the house: not HouseMind's own world, a person or a service."""
    return entity.domain not in NOT_A_DEVICE and not entity.domain.startswith("input_")


def by_rules(entity: Entity, model: HouseModel, ctx: Context) -> str | None:
    """What the rules say an entity is (51.3, steps 1 and 2); ``None``: they cannot tell."""
    domain, cls = entity.domain, entity.device_class
    if not has_function(entity):
        return None
    if domain == "update":
        return "update"
    if entity.entity_category == "config" or domain in SETTING_DOMAINS:
        role = ctx.roles.get(entity.entity_id)
        if role is None:
            return "setting"
        # A setting HouseMind steers by its role, such as the export limit of the energy plan:
        # the device's own control, never left alone.
        return SWITCH_ROLES.get(role, "appliance_power") if domain == "switch" else "device_control"
    if domain in ("sensor", "binary_sensor") and cls in STATUS_CLASSES:
        return STATUS_CLASSES[cls]
    if entity.entity_category == "diagnostic":
        return "device_state"
    if domain == "light":
        return "status_light" if entity.entity_id in ctx.status_leds else "lamp"
    if domain == "switch":
        role = ctx.roles.get(entity.entity_id)
        if role is None and entity.original_name and entity.device_id in ctx.main_devices:
            return "setting"  # next to the device's own switch or lamp (E-458)
        return None if role is None else SWITCH_ROLES.get(role, "appliance_power")
    if domain == "climate":
        return "room_climate" if entity.entity_id in ctx.room_climates else "device_control"
    if domain in CONTROL_DOMAINS:
        return CONTROL_DOMAINS[domain]
    area = model.area_of(entity.entity_id)
    outside = area is not None and area.id in ctx.outside_areas
    if domain == "sensor":
        return _sensor(entity, outside, ctx)
    if domain == "binary_sensor":
        return _binary(entity, ctx)
    if domain == "weather":
        return "weather"
    if domain == "event":
        return "device_state"
    return None


def _sensor(entity: Entity, outside: bool, ctx: Context) -> str | None:
    cls, eid = entity.device_class, entity.entity_id
    text = f"{eid} {entity.name}"
    if cls == "temperature":
        if eid in ctx.room_temperatures:
            return "air_temperature"
        if outside or has_word(text, ctx.outside_words):
            return "outside_temperature"
        if has_word(text, ctx.water_words):
            return "water_temperature"
        if eid in ctx.device_temperatures or has_word(text, ctx.not_room_words):
            return "device_temperature"
        return None
    if cls == "humidity":
        return "weather" if outside else "humidity"
    if cls == "carbon_dioxide":
        return "co2"
    if cls in AIR_QUALITY:
        return "weather" if outside else "air_quality"
    if cls == "illuminance":
        return "outside_light" if outside else "light_level"
    if cls in ("power", "apparent_power", "reactive_power"):
        return "device_power"
    if cls == "energy":
        return "device_energy"
    if cls in ("pressure", "atmospheric_pressure", "wind_speed", "precipitation"):
        return "weather"
    if cls is not None:
        return "device_state"  # a voltage, a duration, a timestamp …: the device's own
    return None


def _binary(entity: Entity, ctx: Context) -> str | None:
    cls = entity.device_class
    on_camera = entity.device_id is not None and entity.device_id in ctx.camera_devices
    if cls in ("presence", "occupancy"):
        return "device_state" if on_camera else "presence"
    if cls == "motion":
        return "device_state" if on_camera else "motion"
    if cls in OPENINGS:
        return "opening" if entity.entity_id in ctx.openings else "device_state"
    if cls in SAFETY:
        return "safety"
    if cls is not None:
        return "device_state"
    return None


def has_word(text: str, words: Iterable[str]) -> bool:
    """A word at the start of a word in the text ("buiten" in "Buitentemperatuur")."""
    return words_have(text, words)  # one rule for every language (code QA 3, punt 4)


def counts_for_acting(function: str, source: str) -> bool:
    """Whether a function may make HouseMind propose or act (E-430): what makes it do less
    always; what makes it do more only from the rules, behaviour after Klopt, or a person."""
    return function in LESS or source in ("rules", "person")
