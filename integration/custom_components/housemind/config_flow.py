"""Setting up HouseMind: found by the App's announcement, or from the file the App placed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HouseMindClient, HouseMindError
from .const import CONF_HOST, CONF_PORT, CONF_SECRET, DOMAIN

CONNECTION = Path(__file__).parent / "connection.json"


def _read_connection() -> dict[str, Any] | None:
    try:
        raw = json.loads(CONNECTION.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not all(k in raw for k in (CONF_HOST, CONF_PORT, CONF_SECRET)):
        return None
    return {k: raw[k] for k in (CONF_HOST, CONF_PORT, CONF_SECRET)}


class HouseMindConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._connection: dict[str, Any] = {}

    async def _reachable(self, connection: dict[str, Any]) -> bool:
        client = HouseMindClient(
            async_get_clientsession(self.hass),
            str(connection[CONF_HOST]),
            int(connection[CONF_PORT]),
            str(connection[CONF_SECRET]),
        )
        try:
            await client.state()
        except HouseMindError:
            return False
        return True

    async def async_step_hassio(self, discovery_info: Any) -> ConfigFlowResult:
        """The App announced itself (Supervisor discovery)."""
        config = dict(getattr(discovery_info, "config", {}) or {})
        connection = {k: config.get(k) for k in (CONF_HOST, CONF_PORT, CONF_SECRET)}
        # The App's connection next to this integration is always the current one; an older
        # announcement can still carry the key from before the factory settings (found in the
        # real house: confirming it was refused).
        placed = await self.hass.async_add_executor_job(_read_connection)
        if placed is not None:
            connection = placed
        if not all(connection.values()):
            return self.async_abort(reason="no_app")
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured(updates=connection)
        self._connection = connection
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            if not await self._reachable(self._connection):
                return self.async_abort(reason="cannot_connect")
            return self.async_create_entry(title="HouseMind", data=self._connection)
        return self.async_show_form(step_id="hassio_confirm")

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Set up by hand: the App placed its connection next to this integration."""
        connection = await self.hass.async_add_executor_job(_read_connection)
        if connection is None:
            return self.async_abort(reason="no_app")
        # A discovered HouseMind may still be waiting: setting it up by hand is fine too, and
        # HA closes the discovered one once this entry exists.
        await self.async_set_unique_id(DOMAIN, raise_on_progress=False)
        self._abort_if_unique_id_configured(updates=connection)
        if user_input is not None:
            if not await self._reachable(connection):
                return self.async_show_form(step_id="user", errors={"base": "cannot_connect"})
            return self.async_create_entry(title="HouseMind", data=connection)
        return self.async_show_form(step_id="user")
