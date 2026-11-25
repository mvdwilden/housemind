"""The common base: one HouseMind device with its entities."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN


class HouseMindEntity(CoordinatorEntity[DataUpdateCoordinator[dict[str, Any]]]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: DataUpdateCoordinator[dict[str, Any]], key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        entry_id = entry.entry_id if entry is not None else DOMAIN
        self._attr_unique_id = f"{entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=str(coordinator.data.get("name") or "HouseMind"),
            manufacturer="HouseMind",
            sw_version=str(coordinator.data.get("version") or ""),
            entry_type=DeviceEntryType.SERVICE,
        )
