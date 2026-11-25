"""HouseMind in Home Assistant (B-06, E-96, E-97, E-167).

A small integration the HouseMind App places after an administrator's yes. It shows
HouseMind's status as entities, offers its actions, and is a conversation agent for Assist.
It never makes automations, scripts or scenes (E-03): every action goes to the App, through
the same safety gate as in the app itself (E-110). One thing it does by itself: when HouseMind
goes silent for five minutes, its watchdog gives back what HouseMind had set on the energy
devices (E-329, B-204).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import HouseMindClient, HouseMindError
from .const import CONF_HOST, CONF_PORT, CONF_SECRET, DOMAIN, MODES, SCAN_INTERVAL
from .watchdog import Watchdog

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.SELECT, Platform.CONVERSATION]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class HouseMindData:
    client: HouseMindClient
    coordinator: DataUpdateCoordinator[dict[str, Any]]


type HouseMindConfigEntry = ConfigEntry[HouseMindData]


async def _start_watchdog(hass: HomeAssistant) -> Watchdog:
    """Runs from Home Assistant's start, also while HouseMind cannot be reached (B-204)."""
    store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.fallback")

    async def call(domain: str, service: str, data: dict[str, Any]) -> None:
        await hass.services.async_call(domain, service, data, blocking=True)

    async def notify(title: str, text: str) -> None:
        persistent_notification.async_create(hass, text, title, f"{DOMAIN}_watchdog")
        _LOGGER.warning("%s: %s", title, text)

    def name(entity_id: str) -> str:
        state = hass.states.get(entity_id)
        return str(state.name) if state is not None else entity_id

    watchdog = Watchdog(
        dt_util.utcnow, store.async_save, call, notify, lambda: hass.config.language, name
    )
    watchdog.load(await store.async_load())

    async def check(_now: Any) -> None:
        await watchdog.check()

    async_track_time_interval(hass, check, SCAN_INTERVAL)
    return watchdog


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """The actions (E-97). They work when HouseMind is set up. And the watchdog (B-204)."""
    hass.data[DOMAIN] = {"watchdog": await _start_watchdog(hass)}

    def data() -> HouseMindData:
        for entry in hass.config_entries.async_entries(DOMAIN):
            runtime = getattr(entry, "runtime_data", None)
            if isinstance(runtime, HouseMindData):
                return runtime
        raise HomeAssistantError("HouseMind is not set up")

    async def send(payload: dict[str, Any]) -> dict[str, Any]:
        runtime = data()
        try:
            result = await runtime.client.action(payload)
        except HouseMindError as err:
            raise HomeAssistantError(f"HouseMind did not answer: {err}") from err
        await runtime.coordinator.async_request_refresh()
        return result

    # Who asked, so HouseMind knows: longer than an hour only for an administrator, and the
    # Logboek says who paused (QA 3, gat 3). An automation has no person: an hour.
    async def pause(call: ServiceCall) -> None:
        await send(
            {
                "action": "pause",
                "minutes": call.data.get("minutes"),
                "user_id": call.context.user_id,
            }
        )

    async def resume(call: ServiceCall) -> None:
        await send({"action": "resume", "user_id": call.context.user_id})

    async def house_mode(call: ServiceCall) -> None:
        await send(
            {"action": "house_mode", "mode": call.data["mode"], "user_id": call.context.user_id}
        )

    async def feedback(call: ServiceCall) -> None:
        # One decision if given, else the last action; the person from the call (E-298).
        await send(
            {
                "action": "feedback",
                "good": call.data["good"],
                "decision": call.data.get("decision"),
                "user_id": call.context.user_id,
            }
        )

    async def house_rule(call: ServiceCall) -> ServiceResponse:
        result = await send(
            {
                "action": "house_rule",
                "text": call.data["text"],
                "confirm": call.data.get("confirm", False),
                "language": hass.config.language,
                "user_id": call.context.user_id,  # only from level Involved (E-292)
            }
        )
        return {k: result.get(k) for k in ("ok", "read_back", "saved", "message")}

    async def ask(call: ServiceCall) -> ServiceResponse:
        result = await send(
            {
                "action": "ask",
                "question": call.data["question"],
                "language": hass.config.language,
                "user_id": call.context.user_id,
            }
        )
        return {"answer": result.get("answer", "")}

    hass.services.async_register(
        DOMAIN,
        "pause",
        pause,
        schema=vol.Schema({vol.Optional("minutes"): vol.All(int, vol.Range(min=0, max=10080))}),
    )
    hass.services.async_register(DOMAIN, "resume", resume)
    hass.services.async_register(
        DOMAIN, "house_mode", house_mode, schema=vol.Schema({vol.Required("mode"): vol.In(MODES)})
    )
    hass.services.async_register(
        DOMAIN,
        "feedback",
        feedback,
        schema=vol.Schema(
            {vol.Required("good"): cv.boolean, vol.Optional("decision"): vol.Coerce(int)}
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "house_rule",
        house_rule,
        schema=vol.Schema(
            {
                vol.Required("text"): vol.All(cv.string, vol.Length(max=300)),
                vol.Optional("confirm", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "ask",
        ask,
        schema=vol.Schema({vol.Required("question"): vol.All(cv.string, vol.Length(max=500))}),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HouseMindConfigEntry) -> bool:
    client = HouseMindClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        int(entry.data[CONF_PORT]),
        entry.data[CONF_SECRET],
    )

    watchdog: Watchdog | None = hass.data.get(DOMAIN, {}).get("watchdog")

    async def update() -> dict[str, Any]:
        try:
            state = await client.state(watchdog.done_at if watchdog else None)
        except HouseMindError as err:
            raise UpdateFailed(str(err)) from err
        if watchdog is not None:
            await watchdog.contact(state.get("fallback"))
        return state

    coordinator: DataUpdateCoordinator[dict[str, Any]] = DataUpdateCoordinator(
        hass,
        _LOGGER,
        config_entry=entry,
        name=DOMAIN,
        update_method=update,
        update_interval=SCAN_INTERVAL,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = HouseMindData(client, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HouseMindConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
