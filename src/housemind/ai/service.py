"""The AI service: consent, budget, privacy, logging and the few things AI may do (H3.7).

What AI may do in R1
- understand a house rule in free text when the fixed sentence patterns do not (E-87)
- answer a free question about the house and *suggest* actions (B-17, E-110)
- suggest the role of an area whose name HouseMind does not recognise (H5.3)
- read the house (E-204, B-344 – B-346): the role of a room or of a switch whose purpose is
  unknown, and what an automation or script does in plain words – always as a suggestion that
  becomes house knowledge, used for proposing and acting only after a person's Klopt (50.8);
  and its pick where the rules doubt (B-359), which counts only after Klopt
- Vesta slimmer (design 50.4, B-351 – B-354): for a question the AI may look things up in the
  house – read only, at most five times, each through the privacy filter (E-419); it may point
  at a house rule and give the changed one, which HouseMind reads back and keeps only after a
  yes (E-420); it knows what the conversation was about (E-421); and its answer reaches the
  window as it is written, with Vesta's own answer after 20 seconds without one (E-159)

Every outgoing question passes the privacy filter, is logged (visible at level 4, E-169) and
counts towards the monthly budget (E-49). At 100 % HouseMind simply continues on local rules.
Each question names the form of its answer, and where the service can, the AI is held to it
(E-453); HouseMind still checks every answer itself, and of one that was cut off it keeps
what was whole.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, time, timedelta
from typing import Any, cast

import jsonschema

from housemind.ai.keystore import KeyStore
from housemind.ai.privacy import DATA_RULE, Labels, as_data
from housemind.ai.providers import (
    CUT_OFF,
    DEFAULT_MODELS,
    AIProvider,
    AIResult,
    Conversing,
    HomeAssistantAI,
    OnText,
    Tool,
    Turn,
    is_local,
    make_provider,
)
from housemind.brain.filters import PARTS
from housemind.brain.functions import FUNCTIONS, UNKNOWN
from housemind.brain.rules import CATEGORIES, HouseRule
from housemind.core.clock import Clock
from housemind.knowledge.area_roles import ROLE_ORDER
from housemind.memory.database import Database
from housemind.safety.gate import ActionRequest, Layer

_LOGGER = logging.getLogger(__name__)

WARN_AT = 0.8
AI_TASK_PER_DAY = 100  # questions a day to a cloud AI task of Home Assistant (E-422)
# A label that stands for a room, device or person (ai/privacy.py): "kitchen_1", "light_3".
LABEL = re.compile(r"\b[a-z][a-z_]*_\d+\b")
MAX_TOKENS = {
    "rule": 300,
    "ask": 600,
    "roles": 600,
    "general": 400,
    "vesta": 250,
    "switches": 900,
    "functions": 3500,
    "automations": 1500,
    "doubts": 900,
    "habits": 1500,  # a few sentences for every room: 900 was cut off in a real house
    "report": 600,
    "unusual": 300,
    "suggest_rules": 900,
}
# The newest models may think before they answer, and that counts towards the room for the
# answer: room for that too, or a short answer is cut off mid-sentence. The answer itself stays
# short – each question checks its length.
THINKING_ROOM = 2000


def _room(purpose: str) -> int:
    return MAX_TOKENS.get(purpose, 400) + THINKING_ROOM


def _numbers(text: str) -> set[str]:
    """The numbers in a text, without those in labels ("kitchen_1"); "07" is 7."""
    return {str(int(n)) for n in re.findall(r"\d+", LABEL.sub(" ", text))}


def _failed(result: AIResult) -> str:
    """Why there was no answer – and, for one that was cut off, what did come (E-169)."""
    return " ".join(part for part in (result.error or "", result.text[:4000]) if part)


# The tasks an administrator on Expert can give a model of their own (E-49, 20.19), and the
# questions that belong to each. Reading the house is the analysis (E-204).
TASKS = {
    "vesta": ("vesta", "habits", "report", "unusual"),  # words for people: the cheapest model
    "ask": ("ask", "general"),
    "rule": ("rule", "suggest_rules"),
    "analysis": ("roles", "functions", "switches", "automations", "doubts", "image"),
}
TASK_OF = {purpose: task for task, purposes in TASKS.items() for purpose in purposes}
VESTA_SHARE = 0.5  # Vesta's own sentences use at most half the monthly budget
ANALYSIS_SHARE = 0.5  # reading the house too (design 50.6, 50.8); a local AI has no limit
SHARES = {"vesta": VESTA_SHARE, "analysis": ANALYSIS_SHARE}
# Background questions: not too often (E-49); an administrator's "Nu bekijken" may ask now.
MIN_INTERVAL = {
    "roles": timedelta(hours=24),
    "switches": timedelta(hours=24),
    "automations": timedelta(hours=24),
    "doubts": timedelta(hours=24),
}
# Estimated prices per million tokens (input, output); the administrator can override them.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "gemini-flash-lite-latest": (0.30, 2.50),
    "gemini-flash-latest": (1.50, 7.50),
}
UNKNOWN_PRICE = (3.0, 15.0)
ROLES = (*ROLE_ORDER, "other")
# What a switch may be for, in the words an AI gets; a role the catalogue adds later gets
# its own name only (E-204, B-345).
SWITCH_ROLE_WORDS = {
    "light": "it powers a lamp or a light",
    "cooking_off": "the power switch of a cooking appliance (stove, oven, kettle, fryer)",
    "appliance_start": "it starts the program of an appliance (washing machine, dishwasher)",
}
WHY = {"type": "string", "maxLength": 200}
CERTAINTY = {"type": "number", "minimum": 0, "maximum": 1}
LOOKUPS = 5  # lookups for one question at most (E-419)
# Said to an AI that is done looking things up, or that answered without the asked form: once
# more, plainly (E-453). An empty answer after five lookups left Vesta without one.
NUDGE = "No more lookups now: give your answer, as one JSON object in the asked form."
LOOKUP_TIMEOUT = 5.0  # seconds one lookup may take; then it says so and the AI goes on
ANSWER_WAIT = 20.0  # seconds Vesta waits for an answer; then her own (E-159)
# An AI that does not know lookups or answering as it comes says so with one of these: then
# the same question once more without them (E-419).
NOT_SUPPORTED = frozenset({"http_400", "http_404", "http_422", "http_501"})
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # as an AI names a rule's days

RULE_FIELDS: dict[str, Any] = {
    "scope_type": {"enum": ["entity", "area", "house"]},
    "scope": {"type": "string", "maxLength": 64},
    "effect": {"enum": ["never", "limit"]},
    "category": {"enum": sorted(CATEGORIES)},
    "value": {"type": ["number", "null"], "minimum": 5, "maximum": 35},
    "time_from": {"type": ["string", "null"], "pattern": "^[0-2]?[0-9]:[0-5][0-9]$"},
    "time_to": {"type": ["string", "null"], "pattern": "^[0-2]?[0-9]:[0-5][0-9]$"},
    "days": {"type": ["array", "null"], "maxItems": 7, "items": {"enum": list(DAYS)}},
}
RULE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["understood"],
    "properties": {"understood": {"type": "boolean"}, **RULE_FIELDS},
}
RULE_WORDS = (
    "scope_type: entity, area or house; scope: a device or room label from the data, or '*' "
    "for the whole house; effect: 'never' (never do this action) or 'limit' (a maximum "
    "temperature in value); category: one of the categories (on, off, open, close, heat); "
    "value: degrees for a limit; time_from/time_to as HH:MM or null; days: the days it holds "
    f"on, of {', '.join(DAYS)}, or null for every day."
)
ASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["answer"],
    "properties": {
        "answer": {"type": "string", "maxLength": 1200},
        "about_house": {"type": "boolean"},
        "section": {"type": "string", "maxLength": 120},
        # Vesta listens (E-349, B-232): which of her choices an answer means, and choices of
        # the AI's own when something is not clear.
        "reply": {"type": "string", "maxLength": 40},
        "choices": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "minLength": 1, "maxLength": 60},
        },
        "suggestions": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "required": ["device", "action"],
                "properties": {
                    "device": {"type": "string", "maxLength": 64},
                    "action": {"type": "string", "pattern": "^[a-z_]+\\.[a-z_]+$"},
                    "data": {"type": "object"},
                },
            },
        },
        # A house rule in your own words (E-420): which one, and the new one.
        "rule_change": {
            "type": "object",
            "required": ["number", "rule"],
            "properties": {
                "number": {"type": "integer", "minimum": 1, "maximum": 99},
                "rule": {
                    "type": "object",
                    "required": ["effect", "category"],
                    "properties": RULE_FIELDS,
                },
                "keep_other_days": {"type": "boolean"},
            },
        },
    },
}
ROLES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["roles"],
    "properties": {
        "roles": {"type": "object", "additionalProperties": {"enum": list(ROLES)}},
        "certainty": {"type": "object", "additionalProperties": CERTAINTY},
        "why": {"type": "object", "additionalProperties": WHY},
    },
}
SWITCHES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["switches"],
    "properties": {
        "switches": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["role"],
                "properties": {"role": {"type": "string"}, "certainty": CERTAINTY, "why": WHY},
            },
        },
    },
}
FUNCTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["functions"],
    "properties": {
        "functions": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["function"],
                "properties": {
                    "function": {"type": "string"},
                    "certainty": CERTAINTY,
                    "why": WHY,
                },
            },
        },
    },
}
FUNCTION_WORDS = {  # what each function means, for the AI (design 51.2)
    "lamp": "a lamp that lights the room",
    "cover": "a curtain, blind, shutter or garage door",
    "room_climate": "the thermostat or airco of the room",
    "fan": "a fan",
    "player": "a media player or speaker",
    "appliance_power": "the power of an appliance (a plug, a relay)",
    "appliance_start": "starts an appliance's program or a robot",
    "lock": "a lock",
    "other_control": "another control of the room",
    "air_temperature": "the temperature of the room's air",
    "humidity": "the humidity of the room",
    "co2": "the CO2 of the room",
    "air_quality": "the air quality of the room",
    "light_level": "how light the room is",
    "presence": "someone present in the room",
    "motion": "motion in the room",
    "opening": "a door or window of the house",
    "safety": "smoke, gas, carbon monoxide or a leak",
    "outside_temperature": "the temperature outside",
    "weather": "the weather outside",
    "outside_light": "the light outside",
    "device_control": "a control of the device itself, not of the room (a heat pump's water)",
    "device_temperature": "the device's own temperature (a chip, an oven, a battery)",
    "water_temperature": "a water temperature (a tank, a heating circuit)",
    "device_power": "the power a device uses now",
    "device_energy": "the energy a device used in total",
    "device_state": "a state or reading of the device itself (a program, a time left)",
    "status_light": "the device's own status light or LED ring, not a lamp",
    "fault": "a fault or problem of the device",
    "battery": "the device's battery",
    "signal": "the device's signal",
    "update": "an update of the device",
    "connectivity": "the device's connection",
    "setting": "a setting of the device (a child lock, a mode, a sensitivity, a calibration)",
    UNKNOWN: "you are not sure",
}
SURE_ENOUGH = 0.6  # below this an AI's function is "not sure" (design 51.4)
DOUBTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["doubts"],
    "properties": {
        "doubts": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["pick"],
                "properties": {"pick": {"type": "string"}, "certainty": CERTAINTY, "why": WHY},
            },
        },
    },
}
AUTOMATIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["automations"],
    "properties": {
        "automations": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["summary", "part", "take_over"],
                "properties": {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 240},
                    "part": {"enum": list(PARTS)},
                    "take_over": {"enum": ["yes", "partly", "no"]},
                },
            },
        },
    },
}

# What a service that holds the AI to a form does not take (E-453): HouseMind checks it itself.
NOT_IN_FORMS = frozenset(
    {"minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "pattern"}
)
TEXT_FORM: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def _form(schema: Any) -> Any:
    """The form of an answer as a service holds the AI to it (E-453): every object closed – no
    field of its own –, without the limits it does not take. Only for answers of a fixed shape:
    one per label is ``_per_label``."""
    if isinstance(schema, list):
        return [_form(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    form: dict[str, Any] = {}
    for key, value in schema.items():
        if key in NOT_IN_FORMS:
            continue
        if key == "properties":
            form[key] = {name: _form(field) for name, field in value.items()}
        elif key in ("items", "anyOf", "allOf"):
            form[key] = _form(value)
        else:
            form[key] = value
    if "properties" in form:
        form["additionalProperties"] = False
    return form


def _per_label(labels: Sequence[str], answer: dict[str, Any]) -> dict[str, Any]:
    """The form of an answer per label: exactly the labels that were sent, each with its
    answer – no label the AI makes up, none it leaves out (E-453)."""
    form: dict[str, Any] = _form(
        {"type": "object", "properties": dict.fromkeys(labels, answer), "required": list(labels)}
    )
    return form


@dataclass(slots=True)
class AIConfig:
    enabled: bool = False
    # anthropic | gemini | openai | openrouter | ollama | openai_compatible | home_assistant
    provider: str = "anthropic"
    base_url: str | None = None  # for home_assistant: the AI task's entity (E-422)
    model_routine: str | None = None
    model_strong: str | None = None
    monthly_budget: float = 5.0
    price_in: float | None = None
    price_out: float | None = None
    consent_at: str | None = None
    consent_by: str | None = None
    consent_for: str | None = None  # the service (provider and address) the "yes" was for
    task_models: dict[str, str] = field(default_factory=dict)  # Expert: a model per task
    # Camera images described by an AI in the house, after an administrator's own yes (E-424).
    images: bool = False
    images_by: str | None = None

    def service(self) -> str:
        return f"{self.provider}|{self.base_url or ''}"

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> AIConfig:
        known = {k: v for k, v in (raw or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def models(self) -> tuple[str | None, str | None]:
        routine, strong = DEFAULT_MODELS.get(self.provider, (None, None))
        routine = self.model_routine or routine
        return routine, self.model_strong or strong or routine

    def model_for(self, purpose: str, strong: bool = False) -> str | None:
        """The model for one question: the administrator's choice for its task, else Vesta's
        sentences always the cheapest, the rest as asked (E-49)."""
        chosen = self.task_models.get(TASK_OF.get(purpose, ""))
        if chosen:
            return chosen
        routine, strong_model = self.models()
        return routine if purpose == "vesta" or not strong else strong_model


@dataclass(frozen=True, slots=True)
class RuleChange:
    """The AI points at a house rule and gives the new one (E-420); HouseMind reads it back in
    its own words and keeps it only after a yes (E-87)."""

    number: int  # of the house rules as the AI got them: 1 for the first
    rule: HouseRule
    keep_other_days: bool = False  # the old rule stays as it was on the other days


@dataclass(frozen=True, slots=True)
class Lookup:
    """Something an AI may look up in the house for a question (E-419): read only. ``run``
    gets the AI's arguments and gives facts with labels instead of names."""

    tool: Tool
    run: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class AskAnswer:
    ok: bool
    text: str = ""
    suggestions: tuple[ActionRequest, ...] = ()
    reason: str | None = None  # why AI could not help: off, budget, error …
    about_house: bool = True  # False: a general question, not about this home (E-167)
    section: str = ""  # the part of the Help or manual the answer came from (E-247)
    reply: str = ""  # the choice of her open question this answer means (E-349, B-233)
    choices: tuple[str, ...] = ()  # the AI asks back with these, as buttons (B-232)
    rule_change: RuleChange | None = None  # a house rule in your own words (E-420)


@dataclass(frozen=True, slots=True)
class Found:
    """What one question to read the house gave (E-204): per local id the answer, the line in
    the AI log it came from, and why nothing came – off, budget, too_soon, bad_answer …"""

    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    log_id: int | None = None
    reason: str | None = None


@dataclass(slots=True)
class AIService:
    db: Database
    keys: KeyStore
    clock: Clock
    config: AIConfig = field(default_factory=AIConfig)
    labels: Labels = field(default_factory=Labels)
    provider: AIProvider | None = None
    ha: Any = None  # Home Assistant, for its own AI task (E-422)
    # Where an AI task of Home Assistant works: "local", "cloud" or "unknown" (E-422).
    place: Callable[[str], str] = field(default=lambda entity_id: "unknown")
    _last_call: dict[str, datetime] = field(default_factory=dict)
    # Questions (purpose, model) whose form the service did not take: without it (E-453).
    _formless: set[tuple[str, str]] = field(default_factory=set)
    # Answers that came too late (E-159): they finish and are logged, nobody waits for them.
    _late: set[asyncio.Future[AskAnswer]] = field(default_factory=set)

    # -- configuration and consent -------------------------------------------------------

    async def load(self) -> None:
        self.config = AIConfig.from_dict(await self.db.get_setting("ai"))
        self._make_provider()

    def _make_provider(self) -> None:
        self.provider = None
        if not self.config.enabled:
            return
        key = self.keys.get(f"ai:{self.config.provider}")
        try:
            self.provider = make_provider(self.config.provider, self.config.base_url, key, self.ha)
        except ValueError:
            self.provider = None

    async def configure(
        self, config: AIConfig, api_key: str | None = None, keep_key: bool = True
    ) -> None:
        if api_key is not None or not keep_key:
            self.keys.set(f"ai:{config.provider}", api_key)
        if self.provider is not None:
            await self.provider.close()
        self.config = config
        await self.db.set_setting("ai", asdict(config))
        self._make_provider()

    async def give_consent(self, user_id: str | None, yes: bool = True) -> None:
        """A "yes" counts for this service only: a yes for a local AI is no yes for a cloud
        AI. A "no" takes it back."""
        self.config.consent_at = self.clock.now().isoformat() if yes else None
        self.config.consent_by = user_id if yes else None
        self.config.consent_for = self.config.service() if yes else None
        await self.db.set_setting("ai", asdict(self.config))

    @property
    def local(self) -> bool:
        if isinstance(self.provider, HomeAssistantAI):
            return self.place(self.provider.entity_id) == "local"  # unknown counts as cloud
        return self.provider is not None and is_local(self.provider.base_url)

    def status(self) -> str:
        if not self.config.enabled:
            return "off"
        if self.provider is None:
            return "not_configured"
        if not self.config.consent_at or (
            self.config.consent_for is not None and self.config.consent_for != self.config.service()
        ):
            return "needs_consent"
        return "on"

    async def close(self) -> None:
        late = list(self._late)
        for work in late:
            work.cancel()  # an answer that came too late is not waited for when stopping
        if late:
            await asyncio.wait(late, timeout=1)
        if self.provider is not None:
            await self.provider.close()

    # -- budget (E-49) -------------------------------------------------------------------

    def _price(self, model: str) -> tuple[float, float]:
        if self.local:
            return 0.0, 0.0
        if self.config.price_in is not None and self.config.price_out is not None:
            return self.config.price_in, self.config.price_out
        return DEFAULT_PRICES.get(model, UNKNOWN_PRICE)

    def images_possible(self) -> bool:
        """Describing camera images can only be done by Home Assistant's AI task in the house
        (E-424): never by an AI in the cloud."""
        return isinstance(self.provider, HomeAssistantAI) and self.local

    def images_on(self) -> bool:
        return self.config.images and self.images_possible() and self.status() == "on"

    async def describe_image(self, camera: str, label: str, language: str) -> str | None:
        """What a camera's image shows, in one short sentence (E-424). Only to an AI in the
        house, and only after an administrator's own yes; HouseMind refuses it otherwise. The
        image goes as a media source of Home Assistant – it never leaves the house – and the
        log keeps no image, only which camera."""
        if not self.images_on() or not isinstance(self.provider, HomeAssistantAI):
            return None
        model, reason = await self._ready("image", False)
        if reason is not None:
            return None
        instruction = (
            "You describe the still image of a home's camera for a message to the people who "
            f"live there, in the language with code '{language}': one short sentence about what "
            "is to be seen, such as a parcel at the door or one person at the gate. Never guess "
            "who someone is and say nothing about faces. "
            'Reply as {"text": "..."}.'
        )
        system = f"{instruction}\nReply with one JSON object only."
        found = await self.ha.ai_task(
            self.provider.entity_id,
            "HouseMind",
            system,
            [
                {
                    "media_content_id": f"media-source://camera/{camera}",
                    "media_content_type": "image/jpeg",
                }
            ],
        )
        text = found if isinstance(found, str) else json.dumps(found) if found is not None else ""
        result = AIResult(
            ok=found is not None, text=text, error=None if found is not None else "unreachable"
        )
        await self._log("image", model, f"[{label}]", result, system)
        parsed = _json_object(text) if found is not None else None
        said = parsed.get("text") if parsed else None
        if not isinstance(said, str) or not 0 < len(said.strip()) <= 200:
            return None
        return said.strip()

    async def _asked_today(self, now: datetime) -> int:
        """Questions that went to Home Assistant's AI task since midnight (UTC)."""
        start = datetime.combine(now.date(), time(0), tzinfo=now.tzinfo)
        row = await self.db.fetchone(
            "SELECT count(*) AS n FROM ai_log WHERE provider = 'home_assistant' AND at >= ?",
            (start.isoformat(),),
        )
        return int(row["n"]) if row else 0

    async def spent_this_month(self, now: datetime | None = None) -> float:
        now = now or self.clock.now()
        start = datetime.combine(now.date().replace(day=1), time(0), tzinfo=now.tzinfo)
        row = await self.db.fetchone(
            "SELECT coalesce(sum(cost), 0) AS spent FROM ai_log WHERE at >= ?",
            (start.isoformat(),),
        )
        return float(row["spent"]) if row else 0.0

    async def spent_by_task(self, now: datetime | None = None) -> dict[str, float]:
        """What each task cost this month (E-49): Vesta's sentences, questions, house rules,
        analysis."""
        now = now or self.clock.now()
        start = datetime.combine(now.date().replace(day=1), time(0), tzinfo=now.tzinfo)
        rows = await self.db.fetchall(
            "SELECT purpose, coalesce(sum(cost), 0) AS spent FROM ai_log WHERE at >= ? "
            "GROUP BY purpose",
            (start.isoformat(),),
        )
        out = dict.fromkeys(TASKS, 0.0)
        for row in rows:
            task = TASK_OF.get(str(row["purpose"]))
            if task is not None:
                out[task] += float(row["spent"])
        return out

    async def budget_state(self) -> tuple[str, float, float]:
        spent = await self.spent_this_month()
        budget = max(0.0, self.config.monthly_budget)
        if self.local:
            return "ok", spent, budget
        if spent >= budget:
            return "empty", spent, budget
        if spent >= WARN_AT * budget:
            return "warn", spent, budget
        return "ok", spent, budget

    # -- one question ---------------------------------------------------------------------

    async def _call(
        self,
        purpose: str,
        instruction: str,
        data: dict[str, Any],
        strong: bool = False,
        form: dict[str, Any] | None = None,
        whole_part: bool = False,
    ) -> tuple[dict[str, Any] | None, str | None]:
        parsed, reason, _ = await self._ask(purpose, instruction, data, strong, form, whole_part)
        return parsed, reason

    async def _ready(self, purpose: str, strong: bool) -> tuple[str, str | None]:
        """May a question go now – AI on, budget left, its task's share, not too soon – and to
        which model; else why not."""
        if self.status() != "on" or self.provider is None:
            return "", self.status()
        now = self.clock.now()
        if (await self.budget_state())[0] == "empty":
            return "", "budget"
        task = TASK_OF.get(purpose, "")
        if task in SHARES and not self.local:
            # Vesta's own sentences and reading the house each use at most their share of the
            # month's budget; then her own words, and the reading waits for next month.
            spent = (await self.spent_by_task()).get(task, 0.0)
            if spent >= SHARES[task] * max(0.0, self.config.monthly_budget):
                return "", "budget"
        if isinstance(self.provider, HomeAssistantAI) and not self.local:
            # Costs HouseMind cannot see: a number of questions a day instead (E-422).
            if await self._asked_today(now) >= AI_TASK_PER_DAY:
                return "", "day_limit"
        last = self._last_call.get(purpose)
        interval = MIN_INTERVAL.get(purpose)
        if interval is not None and self.local:
            interval = interval / 4  # an AI in the house may read the house more often (E-423)
        if interval is not None and last is not None and now - last < interval:
            return "", "too_soon"
        self._last_call[purpose] = now
        if isinstance(self.provider, HomeAssistantAI):
            return self.provider.entity_id, None  # Home Assistant chooses the model (E-422)
        model = self.config.model_for(purpose, strong) or ""
        return model, None if model else "not_configured"

    async def _ask(
        self,
        purpose: str,
        instruction: str,
        data: dict[str, Any],
        strong: bool = False,
        form: dict[str, Any] | None = None,
        whole_part: bool = False,
    ) -> tuple[dict[str, Any] | None, str | None, int | None]:
        """One question, with the line in the AI log it got (E-169). ``form``: the answer's
        form, which a service that can holds the AI to – one that does not take it gets the
        question again without it, and from then on (E-453). ``whole_part``: of an answer
        that was cut off, what was whole, with the reason ``cut_off``."""
        model, reason = await self._ready(purpose, strong)
        if reason is not None or self.provider is None:
            return None, reason or "not_configured", None
        system = f"{instruction}\n\n{DATA_RULE}\nReply with one JSON object only."
        user = as_data(data)
        shaped = (
            form is not None
            and getattr(self.provider, "forms", False) is True
            and (purpose, model) not in self._formless
        )
        if shaped:
            result = await self.provider.complete(system, user, model, _room(purpose), form=form)  # type: ignore[call-arg]
        else:
            result = await self.provider.complete(system, user, model, _room(purpose))
        log_id = await self._log(purpose, model, user, result, system)
        if shaped and not result.ok and result.error in NOT_SUPPORTED:
            self._formless.add((purpose, model))  # this service does not take the form
            result = await self.provider.complete(system, user, model, _room(purpose))
            log_id = await self._log(purpose, model, user, result, system)
        if not result.ok:
            if whole_part and result.error == CUT_OFF:
                part = _whole_part(result.text)
                if part is not None:
                    return part, CUT_OFF, log_id
            return None, result.error or "error", log_id
        parsed = _json_object(result.text)
        if parsed is None:
            return None, "bad_answer", log_id
        return parsed, None, log_id

    async def _converse(
        self,
        purpose: str,
        instruction: str,
        data: dict[str, Any],
        lookups: Sequence[Lookup],
        partial: _Partial | None,
        strong: bool = False,
    ) -> tuple[dict[str, Any] | None, str | None]:
        """One question in rounds (E-419, E-159): the AI may look things up first – at most
        five times, each within its time limit, all of it through the privacy filter – and its
        answer may come as it is written. Each round is a line in the AI log (E-169). An AI
        that knows neither gets the same question once more without them."""
        model, reason = await self._ready(purpose, strong)
        if reason is not None or self.provider is None:
            return None, reason or "not_configured"
        provider = cast(Conversing, self.provider)
        system = f"{instruction}\n\n{DATA_RULE}\nReply with one JSON object only."
        sent = as_data(data)
        turns = [Turn("user", sent)]
        tools = tuple(lookup.tool for lookup in lookups)
        named = {lookup.tool.name: lookup for lookup in lookups}
        used = 0
        nudged = False  # asked once more for the answer in its form (E-453)
        for _round in range(LOOKUPS + 4):  # the lookups, the answer, once more, without
            final = used >= LOOKUPS or nudged
            if final and turns[-1].role == "tool" and not turns[-1].text:
                turns[-1] = replace(turns[-1], text=NUDGE)  # no more lookups: the answer now
            result = await provider.converse(
                system, turns, model, _room(purpose), tools, final, partial
            )
            asked = [{"lookup": c.name, "with": c.args} for c in result.calls]
            told = f"{result.text} {json.dumps(asked)}".strip() if asked else result.text
            await self._log(purpose, model, sent, replace(result, text=told), system)
            if not result.ok and result.error in NOT_SUPPORTED and tools and len(turns) == 1:
                tools = ()  # this AI cannot look things up: the snapshot of now (E-419)
                continue
            if not result.ok and result.error in NOT_SUPPORTED and partial is not None:
                partial = None  # nor answer as it comes: all at once, as before
                continue
            if not result.ok:
                return None, result.error or "error"
            if not result.calls or not tools:
                parsed = _json_object(result.text)
                if parsed is not None:
                    return parsed, None
                if nudged:
                    return None, "bad_answer"
                # No answer in its form – nothing, or only words: once more, asked plainly.
                nudged = True
                if result.text.strip():
                    turns += [Turn("assistant", result.text), Turn("user", NUDGE)]
                else:
                    last = turns[-1]
                    turns[-1] = replace(last, text=f"{last.text}\n\n{NUDGE}".strip())
                if partial is not None:
                    partial.reset()
                continue
            found: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
            for call in result.calls:
                if call.name not in named:
                    answer: dict[str, Any] = {"error": "no such lookup"}
                elif used >= LOOKUPS:
                    answer = {"error": f"at most {LOOKUPS} lookups for one question"}
                else:
                    used += 1
                    answer = await self._look(named[call.name], call.args)
                found.append((call.name, call.args, answer))
            pairs = zip(result.calls, found, strict=True)
            results = tuple((call, as_data(answer)) for call, (_, _, answer) in pairs)
            turns += [Turn("assistant", result.text, result.calls), Turn("tool", results=results)]
            sent = as_data({"lookups": [{"lookup": n, "with": a, "found": f} for n, a, f in found]})
            if partial is not None:
                partial.reset()  # a new round: the answer is still to come
        return None, "bad_answer"

    async def _look(self, lookup: Lookup, args: dict[str, Any]) -> dict[str, Any]:
        """One lookup: read only, within its time limit; what it gives goes through the
        privacy filter once more – names to labels, codes out (E-15)."""
        try:
            found = await asyncio.wait_for(lookup.run(dict(args)), LOOKUP_TIMEOUT)
        except TimeoutError:
            return {"error": "timeout"}
        except Exception:  # a lookup never takes Vesta down
            _LOGGER.exception("AI lookup %s failed", lookup.tool.name)
            return {"error": "failed"}
        return cast(dict[str, Any], self._private(found))

    def _private(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.labels.pseudonymise(value)
        if isinstance(value, dict):
            return {str(k): self._private(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self._private(v) for v in value]
        return value

    def ask_now(self, task: str) -> None:
        """An administrator asked to look now ("Nu bekijken", E-207): the questions of this
        task may go at once, not a day after the last; the budget still holds."""
        for purpose in TASKS.get(task, ()):
            self._last_call.pop(purpose, None)

    async def _log(
        self, purpose: str, model: str, sent: str, result: AIResult, instruction: str | None = None
    ) -> int:
        """One line in the AI log (E-169): the instruction – why HouseMind asks and what answer
        it wants –, the data it sent, and what came back or why not."""
        price_in, price_out = self._price(model)
        cost = (result.input_tokens * price_in + result.output_tokens * price_out) / 1_000_000
        return await self.db.execute_insert(
            "INSERT INTO ai_log(at, purpose, provider, model, instruction, sent, received, "
            "input_tokens, output_tokens, cost, ok) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.clock.now().isoformat(),
                purpose,
                self.config.provider,
                model,
                instruction,
                sent,
                result.text[:4000] if result.ok else _failed(result),
                result.input_tokens,
                result.output_tokens,
                cost,
                int(result.ok),
            ),
        )

    async def log_forward(self, agent: str, question: str, answer: str | None) -> None:
        """A question forwarded to another conversation agent: visible on level 4 (E-169)."""
        await self._log(
            "forward", agent, question, AIResult(ok=answer is not None, text=answer or "")
        )

    async def log(self, limit: int = 50) -> list[dict[str, Any]]:
        """What was sent and received (level 4, E-169)."""
        rows = await self.db.fetchall("SELECT * FROM ai_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # -- house rules in free text (E-87) -------------------------------------------------

    async def parse_rule(self, text: str) -> tuple[HouseRule | None, str | None]:
        data = {
            "rule_text": self.labels.pseudonymise(text),
            "rooms": sorted(
                label for local, label in self.labels.to_label.items() if local.startswith("area:")
            ),
            "devices": sorted(
                label
                for local, label in self.labels.to_label.items()
                if not local.startswith("area:")
            ),
            "categories": sorted(CATEGORIES),
        }
        instruction = (
            "You turn one house rule, written by a resident, into a structured rule. "
            f"Fields: understood (bool); {RULE_WORDS} "
            "If the text is not a rule about these rooms and devices, reply "
            '{"understood": false}.'
        )
        parsed, reason = await self._call("rule", instruction, data, form=_form(RULE_SCHEMA))
        if parsed is None:
            return None, reason
        try:
            jsonschema.validate(parsed, RULE_SCHEMA)
        except jsonschema.ValidationError:
            return None, "bad_answer"
        if not parsed.get("understood"):
            return None, "not_understood"
        rule = self._rule(parsed, text)
        return (rule, None) if rule is not None else (None, "not_understood")

    def _rule(self, fields: dict[str, Any], text: str) -> HouseRule | None:
        """A structured rule from an AI, its labels back to the rooms and devices of this
        house; None when it is about one the house does not have, or no rule HouseMind keeps."""
        if "effect" not in fields or "category" not in fields:
            return None
        scope_type = fields.get("scope_type", "house")
        scope_label = str(fields.get("scope", "*"))
        if scope_type == "area":
            scope_id = self.labels.area_id(scope_label)
        elif scope_type == "entity":
            scope_id = self.labels.entity_id(scope_label)
        else:
            scope_id = "*"
        if scope_id is None:
            return None
        effect = fields["effect"]
        if effect == "limit" and (fields["category"] != "heat" or fields.get("value") is None):
            return None
        days = tuple(sorted({DAYS.index(d) for d in fields.get("days") or ()}))
        return HouseRule(
            scope_type=scope_type,
            scope_id=scope_id,
            effect=effect,
            category=fields["category"],
            value=float(fields["value"]) if fields.get("value") is not None else None,
            time_from=_time(fields.get("time_from")),
            time_to=_time(fields.get("time_to")),
            source_text=text,
            days=days if len(days) < len(DAYS) else (),
        )

    # -- a free question about the house (H8.2) ------------------------------------------

    async def ask(
        self,
        question: str,
        language: str,
        house: dict[str, Any],
        local_names: Any,  # local id -> name; Vesta gets plain names (E-248)
        persona: dict[str, Any] | None = None,
        manual: list[dict[str, str]] | None = None,
        conversation: dict[str, Any] | None = None,
        lookups: Sequence[Lookup] = (),
        on_partial: OnText | None = None,
    ) -> AskAnswer:
        """A question to the AI, waited for at most 20 seconds (E-159): after that Vesta
        answers herself, and what the AI still says is only logged. ``on_partial`` gets her
        answer as it is written, in plain names."""
        partial = _Partial(self.labels, local_names, on_partial) if on_partial else None
        work = asyncio.ensure_future(
            self._answer(
                question,
                language,
                house,
                local_names,
                persona,
                manual,
                conversation,
                lookups,
                partial,
            )
        )
        try:
            return await asyncio.wait_for(asyncio.shield(work), ANSWER_WAIT)
        except TimeoutError:
            if partial is not None:
                partial.quiet = True  # its words no longer reach the window
            self._late.add(work)
            work.add_done_callback(_forget(self._late))
            return AskAnswer(ok=False, reason="timeout")
        except asyncio.CancelledError:
            work.cancel()
            raise

    async def _answer(
        self,
        question: str,
        language: str,
        house: dict[str, Any],
        local_names: Any,
        persona: dict[str, Any] | None,
        manual: list[dict[str, str]] | None,
        conversation: dict[str, Any] | None,
        lookups: Sequence[Lookup],
        partial: _Partial | None,
    ) -> AskAnswer:
        """The question as asked (names as labels, E-15), the house facts and at most three
        pieces of the Help and manual – general text, nothing of the house (E-247, B-95). With
        the conversation so far – her open question, the last sentences and what it was about,
        also as labels – and to the stronger model: Vesta with an AI is free and smart (E-349,
        B-232, E-421). An AI that can look things up gets the lookups too (E-419)."""
        data: dict[str, Any] = {"question": self.labels.pseudonymise(question), "house": house}
        asked = (conversation or {}).get("asked")
        if conversation:
            data["conversation"] = {
                "turns": [
                    {"who": str(turn["who"]), "text": self.labels.pseudonymise(str(turn["text"]))}
                    for turn in conversation.get("turns", [])
                ],
            }
            if isinstance(asked, dict):
                data["conversation"]["asked"] = {
                    "kind": str(asked["kind"]),
                    "text": self.labels.pseudonymise(str(asked["text"])),
                    "choices": [str(c) for c in asked["choices"]],
                }
            about = self._about(conversation.get("about"))
            if about:
                data["conversation"]["about"] = about
        if manual:
            data["manual"] = [
                {"id": m["id"], "title": m["title"], "text": m["text"][:1500]} for m in manual[:3]
            ]
        conversing = bool(lookups) and getattr(self.provider, "tools", False) is True
        streaming = partial is not None and getattr(self.provider, "streams", False) is True
        who = persona or {}
        form = "the polite form (u, Sie, vous)" if who.get("formal") else "the casual form"
        instruction = (
            f"You are {who.get('name', 'Vesta')}, the voice of this home: a helper in the "
            "house, warm, human and short, with now and then a dry joke – never with something "
            f"important. Speak with {form}. Answer the question in at most "
            f"three short sentences in the language with code '{language}', using only the "
            "facts in the data. You cannot operate anything yourself; you may suggest at most "
            "three actions as {device, action, data}, e.g. light.turn_on. A person decides: "
            "the home asks about each suggestion itself, so do not ask it in your answer. "
            'If the question is not about this home, set "about_house" to false and say '
            "briefly that you only know about the home. "
            'The "manual" pieces, if any, are from the app\'s Help and manual: use them when '
            'they answer the question, and put the id of the one you used in "section". '
            'The "conversation", if any, is what was said just before (oldest first). If it '
            'has an "asked" question and the question is an answer to it, put the one of its '
            '"choices" it means in "reply" ("none" if it is no answer to it); the person\'s '
            "choice is then carried out by the home, not by you. If something is not clear, you "
            'may ask back with at most four short "choices" the person can tap. '
            'Its "about", if any, is the device, room and action the conversation was about, '
            "so 'and in the kitchen too' or 'that one too' means the same action there; "
            '"done" says the home already did it. '
            'The "house" may have "notices" (open messages for this person, with the "answers" '
            'they can give), "house_rules" and "your_choices" (what this person switched off, '
            "put off until a time, or said no to). Never suggest anything against a house rule "
            "or against what the person said no to, and do not bring up again what they "
            "switched off or put off. "
            'If the person asks to change one of the "house_rules", put in "rule_change" its '
            '"number" (1 for the first) and the whole changed "rule" with these fields: '
            f'{RULE_WORDS} Set "keep_other_days" to true when the old rule stays as it was on '
            "the days the new one does not hold on. The home reads it back and asks the person, "
            "so do not ask it in your answer. "
            'Reply as {"answer": "...", "about_house": true, "section": "", "reply": "none", '
            '"choices": [], "suggestions": [...]}.'
        )
        if conversing:
            instruction += (
                " You may look things up in the home with the functions – read only, at most "
                f"{LOOKUPS} times – when the data does not tell enough: a room now, why something "
                "happened, what a device or room did in the last days, what the home plans, "
                "the house rules, energy. Use the labels of the data. Then answer as above."
            )
        if conversing or streaming:
            parsed, reason = await self._converse(
                "ask",
                instruction,
                data,
                lookups if conversing else (),
                partial if streaming else None,
                strong=True,
            )
        else:
            parsed, reason = await self._call("ask", instruction, data, strong=True)
        if partial is not None:
            partial.quiet = True  # the answer is there: it replaces what came so far
        if parsed is None:
            return AskAnswer(ok=False, reason=reason)
        try:
            jsonschema.validate(parsed, ASK_SCHEMA)
        except jsonschema.ValidationError:
            return AskAnswer(ok=False, reason="bad_answer")
        suggestions = []
        for item in parsed.get("suggestions", []):
            entity_id = self.labels.entity_id(str(item["device"]))
            action = str(item["action"])
            if entity_id is None or action.split(".", 1)[0] != entity_id.split(".", 1)[0]:
                continue  # unknown device or a mismatching action: ignored
            suggestions.append(
                ActionRequest(
                    Layer.AI,
                    entity_id,
                    action,
                    dict(item.get("data") or {}),
                    reason="ai",
                    created_at=self.clock.now(),
                    facts={"question": question[:200]},
                )
            )
        change = parsed.get("rule_change")
        rule = self._rule(change["rule"], question) if change else None
        text = self.labels.restore(str(parsed["answer"]), local_names)
        choices = tuple(self.labels.restore(str(c), local_names) for c in parsed.get("choices", []))
        reply = str(parsed.get("reply") or "")
        return AskAnswer(
            ok=True,
            text=text,
            suggestions=tuple(suggestions),
            about_house=bool(parsed.get("about_house", True)),
            section=str(parsed.get("section") or ""),
            reply=reply if isinstance(asked, dict) and reply in asked["choices"] else "",
            choices=tuple(c for c in choices if not LABEL.search(c)),
            rule_change=(
                RuleChange(int(change["number"]), rule, bool(change.get("keep_other_days")))
                if change and rule is not None
                else None
            ),
        )

    def _about(self, about: Any) -> dict[str, Any]:
        """What the conversation was about (E-421), as labels: a device only when an AI may
        know it at all (E-15)."""
        if not isinstance(about, dict):
            return {}
        out: dict[str, Any] = {}
        device = self.labels.to_label.get(str(about.get("device") or ""))
        room = self.labels.area(about.get("room"))
        if device:
            out["device"] = device
        if room:
            out["room"] = room
        if out and about.get("action"):
            out |= {"action": str(about["action"]), "done": bool(about.get("done"))}
        return out

    async def general(self, question: str, language: str) -> AskAnswer:
        """A general question from Assist (E-167, E-169): only the question goes along – no
        house data, names or history – and the answer can never operate anything."""
        data = {"question": self.labels.pseudonymise(question)}
        instruction = (
            "Answer this general question in at most three short sentences, in the language "
            f"with code '{language}'. Do not ask for personal details. "
            'Reply as {"answer": "..."}.'
        )
        answer_form = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }
        parsed, reason = await self._call("general", instruction, data, form=_form(answer_form))
        if parsed is None:
            return AskAnswer(ok=False, reason=reason)
        answer = parsed.get("answer")
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 1200:
            return AskAnswer(ok=False, reason="bad_answer")
        return AskAnswer(ok=True, text=answer.strip(), about_house=False)

    # -- Vesta's words (E-222, B-79) ----------------------------------------------------

    async def vesta(
        self,
        persona: dict[str, Any],
        messages: list[dict[str, Any]],
        language: str,
        local_names: Any,  # local id -> name; Vesta gets plain names (E-248)
    ) -> str | None:
        """Vesta's line in more human words: only the chosen messages go along, with labels
        instead of names (E-15). The answer may not add a device, a place or a person, and
        is short; anything else gives None – then Vesta uses her own words."""
        sent = [
            {
                "kind": m["kind"],
                "important": bool(m.get("important")),
                "facts": {k: self.labels.pseudonymise(str(v)) for k, v in m["facts"].items()},
            }
            for m in messages
        ]
        known = {w for m in sent for v in m["facts"].values() for w in LABEL.findall(v)}
        form = "the polite form (u, Sie, vous)" if persona.get("formal") else "the casual form"
        instruction = (
            f"You are {persona.get('name', 'Vesta')}, the voice of a home: a helper in the "
            "house, warm, human and short, with now and then a dry joke – never with something "
            "important. Speak to one person, in the language with code "
            f"'{language}', using {form}. Say only the messages in the data, in your own words "
            f"and in this order, at most {persona.get('sentences', 2)} short sentences each. "
            "Always speak in whole, natural, flowing sentences like a person in a conversation, "
            "never a curt reply of one or two words. "
            "Do not add facts, devices, places, people, actions or promises that are not in "
            "the data, and do not list anything else. A question stays a question. "
            'Reply as {"text": "..."}.'
        )
        data = {"moment": persona.get("moment", "tap"), "messages": sent}
        parsed, _ = await self._call("vesta", instruction, data, form=TEXT_FORM)
        text = parsed.get("text") if parsed else None
        if not isinstance(text, str) or not text.strip() or len(text) > 600:
            return None
        if set(LABEL.findall(text)) - known:
            return None  # a device, place or person that was not in the messages
        restored = self.labels.restore(text.strip(), local_names)
        return None if LABEL.search(restored) or "[device]" in restored else restored

    # -- learning and explaining in words (E-415, E-418, design 50.3) ----------------------

    async def habit_words(
        self, rooms: dict[str, list[str]], language: str, local_names: Any
    ) -> dict[str, tuple[str, str]]:
        """What HouseMind learned per room as a short story (E-415): the AI gets the sentences
        of the core with labels for the rooms and devices, and writes. A story with a time or a
        number the sentences do not have, or with a place or device that is not in them, is not
        used (E-162). Per room label: (the story with labels, the story in plain names)."""
        sent = {
            label: [self.labels.pseudonymise(line) for line in lines]
            for label, lines in rooms.items()
        }
        instruction = (
            "You help HouseMind, the assistant of a home, tell the people who live there what it "
            "learned. The data has, per room (a label), sentences HouseMind made itself about "
            "what it learned and does there; their times and numbers are right. For each room "
            "write a short story of at most three sentences, in the language with code "
            f"'{language}', in plain everyday words, as HouseMind speaking ('I …'). Use only "
            "what the sentences say: keep every time and number exactly as it is, in digits, and "
            "add no time, number, device, place, person or habit that is not in them. Keep the "
            'labels as they are. Reply as {"rooms": {"<room label>": "..."}}.'
        )
        form = {
            "type": "object",
            "properties": {"rooms": _per_label(list(sent), {"type": "string"})},
            "required": ["rooms"],
        }
        parsed, _ = await self._call(
            "habits", instruction, {"rooms": sent}, form=_form(form), whole_part=True
        )
        found = parsed.get("rooms") if parsed else None
        out: dict[str, tuple[str, str]] = {}
        for label, text in (found if isinstance(found, dict) else {}).items():
            if label not in sent or not isinstance(text, str) or not 0 < len(text.strip()) <= 700:
                continue
            plain = self._own_words(text.strip(), " ".join(sent[label]), local_names, {label})
            if plain is not None:
                out[label] = (text.strip(), plain)
        return out

    async def report_words(
        self, sentences: list[str], habits: list[str], language: str, local_names: Any
    ) -> str | None:
        """The weekly report in plain words (E-418): the AI gets the report's sentences – and
        the stories of the rooms where something was learned (E-415) – and writes one short
        paragraph. Every number comes from the core; a number of its own and it is not used."""
        sent = [self.labels.pseudonymise(line) for line in sentences]
        data: dict[str, Any] = {"sentences": sent}
        if habits:
            data["learned_per_room"] = habits
        instruction = (
            "You write the weekly report of HouseMind, the assistant of a home, for the people "
            "who live there, in the language with code "
            f"'{language}'. The data has the report as sentences HouseMind made itself; their "
            "numbers are right. Write it as one short, friendly paragraph of at most five "
            "sentences in plain everyday words, starting with what stands out most. Keep every "
            "number exactly as it is, in digits, and add no number, device, place, person or "
            'fact that is not in the data. Keep the labels as they are. Reply as {"text": "..."}.'
        )
        parsed, _ = await self._call("report", instruction, data, form=TEXT_FORM)
        text = parsed.get("text") if parsed else None
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 900:
            return None
        return self._own_words(text.strip(), " ".join(sent + habits), local_names, set())

    async def unusual(self, facts: dict[str, Any], language: str, local_names: Any) -> str | None:
        """Something out of the ordinary the core saw (E-416): the AI thinks of a likely reason
        and words the question. The hours are the core's and must be in it; the question stays
        whether HouseMind should switch it off."""
        instruction = (
            "HouseMind, the assistant of a home, noticed something out of the ordinary. The data "
            "says what: a light (a label) that has been on for some hours in a room (a label) "
            "where nobody has been for a while, and the time of day. In the language with code "
            f"'{language}', write two or three short sentences to the people who live there: "
            "first what HouseMind saw, with the number of hours exactly as in the data, in "
            "digits; then one likely, everyday reason; then ask whether HouseMind should switch "
            "it off. Add no number, device, place or person that is not in the data, and keep "
            'the labels as they are. Reply as {"text": "..."}.'
        )
        parsed, _ = await self._call("unusual", instruction, facts, form=TEXT_FORM)
        text = parsed.get("text") if parsed else None
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 400:
            return None
        if str(facts.get("hours")) not in _numbers(text):
            return None  # it has to say what HouseMind saw
        return self._own_words(text.strip(), json.dumps(facts), local_names, set())

    async def suggest_rules(
        self,
        learned: dict[str, list[str]],
        rules: list[str],
        refused: list[str],
        language: str,
        local_names: Any,
    ) -> list[tuple[HouseRule, str]]:
        """House rules the AI proposes from what was learned (E-417): at most three, as
        structured rules – only 'never' and a maximum temperature, never one that makes
        HouseMind act – about the rooms and devices of this house. HouseMind checks each: a
        time or a value the data does not have, or a room it does not know, and it is not
        asked. A person decides. Per rule: the rule, and why in plain names."""
        data = {
            "learned_per_room": {
                label: [self.labels.pseudonymise(line) for line in lines]
                for label, lines in learned.items()
            },
            "house_rules": [self.labels.pseudonymise(rule) for rule in rules],
            "refused": [self.labels.pseudonymise(text) for text in refused],
            "rooms": sorted(
                label for local, label in self.labels.to_label.items() if local.startswith("area:")
            ),
            "devices": sorted(
                label
                for local, label in self.labels.to_label.items()
                if not local.startswith("area:")
            ),
            "categories": sorted(CATEGORIES),
        }
        instruction = (
            "You help HouseMind, the assistant of a home, suggest house rules to the people who "
            "live there. The data has what HouseMind learned per room, the house rules there "
            "already are, and what the residents refused before. Suggest at most three new "
            "house rules that fit what was learned and that the residents would likely want – "
            "never one that is already a house rule, goes against one, or was refused before. "
            f"Each as a structured rule: {RULE_WORDS} Add 'why': one short sentence in the "
            f"language with code '{language}' saying what in the learned facts it comes from. "
            "Use only times and numbers that are in the data, and keep the labels as they are. "
            'Reply as {"suggestions": [...]}, or {"suggestions": []} when nothing fits well.'
        )
        one = {
            "type": "object",
            "properties": {**RULE_FIELDS, "why": {"type": "string"}},
            "required": ["effect", "category", "why"],
        }
        form = {
            "type": "object",
            "properties": {"suggestions": {"type": "array", "items": one}},
            "required": ["suggestions"],
        }
        parsed, _ = await self._call("suggest_rules", instruction, data, form=_form(form))
        found = parsed.get("suggestions") if parsed else None
        source = json.dumps(data, ensure_ascii=False)
        out: list[tuple[HouseRule, str]] = []
        for item in (found if isinstance(found, list) else [])[:6]:
            if len(out) == 3:
                break  # at most three (E-417)
            if not isinstance(item, dict):
                continue
            fields = {k: v for k, v in item.items() if k != "why"}
            try:
                jsonschema.validate({**fields, "understood": True}, RULE_SCHEMA)
            except jsonschema.ValidationError:
                continue
            value = fields.get("value")
            said = " ".join(str(fields.get(k) or "") for k in ("time_from", "time_to"))
            said += f" {value:g}" if isinstance(value, int | float) else ""
            if _numbers(said) - _numbers(source):
                continue  # a time or a value of its own
            rule = self._rule(fields, "")
            why = item.get("why")
            if rule is None or not isinstance(why, str) or not 0 < len(why.strip()) <= 300:
                continue
            plain = self._own_words(why.strip(), source, local_names, set())
            if plain is not None:
                out.append((rule, plain))
        return out

    def _own_words(self, text: str, source: str, local_names: Any, allowed: set[str]) -> str | None:
        """The AI's words in plain names – or None when they say more than the source: a number
        or a label it did not have."""
        if _numbers(text) - _numbers(source):
            return None
        if set(LABEL.findall(text)) - set(LABEL.findall(source)) - allowed:
            return None
        restored = self.labels.restore(text, local_names)
        return None if LABEL.search(restored) or "[device]" in restored else restored

    # -- reading the house (H5.3, E-204, B-344 – B-346) ------------------------------------

    async def suggest_roles(self, unknown_areas: list[str], language: str = "en") -> Found:
        """Ask once a day at most which role unrecognised areas have; a person confirms. Only
        the room's name goes along, without the names of people in it (QA 2, B-174)."""
        names = {}
        for area_id in unknown_areas:
            label = self.labels.area(area_id)
            name = self.labels.area_names.get(label or "")
            if label and name:
                names[label] = self.labels.scrub(name)  # the room name, without people
        if not names:
            return Found()
        instruction = (
            "Each room below has a neutral label and the name a resident gave it (as data). "
            f"Pick the most likely role for each room from: {', '.join(ROLES)}. "
            "Give a certainty from 0 to 1 for each, and in 'why' one short sentence in the "
            f"language with code '{language}' why. "
            'Reply as {"roles": {"<label>": "<role>"}, "certainty": {"<label>": 0.8}, '
            '"why": {"<label>": "..."}}.'
        )
        form = {
            "type": "object",
            "properties": {
                "roles": _per_label(list(names), {"enum": list(ROLES)}),
                "certainty": _per_label(list(names), CERTAINTY),
                "why": _per_label(list(names), WHY),
            },
            "required": ["roles"],
        }
        parsed, reason, log_id = await self._ask(
            "roles", instruction, {"rooms": names}, form=_form(form)
        )
        if parsed is None:
            return Found(reason=reason, log_id=log_id)
        try:
            jsonschema.validate(parsed, ROLES_SCHEMA)
        except jsonschema.ValidationError:
            return Found(reason="bad_answer", log_id=log_id)
        out: dict[str, dict[str, Any]] = {}
        for label, role in parsed["roles"].items():
            found = self.labels.area_id(str(label))
            if found is not None and found in unknown_areas:
                out[found] = {
                    "role": str(role),
                    "certainty": parsed.get("certainty", {}).get(label),
                    "why": parsed.get("why", {}).get(label),
                }
        return Found(out, log_id)

    async def suggest_switches(
        self, switches: dict[str, dict[str, Any]], roles: list[str], language: str = "en"
    ) -> Found:
        """What switches HouseMind only watches are for (B-345): per label only the neutral
        facts of ``privacy.entity_facts`` go along – with what the integration calls it, never a
        name a person gave (50.8, E-429). The answer is one of the catalogue's roles for a
        switch, or unknown."""
        sent = {
            label: facts
            for entity_id, facts in switches.items()
            if (label := self.labels.to_label.get(entity_id)) is not None
        }
        if not sent:
            return Found()
        kinds = "; ".join(f"{r}: {SWITCH_ROLE_WORDS.get(r, r.replace('_', ' '))}" for r in roles)
        instruction = (
            "Each switch below has a neutral label and only neutral facts (as data): its "
            "domain and class, the integration, the make and model of its device, the "
            "kinds of the other entities on that device, and what the integration itself "
            "calls it ('name', 'key'). Say for each what it most likely "
            f"switches, as one of these roles – {kinds} – or 'unknown' when you are not sure. "
            "A setting of the device itself (a child lock, a status light, a mode) is "
            "'unknown'. In a house, a smart switch or relay in a room most often powers the "
            "lamp of that room; sometimes another device, such as a fan, a heater or a pump "
            "('unknown' when none of the roles fits that). Give a certainty from 0 to 1, and "
            f"in 'why' one short sentence in the language with code '{language}'. "
            'Reply as {"switches": {"<label>": {"role": "...", "certainty": 0.8, '
            '"why": "..."}}}.'
        )
        answer = {
            "type": "object",
            "properties": {
                "role": {"enum": [*roles, "unknown"]},
                "certainty": CERTAINTY,
                "why": WHY,
            },
            "required": ["role", "certainty", "why"],
        }
        form = {
            "type": "object",
            "properties": {"switches": _per_label(list(sent), answer)},
            "required": ["switches"],
        }
        parsed, reason, log_id = await self._ask(
            "switches", instruction, {"switches": sent}, form=_form(form)
        )
        if parsed is None:
            return Found(reason=reason, log_id=log_id)
        try:
            jsonschema.validate(parsed, SWITCHES_SCHEMA)
        except jsonschema.ValidationError:
            return Found(reason="bad_answer", log_id=log_id)
        out: dict[str, dict[str, Any]] = {}
        for label, answer in parsed["switches"].items():
            entity_id = self.labels.entity_id(str(label))
            if entity_id in switches:
                role = str(answer["role"])  # an answer outside the list: unknown
                out[entity_id] = {**answer, "role": role if role in roles else "unknown"}
        return Found(out, log_id)

    async def suggest_functions(
        self, entities: dict[str, dict[str, Any]], language: str = "en"
    ) -> Found:
        """Every entity's function where the rules cannot tell (E-429, design 51.4): per label
        only the neutral facts of ``privacy.entity_facts`` – with what the integration calls it,
        never a name a person gave. The answer per entity: a function of the fixed list, or
        unknown when it is not in the list, not sure enough, or for a label not sent."""
        own = {f"entity_{i}": entity_id for i, entity_id in enumerate(entities, 1)}
        sent = {label: entities[entity_id] for label, entity_id in own.items()}
        if not sent:
            return Found()
        kinds = "; ".join(f"{name}: {words}" for name, words in FUNCTION_WORDS.items())
        instruction = (
            "Each entity of a smart home below has a neutral label and only neutral facts (as "
            "data): its domain and class, unit and category, the integration, the make and "
            "model of its device, the kinds of the other entities on that device, and what the "
            "integration itself calls it ('name', 'key'). Say for each what it is, as exactly "
            f"one of these functions – {kinds}. A device's own light, a child lock, a mode or a "
            "calibration is never a lamp or a power switch. Answer 'unknown' when you are not "
            "sure. Give a certainty from 0 to 1, and in 'why' at most ten words, always in the "
            f"language with code '{language}' – never in another language. "
            'Reply as {"functions": {"<label>": {"function": "...", "certainty": 0.8, '
            '"why": "..."}}}.'
        )
        answer = {
            "type": "object",
            "properties": {
                "function": {"enum": list(FUNCTION_WORDS)},
                "certainty": CERTAINTY,
                "why": WHY,
            },
            "required": ["function", "certainty", "why"],
        }
        form = {
            "type": "object",
            "properties": {"functions": _per_label(list(sent), answer)},
            "required": ["functions"],
        }
        parsed, reason, log_id = await self._ask(
            "functions", instruction, {"entities": sent}, form=_form(form), whole_part=True
        )
        if parsed is None:
            return Found(reason=reason, log_id=log_id)
        try:
            jsonschema.validate(parsed, FUNCTIONS_SCHEMA)
        except jsonschema.ValidationError:
            return Found(reason="bad_answer", log_id=log_id)
        out: dict[str, dict[str, Any]] = {}
        for label, answer in parsed["functions"].items():
            if label not in own:
                continue
            function = str(answer["function"]).strip()
            sure = answer.get("certainty")
            if function not in FUNCTIONS or (isinstance(sure, int | float) and sure < SURE_ENOUGH):
                function = UNKNOWN
            out[own[label]] = {**answer, "function": function}
        return Found(out, log_id, reason)  # cut_off: the ones that were whole (E-453)

    async def explain_automations(
        self, items: dict[str, dict[str, Any]], language: str = "en"
    ) -> Found:
        """What automations and scripts the rules cannot place do, in plain words (B-346):
        only their structure goes along, with labels instead of entities and without their
        names (``privacy.plain_structure``). Only to show: AI never changes one (E-03)."""
        if not items:
            return Found()
        own = {
            f"{entity_id.split('.', 1)[0]}_{i}": entity_id for i, entity_id in enumerate(items, 1)
        }
        sent = {label: items[entity_id] for label, entity_id in own.items()}
        instruction = (
            "Each automation or script of a home below has a neutral label and only its "
            "structure (as data): what starts it, what must be true, and what it does, with "
            "neutral labels for rooms and devices. For each, say in one short sentence in the "
            f"language with code '{language}' what it does for the people in the home; use "
            "the labels of rooms and devices exactly. Also give the part of the home it is "
            f"about, one of: {', '.join(PARTS)}; and in 'take_over' whether a smart home that "
            "learns lighting, curtains and climate by itself could do the same: yes, partly "
            "or no. "
            'Reply as {"automations": {"<label>": {"summary": "...", "part": "...", '
            '"take_over": "..."}}}.'
        )
        form = {
            "type": "object",
            "properties": {
                "automations": _per_label(
                    list(sent),
                    AUTOMATIONS_SCHEMA["properties"]["automations"]["additionalProperties"],
                )
            },
            "required": ["automations"],
        }
        parsed, reason, log_id = await self._ask(
            "automations", instruction, {"automations": sent}, form=_form(form)
        )
        if parsed is None:
            return Found(reason=reason, log_id=log_id)
        try:
            jsonschema.validate(parsed, AUTOMATIONS_SCHEMA)
        except jsonschema.ValidationError:
            return Found(reason="bad_answer", log_id=log_id)
        out = {
            own[label]: dict(answer)
            for label, answer in parsed["automations"].items()
            if label in own
        }
        return Found(out, log_id)

    async def pick_doubts(self, doubts: dict[str, dict[str, Any]], language: str = "en") -> Found:
        """Where the rules are unsure (design 50.2, B-359): which sensor measures a room, is a
        contact a door or window of the house, is a light only a device's status light. Per
        question only the kind of the room and per device the neutral facts of
        ``privacy.switch_facts``, with labels – never a name (50.8). The answer per key: the
        chosen entity, True or False, or None when the AI is not sure."""
        sent: dict[str, dict[str, Any]] = {}
        own: dict[str, str] = {}
        for i, (key, doubt) in enumerate(doubts.items(), 1):
            question = {k: v for k, v in doubt.items() if k != "options"}
            if "options" in doubt:
                question["options"] = {
                    label: facts
                    for entity_id, facts in doubt["options"].items()
                    if (label := self.labels.to_label.get(entity_id)) is not None
                }
            own[f"doubt_{i}"] = key
            sent[f"doubt_{i}"] = question
        if not sent:
            return Found()
        instruction = (
            "Each question below is about a home, as data with neutral labels and only neutral "
            "facts: the kind of the room, and per device its domain and class, the "
            "integration, its make and model, the kinds of the other entities on it, and what "
            "the integration itself calls it ('name', 'key'). "
            "'room_temperature': pick the label of the option that most likely measures the "
            "air of the room, not a device's own warmth. 'house_opening': is the contact a "
            "door or window of the house ('yes'), or of an appliance, a vehicle or furniture "
            "('no')? 'one_door_room': is a room of this kind, with this one door contact and no "
            "motion sensor, a small room with one door that people go into and shut behind "
            "them, such as a toilet ('yes'), or one with another way in, or that people do not "
            "shut behind them ('no')? "
            "'status_light': is the light only the status light or ring of a device "
            "('yes'), or a lamp that lights the room ('no')? Answer 'unknown' when you are not "
            "sure. Give a certainty from 0 to 1, and in 'why' one short sentence in the "
            f"language with code '{language}'. "
            'Reply as {"doubts": {"<label>": {"pick": "...", "certainty": 0.8, "why": "..."}}}.'
        )
        answer = {
            "type": "object",
            "properties": {"pick": {"type": "string"}, "certainty": CERTAINTY, "why": WHY},
            "required": ["pick", "certainty", "why"],
        }
        form = {
            "type": "object",
            "properties": {"doubts": _per_label(list(sent), answer)},
            "required": ["doubts"],
        }
        parsed, reason, log_id = await self._ask(
            "doubts", instruction, {"questions": sent}, form=_form(form)
        )
        if parsed is None:
            return Found(reason=reason, log_id=log_id)
        try:
            jsonschema.validate(parsed, DOUBTS_SCHEMA)
        except jsonschema.ValidationError:
            return Found(reason="bad_answer", log_id=log_id)
        out: dict[str, dict[str, Any]] = {}
        for label, answer in parsed["doubts"].items():
            if label not in own:
                continue
            key, said = own[label], str(answer["pick"]).strip()
            pick: str | bool | None = None
            if "options" in doubts[key]:
                entity_id = self.labels.entity_id(said)
                pick = entity_id if entity_id in doubts[key]["options"] else None
            elif said.lower() in ("yes", "no"):
                pick = said.lower() == "yes"
            out[key] = {**answer, "pick": pick}  # anything else: not sure
        return Found(out, log_id)

    def plain(self, text: Any, local_names: Any) -> str | None:
        """An AI's sentence for a person: labels back to the local names; None when it is no
        text or a label is left that is not of this house."""
        if not isinstance(text, str) or not text.strip():
            return None
        restored = self.labels.restore(text.strip(), local_names)
        return None if LABEL.search(restored) or "[device]" in restored else restored


class _Partial:
    """Her answer as it is written (E-159): only the words of its "answer" so far, whole
    words, labels back to plain names – never a label that is not of this house – and quiet
    once the answer is there or the 20 seconds are over."""

    def __init__(self, labels: Labels, local_names: Any, out: OnText) -> None:
        self.labels, self.local_names, self.out = labels, local_names, out
        self.written = ""
        self.said = ""
        self.quiet = False

    def reset(self) -> None:
        self.written = ""

    async def __call__(self, piece: str) -> None:
        self.written += piece
        found = partial_answer(self.written)
        if self.quiet or not found or " " not in found.strip():
            return
        words = found[: found.rstrip().rfind(" ")].strip()  # the last word may not be whole
        plain = self.labels.restore(words, self.local_names)
        left = LABEL.search(plain)
        if left is not None:
            plain = plain[: left.start()].rstrip()
        plain = plain[:1].upper() + plain[1:]
        if len(plain) > len(self.said):
            self.said = plain
            await self.out(plain)


_ANSWER = re.compile(r'"answer"\s*:\s*"')
_ESCAPED = {"n": " ", "r": "", "t": " ", "b": "", "f": ""}


def partial_answer(written: str) -> str | None:
    """The "answer" of a JSON reply that is still being written, so far (E-159)."""
    match = _ANSWER.search(written)
    if match is None:
        return None
    out: list[str] = []
    i = match.end()
    while i < len(written) and written[i] != '"':
        if written[i] != "\\":
            out.append(written[i])
            i += 1
            continue
        if i + 1 >= len(written):
            break  # half an escape: the rest comes with the next piece
        code = written[i + 1]
        if code == "u":
            digits = written[i + 2 : i + 6]
            if len(digits) < 4:
                break
            with contextlib.suppress(ValueError):
                out.append(chr(int(digits, 16)))
            i += 6
            continue
        out.append(_ESCAPED.get(code, code))
        i += 2
    return "".join(out)


def _forget(late: set[asyncio.Future[AskAnswer]]) -> Callable[[asyncio.Future[AskAnswer]], None]:
    """An answer that came too late is done: out of the list, and its error only logged."""

    def done(work: asyncio.Future[AskAnswer]) -> None:
        late.discard(work)
        if not work.cancelled() and work.exception() is not None:
            _LOGGER.debug("A late AI answer failed: %s", type(work.exception()).__name__)

    return done


def _json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in a reply (models sometimes add words or code fences)."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _whole_part(text: str) -> dict[str, Any] | None:
    """What was whole of a JSON answer that was cut off (E-453): everything up to the last
    value that was finished, closed again – so the rooms or devices it did answer count."""
    start = text.find("{")
    if start < 0:
        return None
    ends: list[tuple[int, str]] = []  # where a value ended, and what then was still open
    open_: list[str] = []
    in_text = escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_text:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_text = False
                ends.append((i + 1, "".join(reversed(open_))))
            continue
        if char == '"':
            in_text = True
        elif char in "{[":
            open_.append("}" if char == "{" else "]")
        elif char in "}]" and open_:
            open_.pop()
            if not open_:
                return _json_object(text[start : i + 1])  # it was whole after all
            ends.append((i + 1, "".join(reversed(open_))))
    for end, closing in reversed(ends[-60:]):
        try:
            value = json.loads(text[start:end] + closing)
        except json.JSONDecodeError:
            continue  # it ended on a name, not on a value
        return value if isinstance(value, dict) else None
    return None


def _time(value: Any) -> time | None:
    if not value:
        return None
    hours, minutes = str(value).split(":")
    return time(int(hours) % 24, int(minutes))
