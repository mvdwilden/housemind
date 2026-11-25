"""Pause HouseMind from HA (E-83, E-97). Emergency reactions keep working while paused."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HouseMindConfigEntry
from .entity import HouseMindEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HouseMindConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HouseMindPause(entry, "pause")])


class HouseMindPause(HouseMindEntity, SwitchEntity):
    # A setting, not a device (QA 3, gat 3): Assist does not offer it to anyone in earshot.
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: HouseMindConfigEntry, key: str) -> None:
        super().__init__(entry.runtime_data.coordinator, key)
        self._client = entry.runtime_data.client

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.get("paused"))

    def _user(self) -> str | None:
        """The person who switched it, if a person did."""
        return self._context.user_id if self._context is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._client.action({"action": "pause", "minutes": None, "user_id": self._user()})
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._client.action({"action": "resume", "user_id": self._user()})
        await self.coordinator.async_request_refresh()
