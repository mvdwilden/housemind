"""A simulated Home Assistant for the demo houses, the time machine and the tests (E-48, E-99).

It implements the same protocol as the real websocket client, applies simple, realistic
effects for each action, and lets a scenario play "people" and "wall switches".
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from housemind.core.clock import Clock
from housemind.core.model import Context, Entity, HouseModel, Source, State, StateChange
from housemind.ha.adapter import (
    ActionResult,
    ConnectionListener,
    EventListener,
    HAAutomation,
    HAConfig,
    HAConfigEntry,
    HALightRhythm,
    HAScript,
    HAStep,
    HATrigger,
    HAUser,
    HistoryChange,
    Installation,
    RegistryListener,
    StateListener,
    Subscription,
    Voice,
)
from housemind.ha.source import SourceTracker
from housemind.ha.translate import _as_list, _walk, parse_automation, parse_script, parse_trigger

OWN_USER_ID = "housemind-app-user"
PERSON_USER_ID = "demo-person"

_Effect = Callable[[State, dict[str, Any]], tuple[str, dict[str, Any]]]


# What Home Assistant reports for the colour names a stand may have (E-490), as hue and
# saturation: a lamp told a name is in colour mode "hs" from then on.
NAMED_HS: dict[str, tuple[float, float]] = {
    "orange": (38.8, 100.0),
    "red": (0.0, 100.0),
    "hotpink": (330.0, 58.8),
    "mediumpurple": (259.6, 48.9),
    "royalblue": (225.0, 71.1),
    "turquoise": (174.0, 71.4),
    "limegreen": (120.0, 75.6),
    "gold": (50.6, 100.0),
}


def _on(state: State, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    attrs = dict(state.attributes)
    if "brightness_pct" in data:
        attrs["brightness"] = round(float(data["brightness_pct"]) * 255 / 100)
    if "color_temp_kelvin" in data:
        attrs["color_temp_kelvin"] = data["color_temp_kelvin"]
        if "hs_color" in attrs:
            attrs["color_mode"] = "color_temp"
            attrs.pop("hs_color", None)
    hs = data.get("hs_color")
    if hs is None and data.get("color_name") in NAMED_HS:
        hs = NAMED_HS[str(data["color_name"])]
    if hs is not None:
        attrs["color_mode"] = "hs"
        attrs["hs_color"] = (float(hs[0]), float(hs[1]))
        attrs.pop("color_temp_kelvin", None)
    if "percentage" in data:
        attrs["percentage"] = data["percentage"]
    return "on", attrs


def _off(state: State, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    attrs = dict(state.attributes)
    attrs.pop("brightness", None)
    return "off", attrs


def _set_attr(name: str, key: str | None = None) -> _Effect:
    def effect(state: State, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        attrs = dict(state.attributes)
        attrs[name] = data.get(key or name)
        return state.state, attrs

    return effect


def _to(new_state: str, **attrs_update: Any) -> _Effect:
    def effect(state: State, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        attrs = dict(state.attributes)
        attrs.update(attrs_update)
        return new_state, attrs

    return effect


def _position(state: State, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    attrs = dict(state.attributes)
    position = int(data.get("position", 0))
    attrs["current_position"] = position
    return ("open" if position > 0 else "closed"), attrs


def _hvac(state: State, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    return str(data.get("hvac_mode", state.state)), dict(state.attributes)


def _chosen(actions: Any, trigger_id: Any) -> list[Any]:
    """The actions one run does: inside choose, the first option whose conditions name the
    trigger that fired (only "trigger" conditions are read here), otherwise its default."""
    out: list[Any] = []
    for raw in _as_list(actions):
        if not isinstance(raw, dict) or "choose" not in raw:
            out.append(raw)
            continue
        for option in _as_list(raw.get("choose")):
            conditions = _as_list(option.get("conditions")) if isinstance(option, dict) else []
            if all(
                isinstance(c, dict)
                and c.get("condition") == "trigger"
                and trigger_id in _as_list(c.get("id"))
                for c in conditions
            ):
                out += _chosen(option.get("sequence"), trigger_id)
                break
        else:
            out += _chosen(raw.get("default"), trigger_id)
    return out


def _run_effect(domain: str, service: str, current: State | None) -> _Effect | None:
    """What an automation's step does here: as HouseMind's own actions, plus toggling, a
    helper on or off, and a remote dimming a lamp (E-448)."""
    if service == "toggle":
        return _off if current is not None and current.state == "on" else _on
    if domain == "input_boolean" and service in ("turn_on", "turn_off"):
        return _on if service == "turn_on" else _off
    if domain == "light" and service in ("brightness_increase", "brightness_decrease"):
        step = 10 if service == "brightness_increase" else -10

        def dim(state: State, data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            now = float(state.attributes.get("brightness") or 0) * 100 / 255
            return _on(state, {"brightness_pct": max(1.0, min(100.0, now + step))})

        return dim
    return EFFECTS.get(f"{domain}.{service}")


EFFECTS: dict[str, _Effect] = {
    "light.turn_on": _on,
    "light.turn_off": _off,
    "switch.turn_on": _on,
    "switch.turn_off": _off,
    "fan.turn_on": _on,
    "fan.turn_off": _off,
    "fan.set_percentage": _on,
    "humidifier.turn_on": _on,
    "humidifier.turn_off": _off,
    "humidifier.set_humidity": _set_attr("humidity"),
    "cover.open_cover": _to("open", current_position=100),
    "cover.close_cover": _to("closed", current_position=0),
    "cover.stop_cover": lambda s, d: (s.state, dict(s.attributes)),
    "cover.set_cover_position": _position,
    "cover.set_cover_tilt_position": _set_attr("current_tilt_position", "tilt_position"),
    "climate.set_temperature": _set_attr("temperature"),
    "climate.set_preset_mode": _set_attr("preset_mode"),
    "climate.set_hvac_mode": _hvac,
    "water_heater.set_temperature": _set_attr("temperature"),
    "lock.lock": _to("locked"),
    "lock.unlock": _to("unlocked"),
    "lock.open": _to("open"),
    "valve.close_valve": _to("closed"),
    "valve.open_valve": _to("open"),
    "alarm_control_panel.alarm_arm_away": _to("armed_away"),
    "alarm_control_panel.alarm_arm_home": _to("armed_home"),
    "alarm_control_panel.alarm_arm_night": _to("armed_night"),
    "alarm_control_panel.alarm_disarm": _to("disarmed"),
    "media_player.turn_off": _to("off"),
    "media_player.media_pause": _to("paused"),
    "vacuum.start": _to("cleaning"),
    "vacuum.return_to_base": _to("returning"),
    "lawn_mower.start_mowing": _to("mowing"),
    "lawn_mower.dock": _to("docked"),
    "siren.turn_on": _to("on"),
    "automation.turn_on": _to("on"),
    "automation.turn_off": _to("off"),
    "select.select_option": lambda s, d: (str(d.get("option", s.state)), dict(s.attributes)),
    "number.set_value": lambda s, d: (str(d.get("value", s.state)), dict(s.attributes)),
    "button.press": lambda s, d: (s.state, dict(s.attributes)),
    "water_heater.set_operation_mode": lambda s, d: (
        str(d.get("operation_mode", s.state)),
        dict(s.attributes),
    ),
}


class FakeHomeAssistant:
    """In-memory Home Assistant with the same protocol as the real one."""

    def __init__(
        self,
        clock: Clock,
        model: HouseModel,
        states: dict[str, State],
        config: HAConfig | None = None,
        energy: dict[str, Any] | None = None,
    ) -> None:
        self._clock = clock
        self.model = model
        self.states = dict(states)
        self.config = config or HAConfig(version="2026.10.0", language="en", country="NL")
        self.energy = energy or {}
        self.solar_forecast_wh: dict[datetime, float] = {}  # hour start -> Wh (demo)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._connection_listeners: list[ConnectionListener] = []
        self.down = False  # Home Assistant restarting (see drop / come_back)
        self.call_times: list[datetime] = []
        self.failing_entities: set[str] = set()
        self.delivery_lag = timedelta(0)  # HA saw a change this long before HouseMind hears it
        self.removed_actions: set[str] = set()
        self.tracker = SourceTracker(clock)
        self.tracker.own_user_id = OWN_USER_ID
        self._ids = itertools.count(1)
        self._state_listeners: list[StateListener] = []
        self._registry_listeners: list[RegistryListener] = []
        self._action_listeners: list[EventListener] = []
        self.users_list: list[HAUser] = []
        self.notifications: list[tuple[str, str, str, dict[str, Any]]] = []
        self.notify_services: set[str] = set()
        self.forecasts: dict[str, list[dict[str, Any]]] = {}  # weather entity → days (demo)
        self.hourly: dict[str, list[dict[str, Any]]] = {}  # weather entity → hours (E-536)
        self.events: dict[str, list[dict[str, Any]]] = {}  # calendar entity → events (demo)
        self.spoken: list[tuple[str, str]] = []  # (speaker, message)
        self.sounds: list[tuple[str, Voice]] = []  # sentences made into sound for a browser
        self.voice: Voice | None = None  # the voice of an Assist pipeline, if any
        self.tts_locales: dict[str, list[str]] = {}  # engine → languages it speaks
        self.heard: list[str] = []  # what speech recognition will hear next
        self.listened: list[int] = []  # how much sound it got each time
        self.agents: dict[str, str] = {}  # conversation agent → fixed answer (demo)
        self.conversed: list[tuple[str, str]] = []  # (agent, text) – what was sent along
        # Home Assistant's own AI task (E-422): what was asked, and how it answers.
        self.ai_tasks: list[tuple[str, str, str, list[dict[str, str]]]] = []
        self.ai_task_reply: Any = lambda entity_id, task_name, instructions: None
        self.restarts = 0
        self.integrations: dict[str, str] = {}  # domain → "configured" / "discovered" (demo)
        self.unlinked = False  # HouseMind's own link taken away (E-274)
        # Integrations HA knows (their files were there when it started), and the ones it will
        # know after a restart (placed since): setting one up needs it known (20.23).
        self.known_integrations: set[str] = set()
        self.restart_loads: set[str] = set()
        self.refuse_flows = False  # the flow endpoints refuse this token
        self.restarting = False  # HA is away for a restart: commands fail
        self.mqtt: dict[str, Any] = {}  # retained MQTT messages by topic (Zigbee2MQTT's groups)
        self._following: set[str] = set()  # groups taking their lamps along right now (E-458)
        self._settling: set[str] = set()  # groups whose state only follows their lamps
        self.multicasts: list[tuple[bool, int, list[str]]] = []  # Z-Wave multicasts sent
        self.refuse_multicast = False
        self.pipelines: list[dict[str, Any]] = []  # Assist pipelines (E-280)
        self.stt: dict[str, list[str]] = {}  # speech-to-text engine → the languages it hears
        self.wake_word_ids: dict[str, list[tuple[str, str]]] = {}  # engine → (id, name)
        self.automation_configs: dict[str, dict[str, Any]] = {}
        self.script_configs: dict[str, dict[str, Any]] = {}
        self.config_entries_list: list[HAConfigEntry] = []  # integrations and their state
        self.light_rhythm_list: list[HALightRhythm] = []  # light that follows the day (E-465)
        self.energy_validation: dict[str, Any] = {}  # what HA finds in its energy settings
        self.health: dict[str, Any] = {}  # system_health/info by domain (B-241)
        # backup/info (B-241); None: Home Assistant does not say, so no message about it.
        self.backup_list: list[dict[str, Any]] | None = None
        self.backup_settings: dict[str, Any] | None = None  # backup/config/info (E-397)
        # How Home Assistant is installed, as the Supervisor says (E-390, B-293); None: no
        # Supervisor to ask, as in a demo house.
        self.install: Installation | None = None
        self.registry_writes: list[tuple[str, bool]] = []  # entities switched off or on (B-243)
        # The recorder: every change, with who did it (E-257); tests add older ones. What the
        # house started with is the state before anything was recorded.
        self.recorded: list[HistoryChange] = []
        self._baseline = {
            e: HistoryChange(e, st.state, dict(st.attributes), st.last_changed)
            for e, st in self.states.items()
        }
        self.connected = False

    # -- protocol -----------------------------------------------------------------------

    async def connect(self) -> HAConfig:
        self.connected = True
        return self.config

    async def close(self) -> None:
        self.connected = False

    async def ha_config(self) -> HAConfig:
        return self.config

    def on_connection(self, listener: ConnectionListener) -> Subscription:
        self._connection_listeners.append(listener)
        return Subscription(lambda: self._connection_listeners.remove(listener))

    async def drop(self) -> None:
        """Home Assistant goes away (a restart or an update)."""
        self.down = True
        self.connected = False
        for listener in list(self._connection_listeners):
            await listener(False)

    async def come_back(self, version: str | None = None) -> None:
        """Home Assistant is back, maybe with another version."""
        if version is not None:
            self.config = replace(self.config, version=version)
        self.down = False
        self.connected = True
        for listener in list(self._connection_listeners):
            await listener(True)

    async def load_model(self) -> HouseModel:
        return HouseModel(
            floors=dict(self.model.floors),
            areas=dict(self.model.areas),
            devices=dict(self.model.devices),
            entities=dict(self.model.entities),
            labels=dict(self.model.labels),
            categories=dict(self.model.categories),
        )

    async def get_states(self) -> dict[str, State]:
        return dict(self.states)

    async def call_action(
        self, action: str, entity_id: str, data: dict[str, Any] | None = None
    ) -> ActionResult:
        data = dict(data or {})
        if self.down:
            return ActionResult(ok=False, error="not connected")
        self.calls.append((action, entity_id, data))
        self.call_times.append(self._clock.now())
        if action in self.removed_actions or action not in EFFECTS:
            return ActionResult(ok=False, error=f"unknown action {action}")
        if entity_id in self.failing_entities:
            return ActionResult(ok=False, error="device did not respond")
        current = self.states.get(entity_id)
        if current is None:
            return ActionResult(ok=False, error=f"unknown entity {entity_id}")
        self.tracker.expect(entity_id)
        context = Context(id=f"ctx-{next(self._ids)}", user_id=OWN_USER_ID)
        self.tracker.record_own_context(context.id)
        new_state, attrs = EFFECTS[action](current, data)
        await self._emit(entity_id, new_state, attrs, context)
        return ActionResult(ok=True, context_id=context.id)

    async def available_actions(self) -> dict[str, set[str]]:
        if self.restarting:
            raise ConnectionError("not connected")  # as the websocket does while HA restarts
        result: dict[str, set[str]] = {}
        for action in EFFECTS:
            if action in self.removed_actions:
                continue
            domain, service = action.split(".", 1)
            result.setdefault(domain, set()).add(service)
        if self.notify_services:
            result["notify"] = set(self.notify_services)
        return result

    async def energy_preferences(self) -> dict[str, Any]:
        return dict(self.energy)

    async def solar_forecast(self) -> dict[datetime, float]:
        return dict(self.solar_forecast_wh)

    async def automations(self) -> list[HAAutomation]:
        return [
            parse_automation(self.states[entity_id], config)
            for entity_id, config in self.automation_configs.items()
            if entity_id in self.states
        ]

    async def history(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> list[HistoryChange]:
        wanted = set(entity_ids)
        kept = sorted((c for c in self.recorded if c.entity_id in wanted), key=lambda c: c.at)
        out: list[HistoryChange] = []
        for entity_id in sorted(wanted):  # the state at the start, like the recorder gives it
            before = [c for c in kept if c.entity_id == entity_id and c.at <= start]
            last = before[-1] if before else self._baseline.get(entity_id)
            if last is not None:
                out.append(replace(last, at=start))
        out += [c for c in kept if start < c.at < end]
        return out

    async def history_start(self) -> datetime | None:
        return min((c.at for c in self.recorded), default=None)

    def record(
        self,
        entity_id: str,
        state: str,
        at: datetime,
        attributes: dict[str, Any] | None = None,
        by: Source = Source.PHYSICAL,
        cause: str | None = None,
    ) -> None:
        """Something that happened before HouseMind came (tests): only in the recorder."""
        self.recorded.append(HistoryChange(entity_id, state, dict(attributes or {}), at, by, cause))

    async def scripts(self) -> list[HAScript]:
        return [
            parse_script(self.states[entity_id], config)
            for entity_id, config in self.script_configs.items()
            if entity_id in self.states
        ]

    async def set_entity_enabled(self, entity_id: str, enabled: bool) -> ActionResult:
        entity = self.model.entities.get(entity_id)
        if entity is None:
            return ActionResult(False, error="unknown_entity")
        self.model.entities[entity_id] = replace(entity, disabled=not enabled)
        self.registry_writes.append((entity_id, enabled))
        return ActionResult(True)

    async def energy_validate(self) -> dict[str, Any]:
        return dict(self.energy_validation)

    async def system_health(self) -> dict[str, Any]:
        return dict(self.health)

    async def backups(self) -> list[dict[str, Any]] | None:
        return None if self.backup_list is None else list(self.backup_list)

    async def backup_config(self) -> dict[str, Any] | None:
        return None if self.backup_settings is None else dict(self.backup_settings)

    async def installation(self) -> Installation | None:
        return self.install

    async def config_entries(self) -> list[HAConfigEntry]:
        return list(self.config_entries_list)

    async def light_rhythms(self) -> list[HALightRhythm]:
        return list(self.light_rhythm_list)

    async def users(self) -> list[HAUser]:
        return list(self.users_list)

    async def notify(
        self, service: str, title: str, message: str, data: dict[str, Any] | None = None
    ) -> ActionResult:
        if service.split(".", 1)[1] not in self.notify_services:
            return ActionResult(ok=False, error=f"unknown service {service}")
        self.notifications.append((service, title, message, dict(data or {})))
        return ActionResult(ok=True)

    async def daily_forecast(self, entity_id: str) -> list[dict[str, Any]]:
        return list(self.forecasts.get(entity_id, []))

    async def hourly_forecast(self, entity_id: str) -> list[dict[str, Any]]:
        return list(self.hourly.get(entity_id, []))

    async def calendar_events(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        found = []
        for event in self.events.get(entity_id, []):
            at = datetime.fromisoformat(str(event["start"]))
            if at.tzinfo is None:  # an all-day event: just a date
                at = at.replace(tzinfo=start.tzinfo)
            if start <= at < end:
                found.append(dict(event))
        return found

    async def speak(
        self, target: str, message: str, tts: str | None = None, voice: Voice | None = None
    ) -> ActionResult:
        tts = voice.engine if voice is not None else tts
        if target not in self.states or not (target.startswith("assist_satellite.") or tts):
            return ActionResult(ok=False, error="cannot speak there")
        self.spoken.append((target, message))
        return ActionResult(ok=True)

    async def tts_url(self, message: str, voice: Voice) -> str | None:
        self.sounds.append((message, voice))
        return f"/api/tts_proxy/demo-{len(self.sounds)}.mp3"

    async def assist_voice(self) -> Voice | None:
        return self.voice

    async def speech_to_text(self, audio: bytes, language: str) -> str | None:
        self.listened.append(len(audio))
        return self.heard.pop(0) if self.heard and audio else None

    async def tts_engines(self, language: str) -> list[str]:
        return sorted(e for e in self.states if e.startswith("tts."))

    async def tts_languages(self, engine: str) -> list[str]:
        return self.tts_locales.get(engine, ["de-DE", "en-US", "fr-FR", "nl-NL"])

    async def tts_voices(self, engine: str, language: str) -> list[tuple[str, str]]:
        return [("calm", "Calm"), ("bright", "Bright")] if engine.startswith("tts.") else []

    async def converse(self, agent_id: str, text: str, language: str) -> str | None:
        self.conversed.append((agent_id, text))
        return self.agents.get(agent_id)

    async def ai_task(
        self,
        entity_id: str,
        task_name: str,
        instructions: str,
        attachments: list[dict[str, str]] | None = None,
    ) -> Any:
        self.ai_tasks.append((entity_id, task_name, instructions, attachments or []))
        reply = self.ai_task_reply(entity_id, task_name, instructions)
        return None if reply is None else reply

    async def restart(self) -> ActionResult:
        self.restarts += 1
        self.known_integrations |= self.restart_loads
        return ActionResult(ok=True)

    async def multicast(self, on: bool, command_class: int, entity_ids: list[str]) -> ActionResult:
        if self.down or self.refuse_multicast:
            return ActionResult(ok=False, error="multicast failed")
        self.multicasts.append((on, command_class, list(entity_ids)))
        context = Context(id=f"ctx-{next(self._ids)}", user_id=OWN_USER_ID)
        self.tracker.record_own_context(context.id)
        for entity_id in entity_ids:
            current = self.states.get(entity_id)
            if current is None:
                continue
            self.tracker.expect(entity_id)
            action = f"{entity_id.split('.', 1)[0]}.turn_{'on' if on else 'off'}"
            new_state, attrs = EFFECTS[action](current, {})
            await self._emit(entity_id, new_state, attrs, context)
        return ActionResult(ok=True, context_id=context.id)

    async def mqtt_retained(self, topic: str) -> Any:
        return self.mqtt.get(topic)

    async def finish_integration(self, domain: str) -> str:
        if self.refuse_flows:
            return "failed"
        if domain not in self.known_integrations:
            return "restart"
        self.integrations[domain] = "configured"
        return "done"

    async def integration_setup(self, domain: str) -> str | None:
        return self.integrations.get(domain)

    def _ours(self, entity_id: Any) -> bool:
        entity = self.model.entities.get(entity_id) if isinstance(entity_id, str) else None
        return entity is not None and entity.platform == "housemind"

    async def unlink_housemind(self, pipeline: str | None = None) -> bool:
        self.pipelines = [
            p
            for p in self.pipelines
            if p["id"] != pipeline or not self._ours(p.get("conversation_engine"))
        ]
        self.integrations.pop("housemind", None)
        self.unlinked = True
        return True

    async def assist_pipelines(self) -> list[dict[str, Any]]:
        return [dict(p) for p in self.pipelines]

    async def wake_words(self, entity_id: str) -> list[tuple[str, str]]:
        return list(self.wake_word_ids.get(entity_id, []))

    async def stt_engines(self, language: str) -> list[tuple[str, list[str]]]:
        return [
            (engine, codes)
            for engine, codes in sorted(self.stt.items())
            if any(code.split("-")[0] == language for code in codes)
        ]

    async def vesta_pipeline(self, pipeline: str | None, settings: dict[str, Any]) -> str | None:
        if not self._ours(settings.get("conversation_engine")):
            return None
        if pipeline is not None:
            existing = next((p for p in self.pipelines if p["id"] == pipeline), None)
            if existing is None or not self._ours(existing.get("conversation_engine")):
                return None
            existing.update(settings)
            return pipeline
        made = {"id": f"pipeline-{len(self.pipelines) + 1}", **settings}
        self.pipelines.append(made)
        return str(made["id"])

    def on_notification_action(self, listener: EventListener) -> Subscription:
        self._action_listeners.append(listener)
        return Subscription(lambda: self._action_listeners.remove(listener))

    async def press_notification_action(self, action: str) -> None:
        """Simulate a person pressing a button on a phone notification."""
        for listener in list(self._action_listeners):
            await listener({"action": action})

    def on_state_change(self, listener: StateListener) -> Subscription:
        self._state_listeners.append(listener)
        return Subscription(lambda: self._state_listeners.remove(listener))

    def on_registry_change(self, listener: RegistryListener) -> Subscription:
        self._registry_listeners.append(listener)
        return Subscription(lambda: self._registry_listeners.remove(listener))

    # -- simulation helpers -------------------------------------------------------------

    async def simulate(
        self,
        entity_id: str,
        state: str,
        attributes: dict[str, Any] | None = None,
        by: Source = Source.PHYSICAL,
    ) -> None:
        """Change an entity as if a person, a wall switch or an automation did it."""
        current = self.states.get(entity_id)
        attrs = dict(current.attributes) if current else {}
        attrs.update(attributes or {})
        ctx_id = f"ext-{next(self._ids)}"
        if by is Source.PERSON:
            context = Context(id=ctx_id, user_id=PERSON_USER_ID)
        elif by is Source.AUTOMATION:
            context = Context(id=ctx_id, parent_id=f"auto-{ctx_id}")
        else:
            context = Context(id=ctx_id)
        await self._emit(entity_id, state, attrs, context)

    async def add_entity(self, entity: Entity, state: State) -> None:
        self.model.entities[entity.entity_id] = entity
        self.states[entity.entity_id] = state
        self._baseline[entity.entity_id] = HistoryChange(
            entity.entity_id, state.state, dict(state.attributes), state.last_changed
        )
        for listener in list(self._registry_listeners):
            await listener("entity_registry_updated")

    async def add_automation(
        self, entity_id: str, name: str, config: dict[str, Any], enabled: bool = True
    ) -> None:
        """An automation someone made in Home Assistant (HouseMind never makes one, E-03)."""
        attrs = {"friendly_name": name, "id": config.get("id")}
        state = State(entity_id, "on" if enabled else "off", attrs, self._clock.now())
        self.automation_configs[entity_id] = dict(config)
        await self.add_entity(Entity(entity_id, f"reg-{entity_id}", name), state)

    async def add_script(self, entity_id: str, name: str, config: dict[str, Any]) -> None:
        """A script someone made in Home Assistant (HouseMind never makes one, E-03)."""
        state = State(entity_id, "off", {"friendly_name": name}, self._clock.now())
        self.script_configs[entity_id] = dict(config)
        await self.add_entity(Entity(entity_id, f"reg-{entity_id}", name), state)

    async def remove_entity(self, entity_id: str) -> None:
        """Someone deleted the device in Home Assistant."""
        self.model.entities.pop(entity_id, None)
        self.states.pop(entity_id, None)
        for listener in list(self._registry_listeners):
            await listener("entity_registry_updated")

    async def rename_entity(self, old_entity_id: str, new_entity_id: str) -> None:
        """Rename keeps the registry id, like Home Assistant does (E-116)."""
        entity = self.model.entities.pop(old_entity_id)
        self.model.entities[new_entity_id] = replace(entity, entity_id=new_entity_id)
        state = self.states.pop(old_entity_id, None)
        if state is not None:
            self.states[new_entity_id] = replace(state, entity_id=new_entity_id)
        if old_entity_id in self.automation_configs:
            self.automation_configs[new_entity_id] = self.automation_configs.pop(old_entity_id)
        for listener in list(self._registry_listeners):
            await listener("entity_registry_updated")

    async def _emit(
        self, entity_id: str, new_state: str, attrs: dict[str, Any], context: Context
    ) -> None:
        old = self.states.get(entity_id)
        new = State(entity_id, new_state, attrs, self._clock.now() - self.delivery_lag, context)
        self.states[entity_id] = new
        change = StateChange(
            entity_id,
            old,
            new,
            self.tracker.classify(entity_id, context),
            self.tracker.run_of(context),
        )
        real = old is not None and old.state not in ("unavailable", "unknown")
        self.tracker.saw(context, change.source, real)
        if old is None or old.state != new.state or old.attributes != new.attributes:
            # The logbook names the automation that did it, also a button's (E-257, E-448).
            run = self.tracker.run_of(context)
            parent = context.parent_id
            cause = run or (parent.removeprefix("trigger-") if parent else None)
            source = Source.AUTOMATION if run else change.source
            self.recorded.append(
                HistoryChange(entity_id, new_state, dict(attrs), new.last_changed, source, cause)
            )
        for listener in list(self._state_listeners):
            await listener(change)
        if old is None or old.state != new.state:
            await self._run_automations(entity_id, new_state, context)
        if old is None or old.state != new.state:
            await self._groups_follow(entity_id, new_state, attrs, context)

    def _members(self, entity_id: str) -> list[str]:
        state = self.states.get(entity_id)
        for key in ("group_entities", "entity_id"):
            value = state.attributes.get(key) if state is not None else None
            if isinstance(value, list) and value:
                return [str(x) for x in value if x != entity_id]
        return []

    async def _groups_follow(
        self, entity_id: str, new_state: str, attrs: dict[str, Any], context: Context
    ) -> None:
        """As Home Assistant and the radio network do (E-458): a group switched on or off takes
        its lamps along, and a group's own state follows its lamps – on while any is on."""
        if new_state not in ("on", "off") or entity_id.split(".", 1)[0] not in ("light", "switch"):
            return
        members = [] if entity_id in self._settling else self._members(entity_id)
        if members:
            self._following.add(entity_id)
            try:
                kept = {k: v for k, v in attrs.items() if k in ("brightness", "color_temp_kelvin")}
                for member in members:
                    current = self.states.get(member)
                    if current is not None and current.state in ("on", "off"):
                        if current.state != new_state:
                            await self._emit(
                                member, new_state, {**current.attributes, **kept}, context
                            )
            finally:
                self._following.discard(entity_id)
        for group, state in list(self.states.items()):
            if group in self._following or group == entity_id or state.state not in ("on", "off"):
                continue
            lamps = self._members(group)
            if entity_id not in lamps:
                continue
            on = any((s := self.states.get(x)) is not None and s.state == "on" for x in lamps)
            if state.state != ("on" if on else "off"):
                self._settling.add(group)  # the group reports; its lamps stay as they are
                try:
                    await self._emit(group, "on" if on else "off", dict(state.attributes), context)
                finally:
                    self._settling.discard(group)

    async def _start(self, fires: Callable[[HATrigger], bool], parent: str | None) -> None:
        """Run each automation that is on and one of whose triggers fires – inside choose
        only the option that trigger's id meets, as Home Assistant does."""
        for automation_id, config in list(self.automation_configs.items()):
            state = self.states.get(automation_id)
            if state is None or state.state != "on":
                continue
            for raw in _as_list(config.get("triggers", config.get("trigger"))):
                trigger = parse_trigger(raw)
                if trigger is not None and fires(trigger):
                    steps: list[HAStep] = []
                    chosen = _chosen(config.get("actions", config.get("action")), raw.get("id"))
                    _walk(chosen, steps, [0])
                    await self._run(automation_id, steps, parent)
                    break

    async def _run_automations(self, entity_id: str, new_state: str, cause: Context) -> None:
        """Run simple automations, as Home Assistant would (demo, tests): state triggers, and
        a switch's device trigger "turned on" or "turned off" (E-448)."""
        if cause.parent_id is not None or self.tracker.run_of(cause) is not None:
            return  # no chains: one automation does not start another here
        entity = self.model.entities.get(entity_id)
        refs = {entity_id, entity.registry_id} if entity is not None else {entity_id}
        turned = {"turned_on": "on", "turned_off": "off"}
        await self._start(
            lambda t: (
                (t.platform == "state" and entity_id in t.entity_ids and t.to in (None, new_state))
                or (
                    t.platform == "device"
                    and bool(refs & set(t.entity_ids))
                    and turned.get(t.to or "") == new_state
                )
            ),
            cause.id,
        )

    async def fire_event(self, event_type: str, by: Source = Source.PERSON) -> None:
        """An event in Home Assistant – an action in the phone app, a button – and the
        automations it starts (E-448)."""
        user = PERSON_USER_ID if by is Source.PERSON else None
        context = Context(id=f"ext-{next(self._ids)}", user_id=user)
        await self._start(lambda t: t.platform == "event" and t.to == event_type, context.id)

    async def press(self, subtype: str, kind: str = "action") -> None:
        """A button of a remote (a device trigger without an entity, E-448); like Home
        Assistant, the run has no parent."""
        await self._start(
            lambda t: (
                t.platform == "device" and not t.entity_ids and (t.to, t.subtype) == (kind, subtype)
            ),
            None,
        )

    async def _run(self, automation_id: str, steps: list[HAStep], parent: str | None) -> None:
        """One run: its actions carry its own context, with what started it as parent – and
        Home Assistant says which automation started (``automation_triggered``)."""
        context = Context(id=f"run-{next(self._ids)}", parent_id=parent)
        self.tracker.automation_ran(context.id, automation_id, parent)
        for step in steps:
            domain, _, service = step.action.partition(".")
            targets = list(step.entity_ids)
            if "all" in targets:
                targets = [e for e in self.states if e.split(".", 1)[0] == domain]
            targets += [
                e.entity_id
                for e in self.model.entities.values()
                if e.domain == domain and e.device_id is not None and e.device_id in step.device_ids
            ]
            for target in dict.fromkeys(targets):
                current = self.states.get(target)
                effect = _run_effect(domain, service, current)
                if current is not None and effect is not None:
                    changed, attrs = effect(current, dict(step.data))
                    await self._emit(target, changed, attrs, context)
