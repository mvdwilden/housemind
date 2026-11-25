"""The running HouseMind: wires the translation layer, knowledge, world view, skills, learning,
proposals, memory and safety together (H2).

Every change from Home Assistant goes: world view → learning (what did people do?) →
emergency reactions → skills → safety gate → (action | proposal | observation) → memory.
A tick every 30 seconds handles time-based skills, expiring predictions and step reviews.
Existing automations are read at start and at every review: HouseMind learns from them,
keeps off their devices and asks before it switches one off (E-41, E-42).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import re
import secrets
import time as timer
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
import yaml

from housemind import __version__
from housemind.ai.jobs import AIJobs
from housemind.ai.keystore import KeyStore
from housemind.ai.lookups import HouseLookups
from housemind.ai.privacy import Labels, device_facts, is_safe_entity, names_in_rooms
from housemind.ai.providers import OnText
from housemind.ai.service import AIService, RuleChange
from housemind.brain import (
    history,
    house_settings,
    news,
    rain,
    security,
    setting_up,
    start_over,
    vacation,
)
from housemind.brain import parts as part_list
from housemind.brain.air import AirWatch, co2_by_room, ppm_text
from housemind.brain.automations import (
    Automations,
    load_hand_words,
)
from housemind.brain.automations import Status as AutomationStatus
from housemind.brain.cameras import CameraIndex
from housemind.brain.care import CareSettings, quiet_reason
from housemind.brain.conversation import Conversation, Topic
from housemind.brain.daylight import DaylightBook
from housemind.brain.days import DayNotes, calendar_kind, day_wish, day_words
from housemind.brain.explain import Explainer
from housemind.brain.filters import PARTS_BY_DOMAIN
from housemind.brain.house_knowledge import HouseKnowledge
from housemind.brain.household import EXPERIMENTAL, Household
from housemind.brain.icons import Icons
from housemind.brain.knowing import Knowing, load_settings
from housemind.brain.lampday import LampDay
from housemind.brain.learning import Learning, StepChange, Thresholds
from housemind.brain.levels import SETTING as LEVELS_SETTING
from housemind.brain.levels import LightLevels
from housemind.brain.library import STRONG, WEAK, Library, Piece, gist, good_name
from housemind.brain.maintenance import PER_DAY as MAINTENANCE_PER_DAY
from housemind.brain.maintenance import (
    BoilerWatch,
    WaterNight,
    batteries,
    filters,
    offline,
    problems,
    waste_from_events,
    waste_from_sensors,
    water_meters,
)
from housemind.brain.names import Names, PlainNames, load_lexicons, sentence_case
from housemind.brain.notify import (
    QUIET_DEFAULT,
    SAFETY_SKILLS,
    Kind,
    NotificationRouter,
    Person,
    Signer,
    in_quiet,
    kind_key,
    notice_text,
    people_by_user,
)
from housemind.brain.proposals import Answer, Proposal, Proposals
from housemind.brain.report import domain_of, due, week_key
from housemind.brain.report import lines as report_lines
from housemind.brain.rhythm import Rhythm, alarm_sensors, daily_words, next_alarm
from housemind.brain.rules import HouseRule, RuleBook, parse_rule, read_back, rule_text
from housemind.brain.scenes import (
    Scene,
    SceneSpec,
    capture,
    derive,
    from_json,
    load_start_scenes,
    mentions,
    scene_words,
    to_json,
)
from housemind.brain.skills import SkillEngine, load_skill_params, quiet_copy
from housemind.brain.speech import Speech, speakers, tts_engine
from housemind.brain.talk import PARTS as VOICE_PARTS
from housemind.brain.talk import (
    asks_why_not,
    charge_wish,
    find_target,
    intent,
    normalise,
    part_named,
)
from housemind.brain.undo import inverse
from housemind.brain.voice import (
    Moment,
    Phrases,
)
from housemind.brain.water import WaterWatch, flow_sensors, meters
from housemind.core.clock import Clock
from housemind.core.model import Entity, HouseModel
from housemind.energy.country import load_country
from housemind.energy.integrations import load_integrations
from housemind.energy.manager import CURRENCY_SIGNS, EnergyManager
from housemind.energy.meter import live, power
from housemind.energy.model import EnergySettings
from housemind.energy.prices import load_price_words
from housemind.energy.setup import energy_words, load_managers
from housemind.energy.tips import cheapest_block
from housemind.ha.adapter import (
    HAConfig,
    HALightRhythm,
    HAScript,
    HomeAssistant,
    Installation,
    Subscription,
    Voice,
)
from housemind.ha.integration import FOREVER, IntegrationLink
from housemind.i18n.catalog import Languages, load_languages
from housemind.knowledge import updates
from housemind.knowledge.area_roles import SLEEPING_ROLES
from housemind.knowledge.catalog import Catalog, Step
from housemind.knowledge.compat import Compatibility, load_compatibility
from housemind.knowledge.labels import load_label_hints
from housemind.knowledge.loader import load_catalog
from housemind.knowledge.matcher import recognise
from housemind.knowledge.roles import EntityRole, entity_role
from housemind.knowledge.themes import Schemes, load_schemes, signature
from housemind.memory import snapshots
from housemind.memory.database import Database, MigrationError
from housemind.parts.automations import AutomationsPart
from housemind.parts.daylight import DaylightPart
from housemind.parts.emergencies import EmergenciesPart
from housemind.parts.ha_changes import HAChangesPart
from housemind.parts.heat_cool import HeatCoolPart
from housemind.parts.labels import LabelsPart
from housemind.parts.proof import ProofPart
from housemind.parts.rules_follow import RulesFollowPart
from housemind.parts.testing import Play, Quarter, TestingPart, Try
from housemind.parts.thinking import LEARN_SHOW, WAKE_SKILLS, Recent, ThinkingPart
from housemind.parts.tidy import TidyPart
from housemind.parts.understanding import UnderstandingPart
from housemind.parts.vesta import VestaPart
from housemind.parts.words import WordsPart
from housemind.safety.emergency import ALARMS, Emergency, EmergencyReactions
from housemind.safety.gate import ActionRequest, Decision, GateState, Layer, Outcome, SafetyGate
from housemind.support import diagnostics
from housemind.world.calendar import DayType, day_type, time_bucket
from housemind.world.climate import Temperatures
from housemind.world.house_mode import HouseMode
from housemind.world.world import World, WorldState

_LOGGER = logging.getLogger(__name__)
# Gate reasons that are no news for Vesta (like the mind view, B-76).

LEVELS = ((1, "carefree"), (2, "informed"), (3, "involved"), (4, "expert"))


# What a device that does nothing reports: it goes to an AI only when the question is about its
# room (QA 2, M-11).
AI_IDLE_STATES = frozenset({"off", "closed", "idle", "standby", "unavailable", "unknown"})
ACTING_DOMAINS = frozenset(
    {
        "light",
        "cover",
        "climate",
        "switch",
        "fan",
        "valve",
        "lock",
        "media_player",
        "vacuum",
        "water_heater",
        "humidifier",
        "alarm_control_panel",
        "siren",
        "lawn_mower",
    }
)
# On a Home Assistant version newer than tested: careful until a day without failures (E-155).
# What an AI question gets per room: the climate as one value each, at most this many devices.
ROOM_CLIMATE = ("temperature", "humidity", "carbon_dioxide", "illuminance")
MAX_DEVICES_PER_ROOM = 20
# Longer than this only an administrator pauses HouseMind (QA 3, gat 3).
SHORT_PAUSE = 60
# The highest level for someone who is not an administrator, unless an administrator chose
# otherwise (QA 3, gat 3).
RESIDENT_TOP_LEVEL = house_settings.default("resident_top_level")
OPENINGS = {"door", "window", "opening", "garage_door"}
# HouseMind's domains and the Home Assistant domains they cover (the chips, E-12, E-83).
DOMAINS = {"lighting": ("light",), "covers": ("cover",), "climate": ("climate",)}
# A heat pump's own report of what HouseMind set may come this much later (E-318).
WAKE_LEAD = timedelta(minutes=30)
# A closed notice stays closed while its cause stays, at most this long (E-383, B-282).
CLOSED_FOR = timedelta(days=7)
AI_MEMORY_MAX = 10  # notices, house rules and choices an AI gets at most, each (E-384)
# The notices of a part of its own, which close when it goes off (E-400).
PART_TOPICS = {
    "energy": ("energy:rest:", "base_load:"),
    "water": ("water_running", "water_valve_closed"),
    "household": ("house:done:",),
    "care": ("care:",),
}


def _speech_engines(folders: list[Path], name: str = "speech_engines.yaml") -> dict[str, list[str]]:
    """Which words in a speech engine's id mean in the house or in the cloud (20.19); the same
    for an AI task of Home Assistant (``ai_tasks.yaml``, E-422)."""
    found: dict[str, list[str]] = {"local": [], "cloud": []}
    for folder in folders:
        path = folder / name
        if not path.is_file():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for place in found:
            found[place] += [str(w).lower() for w in raw.get(place, []) or []]
    return found


@dataclass(slots=True)
class Paths:
    knowledge: list[Path]  # shipped → update → user (H5.4)
    data: Path
    ha_config: Path | None = None  # HA's /config, mapped into the App (for the integration)
    supervisor_token: str | None = None  # only to tell HA where the App is (discovery)
    # The App's own folder (/data): what belongs to the App and not to one house's memory –
    # the demo house and the real house share it.
    app_data: Path | None = None


@dataclass(slots=True)
class Status:
    orb: str = "calm"
    emergency: Emergency | None = None
    emergency_sensor: str | None = None
    decisions: list[Decision] = field(default_factory=list)
    step_changes: list[StepChange] = field(default_factory=list)
    thoughts: list[Decision] = field(default_factory=list)  # everything considered (level 4)
    reactions: list[float] = field(default_factory=list)  # trigger → done, ms, last 50 (L8)
    ha_lost: datetime | None = None  # since when the connection to Home Assistant is gone
    outside_alarms: set[str] = field(default_factory=set)  # sensors outside hearing an alarm
    last_tick: datetime | None = None  # the last round of thinking, and how long it took
    tick_ms: float = 0.0
    thinking_until: datetime | None = None  # the Orb shows "thinking" until then
    answering: bool = False  # that thinking is an answer to someone (shows also at night)
    learning_until: datetime | None = None  # the Orb shows "learning" until then


Listener = Callable[[], Awaitable[None]]
ActionListener = Callable[[int, Decision], Awaitable[None]]
VestaOut = Callable[[str, str, str | None, str], Awaitable[None]]  # room, text, sound, name
STEP_NAMES = {s.name.lower(): s for s in Step}


class HouseMind(
    TestingPart,
    DaylightPart,
    ThinkingPart,
    EmergenciesPart,
    HAChangesPart,
    AutomationsPart,
    ProofPart,
    RulesFollowPart,
    HeatCoolPart,
    VestaPart,
    LabelsPart,
    TidyPart,
    UnderstandingPart,
    WordsPart,
    IntegrationLink,
):
    # A new installation starts "Nog niet actief" (E-275); the tests of everything else start a
    # house that is already on (tests/conftest.py).
    FRESH_INACTIVE = True

    def __init__(
        self, clock: Clock, ha: HomeAssistant, paths: Paths, pet_areas: set[str] | None = None
    ) -> None:
        self.clock = clock
        self.ha = ha
        self.paths = paths
        self.pet_areas = set(pet_areas or ())
        self._default_pets = set(self.pet_areas)  # until an administrator chooses
        self.catalog = Catalog()
        self.label_hints = load_label_hints(paths.knowledge)  # HA's labels as a hint (E-345)
        self.hand_words = load_hand_words(paths.knowledge)  # a button is a person (E-448)
        self.languages = Languages()
        self.icons = Icons(self.languages)  # one icon per device and room (E-509)
        self.vesta_phrases = Phrases({})  # Vesta's own words (E-222)
        self._vesta_name: str | None = None  # another name the administrator gave her
        self._vesta_last: dict[tuple[str | None, str], int] = {}
        self._vesta_rng = random.Random()  # noqa: S311 – only to vary her words
        self._vesta_ai_said: dict[str, tuple[datetime, str]] = {}  # the AI's words, by message
        self._habit_words: dict[str, Any] = {}  # the AI's stories per room and language (E-415)
        self._unusual_busy: set[str] = set()  # questions the AI is wording now (E-416)
        self._rule_suggest: dict[str, Any] = {}  # house rules the AI suggested (E-417)
        self._asked: dict[tuple[str | None, str], datetime] = {}  # questions Vesta asked (E-252)
        self.model = HouseModel()
        self.names = Names(self.model, {})  # plain names for what Vesta says (E-248)
        self.knowing = Knowing(self)  # what she knows herself (E-243 – E-245)
        self.library = Library()  # the Help and the manual, for her answers (E-246, B-94)
        self.news: dict[str, dict[str, dict[str, Any]]] = {}  # what is new, per level (E-150)
        self.settings_catalog: dict[str, Any] = {}  # where each setting is (E-244)
        self.schemes = Schemes()  # the colour schemes that passed the check (E-266, E-267)
        self._schemes_seen: tuple[tuple[str, int, int], ...] = ()
        self._schemes_checked = timer.monotonic()
        self.config: HAConfig | None = None
        self.db = Database(paths.data / "housemind.db")
        self._boilers: BoilerWatch | None = None  # since when each boiler has not heated (E-526)
        self._air: AirWatch | None = None  # stale air per room, and last night's (E-532)
        self._water_night = WaterNight()  # water that runs away in the night (E-535)
        self._rain_told: datetime | None = None  # rain and something open, last told (E-536)
        self._hourly: tuple[datetime, list[dict[str, Any]]] | None = None
        self.gate: SafetyGate | None = None
        self.emergencies: EmergencyReactions | None = None
        # Every emergency sensor that is on now: the house is back to normal only when the
        # last one is off, not when the first clears. Unavailable since when.
        self._active: dict[str, Emergency] = {}
        self._lost_since: dict[str, datetime] = {}
        self.world: World | None = None
        self.skills: SkillEngine | None = None
        self.learning: Learning | None = None
        self.proposals = Proposals(self.db)
        self.status = Status()
        self._reactions_unsaved = False  # reaction times to keep at the next round
        self.area_roles: dict[str, str] = {}
        self.temperatures = Temperatures()  # which sensor measures a room, which a device
        self.openings: set[str] = set()  # door and window contacts of the house
        self.explainer: Explainer | None = None
        self.vesta_explainer: Explainer | None = None  # the same, in plain names (E-248)
        self.rules = RuleBook(self.model)
        self.router: NotificationRouter | None = None
        self.energy: EnergyManager | None = None
        self.ai = AIService(self.db, KeyStore(paths.data / "secrets"), clock)
        self.ai.ha = ha  # its own AI task, when an administrator chooses it (E-422)
        self.ai.place = self.ai_task_place
        self._ai_task_words: dict[str, list[str]] = {"local": [], "cloud": []}
        # AI work in a queue of its own, beside the core: the core never waits for it (E-414).
        self.ai_jobs = AIJobs()
        # What HouseMind knows of the house (E-205): the findings in memory, the switch roles
        # they gave the gate, and which questions to read the house wait for a next try.
        self.knowledge = HouseKnowledge(self.db)
        self._knowledge_roles: dict[str, str] = {}
        self._house_unread: set[str] = set()
        # Where what a decision rests on came from, for Waarom? (E-414), and deriving from
        # behaviour beside the core, without AI too (E-203).
        self._knowledge_said: dict[str, str] = {}
        self._deriving: asyncio.Task[None] | None = None
        self._people: list[Person] = []
        # Open notification topics (one topic = one message), with their kind and whom they
        # concern: an open important message for the house makes the Orb say "attention".
        self._raised: dict[str, tuple[Kind, str, datetime]] = {}
        # Notices someone closed (Klaar, Ik kijk ernaar): not again while the cause stays
        # (E-383, B-282), kept over a restart; topic → when.
        self._closed: dict[str, str] = {}
        self.cameras: CameraIndex | None = None  # who reports which detection (16.1)
        self.security = security.Watch()  # cameras and the alarm (16.2 – 16.6)
        self._orb_timer: asyncio.TimerHandle | None = None
        self.last_not_done: dict[str, Decision] = {}
        self._listeners: list[Listener] = []
        self._action_listeners: list[ActionListener] = []
        # What HouseMind knows changed (code QA 3, punt 11): something decided, learned, set or
        # answered. "Klaar voor autonomie?" is only made again after that, or after a round;
        # every screen of the same language and level shares it.
        self.knows = 0
        self.readiness_made: dict[tuple[str, int, bool], tuple[tuple[Any, ...], Any]] = {}
        # Vesta on wall tablets (E-223): who listens per room, and what the screens see.
        self._vesta_out: list[VestaOut] = []
        self._vesta_people: list[VestaOut] = []  # (person, text, sound, name)
        self.tablet_areas: Callable[[], set[str]] = set
        self._arrivals: dict[str, tuple[datetime, str | None]] = {}  # person → since, user
        self._greeted: dict[str, datetime] = {}  # room → last greeting there
        self.recent: dict[int, Recent] = {}
        self.automations = Automations(self.db)
        self._kept_off: set[str] = set()  # devices of automations that are on (E-42)
        # Automations an administrator wants kept off after a step back (E-317).
        self._keep_off_chosen: set[str] = set()
        self._proof_answers: dict[str, dict[str, str]] = {}  # E-460: "not yet" and "never"
        self._proof_offers: dict[str, list[str]] = {}  # the question → the automations in it
        self._proof_memo: dict[tuple[str, str], bool] | None = None
        self._lamp_rules: dict[str, list[tuple[str, str]]] = {}  # E-461: found once per sync
        self.learning_allowed = False  # B-20: only after a "yes"
        # Starting again (E-271 – E-275): "Nog niet actief" until an administrator finished the
        # introduction; the factory settings ask the App for a fresh start.
        self.activated = True
        self.restart_requested = asyncio.Event()
        self.reset_requested = False
        self._subscriptions: list[Subscription] = []
        self._voices: dict[tuple[Voice, str], Voice] = {}  # her voice per language (B-124)
        self._house_language: str | None = None  # chosen when setting up (E-281)
        self._measurements_changed = False
        self._sensor_thought: dict[str, datetime] = {}
        # Looking again when a walk-through room's wait runs out (E-443).
        self._recheck_task: asyncio.Task[Any] | None = None
        self._recheck_at: datetime | None = None
        self.daylight = DaylightBook(self.model)  # the day's rhythm of the light (E-465)
        self.light_levels = LightLevels(self.model)  # standen voor lampen (E-490, design 73)
        self.lamp_day = LampDay()  # vandaag met deze lamp (E-500)
        self._daylight_plain: set[str] = set()
        self._daylight_task: asyncio.Task[Any] | None = None
        self._daylight_saving: set[asyncio.Task[Any]] = set()
        self._daylight_own_kept: dict[str, tuple[datetime, HALightRhythm]] = {}
        # Energy per step (E-343): which block already counted for learning and was asked about.
        self._energy_blocks: dict[str, str] = {}
        self._energy_asked: dict[str, str] = {}
        # The pauses the screens last saw: one that ends shows at once (E-83).
        self._pauses_seen: tuple[Any, ...] = (None, ())
        self.skill_params: dict[str, dict[str, Any]] = {}
        self.scripts: list[HAScript] = []  # Home Assistant's scripts, read only (E-03, E-258)
        self._tidy_setup()  # Home Assistant tidy (E-351, B-235)
        # Learning from the history in Home Assistant (E-257): the reading, and what it said.
        self.history_status = history.HistoryStatus()
        self.history_automations: dict[str, history.AutomationHistory] = {}
        self._history_task: asyncio.Task[None] | None = None
        self._z2m_members: dict[str, list[str]] = {}  # Zigbee2MQTT groups HA does not list (E-312)
        # The integration's last call with the right key, and with a wrong or old one (20.23).
        self.bridge_seen: datetime | None = None
        self.bridge_refused: datetime | None = None
        self._announced_at: datetime | None = None
        # Finishing the link in HA itself (20.23): when it was last tried, how it went, and
        # since when the administrator's "Koppelen" is under way.
        self._finish_tried: datetime | None = None
        self._finish_result: str | None = None
        self.linking_since: datetime | None = None
        self._history_seen: set[str] = set()
        self._last_request: dict[str, tuple[str, str, datetime]] = {}
        self._last_review: datetime | None = None
        self._last_cleanup_day: str | None = None
        self._model_dirty: datetime | None = None
        self.compatibility = Compatibility()
        self.self_check: dict[str, Any] = {}
        self.installation: Installation | None = None  # as the Supervisor says (E-390)
        self._install_read: datetime | None = None
        self._ticker: asyncio.Task[None] | None = None
        self._side: set[asyncio.Task[Any]] = set()  # work beside the queue of changes (M-31)
        self._tries: dict[int, Try] = {}  # a device tried in Test het huis (E-476)
        self._plays: dict[int, Play] = {}  # Doe het echt in Test het huis (E-518)
        self._stand_tries: dict[str, int] = {}  # Proef of a stand on a lamp (E-491)
        self._quarters: dict[int, Quarter] = {}  # the testkwartier (E-477)
        self._carefree_only = False
        self._phoneless = False  # residents without a phone of their own (E-293)
        self._corrected: set[int] = set()  # decisions a correction was counted for (B-179)
        self._watched: dict[tuple[str, str, str], datetime] = {}  # only watched, when (B-181)
        self.rhythm = Rhythm()
        self._alarm_sensors: list[str] = []
        self._last_mode: HouseMode | None = None
        self._occupied_before: set[str] = set()
        self.days = DayNotes()
        self._day_words: dict[str, list[str]] = {}
        self.speech = Speech(self.db, ha)
        self.scene_specs: dict[str, SceneSpec] = {}
        self._own_scenes: list[Scene] = []
        self._scene_uses: dict[str, int] = {}
        self._scene_words: dict[str, list[str]] = {}
        self._assist_pending: dict[str, tuple[datetime, dict[str, Any], str]] = {}
        self._assist_talks: dict[str, Conversation] = {}  # her question per Assist talk (E-349)
        self.water = WaterWatch()
        self.household: Household | None = None
        self.experimental: set[str] = set()  # experimental domains an administrator turned on
        # Parts on and off (E-399 – E-401): per part its state, and since when it is off.
        self.parts: dict[str, str] = {}
        self.parts_off: dict[str, dict[str, str]] = {}  # part -> {"since", "was"}
        self._last_snapshot_day: str | None = None  # what was learned, kept at 04:00 (20.13)
        self._kept_mode: dict[str, Any] | None = None  # vacation or guests, kept (E-286)
        self._kept_lamps: dict[str, str] = {}  # when bedroom lamps went on, kept (E-88)
        # A heat pump that heats and cools (E-318): the wish a person set per device, the preset
        # it had before HouseMind chose "away", and what HouseMind itself sent last (its own
        # echo, through a cloud integration, is no person's wish).
        self._heat_cool: dict[str, dict[str, Any]] = {"wish": {}, "away": {}}
        self._heat_cool_dirty = False
        self._hc_sent: dict[str, tuple[str, Any, datetime]] = {}
        self._calendar_ok = False  # the calendar is read only after an administrator's yes
        self._thumbs_asked: dict[str, str] = {}  # "Was dit goed?": once a day per person
        # The safe mode (E-301): the data could not be converted, or is from a newer version.
        self.safe_mode: str | None = None
        # Which speech engines work in the house and which in the cloud (20.19), and where
        # Vesta's voice goes now: checked now and then, shown in the privacy statement.
        self.speech_engines: dict[str, list[str]] = {}
        self.speech_out: dict[str, Any] | None = None
        self._speech_checked: datetime | None = None
        # Looking lived-in on vacation (E-285): on unless an administrator turned it off.
        self._lived_in_on = True
        self._lived_in_day: date | None = None
        self._lived_in_plan: list[vacation.Step] = []
        self._lived_in_lit: set[str] = set()  # lamps the plan has on now: skills keep off
        # Steps an administrator set themselves (20.10): key → (step, who set it).
        self.admin_steps: dict[str, tuple[Step, str]] = {}
        self._done_notices: dict[str, datetime] = {}
        self._started_at = clock.now()
        self._woke: datetime | None = None  # when the house woke up this morning (local)
        self._briefing_data: tuple[datetime, list[dict[str, Any]], list[dict[str, Any]]] | None = (
            None
        )

    # -- lifecycle ----------------------------------------------------------------------

    async def start(self, autorun: bool = True) -> None:
        if diagnostics.RECENT not in logging.getLogger().handlers:
            logging.getLogger().addHandler(diagnostics.RECENT)
        self.catalog, report = load_catalog([p / "catalog" for p in self.paths.knowledge])
        for warning in report.warnings:
            _LOGGER.warning("Knowledge: %s", warning)
        self.languages = load_languages([p / "languages" for p in self.paths.knowledge])
        self.icons = Icons(self.languages)
        self.vesta_phrases = Phrases.load(self.paths.knowledge)
        self.load_schemes()
        try:
            await self.db.open()
        except MigrationError as err:
            await self._safe_mode(err)
        self._vesta_name = good_name(await self.setting("vesta_name"))
        # One name, also in Assist (E-265): a name the administrator once gave the brain for
        # Assist (E-170) becomes her name, once, if she has none of her own yet.
        old = await self.db.get_setting("brain_name")
        if isinstance(old, str) and old.strip() and old.strip() != "HouseMind":
            if not self._vesta_name and (name := good_name(old.strip()[:24])) is not None:
                self._vesta_name = name
                await self.db.set_setting("vesta_name", name)
            await self.db.set_setting("brain_name", None)
        self.library = Library.build(self.languages, self.paths.knowledge, self.vesta_name("nl"))
        self.news = news.load(self.paths.knowledge)
        self.config = await self.ha.connect()
        self.model = await self.ha.load_model()
        self.names = Names(self.model, load_lexicons(self.paths.knowledge))
        self.settings_catalog = load_settings(list(self.paths.knowledge))
        await self._load_area_roles()
        self.gate = SafetyGate(self.clock, self.ha, self.catalog, self.model, GateState())
        self.emergencies = EmergencyReactions(self.clock, self.catalog, self.model, self.area_roles)
        self.emergencies.not_leak = daily_words(self.languages, "not_leak")
        self.emergencies.fridge = daily_words(self.languages, "fridge")
        self.world = World(
            self.model, self.config.time_zone, self.config.country, self.area_roles, self.pet_areas
        )
        self.world.location = self.place()
        self.world.status_words = tuple(daily_words(self.languages, "status_light"))
        self.world.load_states(await self.ha.get_states())
        await self._read_z2m_groups()
        self._index_groups()
        country = load_country([p / "countries" for p in self.paths.knowledge], self.config.country)
        self.energy = EnergyManager(
            self.db, self.ha, self.clock, country, self.config.time_zone, self.config.country
        )
        await self.energy.start(
            self.model,
            self.world.states,
            energy_words(self.languages),
            load_managers([p / "energy" for p in self.paths.knowledge]),
            load_price_words([p / "energy" for p in self.paths.knowledge]),
            load_integrations([p / "energy" for p in self.paths.knowledge]),
        )
        self._energy_roles()
        installed = await self.db.get_setting("installed_at")
        if installed is None:
            installed = self.clock.now().isoformat()
            await self.db.set_setting("installed_at", installed)
        activated = await self.db.get_setting("activated")
        if activated is None:  # a new installation, or after the factory settings (E-275)
            activated = not self.FRESH_INACTIVE
            await self.db.set_setting("activated", activated)
        self.activated = bool(activated) and self.safe_mode is None  # E-301: only emergencies
        if await self.db.get_setting("setup") is None:  # a new installation waits (E-277)
            await self.db.set_setting("setup", "pending" if self.FRESH_INACTIVE else "done")
        language = await self.db.get_setting("house_language")
        self._house_language = language if isinstance(language, str) else None
        # Learning again starts the start knowledge over, as after an install (E-273).
        installed = max(installed, await self.db.get_setting("relearned_at") or installed)
        params = load_skill_params([p / "skills" for p in self.paths.knowledge])
        self.skill_params = params
        self.learning = Learning(
            self.db,
            datetime.fromisoformat(installed),
            set(params),
            answers_only={s for s, p in params.items() if p.get("learn_from") == "answers"},
            parts={s: str(p["domain"]) for s, p in params.items() if p.get("domain")},
        )
        await self.learning.load()
        self.learning.someone_there = self._someone_there
        await self._load_learning_settings()
        self.skills = SkillEngine(
            self.catalog, self.model, self.area_roles, params, self.gate.state.roles, self.learning
        )
        # Standen voor lampen (E-490 – E-496): one book for the skills, the day's rhythm,
        # learning and the emergency reactions.
        self.light_levels.model = self.model
        self.light_levels.load(await self.db.get_setting(LEVELS_SETTING))
        self.skills.stands = self.light_levels
        self.daylight.stands = self.light_levels
        self.learning.felt = self.light_levels.felt
        self.emergencies.stands = self.light_levels
        self.rules = RuleBook(self.model)
        await self.rules.load(self.db)
        if self.energy is not None:
            self.skills.heat_offset = self.energy.heat_offset
        self.rhythm = Rhythm.from_json(await self.db.get_setting("rhythm"))
        # The part of the day from the house's own rhythm (E-455); read at each look, so a
        # rhythm learned again counts at once.
        self.world.part_of = lambda *now: self.rhythm.part(*now)
        # When the house usually wakes up: before it, the dog does not wake it (E-529).
        self.world.wake_at = lambda day, kind: (
            self.rhythm.usual("wake", kind) if kind is not DayType.DEVIANT else None
        )
        self.world.bed_at = lambda local, today, tomorrow: self.rhythm.bed_tonight(
            local, today, tomorrow
        )
        self._alarm_sensors = alarm_sensors(self.model, daily_words(self.languages, "alarm"))
        self._sort_temperatures()
        self._sort_openings()
        self.world.prime(self.clock.now())
        self._opener_roles()
        await security.start(self)
        self.skills.wake_at = self._wake_at
        self.skills.bed_at = self._bed_at
        self.skills.camera_lit = lambda entity_id: entity_id in self.security.lit
        self.skills.in_signal = self.gate.state.signalling
        # The words that make an outside place the entrance, in every language (E-447).
        self.skills.entrance_words = tuple(daily_words(self.languages, "entrance"))
        await self._load_heat_cool()
        self._day_words = day_words(self.languages)
        await self._load_days()
        self.speech.devices = speakers(self.model)
        self.speech.tts = tts_engine(self.model, self.world.states)
        self.speech.voice = await self.voice_for(None, await self.vesta_voice())
        await self._update_integration()
        await self._announce_again()
        configured = self.energy.setup.water if self.energy is not None else []
        found = meters(self.model, self.world.states, configured)
        self.water = WaterWatch(found, flow_sensors(self.model, found))
        self.water.load(await self.db.get_setting("water"))
        words = ("cooking", "fridge", "appliance", "tv", "power_switch", "not_power_switch")
        self.household = Household(
            self.model, {k: daily_words(self.languages, k) for k in words}, self.skill_params
        )
        self._cooking_roles()
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        now = self.clock.now()
        self.water.update(self.world.states, now, now.astimezone(tz).date().isoformat())
        self.scene_specs = load_start_scenes([p / "scenes" for p in self.paths.knowledge])
        self.speech_engines = _speech_engines(self.paths.knowledge)  # local or cloud (20.19)
        self._ai_task_words = _speech_engines(self.paths.knowledge, "ai_tasks.yaml")  # E-422
        self._scene_words = scene_words(self.languages, self.scene_specs)
        await self._load_scenes()
        await self._load_habit_words()
        self.gate.state.skill_step = self._skill_step
        self.gate.state.device_step = self._skill_step  # with a device's own step (20.10)
        self.gate.state.inactive = not self.activated
        self.gate.state.house_rule_veto = self._rule_veto
        self.gate.state.part_off = self.part_off  # a part that is off (E-400)
        self.gate.state.part_of = self._part_of  # a testkwartier is per part (E-477)
        self.gate.state.conditions = self._start_conditions
        await self._load_house_settings()
        await self._apply_label_roles()  # after the settings: a label never undoes 'met rust'
        await self._load_knowledge()  # after every other role: what AI or a person said (E-205)
        await self._restore_house_mode()  # vacation or guests, kept over a restart (E-286)
        await self._restore_lamps()  # when bedroom lamps went on, kept over a restart (E-88)
        known = self._knowledge_said  # what HouseMind knows of the house, confirmed (E-414)
        self.explainer = Explainer(self.languages, self.model, self.learning, known=known)
        self.explainer.rooms = self.names  # "de eetkamer" in a question about a stand (E-499)
        self.vesta_explainer = Explainer(
            self.languages, self.model, self.learning, self.names, known=known
        )
        await history.load(self)
        await self.automations.load()
        await self._sync_automations()
        await self._daylight_start(self.clock.now())
        await self._test_start()  # a test still open before a restart goes back (B-394)
        secret = await self.db.get_setting("signing_secret")
        if not secret:
            secret = secrets.token_hex(32)
            await self.db.set_setting("signing_secret", secret)
        self.router = NotificationRouter(self.db, self.ha, self.languages, Signer(str(secret)))
        await self.router.load()  # what was sent and ignored survives a restart (H20)
        self.router.where = self._people_in  # a question about a room: who is there (E-287)
        self.router.name_of = self.vesta_name  # she sends the messages: her name (20.21)
        self._raised = {
            str(r["topic"]): (
                Kind(str(r["kind"])),
                str(r["concerns"]),
                datetime.fromisoformat(str(r["at"])),
            )
            for r in await self.db.fetchall(
                "SELECT topic, kind, concerns, at FROM notifications WHERE resolved_at IS NULL"
            )
        }
        closed = await self.db.get_setting("notice_closed")
        self._closed = (
            {str(k): str(v) for k, v in closed.items()} if isinstance(closed, dict) else {}
        )
        self._subscriptions.append(self.ha.on_notification_action(self._on_phone_action))
        await self.ai.load()
        self.ai_jobs.start()
        await self._refresh_labels()
        self.derive_soon()  # first what behaviour says, without AI (E-201, E-203)
        self.read_house_soon()  # shortly after the start, beside the core (E-208, E-414)
        paused = await self.db.get_setting("paused_until")
        if paused:
            self.gate.state.paused_until = datetime.fromisoformat(paused)
        for domain, until in (await self.db.get_setting("paused_domains", {}) or {}).items():
            self.gate.state.paused_domains[domain] = datetime.fromisoformat(until)
        # The meter on Expert keeps its last 50 over a restart (E-322, L8): it counts only what
        # HouseMind does itself, which can take a day.
        measured = await self.db.get_setting("reactions", []) or []
        self.status.reactions = [float(ms) for ms in measured]
        self._subscriptions += [
            self.ha.on_state_change(self._on_change),
            self.ha.on_state_change(self._tidy_count),  # what changes most often (B-241)
            self.ha.on_connection(self._on_connection),
            self.ha.on_registry_change(self._on_registry),
        ]
        await self._caught_up()
        await self._refresh_people()
        self.compatibility = load_compatibility(self.paths.knowledge)
        await self._self_check()
        await self._emergencies_now()
        self._refresh_orb()
        if self.learning_allowed and self.history_status.read_at is None:
            self.start_history()  # learning was allowed before the history could be read
        await start_over.maybe_activate(self)
        if self.safe_mode is not None:
            await self._tell_safe_mode()
        if autorun:
            self._ticker = asyncio.create_task(self._tick_loop(), name="housemind-tick")

    # -- learning from the history (E-257) ---------------------------------------------------

    def place(self) -> tuple[float, float] | None:
        """Where the house is, for the height of the sun (E-268) – it stays in the house."""
        config = self.config
        if config is None or config.latitude is None or config.longitude is None:
            return None
        return (config.latitude, config.longitude)

    def start_history(self) -> bool:
        """Read the history in the background, once at a time; the house goes on."""
        if self._history_task is not None and not self._history_task.done():
            return False
        self._history_task = asyncio.create_task(self._history_job(), name="housemind-history")
        return True

    async def _history_job(self) -> None:
        try:
            await history.run(self)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Could not learn from the history in Home Assistant")
            self.history_status.state = "failed"
        await self._notify()
        await start_over.maybe_activate(self)  # after the introduction, the reading ends it

    async def history_told(self, facts: dict[str, Any]) -> None:
        """Vesta's short summary of what the history taught her, at Meldingen (E-257)."""
        assert self.world is not None
        await self._resolve("history_read")
        await self._raise(
            Kind.INFO,
            "history_read",
            "notify.history_read",
            facts,
            self.world.snapshot(self.clock.now()),
        )

    async def stop(self) -> None:
        if self._reactions_unsaved and self.db is not None:
            try:
                await self.db.set_setting("reactions", self.status.reactions)
            except Exception:  # a database already closed keeps what it had
                _LOGGER.debug("Reaction times not kept at stop", exc_info=True)
        # Everything HouseMind set itself on the energy devices goes back first (E-329).
        try:
            await self.give_back_energy("stopping")
        except Exception:
            _LOGGER.exception("Could not give back the energy devices")
        for task in list(self._side):
            task.cancel()  # a greeting halfway when HouseMind stops is not said
        await asyncio.gather(*self._side, return_exceptions=True)
        for subscription in self._subscriptions:
            subscription.cancel()  # a fresh HouseMind may listen to the same house (E-274)
        self._subscriptions.clear()
        if self._orb_timer is not None:
            self._orb_timer.cancel()
        if self._history_task is not None and not self._history_task.done():
            self._history_task.cancel()  # it starts over after the next start
            with contextlib.suppress(asyncio.CancelledError):
                await self._history_task
        if self._ticker is not None:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
        if self._recheck_task is not None:
            self._recheck_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recheck_task
        await self._daylight_stop()
        if self.learning is not None:
            await self.learning.save()
        await self.db.set_setting("rhythm", self.rhythm.to_json())
        await self.ai_jobs.stop()  # never waits for an AI that does not answer (E-414)
        await self.ai.close()
        await self.ha.close()
        await self.db.close()

    def on_update(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def on_vesta_person(self, listener: VestaOut) -> None:
        """Called when Vesta says something by herself to one person, for their open app."""
        self._vesta_people.append(listener)

    def on_vesta(self, listener: VestaOut) -> None:
        """Called when Vesta speaks in a room by herself, for the wall tablets there."""
        self._vesta_out.append(listener)

    def on_action(self, listener: ActionListener) -> None:
        """Called after HouseMind did something, for the message with Why? and Undo (E-135)."""
        self._action_listeners.append(listener)

    async def _notify(self, knows: bool = True) -> None:
        """Every screen gets the newest picture. ``knows=False`` only for what changes no
        knowledge – a sensor that changed, new measurements – so "Klaar voor autonomie?" is
        not made again for that."""
        if knows:
            self.knows += 1
        for listener in list(self._listeners):
            try:
                await listener()
            except Exception:
                _LOGGER.exception("UI listener failed")

    # -- the loop -----------------------------------------------------------------------

    # -- Home Assistant restarting, updating and changing (H7.4, H7.5) --------------------

    # -- the integration in Home Assistant: status, actions, Assist (B-06, E-96, E-97) ----

    # -- colour schemes as files (E-266, E-267, B-113, B-114) ----------------------------

    def _scheme_folders(self) -> tuple[list[Path], Path | None]:
        folders = [p / "themes" for p in self.paths.knowledge]
        own = folders[-1] if len(folders) > 1 else None  # shipped → update → own (H5.4)
        return folders, own

    def load_schemes(self, only_if_changed: bool = False) -> bool:
        """Read the colour scheme files and check them; True when the list changed."""
        folders, own = self._scheme_folders()
        seen = signature(folders)
        if only_if_changed and seen == self._schemes_seen:
            return False
        self._schemes_seen = seen
        self.schemes = load_schemes(folders, own)
        _LOGGER.info(
            "Colour schemes: %s%s",
            ", ".join(s.id for s in self.schemes.ordered()) or "none",
            f" ({len(self.schemes.problems)} problems)" if self.schemes.problems else "",
        )
        return True

    # -- the App and its integration (B-06) ---------------------------------------------
    # The secret belongs to the App, not to one house's memory: demo mode has its own
    # memory and must not lock the integration out.

    async def set_house_mode(self, mode: str) -> bool:
        """A person sets the house mode – in Vesta's window, by voice or through HA (E-97,
        E-286); it holds until who is home changes. Vacation and guests are kept over a
        restart."""
        assert self.world is not None
        try:
            chosen = HouseMode(mode)
        except ValueError:
            return False
        now = self.clock.now()
        self.world.modes.set_manual(chosen, now)
        await self._resolve("vacation_suggestion")
        await self._remember_house_mode(self.world.snapshot(now))
        await self._notify()
        return True

    async def person_level(self, user_id: str | None) -> int:
        """The level of someone HouseMind knows; 0 for someone it does not."""
        if not user_id:
            return 0
        row = await self.db.fetchone("SELECT level FROM people WHERE user_id = ?", (user_id,))
        return int(row["level"]) if row else 0

    def _learns_now(self) -> bool:
        """Learning goes on – after a yes (B-20), once on (E-275), not on vacation or with
        guests: those days are not how the house usually lives (E-286)."""
        mode = self.world.modes.mode if self.world is not None else HouseMode.HOME
        return (
            self.learning_allowed
            and self.activated
            and mode not in (HouseMode.VACATION, HouseMode.GUESTS)
        )

    # -- a heat pump that heats and cools (E-318, B-185) -----------------------------------

    async def _remember_house_mode(self, snapshot: WorldState) -> None:
        """Vacation and guests chosen by a person survive a restart (E-286); once the house
        left them, nothing is kept. Away is kept with since when, so that a restart does not
        start the 24 hours before the heating goes lower again (E-315), and with a 'no' to
        'Zijn jullie op vakantie?', so that it is not asked again (E-383)."""
        assert self.world is not None
        modes = self.world.modes
        chosen = modes.mode in (HouseMode.VACATION, HouseMode.AWAY) or (
            modes.mode is HouseMode.GUESTS and modes.manual
        )
        declined = {"declined": True} if modes.vacation_declined else {}
        value = (
            {"mode": modes.mode.value, "since": modes.since.isoformat(), **declined}
            if chosen and modes.since is not None
            else None
        )
        if value != self._kept_mode:
            self._kept_mode = value
            await self.db.set_setting("house_mode", value)

    async def _restore_house_mode(self) -> None:
        assert self.world is not None
        raw = await self.db.get_setting("house_mode")
        self._kept_mode = raw if isinstance(raw, dict) else None
        if not self._kept_mode:
            return
        try:
            mode = HouseMode(str(self._kept_mode["mode"]))
            since = datetime.fromisoformat(str(self._kept_mode["since"]))
        except (KeyError, ValueError):
            return
        if mode is HouseMode.AWAY:
            # Not chosen by a person: the house goes on from there; whoever is home now
            # brings it back at the next look (E-315).
            self.world.modes.mode, self.world.modes.since = mode, since
            self.world.modes.vacation_declined = bool(self._kept_mode.get("declined"))
            return
        self.world.modes.set_manual(mode, since)

    async def _remember_lamps(self) -> None:
        """When each bedroom lamp went on, so that a lamp that is always on stays known as such
        after a restart; lamps gone from Home Assistant are dropped (E-88)."""
        assert self.world is not None
        kept = {
            entity_id: since.isoformat()
            for entity_id, since in self.world.lamps_on_since.items()
            if entity_id in self.model.entities
        }
        if kept != self._kept_lamps:
            self._kept_lamps = kept
            await self.db.set_setting("lamps_on_since", kept)

    async def _restore_lamps(self) -> None:
        assert self.world is not None
        raw = await self.db.get_setting("lamps_on_since")
        for entity_id, since in (raw if isinstance(raw, dict) else {}).items():
            try:
                self.world.lamps_on_since[str(entity_id)] = datetime.fromisoformat(str(since))
            except ValueError:
                continue
        self._kept_lamps = {k: v.isoformat() for k, v in self.world.lamps_on_since.items()}

    async def _lived_in(self, snapshot: WorldState) -> None:
        """Looking lived-in while on vacation (E-285): every evening a new plan, each step
        through the safety gate, with only lamps HouseMind may already switch itself."""
        if snapshot.mode is not HouseMode.VACATION or not self._lived_in_on:
            self._lived_in_plan, self._lived_in_day = [], None
            self._lived_in_lit.clear()
            return
        assert self.gate is not None
        local = snapshot.local
        if self._lived_in_day != local.date() and local.hour >= 12:
            self._lived_in_day = local.date()
            living, bedroom = self._lived_in_lamps()
            bed = self._bed_at(snapshot)
            bedtime = (
                bed.astimezone(local.tzinfo)
                if bed is not None
                else datetime.combine(local.date(), vacation.BEDTIME, tzinfo=local.tzinfo)
            )
            dark = vacation.dusk(local.date(), local.tzinfo, self.place())
            rng = random.Random()  # noqa: S311 - another evening each time, not security
            self._lived_in_plan = vacation.evening_plan(dark, bedtime, living, bedroom, rng)
        now = snapshot.now
        due = [s for s in self._lived_in_plan if s.at <= now]
        self._lived_in_plan = [s for s in self._lived_in_plan if s.at > now]
        for step in due:
            if now - step.at > timedelta(minutes=30):
                continue  # long past (HouseMind was not running): no lamps at odd times
            area = self.model.area_of(step.entity_id)
            request = ActionRequest(
                Layer.PLANNER,
                step.entity_id,
                step.action,
                {},
                reason="lived_in",
                area_id=area.id if area else None,
                created_at=now,
                facts={"lived_in": True},
            )
            decision = await self.gate.submit(request)
            await self._record(decision)
            if decision.executed and step.action == "light.turn_on":
                self._lived_in_lit.add(step.entity_id)
            elif step.action == "light.turn_off":
                self._lived_in_lit.discard(step.entity_id)
            self.status.thoughts.append(decision)  # in Logboek as looking lived-in
            del self.status.thoughts[:-100]

    def _lived_in_lamps(self) -> tuple[list[str], list[str]]:
        """The living room's lamps and one bedroom's: only what HouseMind may switch itself –
        not left alone, not protected, and no limit below Autonomous (E-285)."""
        assert self.gate is not None
        state = self.gate.state
        caps = state.admin_max_step

        def free(entity_id: str, area_id: str) -> bool:
            entity = self.model.entities.get(entity_id)
            if entity is None or entity.hidden or entity.disabled:
                return False
            whole = (entity_id, *self.model.groups.get(entity_id, ()))
            if entity_id in state.excluded or any(e in state.protected_entities for e in whole):
                return False
            return all(
                caps.get(key, Step.AUTONOMOUS) is Step.AUTONOMOUS
                for key in ("light", f"area:{area_id}", *whole)
            )

        rooms: dict[str, list[str]] = {}
        for entity in self.model.entities.values():
            if entity.domain != "light" or entity.entity_id in self.model.groups:
                continue
            # Through the group that steers it, once; nothing left alone (E-458).
            unit = self.model.unit(entity.entity_id)
            area = self.model.area_of(unit) if unit is not None else None
            if unit is None or area is None or unit in rooms.get(area.id, ()):
                continue
            if free(unit, area.id):
                rooms.setdefault(area.id, []).append(unit)
        living = sorted(
            e for a, lamps in rooms.items() if self.area_roles.get(a) == "living" for e in lamps
        )
        bedrooms = sorted(a for a in rooms if self.area_roles.get(a) == "bedroom")
        return living, sorted(rooms[bedrooms[0]]) if bedrooms else []

    async def _check_speech(self, now: datetime) -> None:
        """Where Vesta's voice goes (20.19), every six hours and at the start."""
        if self._speech_checked is not None and now - self._speech_checked < timedelta(hours=6):
            return
        self._speech_checked = now
        try:
            self.speech_out = await setting_up.speech_places(self)
        except Exception:
            _LOGGER.debug("Could not see which speech engines Vesta uses", exc_info=True)

    async def _safe_mode(self, err: MigrationError) -> None:
        """After a failed update (E-301): the old data is back and stays untouched; HouseMind
        runs on an empty scratch memory where only the emergency reactions work."""
        _LOGGER.error("Safe mode: %s", err)
        self.safe_mode = err.reason
        await self.db.open_scratch(self.paths.data / "safe-mode.db")

    async def _tell_safe_mode(self) -> None:
        """The administrators hear it in Home Assistant: in its notification panel and on
        their phones (E-301)."""
        lang = self._language()
        t = self.languages.text
        title = t(lang, "safe.title")
        message = t(lang, f"safe.{self.safe_mode}")
        await self.ha.notify("notify.persistent_notification", title, message, None)
        for person in self._people:
            if person.is_admin:
                for service in person.notify_services:
                    await self.ha.notify(service, title, message, None)

    async def _ask_was_it_good(self, snapshot: WorldState) -> None:
        """'Was dit goed?' (E-298, 20.20): now and then, at most once a day per person – also
        across a restart (B-417) – about something HouseMind did itself in the last half hour,
        in the part with the fewest thumbs this week; within the question budget (the router
        keeps that). Only about a try on Voorstellen (E-57, E-287): a rule on Autonoom asks
        nothing, not even afterwards (E-498, B-416)."""
        if not self.activated or not self._learns_now():
            return
        now = snapshot.now
        recent = [
            r
            for r in self.recent.values()
            if r.decision.executed
            and not r.undone
            and r.decision.request.skill
            and r.decision.request.layer is Layer.TRIAL
            and now - r.decision.at <= timedelta(minutes=30)
        ]
        if not recent:
            return
        counted = await self.thumbs_week(now)
        chosen = min(
            recent,
            key=lambda r: (
                sum(counted.get(self._part_of(r.decision.request) or "", (0, 0))),
                -r.id,
            ),
        )
        today = snapshot.local.date().isoformat()
        for person in self._people:
            if person.level < 2 or self._thumbs_asked.get(person.user_id) == today:
                continue
            self._thumbs_asked[person.user_id] = today
            await self.db.set_setting("thumbs_asked", self._thumbs_asked)
            explainer = self.vesta_explainer or self.explainer
            assert explainer is not None
            what = explainer.action_phrase(person.language, chosen.decision)
            await self._raise(
                Kind.LEARNING,
                f"thumbs:{chosen.id}",
                "notify.was_it_good",
                {"what": what},
                snapshot,
                concerns=f"user:{person.user_id}",
                actions=(("good", "notify.answer.good"), ("bad", "notify.answer.bad")),
            )

    def _calendars(self) -> list[str]:
        """The calendars HouseMind may read: none without an administrator's yes (20.19)."""
        if not self._calendar_ok or self.world is None:
            return []
        return sorted(e for e in self.world.states if e.startswith("calendar."))

    async def _ask_calendar(self, snapshot: WorldState) -> None:
        """A house that already runs and has a calendar: the administrators are asked once
        whether HouseMind may read it (20.19). Until then it does not."""
        if self.world is None or self._calendar_ok:
            return
        if await self.setting("calendar") is not None:
            return  # answered (no stays no)
        if await self.db.get_setting("setup") != "done":
            return  # setting up asks it itself
        if not any(e.startswith("calendar.") for e in self.world.states):
            return
        if "calendar_consent" not in self._raised:
            await self._raise(
                Kind.IMPORTANT,
                "calendar_consent",
                "notify.calendar_consent",
                {},
                snapshot,
                concerns="admins",
                actions=(("yes", "notify.answer.yes"), ("no", "notify.answer.no")),
            )

    async def answer_calendar(self, yes: bool) -> None:
        await self.set_house_setting("calendar", yes)

    async def answer_vacation(self, yes: bool, user_id: str | None) -> None:
        """'Zijn jullie op vakantie?' (E-286): yes turns the vacation mode on; no is not asked
        again until someone is home."""
        assert self.world is not None
        await self._resolve("vacation_suggestion")
        if yes:
            await self.set_house_mode("vacation")
        else:
            self.world.modes.vacation_declined = True
            self.world.modes.suggest_vacation = False
        await self._notify()

    def _vacation_admin(self) -> str | None:
        """Who is asked whether it is a vacation: the administrator who left last, else the
        first administrator from level 2 (E-286)."""
        if self.world is None:
            return None
        admins = {p.user_id: p for p in self._people if p.is_admin}
        left = sorted(
            (
                (state.last_changed, str(state.attributes.get("user_id") or ""))
                for entity_id, state in self.world.states.items()
                if entity_id.startswith("person.") and state.state not in ("home",)
            ),
            reverse=True,
        )
        for _, user_id in left:
            if user_id in admins:
                return user_id
        first = next((p for p in self._people if p.is_admin and p.level >= 2), None)
        return first.user_id if first else None

    async def feedback(
        self, good: bool, decision_id: int | None = None, user_id: str | None = None
    ) -> dict[str, Any]:
        """👍/👎 (E-97, E-298) on one thing HouseMind did: 👍 a light yes, 👎 a correction
        (20.9). Kept per decision and person, with what it taught, so the logbook shows it and
        it can be counted. Without a decision (HA's action): HouseMind's last action. A thumb on
        something it did not do teaches nothing and is not moved to another action (found in
        the real house: a thumb under "not done" landed on the last thing it did)."""
        assert self.learning is not None
        if decision_id is not None:
            # Also an action the brain no longer has in memory (only the last 50, none after a
            # restart; B-180). The question goes either way.
            decision = await self.stored_decision(decision_id)
            if decision is None or not decision.executed:
                await self._resolve(f"thumbs:{decision_id}")
                await self._notify()
                return {"ok": False, "reason": "not_done"}
            found_id = decision_id
        else:
            done = [r for r in self.recent.values() if r.decision.executed]
            if not done:
                return {"ok": False}
            found_id, decision = done[-1].id, done[-1].decision
        request = decision.request
        now = self.clock.now()
        learned = None
        earlier = await self.db.fetchone(
            "SELECT id, learned FROM thumbs WHERE decision_id = ? AND user_id IS ?",
            (found_id, user_id),
        )
        if earlier is not None:
            # One thumb per person per action (QA 2, B-179): a new one replaces the shown
            # thumb, and teaches nothing again.
            await self.db.execute(
                "UPDATE thumbs SET at = ?, good = ? WHERE id = ?",
                (now.isoformat(), int(good), earlier["id"]),
            )
            await self._resolve(f"thumbs:{found_id}")
            await self._notify()
            return {
                "ok": True,
                "skill": request.skill,
                "area": request.area_id,
                "learned": earlier["learned"],
            }
        if request.skill and request.area_id and self.learning_allowed:
            context = str(request.facts.get("context", ""))
            if good:
                self.learning.liked(request.skill, request.area_id, context, now)
                learned = "yes"
            elif found_id not in self._corrected:
                # 👎 and Undo on the same action are one correction together (B-179).
                self._corrected.add(found_id)
                self.learning.corrected(request.skill, request.area_id, context, now)
                await self._store_correction(now, request.skill, request.area_id, request.entity_id)
                learned = "correction"
        await self.db.execute(
            "INSERT INTO thumbs(at, decision_id, user_id, part, good, learned) VALUES(?,?,?,?,?,?)",
            (now.isoformat(), found_id, user_id, self._part_of(request), int(good), learned),
        )
        await self._resolve(f"thumbs:{found_id}")
        await self._notify()  # the logbook shows the answer at once
        return {"ok": True, "skill": request.skill, "area": request.area_id, "learned": learned}

    async def thumbs_for(self, decision_id: int) -> list[dict[str, Any]]:
        """The thumbs on one decision, oldest first (the logbook, E-298)."""
        rows = await self.db.fetchall(
            "SELECT at, user_id, good, learned FROM thumbs WHERE decision_id = ? ORDER BY id",
            (decision_id,),
        )
        return [dict(r) for r in rows]

    def _part_of(self, request: ActionRequest) -> str | None:
        """The part a decision belongs to: its skill's, else by the device (lighting …)."""
        part = self.skill_params.get(request.skill or "", {}).get("domain")
        if part:
            return str(part)
        domain = request.entity_id.split(".", 1)[0]
        return next((p for p, ha in DOMAINS.items() if domain in ha), None)

    async def thumbs_week(self, now: datetime) -> dict[str, tuple[int, int]]:
        """👍 and 👎 per part over the last seven days (S-02 b)."""
        rows = await self.db.fetchall(
            "SELECT part, good, count(*) AS n FROM thumbs WHERE at >= ? GROUP BY part, good",
            ((now - timedelta(days=7)).isoformat(),),
        )
        out: dict[str, tuple[int, int]] = {}
        for row in rows:
            up, down = out.get(str(row["part"]), (0, 0))
            if row["good"]:
                up += int(row["n"])
            else:
                down += int(row["n"])
            out[str(row["part"])] = (up, down)
        return out

    # -- the weekly house report (E-91) -------------------------------------------------

    async def week_data(self, now: datetime) -> dict[str, Any]:
        """The numbers of the last seven days, from what HouseMind already keeps."""
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        start = (now - timedelta(days=7)).isoformat()
        did: dict[str, int] = {}
        counts = {"proposed": 0, "denied": 0}
        energy_devices = set(self.energy.setup.controllable()) if self.energy else set()
        for row in await self.db.fetchall(
            "SELECT entity_id, source_layer, outcome, reason FROM decisions WHERE at >= ?",
            (start,),
        ):
            outcome = str(row["outcome"])
            if outcome in counts:
                counts[outcome] += 1
            if outcome != "executed" or row["source_layer"] in (
                "emergency",
                "person_answer",
                "restore",
            ):
                continue
            entity_id = str(row["entity_id"])
            domain = "energy" if entity_id in energy_devices else domain_of(entity_id, None)
            did[domain] = did.get(domain, 0) + 1
        undone = await self.db.fetchone(
            "SELECT count(*) AS n FROM corrections WHERE at >= ?", (start,)
        )
        learned = [
            {
                "skill": str(r["skill"]),
                "area": str(r["area_id"]),
                "step": Step(int(r["step"])).name.lower(),
            }
            for r in await self.db.fetchall(
                "SELECT skill, area_id, step FROM skill_steps WHERE since >= ? ORDER BY since",
                (start,),
            )
        ]
        answers = await self.db.fetchall(
            "SELECT answer, count(*) AS n FROM proposals WHERE answered_at >= ? GROUP BY answer",
            (start,),
        )
        by_answer = {str(r["answer"]): int(r["n"]) for r in answers}
        days = await self.db.fetchone(
            "SELECT count(*) AS n FROM day_notes WHERE day >= ?",
            ((now.astimezone(tz).date() - timedelta(days=7)).isoformat(),),
        )
        thumbs = await self.thumbs_week(now)
        data: dict[str, Any] = {
            "did": did,
            "up": sum(up for up, _ in thumbs.values()),
            "down": sum(down for _, down in thumbs.values()),
            "undone": int(undone["n"]) if undone else 0,
            "learned": learned,
            "yes": by_answer.get("do", 0) + by_answer.get("always", 0),
            "no": by_answer.get("never", 0),  # "not now" is no "no" (B-178)
            "days": int(days["n"]) if days else 0,
            "water_l": self.water.week_litres(now.astimezone(tz).date().isoformat()),
            "updates": sum(
                1
                for e, st in (self.world.states.items() if self.world else [])
                if e.startswith("update.") and st.state == "on"
            ),
            **counts,
        }
        if self.energy is not None and self.energy.setup.has_any:
            savings = await self.energy.savings(now)
            data["measuring"] = bool(savings["measuring"])
            data["days_left"] = int(savings.get("daysLeft", 0))
            week_start = (now.astimezone(tz).date() - timedelta(days=7)).isoformat()
            week = [d["saving"] for d in savings.get("days", []) if str(d["day"]) >= week_start]
            if week and not savings["measuring"]:
                data["saved"] = round(sum(week), 2)
            if self.world is not None:
                insights = await self.energy.insights(now, self.world.states)
                data["base_w"] = insights.get("baseW")
        return data

    def report_text(self, data: dict[str, Any], level: int, lang: str) -> list[str]:
        """The report for one person, with names in their language."""
        t = self.languages.text
        named = dict(data)
        named["learned"] = [
            {
                "skill": t(lang, f"skills.{i['skill']}"),
                "area": self.model.areas[i["area"]].name if i["area"] in self.model.areas else "",
                "step": i["step"],
            }
            for i in data.get("learned", [])
        ]
        sign = CURRENCY_SIGNS.get(self.config.currency if self.config else "EUR", "€")

        def money(amount: float) -> str:
            return f"{sign} {self.languages.number(lang, amount, 2)}"

        return report_lines(t, lang, level, named, money)

    async def latest_report(self) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM reports ORDER BY week DESC LIMIT 1")
        return {"week": str(row["week"]), "data": json.loads(row["data"])} if row else None

    async def _weekly_report(self, snapshot: WorldState) -> None:
        """Each person gets the report on their day, from 19:30 (B-33)."""
        sent = await self.db.get_setting("report_sent", {}) or {}
        week = week_key(snapshot.local.date())
        due_people = [
            p
            for p in self._people
            if p.level >= 2
            and due(snapshot.local, int(p.preferences.get("report_day", 6)), sent.get(p.user_id))
        ]
        if not due_people:
            return
        stored = await self.latest_report()
        if stored is None or stored["week"] != week:
            data = await self.week_data(snapshot.now)
            await self.db.execute(
                "INSERT INTO reports(week, at, data) VALUES(?,?,?) ON CONFLICT(week) DO UPDATE "
                "SET at = excluded.at, data = excluded.data",
                (week, snapshot.now.isoformat(), json.dumps(data)),
            )
        else:
            data = stored["data"]
        assert self.router is not None
        sleeping = snapshot.mode is HouseMode.SLEEP
        for person in due_people:
            lang = self.languages.resolve(person.language)
            level = 2 if person.level == 2 else 3
            if self.wants_report_words(data, level, lang):
                self.report_words_soon(week, data, lang)  # in plain words, a quarter later (E-418)
                continue
            text = " ".join(self.report_words(data, level, lang)[0])
            if await self.router.direct(person, "report.title", text, f"report:{week}", sleeping):
                sent[person.user_id] = week
            elif not sleeping:
                sent[person.user_id] = week  # does not want it as a message: the screen has it
        await self.db.set_setting("report_sent", sent)

    # -- scenes (E-106) -----------------------------------------------------------------

    def _scene_devices(self, area_id: str) -> tuple[list[tuple[Entity, set[str]]], list[Entity]]:
        assert self.skills is not None and self.gate is not None
        lights = []
        for entity in self.skills._entities(area_id, "light"):
            kind = recognise(self.catalog, entity, self.gate.state.roles.get(entity.entity_id)).kind
            allowed = kind.allowed(f"{entity.domain}.turn_on") if kind else None
            lights.append((entity, set(allowed.values) if allowed else set()))
        return lights, self.skills._entities(area_id, "cover", "cover.window_covering")

    async def _load_scenes(self) -> None:
        rows = await self.db.fetchall("SELECT * FROM scenes WHERE source = 'person' ORDER BY id")
        self._own_scenes = [
            Scene(
                f"person:{row['id']}",
                str(row["area_id"]),
                str(row["key"]),
                str(row["name"] or ""),
                "person",
                from_json(str(row["states"])),
            )
            for row in rows
        ]
        uses = await self.db.get_setting("scene_uses", {})
        self._scene_uses = dict(uses) if isinstance(uses, dict) else {}

    def scenes(self, area_id: str) -> list[Scene]:
        """The moments of one room: the start set for its role, then people's own."""
        role = self.area_roles.get(area_id)
        lights, covers = self._scene_devices(area_id)
        found = []
        if lights:
            for key, spec in self.scene_specs.items():
                if role in spec.roles:
                    steps = derive(spec, lights, covers)
                    found.append(Scene(f"start:{key}", area_id, key, None, "start", steps))
        found += [s for s in self._own_scenes if s.area_id == area_id]
        return sorted(found, key=lambda s: -self._scene_uses.get(f"{area_id}|{s.id}", 0))

    def scene_name(self, scene: Scene, lang: str) -> str:
        return scene.name or self.languages.text(lang, f"scenes.name.{scene.key}")

    async def activate_scene(
        self, area_id: str, scene_id: str, user_id: str | None
    ) -> dict[str, Any]:
        """A person starts a moment: each step goes through the safety gate (E-110)."""
        assert self.gate is not None and self.world is not None
        scene = next((s for s in self.scenes(area_id) if s.id == scene_id), None)
        if scene is None:
            return {"ok": False}
        now = self.clock.now()
        done = 0
        for entity_id, (action, data) in scene.steps.items():
            state = self.world.states.get(entity_id)
            if state is not None and (
                (action.endswith("turn_off") and state.state == "off")
                or (action == "cover.close_cover" and state.state == "closed")
                or (action == "cover.open_cover" and state.state == "open")
            ):
                continue
            request = ActionRequest(
                Layer.PERSON_ANSWER,
                entity_id,
                action,
                data,
                reason=f"scene:{scene.key}",
                area_id=area_id,
                created_at=now,
                user_id=user_id,
            )
            decision = await self.gate.submit(request)
            await self._record(decision)
            done += int(decision.executed)
        key = f"{area_id}|{scene.id}"
        self._scene_uses[key] = self._scene_uses.get(key, 0) + 1
        await self.db.set_setting("scene_uses", self._scene_uses)
        await self._notify()
        return {"ok": True, "done": done}

    async def save_scene(self, area_id: str, name: str) -> Scene | None:
        """ "Remember this": how the room looks now becomes a moment of its own."""
        assert self.world is not None
        name = " ".join(name.split())[:40]
        if area_id not in self.model.areas or not name:
            return None
        lights, covers = self._scene_devices(area_id)
        steps = capture(self.world.states, lights, covers)
        if not steps:
            return None
        key = f"own_{len(self._own_scenes) + 1}_{self.clock.now().strftime('%Y%m%d%H%M%S')}"
        await self.db.execute(
            "INSERT INTO scenes(area_id, key, name, source, states, created_at) "
            "VALUES(?,?,?, 'person', ?, ?)",
            (area_id, key, name, to_json(steps), self.clock.now().isoformat()),
        )
        await self._load_scenes()
        await self._notify()
        return next((s for s in self._own_scenes if s.key == key), None)

    async def remove_scene(self, scene_id: str) -> bool:
        if not scene_id.startswith("person:"):
            return False  # the start set stays; it is only offered
        removed = await self.db.execute(
            "DELETE FROM scenes WHERE id = ? AND source = 'person'", (int(scene_id[7:]),)
        )
        await self._load_scenes()
        await self._notify()
        return bool(removed)

    def _scene_from_text(self, text: str) -> tuple[str, Scene] | None:
        """ "Film" or "cooking in the kitchen": a short command naming a moment."""
        assert self.world is not None
        if len(text.split()) > 5:
            return None
        _, area_id = find_target(text, self.model)
        snapshot = self.world.snapshot(self.clock.now())
        occupied = [a for a, o in snapshot.occupancy.items() if o.occupied]
        areas = [area_id] if area_id else occupied + sorted(self.model.areas)
        for area in areas:
            for scene in self.scenes(area):
                words = [scene.name] if scene.name else self._scene_words.get(scene.key, [])
                if any(mentions(text, w) for w in words):
                    return area, scene
        return None

    # -- maintenance and reminders (domain 16) -------------------------------------------

    async def _maintenance(self, snapshot: WorldState) -> None:
        """Batteries, devices that went silent, filters and the bins – a word, never an act."""
        assert self.world is not None
        states = self.world.states
        skip = set()
        if self.energy is not None:
            skip = {e for e in (self.energy.setup.battery_soc, self.energy.setup.car_soc) if e}
        # A device that has not answered for a day is part of Home Assistant netjes now, for
        # the administrators (E-354, B-238); an emergency sensor stays here, for everyone.
        silent = [
            f for f in offline(self.model, states, snapshot.now) if f.kind == "offline_safety"
        ]
        found = (
            batteries(self.model, states, skip)
            + silent
            + filters(self.model, states, daily_words(self.languages, "filter"))
            + problems(self.model, states, snapshot.now)  # a device's own fault (E-537)
        )
        current = {f.topic for f in found}
        day = snapshot.local.date().isoformat()
        counted = await self.db.get_setting("maintenance_today", {}) or {}
        told = int(counted.get(day, 0)) if isinstance(counted, dict) else 0
        for finding in found:
            if finding.topic in self._raised:
                continue
            urgent = finding.kind in ("offline_safety", "problem_danger")
            if not urgent:
                if told >= MAINTENANCE_PER_DAY:
                    continue  # the rest tomorrow – a calm house, not a stream of notices
                told += 1
            key = finding.kind if finding.kind != "battery" or not finding.value else "battery_pct"
            await self._raise(
                Kind.IMPORTANT if urgent else Kind.INFO,
                finding.topic,
                f"notify.maintenance.{key}",
                {"device": finding.device, "value": finding.value},
                snapshot,
            )
        await self.db.set_setting("maintenance_today", {day: told})
        # A boiler that has not heated for more than a day (E-526, B-450): important, for
        # everyone, once per cold spell and outside the day's count – gone once it heats again.
        if self._boilers is None:
            self._boilers = BoilerWatch.load(await self.db.get_setting("boiler_watch", {}))
        cold = self._boilers.see(
            self.model,
            states,
            snapshot.now,
            fahrenheit=bool(self.config and "F" in self.config.temperature_unit),
            away=snapshot.mode is HouseMode.VACATION,
        )
        if self._boilers.changed:
            await self.db.set_setting("boiler_watch", self._boilers.dump())
            self._boilers.changed = False
        for finding in cold:
            current.add(finding.topic)
            if finding.topic not in self._raised:
                await self._raise(
                    Kind.IMPORTANT,
                    finding.topic,
                    "notify.maintenance.boiler_cold",
                    {"device": finding.device, "value": finding.value, "target": finding.target},
                    snapshot,
                )
        # Even luchten (E-532): stale air where someone is gets one word per room per day; at
        # night none – the morning briefing says it. Gone once the air is fresh again.
        if self._air is None:
            self._air = AirWatch.load(await self.db.get_setting("air_watch", {}))
        asleep = snapshot.mode is HouseMode.SLEEP
        there = {a for a, o in snapshot.occupancy.items() if o.occupied}
        if asleep:  # a sleeper the sensor does not see is still there
            there |= {a for a in self.model.areas if self.area_roles.get(a) in SLEEPING_ROLES}
        excluded = self.gate.state.excluded if self.gate is not None else set()
        stale, stays = self._air.see(
            co2_by_room(self.model, states, excluded), there, asleep, day, snapshot.now
        )
        if self._air.changed:
            await self.db.set_setting("air_watch", self._air.dump())
            self._air.changed = False
        current |= {f"maintenance:air:{a}" for a in stays}
        lang = self._language()
        for area_id, ppm in stale:
            area = self.model.areas.get(area_id)
            where = self.names.where(area_id, lang) or (area.name if area else "")
            await self._raise(
                Kind.INFO,
                f"maintenance:air:{area_id}",
                "notify.maintenance.air_stuffy",
                {"where": where, "value": ppm_text(ppm, lang)},
                snapshot,
            )
        # Water that runs away quietly in the night (E-535): judged once a morning, after
        # five; important, outside the day's count, and gone after a night without it.
        if self._water_night.see(water_meters(self.model, states), snapshot.local):
            await self._raise(
                Kind.IMPORTANT,
                "maintenance:water_night",
                "notify.maintenance.water_night",
                {"litres": round(self._water_night.litres)},
                snapshot,
            )
        if self._water_night.leaking:
            current.add("maintenance:water_night")
        # Rain on its way and something open (E-536).
        if await self._rain_open(snapshot):
            current.add("maintenance:rain_open")
        for topic in self._tracked("maintenance:"):
            if topic not in current:
                await self._resolve(topic)
        await self._waste(snapshot)
        # Rules follow the devices (E-486, E-487): a rule whose devices went, and a room
        # HouseMind does not know.
        await self._rules_follow(snapshot)
        await self._room_kinds(snapshot)

    async def _rain_open(self, snapshot: WorldState) -> bool:
        """It rains, or will within the hour, while a window or outside door is open (E-536):
        one important word with what is open, at most every three hours. True while it holds."""
        assert self.world is not None
        states = self.world.states
        asleep = snapshot.mode is HouseMode.SLEEP
        open_now = []
        for entity_id in sorted(self.openings):
            state = states.get(entity_id)
            if state is None or state.state != "on":
                continue
            area = self.model.area_of(entity_id)
            if asleep and area is not None and self.area_roles.get(area.id) in SLEEPING_ROLES:
                continue  # a window open on purpose at night
            entity = self.model.entities.get(entity_id)
            device = self.model.devices.get(entity.device_id or "") if entity else None
            open_now.append(device.name if device else entity.name if entity else entity_id)
        if not open_now:
            return False
        now = snapshot.now
        raining = rain.raining_now(states)
        if not raining:
            if self._hourly is None or now - self._hourly[0] >= timedelta(minutes=30):
                hours: list[dict[str, Any]] = []
                for weather in sorted(e for e in states if e.startswith("weather.")):
                    hours = await self.ha.hourly_forecast(weather)
                    if hours:
                        break
                self._hourly = (now, hours)
            if not rain.rain_soon(self._hourly[1], now):
                return False
        topic = "maintenance:rain_open"
        if topic not in self._raised and (
            self._rain_told is None or now - self._rain_told >= rain.AGAIN_AFTER
        ):
            self._rain_told = now
            lang = self._language()
            names = sorted(set(open_now))
            joined = (
                ", ".join(names[:-1]) + f" {self.languages.text(lang, 'ui.and')} " + names[-1]
                if len(names) > 1
                else names[0]
            )
            await self._raise(
                Kind.IMPORTANT,
                topic,
                "notify.maintenance.rain_now" if raining else "notify.maintenance.rain_soon",
                {"open": joined, "count": len(names)},
                snapshot,
            )
        return True

    async def _waste(self, snapshot: WorldState) -> None:
        """The evening before a pickup: "put the bin out" (calendar or waste sensor)."""
        assert self.world is not None
        local = snapshot.local
        today = local.date()
        for topic in self._tracked("reminder:waste:"):
            if topic.rsplit(":", 1)[1] <= today.isoformat():
                await self._resolve(topic)
        if not 17 <= local.hour < 22:
            return
        if await self.db.get_setting("waste_checked") == today.isoformat():
            return
        await self.db.set_setting("waste_checked", today.isoformat())
        words = daily_words(self.languages, "waste")
        tomorrow = today + timedelta(days=1)
        kinds = waste_from_sensors(self.model, self.world.states, words, tomorrow)
        start = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        for entity_id in self._calendars():
            events = await self.ha.calendar_events(entity_id, start, start + timedelta(days=1))
            kinds += waste_from_events(events, words)
        if not kinds:
            return
        what = ", ".join(dict.fromkeys(kinds))
        await self._raise(
            Kind.INFO,
            f"reminder:waste:{tomorrow.isoformat()}",
            "notify.waste",
            {"what": what},
            snapshot,
        )
        for area_id in [a for a in self.speech.devices if snapshot.occupied(a)][:1]:
            text = await self._vesta_room(
                snapshot, "waste", {"what": what}, f"waste:{tomorrow.isoformat()}", 40
            )
            await self.say(snapshot, area_id, text, "reminder")

    # -- around the house: appliances, fridge, oven, media, cleaning, garden -------------

    async def _household(self, snapshot: WorldState) -> None:
        assert self.world is not None
        if self.household is None:
            return
        states = self.world.states
        notices = self.household.cooking_on_while_away(snapshot, states)
        notices += self.household.fridge_open(snapshot, states)
        current = {n.topic for n in notices}
        if "household" in self.experimental:
            done = self.household.appliances_done(snapshot, states)
            notices += done
            for notice in done:
                self._done_notices[notice.topic] = snapshot.now
        else:
            self.household.appliances_done(snapshot, states)  # keep following, say nothing
        for notice in notices:
            if notice.topic in self._raised:
                continue
            await self._raise(
                # A machine that is ready is information; an oven on while away is not.
                Kind.INFO if notice.key == "appliance_done" else Kind.IMPORTANT,
                notice.topic,
                f"notify.house.{notice.key}",
                {"device": notice.device},
                snapshot,
                # With a power switch: "Zal ik hem uitzetten?" (E-61, B-327).
                actions=(("yes", "notify.answer.turn_off"), ("no", "notify.answer.keep_on"))
                if notice.switch
                else (),
            )
            if notice.key == "appliance_done":
                for area_id in [a for a in self.speech.devices if snapshot.occupied(a)][:1]:
                    text = await self._vesta_room(
                        snapshot, "appliance_done", {"device": notice.device}, notice.topic, 40
                    )
                    await self.say(snapshot, area_id, text, "reminder")
        for topic in self._tracked("house:cooking:", "house:fridge:"):
            if topic not in current:
                await self._resolve(topic)
        for topic, at in list(self._done_notices.items()):
            if snapshot.now - at >= timedelta(hours=2):
                del self._done_notices[topic]
                await self._resolve(topic)
        for request in self.household.requests(snapshot, states):
            decision = await self.consider(request)
            if decision is not None and decision.outcome in (Outcome.EXECUTED, Outcome.PROPOSED):
                self.household.done(request.entity_id, snapshot)

    # -- looking after HouseMind itself: knowledge updates, the count, diagnostics --------

    @property
    def knowledge_target(self) -> Path:
        return self.paths.data / "knowledge-update"

    async def _knowledge_update(self, snapshot: WorldState) -> None:
        """Once a day: a newer signed knowledge bundle (E-154). Off when the administrator
        turned it off, and nothing at all until bundles are published."""
        if not await self.setting("knowledge_updates"):
            return
        if updates.INDEX_URL is None or updates.PUBLIC_KEY_HEX is None:
            return
        today = snapshot.local.date().isoformat()
        if await self.db.get_setting("knowledge_checked") == today:
            return
        await self.db.set_setting("knowledge_checked", today)
        await self.check_knowledge(updates.INDEX_URL, updates.PUBLIC_KEY_HEX, snapshot)

    async def check_knowledge(self, index_url: str, key: str, snapshot: WorldState) -> str:
        """Fetch, check and install; returns what happened ("installed", "current", a reason)."""
        try:
            found = await updates.fetch(index_url)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return "unreachable"
        if found is None:
            return "unreachable"
        version, data = found
        if version and version == updates.installed_version(self.knowledge_target):
            return "current"
        try:
            manifest = updates.verify(data, key, __version__)
            installed = await asyncio.to_thread(
                updates.install, data, manifest, self.knowledge_target
            )
        except updates.BundleError as err:
            _LOGGER.warning("Knowledge bundle not used: %s", err)
            return str(err)
        await self._raise(
            Kind.INFO,
            f"knowledge:{installed.version}",
            "notify.knowledge_updated",
            {"version": installed.version, "kinds": installed.kinds},
            snapshot,
            concerns="admins",
        )
        return "installed"

    async def diagnostics(self) -> dict[str, Any]:
        """The diagnostics package (E-98): counts and versions, anonymised logs."""
        domains: dict[str, int] = {}
        for entity_id in self.model.entities:
            domain = entity_id.split(".", 1)[0]
            domains[domain] = domains.get(domain, 0) + 1
        steps: dict[str, int] = {}
        if self.learning is not None:
            for step in self.learning.steps.values():
                steps[step.name.lower()] = steps.get(step.name.lower(), 0) + 1
        names = [a.name for a in self.model.areas.values()]
        names += [d.name for d in self.model.devices.values()]
        names += [p.name for p in self._people if p.name]
        if self.world is not None:
            names += [
                str(s.attributes.get("friendly_name") or "")
                for e, s in self.world.states.items()
                if e.startswith("person.")
            ]
        states = self.world.states if self.world else {}
        persons = sum(1 for e in states if e.startswith("person."))
        # Open messages per kind and subject ("automation", "proposal" …) with the oldest
        # date – never which device or person.
        open_kinds: dict[str, dict[str, Any]] = {}
        for notice in await self.notifications():
            subject = f"{notice['kind']}:{str(notice['topic']).split(':', 1)[0]}"
            entry = open_kinds.setdefault(subject, {"count": 0, "oldest": str(notice["at"])})
            entry["count"] += 1
            entry["oldest"] = min(entry["oldest"], str(notice["at"]))
        facts = {
            "housemind": __version__,
            "home_assistant": self.config.version if self.config else None,
            "language": self.config.language if self.config else None,
            "areas": len(self.model.areas),
            "devices": len(self.model.devices),
            "entities_per_domain": dict(sorted(domains.items())),
            "steps": steps,
            "rules": len(self.rules.rules),
            "energy": self.energy.setup.summary() if self.energy else None,
            "ai": self.ai.status(),
            "experimental": sorted(self.experimental),
            "knowledge_update": updates.installed_version(self.knowledge_target),
            "temperatures": {  # counts only: rooms measured, and sockets watched for heat
                "rooms": len(self.temperatures.room),
                "rooms_secondary_only": len(
                    set(self.temperatures.secondary) - set(self.temperatures.room)
                ),
                "devices_watched": len(self.temperatures.devices),
            },
            "orb": self.status.orb,
            "reaction_ms": self.status.reactions[-1] if self.status.reactions else None,
            "last_tick": self.status.last_tick.isoformat() if self.status.last_tick else None,
            "tick_ms": self.status.tick_ms,
            "last_review": self._last_review.isoformat() if self._last_review else None,
            # For a trial in a real house: what was recognised and what the self-check found
            # – counts and action names only, never which device (E-15).
            "recognised": self._recognised_counts(),
            "self_check": {
                "ha_version": self.self_check.get("ha_version"),
                "broken_actions": self.self_check.get("broken_actions", []),
                "missing": len(self.self_check.get("missing", [])),
                "untested_ha": bool(self.gate and self.gate.state.untested_ha),
                "connected": self.status.ha_lost is None,
                # The kind only, never the host's name or machine (E-15, E-390).
                "installation": (
                    {
                        "kind": self.installation.kind,
                        "supported": self.installation.supported,
                        "healthy": self.installation.healthy,
                    }
                    if self.installation
                    else None
                ),
            },
            "people": {"known": len(self._people), "persons_in_ha": persons},
            "open_notifications": open_kinds,
            "last_hour": await self._last_hour_by_skill(),
            "detections": await security.counts(self),  # per kind, numbers only (16.9)
        }
        return diagnostics.package(facts, names)

    async def _last_hour_by_skill(self) -> dict[str, int]:
        """What HouseMind considered in the last hour, per skill, area role and outcome."""
        since = (self.clock.now() - timedelta(hours=1)).isoformat()
        found: dict[str, int] = {}
        for decision in self.status.thoughts:
            if decision.at.isoformat() < since:
                continue
            area = self.model.area_of(decision.request.entity_id)
            role = self.area_roles.get(area.id, "other") if area else "none"
            key = f"{decision.request.skill or decision.request.layer.value}|{role}|"
            key += f"{decision.outcome.value}|{decision.reason or ''}"
            found[key] = found.get(key, 0) + 1
        rows = await self.db.fetchall(
            "SELECT skill, area_id, count(*) AS n FROM proposals WHERE at >= ? GROUP BY 1, 2",
            (since,),
        )
        for row in rows:
            role = self.area_roles.get(str(row["area_id"]), "other")
            key = f"proposals|{row['skill']}|{role}"
            found[key] = found.get(key, 0) + int(row["n"])
        return dict(sorted(found.items(), key=lambda kv: -kv[1])[:40])

    def _recognised_counts(self) -> dict[str, list[int]]:
        """Per domain HouseMind could act on: [recognised as a known kind, in use]. Devices
        that only fall under the safe generic entry count as not recognised (E-156)."""
        found: dict[str, list[int]] = {}
        roles = self.gate.state.roles if self.gate else {}
        states = self.world.states if self.world else {}
        for entity in self.model.entities.values():
            if entity.domain not in ACTING_DOMAINS or entity.hidden:
                continue
            if entity.entity_id not in states:
                continue
            counts = found.setdefault(entity.domain, [0, 0])
            counts[1] += 1
            kind = recognise(self.catalog, entity, roles.get(entity.entity_id)).kind
            if kind is not None and not kind.generic:
                counts[0] += 1
        return dict(sorted(found.items()))

    # -- care and wellbeing (domain 17, experimental, E-21) ------------------------------

    async def _care(self, snapshot: WorldState) -> None:
        """Unusual quiet while the person it is about is home: one message to the contact."""
        assert self.world is not None
        settings = CareSettings.from_json(await self.setting("care"))
        person = self.world.states.get(settings.person) if settings else None
        active = (
            "care" in self.experimental
            and settings is not None
            and await self.care_agreed(settings)
            and person is not None
            and person.state == "home"
        )
        last = max(
            (o.last_activity for o in snapshot.occupancy.values() if o.last_activity),
            default=None,
        )
        usual = (
            self.rhythm.usual("wake", snapshot.day_type)
            if snapshot.day_type.value != "deviant"
            else None
        )
        reason = quiet_reason(snapshot.local, last, self._started_at, usual) if active else None
        topic = f"care:{reason}:{snapshot.local.date().isoformat()}" if reason else None
        for old in [t for t in self._tracked("care:") if t != topic]:
            await self._resolve(old)
        if topic is None or topic in self._raised or settings is None or person is None:
            return
        await self._raise(
            Kind.IMPORTANT,
            topic,
            f"notify.care.{reason}",
            {"name": str(person.attributes.get("friendly_name") or settings.person)},
            snapshot,
            concerns=f"user:{settings.contact}",
        )

    def care_user(self, person: str) -> str | None:
        """The Home Assistant user of the person care is about, if they have one."""
        state = self.world.states.get(person) if self.world else None
        user = state.attributes.get("user_id") if state is not None else None
        return str(user) if user else None

    async def care_agreed(self, settings: CareSettings) -> bool:
        """The yes of the person it is about (QA 2, M-8): their own, in their consents, when
        they have an account in Home Assistant; else the administrator confirmed that they
        said yes themselves."""
        user = self.care_user(settings.person)
        if user is None:
            return settings.consent
        row = await self.db.fetchone("SELECT consents FROM people WHERE user_id = ?", (user,))
        consents = json.loads(row["consents"] or "{}") if row else {}
        return bool(consents.get("care"))

    async def _ask_care(self) -> None:
        """Care was set up for someone with an account: they are asked themselves."""
        settings = CareSettings.from_json(await self.setting("care"))
        user = self.care_user(settings.person) if settings else None
        if settings is None or user is None or "care" not in self.experimental:
            return
        if await self.care_agreed(settings) or "care_consent" in self._raised:
            return
        assert self.world is not None
        contact = await self.db.fetchone(
            "SELECT display_name FROM people WHERE user_id = ?", (settings.contact,)
        )
        await self._raise(
            Kind.IMPORTANT,
            "care_consent",
            "notify.care_consent",
            {"contact": str(contact["display_name"]) if contact else settings.contact},
            self.world.snapshot(self.clock.now()),
            concerns=f"user:{user}",
            actions=(("yes", "notify.answer.yes"), ("no", "notify.answer.no")),
        )

    async def answer_care(self, user_id: str | None, yes: bool) -> bool:
        """The person's own yes or no to care (QA 2, M-8); a no also withdraws an earlier yes."""
        settings = CareSettings.from_json(await self.setting("care"))
        if settings is None or user_id is None or self.care_user(settings.person) != user_id:
            return False
        row = await self.db.fetchone("SELECT consents FROM people WHERE user_id = ?", (user_id,))
        if row is None:
            return False
        consents = json.loads(row["consents"] or "{}")
        if yes:
            consents["care"] = {"contact": settings.contact, "at": self.clock.now().isoformat()}
        else:
            consents.pop("care", None)
        await self.db.execute(
            "UPDATE people SET consents = ? WHERE user_id = ?", (json.dumps(consents), user_id)
        )
        await self._resolve("care_consent")
        await self._notify()
        return True

    # -- water (domain 11) ---------------------------------------------------------------

    async def _water(self, snapshot: WorldState) -> None:
        """Follow the water meters; report water that keeps running while nobody uses it, and
        ask (or, when allowed, close) the main water valve – closing only (E-58)."""
        assert self.world is not None
        if "water" in self.parts_off:
            return  # off (E-400): no word about it, and no valve – the emergency reactions stay
        if not (self.water.meters or self.water.flows):
            return
        valves = [
            e
            for e in self.model.entities.values()
            if e.domain == "valve" and e.device_class == "water"
        ]
        if "water_valve_closed" in self._raised and any(
            (s := self.world.states.get(v.entity_id)) is not None and s.state == "open"
            for v in valves
        ):
            await self._resolve("water_valve_closed")  # someone opened it again
        before = self.water.day
        self.water.update(self.world.states, snapshot.now, snapshot.local.date().isoformat())
        if before is not None and before != self.water.day:
            await self.db.set_setting("water", self.water.to_json())
        params = self.skill_params.get("water_leak_flow", {})
        if not self.water.suspect(snapshot.mode.value, params):
            await self._resolve("water_running")
            return
        minutes = round(self.water.running_for().total_seconds() / 60)
        if "water_running" not in self._raised:
            await self._raise(
                Kind.IMPORTANT,
                "water_running",
                "notify.water_running",
                {"minutes": minutes},
                snapshot,
            )
        for entity in valves:
            state = self.world.states.get(entity.entity_id)
            if state is None or state.state not in ("open", "opening"):
                continue
            area = self.model.area_of(entity.entity_id)
            decision = await self.consider(
                ActionRequest(
                    Layer.PLANNER,
                    entity.entity_id,
                    "valve.close_valve",
                    {},
                    reason="skill:water_leak_flow",
                    skill="water_leak_flow",
                    area_id=area.id if area else None,
                    created_at=snapshot.now,
                    facts={"context": snapshot.context_key(), "minutes": minutes},
                )
            )
            if decision is not None and decision.executed:
                # Nobody may wake up without water and not know why.
                await self._raise(
                    Kind.IMPORTANT,
                    "water_valve_closed",
                    "notify.water_valve_closed",
                    {"device": entity.name, "minutes": minutes},
                    snapshot,
                )

    # -- speaking, the morning briefing and the bedtime check (E-19, E-107) ---------------

    def _levels_home(self) -> list[int]:
        levels = [p.level for p in self._people if p.home]
        mode = self.world.modes.mode if self.world is not None else HouseMode.HOME
        if self._phoneless and mode not in (HouseMode.AWAY, HouseMode.VACATION):
            levels.append(1)  # a resident without a phone may be home: Carefree (E-22, E-293)
        if mode is HouseMode.GUESTS:
            levels.append(1)  # guests: the shared devices as on Carefree (E-52)
        return levels

    # -- days that differ (E-89) --------------------------------------------------------

    async def _load_days(self) -> None:
        assert self.world is not None
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        since = (self.clock.now().astimezone(tz).date() - timedelta(days=1)).isoformat()
        for row in await self.db.fetchall("SELECT * FROM day_notes WHERE day >= ?", (since,)):
            self.days.notes[str(row["day"])] = (str(row["kind"]), str(row["source"]))
            self.world.mark_deviant_day(str(row["day"]))
        left = await self.db.get_setting("left_early", {})
        if isinstance(left, dict):
            self.days.left = {str(k): bool(v) for k, v in left.items()}

    async def note_day(self, offset: int, kind: str | None, source: str) -> None:
        """Mark today (0) or tomorrow (1) as a day that differs – or as a normal day again."""
        assert self.world is not None
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        day = (self.clock.now().astimezone(tz).date() + timedelta(days=offset)).isoformat()
        if kind is None:
            self.days.notes.pop(day, None)
            self.world.unmark_deviant_day(day)
            await self.db.execute("DELETE FROM day_notes WHERE day = ?", (day,))
        else:
            self.days.notes[day] = (kind, source)
            self.world.mark_deviant_day(day)
            await self.db.execute(
                "INSERT INTO day_notes(day, kind, source, at) VALUES(?,?,?,?) "
                "ON CONFLICT(day) DO UPDATE SET kind = excluded.kind, source = excluded.source, "
                "at = excluded.at",
                (day, kind, source, self.clock.now().isoformat()),
            )
        await self._notify()

    async def _check_day(self, snapshot: WorldState) -> None:
        """Read the calendar, and see whether today looks like a day at home (E-89)."""
        assert self.world is not None
        if self.days.get(snapshot.local.date()) is not None:
            return
        kind = (
            calendar_kind(self.model, self.world.states, self._day_words)
            if self._calendar_ok
            else None
        )
        if kind is not None:
            await self.note_day(0, kind, "calendar")
            return
        decided = snapshot.local.date().isoformat() in self.days.left
        if self.days.looks_like_home_day(
            snapshot.local, snapshot.day_type, snapshot.residents_home
        ):
            await self.note_day(0, "home_day", "behaviour")
        if not decided and snapshot.local.date().isoformat() in self.days.left:
            await self.db.set_setting("left_early", self.days.left)

    # -- sleeping and waking up (E-88) --------------------------------------------------

    def _wake_at(self, world: WorldState) -> tuple[datetime, str] | None:
        alarm = (
            next_alarm(self.model, self.world.states, self._alarm_sensors, world.now)
            if self.world is not None
            else None
        )
        return self.rhythm.wake_today(world.local, world.day_type, alarm, WAKE_LEAD)

    def _can_warm_up(self) -> bool:
        """Is there a thermostat in a room where waking up also means warmth (E-88)?"""
        rooms = self.skill_params.get("wake_warmth", {}).get("in", ())
        return any(
            entity.domain == "climate"
            and area is not None
            and self.area_roles.get(area.id) in rooms
            for entity in self.model.entities.values()
            for area in (self.model.area_of(entity.entity_id),)
        )

    def _bed_at(self, world: WorldState) -> datetime | None:
        country = self.config.country if self.config else None
        tomorrow = day_type(world.local.date() + timedelta(days=1), country)
        return self.rhythm.bed_tonight(world.local, world.day_type, tomorrow)

    async def _follow_rhythm(self, snapshot: WorldState) -> None:
        """Learn bedtime and wake-up time from the house mode (E-88)."""
        assert self.world is not None
        mode = snapshot.mode
        if self._last_mode is not None and mode is not self._last_mode:
            since = self.world.modes.since or snapshot.now
            local = since.astimezone(snapshot.local.tzinfo)
            country = self.config.country if self.config else None
            tomorrow = day_type(local.date() + timedelta(days=1), country)
            today = day_type(local.date(), country)
            if snapshot.day_type.value == "deviant" and local.date() == snapshot.local.date():
                today = snapshot.day_type
            learned = self._learns_now() and self.rhythm.record(
                self._last_mode, mode, local, today, tomorrow
            )
            # The last vacation and guests (E-367): after the yes to learning, also while the
            # house is on vacation or has guests – that is when they begin.
            marked = self.learning_allowed and self.rhythm.mark(self._last_mode, mode, local)
            if learned or marked:
                await self.db.set_setting("rhythm", self.rhythm.to_json())
            if (
                self._last_mode is HouseMode.SLEEP
                and mode is HouseMode.HOME
                and 5 <= local.hour < 11
            ):
                self._woke = local
            if self._last_mode is HouseMode.SLEEP and mode is not HouseMode.SLEEP:
                assert self.router is not None
                await self.router.deliver_held(self._people, snapshot.now, snapshot.local)
        self._last_mode = mode

    async def _wake_question(self, snapshot: WorldState) -> None:
        """In the step Propose HouseMind asks the evening before – not at six in the morning:
        "Shall I wake you slowly tomorrow at 6:40?" (E-88, E-133)."""
        assert self.gate is not None and self.skills is not None and self.world is not None
        local = snapshot.local
        evening = local.hour >= 20
        night = snapshot.mode is HouseMode.SLEEP and local.hour < 3
        if not (evening or night) or self._carefree_only or snapshot.residents_home == 0:
            return
        day = local.date() + timedelta(days=1) if evening else local.date()
        asked = await self.db.get_setting("wake_asked")
        if asked == day.isoformat():
            return
        country = self.config.country if self.config else None
        alarm = next_alarm(self.model, self.world.states, self._alarm_sensors, snapshot.now)
        found = self.rhythm.wake_for(day, day_type(day, country), alarm, local.tzinfo)
        if found is None:
            return
        at, source = found
        params = self.skill_params.get("wake_light", {})
        for area_id, role in sorted(self.area_roles.items()):
            if role not in params.get("in", ()):
                continue
            if self._skill_step("wake_light", area_id) is not Step.PROPOSE:
                continue
            light = self.skills.dimmable_light(area_id)
            if light is None:
                continue
            steps = params.get("steps_pct", [60])
            request = ActionRequest(
                Layer.PLANNER,
                light.entity_id,
                f"{light.domain}.turn_on",
                {"brightness_pct": int(steps[-1])},
                reason="skill:wake_light",
                skill="wake_light",
                area_id=area_id,
                created_at=snapshot.now,
                facts={
                    "context": f"{day_type(day, country).value}|{time_bucket(at)}",
                    "planned": True,
                    "planned_for": at.isoformat(),
                    "at": at.strftime("%H:%M"),
                    "source": source,
                    "when": "tomorrow" if evening else "this_morning",
                    "warmth": self._can_warm_up(),
                },
            )
            decision = await self.gate.submit(request)
            if decision.outcome is not Outcome.PROPOSED:
                continue
            proposal_id = await self.proposals.add(decision, until=at)
            await self.db.set_setting("wake_asked", day.isoformat())
            if proposal_id is not None:
                await self._ask(proposal_id, decision)
            return

    def _rule_veto(self, request: ActionRequest) -> str | None:
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        mode = self.world.modes.mode.value if self.world else "home"
        return self.rules.veto(request, self.clock.now().astimezone(tz), mode)

    # -- house rules (E-87, E-103, E-104) -----------------------------------------------

    def understand_rule(self, text: str, language: str) -> dict[str, Any]:
        lang = self.languages.resolve(language)
        rule = parse_rule(text, lang, self.languages, self.model)
        if rule is None:
            return {"ok": False, "message": self.languages.text(lang, "rules.not_understood")}
        return {
            "ok": True,
            "rule": rule,
            "read_back": read_back(rule, lang, self.languages, self.model),
            "conflicts": [c.id for c in self.rules.conflicts(rule)],
        }

    def ai_task_place(self, entity_id: str) -> str:
        """Where an AI task of Home Assistant works – "local", "cloud" or "unknown" – from the
        integration that provides it and its id (E-422). Unknown counts as the cloud."""
        entity = self.model.entities.get(entity_id)
        said = f"{entity_id} {getattr(entity, 'platform', '') or ''}".lower()
        for place in ("local", "cloud"):
            if any(word in said for word in self._ai_task_words.get(place, [])):
                return place
        return "unknown"

    def ai_tasks(self) -> list[dict[str, str]]:
        """The AI tasks set up in Home Assistant, to choose from (E-422)."""
        return [
            {
                "id": e.entity_id,
                "name": e.name or e.entity_id,
                "place": self.ai_task_place(e.entity_id),
            }
            for e in sorted(self.model.entities.values(), key=lambda e: e.entity_id)
            if e.domain == "ai_task"
        ]

    async def understand_rule_text(self, text: str, language: str) -> dict[str, Any]:
        """Fixed sentence patterns first; only if they fail, and AI is on, ask the AI (E-87)."""
        result = self.understand_rule(text, language)
        if result["ok"] or self.ai.status() != "on":
            return result
        lang = self.languages.resolve(language)
        rule, why = await self.ai.parse_rule(text)
        if rule is None:
            message = self.languages.text(lang, "rules.not_understood")
            if why and why != "not_understood":
                message = f"{message} {self.languages.text(lang, f'ai.why.{why}')}"
            return {"ok": False, "message": message}
        return {
            "ok": True,
            "rule": rule,
            "read_back": read_back(rule, lang, self.languages, self.model),
            "conflicts": [c.id for c in self.rules.conflicts(rule)],
            "by_ai": True,
        }

    async def save_rule(
        self, rule: HouseRule, user_id: str | None, replace_ids: list[int] | None = None
    ) -> int:
        for rule_id in replace_ids or []:
            await self.rules.remove(self.db, rule_id)
        return await self.rules.add(self.db, rule, user_id, self.clock.now())

    async def keep_rule(self, pending: dict[str, Any], user_id: str | None) -> str:
        """A yes to a house rule read back (E-87): it is saved, and a changed rule replaces
        the old one, which may stay as it was on its other days (E-420). What she says then."""
        await self.save_rule(
            pending["rule"], user_id, [*pending["conflicts"], *pending.get("replaces", [])]
        )
        for kept in pending.get("also", []):
            await self.save_rule(kept, user_id)
        return "talk.rule_changed" if pending.get("replaces") else "talk.rule_saved"

    def rule_from_answer(self, proposal: Proposal, answer: Answer) -> HouseRule | None:
        """ "Always" / "Never" on a proposal becomes a house rule (E-133), after confirmation."""
        if (
            not proposal.skill
            or not proposal.area_id
            or answer not in (Answer.ALWAYS, Answer.NEVER)
        ):
            return None
        effect = "always" if answer is Answer.ALWAYS else "never"
        return HouseRule("area", proposal.area_id, effect, skill=proposal.skill)

    async def _refresh_people(self) -> None:
        rows = await self.db.fetchall("SELECT level FROM people")
        self._carefree_only = bool(rows) and all(int(r["level"]) == 1 for r in rows)
        if self.router is not None and self.world is not None:
            language = self.config.language if self.config else "en"
            try:
                self._people = await self.router.people(self.world.states, language)
            except (ConnectionError, TimeoutError):
                # HA away for a moment, as during its restart: keep who was known, so a screen
                # still gets its hello; the next refresh has it.
                return
            tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
            local = self.clock.now().astimezone(tz)
            for person in self._people:
                person.quiet = self._quiet_window(person, local)

    def _quiet_window(self, person: Person, local: datetime) -> tuple[time, time]:
        """Someone's quiet hours (E-290): from the learned bedtime of the house to their
        phone's alarm or the learned wake-up time; 23:00–07:00 until HouseMind knows. On
        level 4 a person can set their own (the time window for messages, E-128)."""
        own = (
            _clock_time(person.preferences.get("quiet_from")),
            _clock_time(person.preferences.get("quiet_until")),
        )
        if person.level >= 4 and own[0] is not None and own[1] is not None:
            return own[0], own[1]
        country = self.config.country if self.config else None
        today = day_type(local.date(), country)
        tomorrow = day_type(local.date() + timedelta(days=1), country)
        bed = self.rhythm.bed_tonight(local, today, tomorrow)
        start = bed.time() if bed is not None else QUIET_DEFAULT[0]
        wake_day = local.date() if local.hour < 12 else local.date() + timedelta(days=1)
        found = self.rhythm.wake_for(
            wake_day, day_type(wake_day, country), self._alarm_of(person), local.tzinfo
        )
        end = found[0].time() if found is not None else QUIET_DEFAULT[1]
        return start, end

    def _phones_of(self, person: Person) -> set[str]:
        """The devices of this person's phones (through the person's trackers in HA)."""
        devices: set[str] = set()
        if self.world is None:
            return devices
        state = people_by_user(self.world.states).get(person.user_id)
        for tracker in state.attributes.get("device_trackers") or [] if state else []:
            entity = self.model.entities.get(str(tracker))
            if entity is not None and entity.device_id:
                devices.add(entity.device_id)
        return devices

    def _people_in(self, area_id: str) -> set[str] | None:
        """Who is in this room (E-287) – only when a room sensor on people's phones tells it
        (such as Bluetooth positioning: a sensor whose state is the name of a room). None:
        HouseMind cannot tell, so a question goes to everyone at home."""
        if self.world is None or not self._people:
            return None
        rooms: dict[str, str] = {}
        for area in self.model.areas.values():
            for name in (area.id, area.name, *area.aliases):
                rooms[name.strip().lower()] = area.id
        told = False
        there: set[str] = set()
        for person in self._people:
            devices = self._phones_of(person)
            for entity in self.model.entities.values():
                if entity.domain != "sensor" or entity.device_id not in devices:
                    continue
                state = self.world.states.get(entity.entity_id)
                room = rooms.get(state.state.strip().lower()) if state is not None else None
                if room is not None:
                    told = True
                    if room == area_id:
                        there.add(person.user_id)
        return there if told else None

    def _asks_nobody(self, request: ActionRequest, planned: bool = False) -> bool:
        """A question nobody can take (E-287): only people on Carefree concern it, or none
        of the others could see it now (away, in their quiet hours). Then HouseMind tries.
        Something planned (waking up) was asked the evening before: quiet hours do not count
        there – waking up is in them by nature – so only the levels do."""
        if not self._people:
            return False  # nobody known yet: the question waits for the first person
        if self.world is not None and self.world.modes.mode is HouseMode.GUESTS:
            return True  # guests: the shared devices as on Carefree (E-52)
        there = self._people_in(request.area_id) if request.area_id else None
        if there:
            concerned = [p for p in self._people if p.user_id in there]
        else:
            concerned = [p for p in self._people if p.home] or list(self._people)
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        local = self.clock.now().astimezone(tz)
        # Who is home can see a question in the app, also in their quiet hours (then only the
        # phone message waits); who is away only through the phone. Quiet hours do not count
        # for something planned, nor for a question about safety (E-289).
        always = planned or request.skill in SAFETY_SKILLS
        return not any(
            p.level >= 2 and (always or p.home or not in_quiet(p, local)) for p in concerned
        )

    def _alarm_of(self, person: Person) -> datetime | None:
        """The next alarm on this person's own phone (the HA companion app), if any."""
        if self.world is None:
            return None
        devices = self._phones_of(person)
        sensors = [
            e
            for e in self._alarm_sensors
            if (entity := self.model.entities.get(e)) is not None and entity.device_id in devices
        ]
        if not sensors:
            return None
        return next_alarm(self.model, self.world.states, sensors, self.clock.now())

    # -- notifications (E-126 t/m E-128, H8.1) -------------------------------------------

    def _language(self) -> str:
        """The language of the house: chosen when setting up (E-281), else HA's."""
        chosen = self._house_language or (self.config.language if self.config else None)
        return self.languages.resolve(chosen)

    async def set_house_language(self, language: str) -> None:
        await self.db.set_setting("house_language", language)
        self._house_language = language
        self.speech.voice = await self.voice_for(None, await self.vesta_voice())
        await setting_up.refresh_pipeline(self)

    async def _raise(
        self,
        kind: Kind,
        topic: str,
        text_key: str,
        params: dict[str, Any],
        snapshot: WorldState,
        concerns: str = "home",
        actions: tuple[tuple[str, str], ...] = (),
        safety: bool = False,
        push: dict[str, str] | None = None,
        to: list[Person] | None = None,
    ) -> int | None:
        """``to``: only these of the people it concerns (B-360: administrators on
        Geïnformeerd); by default everyone."""
        assert self.router is not None
        if not self.activated and kind is not Kind.EMERGENCY:
            return None  # "Nog niet actief": nothing of its own accord but emergencies (E-275)
        if kind is not Kind.EMERGENCY and self._still_closed(topic):
            return None  # someone closed it, and its cause stays (E-383, B-282)
        new = topic not in self._raised
        since = self._raised[topic][2] if topic in self._raised else self.clock.now()
        self._raised[topic] = (kind, concerns, since)
        result = await self.router.raise_(
            kind,
            topic,
            text_key,
            params,
            self._people if to is None else to,
            self.clock.now(),
            snapshot.local,
            snapshot.mode.value == "sleep",
            concerns,
            actions,
            safety,
            push,
        )
        if new and kind is Kind.IMPORTANT and concerns == "home":
            await self._vesta_now(text_key, params, topic, snapshot)
        return result

    def _still_closed(self, topic: str) -> bool:
        at = self._closed.get(topic)
        if at is None:
            return False
        return self.clock.now() - datetime.fromisoformat(at) < CLOSED_FOR

    async def close_notice(self, topic: str) -> None:
        """Klaar or Ik kijk ernaar (E-383, B-282): the notice closes for everyone, and does not
        come back while its cause stays – a device that stays off, also after a restart."""
        assert self.router is not None
        await self._resolve(topic)
        await self.router.resolve(topic, self.clock.now())
        self._closed[topic] = self.clock.now().isoformat()
        await self.db.set_setting("notice_closed", self._closed)

    def _tracked(self, *prefixes: str) -> list[str]:
        """The open topics with these beginnings, and the closed ones (E-383): when their cause
        goes away they are resolved too, so that next time it is a new notice."""
        closed = [t for t in self._closed if t not in self._raised]
        return [t for t in [*self._raised, *closed] if t.startswith(prefixes)]

    async def _resolve(self, topic: str) -> None:
        if topic in self._closed:
            # Its cause went away: a notice someone closed may come again, as new (E-383).
            del self._closed[topic]
            await self.db.set_setting("notice_closed", self._closed)
        if topic in self._raised:
            assert self.router is not None
            self._raised.pop(topic, None)
            await self.router.resolve(topic, self.clock.now())

    async def _ask(self, proposal_id: int, decision: Decision) -> None:
        """A proposal also goes to the phone, with "Do it" / "Not now" (B-16)."""
        if self.world is None or self.explainer is None:
            return
        # The question in every language, so each person reads it in their own.
        question = {
            "lang": {
                code: self.explainer.question(code, decision) for code in self.languages.languages
            }
        }
        later = bool(decision.request.facts.get("planned_for"))
        area = decision.request.area_id
        self._show_thinking()
        # The same question as one about something unusual (E-416): this one asks it now.
        if (same := f"unusual:light_on:{decision.request.entity_id}") in self._raised:
            await self._resolve(same)
        await self._raise(
            Kind.LEARNING,
            f"proposal:{proposal_id}",
            "notify.proposal",
            {"question": question},
            self.world.snapshot(self.clock.now()),
            concerns=f"area:{area}" if area else "home",  # to whom it concerns (E-287)
            safety=decision.request.skill in SAFETY_SKILLS,  # e.g. the main water valve
            actions=(
                ("do", "notify.answer.plan_yes" if later else "notify.answer.do"),
                ("not_now", "notify.answer.plan_no" if later else "notify.answer.not_now"),
            ),
        )

    async def _expire_proposal_notifications(self, now: datetime) -> None:
        open_ids = {p.id for p in await self.proposals.open(now)}
        for topic in self._tracked("proposal:"):
            if int(topic.split(":", 1)[1]) not in open_ids:
                await self._resolve(topic)

    async def _learned(self, change: StepChange, snapshot: WorldState) -> None:
        area = self.model.areas.get(change.area_id)
        key = "skills.learned_now" if change.new is Step.AUTONOMOUS else "skills.learned_propose"
        # Written later, in the language of who reads or hears it (not the house's).
        what = {
            "t": key,
            "p": {"skill": {"t": f"skills.{change.skill}"}, "area": area.name if area else ""},
        }
        await self._raise(
            Kind.INFO,
            f"learned:{change.skill}:{change.area_id}:{change.new.name.lower()}",
            "notify.learned",
            {"what": what},
            snapshot,
        )

    async def _stepped_back(self, change: StepChange, snapshot: WorldState) -> None:
        """Two corrections or "no" within a week (20.9): the administrators hear it in plain
        words, as info; a step an administrator set themselves goes to who set it (20.10)."""
        area = self.model.areas.get(change.area_id)
        params: dict[str, Any] = {
            "skill": {"t": f"skills.{change.skill}"},
            "area": area.name if area else "",
            "why": {"t": f"skills.stepped_why_{change.reason}"},
        }
        concerns = "admins"
        if change.by_admin:
            key = "skills.stepped_admin"
            params["step"] = {"t": f"steps.{change.old.name.lower()}"}
            params["lower"] = {"t": f"steps.{change.new.name.lower()}"}
            setter = self._admin_setter(change.skill, change.area_id)
            if setter:
                concerns = f"user:{setter}"
        elif change.old is Step.AUTONOMOUS:
            key = "skills.stepped_auto"
        else:
            key = "skills.stepped_propose"
        await self._raise(
            Kind.INFO,
            f"stepped_back:{change.skill}:{change.area_id}:{change.new.name.lower()}",
            "notify.stepped_back",
            {"what": {"t": key, "p": params}},
            snapshot,
            concerns=concerns,
        )

    # -- an administrator's own step (20.10) and thresholds (20.11) ----------------------

    async def _load_learning_settings(self) -> None:
        assert self.learning is not None
        raw = await self.setting("admin_step") or {}
        self.admin_steps = {
            str(k): (STEP_NAMES[v["step"]], str(v.get("by") or ""))
            for k, v in raw.items()
            if isinstance(v, dict) and v.get("step") in STEP_NAMES
        }
        limits = await self.setting("thresholds") or {}
        self.learning.limits = {
            str(part): found
            for part, values in limits.items()
            if (found := Thresholds.read(values)) is not None
        }

    def _admin_keys(self, skill: str, area_id: str | None) -> list[str]:
        """The keys of an administrator's step that touch this skill in this room: the skill
        in the room, the room, the part, and the room's devices of that part."""
        part = self.skill_params.get(skill, {}).get("domain")
        keys = [f"{skill}@{area_id}", f"area:{area_id}"] if area_id else []
        if part:
            keys.append(str(part))
        if area_id:
            ha = DOMAINS.get(str(part), ())
            keys += [
                key
                for key in self.admin_steps
                if key in self.model.entities
                and key.split(".", 1)[0] in ha
                and (found := self.model.area_of(key)) is not None
                and found.id == area_id
            ]
        return keys

    def _admin_lift(
        self, skill: str, area_id: str | None, entity_id: str | None = None
    ) -> Step | None:
        """The step an administrator set (20.10) for the part, the room, the skill in the room
        or the device: at least that one, the highest that applies – but no higher than where
        this skill in this room went back to after corrections."""
        part = self.skill_params.get(skill, {}).get("domain")
        keys = [
            entity_id,
            f"{skill}@{area_id}" if area_id else None,
            f"area:{area_id}" if area_id else None,
            str(part) if part else None,
        ]
        lifts = [self.admin_steps[k][0] for k in keys if k and k in self.admin_steps]
        if not lifts:
            return None
        lift = max(lifts)
        held = self.learning.held.get((skill, area_id)) if self.learning and area_id else None
        return min(lift, held) if held is not None else lift

    def _lifted(self) -> dict[tuple[str, str], Step]:
        """Per skill × room the step an administrator set, where it is higher than earned."""
        assert self.learning is not None
        if not self.admin_steps:
            return {}
        now = self.clock.now()
        out: dict[tuple[str, str], Step] = {}
        pairs = set(self.learning.steps) | set(self.learning.first_seen) | self.rules_possible()
        for skill, area_id in pairs:
            if not self.rule_there(skill, area_id):
                continue  # its devices are gone (E-487)
            lifts = [
                self.admin_steps[k][0]
                for k in self._admin_keys(skill, area_id)
                if k in self.admin_steps
            ]
            if not lifts:
                continue
            lift = max(lifts)
            held = self.learning.held.get((skill, area_id))
            if held is not None:
                lift = min(lift, held)
            if lift > self.learning.step_at(skill, area_id, now):
                out[(skill, area_id)] = lift
        return out

    def _admin_setter(self, skill: str, area_id: str) -> str | None:
        for key in self._admin_keys(skill, area_id):
            if key in self.admin_steps and self.admin_steps[key][1]:
                return self.admin_steps[key][1]
        return None

    async def set_admin_step(
        self, key: str, step: str | None, user_id: str | None, match: bool = True
    ) -> bool:
        """An administrator sets a step themselves (20.10), or takes it away (None). Then the
        automations follow (E-42, E-255): on Autonomous HouseMind switches off what it can
        wholly take over there; lower, what it took over goes back on. ``match=False``: the
        caller does that itself (switching one automation off with its other rows)."""
        assert self.learning is not None
        if not self._admin_key_known(key):
            return False
        raw = dict(await self.setting("admin_step") or {})
        if step is None:
            raw.pop(key, None)
        elif step in STEP_NAMES:
            raw[key] = {"step": step, "by": user_id or ""}
        else:
            return False
        await self.db.set_setting("admin_step", raw)
        # A new choice starts afresh: where a pair went back under the old one, it lifts again.
        for pair in list(self.learning.held):
            if key in self._admin_keys(*pair):
                del self.learning.held[pair]
                self.learning._earn_dirty.add(pair)
        await self.learning.save()
        await self._load_learning_settings()
        if match:
            chosen = self.pairs_for_key(key) if step == "autonomous" else set()
            await self.match_automations(user_id, chosen)
        await self._notify()
        return True

    def _admin_key_known(self, key: str) -> bool:
        if key in self.model.entities:
            return True
        if key.startswith("area:"):
            return key[5:] in self.model.areas
        if "@" in key:
            skill, _, area_id = key.partition("@")
            return skill in self.skill_params and area_id in self.model.areas
        return key in {str(p.get("domain")) for p in self.skill_params.values()}

    async def set_thresholds(self, part: str, values: dict[str, Any] | None) -> bool:
        """The thresholds of one part (E-297); None is "Standaard"."""
        assert self.learning is not None
        if part not in {str(p.get("domain")) for p in self.skill_params.values()}:
            return False
        raw = dict(await self.setting("thresholds") or {})
        if values is None:
            raw.pop(part, None)
        else:
            found = Thresholds.read(values)
            if found is None:
                return False
            raw[part] = found.view()
        await self.db.set_setting("thresholds", raw)
        await self._load_learning_settings()
        await self._notify()
        return True

    async def reload_settings(self) -> None:
        """Everything that comes from the house settings, again – after a settings file was
        read in (E-296)."""
        assert self.rules is not None
        await self._load_house_settings()
        await self._load_learning_settings()
        await self.rules.load(self.db)
        await self._load_scenes()
        await self._load_area_roles()
        if self.energy is not None:
            self.energy.settings = EnergySettings.from_dict(await self.setting("energy"))
        name = await self.setting("vesta_name")
        if isinstance(name, str) and name.strip():
            self._vesta_name = name.strip()
            self.library = Library.build(self.languages, self.paths.knowledge, self._vesta_name)
        await self._notify()

    # -- my data (E-300, 20.17) -------------------------------------------------------------

    async def my_data(self, user_id: str, lang: str) -> dict[str, Any]:
        """What HouseMind keeps about one person, in plain words, for 'Mijn gegevens'."""
        lang = self.languages.resolve(lang)
        row = await self.db.fetchone("SELECT * FROM people WHERE user_id = ?", (user_id,))
        if row is None:
            return {"known": False}
        since = (self.clock.now() - timedelta(days=30)).isoformat()
        talks = await self.db.fetchone(
            "SELECT count(*) AS n FROM conversations WHERE user_id = ? AND at >= ?",
            (user_id, since),
        )
        answers = await self.db.fetchall(
            "SELECT at, entity_id, action, answer FROM proposals WHERE answered_by = ? "
            "ORDER BY id DESC LIMIT 50",
            (user_id,),
        )
        rules = await self.db.fetchall(
            "SELECT id FROM house_rules WHERE created_by = ? ORDER BY id", (user_id,)
        )
        mine = {int(r["id"]) for r in rules}
        consents = json.loads(row["consents"] or "{}")
        return {
            "known": True,
            "level": int(row["level"]),
            "preferences": json.loads(row["preferences"] or "{}"),
            "consents": consents,
            "conversations": int(talks["n"]) if talks else 0,
            "answers": [
                {
                    "at": str(a["at"])[:16].replace("T", " "),
                    "device": self.model.entities[a["entity_id"]].name
                    if a["entity_id"] in self.model.entities
                    else str(a["entity_id"]),
                    "answer": self.languages.text(lang, f"notify.answer.{a['answer']}"),
                }
                for a in answers
            ],
            "rules": [
                read_back(r, lang, self.languages, self.model)
                for r in self.rules.rules
                if r.id in mine
            ],
        }

    async def export_me(self, user_id: str) -> dict[str, Any]:
        """'Bewaar als bestand': only this person's own data, readable (E-300)."""
        lang = self.languages.resolve(None)
        data = await self.my_data(user_id, lang)
        talks = await self.db.fetchall(
            "SELECT at, question, answer FROM conversations WHERE user_id = ? ORDER BY id",
            (user_id,),
        )
        return {
            "about": "HouseMind – my data",
            "made": self.clock.now().isoformat(),
            "user": user_id,
            "level": data.get("level"),
            "preferences": data.get("preferences", {}),
            "consents": data.get("consents", {}),
            "conversations": [dict(t) for t in talks],
            "answers": data.get("answers", []),
            "house_rules": data.get("rules", []),
        }

    async def forget_me(self, user_id: str) -> bool:
        """'Vergeet mij' (E-300): preferences, consents, conversations, what Vesta said and
        notifications for this person alone go; answers and house rules stay for the house,
        without the name. Next time the person begins with their own short introduction."""
        row = await self.db.fetchone("SELECT 1 FROM people WHERE user_id = ?", (user_id,))
        if row is None:
            return False
        for sql in (
            "DELETE FROM people WHERE user_id = ?",
            "DELETE FROM conversations WHERE user_id = ?",
            "DELETE FROM vesta_said WHERE user_id = ?",
            "DELETE FROM presence_history WHERE user_id = ?",
            "UPDATE proposals SET answered_by = NULL WHERE answered_by = ?",
            "UPDATE house_rules SET created_by = NULL WHERE created_by = ?",
            "UPDATE thumbs SET user_id = NULL WHERE user_id = ?",  # counted, no longer theirs
        ):
            await self.db.execute(sql, (user_id,))
        await self.db.execute("DELETE FROM notifications WHERE concerns = ?", (f"user:{user_id}",))
        if self._thumbs_asked.pop(user_id, None) is not None:  # the day of 'Was dit goed?'
            await self.db.set_setting("thumbs_asked", self._thumbs_asked)
        if self.router is not None:
            tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
            await self.router.forget(user_id, self.clock.now().astimezone(tz))
        await self._refresh_people()
        await self._notify()
        return True

    # -- what HouseMind would do now (E-295, 20.14) ---------------------------------------

    def simulate(self, lang: str) -> dict[str, Any]:
        """'Wat ik nu zou doen': per room what HouseMind would do now and why, and what the
        gate thinks of it. A copy of the skills with a learning that keeps nothing runs on the
        world of now; nothing is switched, kept or learned."""
        assert self.skills is not None and self.world is not None and self.gate is not None
        assert self.learning is not None and self.explainer is not None
        lang = self.languages.resolve(lang)
        t = self.languages.text
        now = self.clock.now()
        snapshot = self.world.look(now)  # only looking: the house mode stays (design 70.1)
        twin = quiet_copy(self.skills, self.learning)
        rooms: dict[str, list[dict[str, str]]] = {}
        if self.status.emergency not in ALARMS:  # in an alarm the emergency reactions rule
            for request in twin.evaluate(snapshot, dict(self.world.states)):
                decision = self.gate.evaluate(request)
                reason = self.explainer.reason_phrase(lang, decision)
                if decision.outcome is Outcome.EXECUTED:
                    gate = t(lang, "ui.simulate.gate.do")
                elif decision.outcome is Outcome.PROPOSED:
                    gate = t(lang, "ui.simulate.gate.ask")
                else:
                    gate = t(
                        lang, "ui.simulate.gate.not", why=self.explainer.why_not(decision, lang)
                    )
                rooms.setdefault(request.area_id or "", []).append(
                    {
                        "text": t(
                            lang,
                            "ui.simulate.line",
                            action=self.explainer.wanted(lang, decision),
                            reason=reason,
                            Reason=reason[:1].upper() + reason[1:] + ".",
                        ),
                        "gate": gate,
                        "outcome": decision.outcome.value,
                    }
                )
        out = [
            {"area": self.model.areas[a].name if a in self.model.areas else "", "items": items}
            for a, items in sorted(
                rooms.items(),
                key=lambda kv: self.model.areas[kv[0]].name if kv[0] in self.model.areas else "",
            )
        ]
        local = now.astimezone(ZoneInfo(self.config.time_zone if self.config else "UTC"))
        return {
            "at": local.strftime("%H:%M:%S"),
            "rooms": out,
            "nothing": None if out else t(lang, "ui.simulate.nothing"),
        }

    # -- forget one thing, or go back to an earlier day (E-94, 20.13) ----------------------

    async def forget_pair(self, skill: str, area_id: str) -> bool:
        """'Vergeet dit': what HouseMind learned about this skill in this room goes."""
        assert self.learning is not None
        if skill not in self.skill_params or area_id not in self.model.areas:
            return False
        await self.learning.forget_pair(skill, area_id, self.clock.now())
        await self.forget_proof(skill, area_id)
        for explainer in (self.explainer, self.vesta_explainer):
            if explainer is not None:
                explainer.history = {
                    k: v for k, v in explainer.history.items() if k[:2] != (skill, area_id)
                }
        await self._notify()
        return True

    def snapshot_days(self) -> list[str]:
        return snapshots.days(self.paths.data / "snapshots")

    async def restore_snapshot(self, day: str) -> bool:
        """Put back what was learned on an earlier day; settings, house rules and the logbook
        stay (20.13)."""
        assert self.learning is not None
        if not await snapshots.restore(self.db, self.paths.data / "snapshots", day):
            return False
        await self.learning.reload()
        self.rhythm = Rhythm.from_json(await self.db.get_setting("rhythm"))
        await history.load(self)
        await self._notify()
        return True

    async def _security_checks(self, snapshot: WorldState) -> None:
        """Security in R1: report, never act (E-19). Open doors/windows while nobody is home."""
        assert self.world is not None
        away = snapshot.mode.value in ("away", "vacation")
        for entity in self.model.entities.values():
            if entity.domain != "binary_sensor" or entity.device_class not in OPENINGS:
                continue
            if entity.entity_id not in self.openings:
                continue  # an appliance's or car's door is not the house left open
            topic = f"open_when_away:{entity.entity_id}"
            state = self.world.states.get(entity.entity_id)
            if away and state is not None and state.state == "on":
                if topic not in self._raised:
                    await self._raise(
                        Kind.IMPORTANT,
                        topic,
                        "notify.open_when_away",
                        {"device": entity.name},
                        snapshot,
                        concerns="away",
                    )
            else:
                await self._resolve(topic)
        if snapshot.guests_unconfirmed:
            if "guests_unconfirmed" not in self._raised:
                await self._raise(
                    Kind.IMPORTANT,
                    "guests_unconfirmed",
                    "notify.guests_unconfirmed",
                    {},
                    snapshot,
                    concerns="away",
                )
        else:
            await self._resolve("guests_unconfirmed")
        if snapshot.suggest_vacation:
            if "vacation_suggestion" not in self._raised:
                # One question, to one administrator, with yes and no (E-286).
                admin = self._vacation_admin()
                await self._raise(
                    Kind.IMPORTANT,
                    "vacation_suggestion",
                    "notify.vacation_suggestion",
                    {},
                    snapshot,
                    concerns=f"user:{admin}" if admin else "admins",
                    actions=(("yes", "notify.answer.yes"), ("no", "notify.answer.no")),
                )
        else:
            await self._resolve("vacation_suggestion")

    async def _on_phone_action(self, data: dict[str, Any]) -> None:
        """A button on a phone notification. Only correctly signed buttons count (E-86)."""
        assert self.router is not None
        payload = self.router.signer.verify(str(data.get("action", "")))
        if payload is None:
            _LOGGER.warning("Ignored a phone notification button with a wrong signature")
            return
        notification_id, _, code = payload.partition("|")
        row = await self.db.fetchone(
            "SELECT topic FROM notifications WHERE id = ?", (int(notification_id),)
        )
        if row is None:
            return
        topic = str(row["topic"])
        await self.router.acknowledged_topic(topic, None)  # answered: not ignored
        if topic.startswith("proposal:") and code in {a.value for a in Answer}:
            await self.answer(int(topic.split(":", 1)[1]), Answer(code), None)
        elif topic == "vacation_suggestion" and code in ("yes", "no"):
            await self.answer_vacation(code == "yes", None)
        elif topic == "calendar_consent" and code in ("yes", "no"):
            await self.answer_calendar(code == "yes")
        elif topic == "care_consent" and code in ("yes", "no"):
            row = await self.db.fetchone(
                "SELECT concerns FROM notifications WHERE id = ?", (int(notification_id),)
            )
            user = (
                str(row["concerns"])[5:]
                if row and str(row["concerns"]).startswith("user:")
                else None
            )
            await self.answer_care(user, code == "yes")  # only their own phone got it
        elif topic.startswith(("label_lamp:", "label_system:")) and code in ("yes", "no"):
            await self.answer_label(topic, code == "yes")  # only administrators got it (E-345)
        elif topic.startswith("rule_suggest:") and code in ("yes", "no"):
            # Only administrators got it; the signature proves the button (E-417).
            await self.answer_rule_suggestion(topic, code == "yes", None)
        elif topic.startswith("unusual:") and code in ("yes", "no"):
            await self.answer_unusual(topic, code == "yes", None)  # Ja, zet uit (E-416)
        elif topic.startswith("knowledge:") and code in ("yes", "no"):
            # Klopt or Klopt niet (B-360): only administrators on Geïnformeerd got it.
            await self.answer_knowledge(topic.split(":", 1)[1], code == "yes")
        elif topic.startswith("thumbs:") and code in ("good", "bad"):
            await self.feedback(code == "good", int(topic.split(":", 1)[1]), None)
        elif topic.startswith("room_kind:") and code == "no":
            await self.answer_room_kind(topic)  # Nu niet (E-486); Kiezen needs the app
        elif topic.startswith("automation:") and code in ("yes", "no", "later"):
            # Only administrators got this question (E-42); the signature proves the button.
            await self.answer_automation(
                topic.split(":", 1)[1], code == "yes", None, later=code == "later"
            )
        elif topic.startswith("ready:") and code in ("yes", "no", "later"):
            await self.answer_ready(topic, code, None)  # one yes (E-460)
        elif topic.startswith("house:cooking:") and code in ("yes", "no"):
            await self.answer_cooking(topic, code == "yes", None)  # E-61, B-327
        elif topic.startswith(("alarm_arm:", "alarm_self_offer:")) or topic == "alarm_choice":
            await security.answer(self, topic, code, None)  # "Alarm op afwezig zetten?

    def _cooking_roles(self) -> None:
        """The power switches of cooking appliances (E-61, B-327): in the gate only ever
        "off", and only after a person's yes (catalogue ``switch.cooking_off``)."""
        if self.gate is None or self.household is None:
            return
        roles = self.gate.state.roles
        for entity_id in [e for e, role in roles.items() if role == "cooking_off"]:
            del roles[entity_id]
        for entity_id in self.household.off_switches():
            roles.setdefault(entity_id, "cooking_off")

    async def answer_cooking(self, topic: str, yes: bool, user_id: str | None) -> dict[str, Any]:
        """ "Zal ik hem uitzetten?" (E-61, B-327). Uitzetten: the power switch goes off through
        the gate, as a person's answer. Laat aan: the notice closes while it stays on."""
        assert self.gate is not None and self.household is not None
        switch = self.household.off_switch(topic)
        if not yes or switch is None:
            await self.close_notice(topic)
            await self._notify()
            return {"ok": not yes}
        request = ActionRequest(
            Layer.PERSON_ANSWER,
            switch,
            "switch.turn_off",
            reason="cooking_away",
            created_at=self.clock.now(),
            user_id=user_id,
        )
        decision = await self.gate.submit(request)
        await self._record(decision)
        if decision.executed:
            await self.close_notice(topic)  # it comes back only if it is on again later
        await self._notify()
        return {"ok": decision.executed}

    async def notifications(self) -> list[dict[str, Any]]:
        """Open notifications for the notification centre."""
        assert self.router is not None
        return await self.router.open_list(self.clock.now())

    # -- proposals (E-133) --------------------------------------------------------------

    async def answer(
        self, proposal_id: int, answer: Answer, user_id: str | None, said_no: bool = False
    ) -> dict[str, Any]:
        """A person answers a question (E-133). ``said_no``: a plain "nee" to Vesta, which
        counts towards a step back like Never; "Not now" only brings it back later (20.9)."""
        assert self.gate is not None and self.learning is not None
        now = self.clock.now()
        proposal = await self.proposals.answer(proposal_id, answer, user_id, now)
        if proposal is None:
            return {"ok": False, "reason": "already_answered"}
        if self.router is not None:
            await self.router.acknowledged_topic("proposal:", user_id)
        approved = answer in (Answer.DO, Answer.ALWAYS, Answer.FROM_NOW)
        not_now = answer is Answer.NOT_NOW and not said_no
        if proposal.skill and proposal.area_id and not_now:
            # "Not now" is no "no" (QA 2, B-178): it only brings the question back later.
            self.learning.drop_pending(proposal.skill, proposal.area_id)
        elif proposal.skill and proposal.area_id and self.learning_allowed:
            self.status.learning_until = now + LEARN_SHOW  # it learns from the answer
            self.learning.answered(
                proposal.skill,
                proposal.area_id,
                proposal.context,
                approved,
                now,
                strong=answer in (Answer.ALWAYS, Answer.NEVER, Answer.FROM_NOW),
                setback=answer is not Answer.NOT_NOW or said_no,
            )
            if answer is Answer.FROM_NOW:  # from now on itself, no question any more (E-460)
                await self.learning.grant(proposal.skill, proposal.area_id, now, "from_now")
                await self.forget_proof(proposal.skill, proposal.area_id)
        await self._resolve(f"proposal:{proposal_id}")
        result: dict[str, Any] = {"ok": True}
        planned_for = proposal.facts.get("planned_for")
        if approved and planned_for:
            # "Yes, wake me": nothing happens now; that morning HouseMind may (E-88).
            day = datetime.fromisoformat(str(planned_for)).date()
            self.rhythm.grant(WAKE_SKILLS, day)
            await self.db.set_setting("rhythm", self.rhythm.to_json())
            result["planned"] = True
        elif approved:
            asked = proposal.request(Layer.PERSON_ANSWER, now, user_id)
            previous = self.world.states.get(asked.entity_id) if self.world else None
            decision = await self.gate.submit(asked)
            result["decision"] = await self._record(decision, previous)  # "zet dat terug"
            result["executed"] = decision.executed
            result["reason"] = decision.reason
        # The same answer for the rest of the question: the other lamps of this skill in this
        # room (learning counted once, above).
        for other in await self.proposals.same_question(proposal):
            if await self.proposals.answer(other.id, answer, user_id, now) is None:
                continue
            await self._resolve(f"proposal:{other.id}")
            if approved and not other.facts.get("planned_for"):
                await self._record(
                    await self.gate.submit(other.request(Layer.PERSON_ANSWER, now, user_id))
                )
        if answer in (Answer.ALWAYS, Answer.NEVER):
            result["suggest_rule"] = True  # becomes a house rule after confirmation (E-87)
        await self._notify()
        return result

    async def undo(self, decision_id: int, user_id: str | None) -> dict[str, Any]:
        """Put a device back as it was (E-92). Counts as a correction for learning."""
        assert self.gate is not None and self.learning is not None
        recent = self.recent.get(decision_id)
        if recent is None or recent.undone or not recent.decision.executed:
            return {"ok": False, "reason": "cannot_undo"}
        now = self.clock.now()
        request = (
            inverse(recent.decision.request, recent.previous, now, user_id)
            if recent.previous is not None
            else None
        )
        if request is None:
            return {"ok": False, "reason": "cannot_undo"}
        decision = await self.gate.submit(request)
        await self._record(decision)
        if not decision.executed:
            return {"ok": False, "reason": decision.reason}
        recent.undone = True
        original = recent.decision.request
        entity = self.model.entities.get(original.entity_id)
        if entity is not None:
            kind = recognise(self.catalog, entity, self.gate.state.roles.get(entity.entity_id)).kind
            hours = kind.people_win_max_hours if kind is not None else 2
            self.gate.state.people_win_until[entity.entity_id] = now + timedelta(hours=hours)
        if (
            original.skill
            and original.area_id
            and self.learning_allowed
            and decision_id not in self._corrected  # 👎 already counted it (B-179)
        ):
            self._corrected.add(decision_id)
            context = str(original.facts.get("context", ""))
            self.learning.corrected(original.skill, original.area_id, context, now)  # 20.9
            await self._store_correction(now, original.skill, original.area_id, original.entity_id)
        await self._notify()
        return {"ok": True}

    def why(self, decision_id: int, level: int, language: str, plain: bool = False) -> list[str]:
        recent = self.recent.get(decision_id)
        if recent is None:
            return []
        return self.explain(recent.decision, max(level, 3), language, plain)

    async def why_stored(
        self, decision_id: int, level: int, language: str, plain: bool = False
    ) -> list[str]:
        """Why?, also for a decision the brain no longer has in memory: the logbook keeps
        every decision (QA 2, B-180)."""
        decision = await self.stored_decision(decision_id)
        if decision is None:
            return []
        return self.explain(decision, max(level, 3), language, plain)

    async def stored_decision(self, decision_id: int) -> Decision | None:
        """One decision: from memory, or – when the brain no longer has it there (only the
        last 50, none after a restart) – from the database, which keeps every one (B-180)."""
        recent = self.recent.get(decision_id)
        if recent is not None:
            return recent.decision
        row = await self.db.fetchone("SELECT * FROM decisions WHERE id = ?", (decision_id,))
        if row is None:
            return None
        from housemind.server.logbook import decision_from_row  # the server builds on this

        try:
            return decision_from_row(row)
        except (KeyError, ValueError, TypeError):
            return None

    # -- house settings (administrators, E-82) ------------------------------------------

    async def _load_house_settings(self) -> None:
        assert self.gate is not None
        self.learning_allowed = bool(await self.setting("consent_learning"))
        caps = await self.setting("admin_max_step") or {}
        await self._load_parts(caps)
        ha_domains = {d for domains in part_list.HA_DOMAINS.values() for d in domains}
        self.gate.state.admin_max_step = {
            key: STEP_NAMES[value]
            for key, value in caps.items()
            if value in STEP_NAMES and key not in ha_domains  # those come from the parts now
        }
        for part, domains in part_list.HA_DOMAINS.items():
            if self.parts.get(part) in ("observe", "propose"):
                for domain in domains:
                    self.gate.state.admin_max_step[domain] = STEP_NAMES[self.parts[part]]
        if await self.db.get_setting("given_back", False):
            # E-112: everything back to Observe; emergency reactions keep working.
            for domain in {e.domain for e in self.model.entities.values()}:
                self.gate.state.admin_max_step[domain] = Step.OBSERVE
        self.gate.state.excluded = set(await self.setting("excluded") or [])
        kept = await self.db.get_setting("automation_keep_off", []) or []
        self._keep_off_chosen = {str(e) for e in kept}  # E-317
        self._proof_answers = dict(await self.db.get_setting("proof_answers", {}) or {})  # E-460
        self._proof_offers = dict(await self.db.get_setting("proof_offers", {}) or {})
        # Devices an administrator added to the hard limits: the list only grows (E-31).
        self.gate.state.protected_entities = set(await self.setting("protected") or [])
        if self.world is not None:
            self._index_groups()  # a lamp left alone: its group no longer steers it (E-312)
        self._sort_openings()
        self._index_functions()  # a contact left out is no opening of the house (E-427)
        self._phoneless = bool(await self.setting("phoneless_residents"))
        self._lived_in_on = await self.setting("lived_in") is not False  # E-285
        self._calendar_ok = await self.setting("calendar") is True  # one yes (20.19)
        asked = await self.db.get_setting("thumbs_asked", {})  # once a day, also after a restart
        self._thumbs_asked = (
            {str(k): str(v) for k, v in asked.items()} if isinstance(asked, dict) else {}
        )
        if self.world is not None:
            self.world.modes.phoneless = self._phoneless  # E-293
        stored = await self.setting("pet_areas")
        pets = set(stored) if isinstance(stored, list) else set(self._default_pets)
        self.pet_areas = pets
        if self.world is not None:
            self.world.pet_areas = set(pets)
            self.world.occupancy.pet_areas = set(pets)

    async def give_back(
        self, yes: bool, user_id: str | None = None, unlink: bool = True
    ) -> dict[str, Any]:
        """Before removing HouseMind (E-112): every automation HouseMind switched off goes back
        on, all domains go back to Observe, and the link in HA goes (the device, the Assist
        agent and her own pipeline). ``False`` takes it up again as before; the automations
        then stay on until an administrator says yes again (E-42), and the link can be placed
        again."""
        failed: list[str] = []
        linked = True
        if yes:
            for entity_id in self.automations.turned_off():
                result = await self.restore_automation(entity_id, user_id, reason="give_back")
                if not result["ok"]:
                    failed.append(entity_id)
            # What Home Assistant netjes switched off goes back on too (B-243).
            failed += await self.tidy_all_on(user_id, reason="give_back")
            failed += await self.daylight_all_on()  # and the day's rhythm of the light (E-468)
            for entity_id, status in list(self.automations.status.items()):
                if status is AutomationStatus.OFFERED:  # the open questions are withdrawn
                    await self.automations.set_status(
                        entity_id, AutomationStatus.SEEN, self.clock.now(), user_id
                    )
                    await self._resolve(f"automation:{entity_id}")
            if unlink and not failed:
                linked = await start_over.remove_link(self)
        await self.db.set_setting("given_back", bool(yes))
        await self._load_house_settings()
        await self._notify()
        return {"ok": not failed, "failed": failed, "link": linked}

    async def setting(self, key: str) -> Any:
        """A house setting as it is now, or its default from the one table (code QA 3, punt 1)."""
        return await self.db.get_setting(key, house_settings.default(key))

    async def set_house_setting(self, key: str, value: Any) -> bool:
        """Change a house setting; returns False for unknown keys or bad values. A plain one
        (a tick, a level) is checked by the table; the others here, with what follows."""
        if key == "consent_learning" and isinstance(value, bool):
            await self.db.set_setting(key, value)
            task = self._history_task
            if not value and task is not None and not task.done():
                # 'Nee' after all (E-395): the reading stops, and nothing of it is taken over.
                task.cancel()
                self.history_status.state, self.history_status.progress = "idle", 0.0
            if value and not await self.db.get_setting("learning_since"):
                # From now on HouseMind learns itself; the history covers the days before.
                await self.db.set_setting("learning_since", self.clock.now().isoformat())
        elif key == "history_learning" and isinstance(value, bool):
            await self.db.set_setting(key, value)
            self.history_status.state = "idle" if value else "off"
        elif key == "admin_max_step" and isinstance(value, dict):
            clean = {
                str(k): str(v) for k, v in value.items() if str(v) in STEP_NAMES and len(k) < 80
            }
            await self.db.set_setting(key, clean)
            # A limit per Home Assistant domain, as before the list of parts (B-320): it goes
            # in the list, but never switches on a part that is off.
            change = {
                part: min(
                    [clean[d] for d in ha if d in clean] or ["autonomous"],
                    key=part_list.STATES.index,
                )
                for part, ha in part_list.HA_DOMAINS.items()
                if self.parts.get(part) != "off"
            }
            await self.set_parts(change, None, expert=True)
        elif key == "parts":
            await self.set_parts(value, None, expert=True)
        elif key == "excluded" and isinstance(value, list):
            await self.db.set_setting(
                key, sorted({str(e) for e in value if str(e) in self.model.entities})
            )
        elif key == "pet_areas" and isinstance(value, list):
            await self.db.set_setting(
                key, sorted({str(a) for a in value if str(a) in self.model.areas})
            )
        elif key == "care" and isinstance(value, dict):
            care = CareSettings.from_json(value)
            if care is None or not care.person.startswith("person."):
                return False
            await self.db.set_setting(
                key, {"person": care.person, "contact": care.contact, "consent": care.consent}
            )
            await self._ask_care()  # someone with an account says yes themselves (M-8)
        elif key == "calendar" and isinstance(value, bool):
            await self.db.set_setting(key, value)  # one yes for the calendar (20.19)
            await self._resolve("calendar_consent")
            self._briefing_data = None
        elif key == "phoneless_residents" and isinstance(value, bool):
            await self.db.set_setting(key, value)  # residents without a phone (E-293)
            await self._load_house_settings()
        elif key == "private_names" and isinstance(value, list):
            # Their first names, only to leave them out at an AI (QA 2, B-174).
            names = [str(n).strip()[:40] for n in value if isinstance(n, str) and n.strip()]
            await self.db.set_setting(key, list(dict.fromkeys(names))[:30])
            await self._refresh_labels()
        elif key == "vesta_voice" and (not value or isinstance(value, dict)):
            if value and not str(value.get("engine", "")).startswith("tts."):
                return False
            await self.db.set_setting(
                key,
                {
                    "engine": str(value["engine"]),
                    "language": (str(value["language"])[:16] if value.get("language") else None),
                    "voice": str(value["voice"])[:80] if value.get("voice") else None,
                }
                if value
                else None,  # back to the recommended voice
            )
            self.speech.voice = await self.voice_for(None, await self.vesta_voice())
            await setting_up.refresh_pipeline(self)  # her own pipeline follows (E-280)
        elif key == "house_language" and value in self.languages.languages:
            # The language of the house, changed later (E-320): a chosen voice that speaks
            # another language goes back to the recommended one; her pipeline follows.
            chosen = await self.setting("vesta_voice")
            spoken = str(chosen.get("language") or "") if isinstance(chosen, dict) else ""
            if spoken and spoken.split("-")[0].lower() != value:
                await self.db.set_setting("vesta_voice", None)
            await self.set_house_language(value)
        elif key == "vesta_name" and (name := good_name(value)) is not None:
            await self.db.set_setting(key, name)
            self._vesta_name = name
            self.library = Library.build(self.languages, self.paths.knowledge, self._vesta_name)
            await setting_up.refresh_pipeline(self)
        elif key == "experimental" and isinstance(value, list):
            # The ticks of before the list of parts (B-320): ticked is on, else off.
            chosen = {str(d) for d in value}
            await self.set_parts(
                {
                    d: (self.parts.get(d, "off") if self.parts.get(d) != "off" else "autonomous")
                    if d in chosen
                    else "off"
                    for d in EXPERIMENTAL
                },
                None,
                expert=True,
            )
        elif key in security.SETTINGS:
            if not await security.set_setting(self, key, value):  # which alarm, sounds (16.6)
                return False
        elif house_settings.TABLE.get(key, house_settings.Setting()).plain:
            # A tick or a level and nothing that follows (knowledge updates, Vesta's window, a
            # new person's level, the highest for a resident, looking lived-in): the table.
            good, kept = house_settings.plain(key, value)
            if not good:
                return False
            await self.db.set_setting(key, kept)
        else:
            return False
        await self._load_house_settings()
        if key in ("consent_learning", "history_learning") and value is True:
            if self.history_status.read_at is None:
                self.start_history()
        await self._notify()
        return True

    def explain(
        self, decision: Decision, level: int, language: str, plain: bool = False
    ) -> list[str]:
        """Why it did something; ``plain`` when Vesta says it (E-248)."""
        explainer = self.vesta_explainer if plain else self.explainer
        assert explainer is not None
        lang = self.languages.resolve(language)
        return [
            sentence_case(x) for x in explainer.explain(decision, level, lang, self.clock.now())
        ]

    def why_not(self, entity_id: str, language: str, plain: bool = False) -> str:
        explainer = self.vesta_explainer if plain else self.explainer
        assert explainer is not None
        lang = self.languages.resolve(language)
        decision = self.last_not_done.get(entity_id)
        part = PARTS_BY_DOMAIN.get(entity_id.split(".", 1)[0])
        off = self.parts_off.get(part or "")
        if off is not None and (
            decision is None or decision.at < datetime.fromisoformat(off["since"])
        ):
            # Its part is off (E-400): that is why – unless something newer was left for
            # another reason, such as a kind of device HouseMind does not know.
            said = self.languages.text(lang, f"ui.domain.{part}")
            return self.languages.text(lang, "gate.reason.part_off", part=said)
        name = self.automations.controller_of(entity_id)
        if decision is not None and decision.reason == "other_system" and name:
            return self.languages.text(lang, "gate.reason.automation", name=name)
        said = sentence_case(explainer.why_not(decision, lang))
        note = self.label_note(entity_id, lang)  # where a label gave the part (E-345)
        return f"{said} {note}" if note else said

    async def why_device(self, entity_id: str, language: str) -> str:
        """'Waarom niet?' under a device (E-93). When HouseMind itself last switched it – or a
        group it is in – after the last thing it left, it says that, with when: before, it said
        "Ik heb hier nog niets mee gedaan" right after it switched the hall lights."""
        lang = self.languages.resolve(language)
        entity = self.model.entities.get(entity_id)
        if self.world is not None and entity_id in self.world.status_led_ids:
            return self.languages.text(lang, "ui.room.status_led_why")  # no lamp (E-426)
        if entity is not None and entity_role(entity) is EntityRole.SETTING:
            return self.languages.text(lang, "ui.room.setting_why")
        ids = [entity_id, *(g for g, lamps in self.model.groups.items() if entity_id in lamps)]
        last: int | None = None
        for one in ids:  # the device itself, and each group it is in (index on entity_id, at)
            row = await self.db.fetchone(
                "SELECT id FROM decisions WHERE entity_id = ? AND outcome = ? "
                "ORDER BY at DESC, id DESC LIMIT 1",
                (one, Outcome.EXECUTED.value),
            )
            if row is not None and (last is None or int(row["id"]) > last):
                last = int(row["id"])
        done = await self.stored_decision(last) if last is not None else None
        left = self.last_not_done.get(entity_id)
        if done is None or (left is not None and left.at > done.at):
            return self.why_not(entity_id, lang)
        said = " ".join(self.explain(done, 2, lang)[:1])
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        at = done.at.astimezone(tz)
        days = (self.clock.now().astimezone(tz).date() - at.date()).days
        key = (
            ("talk.why_not_at", "talk.why_not_yesterday")[days] if days < 2 else "talk.why_not_days"
        )
        return self.languages.text(lang, key, reason=said, time=at.strftime("%H:%M"), days=days)

    # -- existing automations (E-03, E-41, E-42, E-112) ----------------------------------

    def domain_cap(self, domain: str) -> Step | None:
        """The administrator's limit for a HouseMind domain, if any (E-12, E-399)."""
        assert self.gate is not None
        if domain not in DOMAINS:
            state = self.parts.get(domain)
            return STEP_NAMES[state] if state in ("observe", "propose") else None
        caps = self.gate.state.admin_max_step
        return min((caps[d] for d in DOMAINS.get(domain, ()) if d in caps), default=None)

    # -- parts on and off (E-399 – E-401, B-320 – B-326) ----------------------------------

    async def _load_parts(self, caps: dict[str, Any]) -> None:
        """The list of parts; the first time from the settings of before (B-320)."""
        stored = await self.db.get_setting("parts")
        if not isinstance(stored, dict):
            before = await self.setting("experimental")
            stored = part_list.from_before(caps, before if isinstance(before, list) else [])
            await self.db.set_setting("parts", stored)
        self.parts = part_list.merged({}, stored, expert=True)
        off = await self.db.get_setting("parts_off", {}) or {}
        now = self.clock.now().isoformat()
        self.parts_off = {}
        for part, state in self.parts.items():
            if state == "off":
                kept = off.get(part) if isinstance(off, dict) else None
                self.parts_off[part] = (
                    {str(k): str(v) for k, v in kept.items()}
                    if isinstance(kept, dict) and "since" in kept
                    # Off without anyone choosing it: an experimental part never turned on.
                    else {"since": now, "was": "autonomous", "by": "default"}
                )
        if self.parts_off != off:
            await self.db.set_setting("parts_off", self.parts_off)  # the same "since" later
        self.experimental = {d for d in EXPERIMENTAL if self.parts.get(d) != "off"}
        if self.learning is not None:
            self.learning.paused = {
                p: datetime.fromisoformat(v["since"]) for p, v in self.parts_off.items()
            }

    def part_off(self, request: ActionRequest) -> str | None:
        """The part of a request, when it is off: then HouseMind does nothing with it."""
        part = self._part_of(request)
        return part if part is not None and part in self.parts_off else None

    async def set_parts(self, change: Any, user_id: str | None, expert: bool) -> None:
        """Change the list (E-399). Off: asleep, and its open questions and notices go (E-400);
        on again: what it learned goes on where it was. Both in the Logboek."""
        assert self.gate is not None
        now = self.clock.now()
        if isinstance(change, dict):
            # "Aanzetten" in Brein (B-322): back to what it was before it went off.
            for part, state in list(change.items()):
                if state == "on":
                    was = (self.parts_off.get(str(part)) or {}).get("was")
                    change[part] = was if was in part_list.STATES[1:] else "autonomous"
        new = part_list.merged(self.parts, change, expert)
        before_caps = {domain: self.domain_cap(domain) for domain in DOMAINS}
        off = dict(self.parts_off)
        woke: set[str] = set()  # on again, straight to Autonomous: the automations follow
        for part, state in new.items():
            was = self.parts.get(part, part_list.default(part))
            if state == was or "off" not in (state, was):
                continue
            if was == "off" and state == "autonomous":
                woke.add(part)
            if state == "off":
                off[part] = {"since": now.isoformat(), "was": was}
            else:
                if self.learning is not None:
                    self.learning.resume(part, now)
                off.pop(part, None)
            await self.db.execute(
                "INSERT INTO part_changes(at, part, state, user_id) VALUES(?,?,?,?)",
                (now.isoformat(), part, "off" if state == "off" else "on", user_id),
            )
            if state == "off":
                await self._part_asleep(part)
        await self.db.set_setting("parts", new)
        await self.db.set_setting("parts_off", off)
        await self._load_house_settings()
        await self._ask_care()
        await self._after_step_change(before_caps, user_id, woke)

    async def _part_asleep(self, part: str) -> None:
        """A part goes off: its open questions and its own notices close (E-400)."""
        assert self.proposals is not None
        skills = {s for s, p in self.skill_params.items() if p.get("domain") == part}
        for proposal in await self.proposals.open(self.clock.now()):
            if proposal.skill in skills:
                await self.proposals.expire(proposal.id)
                await self._resolve(f"proposal:{proposal.id}")
        for topic in self._tracked(*PART_TOPICS.get(part, ())):
            await self._resolve(topic)

    def domain_step(self, domain: str) -> Step:
        """How far HouseMind goes in a domain: its furthest skill × room, within the
        administrator's limit. A house rule beats learning (E-104). The same skills × rooms as
        the card 'Klaar voor autonomie?', also what an automation teaches (E-41) – else
        Apparaten en entiteiten said Observeren where the card said Autonoom."""
        assert self.learning is not None
        skills = {s for s, p in self.skill_params.items() if p.get("domain") == domain}
        pairs = (
            set(self.learning.steps)
            | set(self.learning.first_seen)
            | {
                (r.skill, r.scope_id)
                for r in self.rules.rules
                if r.skill and r.scope_type == "area"
            }
            | {p for r in self.automations.readings.values() for p in r.learned}
            | self.rules_possible()  # a room's rules from its devices (E-485)
        )
        pairs = {p for p in pairs if self.rule_there(*p)}  # not a rule whose devices went
        step = max(
            (
                self._skill_step(skill, area)
                for skill, area in pairs
                if skill in skills
                and (self.household is None or self.household.applies(skill, area))
            ),
            default=Step.OBSERVE,
        )
        cap = self.domain_cap(domain)
        return min(step, cap) if cap is not None else step

    async def open_proposals(self) -> list[Proposal]:
        return await self.proposals.open(self.clock.now())

    @property
    def started_at(self) -> datetime:
        """When HouseMind last started: what it keeps in memory begins here (E-500)."""
        return self._started_at

    # -- conversation (H8.2) ------------------------------------------------------------

    async def talk(
        self,
        text: str,
        language: str,
        level: int,
        user_id: str | None,
        status_line: str = "",
        channel: str = "app",
        conversation: Conversation | None = None,
        on_partial: OnText | None = None,
    ) -> dict[str, Any]:
        """The Orb thinks while HouseMind answers (E-159), on every screen in the house –
        also while the house sleeps: someone asked (E-284). ``conversation``: what was said
        just before, for an AI (E-349); ``on_partial``: an AI's answer as it comes (E-159)."""
        self.status.thinking_until = self.clock.now() + timedelta(seconds=30)
        self.status.answering = True
        self._refresh_orb()
        await self._notify()
        try:
            reply = await self._answer_talk(
                text, language, level, user_id, status_line, channel, conversation, on_partial
            )
            assert reply is not None
            return reply
        finally:
            self._show_thinking(answering=True)
            await self._notify()

    async def talk_sure(
        self, text: str, language: str, level: int, user_id: str | None, channel: str = "app"
    ) -> dict[str, Any] | None:
        """Only what she knows for sure – the fixed commands and questions (E-349, B-230) –
        or None: while her question is open, the rest is no answer without an AI."""
        return await self._answer_talk(text, language, level, user_id, "", channel, sure=True)

    async def _answer_talk(
        self,
        text: str,
        language: str,
        level: int,
        user_id: str | None,
        status_line: str,
        channel: str,
        conversation: Conversation | None = None,
        on_partial: OnText | None = None,
        sure: bool = False,
    ) -> dict[str, Any] | None:
        """Local sentence patterns first, then (if on) the AI, else an honest "I don't know"."""
        lang = self.languages.resolve(language)
        t = self.languages.text
        # "Zet licht bij binnenkomen in de keuken op Autonoom" (E-317): she reads it back and
        # asks yes or no; the app and Assist check that an administrator is talking.
        from housemind.brain import stepwish

        step_wish = stepwish.find(self, text, lang)
        if step_wish is not None:
            if not step_wish["found"]:
                return {"text": t(lang, "talk.step.not_found")}
            confirm = t(
                lang, "talk.step.confirm", what=step_wish["what"], step=step_wish["stepName"]
            )
            return {"text": confirm, "confirm_step": step_wish}
        found = intent(text, lang, self.languages)
        if found is None and asks_why_not(text, lang, self.languages):
            found = "why_not" if any(find_target(text, self.model)) else None
        wish = day_wish(text, self._day_words) if found in (None, "status") else None
        if wish is not None:
            kind, offset = wish
            await self.note_day(offset, kind, "said")
            when = "tomorrow" if offset else "today"
            if kind is None:
                return {"text": t(lang, f"days.said.normal_{when}")}
            name = t(lang, f"days.kind.{kind}")
            return {"text": t(lang, f"days.said.{when}", kind=name)}
        moment = self._scene_from_text(text) if found in (None, "status") else None
        if moment is not None:
            scene_area, scene = moment
            await self.activate_scene(scene_area, scene.id, user_id)
            area = self.model.areas.get(scene_area)
            return {
                "text": t(
                    lang,
                    "scenes.said.done",
                    name=self.scene_name(scene, lang),
                    area=area.name if area else "",
                )
            }
        if found == "rule":
            result = await self.understand_rule_text(text, lang)
            if not result["ok"]:
                return {"text": result["message"]}
            reply = t(lang, "talk.rule_confirm", rule=result["read_back"].rstrip("."))
            if result["conflicts"]:
                reply = f"{reply} {t(lang, 'talk.rule_conflict')}"
            return {"text": reply, "confirm_rule": result}
        if found == "charge":
            return {"text": self._charge_wish(text, lang)}
        if found == "battery":
            return {"text": t(lang, "talk.battery_not")}  # E-365: only watched (E-363)
        if found == "why_not":
            return {"text": self._why_not_said(text, lang)}
        if found == "why":
            done = [r for r in self.recent.values() if r.decision.executed]
            if not done:
                return {"text": t(lang, "talk.nothing_done")}
            return {"text": " ".join(self.explain(done[-1].decision, max(level, 2), lang, True))}
        topic = conversation.topic if conversation is not None else None
        if found == "undo" and topic is not None and topic.decision is not None:
            # "Zet dat terug": what she did in this conversation, after your yes (E-421).
            result = await self.undo(topic.decision, user_id)
            topic.decision = None
            return {"text": t(lang, "talk.undone" if result["ok"] else "talk.cannot_undo")}
        if found == "undo":
            done = [
                r
                for r in self.recent.values()
                if r.decision.executed
                and not r.undone
                and r.decision.request.layer not in (Layer.EMERGENCY, Layer.PERSON_ANSWER)
            ]
            if not done:
                return {"text": t(lang, "talk.nothing_to_undo")}
            result = await self.undo(done[-1].id, user_id)
            return {"text": t(lang, "talk.undone" if result["ok"] else "talk.cannot_undo")}
        if found == "erase":
            return {"text": t(lang, "talk.erase_where")}  # never on a spoken command (E-271)
        if found in ("vacation", "guests", "home_again"):
            mode = {"vacation": "vacation", "guests": "guests", "home_again": "home"}[found]
            await self.set_house_mode(mode)  # E-286
            if mode == "vacation" and not self._lived_in_on:
                mode = "vacation_quiet"  # no promise of a lived-in house when it is off (M-17)
            return {"text": t(lang, f"talk.mode_{mode}")}
        if found in ("pause", "resume"):
            part = part_named(text, lang, self.languages)
            if part is not None:
                # One part of the house (E-83, E-292): "pauzeer de verlichting".
                for domain in VOICE_PARTS[part]:
                    await self.pause_domain(
                        domain,
                        60 if found == "pause" else None,
                        user_id,
                        await self.admin_user(user_id),
                    )
                name = t(lang, f"ui.domain.{part}")
                key = "talk.paused_part" if found == "pause" else "talk.resumed_part"
                return {"text": sentence_case(t(lang, key, part=name))}
        if found == "pause":
            await self.pause(60, user_id, await self.admin_user(user_id))
            return {"text": t(lang, "talk.paused")}
        if found == "resume":
            await self.pause(None, user_id)
            for domain in list(self.paused_domains()):
                await self.pause_domain(domain, None, user_id)
            return {"text": t(lang, "talk.resumed")}
        if found == "status":
            # "How are things?" – in the app and through Assist: Vesta answers, in her own
            # words, with what matters for this person now (E-220, E-221).
            who = next((p for p in self._people if p.user_id == user_id), None)
            line = await self.vesta_line(
                user_id, level, who.is_admin if who else False, lang, Moment.TAP
            )
            return {"text": line.text}
        if found == "name":
            # "What's your name?" – she knows who she is, by the name the house gave her (E-242).
            return {"text": self.vesta_says(lang, "about_me", user_id)}
        # What she knows herself, without an AI (E-243 – E-245, B-90): the house, the settings,
        # what waits for you and the house rules.
        admin = (
            bool(who.is_admin)
            if (who := next((p for p in self._people if p.user_id == user_id), None))
            else False
        )
        known = await self.knowing.answer(text, lang, level, user_id, admin, channel)
        if known is not None:
            return known
        if found == "help":
            return {"text": t(lang, "talk.help")}
        if sure:
            return None  # the rest she does not know for sure (E-349)
        # The Help and the manual (E-246): a clear hit answers it, in her words, with Lees meer
        # – only for a real question it fits; one word that is in a title is no hit (B-230).
        # With an AI, the AI words it, with the conversation (B-231).
        hits = self.library.search(text, lang, level=level)
        best = hits[0] if hits else None
        fits = best is not None and self.library.fits(text, lang, best[1])
        ai_on = self.ai.status() == "on"
        if best is not None and fits and best[0] >= STRONG and not ai_on:
            return self._from_help(best[1], lang, user_id, channel)
        talking = conversation is not None and bool(conversation.asked or conversation.turns)
        # A piece of the Help or manual makes it about the house only when it fits the question:
        # one shared word ("open" in a Help question about curtains) is no hit (B-230).
        about = (
            self._about_house(text) or (best is not None and fits and best[0] >= WEAK) or talking
        )
        away = ""  # the AI is not there now: she says so, then what she knows (E-349)
        if ai_on and not about:
            # Not about the house: only the question, and only along the way the administrator
            # chose – never a snapshot of the house (E-15, E-167).
            return {"text": await self._forward(text, lang, spoken=channel == "assist")}
        if self.ai.status() == "on":
            # The house does not know: the question as asked, with the best pieces of the Help
            # and manual, to the AI the administrator chose (E-247, B-95).
            answer = await self.ask(
                text, lang, user_id, [p for _, p in hits], conversation, on_partial
            )
            if answer["ok"] and not answer.get("about_house", True) and not talking:
                # Not about the house: in the app as in Assist, only along the way the
                # administrator chose (E-167).
                return {"text": await self._forward(text, lang, spoken=channel == "assist")}
            if answer["ok"] and answer.get("confirm_rule"):
                # A house rule in your own words (E-420): read back in HouseMind's words, and
                # changed only after your yes (E-87).
                change = answer["confirm_rule"]
                said = t(lang, "talk.rule_change_confirm", rule=change["read_back"])
                return {"text": said, "confirm_rule": change, "via_ai": True}
            if answer["ok"]:
                said = self.vesta_fact(lang, "know_via_ai", user_id, text=answer["message"])
                if answer.get("refused") and not answer.get("proposals"):
                    said = f"{said or answer['message']} {answer['refused'][0]}"
                found_ai: dict[str, Any] = {
                    "text": said or answer["message"],
                    "proposals": answer.get("proposals", []),
                    "via_ai": True,
                    "reply": answer.get("reply", ""),  # her question answered (B-233)
                    "choices": answer.get("choices", []),  # the AI asks back (B-232)
                }
                used = next((p for _, p in hits if p.key == answer.get("section")), None)
                more = self._more(used) if used is not None and channel != "assist" else None
                if more is not None:
                    found_ai["more"] = more
                return found_ai
            away = answer["message"]
        if channel == "assist" and not away:
            return {"text": await self._forward(text, lang)}
        if best is not None and fits and best[0] >= WEAK:
            # Honest, with the part of the manual that comes closest (E-247, E-506).
            more = self._more(best[1])
            title = more["title"] if more is not None else best[1].title
            said = self.vesta_fact(lang, "know_unsure", user_id, title=title)
            return {
                "text": f"{away} {said}".strip(),
                **({"more": more} if more is not None else {}),
                "away": away,
            }
        unknown = self.vesta_fact(lang, "know_unknown", user_id) or t(lang, "talk.unknown")
        return {"text": f"{away} {unknown}".strip(), "away": away}

    def _why_not_said(self, text: str, lang: str) -> str:
        """Why something did not happen, asked in words (E-93): about the device or room it
        names and the kind of device it names ("de verwarming", not a lamp there), with when
        that was – and "Dat weet ik niet" when she tried or stopped nothing there (M-14)."""
        t = self.languages.text
        entity_id, area_id = find_target(text, self.model)
        domains = VOICE_PARTS.get(part_named(text, lang, self.languages) or "", ())
        if entity_id is None:
            candidates = [
                (d.at, e)
                for e, d in self.last_not_done.items()
                if (area_id is None or d.request.area_id == area_id)
                and (not domains or e.split(".", 1)[0] in domains)
            ]
            entity_id = max(candidates)[1] if candidates else None
        decision = self.last_not_done.get(entity_id or "")
        if decision is None:
            return t(lang, "talk.why_not_unknown")
        said = self.why_not(decision.request.entity_id, lang, plain=True)  # Vesta (E-248)
        if self.clock.now() - decision.at < timedelta(minutes=15):
            return said
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        at = decision.at.astimezone(tz)
        days = (self.clock.now().astimezone(tz).date() - at.date()).days
        key = (
            ("talk.why_not_at", "talk.why_not_yesterday")[days] if days < 2 else "talk.why_not_days"
        )
        return t(lang, key, reason=said, time=at.strftime("%H:%M"), days=days)

    def _from_help(
        self, piece: Piece, lang: str, user_id: str | None, channel: str
    ) -> dict[str, Any]:
        """An answer from the Help or the manual (E-246): its core in her words, and Lees meer
        to the part of the manual that tells more; through Assist she says where it is in the
        Help – which is the manual (E-506), so by the part's own title."""
        said = self.vesta_fact(lang, "know_manual", user_id, text=gist(piece.text))
        more = self._more(piece)
        if channel == "assist":
            where = (
                self.vesta_fact(lang, "know_where_help", user_id, title=more["title"])
                if more is not None
                else ""
            )
            return {"text": f"{said} {where}".strip()}
        return {"text": said, **({"more": more} if more is not None else {})}

    def _more(self, piece: Piece) -> dict[str, str] | None:
        """Where Lees meer goes: the part of the manual in the app, with its title – also for a
        Help question, whose short list is not shown any more (E-506). None without a part."""
        place = self.library.place(piece)
        if place is None:
            return None
        return {"kind": "manual", "id": place[0], "title": place[1]}

    def _about_house(self, text: str) -> bool:
        """Locally, without asking anyone: does the question name a room, a device or a word
        about the house (from every language file)?"""
        low = text.lower()
        words = daily_words(self.languages, "house_words")
        if any(re.search(rf"(?<![a-z]){re.escape(w)}", low) for w in words):
            return True
        names = [a.name for a in self.model.areas.values()] + [
            e.name for e in self.model.entities.values() if e.name and len(e.name) >= 4
        ]
        return any(name.lower() in low for name in names if name)

    def _charge_wish(self, text: str, lang: str) -> str:
        """ "Full by 7:00": HouseMind does not charge the car (E-363), but says when it is
        cheapest – before the time said, else in the coming twelve hours (E-365)."""
        t = self.languages.text
        said = t(lang, "talk.charge_not")
        if self.energy is None or self.world is None:
            return said
        now = self.clock.now()
        when, _pct = charge_wish(text, lang, self.languages)
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        local = now.astimezone(tz)
        end = now + timedelta(hours=12)
        if when is not None:
            end = local.replace(hour=when[0], minute=when[1], second=0, microsecond=0)
            if end <= local:
                end += timedelta(days=1)
        plan = self.energy.plan
        slots = plan.slots if plan is not None else self.energy.forecast(self.world.states, now)
        block = cheapest_block(slots, now, end)
        if block is None:
            return said
        start, stop = (moment.astimezone(tz).strftime("%H:%M") for moment in block[:2])
        if when is None:
            return f"{said} {t(lang, 'talk.charge_tip', start=start, end=stop)}"
        by = end.astimezone(tz).strftime("%H:%M")
        return f"{said} {t(lang, 'talk.charge_tip_by', by=by, start=start, end=stop)}"

    # -- AI (H3.7, E-15, E-49, B-17) ------------------------------------------------------

    async def _refresh_model(self) -> None:
        """Home Assistant changed rooms, devices, entities or automations: the house is read
        again (H7.5), then what HouseMind knows of it holds again and what is new is read
        (E-205, E-208) – here, outside the parts that decide, which never touch the AI."""
        await HAChangesPart._refresh_model(self)
        await self._house_changed()

    async def _refresh_labels(self) -> None:
        users = await self.ha.users()
        # Names an administrator added for people not in HA: children, family, a neighbour.
        others = [str(n) for n in await self.setting("private_names") or []]
        # Names in room names, like "Slaapkamer Sophie", are names too (QA 2, B-174).
        others += names_in_rooms(self.model, self.languages)
        if self.world is not None:
            others += [
                str(s.attributes.get("friendly_name") or "")
                for e, s in self.world.states.items()
                if e.startswith("person.")
            ]
        people = [u.name for u in users] + others  # every name stays home (E-15)
        self.ai.labels = Labels.build(self.model, self.area_roles, people)
        brands = {
            d.id: {w.lower() for w in f"{d.manufacturer or ''} {d.model or ''}".split() if w}
            for d in self.model.devices.values()
        }
        # Who lives here, for plain names: no accounts HA made itself, such as a tablet's.
        residents = [u.name for u in users if not u.system_generated] + others
        self.names.reset(residents, brands)  # the house or who lives there changed (E-248)

    def house_facts(self, question: str = "", also: tuple[str, ...] = ()) -> dict[str, Any]:
        """A short, neutral snapshot of the house for one AI question (E-15: no history),
        with only what helps to answer: per room whether someone is there, its climate as one
        value each, what is open, and the devices HouseMind knows and could operate – only
        those that are on, except in a room the question names (QA 2, M-11) or a lookup asks
        about (``also``, E-419); for the house a few energy numbers."""
        assert self.world is not None and self.gate is not None
        snap = self.world.snapshot(self.clock.now())
        labels = self.ai.labels
        asked = normalise(question)
        named = {
            area_id
            for area_id, area in self.model.areas.items()
            if area.name and normalise(area.name) in asked
        } | set(also)
        rooms: dict[str, dict[str, Any]] = {}
        for area_id in self.model.areas:
            label = labels.area(area_id)
            if label is None:
                continue
            occupancy = snap.occupancy.get(area_id)
            rooms[label] = {
                "role": self.area_roles.get(area_id, "other"),
                "occupied": bool(occupancy and occupancy.occupied),
                "dark": bool(snap.dark.get(area_id)),
            }
            warm = self.temperatures.of_room(area_id, self.world.states)  # E-209
            if warm is not None:
                rooms[label]["climate"] = {"temperature": warm}
        climate: dict[str, dict[str, list[float]]] = {}
        for entity_id, entity in self.model.entities.items():
            state = self.world.states.get(entity_id)
            area = self.model.area_of(entity_id)
            room = labels.area(area.id if area else None)
            if state is None or room not in rooms or entity.hidden:
                continue
            if not is_safe_entity(entity_id, entity.device_class):
                continue
            if entity.domain == "sensor":
                if entity.device_class in ROOM_CLIMATE and entity.device_class != "temperature":
                    try:
                        value = float(state.state)
                    except ValueError:
                        continue
                    climate.setdefault(room, {}).setdefault(str(entity.device_class), [])
                    climate[room][str(entity.device_class)].append(value)
                continue  # single meters are not needed to answer about the house
            label = labels.to_label.get(entity_id)
            if label is None:
                continue
            if entity.domain == "binary_sensor":
                if entity.device_class == "window" and state.state == "on":
                    rooms[room].setdefault("open", []).append(label)
                continue  # presence is in "occupied"
            role = self.gate.state.roles.get(entity_id)
            kind = recognise(self.catalog, entity, role).kind
            if kind is None or kind.generic or not kind.actions:
                continue  # only what HouseMind knows and could operate
            if state.state in AI_IDLE_STATES and (area is None or area.id not in named):
                continue  # off, closed or idle: only where the question asks about
            devices = rooms[room].setdefault("devices", {})
            if len(devices) < MAX_DEVICES_PER_ROOM:
                seen = device_facts(entity_id, state)
                function = self.world.functions.get(entity_id) if self.world else None
                if function and function != "unknown":
                    seen["function"] = function  # what it is (E-431): a setting is no switch
                devices[label] = seen
        for room, values in climate.items():
            rooms[room].setdefault("climate", {}).update(
                {k: round(sum(v) / len(v), 1) for k, v in values.items()}
            )
        facts: dict[str, Any] = {
            "house_mode": snap.mode.value,
            "day_type": snap.day_type.value,
            "local_time": snap.local.strftime("%H:%M"),
            "residents_home": snap.residents_home,
            "rooms": rooms,
        }
        if self.energy is not None and self.energy.setup.has_any:
            setup, states = self.energy.setup, self.world.states
            energy: dict[str, float] = {}
            for key, reading in (
                ("grid_w", live(states, setup.readings("grid"))),
                ("solar_w", live(states, setup.readings("solar"))),
                ("battery_pct", power(states, setup.battery_soc)),
            ):
                if reading is not None:
                    energy[key] = round(reading)
            facts["energy"] = energy
        return facts

    async def ask(
        self,
        question: str,
        language: str,
        user_id: str | None,
        manual: list[Piece] | None = None,
        conversation: Conversation | None = None,
        on_partial: OnText | None = None,
    ) -> dict[str, Any]:
        """A free question for the AI. Its suggestions only ever become proposals (B-17). With
        the conversation so far, its rooms count as named (E-349, B-232). The AI may look
        things up (E-419) and change a house rule only after your yes (E-420)."""
        assert self.gate is not None
        lang = self.languages.resolve(language)
        if self.world is None:
            return {"ok": False, "message": self.languages.text(lang, "ai.why.error")}
        names = PlainNames(self.names, lang)  # her answer, in plain names (E-248)
        who = next((p for p in self._people if p.user_id == user_id), None)
        persona = {
            "name": self.vesta_name(lang),
            "formal": bool(who and who.preferences.get("address") == "u"),
        }
        pieces = [
            {"id": p.key, "title": p.title, "text": p.text} for p in (manual or [])
        ]  # general text from the Help and manual, nothing of the house (E-247)
        said = " ".join(text for _, text in conversation.turns) if conversation else ""
        house = {**self.house_facts(f"{question} {said}"), **await self.ai_memory(lang, user_id)}
        numbered = list(self.rules.rules[:AI_MEMORY_MAX])  # as the AI gets them (E-420)
        answer = await self.ai.ask(
            question,
            lang,
            house,
            names,
            persona,
            pieces,
            conversation.for_ai() if conversation else None,
            HouseLookups(self, lang, AI_MEMORY_MAX).all(),
            on_partial,
        )
        if not answer.ok:
            return {
                "ok": False,
                "message": self.languages.text(lang, f"ai.why.{answer.reason or 'error'}"),
            }
        proposals = []
        refused: list[str] = []  # what the gate does not allow, honestly why (E-349, B-233)
        for request in answer.suggestions:
            decision = await self.gate.submit(replace(request, user_id=user_id))
            await self._record(decision)
            if decision.outcome is Outcome.PROPOSED:
                proposal_id = await self.proposals.add(decision)
                if proposal_id is not None:
                    proposals.append(proposal_id)
            elif not decision.executed:
                refused.append(" ".join(self.explain(decision, 2, lang, True)))
        await self._notify()
        return {
            "ok": True,
            "message": sentence_case(answer.text),
            "proposals": proposals,
            "refused": refused,
            "about_house": answer.about_house,
            "section": answer.section,
            "reply": answer.reply,
            "choices": list(answer.choices),
            "confirm_rule": self._rule_change(answer.rule_change, numbered, lang),
        }

    async def talked_about(
        self, conversation: Conversation, proposal_id: int, decision: int | None = None
    ) -> None:
        """What a conversation is about now (E-421): the device, room and action of what she
        asked – after your yes with its decision, which "zet dat terug" undoes."""
        proposal = await self.proposals.get(proposal_id)
        if proposal is None:
            return
        area = proposal.area_id or getattr(self.model.area_of(proposal.entity_id), "id", None)
        conversation.topic = Topic(proposal.entity_id, area, proposal.action, decision)

    def _rule_change(
        self, change: RuleChange | None, numbered: list[HouseRule], lang: str
    ) -> dict[str, Any] | None:
        """A house rule the AI changed (E-420), as a yes or no in HouseMind's own words (E-87):
        the new rule – and the old one on the other days, when it stays there – instead of the
        old one. Only a rule that names an action or a limit, not a skill in a room."""
        if change is None or not 0 < change.number <= len(numbered):
            return None
        old = numbered[change.number - 1]
        if old.id is None or old.skill:
            return None
        new = replace(change.rule, modes=old.modes)
        also: list[HouseRule] = []
        rest = tuple(d for d in (old.days or range(7)) if d not in new.days)
        if change.keep_other_days and new.days and rest:
            also.append(replace(old, id=None, days=rest))
        said = [read_back(r, lang, self.languages, self.model) for r in (*also, new)]
        return {
            "ok": True,
            "rule": new,
            "also": also,
            "replaces": [old.id],
            "read_back": " ".join(said),
            "conflicts": [c.id for c in self.rules.conflicts(new) if c.id != old.id],
            "by_ai": True,
        }

    async def ai_memory(self, lang: str, user_id: str | None) -> dict[str, Any]:
        """What was already said, for an AI (E-384): the open notices for this person with
        their answers, the house rules, and this person's own choices – Niet meer tonen, Later
        until when, no to a vacation – all with labels instead of names (E-15)."""
        assert self.router is not None and self.world is not None
        who = next((p for p in self._people if p.user_id == user_id), None)
        labels, now = self.ai.labels, self.clock.now()
        out: dict[str, Any] = {}
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        rules = [rule_text(r, lang, self.languages, self.model) for r in self.rules.rules]
        if rules:
            out["house_rules"] = [labels.pseudonymise(r) for r in rules[:AI_MEMORY_MAX]]
        notices: list[dict[str, Any]] = []
        later: list[dict[str, str]] = []
        for note in await self.notifications():
            kind, topic, concerns = str(note["kind"]), str(note["topic"]), str(note["concerns"])
            if kind == "learning" or note["text_key"] == "notify.proposal":
                continue  # her questions about proposals go along as her question
            if who is None or (concerns == "admins" and not who.is_admin):
                continue
            if concerns.startswith("user:") and concerns[5:] != who.user_id:
                continue
            params = json.loads(str(note["params"]) or "{}")
            text = labels.pseudonymise(
                notice_text(self.languages, lang, str(note["text_key"]), params)
            )
            hidden = (
                None if kind == "emergency" else self.router.hidden(who.user_id, topic, params, now)
            )
            if hidden == "later":
                until = self.router.later_of(who.user_id, kind_key(topic), now)
                at = until.astimezone(tz).strftime("%H:%M") if until else ""
                later.append({"what": text, "until": at})
            elif hidden is None:
                # No Later for an emergency, nor for information (QA 3, schermen 27).
                answers = ["seen"] if kind in ("emergency", "info") else ["seen", "snooze"]
                if kind != "emergency" and who.is_admin:
                    answers.append("mute")  # Niet meer tonen is for the house (E-412)
                notices.append({"text": text, "kind": kind, "answers": answers})
        if notices:
            out["notices"] = notices[:AI_MEMORY_MAX]
        choices: dict[str, Any] = {}
        muted = self.router.muted()
        shown = [
            labels.pseudonymise(
                self.languages.text(lang, str(m["text_key"]), **(m.get("params") or {}))
            )
            for m in muted.values()
            if m.get("text_key")
        ]
        if shown:
            choices["not_shown"] = shown[:AI_MEMORY_MAX]
        if later:
            choices["later"] = later[:AI_MEMORY_MAX]
        if self.world.modes.vacation_declined:
            choices["no_vacation"] = True
        if choices:
            out["your_choices"] = choices
        return out

    async def _base_load_tip(self, snapshot: WorldState) -> None:
        """Now and then one tip about what the house uses at night (E-77, level 2+)."""
        if self.energy is None or not self.energy.setup.has_any or self.world is None:
            return
        week = snapshot.local.strftime("%G-%V")
        if f"base_load:{week}" in self._raised or snapshot.local.weekday() != 6:
            return
        found = await self.energy.insights(self.clock.now(), self.world.states)
        if found["baseW"] is None or found["baseW"] < 100:
            return
        sign = CURRENCY_SIGNS.get(self.energy.country.currency, self.energy.country.currency)
        cost = found["baseYearCost"]
        await self._raise(
            Kind.INFO,
            f"base_load:{week}",
            "notify.base_load",
            {
                "watt": found["baseW"],
                "kwh": found["baseYearKwh"],
                "cost": f"{sign} {cost}" if cost else "?",
            },
            snapshot,
        )

    async def _ai_budget(self, snapshot: WorldState) -> None:
        """The AI's quarter-hour look: its budget, and reading the house when that is due
        (E-208) – queued beside the core, never waited for (E-414). First, also without AI:
        deriving from behaviour and Vesta's question on Geïnformeerd (E-203, E-207)."""
        await self._understand_quarter(snapshot)
        if self.ai.status() != "on":
            return
        await self._read_house_when_due()
        self.habit_words_when_due()  # your habits per room in words, once a day (E-415)
        self.rule_suggestions_when_due()  # house rules the AI suggests, once a week (E-417)
        state, _, _ = await self.ai.budget_state()
        month = snapshot.local.strftime("%Y-%m")
        if state in ("warn", "empty") and f"ai_budget_{state}:{month}" not in self._raised:
            await self._raise(
                Kind.IMPORTANT,
                f"ai_budget_{state}:{month}",
                f"notify.ai_budget_{state}",
                {},
                snapshot,
                concerns="admins",
            )

    # -- pause (E-83) -------------------------------------------------------------------

    async def pause(
        self, minutes: int | None, who: str | None = None, admin: bool = True
    ) -> int | None:
        """``None`` resumes, ``0`` pauses until resumed, ``-1`` until tomorrow morning. Longer
        than an hour only for an administrator (QA 3, gat 3): anyone else – a resident, a wall
        tablet, Assist, an automation – gets an hour. Who paused goes into the Logboek; a
        pause by someone who is not an administrator is told to the administrators. Returns
        the minutes that hold."""
        assert self.gate is not None
        minutes = self._pause_allowed(minutes, admin)
        until = self._pause_until(minutes)
        self.gate.state.paused_until = until
        await self.db.set_setting("paused_until", until.isoformat() if until else None)
        await self._log_pause("house", until, who, admin)
        self._refresh_orb()
        await self._notify()
        return minutes

    async def pause_domain(
        self, domain: str, minutes: int | None, who: str | None = None, admin: bool = True
    ) -> int | None:
        """Pause one domain (lighting, covers, climate …), E-83. Emergencies keep working. The
        same hour for anyone who is not an administrator."""
        assert self.gate is not None
        minutes = self._pause_allowed(minutes, admin)
        until = self._pause_until(minutes)
        if until is None:
            self.gate.state.paused_domains.pop(domain, None)
        else:
            self.gate.state.paused_domains[domain] = until
        await self.db.set_setting(
            "paused_domains",
            {d: u.isoformat() for d, u in self.gate.state.paused_domains.items()},
        )
        await self._log_pause(domain, until, who, admin)
        await self._notify()
        return minutes

    async def admin_user(self, user_id: str | None) -> bool:
        """Whether Home Assistant knows this person as an administrator."""
        if not user_id:
            return False
        return any(u.id == user_id and u.is_admin for u in await self.ha.users())

    @staticmethod
    def _pause_allowed(minutes: int | None, admin: bool) -> int | None:
        if admin or minutes is None or 0 < minutes <= SHORT_PAUSE:
            return minutes
        return SHORT_PAUSE

    async def _log_pause(
        self, what: str, until: datetime | None, who: str | None, admin: bool
    ) -> None:
        """A pause and a resume in the Logboek, with who (QA 3, gat 3): ``pause`` for the whole
        house, ``pause.<domain>`` for one; the state is until when, or "on" again."""
        now = self.clock.now()
        state = "on" if until is None else until.isoformat()
        await self.db.execute(
            "INSERT INTO part_changes(at, part, state, user_id) VALUES(?,?,?,?)",
            (now.isoformat(), "pause" if what == "house" else f"pause.{what}", state, who),
        )
        if until is not None and not admin and self.world is not None:
            snapshot = self.world.look(now)
            local = until.astimezone(ZoneInfo(self.config.time_zone if self.config else "UTC"))
            await self._raise(
                Kind.INFO,
                f"paused_by:{now.isoformat()}",
                "notify.paused_by_someone",
                {"until": local.strftime("%H:%M")},
                snapshot,
                concerns="admins",
            )

    def paused_domains(self) -> dict[str, datetime]:
        if self.gate is None:
            return {}
        now = self.clock.now()
        return {d: u for d, u in self.gate.state.paused_domains.items() if u > now}

    def _pause_until(self, minutes: int | None) -> datetime | None:
        now = self.clock.now()
        if minutes is None:
            return None
        if minutes == 0:
            return FOREVER
        if minutes < 0:
            # Until the morning: 06:00 today while it is not morning yet (a pause after midnight
            # lasted until the next day's morning), else tomorrow.
            tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
            local = now.astimezone(tz)
            day = local.date() if local.time() < time(6, 0) else (local + timedelta(days=1)).date()
            return datetime.combine(day, time(6, 0), tzinfo=tz)
        return now + timedelta(minutes=minutes)

    def paused_until(self) -> datetime | None:
        if self.gate is None or self.gate.state.paused_until is None:
            return None
        if self.gate.state.paused_until <= self.clock.now():
            return None
        return self.gate.state.paused_until

    # -- status -------------------------------------------------------------------------

    # -- Vesta, the voice of the house (E-220 … E-232, chapter 17) ------------------------

    async def inside_temperature(self) -> float | None:
        """The average of the rooms (each room once), from what measures a room (E-209):
        never a socket's, a chip's or an oven's own temperature."""
        states = self.world.states if self.world else {}
        values = [
            v
            for area_id in self.model.areas
            if self.area_roles.get(area_id) != "outside"
            and (v := self.temperatures.of_room(area_id, states)) is not None
        ]
        return round(sum(values) / len(values), 1) if values else None

    async def active_areas(self) -> tuple[int, int]:
        """Rooms where something happens (E-283): someone is there, or HouseMind did
        something there in the last quarter of an hour."""
        if self.world is None:
            return 0, len(self.model.areas)
        now = self.clock.now()
        snap = self.world.look(now)  # for the screen: only looking (design 70.1)
        active = {a for a, o in snap.occupancy.items() if o.occupied}
        for decision in self.status.thoughts:
            area = decision.request.area_id
            if (
                area is not None
                and decision.outcome is Outcome.EXECUTED
                and now - decision.at <= timedelta(minutes=15)
            ):
                active.add(area)
        return len(active & set(self.model.areas)), len(self.model.areas)


def _clock_time(value: Any) -> time | None:
    """A time like "22:30"; anything else gives None."""
    hours, sep, minutes = str(value or "").partition(":")
    if not sep or not hours.isdigit() or not minutes.isdigit():
        return None
    if int(hours) > 23 or int(minutes) > 59:
        return None
    return time(int(hours), int(minutes))
