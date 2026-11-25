"""The house mode as a choice in HA (E-97, E-286): home, away, sleep, vacation or guests.

Choosing one is the same as the action housemind.house_mode: it holds until who is home
changes, and vacation lasts until someone comes home.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HouseMindConfigEntry
from .const import MODES
from .entity import HouseMindEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HouseMindConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([HouseMindModeSelect(entry, "house_mode_choice")])


class HouseMindModeSelect(HouseMindEntity, SelectEntity):
    _attr_options = MODES

    def __init__(self, entry: HouseMindConfigEntry, key: str) -> None:
        super().__init__(entry.runtime_data.coordinator, key)
        self._client = entry.runtime_data.client

    @property
    def current_option(self) -> str | None:
        mode = self.coordinator.data.get("mode")
        return mode if mode in MODES else None

    async def async_select_option(self, option: str) -> None:
        await self._client.action({"action": "house_mode", "mode": option})
        await self.coordinator.async_request_refresh()
