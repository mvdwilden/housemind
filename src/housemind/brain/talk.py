"""Conversation without AI (H8.2, E-131): fixed questions and commands from the language files.

The words live in ``talk.words.*`` (comma separated), so a new language needs no code. House
rules are recognised by their first word ("never" …) and always read back before saving.
"""

from __future__ import annotations

import re

from housemind.core.model import HouseModel
from housemind.i18n.catalog import Languages

# Order matters: "why not" before "why", a house rule before everything else.
# Intents that do something; a question about them ("how does …") never does it.
ACTIONS = {"charge", "battery", "undo", "pause", "resume", "vacation", "guests", "home_again"}
INTENTS = (
    "erase",  # never on a spoken command: she says where it is (E-271)
    "vacation",  # the house modes by voice (E-286)
    "guests",
    "home_again",
    "battery",  # the home battery and the car: she explains and gives a tip (E-365)
    "charge",
    "name",
    "why_not",
    "why",
    "undo",
    "pause",
    "resume",
    "status",
    "help",
)
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])(?:[:.]([0-5]\d))?(?!\d|\s*%)")
_PCT = re.compile(r"(\d{2,3})\s*%")


def _words(languages: Languages, lang: str, key: str) -> list[str]:
    raw = languages.text(lang, key)
    if raw == key:
        return []
    return [w.strip().lower() for w in raw.split(",") if w.strip()]


def normalise(text: str) -> str:
    return " ".join(re.sub(r"[?!.,;]+", " ", text.lower()).split())


def intent(text: str, lang: str, languages: Languages) -> str | None:
    t = normalise(text)
    if not t:
        return None
    never = _words(languages, lang, "rules.words.never")
    if any(t.startswith(w + " ") for w in never):
        return "rule"
    # "Hoe werkt de pauze?" is a question about it, never the thing itself.
    explaining = any(
        re.search(rf"(?<!\w){re.escape(w)}(?!\w)", t)
        for w in _words(languages, lang, "talk.words.explain")
    )
    for name in INTENTS:
        if explaining and name in ACTIONS:
            continue
        for word in _words(languages, lang, f"talk.words.{name}"):
            if not re.search(rf"(?<!\w){re.escape(word)}(?!\w)", t):
                continue
            if name == "why" and len(t.split()) > len(word.split()) + 3:
                continue  # "Waarom werkt mijn microfoon niet?" asks about something else
            return name
    return None


def asks_why_not(text: str, lang: str, languages: Languages) -> bool:
    """ "Waarom staat de verwarming in de kinderkamer niet aan?": a why with a not anywhere
    after it (QA 2, M-14). The runtime only takes it when a device or room is named, so
    "Waarom werkt mijn microfoon niet?" still finds the Help."""
    t = normalise(text)
    nots = _words(languages, lang, "talk.words.not")
    for why in _words(languages, lang, "talk.words.why"):
        found = re.search(rf"(?<!\w){re.escape(why)}(?!\w)", t)
        if found and any(
            re.search(rf"(?<!\w){re.escape(w)}(?!\w)", t[found.end() :]) for w in nots
        ):
            return True
    return False


def charge_wish(text: str, lang: str, languages: Languages) -> tuple[tuple[int, int] | None, float]:
    """A time (hour, minute) and a percentage from "charge the car full by 7:00"."""
    t = normalise(text.replace("%", " %"))
    pct = 100.0
    match = _PCT.search(t)
    if match:
        pct = max(10.0, min(100.0, float(match.group(1))))
    elif not any(
        re.search(rf"(?<!\w){re.escape(w)}(?!\w)", t)
        for w in _words(languages, lang, "talk.words.full")
    ):
        pct = 0.0  # no amount said: the usual target
    clock = _TIME.search(text)
    when = (int(clock.group(1)), int(clock.group(2) or 0)) if clock else None
    return when, pct


def find_target(text: str, model: HouseModel) -> tuple[str | None, str | None]:
    """A device or room named in the text: (entity_id, area_id)."""
    t = normalise(text)
    for entity in sorted(model.entities.values(), key=lambda e: -len(e.name or "")):
        if entity.name and normalise(entity.name) in t:
            return entity.entity_id, entity.area_id
    for area in sorted(model.areas.values(), key=lambda a: -len(a.name)):
        if normalise(area.name) in t:
            return None, area.id
    return None, None


def part_named(text: str, lang: str, languages: Languages) -> str | None:
    """A part of the house named in the text ("pauzeer de verlichting"): lighting, covers …"""
    t = normalise(text)
    for part in PARTS:
        for word in _words(languages, lang, f"talk.know.part.{part}"):
            if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", t):
                return part
    return None


def answer_word(text: str, lang: str, languages: Languages) -> str | None:
    """A short answer to her question: "yes", "no" or "not_now" (also through Assist)."""
    t = normalise(text)
    if not t:
        return None
    for phrase in _words(languages, lang, "talk.words.not_now"):
        if t == phrase or t.startswith(phrase + " "):
            return "not_now"
    first = t.split()[0]
    for kind in ("yes", "no"):
        words = {
            w for phrase in _words(languages, lang, f"talk.words.{kind}") for w in phrase.split()
        }
        if first in words and len(t.split()) <= 4:
            return kind
    return None


# The parts one can pause by voice, and the Home Assistant domains of their devices (E-83).
PARTS = {
    "lighting": ("light",),
    "covers": ("cover",),
    "climate": ("climate", "water_heater"),
    "air": ("fan", "humidifier"),
    "media": ("media_player",),
    "cleaning": ("vacuum",),
    "garden": ("lawn_mower",),
}
