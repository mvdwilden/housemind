"""Entity roles (E-55): which entities of a device matter.

main        what the device does (light, cover, climate …)
observation a useful measurement (sensor / binary_sensor with a known device class)
health      battery, signal, connectivity, firmware updates (E-43)
setting     device configuration – never touched
noise       disabled, hidden, unknown measurements – not followed
"""

from __future__ import annotations

from enum import StrEnum

from housemind.core.model import Entity

MAIN_DOMAINS = frozenset(
    {
        "light",
        "switch",
        "cover",
        "climate",
        "fan",
        "humidifier",
        "water_heater",
        "lock",
        "alarm_control_panel",
        "valve",
        "vacuum",
        "lawn_mower",
        "media_player",
        "siren",
    }
)
HEALTH_CLASSES = frozenset({"battery", "signal_strength", "connectivity", "problem", "update"})
USEFUL_SENSOR_CLASSES = frozenset(
    {
        "temperature",
        "humidity",
        "illuminance",
        "co2",
        "carbon_dioxide",
        "pm25",
        "pm10",
        "pm1",
        "volatile_organic_compounds",
        "volatile_organic_compounds_parts",
        "power",
        "energy",
        "monetary",
        "gas",
        "water",
        "volume_flow_rate",
        "battery",
        "energy_storage",
        "aqi",
        "wind_speed",
        "precipitation",
        "irradiance",
        "sound_pressure",
    }
)


class EntityRole(StrEnum):
    MAIN = "main"
    OBSERVATION = "observation"
    HEALTH = "health"
    SETTING = "setting"
    NOISE = "noise"


def entity_role(entity: Entity) -> EntityRole:
    if entity.disabled or entity.hidden:
        return EntityRole.NOISE
    if entity.entity_category == "config":
        return EntityRole.SETTING
    if entity.domain == "update" or entity.device_class in HEALTH_CLASSES:
        # HA marks a device's own battery as diagnostic; a battery level without a category is
        # the main measurement of that device (e.g. a home battery's state of charge).
        if (
            entity.domain == "sensor"
            and entity.device_class == "battery"
            and entity.entity_category is None
        ):
            return EntityRole.OBSERVATION
        return EntityRole.HEALTH
    if entity.entity_category == "diagnostic":
        return EntityRole.HEALTH
    if entity.domain in MAIN_DOMAINS:
        return EntityRole.MAIN
    if entity.domain in ("binary_sensor", "event", "person", "device_tracker", "sun", "weather"):
        return EntityRole.OBSERVATION
    if entity.domain == "sensor":
        return (
            EntityRole.OBSERVATION
            if entity.device_class in USEFUL_SENSOR_CLASSES
            else EntityRole.NOISE
        )
    if entity.domain in ("number", "select"):
        return EntityRole.MAIN
    return EntityRole.NOISE


def followed(role: EntityRole) -> bool:
    """Only what matters is followed (E-55, E-158)."""
    return role in (EntityRole.MAIN, EntityRole.OBSERVATION, EntityRole.HEALTH)
