"""Real Home Assistant connection over the websocket API (via the Supervisor, H7.1)."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import aiohttp

from housemind.core.clock import Clock
from housemind.core.model import HouseModel, State, StateChange
from housemind.ha import supervisor
from housemind.ha.adapter import (
    ActionResult,
    ConnectionListener,
    EventListener,
    HAAutomation,
    HAConfig,
    HAConfigEntry,
    HALightRhythm,
    HAScript,
    HAUser,
    HistoryChange,
    Installation,
    RegistryListener,
    StateListener,
    Subscription,
    Voice,
)
from housemind.ha.source import SourceTracker
from housemind.ha.translate import (
    build_model,
    form_values,
    parse_automation,
    parse_config_entry,
    parse_history,
    parse_light_rhythm,
    parse_script,
    parse_state,
)

_LOGGER = logging.getLogger(__name__)
AI_TASK_WAIT = 90.0  # seconds an AI task of Home Assistant may take (E-422)

SUPERVISOR_URL = "ws://supervisor/core/websocket"
NOTIFICATION_ACTION = "mobile_app_notification_action"
RHYTHM_DOMAIN = "adaptive_lighting"  # the integration that lets light follow the day (E-465)
AUTOMATION_TRIGGERED = "automation_triggered"  # which automation a change came from (E-448)
USERS_TTL_S = 300
ACTIONS_TTL_S = 60.0  # HA's list of actions, kept this long
_REGISTRY_EVENTS = (
    "area_registry_updated",
    "device_registry_updated",
    "entity_registry_updated",
    "floor_registry_updated",
    "label_registry_updated",  # labels and categories are hints (E-345)
    "category_registry_updated",
)
CATEGORY_SCOPES = ("automation", "script", "scene", "helpers")
# Trying again after the connection was lost: a little longer each time, at most a minute.
RETRY_DELAYS_S = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0)
STARTING_WAIT_S = 300.0  # how long to wait for HA to finish starting before going on anyway


STT_WAIT = 20.0  # seconds HA may take to hear what was said
# An announcement on a voice satellite answers only once the sentence has been said – a morning
# briefing takes half a minute or more. A failure comes back at once; still busy after this long
# means it is being said (found in the house: the briefing five times in a row, every round, each
# wait that ran out taken for 'not said', E-107).
SPEAK_WAIT_S = 5.0


def _degrees(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


class HAConnectionError(ConnectionError):
    pass


class HAAuthError(HAConnectionError):
    """Home Assistant said no to the token: trying again does not help."""


class WebSocketHomeAssistant:
    """Home Assistant via its websocket API."""

    def __init__(
        self,
        clock: Clock,
        token: str,
        url: str = SUPERVISOR_URL,
        session: aiohttp.ClientSession | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._url = url
        self._token = token
        self._timeout = timeout_s
        self.speak_wait_s = SPEAK_WAIT_S
        self._session = session
        self._own_session = session is None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._state_listeners: list[StateListener] = []
        self._registry_listeners: list[RegistryListener] = []
        self._action_listeners: list[EventListener] = []
        self._event_handlers: dict[int, str] = {}
        self._streams: dict[int, asyncio.Queue[dict[str, Any]]] = {}  # a running pipeline
        # Events are handled one by one in their own task, so a listener that asks Home
        # Assistant something never blocks the reader that brings the answer.
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._users: tuple[float, list[HAUser]] | None = None
        self._actions: tuple[float, dict[str, set[str]]] | None = None
        self.tracker = SourceTracker(clock)
        self._connection_listeners: list[ConnectionListener] = []
        self._reconnector: asyncio.Task[None] | None = None
        self._closing = False
        self._ha_version: str | None = None
        self.retry_delays = RETRY_DELAYS_S
        self.starting_poll_s = 2.0

    # -- connection ---------------------------------------------------------------------

    async def connect(self) -> HAConfig:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        if self._worker is None:
            self._worker = asyncio.create_task(self._work(), name="housemind-ha-events")
        attempt = 0
        while True:
            try:
                await self._open()
                break
            except HAAuthError:
                raise
            except (aiohttp.ClientError, OSError, HAConnectionError, TimeoutError) as err:
                # Home Assistant is not there yet at the start (M-34): wait and try again, a
                # little longer each time, as after a lost connection.
                _LOGGER.warning("Home Assistant not reachable yet (%s), trying again", err)
                await asyncio.sleep(self.retry_delays[min(attempt, len(self.retry_delays) - 1)])
                attempt += 1
        return await self.ha_config()

    async def _open(self) -> None:
        """Connect, sign in, listen again, and wait until HA has finished starting."""
        assert self._session is not None
        # History can be a large answer (E-257): more room than aiohttp's 4 MB.
        ws = await self._session.ws_connect(self._url, heartbeat=30, max_msg_size=64 * 2**20)
        try:
            hello = await ws.receive_json(timeout=self._timeout)
            if hello.get("type") != "auth_required":
                raise HAConnectionError(f"unexpected greeting: {hello.get('type')}")
            await ws.send_json({"type": "auth", "access_token": self._token})
            auth = await ws.receive_json(timeout=self._timeout)
            if auth.get("type") != "auth_ok":
                raise HAAuthError("authentication with Home Assistant failed")
        except BaseException:
            await ws.close()
            raise
        self._ha_version = auth.get("ha_version")
        self._ws = ws
        self._actions = None  # a (re)started HA may offer other actions
        self._event_handlers.clear()
        self._reader = asyncio.create_task(self._read_loop(ws), name="housemind-ha-reader")
        user = await self._command({"type": "auth/current_user"})
        self.tracker.own_user_id = user.get("id")
        await self._subscribe("state_changed")
        for event in _REGISTRY_EVENTS:
            await self._subscribe(event)
        await self._subscribe(NOTIFICATION_ACTION)
        try:
            await self._subscribe(AUTOMATION_TRIGGERED)
        except HAConnectionError as err:  # without it, an automation's lamps count as before
            _LOGGER.warning("Could not follow which automation acts: %s", err)
        await self._wait_until_running()

    async def _wait_until_running(self) -> None:
        """Right after a restart HA answers before all its devices are back. HouseMind waits
        for "RUNNING", so its self-check does not mistake a starting HA for a broken one."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STARTING_WAIT_S
        while True:
            config = await self._command({"type": "get_config"})
            state = (config or {}).get("state")
            if state in (None, "RUNNING") or loop.time() >= deadline:
                return
            await asyncio.sleep(self.starting_poll_s)

    async def ha_config(self) -> HAConfig:
        config = await self._command({"type": "get_config"}) or {}
        return HAConfig(
            version=str(config.get("version") or self._ha_version or "unknown"),
            language=str(config.get("language") or "en"),
            country=config.get("country"),
            time_zone=str(config.get("time_zone") or "UTC"),
            currency=str(config.get("currency") or "EUR"),
            temperature_unit=str((config.get("unit_system") or {}).get("temperature") or "°C"),
            latitude=_degrees(config.get("latitude")),
            longitude=_degrees(config.get("longitude")),
        )

    def on_connection(self, listener: ConnectionListener) -> Subscription:
        self._connection_listeners.append(listener)
        return Subscription(lambda: self._connection_listeners.remove(listener))

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def _tell(self, connected: bool) -> None:
        for listener in list(self._connection_listeners):
            try:
                await listener(connected)
            except Exception:
                _LOGGER.exception("Connection listener failed")

    async def _reconnect(self) -> None:
        """The connection was lost: tell HouseMind, then try again until HA is back."""
        await self._tell(False)
        attempt = 0
        while not self._closing:
            await asyncio.sleep(self.retry_delays[min(attempt, len(self.retry_delays) - 1)])
            attempt += 1
            try:
                await self._open()
            except (aiohttp.ClientError, OSError, HAConnectionError, TimeoutError) as err:
                _LOGGER.info("Home Assistant not reachable yet (%s), trying again", err)
                continue
            _LOGGER.info("Connected to Home Assistant again after %s tries", attempt)
            await self._tell(True)
            return

    async def close(self) -> None:
        self._closing = True
        for task in (self._reconnector, self._reader, self._worker):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self._ws is not None:
            await self._ws.close()
        if self._own_session and self._session is not None:
            await self._session.close()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(HAConnectionError("connection closed"))
        self._pending.clear()

    # -- commands -----------------------------------------------------------------------

    async def _command(self, message: dict[str, Any], wait: float | None = None) -> Any:
        if self._ws is None:
            raise HAConnectionError("not connected")
        msg_id = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._ws.send_json({"id": msg_id, **message})
        except (ConnectionResetError, RuntimeError) as err:
            self._pending.pop(msg_id, None)
            raise HAConnectionError("connection lost") from err
        try:
            return await asyncio.wait_for(future, wait or self._timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def _subscribe(self, event_type: str) -> None:
        if self._ws is None:
            raise HAConnectionError("not connected")
        msg_id = next(self._ids)
        self._event_handlers[msg_id] = event_type
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._ws.send_json(
                {"id": msg_id, "type": "subscribe_events", "event_type": event_type}
            )
        except (ConnectionResetError, RuntimeError) as err:
            self._pending.pop(msg_id, None)
            raise HAConnectionError("connection lost") from err
        try:
            await asyncio.wait_for(future, self._timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def _read_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type is not aiohttp.WSMsgType.TEXT:
                continue
            data = msg.json()
            messages = data if isinstance(data, list) else [data]
            for message in messages:
                await self._dispatch(message)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(HAConnectionError("connection lost"))
        if ws is self._ws and not self._closing:
            self._ws = None
            _LOGGER.warning("Lost the connection to Home Assistant; trying again")
            self._reconnector = asyncio.create_task(self._reconnect(), name="housemind-ha-again")

    async def _dispatch(self, message: dict[str, Any]) -> None:
        msg_type = message.get("type")
        msg_id = message.get("id")
        if msg_type == "result" and isinstance(msg_id, int):
            future = self._pending.get(msg_id)
            if future is not None and not future.done():
                if message.get("success"):
                    future.set_result(message.get("result"))
                else:
                    error = message.get("error") or {}
                    future.set_exception(
                        HAConnectionError(f"{error.get('code')}: {error.get('message')}")
                    )
        elif msg_type == "event" and isinstance(msg_id, int) and msg_id in self._streams:
            self._streams[msg_id].put_nowait(message.get("event") or {})
        elif msg_type == "event" and isinstance(msg_id, int):
            self._events.put_nowait(message)

    async def _work(self) -> None:
        while True:
            message = await self._events.get()
            try:
                await self._handle_event(message)
            except Exception:
                _LOGGER.exception("Handling a Home Assistant event failed")

    async def _handle_event(self, message: dict[str, Any]) -> None:
        msg_id = message.get("id")
        if isinstance(msg_id, int):
            event = message.get("event") or {}
            event_type = self._event_handlers.get(msg_id)
            if event_type == "state_changed":
                await self._handle_state_changed(event.get("data") or {})
            elif event_type == NOTIFICATION_ACTION:
                for action_listener in list(self._action_listeners):
                    await action_listener(dict(event.get("data") or {}))
            elif event_type == AUTOMATION_TRIGGERED:
                context = event.get("context") or {}
                automation_id = (event.get("data") or {}).get("entity_id")
                if isinstance(context.get("id"), str) and isinstance(automation_id, str):
                    parent = context.get("parent_id")
                    self.tracker.automation_ran(
                        context["id"], automation_id, parent if isinstance(parent, str) else None
                    )
            elif event_type in _REGISTRY_EVENTS:
                for listener in list(self._registry_listeners):
                    await listener(event_type)

    async def _handle_state_changed(self, data: dict[str, Any]) -> None:
        old = parse_state(data.get("old_state"))
        new = parse_state(data.get("new_state"))
        entity_id = str(data.get("entity_id"))
        context = new.context if new else None
        change = StateChange(
            entity_id,
            old,
            new,
            self.tracker.classify(entity_id, context),
            self.tracker.run_of(context),
        )
        real = old is not None and old.state not in ("unavailable", "unknown")
        self.tracker.saw(context, change.source, real)
        for listener in list(self._state_listeners):
            try:
                await listener(change)
            except Exception:
                _LOGGER.exception("State listener failed for %s", entity_id)

    # -- the protocol -------------------------------------------------------------------

    async def get_states(self) -> dict[str, State]:
        raw = await self._command({"type": "get_states"})
        states = {}
        for item in raw or []:
            state = parse_state(item)
            if state is not None:
                states[state.entity_id] = state
        return states

    async def load_model(self) -> HouseModel:
        floors, areas, devices, entities, states = await asyncio.gather(
            self._command({"type": "config/floor_registry/list"}),
            self._command({"type": "config/area_registry/list"}),
            self._command({"type": "config/device_registry/list"}),
            self._command({"type": "config/entity_registry/list"}),
            self.get_states(),
        )
        labels = await self._optional({"type": "config/label_registry/list"})
        categories = {
            scope: await self._optional({"type": "config/category_registry/list", "scope": scope})
            for scope in CATEGORY_SCOPES
        }
        return build_model(
            floors or [], areas or [], devices or [], entities or [], states, labels, categories
        )

    async def _optional(self, command: dict[str, Any]) -> list[dict[str, Any]]:
        """A registry the house may well do without (labels, categories – E-345): an older or
        failing answer gives none, never a broken start."""
        try:
            return list(await self._command(command) or [])
        except (HAConnectionError, TimeoutError):
            return []

    async def call_action(
        self, action: str, entity_id: str, data: dict[str, Any] | None = None
    ) -> ActionResult:
        domain, service = action.split(".", 1)
        self.tracker.expect(entity_id)
        try:
            result = await self._command(
                {
                    "type": "call_service",
                    "domain": domain,
                    "service": service,
                    "service_data": dict(data or {}),
                    "target": {"entity_id": entity_id},
                }
            )
        except (HAConnectionError, TimeoutError) as err:
            return ActionResult(ok=False, error=str(err))
        context_id = ((result or {}).get("context") or {}).get("id")
        if context_id:
            self.tracker.record_own_context(str(context_id))
        return ActionResult(ok=True, context_id=context_id)

    async def available_actions(self) -> dict[str, set[str]]:
        """Every action HA offers, kept for a minute (code QA 3, punt 15): a person coming
        home asks which phones can get a message, and the whole list is large."""
        now = time.monotonic()
        if self._actions is not None and now - self._actions[0] < ACTIONS_TTL_S:
            return self._actions[1]
        raw = await self._command({"type": "get_services"})
        found = {domain: set(services) for domain, services in (raw or {}).items()}
        self._actions = (now, found)
        return found

    async def energy_preferences(self) -> dict[str, Any]:
        try:
            result = await self._command({"type": "energy/get_prefs"})
        except HAConnectionError:
            return {}
        return dict(result or {})

    async def solar_forecast(self) -> dict[datetime, float]:
        """Sum of the solar forecasts HA's energy settings use (Wh per hour)."""
        try:
            raw = await self._command({"type": "energy/solar_forecast"})
        except (HAConnectionError, TimeoutError):
            return {}
        out: dict[datetime, float] = {}
        for forecast in (raw or {}).values():
            for moment, wh in ((forecast or {}).get("wh_hours") or {}).items():
                try:
                    start = datetime.fromisoformat(str(moment).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if start.tzinfo is None:
                    start = start.replace(tzinfo=UTC)
                if isinstance(wh, int | float):
                    out[start] = out.get(start, 0.0) + float(wh)
        return out

    async def automations(self) -> list[HAAutomation]:
        found = []
        for entity_id, state in (await self.get_states()).items():
            if not entity_id.startswith("automation."):
                continue
            try:
                raw = await self._command({"type": "automation/config", "entity_id": entity_id})
            except (HAConnectionError, TimeoutError):
                raw = None  # unknown to HA's editor: HouseMind cannot read what it does
            config = raw.get("config") if isinstance(raw, dict) else None
            found.append(parse_automation(state, config if isinstance(config, dict) else None))
        return found

    async def history(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> list[HistoryChange]:
        span = {"start_time": start.isoformat(), "end_time": end.isoformat()}
        raw = await self._command(
            {
                "type": "history/history_during_period",
                **span,
                "entity_ids": entity_ids,
                "include_start_time_state": True,
                "significant_changes_only": False,
                "minimal_response": False,
                "no_attributes": False,
            },
            wait=120,
        )
        book = await self._command(
            {"type": "logbook/get_events", **span, "entity_ids": entity_ids}, wait=120
        )
        return parse_history(raw, book, self.tracker.own_user_id)

    async def history_start(self) -> datetime | None:
        now = datetime.now(UTC)
        raw = await self._command(
            {
                "type": "history/history_during_period",
                "start_time": (now - timedelta(days=60)).isoformat(),
                "end_time": now.isoformat(),
                "entity_ids": ["sun.sun"],
                "include_start_time_state": False,
                "minimal_response": True,
                "no_attributes": True,
            },
            wait=60,
        )
        rows = raw.get("sun.sun") if isinstance(raw, dict) else None
        changes = parse_history({"sun.sun": rows[:1]}, [], None) if rows else []
        return changes[0].at if changes else None

    async def scripts(self) -> list[HAScript]:
        found = []
        for entity_id, state in (await self.get_states()).items():
            if not entity_id.startswith("script."):
                continue
            try:
                raw = await self._command({"type": "script/config", "entity_id": entity_id})
            except (HAConnectionError, TimeoutError):
                raw = None
            config = raw.get("config") if isinstance(raw, dict) else None
            found.append(parse_script(state, config if isinstance(config, dict) else None))
        return found

    def on_state_change(self, listener: StateListener) -> Subscription:
        self._state_listeners.append(listener)
        return Subscription(lambda: self._state_listeners.remove(listener))

    def on_registry_change(self, listener: RegistryListener) -> Subscription:
        self._registry_listeners.append(listener)
        return Subscription(lambda: self._registry_listeners.remove(listener))

    async def set_entity_enabled(self, entity_id: str, enabled: bool) -> ActionResult:
        """The registry's 'disabled by user' on or off (B-243). Home Assistant reloads the
        integration by itself after about 30 seconds when an entity comes back."""
        try:
            result = await self._command(
                {
                    "type": "config/entity_registry/update",
                    "entity_id": entity_id,
                    "disabled_by": None if enabled else "user",
                }
            )
        except (HAConnectionError, TimeoutError) as err:
            return ActionResult(False, error=str(err) or "no_answer")
        entry = (result or {}).get("entity_entry") if isinstance(result, dict) else None
        disabled = isinstance(entry, dict) and entry.get("disabled_by") is not None
        if entry is None or disabled == enabled:
            return ActionResult(False, error="not_confirmed")
        return ActionResult(True)

    async def energy_validate(self) -> dict[str, Any]:
        """Home Assistant's own check of its energy settings (B-267), read only."""
        try:
            result = await self._command({"type": "energy/validate"})
        except (HAConnectionError, TimeoutError):
            return {}
        return dict(result or {})

    async def system_health(self) -> dict[str, Any]:
        """System health (B-241): Home Assistant sends what it knows at once as the first
        event, and later what it still had to look up; only that first part is taken."""
        if self._ws is None:
            return {}
        msg_id = next(self._ids)
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._streams[msg_id] = events
        result: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = result
        try:
            await self._ws.send_json({"id": msg_id, "type": "system_health/info"})
            await asyncio.wait_for(result, self._timeout)
            while True:
                event = await asyncio.wait_for(events.get(), self._timeout)
                if event.get("type") == "initial":
                    return dict(event.get("data") or {})
        except (HAConnectionError, TimeoutError, ConnectionResetError, RuntimeError):
            return {}
        finally:
            self._streams.pop(msg_id, None)
            self._pending.pop(msg_id, None)
            with contextlib.suppress(HAConnectionError, TimeoutError):
                await self._command({"type": "unsubscribe_events", "subscription": msg_id})

    async def backups(self) -> list[dict[str, Any]] | None:
        """The backups (B-241), read only."""
        try:
            result = await self._command({"type": "backup/info"})
        except (HAConnectionError, TimeoutError):
            return None
        raw = (result or {}).get("backups") if isinstance(result, dict) else None
        return [b for b in raw if isinstance(b, dict)] if isinstance(raw, list) else None

    async def backup_config(self) -> dict[str, Any] | None:
        """How Home Assistant makes and keeps its automatic backups (E-397), read only. Only
        the schedule and what it keeps leave here; the rest of the answer, with the password
        for the backups, is dropped at once."""
        try:
            result = await self._command({"type": "backup/config/info"})
        except (HAConnectionError, TimeoutError):
            return None
        raw = (result or {}).get("config") if isinstance(result, dict) else None
        return backup_settings(raw) if isinstance(raw, dict) else None

    async def installation(self) -> Installation | None:
        """Home Assistant OS or Supervised, from the Supervisor (E-390, B-293). Only as an App:
        connected some other way there is no Supervisor to ask."""
        if self._url != SUPERVISOR_URL:
            return None
        return await supervisor.installation(self._token)

    async def config_entries(self) -> list[HAConfigEntry]:
        """All integrations with their state (B-236), read only."""
        raw = await self._command({"type": "config_entries/get"})
        return [e for e in (parse_config_entry(r) for r in raw or [] if isinstance(r, dict)) if e]

    async def light_rhythms(self) -> list[HALightRhythm]:
        """Through the form of each group's options – the only place Home Assistant shows them,
        by default not in the states – and that form closed again at once (E-465, B-381)."""
        entries = await self._command({"type": "config_entries/get", "domain": RHYTHM_DOMAIN})
        found = []
        for raw in entries if isinstance(entries, list) else []:
            if not isinstance(raw, dict) or raw.get("disabled_by") or not raw.get("entry_id"):
                continue
            options = await self._options_of(str(raw["entry_id"]))
            found.append(
                parse_light_rhythm(str(raw["entry_id"]), str(raw.get("title") or ""), options)
            )
        return found

    async def _options_of(self, entry_id: str) -> dict[str, Any]:
        assert self._session is not None
        root = f"{self._rest_root()}/api/config/config_entries/options/flow"
        headers = {"Authorization": f"Bearer {self._token}"}
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        try:
            async with self._session.post(
                root, json={"handler": entry_id}, headers=headers, timeout=timeout
            ) as response:
                if response.status >= 300:
                    raise HAConnectionError(f"options: {response.status}")
                flow = await response.json()
            flow_id = flow.get("flow_id") if isinstance(flow, dict) else None
            if flow_id:
                async with self._session.delete(
                    f"{root}/{flow_id}", headers=headers, timeout=timeout
                ):
                    pass
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HAConnectionError(str(err)) from err
        return form_values(flow.get("data_schema") if isinstance(flow, dict) else None)

    async def users(self) -> list[HAUser]:
        """The HA users, kept for five minutes (they rarely change)."""
        now = time.monotonic()
        if self._users is not None and now - self._users[0] < USERS_TTL_S:
            return self._users[1]
        try:
            raw = await self._command({"type": "config/auth/list"})
        except (HAConnectionError, TimeoutError) as err:
            _LOGGER.warning("Could not read the Home Assistant users: %s", err)
            return self._users[1] if self._users is not None else []
        users = [
            HAUser(
                id=str(u["id"]),
                name=str(u.get("name") or ""),
                is_admin=any(g == "system-admin" for g in u.get("group_ids", []))
                or bool(u.get("is_owner")),
                is_active=bool(u.get("is_active", True)),
                system_generated=bool(u.get("system_generated", False)),
            )
            for u in raw or []
        ]
        self._users = (now, users)
        return users

    async def notify(
        self, service: str, title: str, message: str, data: dict[str, Any] | None = None
    ) -> ActionResult:
        domain, name = service.split(".", 1)
        payload: dict[str, Any] = {"title": title, "message": message}
        if data:
            payload["data"] = data
        try:
            result = await self._command(
                {"type": "call_service", "domain": domain, "service": name, "service_data": payload}
            )
        except (HAConnectionError, TimeoutError) as err:
            return ActionResult(ok=False, error=str(err))
        return ActionResult(ok=True, context_id=((result or {}).get("context") or {}).get("id"))

    async def _response(
        self, domain: str, service: str, data: dict[str, Any], entity_id: str
    ) -> Any:
        try:
            result = await self._command(
                {
                    "type": "call_service",
                    "domain": domain,
                    "service": service,
                    "service_data": data,
                    "target": {"entity_id": entity_id},
                    "return_response": True,
                }
            )
        except (HAConnectionError, TimeoutError):
            return None
        return ((result or {}).get("response") or {}).get(entity_id)

    async def daily_forecast(self, entity_id: str) -> list[dict[str, Any]]:
        found = await self._response("weather", "get_forecasts", {"type": "daily"}, entity_id)
        forecast = (found or {}).get("forecast") if isinstance(found, dict) else None
        return [f for f in forecast if isinstance(f, dict)] if isinstance(forecast, list) else []

    async def hourly_forecast(self, entity_id: str) -> list[dict[str, Any]]:
        found = await self._response("weather", "get_forecasts", {"type": "hourly"}, entity_id)
        forecast = (found or {}).get("forecast") if isinstance(found, dict) else None
        return [f for f in forecast if isinstance(f, dict)] if isinstance(forecast, list) else []

    async def calendar_events(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        data = {"start_date_time": start.isoformat(), "end_date_time": end.isoformat()}
        found = await self._response("calendar", "get_events", data, entity_id)
        events = (found or {}).get("events") if isinstance(found, dict) else None
        return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []

    async def speak(
        self, target: str, message: str, tts: str | None = None, voice: Voice | None = None
    ) -> ActionResult:
        if voice is not None and not target.startswith("assist_satellite."):
            tts = voice.engine
        data: dict[str, Any]
        if target.startswith("assist_satellite."):
            domain, service, data, entity = (
                "assist_satellite",
                "announce",
                {"message": message},
                target,
            )
        elif tts is not None:
            domain, service, entity = "tts", "speak", tts
            data = {"media_player_entity_id": target, "message": message}
            if voice is not None and voice.language:
                data["language"] = voice.language
            if voice is not None and voice.voice:
                data["options"] = {"voice": voice.voice}
        else:
            return ActionResult(ok=False, error="no text to speech")
        try:
            await self._command(
                {
                    "type": "call_service",
                    "domain": domain,
                    "service": service,
                    "service_data": data,
                    "target": {"entity_id": entity},
                },
                wait=min(self.speak_wait_s, self._timeout),
            )
        except TimeoutError:
            return ActionResult(ok=True)  # still saying it (SPEAK_WAIT_S)
        except HAConnectionError as err:
            return ActionResult(ok=False, error=str(err))
        return ActionResult(ok=True)

    async def tts_url(self, message: str, voice: Voice) -> str | None:
        query = {"message": message}
        if voice.language:
            query["language"] = voice.language
        if voice.voice:
            query["voice"] = voice.voice
        media = f"media-source://tts/{voice.engine}?{urlencode(query)}"
        try:
            result = await self._command(
                {"type": "media_source/resolve_media", "media_content_id": media}
            )
        except (HAConnectionError, TimeoutError):
            return None
        url = result.get("url") if isinstance(result, dict) else None
        return str(url) if url else None

    async def _pipeline(self) -> str | None:
        """HouseMind's own Assist pipeline, else the preferred one."""
        try:
            result = await self._command({"type": "assist_pipeline/pipeline/list"})
        except (HAConnectionError, TimeoutError):
            return None
        pipelines = [p for p in (result or {}).get("pipelines", []) if isinstance(p, dict)]
        ours = [p for p in pipelines if str(p.get("conversation_engine", "")).endswith("housemind")]
        if ours:
            return str(ours[0]["id"])
        preferred = (result or {}).get("preferred_pipeline")
        return str(preferred) if preferred else None

    async def speech_to_text(self, audio: bytes, language: str) -> str | None:
        if self._ws is None or not audio:
            return None
        pipeline = await self._pipeline()
        msg_id = next(self._ids)
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._streams[msg_id] = events
        result: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = result
        command: dict[str, Any] = {
            "id": msg_id,
            "type": "assist_pipeline/run",
            "start_stage": "stt",
            "end_stage": "stt",
            "input": {"sample_rate": 16000},
        }
        if pipeline:
            command["pipeline"] = pipeline
        text: str | None = None
        try:
            await self._ws.send_json(command)
            while True:
                event = await asyncio.wait_for(events.get(), STT_WAIT)
                kind, data = event.get("type"), event.get("data") or {}
                if kind == "run-start":
                    handler = (data.get("runner_data") or {}).get("stt_binary_handler_id")
                    if not isinstance(handler, int):
                        break
                    prefix = bytes([handler])
                    for i in range(0, len(audio), 4096):
                        await self._ws.send_bytes(prefix + audio[i : i + 4096])
                    await self._ws.send_bytes(prefix)  # the end of what was said
                elif kind == "stt-end":
                    heard = (data.get("stt_output") or {}).get("text")
                    text = str(heard).strip() if heard else None
                elif kind in ("run-end", "error"):
                    break
        except (HAConnectionError, TimeoutError, ConnectionResetError, RuntimeError, TypeError):
            return text
        finally:
            self._streams.pop(msg_id, None)
            self._pending.pop(msg_id, None)
        return text or None

    async def assist_voice(self) -> Voice | None:
        try:
            result = await self._command({"type": "assist_pipeline/pipeline/list"})
        except (HAConnectionError, TimeoutError):
            return None
        pipelines = [p for p in (result or {}).get("pipelines", []) if isinstance(p, dict)]
        preferred = (result or {}).get("preferred_pipeline")
        ours = [p for p in pipelines if str(p.get("conversation_engine", "")).endswith("housemind")]
        chosen = ours + [p for p in pipelines if p.get("id") == preferred] + pipelines
        for pipeline in chosen:
            if pipeline.get("tts_engine"):
                return Voice(
                    str(pipeline["tts_engine"]),
                    pipeline.get("tts_language"),
                    pipeline.get("tts_voice"),
                )
        return None

    async def tts_engines(self, language: str) -> list[str]:
        try:
            result = await self._command({"type": "tts/engine/list", "language": language})
        except (HAConnectionError, TimeoutError):
            return []
        return [
            str(p["engine_id"])
            for p in (result or {}).get("providers", [])
            if isinstance(p, dict) and p.get("engine_id") and p.get("supported_languages") != []
        ]

    async def tts_languages(self, engine: str) -> list[str]:
        try:
            result = await self._command({"type": "tts/engine/get", "engine_id": engine})
        except (HAConnectionError, TimeoutError):
            return []
        languages = ((result or {}).get("provider") or {}).get("supported_languages") or []
        return [str(code) for code in languages if isinstance(code, str)]

    async def tts_voices(self, engine: str, language: str) -> list[tuple[str, str]]:
        try:
            result = await self._command(
                {"type": "tts/engine/voices", "engine_id": engine, "language": language}
            )
        except (HAConnectionError, TimeoutError):
            return []
        return [
            (str(v["voice_id"]), str(v.get("name") or v["voice_id"]))
            for v in (result or {}).get("voices") or []
            if isinstance(v, dict) and v.get("voice_id")
        ]

    async def converse(self, agent_id: str, text: str, language: str) -> str | None:
        try:
            result = await self._command(
                {
                    "type": "call_service",
                    "domain": "conversation",
                    "service": "process",
                    "service_data": {"text": text, "language": language, "agent_id": agent_id},
                    "return_response": True,
                }
            )
        except (HAConnectionError, TimeoutError):
            return None
        speech = (((result or {}).get("response") or {}).get("response") or {}).get("speech") or {}
        plain = speech.get("plain") if isinstance(speech, dict) else None
        text_out = plain.get("speech") if isinstance(plain, dict) else None
        return str(text_out) if text_out else None

    async def ai_task(
        self,
        entity_id: str,
        task_name: str,
        instructions: str,
        attachments: list[dict[str, str]] | None = None,
    ) -> Any:
        data: dict[str, Any] = {
            "task_name": task_name,
            "instructions": instructions,
            "entity_id": entity_id,
        }
        if attachments:
            data["attachments"] = attachments
        try:
            result = await self._command(
                {
                    "type": "call_service",
                    "domain": "ai_task",
                    "service": "generate_data",
                    "service_data": data,
                    "return_response": True,
                },
                wait=AI_TASK_WAIT,
            )
        except (HAConnectionError, TimeoutError):
            return None
        return ((result or {}).get("response") or {}).get("data")

    async def integration_setup(self, domain: str) -> str | None:
        try:
            entries = await self._command({"type": "config_entries/get", "domain": domain})
            if isinstance(entries, list) and any(
                isinstance(e, dict) and e.get("source") != "ignore" for e in entries
            ):
                return "configured"
            flows = await self._command({"type": "config_entries/flow/progress"})
        except (HAConnectionError, TimeoutError):
            return None
        if isinstance(flows, list) and any(
            isinstance(f, dict) and f.get("handler") == domain for f in flows
        ):
            return "discovered"
        return None

    async def _ours(self, entity_id: Any) -> bool:
        """An entity of HouseMind's own integration – its Assist agent."""
        if not isinstance(entity_id, str) or not entity_id:
            return False
        try:
            entry = await self._command(
                {"type": "config/entity_registry/get", "entity_id": entity_id}
            )
        except (HAConnectionError, TimeoutError):
            return False
        return isinstance(entry, dict) and entry.get("platform") == "housemind"

    async def assist_pipelines(self) -> list[dict[str, Any]]:
        try:
            result = await self._command({"type": "assist_pipeline/pipeline/list"})
        except (HAConnectionError, TimeoutError):
            return []
        return [p for p in (result or {}).get("pipelines", []) if isinstance(p, dict)]

    async def wake_words(self, entity_id: str) -> list[tuple[str, str]]:
        try:
            result = await self._command({"type": "wake_word/info", "entity_id": entity_id})
        except (HAConnectionError, TimeoutError):
            return []
        return [
            (str(w["id"]), str(w.get("name") or w["id"]))
            for w in (result or {}).get("wake_words") or []
            if isinstance(w, dict) and w.get("id")
        ]

    async def stt_engines(self, language: str) -> list[tuple[str, list[str]]]:
        try:
            result = await self._command({"type": "stt/engine/list", "language": language})
        except (HAConnectionError, TimeoutError):
            return []
        return [
            (str(p["engine_id"]), [str(c) for c in p.get("supported_languages") or []])
            for p in (result or {}).get("providers", [])
            if isinstance(p, dict) and p.get("engine_id") and p.get("supported_languages")
        ]

    async def vesta_pipeline(self, pipeline: str | None, settings: dict[str, Any]) -> str | None:
        if not await self._ours(settings.get("conversation_engine")):
            return None
        fields = {
            "conversation_engine": settings["conversation_engine"],
            "conversation_language": "*",
            "language": settings["language"],
            "name": settings["name"],
            "stt_engine": settings.get("stt_engine"),
            "stt_language": settings.get("stt_language"),
            "tts_engine": settings.get("tts_engine"),
            "tts_language": settings.get("tts_language"),
            "tts_voice": settings.get("tts_voice"),
            "wake_word_entity": settings.get("wake_word_entity"),  # "Hey Vesta" (E-232)
            "wake_word_id": settings.get("wake_word_id"),
            # Direct commands ("turn on the light") HA handles itself, as a person's action
            # (E-167, design 7.4); only the rest reaches Vesta (QA 2, B-169).
            "prefer_local_intents": True,
        }
        try:
            if pipeline is not None:
                existing = next(
                    (p for p in await self.assist_pipelines() if p.get("id") == pipeline), None
                )
                if existing is None or not await self._ours(existing.get("conversation_engine")):
                    return None
                result = await self._command(
                    {"type": "assist_pipeline/pipeline/update", "pipeline_id": pipeline, **fields}
                )
            else:
                result = await self._command({"type": "assist_pipeline/pipeline/create", **fields})
        except (HAConnectionError, TimeoutError):
            return None
        return str(result["id"]) if isinstance(result, dict) and result.get("id") else pipeline

    async def multicast(self, on: bool, command_class: int, entity_ids: list[str]) -> ActionResult:
        value: bool | int = (255 if on else 0) if command_class == 38 else on
        for entity_id in entity_ids:
            self.tracker.expect(entity_id)
        try:
            result = await self._command(
                {
                    "type": "call_service",
                    "domain": "zwave_js",
                    "service": "multicast_set_value",
                    "service_data": {
                        "command_class": command_class,
                        "property": "targetValue",
                        "value": value,
                    },
                    "target": {"entity_id": list(entity_ids)},
                }
            )
        except (HAConnectionError, TimeoutError) as err:
            return ActionResult(ok=False, error=str(err))
        context_id = ((result or {}).get("context") or {}).get("id")
        if context_id:
            self.tracker.record_own_context(str(context_id))
        return ActionResult(ok=True, context_id=context_id)

    async def mqtt_retained(self, topic: str, wait: float = 3.0) -> Any:
        """Subscribe through HA's MQTT integration (an administrator's command, which the App's
        Supervisor user is), take the retained message HA sends right away, and stop again."""
        if self._ws is None:
            return None
        msg_id = next(self._ids)
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._streams[msg_id] = events
        result: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = result
        try:
            await self._ws.send_json({"id": msg_id, "type": "mqtt/subscribe", "topic": topic})
            await asyncio.wait_for(result, self._timeout)
            event = await asyncio.wait_for(events.get(), wait)
            payload = event.get("payload")
            return json.loads(payload) if isinstance(payload, str) else payload
        except (HAConnectionError, TimeoutError, ValueError, ConnectionResetError, RuntimeError):
            return None
        finally:
            self._streams.pop(msg_id, None)
            self._pending.pop(msg_id, None)
            with contextlib.suppress(HAConnectionError, TimeoutError):
                await self._command({"type": "unsubscribe_events", "subscription": msg_id})

    def _rest_root(self) -> str:
        """HA's REST API next to this websocket: also through the Supervisor's proxy."""
        return self._url.replace("ws", "http", 1).removesuffix("/websocket").removesuffix("/api")

    async def finish_integration(self, domain: str) -> str:
        """Through HA's own REST endpoints for setting up – the websocket has no command for
        a flow – exactly what the administrator's two clicks in HA do: the flow of this
        integration, then its confirmation (20.23). HouseMind's flow asks nothing more."""
        assert self._session is not None
        root = f"{self._rest_root()}/api/config/config_entries/flow"
        headers = {"Authorization": f"Bearer {self._token}"}
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        body: dict[str, Any] = {"handler": domain, "show_advanced_options": False}
        url = root
        try:
            for _ in range(3):  # start, confirm – never more than a step to spare
                async with self._session.post(
                    url, json=body, headers=headers, timeout=timeout
                ) as response:
                    if response.status in (400, 404) and url == root:
                        return "restart"  # "Invalid handler": HA has not loaded the files yet
                    if response.status >= 300:
                        return "failed"
                    flow = await response.json()
                kind = flow.get("type") if isinstance(flow, dict) else None
                if kind == "create_entry" or (
                    kind == "abort" and flow.get("reason") == "already_configured"
                ):
                    return "done"
                if kind != "form" or not flow.get("flow_id") or flow.get("errors"):
                    return "failed"  # e.g. "cannot_connect": HA could not reach HouseMind
                url, body = f"{root}/{flow['flow_id']}", {}
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return "failed"
        return "failed"

    async def unlink_housemind(self, pipeline: str | None = None) -> bool:
        """Only HouseMind's own entries, through HA's own REST endpoint – the websocket has no
        command for it; the device and the Assist agent go with them. First Vesta's own
        pipeline, while its agent can still be seen to be HouseMind's."""
        if pipeline is not None:
            existing = next(
                (p for p in await self.assist_pipelines() if p.get("id") == pipeline), None
            )
            if existing is not None and await self._ours(existing.get("conversation_engine")):
                try:
                    await self._command(
                        {"type": "assist_pipeline/pipeline/delete", "pipeline_id": pipeline}
                    )
                except (HAConnectionError, TimeoutError):
                    return False
        try:
            entries = await self._command({"type": "config_entries/get", "domain": "housemind"})
        except (HAConnectionError, TimeoutError):
            return False
        ids = [
            str(e["entry_id"])
            for e in (entries if isinstance(entries, list) else [])
            if isinstance(e, dict) and e.get("entry_id")
        ]
        if not ids:
            return True
        assert self._session is not None
        root = self._rest_root()
        ok = True
        for entry_id in ids:
            try:
                async with self._session.delete(
                    f"{root}/api/config/config_entries/entry/{entry_id}",
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as response:
                    ok = ok and response.status < 300
            except (aiohttp.ClientError, TimeoutError):
                ok = False
        return ok

    async def restart(self) -> ActionResult:
        if self._ws is None:
            return ActionResult(ok=False, error="not connected")
        try:
            await self._command(
                {"type": "call_service", "domain": "homeassistant", "service": "restart"}
            )
        except HAConnectionError as err:
            # HA goes down before it answers – the restart is under way.
            # An answer with an error (a configuration that does not pass) is no restart.
            if str(err) in ("connection closed", "connection lost"):
                return ActionResult(ok=True)
            return ActionResult(ok=False, error=str(err))
        except TimeoutError as err:
            return ActionResult(ok=False, error=str(err))
        return ActionResult(ok=True)

    def on_notification_action(self, listener: EventListener) -> Subscription:
        self._action_listeners.append(listener)
        return Subscription(lambda: self._action_listeners.remove(listener))


def backup_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Of Home Assistant's backup settings only what HouseMind tells (E-397): the schedule, what
    it keeps, and what each place keeps. Never ``create_backup`` (with the password)."""
    schedule = config.get("schedule")
    agents = config.get("agents")
    return {
        "automatic_backups_configured": config.get("automatic_backups_configured"),
        "schedule": {
            "recurrence": schedule.get("recurrence"),
            "days": [d for d in schedule.get("days") or [] if isinstance(d, str)],
        }
        if isinstance(schedule, dict)
        else {},
        "retention": dict(config["retention"]) if isinstance(config.get("retention"), dict) else {},
        "agents": {
            str(a): {"retention": dict(c["retention"])}
            for a, c in agents.items()
            if isinstance(c, dict) and isinstance(c.get("retention"), dict)
        }
        if isinstance(agents, dict)
        else {},
    }
