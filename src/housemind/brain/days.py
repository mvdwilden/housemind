"""Days that differ from normal (E-89): working from home, ill, a day off, school holidays.

HouseMind hears it (someone says so), reads it (an all-day event in a Home Assistant calendar –
the title stays in the house, E-15) or sees it (on a workday nobody left at the usual time).
That day it behaves as on such a day – learning files it under "days like today" – without
changing what it learned as normal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any

from housemind.core.model import HouseModel, State
from housemind.world.calendar import DayType

KINDS = ("home_work", "sick", "day_off", "school_holiday", "home_day")
SAID = ("school_holiday", "home_work", "sick", "day_off")  # most specific words first
LEFT_BEFORE = time(10, 30)  # on a normal workday someone has left by now
HISTORY = 5  # workdays to compare with
WORDS = ("normal", "tomorrow", *SAID)


def day_words(languages: Any) -> dict[str, list[str]]:
    """``days.words.*`` from every language file."""
    found: dict[str, list[str]] = {key: [] for key in WORDS}
    for texts in languages.languages.values():
        for key in WORDS:
            raw = texts.get(f"days.words.{key}")
            if raw:
                found[key].extend(w.strip().lower() for w in str(raw).split(",") if w.strip())
    return found


def _has(text: str, words: list[str]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text) for w in words)


def day_wish(text: str, words: dict[str, list[str]]) -> tuple[str | None, int] | None:
    """What someone says about a day: (kind or None for "a normal day", 0 today / 1 tomorrow)."""
    t = " ".join(re.sub(r"[?!.,;]+", " ", text.lower()).split())
    offset = 1 if _has(t, words["tomorrow"]) else 0
    if _has(t, words["normal"]):
        return None, offset
    for kind in SAID:
        if _has(t, words[kind]):
            return kind, offset
    return None


def calendar_kind(
    model: HouseModel, states: dict[str, State], words: dict[str, list[str]]
) -> str | None:
    """An all-day event running now in a Home Assistant calendar, e.g. "Working from home"."""
    for entity in model.entities.values():
        if entity.domain != "calendar":
            continue
        state = states.get(entity.entity_id)
        if state is None or state.state != "on" or not state.attributes.get("all_day"):
            continue
        title = " ".join(str(state.attributes.get("message") or "").lower().split())
        for kind in SAID:
            if _has(title, words[kind]):
                return kind
    return None


@dataclass(slots=True)
class DayNotes:
    notes: dict[str, tuple[str, str]] = field(default_factory=dict)  # day → (kind, source)
    left: dict[str, bool] = field(default_factory=dict)  # workday → someone left early

    def get(self, day: date) -> tuple[str, str] | None:
        return self.notes.get(day.isoformat())

    def resident_left(self, local: datetime) -> None:
        if local.time() < LEFT_BEFORE:
            self.left[local.date().isoformat()] = True

    def looks_like_home_day(self, local: datetime, day_type: DayType, residents_home: int) -> bool:
        """On a workday nobody left by 10:30, while on most recent workdays someone did."""
        today = local.date().isoformat()
        if day_type is not DayType.WORKDAY or local.time() < LEFT_BEFORE:
            return False
        if today in self.left:
            return False  # decided already today
        self.left[today] = False
        for old in sorted(self.left)[: -HISTORY * 3]:
            del self.left[old]
        if residents_home == 0:
            return False
        history = [v for d, v in sorted(self.left.items()) if d < today][-HISTORY:]
        return len(history) >= 3 and sum(history) >= 3
