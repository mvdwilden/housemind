"""Vesta listens (E-349, chapter 32, B-227, B-228): the conversation of one screen or one Assist
conversation – her open question and the last few sentences – and what an answer to her
question means.

Without an AI she understands only her own choices: the words on a button, or yes and no where
the question has them. She never guesses: what is not one of her choices is no answer (B-228).
With an AI the rest goes to the AI, with this conversation (B-231) and what it was about – the
last device, room and action she asked or did – so that "en in de keuken ook" has a meaning;
"zet dat terug" undoes what she did, with or without an AI (E-421, B-353).
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from housemind.brain.talk import normalise

KEEP = timedelta(minutes=5)  # the conversation, after the last sentence (E-349)
TURNS = 6  # the last sentences an AI gets along (B-232)
SHORT = 6  # words: a longer sentence is no choice
DISTINCT = 4  # letters: a shorter word of a button never chooses it on its own ("het")

# The button a plain "ja" or "nee" is, per kind of question; a "nee" to a proposal is a real
# no (said_no, 20.9), the button "Nu niet" only brings it back later.
YES_NO = {
    "proposal": ("do", "not_now"),
    "automation": ("yes", "no"),
    "restore": ("yes", "no"),
    "vacation": ("yes", "no"),
    "offer": ("yes", "no"),
    "confirm": ("yes", "no"),
    "knowledge": ("yes", "no"),  # Klopt, Klopt niet (B-360)
}


@dataclass(slots=True)
class Asked:
    """Her open question: what kind, its buttons and what they are for."""

    kind: str  # proposal, task, vacation …, offer, confirm, choices, ai_choice
    key: str
    actions: tuple[str, ...]  # her buttons; an AI's own choices are their words
    text: str
    proposal: int | None = None
    notification: int | None = None
    token: str | None = None  # a house rule or step that waits for yes or no


@dataclass(slots=True)
class Topic:
    """What the conversation is about (E-421): the device, room and action she last asked
    about or did, and – once done after your yes – the decision, which "zet dat terug" undoes."""

    entity_id: str
    area_id: str | None
    action: str
    decision: int | None = None


@dataclass(slots=True)
class Conversation:
    """One conversation: only in memory, at most five minutes after the last sentence."""

    asked: Asked | None = None
    turns: deque[tuple[str, str]] = field(default_factory=lambda: deque(maxlen=TURNS))
    at: datetime | None = None
    topic: Topic | None = None

    def said(self, who: str, text: str, now: datetime) -> None:
        if text.strip():
            self.turns.append((who, text.strip()))
        self.at = now

    def ask(self, asked: Asked, now: datetime) -> None:
        self.asked = asked
        self.said("vesta", asked.text, now)

    def current(self, now: datetime) -> Asked | None:
        """Her open question, unless the conversation is over."""
        if self.at is not None and now - self.at > KEEP:
            self.end()
        return self.asked

    def answered(self, key: str | None = None) -> None:
        if self.asked is not None and (key is None or self.asked.key == key):
            self.asked = None

    def end(self) -> None:
        self.asked = None
        self.turns.clear()
        self.at = None
        self.topic = None

    def for_ai(self) -> dict[str, object]:
        """What an AI gets of it (labels instead of names come later, in the AI service)."""
        out: dict[str, object] = {
            "turns": [{"who": who, "text": text} for who, text in self.turns],
        }
        if self.asked is not None:
            out["asked"] = {
                "kind": self.asked.kind,
                "text": self.asked.text,
                "choices": list(self.asked.actions),
            }
        if self.topic is not None:
            out["about"] = {
                "device": self.topic.entity_id,
                "room": self.topic.area_id,
                "action": self.topic.action,
                "done": self.topic.decision is not None,
            }
        return out


def plain(text: str) -> str:
    """Only letters, digits and spaces: "Doe het!" is "doe het", "Pauze…" is "pauze"."""
    return " ".join(re.sub(r"[^\w\s]", " ", normalise(text)).split())


def _has(said: str, phrase: str) -> bool:
    return bool(phrase) and re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", said) is not None


def choice(
    text: str,
    asked: Asked,
    labels: dict[str, str],
    yes: list[str],
    no: list[str],
) -> tuple[str, bool] | None:
    """Which of her buttons this answer is – and whether it was a plain "no" – or None.

    The whole button, or a short answer with its words or starting with a word only that
    button has ("Nooit" for Nooit zo, "Waarom" for Waarom?); else "ja" or "nee" where the
    question has a yes and a no. Two buttons at once, or
    a yes and a no together, is no choice: she asks again rather than guess."""
    said = plain(text)
    words = said.split()
    if not words:
        return None
    buttons = {action: plain(label) for action, label in labels.items() if plain(label)}
    for action, label in buttons.items():
        if label == said:
            return action, False
    if len(words) > SHORT:
        return None
    found = {action: len(label) for action, label in buttons.items() if _has(said, label)}
    if found:
        best = max(found.values())
        top = [action for action, size in found.items() if size == best]
        return (top[0], False) if len(top) == 1 else None
    # A short answer that starts with a word only one button has: "Nooit" for Nooit zo,
    # "Waarom" for Waarom?, "Pauze" for Pauze…; never a yes or no word ("niet", "goed").
    common = {w for phrase in [*yes, *no] for w in plain(phrase).split()}
    for action, label in buttons.items():
        others = {w for a, other in buttons.items() if a != action for w in other.split()}
        own = {w for w in label.split() if len(w) >= DISTINCT and w not in others | common}
        if words[0] in own:
            return action, False
    pair = YES_NO.get(asked.kind)
    if pair is None:
        return None
    said_yes = any(said == p or said.startswith(p + " ") for p in map(plain, yes) if p)
    said_no = any(_has(said, p) for p in map(plain, no) if p)
    if said_yes and not said_no:
        return pair[0], False
    starts_no = any(said == p or said.startswith(p + " ") for p in map(plain, no) if p)
    if starts_no and not any(_has(said, p) for p in map(plain, yes) if p):
        return pair[1], True
    return None
