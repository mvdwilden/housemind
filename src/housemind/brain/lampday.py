"""Vandaag met deze lamp (E-500, design 73.14): what happened to one lamp today, and who went
first – the day's rhythm, someone by hand, a rule at its moment, safety. Home Assistant's
recorder keeps no brightness of lamps, so HouseMind keeps it itself: one day back, in memory,
from what it sees happen (since its own start)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from housemind.core.model import Source

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from housemind.core.model import StateChange
    from housemind.safety.gate import Decision

KEEP = timedelta(hours=24)
MOST = 40  # per lamp: a day of a busy lamp
CAUSED_WITHIN = timedelta(seconds=30)  # HouseMind's own decision that explains a change


@dataclass(frozen=True, slots=True)
class LampEvent:
    """One change of a lamp: when, how it stood after it, and who did it."""

    at: datetime
    on: bool
    pct: float | None
    kelvin: float | None
    who: str  # rhythm, hand, rule, safety, tried, answer, automation, other, housemind
    skill: str | None = None
    stand: str | None = None


def who_did(change: StateChange, decision: Decision | None) -> tuple[str, str | None, str | None]:
    """Who went first for this change: (who, skill, stand)."""
    if change.source in (Source.PERSON, Source.PHYSICAL):
        return "hand", None, None
    if change.source in (Source.AUTOMATION, Source.SIGNAL):
        return "automation", None, None
    if change.source is Source.OTHER_SYSTEM:
        return "other", None, None
    if change.source is not Source.HOUSEMIND or decision is None:
        return ("housemind" if change.source is Source.HOUSEMIND else "other"), None, None
    return who_did_housemind(decision)


def who_did_housemind(decision: Decision) -> tuple[str, str | None, str | None]:
    """Which of HouseMind's own reasons it was: safety, a Proef, a yes, the rhythm, a rule."""
    r = decision.request
    stand = r.facts.get("stand") if isinstance(r.facts.get("stand"), str) else None
    layer = r.layer.value
    if layer == "emergency":
        return "safety", None, None
    if r.facts.get("tried"):
        return "tried", None, stand
    if layer == "person_answer":
        return "answer", r.skill, stand
    if r.facts.get("day_rhythm"):
        return "rhythm", r.skill, None
    return "rule", r.skill, stand


class LampDay:
    """The lamps' day, kept as it happens."""

    def __init__(self) -> None:
        self.events: dict[str, deque[LampEvent]] = {}

    def saw(
        self,
        change: StateChange,
        recent: Iterable[Decision],
        now: datetime,
        groups: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        """A lamp changed: what it is now, and who did it."""
        new = change.new
        if new is None or not change.entity_id.startswith("light.") or not new.available:
            return
        on = new.state == "on"
        raw = new.attributes.get("brightness")
        warm = new.attributes.get("color_temp_kelvin")
        pct = raw * 100 / 255 if on and isinstance(raw, int | float) else None
        kelvin = float(warm) if on and isinstance(warm, int | float) else None
        kept = self.events.setdefault(change.entity_id, deque(maxlen=MOST))
        last = kept[-1] if kept else None
        if (
            last is not None
            and last.on == on
            and _same(last.pct, pct, 1)
            and _same(last.kelvin, kelvin, 25)
        ):
            return  # only details changed: nothing anyone would see
        decision = _cause(change.entity_id, recent, now, groups or {})
        who, skill, stand = who_did(change, decision)
        kept.append(LampEvent(now, on, pct, kelvin, who, skill, stand))
        while kept and now - kept[0].at > KEEP:
            kept.popleft()

    def decided(self, decision: Decision, groups: Mapping[str, tuple[str, ...]]) -> None:
        """HouseMind's decision was recorded just after its lamp changed: the change gets its
        real cause (the answer of Home Assistant can come before the decision is written)."""
        r = decision.request
        if not decision.executed:
            return
        for lamp in (r.entity_id, *groups.get(r.entity_id, ())):
            kept = self.events.get(lamp)
            if not kept or kept[-1].who != "housemind":
                continue
            last = kept[-1]
            if abs((last.at - decision.at).total_seconds()) > CAUSED_WITHIN.total_seconds():
                continue
            who, skill, stand = who_did_housemind(decision)
            kept[-1] = LampEvent(last.at, last.on, last.pct, last.kelvin, who, skill, stand)

    def today(self, entity_id: str, now: datetime) -> list[LampEvent]:
        return [e for e in self.events.get(entity_id, ()) if now - e.at <= KEEP]


def _same(a: float | None, b: float | None, close: float) -> bool:
    if a is None or b is None:
        return a is b
    return abs(a - b) < close


def _cause(
    entity_id: str,
    recent: Iterable[Decision],
    now: datetime,
    groups: Mapping[str, tuple[str, ...]],
) -> Decision | None:
    """HouseMind's latest executed decision for this lamp (or a group it is in), just now."""
    found: Decision | None = None
    for decision in recent:
        r = decision.request
        if not decision.executed or now - decision.at > CAUSED_WITHIN:
            continue
        if r.entity_id == entity_id or entity_id in groups.get(r.entity_id, ()):
            if found is None or decision.at >= found.at:
                found = decision
    return found


def what_changed(before: LampEvent | None, after: LampEvent) -> list[str]:
    """In words, what this change did: on, off, brighter, softer, warmer, whiter."""
    if not after.on:
        return ["off"]
    if before is None or not before.on:
        return ["on"]
    out = []
    if before.pct is not None and after.pct is not None and abs(after.pct - before.pct) >= 1:
        out.append("brighter" if after.pct > before.pct else "softer")
    if (
        before.kelvin is not None
        and after.kelvin is not None
        and abs(after.kelvin - before.kelvin) >= 25
    ):
        out.append("whiter" if after.kelvin > before.kelvin else "warmer")
    return out or ["colour"]
