"""Vesta, the voice of the house (E-220 … E-232, chapter 17, B-77 … B-84).

Vesta is a layer on top of the brain: she decides nothing and does nothing herself. She tells
what the brain already knows – an emergency, a message, a question, what it just did – in her
own words, never reading a message out. First HouseMind chooses what matters for whoever
listens (``choose``): locally, explainably, the same with or without an AI. Only then come the
words (``compose``): her own lines from ``knowledge/vesta/<language>.yaml``, in a je- and a
u-form, never the same line twice in a row.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml

from housemind.brain.names import sentence_case, tidy

WEIGHTS = {"emergency": 100, "important": 70, "info": 40, "learning": 30}
EMERGENCY = 100
NOW = 70  # something that asks for someone now
GOOD_TO_KNOW = 40
QUESTIONS_PER_DAY = {3: 3, 4: 5}  # B-84
STATEMENTS = {1: 1, 2: 2, 3: 2, 4: 2}  # how many messages per level (B-78)
LIGHT_EVERY = 4  # a dry joke at most every fourth calm line
TEXT_FACTS = frozenset({"text", "question"})  # a sentence of the brain inside her line
# Short already, or said as a whole: never cut to one sentence on level 1.
WHOLE = frozenset({"greeting", "listen", "more", "not_heard", "answer_yes", "answer_no"})
_PLACEHOLDER = re.compile(r"{(\w+)}")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class Moment(StrEnum):
    TAP = "tap"  # a tap on the Orb: what matters now (E-225)
    ARRIVAL = "arrival"  # coming home: a short greeting (E-224, B-81)
    BY_ITSELF = "by_itself"  # something that matters, said without being asked


@dataclass(frozen=True, slots=True)
class Message:
    """A message for Vesta: a kind, facts and how much it matters – not a sentence."""

    topic: str  # the kind of line: appliance_done, emergency, question, did …
    weight: int
    facts: dict[str, str]
    key: str  # what makes it the same message: told once is told (E-221)
    concerns: str = "home"  # "home", "admins" or "user:<id>"
    question_kind: str | None = None  # a question's skill: never the same kind twice in a row
    proposal_id: int | None = None


@dataclass(frozen=True, slots=True)
class Listener:
    user_id: str | None
    level: int
    admin: bool = False
    formal: bool = False  # "u" instead of "je" (E-228)
    language: str = "en"


@dataclass(slots=True)
class Memory:
    """What this person heard from Vesta lately."""

    told: set[str] = field(default_factory=set)
    recent: dict[str, list[int]] = field(default_factory=dict)  # topic → variants, newest last
    questions_today: int = 0
    last_question_kind: str | None = None
    budgeted: bool = False  # the person's question budget (E-294) already chose the questions
    calm_lines: int = 0


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    said: tuple[tuple[str, int, str, bool], ...]  # (topic, variant, key, question) to remember
    question: int | None = None  # the proposal she asked about
    listens: bool = False  # she ends with an invitation to speak: then the app listens
    choices: bool = False  # without an AI she ends with her choices, as buttons (E-349)


class Phrases:
    """Vesta's own lines per language, kind and form."""

    def __init__(self, banks: dict[str, dict[str, object]]) -> None:
        self.banks = banks

    @classmethod
    def load(cls, directories: Iterable[Path]) -> Phrases:
        banks: dict[str, dict[str, object]] = {}
        for directory in directories:
            folder = directory / "vesta"
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.yaml")):
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                banks.setdefault(path.stem, {}).update(raw)  # later folders add or replace
        return cls(banks)

    def _bank(self, language: str) -> dict[str, object]:
        return self.banks.get(language) or self.banks.get(language.split("-")[0]) or {}

    def salute(self, language: str, part: str) -> str:
        """A greeting for the time of day: "Goedenavond", "Good morning" …"""
        for bank in (self._bank(language), self._bank("en")):
            salutes = bank.get("salutes")
            if isinstance(salutes, dict) and salutes.get(part):
                return str(salutes[part])
        return ""

    def moment(self, language: str, part: str) -> str:
        """The time of day in her words: "vanochtend", "this evening" …"""
        for bank in (self._bank(language), self._bank("en")):
            moments = bank.get("moments")
            if isinstance(moments, dict) and moments.get(part):
                return str(moments[part])
        return ""

    def name(self, language: str) -> str:
        return str(self._bank(language).get("name") or self._bank("en").get("name") or "Vesta")

    def has_formal(self, language: str) -> bool:
        """Whether this language has a polite form of its own (je/u, du/Sie, tu/vous)."""
        return any(isinstance(f, dict) and "u" in f for f in self._bank(language).values())

    def variants(self, language: str, topic: str, formal: bool) -> list[str]:
        for bank in (self._bank(language), self._bank("en")):
            forms = bank.get(topic)
            if isinstance(forms, dict):
                lines = forms.get("u" if formal else "je") or forms.get("je")
                if lines:
                    return [str(line) for line in lines]
        return []


def _visible(message: Message, listener: Listener) -> bool:
    if message.concerns == "admins":
        return listener.admin
    if message.concerns.startswith("user:"):
        return message.concerns == f"user:{listener.user_id}"
    return True


def choose(
    messages: Iterable[Message],
    listener: Listener,
    memory: Memory,
    moment: Moment,
    offer: bool = False,
) -> list[Message]:
    """What Vesta says now (B-78): an emergency always, alone; then what asks for someone
    now, then what is good to know – as many as the level allows, only what this person did
    not hear yet (on a tap, something that asks for someone now is said again); from level 3
    a question that fits, from level 4 also what she considered. Nothing at all: an empty
    list, and Vesta says she has nothing special."""
    visible = sorted(
        (m for m in messages if _visible(m, listener)), key=lambda m: (-m.weight, m.key)
    )
    emergencies = [m for m in visible if m.weight >= EMERGENCY]
    if emergencies:
        return emergencies[:1]
    level = max(1, min(4, listener.level))
    if offer:
        # Like a butler (E-225): first "what can I do for you?" – only what asks for you
        # now comes before it, once; from level 3 a question that fits may take its place;
        # how the house is, she tells when you ask.
        urgent = [m for m in visible if m.weight >= NOW and m.key not in memory.told]
        if urgent:
            return urgent[:1]
        question = _question(visible, level, memory, lambda m: m.key not in memory.told)
        return [question] if question else []

    def new(m: Message) -> bool:
        return m.key not in memory.told or (moment is Moment.TAP and m.weight >= NOW)

    floor = NOW if level == 1 else GOOD_TO_KNOW
    if moment is Moment.ARRIVAL and level == 1:
        floor = NOW  # level 1 at home-coming: the greeting, and only what asks for you now
    statements = [
        m
        for m in visible
        if m.topic not in ("question", "did", "considered") and m.weight >= floor and new(m)
    ]
    chosen = statements[: STATEMENTS[level]]
    question = _question(visible, level, memory, new)
    if question is not None:
        chosen.append(question)
    if level >= 4:
        considered = next((m for m in visible if m.topic == "considered" and new(m)), None)
        if considered is not None:
            chosen.append(considered)
    if not chosen and level >= 2 and moment is Moment.TAP:
        did = next((m for m in visible if m.topic == "did" and new(m)), None)
        if did is not None:
            chosen.append(did)
    return chosen


def daypart(hour: int) -> str:
    """The part of the day for her words (E-220): morning, afternoon, evening or night."""
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    if 18 <= hour < 23:
        return "evening"
    return "night"


def _question(
    visible: list[Message], level: int, memory: Memory, new: Callable[[Message], bool]
) -> Message | None:
    """A question that fits (B-84): from level 3, a few a day, never the same kind twice."""
    if level < 3:
        return None
    if not memory.budgeted and memory.questions_today >= QUESTIONS_PER_DAY[level]:
        return None
    for m in visible:
        if (
            m.topic == "question"
            and new(m)
            and (m.question_kind is None or m.question_kind != memory.last_question_kind)
        ):
            return m
    return None


def _first_sentence(template: str) -> str:
    """The core of a line, for level 1 – unless the brain's own sentence is in it."""
    if TEXT_FACTS & set(_PLACEHOLDER.findall(template)):
        return template
    return _SENTENCE_END.split(template.strip(), maxsplit=1)[0]


def _pick(
    phrases: Phrases,
    listener: Listener,
    topic: str,
    facts: dict[str, str],
    memory: Memory,
    rng: random.Random,
) -> tuple[str, int] | None:
    """A variant whose facts are all known, not one of the last ones (E-229)."""
    lines = phrases.variants(listener.language, topic, listener.formal)
    fitting = [
        (i, line)
        for i, line in enumerate(lines)
        if all(p in facts for p in _PLACEHOLDER.findall(line))
    ]
    if not fitting:
        return None
    recent = memory.recent.get(topic, [])
    keep_out = set(recent[-min(2, len(fitting) - 1) :]) if len(fitting) > 1 else set()
    fresh = [(i, line) for i, line in fitting if i not in keep_out] or fitting
    index, template = rng.choice(fresh)
    memory.recent.setdefault(topic, []).append(index)  # never twice in a row, this line too
    if listener.level <= 1 and topic not in WHOLE:
        template = _first_sentence(template)
    text = tidy(listener.language, sentence_case(template.format(**facts)))
    return text, index


def compose(
    chosen: list[Message],
    listener: Listener,
    memory: Memory,
    phrases: Phrases,
    moment: Moment,
    rng: random.Random | None = None,
    quiet: str = "calm",
    listen: bool = False,
    context: dict[str, str] | None = None,
    offer: bool = False,
    choices: bool = False,
) -> Line:
    """Vesta's words for what was chosen. ``quiet``: what she says when nothing matters
    ("calm", "sleeping" or "paused"); ``listen``: she ends with an invitation to speak;
    ``choices``: without an AI that invitation is a choice, never an open question (E-349)."""
    rng = rng or random.Random()  # noqa: S311 – only to vary her words
    parts: list[str] = []
    said: list[tuple[str, int, str, bool]] = []
    question: int | None = None

    def add(topic: str, facts: dict[str, str], key: str, asks: bool = False) -> bool:
        # What is true now – the time of day – may colour any line ("rustig vanavond").
        picked = _pick(phrases, listener, topic, {**(context or {}), **facts}, memory, rng)
        if picked is None:
            return False
        text, index = picked
        parts.append(text)
        said.append((topic, index, key, asks))
        return True

    if moment is Moment.ARRIVAL and not any(m.weight >= EMERGENCY for m in chosen):
        add("greeting", {}, "greeting")
    for n, message in enumerate(chosen):
        if n and message.topic != "considered":
            picked = _pick(phrases, listener, "more", {}, memory, rng)
            if picked is not None:
                parts.append(picked[0])
        asks = message.topic == "question"
        if not add(message.topic, message.facts, message.key, asks) and "text" in message.facts:
            add("other", message.facts, message.key, asks)
        if asks and said and said[-1][2] == message.key:
            question = message.proposal_id
    offering = offer and not chosen and quiet in ("calm", "sleeping")
    if offering:
        add("offer_choices" if choices else "offer", {}, "offer")  # "what can I do for you?"
    elif not chosen and moment is not Moment.ARRIVAL:
        topic = quiet
        if quiet == "calm":
            memory.calm_lines += 1
            if memory.calm_lines % LIGHT_EVERY == 0:
                topic = "calm_light"  # now and then a dry joke – only when all is calm
        if not add(topic, {}, topic):
            add("calm", {}, "calm")
    emergency = any(m.weight >= EMERGENCY for m in chosen)
    listens = listen and not emergency
    asked = offering or any(m.topic == "question" for m in chosen)  # it invites already
    chooses = choices and not emergency and question is None
    if (listens or (chooses and listener.level >= 2)) and not asked:  # level 1: the buttons
        topic = "listen_choices" if choices else "listen"
        picked = _pick(phrases, listener, topic, {}, memory, rng)
        if picked is not None:
            parts.append(picked[0])
    return Line(_join(parts), tuple(said), question, listens, chooses)


def _join(parts: list[str]) -> str:
    """Sentences after each other; a lead-in such as "O ja," runs on into the next line."""
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not out:
            out = part
        elif out.endswith((",", ":")):
            out = f"{out} {part[:1].lower() + part[1:] if out.endswith(',') else part}"
        else:
            out = f"{out} {part}"
    return out
