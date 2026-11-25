"""HouseMind's status in HA (E-96, E-97): state, house mode, health and saving."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HouseMindConfigEntry
from .const import HEALTH, MODES
from .entity import HouseMindEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HouseMindConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data.coordinator
    entities: list[SensorEntity] = [
        HouseMindState(coordinator, "state"),
        HouseMindEnum(coordinator, "house_mode", "mode", MODES),
        HouseMindEnum(coordinator, "health", "health", HEALTH),
    ]
    if (coordinator.data or {}).get("energy", True):  # a house without energy: no saving
        entities.append(HouseMindSaving(coordinator, "saving"))
    async_add_entities(entities)


class HouseMindState(HouseMindEntity, SensorEntity):
    """The Orb in one word, like on the screen (calm, attention, learning …), and why when it
    concerns the whole house (E-389)."""

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.get("orb_text")
        return str(value) if value else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "orb": self.coordinator.data.get("orb"),
            "reason": self.coordinator.data.get("reason"),
        }


class HouseMindEnum(HouseMindEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: Any, key: str, field: str, options: list[str]) -> None:
        super().__init__(coordinator, key)
        self._field = field
        self._attr_options = options

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.get(self._field)
        return str(value) if value in self._attr_options else None


class HouseMindSaving(HouseMindEntity, SensorEntity):
    """What HouseMind saved on energy so far, measured honestly (S-03). While it still
    measures, it says so with the days left, in HouseMind's own words – never "Unknown"
    (E-234)."""

    @property
    def _measuring(self) -> str | None:
        text = self.coordinator.data.get("saving_text")
        return str(text) if text else None

    @property
    def device_class(self) -> SensorDeviceClass | None:
        return None if self._measuring else SensorDeviceClass.MONETARY

    @property
    def native_unit_of_measurement(self) -> str | None:
        return None if self._measuring else str(self.coordinator.data.get("currency") or "EUR")

    @property
    def native_value(self) -> float | str | None:
        text = self._measuring
        if text:
            return text
        value = self.coordinator.data.get("saving")
        return float(value) if isinstance(value, int | float) else None
