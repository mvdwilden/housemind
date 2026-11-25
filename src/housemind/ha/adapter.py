"""The HA translation layer (E-151): the only part of HouseMind that talks to Home Assistant.

Everything else uses this protocol and HouseMind types. Two implementations exist: the real
websocket client (:mod:`housemind.ha.websocket`) and the simulated house used by the demo,
the time machine and the tests (:mod:`housemind.ha.fake`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from housemind.core.model import HouseModel, Source, State, StateChange

StateListener = Callable[[StateChange], Awaitable[None]]
RegistryListener = Callable[[str], Awaitable[None]]
EventListener = Callable[[dict[str, Any]], Awaitable[None]]
ConnectionListener = Callable[[bool], Awaitable[None]]  # False: lost, True: back again


@dataclass(frozen=True, slots=True)
class HAUser:
    id: str
    name: str
    is_admin: bool
    is_active: bool = True
    system_generated: bool = False


@dataclass(frozen=True, slots=True)
class HAConfig:
    version: str
    language: str = "en"
    country: str | None = None
    time_zone: str = "UTC"
    currency: str = "EUR"
    temperature_unit: str = "°C"
    # Where the house is, to work out the height of the sun (E-268): it stays in the house.
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True, slots=True)
class Voice:
    """A voice in HA: its text-to-speech engine, language and voice (E-230)."""

    engine: str
    language: str | None = None
    voice: str | None = None


@dataclass(frozen=True, slots=True)
class ActionResult:
    ok: bool
    context_id: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class HATrigger:
    """When an automation starts, in Home Assistant's own generic terms (E-41)."""

    platform: str  # state, numeric_state, sun, time, zone, device, …
    entity_ids: tuple[str, ...] = ()
    # Wanted state, or the event (sun, zone), the type (device trigger) or the event type.
    to: str | None = None
    attribute: str | None = None
    below: float | None = None
    above: float | None = None
    at: str | None = None  # time trigger, "HH:MM[:SS]"
    for_s: float | None = None  # "for": how long it must hold first, in seconds (E-444)
    subtype: str | None = None  # device trigger: which button of a remote (E-448)


@dataclass(frozen=True, slots=True)
class HAStep:
    """One device action an automation performs, e.g. ``light.turn_on`` on the hall."""

    action: str
    entity_ids: tuple[str, ...] = ()
    area_ids: tuple[str, ...] = ()
    device_ids: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HAAutomation:
    """An existing Home Assistant automation, read only (E-03, E-41)."""

    entity_id: str
    name: str
    enabled: bool
    config_id: str | None = None
    triggers: tuple[HATrigger, ...] = ()
    steps: tuple[HAStep, ...] = ()
    other: int = 0  # what it does besides device actions: scripts, scenes, notifications …
    readable: bool = True  # False when Home Assistant did not give its configuration
    starts: tuple[str, ...] = ()  # the scenes and scripts it starts (E-258)
    refs: tuple[str, ...] = ()  # all it points to: "entity:…", "device:…", "area:…" (B-236)
    # What must be true, in the same terms as a trigger ("state", "sun", "time" …), also
    # inside and/or/not: only to say in plain words what it does (E-204, B-346).
    conditions: tuple[HATrigger, ...] = ()
    # Whether every way through it ends with its lamps out or put back (a scene), and its longest
    # wait in seconds: whether a signal is over by itself, and how long it lasts (E-461).
    ends: bool = False
    wait_s: float = 0.0


@dataclass(frozen=True, slots=True)
class HALightRhythm:
    """One group of the integration that lets light follow the day (E-465): what its settings
    say, read only. The defaults are that integration's own."""

    entry_id: str
    title: str
    lights: tuple[str, ...] = ()
    min_brightness: float = 1.0
    max_brightness: float = 100.0
    min_kelvin: float = 2000.0
    max_kelvin: float = 5500.0
    sleep_brightness: float = 1.0
    sleep_kelvin: float = 1000.0
    interval_s: float = 90.0
    transition_s: float = 45.0
    initial_transition_s: float = 1.0
    only_once: bool = False
    sunrise_time: str | None = None
    min_sunrise_time: str | None = None
    max_sunrise_time: str | None = None
    sunrise_offset_s: float = 0.0
    sunset_time: str | None = None
    min_sunset_time: str | None = None
    max_sunset_time: str | None = None
    sunset_offset_s: float = 0.0
    brightness_mode: str = "default"
    time_dark_s: float = 900.0
    time_light_s: float = 3600.0


@dataclass(frozen=True, slots=True)
class HistoryChange:
    """One past state of an entity from Home Assistant's recorder, with who caused it (from
    the logbook) – to learn from what already happened, read only (E-257)."""

    entity_id: str
    state: str
    attributes: dict[str, Any]
    at: datetime
    source: Source = Source.UNKNOWN
    cause: str | None = None  # the automation or script that did it


@dataclass(frozen=True, slots=True)
class HAScript:
    """An existing Home Assistant script, read only (E-03, E-258): what it acts on."""

    entity_id: str
    name: str
    steps: tuple[HAStep, ...] = ()
    starts: tuple[str, ...] = ()
    readable: bool = True
    refs: tuple[str, ...] = ()  # all it points to (B-236)


@dataclass(frozen=True, slots=True)
class HAConfigEntry:
    """An integration as Home Assistant set it up, read only (B-236)."""

    entry_id: str
    domain: str
    title: str
    state: str  # "loaded", "setup_retry", "not_loaded" …
    disabled: bool = False


@dataclass(frozen=True, slots=True)
class Installation:
    """How Home Assistant is installed, as the Supervisor says (E-390, B-293)."""

    kind: str  # "os" (Home Assistant OS) or "supervised"
    os: str  # the operating system, such as "Home Assistant OS 16.2" or "Debian GNU/Linux 12"
    supported: bool
    healthy: bool


@dataclass(slots=True)
class Subscription:
    """Handle to stop listening."""

    _cancel: Callable[[], None] = field(repr=False)

    def cancel(self) -> None:
        self._cancel()


class HomeAssistant(Protocol):
    """What HouseMind needs from Home Assistant – nothing more."""

    async def connect(self) -> HAConfig: ...

    async def close(self) -> None: ...

    async def ha_config(self) -> HAConfig:
        """Home Assistant's version and settings as they are now (after an update too)."""
        ...

    def on_connection(self, listener: ConnectionListener) -> Subscription:
        """Told when the connection is lost and when it is back (HA restarted or updated).
        In between, the client keeps trying again by itself, waiting a little longer each
        time (H7.4)."""
        ...

    async def load_model(self) -> HouseModel: ...

    async def get_states(self) -> dict[str, State]: ...

    async def call_action(
        self, action: str, entity_id: str, data: dict[str, Any] | None = None
    ) -> ActionResult: ...

    async def available_actions(self) -> dict[str, set[str]]: ...

    async def energy_preferences(self) -> dict[str, Any]: ...

    async def solar_forecast(self) -> dict[datetime, float]:
        """Expected solar energy per hour (Wh) from forecasts already in HA (E-72)."""
        ...

    async def automations(self) -> list[HAAutomation]:
        """The existing automations, to learn from (E-41). Reading only: switching one off
        or on is an ordinary action through the safety gate (E-42, E-110), and there is no
        way to make, change or delete one (E-03)."""
        ...

    async def history(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> list[HistoryChange]:
        """What the recorder kept for these entities in this period, oldest first, the state
        at the start included, each with who did it (E-257). Reading only."""
        ...

    async def history_start(self) -> datetime | None:
        """The oldest moment the recorder still has, if any (E-257)."""
        ...

    async def scripts(self) -> list[HAScript]:
        """The existing scripts, only to show where they act (E-258). Reading only (E-03)."""
        ...

    async def config_entries(self) -> list[HAConfigEntry]:
        """The integrations and how they are now, to see one that does not start (B-236).
        Reading only."""
        ...

    async def light_rhythms(self) -> list[HALightRhythm]:
        """The settings of each group of the integration that lets light follow the day
        (E-465). Reading only: HouseMind never changes them. Raises ConnectionError when they
        cannot be read, so what was read before stays."""
        ...

    async def set_entity_enabled(self, entity_id: str, enabled: bool) -> ActionResult:
        """Switch an entity off in the registry, or back on (B-243). Only through the safety
        gate, only after a yes, never deleting anything (E-03)."""
        ...

    async def energy_validate(self) -> dict[str, Any]:
        """What Home Assistant itself finds wrong in its energy settings (B-267). Reading
        only; empty when it could not say."""
        ...

    async def system_health(self) -> dict[str, Any]:
        """Home Assistant's system health by domain (B-241): the disk, the database and the
        versions. Reading only; empty when it could not say."""
        ...

    async def backups(self) -> list[dict[str, Any]] | None:
        """The backups Home Assistant knows, from every place (B-241). Reading only; None when
        it could not say."""
        ...

    async def backup_config(self) -> dict[str, Any] | None:
        """How Home Assistant makes and keeps its automatic backups (E-397, B-314): only the
        schedule and what it keeps, per place too – never its password. Reading only; None when
        it could not say."""
        ...

    async def installation(self) -> Installation | None:
        """Home Assistant OS or Supervised, and whether the Supervisor calls it supported and
        healthy (E-390, B-293). None when the Supervisor could not say: HouseMind never guesses."""
        ...

    def on_state_change(self, listener: StateListener) -> Subscription: ...

    def on_registry_change(self, listener: RegistryListener) -> Subscription: ...

    async def users(self) -> list[HAUser]: ...

    async def notify(
        self, service: str, title: str, message: str, data: dict[str, Any] | None = None
    ) -> ActionResult: ...

    def on_notification_action(self, listener: EventListener) -> Subscription: ...

    async def daily_forecast(self, entity_id: str) -> list[dict[str, Any]]:
        """The weather per day from a weather entity (for the morning briefing, E-107)."""
        ...

    async def hourly_forecast(self, entity_id: str) -> list[dict[str, Any]]:
        """The weather per hour from a weather entity (rain on its way, E-536); empty when
        it has none."""
        ...

    async def calendar_events(
        self, entity_id: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """Events of a calendar between two moments; they stay in the house (E-15)."""
        ...

    async def speak(
        self, target: str, message: str, tts: str | None = None, voice: Voice | None = None
    ) -> ActionResult:
        """Say something on a voice satellite (announce) or a speaker (text to speech), in
        the chosen voice when there is one."""
        ...

    async def tts_url(self, message: str, voice: Voice) -> str | None:
        """Where a browser can play this sentence in this voice (B-82): HA makes the sound."""
        ...

    async def assist_voice(self) -> Voice | None:
        """The voice of HouseMind's own Assist pipeline, else of the preferred one (E-230)."""
        ...

    async def speech_to_text(self, audio: bytes, language: str) -> str | None:
        """What someone said (B-83): 16 kHz 16-bit mono sound to HA's speech recognition, in
        HouseMind's own Assist pipeline (or the preferred one). Nothing is kept."""
        ...

    async def tts_engines(self, language: str) -> list[str]:
        """The text-to-speech engines in HA that speak this language."""
        ...

    async def tts_languages(self, engine: str) -> list[str]:
        """The languages a text-to-speech engine speaks, e.g. "nl-NL", "en-US"."""
        ...

    async def tts_voices(self, engine: str, language: str) -> list[tuple[str, str]]:
        """The voices of an engine in a language, as (id, name)."""
        ...

    async def converse(self, agent_id: str, text: str, language: str) -> str | None:
        """Ask another conversation agent in HA (only the question goes along, E-169)."""
        ...

    async def ai_task(
        self,
        entity_id: str,
        task_name: str,
        instructions: str,
        attachments: list[dict[str, str]] | None = None,
    ) -> Any:
        """Ask the AI task set up in Home Assistant (``ai_task.generate_data``, E-422): its
        answer – text, or what the AI made of it – or None when there was none.
        ``attachments``: a camera's image as a media source, only to an AI in the house
        (E-424)."""
        ...

    async def restart(self) -> ActionResult:
        """Restart Home Assistant – only after an administrator said yes (B-27)."""
        ...

    async def integration_setup(self, domain: str) -> str | None:
        """ "configured" when HA has an entry for the integration, "discovered" when HA found
        it and waits for someone to confirm it, else None."""
        ...

    async def multicast(self, on: bool, command_class: int, entity_ids: list[str]) -> ActionResult:
        """Z-Wave devices on or off together in one radio message (Z-Wave JS multicast, E-312):
        ``command_class`` 37 (binary switch) or 38 (multilevel switch, 255 = back to its last
        level)."""
        ...

    async def mqtt_retained(self, topic: str) -> Any:
        """The retained message on an MQTT topic, through HA's MQTT integration (parsed JSON),
        or None: Zigbee2MQTT's own list of groups when HA does not list members (E-312)."""
        ...

    async def finish_integration(self, domain: str) -> str:
        """Finish setting up an integration, as an administrator would in Settings → Devices
        and services (20.23): start its own flow and confirm it. "done" when HA has the
        entry, "restart" when HA does not know the integration yet (its files came after HA
        started), else "failed"."""
        ...

    async def unlink_housemind(self, pipeline: str | None = None) -> bool:
        """Take HouseMind's own integration out of HA, with its device and Assist agent (the
        factory settings, E-274), and Vesta's own Assist pipeline (E-280) – nothing else: it
        takes no name, and the pipeline only goes when its agent is HouseMind's. True when it
        is gone."""
        ...

    async def assist_pipelines(self) -> list[dict[str, Any]]:
        """HA's Assist pipelines, read only: id, name, agent, language and voice."""
        ...

    async def wake_words(self, entity_id: str) -> list[tuple[str, str]]:
        """The wake words a wake word engine in HA offers, read only: (id, name) (E-232)."""
        ...

    async def stt_engines(self, language: str) -> list[tuple[str, list[str]]]:
        """The speech-to-text engines for a language, with the forms they hear ("nl-NL")."""
        ...

    async def vesta_pipeline(self, pipeline: str | None, settings: dict[str, Any]) -> str | None:
        """Make, or bring up to date, Vesta's own Assist pipeline (E-280): only one whose agent
        (``conversation_engine``) is HouseMind's own – the layer checks that itself, also of a
        pipeline it updates. Returns its id, or None."""
        ...
