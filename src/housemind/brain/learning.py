"""Explainable learning and the autonomy steps (H3.3, H3.4, E-56, E-57, E-160, E-162, B-14).

HouseMind learns by *counting*, never with a black box:

- Every skill request becomes a **prediction** for one entity ("living light on").
- What people then do resolves it: did the same within the window → **hit**; did the
  opposite, or nothing → **miss**. A person undoing what HouseMind did → **correction**.
- Proposal answers count as **approved** / **rejected**; trials at level Carefree count
  as **trial_ok** / **trial_bad**.
- All counts fade with a half-life of two weeks, so new habits take over.
- Values people choose (brightness, colour temperature, temperature) become a weighted
  mean per skill × area × context, used instead of the start knowledge.

The step per skill × area follows the thresholds of E-56 (and E-160 for start knowledge);
an administrator can set them per part (E-297). Two corrections or "no" within a week give
one step back; then the next step up is earned again, from that moment and after at least a
week (20.9). What an existing Home Assistant automation does counts as start knowledge too
(E-41): a few experiences and its values.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import MISSING, dataclass, field, fields
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from housemind.core.model import Source, StateChange, daypart
from housemind.knowledge.catalog import Step
from housemind.memory.database import Database
from housemind.safety.gate import ActionRequest, Decision, Outcome


def part_of_slot(bucket: str) -> str | None:
    """The part of the day of a learning context like ``workday|19:30``."""
    _, _, slot = bucket.partition("|")
    hour, _, minute = slot.partition(":")
    if not hour.isdigit() or not minute.isdigit():
        return None
    return daypart(datetime(2000, 1, 1, int(hour), int(minute)))


HALF_LIFE = timedelta(days=14)  # B-14

# E-56 thresholds
TO_PROPOSE_SCORE = 0.85
TO_PROPOSE_SITUATIONS = 10  # also what "proven" asks (E-460); was 20
TO_PROPOSE_DAYS = 7
TO_AUTONOMOUS_SCORE = 0.90
TO_AUTONOMOUS_ANSWERS = 15
TO_AUTONOMOUS_DAYS = 7
# E-160 start knowledge may be proposed early
START_PROPOSE_AFTER = timedelta(hours=2)
START_PROPOSE_SITUATIONS = 1
START_PROPOSE_SCORE = 0.4
START_DOUBT_SITUATIONS = 3  # start knowledge is proposed at once, unless people often did otherwise
# Acting without asking (a trial on Carefree), and start knowledge coming back after a step
# back, needs proof (QA 2, B-176): the lower bound of the confidence and a week of watching.
TRY_CONFIDENCE = 0.6
TRY_DAYS = 7.0
# E-41: an existing automation counts as a few experiences (H3.3 step 5), not as situations
AUTOMATION_SEED = 3.0
AUTOMATION_BUCKET = "automation"
# Light off when leaving (E-444): the minutes a room is empty before its lamps go off, learned
# per room within these bounds; HouseMind's off undone within this long means it was too soon.
HOLD_MIN = 1.0
HOLD_MAX = 30.0
HOLD_TOO_SOON = timedelta(minutes=2)
DAY_RHYTHM = "day_rhythm"  # what people choose beside the day's rhythm (E-467)
# The evening light outside (E-446): a lamp put out from this hour on ends the evening.
EVENING_OFF_FROM = 21
# stepping back (S-01)
# A step back (E-56, 20.9): two corrections or "no" within a week give one step back; after
# it only what came since counts, and the next step up waits at least a week.
SETBACK_WINDOW = timedelta(days=7)
SETBACKS_FOR_STEP = 2
EARN_AGAIN_DAYS = 7.0
# "Not now" is no step back by itself; a proposal people mostly say no to still goes back to
# watching (the faded share of yes under half, at least this many no).
MOSTLY_NO = 3.0
SITUATIONS = "_situations"
# Learning from the history in Home Assistant (E-257, B-101): its counts carry this mark in
# their bucket, weigh half and fade from their own day; its situations count apart.
HISTORY = "h|"
HISTORY_WEIGHT = 0.5
HISTORY_SITUATIONS = "_situations_history"
ANSWERS = "_answers"  # plain count of answers and trials (E-56: "at least 15 proposals")
# Learning shared between rooms (E-316, B-182): for a skill that only learns from answers, a
# yes or no in another room counts half; acting by itself needs this many answers in the room.
SHARED_WEIGHT = 0.5
OWN_ANSWERS = 5.0
# Counts fade continuously; allow for the tiny decay between events of one day.
EPS = 0.05
# Proof and one yes (E-460, B-375): a rule that was right often enough gets one question. At most
# this share may have been put back, and someone undoing what an automation just did within this
# long counts as put back. Someone who put the lamp out and then left, while the sensor still
# saw them for a moment, did "light off when leaving" if the room is empty within this long.
PUT_BACK_SHARE = 0.2
PUT_BACK_WINDOW = timedelta(minutes=5)
OFF_THEN_LEFT = timedelta(minutes=10)

# Covers: half an hour (E-268) – an automation at sunset is no "not like that" for HouseMind
# expecting them a quarter of an hour earlier.
WINDOWS = {"light": 180, "switch": 180, "cover": 1800, "climate": 1800, "fan": 600}
OFF_WINDOWS = {"light": 600, "switch": 600}
# No one's action: a cover still moving, a device away or back (E-268) – only the end counts.
NO_ACTION = frozenset({"unavailable", "unknown", "opening", "closing"})

# When it is dark enough for light, learned per room (E-268, B-118): each time someone comes
# into a place where no lamp is on, the height of the sun and the room's light level, and
# whether a lamp went on. Counted in bands; the measure that fits best decides.
BANDS = {
    "sun": (-6.0, 0.0, 3.0, 6.0, 10.0, 20.0),
    "lux": (1.0, 5.0, 20.0, 50.0, 150.0, 500.0),
}
LEVEL_SAMPLES = 6.0  # times someone came in before a measure may decide
LEVEL_BAND = 2.0  # times in the band of now
LIGHT, NONE = "light", "none"
# Walking through is no coming in (E-270, B-120): visits in the dark with the lamps off,
# "pass" (shorter than skills.PASS_WAIT in one go, no lamp went on) or "stay"; kept with the levels.
VISIT, PASS, STAY = "visit", "pass", "stay"
PASS_SAMPLES = 6.0
PASS_SHARE = 0.5
# Two rooms that share a presence sensor – an open kitchen and its dining part – are rooms of
# their own; whether people let the light of the one do for the other is learned (E-530):
# coming into one in the dark while the other is lit, they switch its own lamps on (apart) or
# stay without them, or put out the light HouseMind gave it (shared). Kept with the levels,
# per room and the room next to it.
OPEN, SHARED, APART = "open", "shared", "apart"
OPEN_SAMPLES = 4.0  # times before the light of the room next door may do for this one
OPEN_SHARE = 0.75  # … and this share of them without its own light
# Covers closing in the evening teach at which height of the sun they close in a room (C1).
CLOSE_SUN = (-12.0, 10.0)
# Curtains opened in a bedroom in the morning (E-484, design 71.6): only around dawn does the
# light decide; later, getting up does. Up to an hour after getting up is about getting up;
# up to four hours after the house woke, about this room's own morning.
OPEN_SUN = (-6.0, 6.0)
WAKE_WAIT = (0.0, 60.0)
WAKE_AFTER = (0.0, 240.0)
MOVING = timedelta(minutes=10)  # a cover still arriving where someone sent it


class Result(StrEnum):
    HIT = "hit"
    MISS = "miss"
    CORRECTION = "correction"
    APPROVED = "approved"
    REJECTED = "rejected"
    TRIAL_OK = "trial_ok"
    TRIAL_BAD = "trial_bad"
    # HouseMind did it and nobody put it back: no proof that people do it so (QA 2, B-175).
    KEPT = "kept"
    # An automation did what HouseMind expected and someone undid it at once (E-460): no step
    # back – it was not HouseMind – but it counts against the proof.
    PUT_BACK = "put_back"


# What counts towards a step back (20.9): someone putting it back (also Undo and a trial put
# back) and a "no" (Not now, Never, or no to Vesta).
_YES = frozenset({Result.APPROVED.value, Result.TRIAL_OK.value})
_NO = frozenset({Result.REJECTED.value, Result.TRIAL_BAD.value})

SETBACK_RESULTS = {
    Result.CORRECTION: "corrected",
    Result.TRIAL_BAD: "corrected",
    Result.REJECTED: "rejected",
}


@dataclass(slots=True)
class Prediction:
    skill: str
    area_id: str
    entity_id: str
    action: str
    data: dict[str, Any]
    context: str
    at: datetime
    deadline: datetime
    executed: bool
    trial: bool = False
    group: str | None = None  # lamps of one place at one moment: one situation (B-116)
    # Lamps of the open space around it (E-268): one going on means people chose their light –
    # then this place's own lamps staying off is no "not like that" (one going on still is).
    withdraw: tuple[str, ...] = ()
    lit_nearby: bool = False
    proposed: bool = False  # a question: no answer is no "no" (QA 2, B-178)
    seen: bool = False  # a trial with someone there who could put it back (B-177)
    hold: float | None = None  # light off: the minutes it waited (E-444)
    # Nobody home who could have done it (E-446): nobody doing it is no "not like that".
    nobody_home: bool = False


@dataclass(slots=True)
class Arrival:
    """Someone came into a place where no lamp was on (E-268): did a lamp go on?"""

    area_id: str
    lamps: tuple[str, ...]
    at: datetime
    deadline: datetime
    levels: dict[str, float]
    group: str
    housemind: str = ""  # "proposed" or "executed": HouseMind's own part


@dataclass(slots=True)
class OpenEntry:
    """Someone came into a dark room while a room of the same open space was lit (E-530):
    does this room want its own light too?"""

    area_id: str
    lamps: tuple[str, ...]  # this room's own lamps
    others: tuple[str, ...]  # the rooms next to it that were lit
    at: datetime
    deadline: datetime
    housemind: str = ""  # "proposed" or "executed": HouseMind's own part


@dataclass(frozen=True, slots=True)
class StepChange:
    skill: str
    area_id: str
    old: Step
    new: Step
    reason: str
    by_admin: bool = False  # the step an administrator set went back for this pair (20.10)


@dataclass(frozen=True, slots=True)
class Thresholds:
    """The thresholds of one part (E-297); the defaults are those of E-56 and B-14."""

    propose_score: float = TO_PROPOSE_SCORE
    propose_situations: float = TO_PROPOSE_SITUATIONS
    propose_days: float = TO_PROPOSE_DAYS
    autonomous_score: float = TO_AUTONOMOUS_SCORE
    autonomous_answers: float = TO_AUTONOMOUS_ANSWERS
    autonomous_days: float = TO_AUTONOMOUS_DAYS
    half_life_days: float = HALF_LIFE.total_seconds() / 86400

    @classmethod
    def read(cls, raw: Any) -> Thresholds | None:
        """From a house setting: percentages 1–100, counts from 1, days from 0, the half-life
        from 1 day. Anything that cannot be such a number is refused (None); there is no
        lower limit beyond that – the gate keeps the safety (20.11)."""
        if not isinstance(raw, dict):
            return None
        base = cls()
        try:
            values = {
                "propose_score": float(raw.get("propose_score", base.propose_score * 100)) / 100,
                "propose_situations": float(raw.get("propose_situations", base.propose_situations)),
                "propose_days": float(raw.get("propose_days", base.propose_days)),
                "autonomous_score": float(raw.get("autonomous_score", base.autonomous_score * 100))
                / 100,
                "autonomous_answers": float(raw.get("autonomous_answers", base.autonomous_answers)),
                "autonomous_days": float(raw.get("autonomous_days", base.autonomous_days)),
                "half_life_days": float(raw.get("half_life_days", base.half_life_days)),
            }
        except (TypeError, ValueError):
            return None
        if any(math.isnan(v) or math.isinf(v) for v in values.values()):
            return None
        if not all(0.01 <= values[k] <= 1.0 for k in ("propose_score", "autonomous_score")):
            return None
        if values["propose_situations"] < 1 or values["autonomous_answers"] < 1:
            return None
        if values["propose_days"] < 0 or values["autonomous_days"] < 0:
            return None
        if values["half_life_days"] < 1:
            return None
        return cls(**values)

    def view(self) -> dict[str, float]:
        """As the setting is kept and shown: percentages, counts and days."""
        return {
            "propose_score": round(self.propose_score * 100),
            "propose_situations": round(self.propose_situations),
            "propose_days": round(self.propose_days, 1),
            "autonomous_score": round(self.autonomous_score * 100),
            "autonomous_answers": round(self.autonomous_answers),
            "autonomous_days": round(self.autonomous_days, 1),
            "half_life_days": round(self.half_life_days, 1),
        }


DEFAULT_THRESHOLDS = Thresholds()


@dataclass(slots=True)
class Earn:
    """After a step back: when, and the plain counts then (only what came since counts)."""

    since: datetime
    situations: float
    answers: float
    back_to: Step = Step.OBSERVE  # the step it went back to


@dataclass(slots=True)
class _Count:
    weight: float
    updated: datetime


def _decayed(count: _Count, now: datetime, half_life_s: float | None = None) -> float:
    age = max(0.0, (now - count.updated).total_seconds())
    return count.weight * math.pow(0.5, age / (half_life_s or HALF_LIFE.total_seconds()))


def wilson_lower_bound(successes: float, total: float, z: float = 1.96) -> float:
    """Careful confidence (B-08): 18 of 20 is surer than 2 of 2."""
    if total <= 0:
        return 0.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denominator)


def band(measure: str, value: float) -> str:
    """The band a light level falls in, e.g. ``"3..6"``, ``"<-6"`` or ``">20"``."""
    edges = BANDS[measure]
    if value < edges[0]:
        return f"<{edges[0]:g}"
    for low, high in itertools.pairwise(edges):
        if value < high:
            return f"{low:g}..{high:g}"
    return f">{edges[-1]:g}"


def _desired(action: str, data: dict[str, Any]) -> str:
    """The state an action aims for, in plain terms."""
    service = action.split(".", 1)[1]
    if service in ("turn_on",):
        return "on"
    if service in ("turn_off",):
        return "off"
    if service == "close_cover":
        return "closed"
    if service == "open_cover":
        return "open"
    if service == "set_temperature":
        return f"temp:{data.get('temperature')}"
    return service


def _actual(domain: str, change: StateChange) -> str | None:
    if change.new is None or change.new.state in NO_ACTION:
        return None
    if change.old is not None and change.old.state in ("unavailable", "unknown"):
        return None  # back online: nobody's action
    if domain == "climate":
        value = change.new.attributes.get("temperature")
        old = change.old.attributes.get("temperature") if change.old else None
        return f"temp:{value}" if value is not None and value != old else None
    if change.old is not None and change.old.state == change.new.state:
        return None
    return change.new.state


@dataclass(slots=True)
class Learning:
    db: Database
    installed_at: datetime
    start_knowledge: set[str] = field(default_factory=set)
    # Skills people rarely do by hand (saving heat): silence is no "no"; they learn from answers.
    answers_only: set[str] = field(default_factory=set)
    counts: dict[tuple[str, str, str, str], _Count] = field(default_factory=dict)
    values: dict[tuple[str, str, str, str], tuple[float, float, datetime]] = field(
        default_factory=dict
    )
    steps: dict[tuple[str, str], Step] = field(default_factory=dict)
    first_seen: dict[tuple[str, str], datetime] = field(default_factory=dict)
    pending: list[Prediction] = field(default_factory=list)
    lights_used: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    corrections: list[Prediction] = field(default_factory=list)  # read and cleared by the runtime
    taught_by: dict[tuple[str, str], str] = field(default_factory=dict)  # skill × area → automation
    # When it is dark enough, per room (E-268): (area, measure, band, light/none) → count; the
    # history's under "h|" + measure.
    levels: dict[tuple[str, str, str, str], _Count] = field(default_factory=dict)
    arrivals: list[Arrival] = field(default_factory=list)
    open_entries: list[OpenEntry] = field(default_factory=list)
    # The last real change of each device by a person, switch or automation: its state, when,
    # and its weight (E-268: who was first, and covers closed just before HouseMind expected it).
    switched: dict[str, tuple[str, datetime, float]] = field(default_factory=dict)
    _closed_at: dict[str, datetime] = field(default_factory=dict)  # area → last evening close
    # A cover set moving – by whom and when: the device reports the end itself, without who
    # started it, and an automation's curtain must not count as a wall switch.
    _moving: dict[str, tuple[Source, datetime]] = field(default_factory=dict)
    _hold_at: dict[str, datetime] = field(default_factory=dict)  # E-444: one moment per room
    _wake_open_at: dict[str, datetime] = field(default_factory=dict)  # E-484: one moment
    _evening_off_at: dict[str, datetime] = field(default_factory=dict)  # E-446: the same
    # The tables per room and measure, kept until something is counted there or an hour has
    # passed (replaying the history asks for them at every step).
    _tables: dict[tuple[str, str], tuple[datetime, dict[str, list[float]]]] = field(
        default_factory=dict
    )
    _dirty: set[tuple[str, str, str, str]] = field(default_factory=set)
    # The thresholds per part (E-297): which part a skill belongs to, and the part's own.
    parts: dict[str, str] = field(default_factory=dict)
    limits: dict[str, Thresholds] = field(default_factory=dict)
    # Parts that are off, and since when (E-400): asleep – nothing counted, no step changes,
    # and when one goes on again its learning goes on where it was, without having faded.
    paused: dict[str, datetime] = field(default_factory=dict)
    # Stepping back (20.9): when people corrected or said "no" (and which), per skill × room;
    # since when a pair earns its step again; and how far an administrator's step may still
    # lift a pair that went back under it (20.10).
    setbacks: dict[tuple[str, str], list[tuple[datetime, str]]] = field(default_factory=dict)
    earn: dict[tuple[str, str], Earn] = field(default_factory=dict)
    held: dict[tuple[str, str], Step] = field(default_factory=dict)
    _earn_dirty: set[tuple[str, str]] = field(default_factory=set)
    _lamps_changed: bool = False
    _levels_changed: bool = False
    # Proof and one yes (E-460). What an automation just did that HouseMind expected: device →
    # skill, room, context, when and the state it went to, to see someone undo it at once. A
    # lamp put out by someone the sensor still sees: room → when and context. Questions answered
    # by doing it by hand: read and cleared by the runtime, which closes them.
    _just_done: dict[str, tuple[str, str, str, datetime, str]] = field(default_factory=dict)
    off_seen: dict[str, tuple[datetime, str]] = field(default_factory=dict)
    done_by_hand: list[tuple[str, str, str]] = field(default_factory=list)
    # Whether someone is in a room, or home when the room has no sensor that can tell: only
    # then can a trial be put back (QA 2, B-177). Set by the runtime; without it: nobody.
    someone_there: Callable[[str], bool] = field(default=lambda area_id: False)
    # A lamp's own brightness on the felt scale of the brain (E-494, B-410); None: the same.
    felt: Callable[[str, float], float] = field(default=lambda entity_id, pct: pct)

    def asleep(self, skill: str) -> bool:
        return self.parts.get(skill) in self.paused

    def resume(self, part: str, now: datetime) -> None:
        """A part on again (E-400): what it learned is as old as when it went off – the
        fading did not go on while it slept."""
        since = self.paused.pop(part, None)
        if since is None:
            return
        slept = max(timedelta(0), now - since)
        for key, count in self.counts.items():
            if self.parts.get(key[0]) == part:
                self.counts[key] = _Count(count.weight, count.updated + slept)
                self._dirty.add(key)
        for key, (mean, weight, updated) in list(self.values.items()):
            if self.parts.get(key[0]) == part:
                self.values[key] = (mean, weight, updated + slept)

    # -- loading and saving -------------------------------------------------------------

    async def load(self) -> None:
        for row in await self.db.fetchall("SELECT * FROM skill_counts"):
            self.counts[(row["skill"], row["area_id"], row["bucket"], row["outcome"])] = _Count(
                float(row["weight"]), datetime.fromisoformat(row["updated"])
            )
        for row in await self.db.fetchall("SELECT * FROM skill_values"):
            self.values[(row["skill"], row["area_id"], row["bucket"], row["key"])] = (
                float(row["mean"]),
                float(row["weight"]),
                datetime.fromisoformat(row["updated"]),
            )
        for row in await self.db.fetchall("SELECT * FROM skill_steps"):
            self.steps[(row["skill"], row["area_id"])] = Step(int(row["step"]))
            if row["first_seen"]:
                self.first_seen[(row["skill"], row["area_id"])] = datetime.fromisoformat(
                    row["first_seen"]
                )
        # Kept over a restart, so the days and which lamps people use do not start over.
        for row in await self.db.fetchall("SELECT * FROM skill_seen"):
            pair = (row["skill"], row["area_id"])
            seen = datetime.fromisoformat(row["first_seen"])
            self.first_seen[pair] = min(self.first_seen.get(pair, seen), seen)
        for row in await self.db.fetchall("SELECT * FROM skill_earn"):
            pair = (row["skill"], row["area_id"])
            self.setbacks[pair] = [
                (datetime.fromisoformat(at), kind) for at, kind in json.loads(row["setbacks"])
            ]
            if row["since"]:
                self.earn[pair] = Earn(
                    datetime.fromisoformat(row["since"]),
                    float(row["situations"] or 0.0),
                    float(row["answers"] or 0.0),
                    Step(int(row["back_to"] or 0)),
                )
            if row["held"] is not None:
                self.held[pair] = Step(int(row["held"]))
        used = await self.db.get_setting("lights_used", {}) or {}
        for area_id, lamps in used.items():
            self.lights_used[area_id] = set(lamps)
        for area_id, measure, where, outcome, weight, updated in (
            await self.db.get_setting("light_levels", []) or []
        ):
            self.levels[(area_id, measure, where, outcome)] = _Count(
                float(weight), datetime.fromisoformat(updated)
            )

    async def save(self) -> None:
        rows = []
        for key in self._dirty:
            count = self.counts[key]
            rows.append((*key, count.weight, count.updated.isoformat()))
        self._dirty.clear()
        if rows:
            await self.db.executemany(
                "INSERT INTO skill_counts(skill, area_id, bucket, outcome, weight, updated) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(skill, area_id, bucket, outcome) "
                "DO UPDATE SET weight = excluded.weight, updated = excluded.updated",
                rows,
            )
        seen = [(s, a, at.isoformat()) for (s, a), at in self.first_seen.items()]
        if seen:
            await self.db.executemany(
                "INSERT INTO skill_seen(skill, area_id, first_seen) VALUES(?,?,?) "
                "ON CONFLICT(skill, area_id) DO NOTHING",
                seen,
            )
        if self._lamps_changed:
            self._lamps_changed = False
            used = {area: sorted(lamps) for area, lamps in self.lights_used.items() if lamps}
            await self.db.set_setting("lights_used", used)
        if self._levels_changed:
            self._levels_changed = False
            await self.db.set_setting(
                "light_levels",
                [[*key, c.weight, c.updated.isoformat()] for key, c in sorted(self.levels.items())],
            )
        await self._save_earn()
        value_rows = [(*k, m, w, u.isoformat()) for k, (m, w, u) in self.values.items()]
        if value_rows:
            await self.db.executemany(
                "INSERT INTO skill_values(skill, area_id, bucket, key, mean, weight, updated) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(skill, area_id, bucket, key) "
                "DO UPDATE SET mean = excluded.mean, weight = excluded.weight, "
                "updated = excluded.updated",
                value_rows,
            )

    async def _save_earn(self) -> None:
        for pair in sorted(self._earn_dirty):
            setbacks = self.setbacks.get(pair, [])
            again = self.earn.get(pair)
            held = self.held.get(pair)
            if not setbacks and again is None and held is None:
                await self.db.execute(
                    "DELETE FROM skill_earn WHERE skill = ? AND area_id = ?", pair
                )
                continue
            await self.db.execute(
                "INSERT INTO skill_earn(skill, area_id, setbacks, since, situations, answers, "
                "back_to, held) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(skill, area_id) DO UPDATE "
                "SET setbacks = excluded.setbacks, since = excluded.since, "
                "situations = excluded.situations, answers = excluded.answers, "
                "back_to = excluded.back_to, held = excluded.held",
                (
                    *pair,
                    json.dumps([[at.isoformat(), kind] for at, kind in setbacks]),
                    again.since.isoformat() if again else None,
                    again.situations if again else None,
                    again.answers if again else None,
                    int(again.back_to) if again else None,
                    int(held) if held is not None else None,
                ),
            )
        self._earn_dirty.clear()

    async def _save_step(
        self, skill: str, area_id: str, step: Step, now: datetime, reason: str
    ) -> None:
        first = self.first_seen.get((skill, area_id), now)
        score, n, _ = self.score(skill, area_id, now)
        await self.db.execute(
            "INSERT INTO skill_steps(skill, area_id, step, score, situations, since, first_seen, "
            "history) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(skill, area_id) "
            "DO UPDATE SET step = excluded.step, "
            "score = excluded.score, situations = excluded.situations, since = excluded.since, "
            "history = json_insert(skill_steps.history, '$[#]', json(?))",
            (
                skill,
                area_id,
                int(step),
                score,
                int(n),
                now.isoformat(),
                first.isoformat(),
                json.dumps([{"at": now.isoformat(), "step": int(step), "reason": reason}]),
                json.dumps({"at": now.isoformat(), "step": int(step), "reason": reason}),
            ),
        )

    # -- counting -----------------------------------------------------------------------

    def _add(
        self,
        skill: str,
        area_id: str,
        context: str,
        result: Result,
        now: datetime,
        weight: float = 1.0,
        setback: bool = True,
        plain: bool = True,
    ) -> None:
        """``plain=False``: it teaches, but is no situation or answer of its own (a thumb)."""
        if self.asleep(skill):
            return  # its part is off: nothing is counted (E-400)
        key = (skill, area_id, context, result.value)
        current = self.counts.get(key)
        base = _decayed(current, now, self._half_life(skill)) if current else 0.0
        self.counts[key] = _Count(base + weight, now)
        self._dirty.add(key)
        if setback and result in SETBACK_RESULTS and not context.startswith(HISTORY):
            self._setback(skill, area_id, SETBACK_RESULTS[result], now)
        if plain and result in (Result.HIT, Result.MISS, Result.CORRECTION):
            self._situation(skill, area_id, now)
        if plain and result in (
            Result.APPROVED,
            Result.REJECTED,
            Result.TRIAL_OK,
            Result.TRIAL_BAD,
        ):
            self._plain(skill, area_id, ANSWERS, now)
        self.first_seen.setdefault((skill, area_id), now)

    def _setback(self, skill: str, area_id: str, kind: str, now: datetime) -> None:
        """A correction or a "no" (20.9): kept with its time for a week."""
        pair = (skill, area_id)
        kept = [(at, k) for at, k in self.setbacks.get(pair, []) if now - at <= SETBACK_WINDOW]
        self.setbacks[pair] = [*kept, (now, kind)]
        self._earn_dirty.add(pair)

    def recent_setbacks(self, skill: str, area_id: str, now: datetime) -> list[str]:
        """The corrections and "no" of the last week, since the last step back."""
        again = self.earn.get((skill, area_id))
        return [
            kind
            for at, kind in self.setbacks.get((skill, area_id), [])
            if now - at <= SETBACK_WINDOW and (again is None or at >= again.since)
        ]

    def earned(
        self,
        skill: str,
        area_id: str,
        now: datetime,
        scored: tuple[float, float, float] | None = None,
        approved: tuple[float, float, float] | None = None,
    ) -> tuple[float, float, float]:
        """Situations, answers and days that count towards the next step up: after a step
        back only what came since (20.9). ``scored`` and ``approved``: ``score`` and
        ``approval`` already worked out for this pair now (S4: the card asks for both)."""
        _, n, days = scored if scored is not None else self.score(skill, area_id, now)
        _, _, answers = approved if approved is not None else self.approval(skill, area_id, now)
        again = self.earn.get((skill, area_id))
        if again is None:
            return n, answers, days
        since = (now - again.since).total_seconds() / 86400
        return max(0.0, n - again.situations), max(0.0, answers - again.answers), min(days, since)

    def thresholds(self, skill: str) -> Thresholds:
        """The thresholds of the part this skill belongs to (E-297)."""
        return self.limits.get(self.parts.get(skill, ""), DEFAULT_THRESHOLDS)

    def _half_life(self, skill: str) -> float:
        return self.thresholds(skill).half_life_days * 86400

    def _situation(self, skill: str, area_id: str, now: datetime) -> None:
        """Number of situations seen (E-56) – a plain count, it does not fade."""
        self._plain(skill, area_id, SITUATIONS, now)

    def _plain(self, skill: str, area_id: str, name: str, now: datetime) -> None:
        n_key = (skill, area_id, "*", name)
        previous = self.counts.get(n_key)
        self.counts[n_key] = _Count((previous.weight if previous else 0.0) + 1.0, now)
        self._dirty.add(n_key)
        self.first_seen.setdefault((skill, area_id), now)

    def from_automation(
        self, skill: str, area_id: str, values: dict[str, float], now: datetime
    ) -> None:
        """Start knowledge from an existing automation (E-41, E-160): it counts as a few good
        experiences and its values as preferences, so HouseMind's own counts soon take over."""
        if self.asleep(skill):
            return  # its part is off (E-400)
        key = (skill, area_id, AUTOMATION_BUCKET, Result.HIT.value)
        current = self.counts.get(key)
        self.counts[key] = _Count(
            (_decayed(current, now) if current else 0.0) + AUTOMATION_SEED, now
        )
        self._dirty.add(key)
        for name, value in values.items():
            if name == "hold_minutes":  # an automation that puts the lamps out at once (E-444)
                value = min(HOLD_MAX, max(HOLD_MIN, value))
            mean, weight, updated = self.values.get((skill, area_id, "*", name), (value, 0.0, now))
            age = max(0.0, (now - updated).total_seconds())
            weight *= math.pow(0.5, age / HALF_LIFE.total_seconds())
            self.values[(skill, area_id, "*", name)] = (
                (mean * weight + value * AUTOMATION_SEED) / (weight + AUTOMATION_SEED),
                weight + AUTOMATION_SEED,
                now,
            )
        self.first_seen.setdefault((skill, area_id), now)

    def knows(self, skill: str, area_id: str, now: datetime) -> bool:
        """HouseMind predicts this skill × room well enough to do it (the E-56 thresholds)."""
        score, n, days = self.score(skill, area_id, now)
        limits = self.thresholds(skill)
        return (
            score >= limits.propose_score
            and n >= limits.propose_situations
            and days >= limits.propose_days
        )

    def judged(self, skill: str, area_id: str) -> bool:
        """Something was counted as right or not here: the share says something (E-460). A
        skill that only learns from answers and got none has only seen situations."""
        kinds = (Result.HIT.value, Result.MISS.value, Result.CORRECTION.value)
        return any(s == skill and a == area_id and r in kinds for s, a, _, r in self.counts)

    def put_back_share(self, skill: str, area_id: str, now: datetime) -> float:
        """How much of what was right was put back at once (E-460): HouseMind's own action
        undone, or an automation's."""
        half = self._half_life(skill)
        hits = back = 0.0
        for (s, a, _, r), count in self.counts.items():
            if s == skill and a == area_id:
                if r == Result.HIT.value:
                    hits += _decayed(count, now, half)
                elif r in (Result.CORRECTION.value, Result.PUT_BACK.value):
                    back += _decayed(count, now, half)
        return back / (hits + back) if hits + back else 0.0

    def proof_wait(self, skill: str, area_id: str) -> float:
        """The days proof takes: the part's own, and at least a week after a step back (20.9)."""
        days = self.thresholds(skill).propose_days
        return max(days, EARN_AGAIN_DAYS) if (skill, area_id) in self.earn else days

    def proven(self, skill: str, area_id: str, now: datetime) -> bool:
        """This rule was right often enough to ask once whether HouseMind may do it itself from
        now on (E-460): only counting, the thresholds of its part (E-297), and after a step back
        only what came since. What learns from answers alone proves itself there."""
        if skill in self.answers_only:
            return False
        score, _, _ = self.score(skill, area_id, now)
        n, _, days = self.earned(skill, area_id, now)
        return self.proof_holds(skill, area_id, score, n, days, now)

    def proof_holds(
        self, skill: str, area_id: str, score: float, n: float, days: float, now: datetime
    ) -> bool:
        """``proven`` with what the caller already counted: going through the counts is what a
        screen update spends its time on (S4)."""
        limits = self.thresholds(skill)
        return (
            score >= limits.propose_score
            and n >= limits.propose_situations - EPS
            and days >= self.proof_wait(skill, area_id)
            and self.put_back_share(skill, area_id, now) <= PUT_BACK_SHARE
        )

    async def grant(self, skill: str, area_id: str, now: datetime, reason: str) -> None:
        """Someone said yes: from now on HouseMind does it itself (E-460). An earned step, so
        two corrections or "no" within a week take it back as always (20.9)."""
        self.steps[(skill, area_id)] = Step.AUTONOMOUS
        self.first_seen.setdefault((skill, area_id), now)
        await self._save_step(skill, area_id, Step.AUTONOMOUS, now, reason)
        await self.save()

    def total(self, skill: str, area_id: str, result: Result, now: datetime) -> float:
        half = self._half_life(skill)
        return sum(
            _decayed(c, now, half)
            for (s, a, _, r), c in self.counts.items()
            if s == skill and a == area_id and r == result.value
        )

    def score(self, skill: str, area_id: str, now: datetime) -> tuple[float, float, float]:
        """Share of good predictions, number of situations, and days of experience."""
        hits = self.total(skill, area_id, Result.HIT, now)
        misses = (
            self.total(skill, area_id, Result.MISS, now)
            + self.total(skill, area_id, Result.CORRECTION, now)
            + self.total(skill, area_id, Result.PUT_BACK, now)
        )
        situations = self.counts.get((skill, area_id, "*", SITUATIONS))
        history = self.counts.get((skill, area_id, "*", HISTORY_SITUATIONS))
        n = (situations.weight if situations else 0.0) + (history.weight if history else 0.0)
        first = self.first_seen.get((skill, area_id))
        days = (now - first).total_seconds() / 86400 if first else 0.0
        share = hits / (hits + misses) if hits + misses else 0.0
        return share, n, days

    def approval(
        self, skill: str, area_id: str, now: datetime, shared: bool = True
    ) -> tuple[float, float, float]:
        """Yes and no to proposals and trials (they fade), and the plain number of answers.
        For a skill that only learns from answers, those in the other rooms count half
        (E-316, B-182): a yes for warmth in the living room also says something about the
        kitchen."""
        share = shared and skill in self.answers_only
        half = self._half_life(skill)
        approved = rejected = answered = 0.0
        for (s, a, bucket, r), count in self.counts.items():
            if s != skill or (a != area_id and not share):
                continue
            weight = 1.0 if a == area_id else SHARED_WEIGHT
            if r in _YES:
                approved += weight * _decayed(count, now, half)
            elif r in _NO:
                rejected += weight * _decayed(count, now, half)
            elif r == ANSWERS and bucket == "*":
                answered += weight * count.weight
        return approved, rejected, answered

    def own_answers(self, skill: str, area_id: str) -> float:
        """The plain number of answers in this room itself (E-316)."""
        answered = self.counts.get((skill, area_id, "*", ANSWERS))
        return answered.weight if answered else 0.0

    # -- the history (E-257) --------------------------------------------------------------

    def history_share(self, skill: str, area_id: str, now: datetime) -> float:
        """How much of the evidence (right, different, undone) came from the history."""
        mine = theirs = 0.0
        judged = (Result.HIT.value, Result.MISS.value, Result.CORRECTION.value)
        half = self._half_life(skill)
        for (s, a, bucket, r), count in self.counts.items():
            if s == skill and a == area_id and r in judged:
                weight = _decayed(count, now, half)
                if bucket.startswith(HISTORY):
                    theirs += weight
                else:
                    mine += weight
        return theirs / (mine + theirs) if mine + theirs else 0.0

    def history_situations(self, skill: str, area_id: str) -> float:
        found = self.counts.get((skill, area_id, "*", HISTORY_SITUATIONS))
        return found.weight if found else 0.0

    async def forget_history(self) -> None:
        """Wipe what an earlier reading of the history taught, before reading again; what
        HouseMind saw itself stays – and so does what a part that is off learned (E-400)."""
        for key in [
            k
            for k in self.counts
            if (k[2].startswith(HISTORY) or k[3] == HISTORY_SITUATIONS) and not self.asleep(k[0])
        ]:
            del self.counts[key]
            self._dirty.discard(key)
        for key in [k for k in self.values if k[2].startswith(HISTORY) and not self.asleep(k[0])]:
            del self.values[key]
        for key in [k for k in self.levels if k[1].startswith(HISTORY)]:
            del self.levels[key]
            self._levels_changed = True
        self._tables.clear()
        kept = sorted(s for s in self.parts if self.asleep(s))
        spare = f" AND skill NOT IN ({','.join('?' * len(kept))})" if kept else ""
        await self.db.execute(
            f"DELETE FROM skill_counts WHERE (bucket LIKE 'h|%' OR outcome = ?){spare}",  # noqa: S608
            (HISTORY_SITUATIONS, *kept),
        )
        await self.db.execute(
            f"DELETE FROM skill_values WHERE bucket LIKE 'h|%'{spare}",  # noqa: S608
            tuple(kept),
        )

    async def forget_skills(self, skills: set[str]) -> None:
        """Wipe everything learned for skills HouseMind no longer has (B-248, K2)."""
        for learned in (self.counts, self.values):
            for key in [k for k in learned if k[0] in skills]:
                del learned[key]
                self._dirty.discard(key)
        for pairs in (
            self.steps,
            self.first_seen,
            self.setbacks,
            self.earn,
            self.held,
            self.taught_by,
        ):
            for pair in [p for p in pairs if p[0] in skills]:
                del pairs[pair]
        self._earn_dirty = {p for p in self._earn_dirty if p[0] not in skills}
        marks = ",".join("?" * len(skills))
        for table in ("skill_counts", "skill_values", "skill_steps", "skill_seen", "skill_earn"):
            await self.db.execute(
                f"DELETE FROM {table} WHERE skill IN ({marks})",  # noqa: S608 – fixed names
                tuple(sorted(skills)),
            )

    async def forget_all(self, now: datetime) -> None:
        """Learning again (E-273): everything learned goes – what went well and differently,
        what the history taught, the usual lamps and values, when it is dark enough, and the
        steps (all back to Observe). The start knowledge waits again as after an install."""
        self._reset()
        self.installed_at = now
        for table in ("skill_counts", "skill_values", "skill_steps", "skill_seen", "skill_earn"):
            await self.db.execute(f"DELETE FROM {table}")  # noqa: S608 – a fixed name
        await self.db.execute("DELETE FROM settings WHERE key IN ('lights_used', 'light_levels')")

    def _reset(self) -> None:
        """Everything learned out of memory; what the house is set up with stays."""
        keep = {"db", "installed_at", "start_knowledge", "answers_only", "parts", "limits"}
        for item in fields(self):
            if item.name in keep:
                continue
            if item.default_factory is not MISSING:
                setattr(self, item.name, item.default_factory())
            else:
                setattr(self, item.name, item.default)

    async def reload(self) -> None:
        """Read what was learned again from the database (a snapshot put back, 20.13)."""
        self._reset()
        await self.load()

    def absorb(self, replay: Learning, days: float, skills: set[str]) -> None:
        """Take over what replaying the history taught (E-257): marked, at half weight,
        fading from its own day. Never answers – Autonomous always needs real ones – and the
        days only count as experience when there are at least seven."""
        judged = (Result.HIT.value, Result.MISS.value, Result.CORRECTION.value)
        for (skill, area, bucket, outcome), count in replay.counts.items():
            if skill not in skills:
                continue
            if outcome == SITUATIONS:
                key = (skill, area, "*", HISTORY_SITUATIONS)
            elif outcome in judged and not bucket.startswith(HISTORY):
                key = (skill, area, HISTORY + bucket, outcome)
            else:
                continue
            self.counts[key] = _Count(count.weight * HISTORY_WEIGHT, count.updated)
            self._dirty.add(key)
        for (skill, area, bucket, name), (mean, weight, updated) in replay.values.items():
            if skill in skills:
                self.values[(skill, area, HISTORY + bucket, name)] = (
                    mean,
                    weight * HISTORY_WEIGHT,
                    updated,
                )
        for pair, seen in replay.first_seen.items():
            if pair[0] in skills and days >= TO_PROPOSE_DAYS:
                self.first_seen[pair] = min(self.first_seen.get(pair, seen), seen)
        for area, lamps in replay.lights_used.items():
            if lamps - self.lights_used[area]:
                self.lights_used[area] |= lamps
                self._lamps_changed = True
        for (area, measure, where, outcome), count in replay.levels.items():
            if not measure.startswith(HISTORY):
                self.levels[(area, HISTORY + measure, where, outcome)] = _Count(
                    count.weight * HISTORY_WEIGHT, count.updated
                )
                self._levels_changed = True
        self._tables.clear()

    def confidence(self, skill: str, area_id: str, now: datetime) -> float:
        hits = self.total(skill, area_id, Result.HIT, now)
        n = (
            hits
            + self.total(skill, area_id, Result.MISS, now)
            + self.total(skill, area_id, Result.CORRECTION, now)
        )
        return round(wilson_lower_bound(hits, n), 2)

    # -- preferences (the Preferences protocol of the skills) ---------------------------

    def _learn_value(
        self, skill: str, area_id: str, context: str, key: str, value: float, now: datetime
    ) -> None:
        if self.asleep(skill):
            return  # its part is off (E-400)
        for bucket in (context, "*"):
            mean, weight, updated = self.values.get(
                (skill, area_id, bucket, key), (value, 0.0, now)
            )
            age = max(0.0, (now - updated).total_seconds())
            weight = weight * math.pow(0.5, age / self._half_life(skill))
            new_weight = weight + 1.0
            self.values[(skill, area_id, bucket, key)] = (
                (mean * weight + value) / new_weight,
                new_weight,
                now,
            )

    def value(self, skill: str, area_id: str, context: str, key: str) -> float | None:
        """Learned value with fall-back to a coarser context (H3.3 step 4), and then to what
        the history taught (E-257)."""
        for bucket in (context, "*", HISTORY + context, HISTORY + "*"):
            found = self.values.get((skill, area_id, bucket, key))
            needed = 2.0 if bucket in (context, HISTORY + context) else 3.0
            if (
                found is not None
                and found[1]
                >= needed * (HISTORY_WEIGHT if bucket.startswith(HISTORY) else 1.0) - EPS
            ):
                return round(found[0], 1)
        return None

    def light_value(
        self, skill: str, area_id: str, context: str, part: str, key: str
    ) -> float | None:
        """The value for a lamp now (E-469, B-384): what it learned of this half hour, else of
        this part of the day, and only then of the whole day – an evening is no average of the
        white hours. Live before what the history taught, as the card shows it."""
        own = self.values.get((skill, area_id, context, key))
        if own is not None and own[1] >= 2.0 - EPS:
            return round(own[0], 1)
        spread = self._in_part(skill, area_id, part, key)
        if False in spread:
            return spread[False]
        past = self.values.get((skill, area_id, HISTORY + context, key))
        if past is not None and past[1] >= 2.0 * HISTORY_WEIGHT - EPS:
            return round(past[0], 1)
        if True in spread:
            return spread[True]
        return self.value(skill, area_id, "*", key)

    def part_value(self, skill: str, area_id: str, part: str, key: str) -> float | None:
        """What it learned of this part of the day only, live before the history (E-471)."""
        spread = self._in_part(skill, area_id, part, key)
        return spread.get(False, spread.get(True))

    def _in_part(self, skill: str, area_id: str, part: str, key: str) -> dict[bool, float]:
        """The weighted mean over the half hours of a part of the day: live (False) and what
        the history taught (True)."""
        spread: dict[bool, tuple[float, float]] = {}
        for (s, a, bucket, k), (mean, weight, _) in self.values.items():
            if s != skill or a != area_id or k != key:
                continue
            history = bucket.startswith(HISTORY)
            if part_of_slot(bucket.removeprefix(HISTORY)) != part:
                continue
            if weight >= (2.0 * HISTORY_WEIGHT if history else 2.0) - EPS:
                total, weights = spread.get(history, (0.0, 0.0))
                spread[history] = (total + mean * weight, weights + weight)
        return {h: round(total / weights, 1) for h, (total, weights) in spread.items()}

    # -- the day's rhythm, as people here prefer it (E-467) -------------------------------

    def chose_light(self, area_id: str, part: str, key: str, off_by: float, now: datetime) -> None:
        """Someone chose another brightness or colour than the day's rhythm: how much, per room
        and part of the day. Fading like everything learned."""
        if self.asleep("light_on_entry"):
            return  # its part is off (E-400)
        slot = (DAY_RHYTHM, area_id, part, key)
        mean, weight, updated = self.values.get(slot, (off_by, 0.0, now))
        age = max(0.0, (now - updated).total_seconds())
        weight *= math.pow(0.5, age / self._half_life("light_on_entry"))
        self.values[slot] = ((mean * weight + off_by) / (weight + 1.0), weight + 1.0, now)

    def day_shift(self, area_id: str, part: str, key: str) -> float | None:
        """How far people here move the day's rhythm in this part of the day, once it is clear
        they do: a single choice stays a single choice."""
        found = self.values.get((DAY_RHYTHM, area_id, part, key))
        return round(found[0], 1) if found is not None and found[1] >= 3.0 - EPS else None

    def usual_lights(self, area_id: str) -> set[str] | None:
        used = self.lights_used.get(area_id)
        return set(used) if used else None

    # -- when it is dark enough, per room (E-268, B-118) ----------------------------------

    def _table(self, area_id: str, measure: str, now: datetime) -> dict[str, list[float]]:
        """Per band: [light, none] – what people did, and half of what the history said."""
        kept = self._tables.get((area_id, measure))
        if kept is not None and timedelta(0) <= now - kept[0] < timedelta(hours=1):
            return kept[1]
        table: dict[str, list[float]] = {}
        for (area, kind, where, outcome), count in self.levels.items():
            if area == area_id and kind in (measure, HISTORY + measure):
                row = table.setdefault(where, [0.0, 0.0])
                row[0 if outcome == LIGHT else 1] += _decayed(count, now)
        self._tables[(area_id, measure)] = (now, table)
        return table

    def dark(self, area_id: str, levels: dict[str, float], now: datetime) -> bool | None:
        """Dark enough for light in this room now, as people showed it; None: too little seen
        yet (then the general rule holds). The measure that fitted best decides – a light
        sensor that says "dark" all day fits worse than the sun and loses."""
        best: tuple[float, str, dict[str, list[float]]] | None = None
        for measure in ("sun", "lux"):
            if measure not in levels:
                continue
            table = self._table(area_id, measure, now)
            total = sum(light + none for light, none in table.values())
            if total < LEVEL_SAMPLES - EPS:
                continue
            fit = sum(max(light, none) for light, none in table.values()) / total
            if best is None or fit > best[0] + 1e-9:
                best = (fit, measure, table)
        if best is None:
            return None
        _fit, measure, table = best
        light, none = table.get(band(measure, levels[measure]), [0.0, 0.0])
        if light + none < LEVEL_BAND - EPS:
            return None
        return light >= none

    def visited(self, area_id: str, passed: bool, now: datetime) -> None:
        """A visit in the dark with the lamps off ended or lasted (E-270)."""
        key = (area_id, VISIT, "*", PASS if passed else STAY)
        current = self.levels.get(key)
        self.levels[key] = _Count((_decayed(current, now) if current else 0.0) + 1.0, now)
        self._levels_changed = True

    # -- how long a room is empty before its lamps go off (E-444) ------------------------

    def _learn_hold(self, area_id: str, minutes: float, now: datetime, weight: float) -> None:
        self._learn_minutes("light_off_leave", "hold_minutes", area_id, minutes, now, weight)

    def _learn_minutes(
        self, skill: str, name: str, area_id: str, minutes: float, now: datetime, weight: float
    ) -> None:
        if self.asleep(skill):
            return  # its part is off (E-400)
        value = min(HOLD_MAX, max(HOLD_MIN, minutes))
        key = (skill, area_id, "*", name)
        mean, old, updated = self.values.get(key, (value, 0.0, now))
        age = max(0.0, (now - updated).total_seconds())
        old *= math.pow(0.5, age / self._half_life(skill))
        self.values[key] = ((mean * old + value * weight) / (old + weight), old + weight, now)

    def arrival_minutes(self, area_id: str, minutes: float, now: datetime, weight: float) -> None:
        """How long the lamps at the entrance stay on after coming home (E-447): what someone
        showed by putting them out sooner, or – too short – by switching them on again."""
        self._learn_minutes("light_outside_arrival", "on_minutes", area_id, minutes, now, weight)

    def known_here(self, skill: str, area_id: str) -> bool:
        """This skill knows something at this place: an automation taught it, HouseMind saw it
        there, or it has a step (E-447: the lamps at the entrance)."""
        return (skill, area_id) in self.first_seen or (skill, area_id) in self.steps

    def _hold_seen(self, area_id: str, minutes: float, now: datetime) -> None:
        """A person or automation put the lamps of an empty room out after this many minutes:
        what this house does there. Several lamps at once are one moment."""
        last = self._hold_at.get(area_id)
        if last is not None and timedelta(0) <= now - last < timedelta(minutes=1):
            return
        self._hold_at[area_id] = now
        self._learn_hold(area_id, minutes, now, 1.0)

    def evening_off_seen(
        self, change: StateChange, area_id: str, context: str, local: datetime, now: datetime
    ) -> None:
        """A person or automation put a lamp outside out late in the evening or in the night,
        while the house was not going to sleep: the time this house ends its evening light
        outside (E-446). Several lamps at once are one moment."""
        if change.source not in (Source.PERSON, Source.PHYSICAL, Source.AUTOMATION):
            return
        if change.old is None or change.new is None or not change.entity_id.startswith("light."):
            return
        if change.old.state != "on" or change.new.state != "off":
            return
        hour = local.hour
        if not (hour >= EVENING_OFF_FROM or hour < 4):
            return  # earlier in the evening is someone done on the terrace, not the end
        last = self._evening_off_at.get(area_id)
        if last is not None and timedelta(0) <= now - last < timedelta(minutes=10):
            return
        self._evening_off_at[area_id] = now
        after = (hour - 12 if hour >= 12 else hour + 12) * 60 + local.minute
        self._learn_value("light_outside_evening", area_id, context, "off_minutes", after, now)

    def wake_open_seen(
        self,
        change: StateChange,
        area_id: str,
        context: str,
        local: datetime,
        now: datetime,
        sun: float | None,
        minutes: float | None,
        signal: str,
    ) -> None:
        """A person or automation opened the curtains in a bedroom in the morning (E-484, design
        71.6): how light it was around dawn, and how many ``minutes`` after getting up – or,
        where HouseMind sees no sleep (``signal`` "none"), after the house woke. Several
        curtains at once are one moment."""
        if change.source not in (Source.PERSON, Source.PHYSICAL, Source.AUTOMATION):
            return
        if change.old is None or change.new is None or not change.entity_id.startswith("cover."):
            return
        if change.new.state != "open" or change.old.state in ("open", "opening"):
            return
        if not 4 <= local.hour < 12:
            return
        last = self._wake_open_at.get(area_id)
        if last is not None and timedelta(0) <= now - last < timedelta(minutes=10):
            return
        self._wake_open_at[area_id] = now
        if sun is not None and OPEN_SUN[0] <= sun <= OPEN_SUN[1]:
            self._learn_value("covers_open_wake", area_id, context, "sun_elevation", sun, now)
        if minutes is None:
            return
        key, bounds = (
            ("after_wake_minutes", WAKE_AFTER) if signal == "none" else ("wait_minutes", WAKE_WAIT)
        )
        if bounds[0] <= minutes <= bounds[1]:
            value = max(1.0, minutes) if key == "wait_minutes" else minutes
            self._learn_value("covers_open_wake", area_id, context, key, value, now)

    def seed_hold(self, area_id: str, minutes: float, now: datetime) -> None:
        """The wait of an automation HouseMind already learned from: start knowledge, once."""
        if ("light_off_leave", area_id, "*", "hold_minutes") not in self.values:
            self._learn_hold(area_id, minutes, now, AUTOMATION_SEED)

    def held_too_short(self, area_id: str, minutes: float, now: datetime) -> None:
        """Someone was still there or came back – during the dim warning, or by switching the
        lamps on again just after HouseMind put them out: half as long again, and it counts
        double, like a correction (E-92)."""
        self._learn_hold(area_id, 1.5 * minutes, now, 2.0)

    def passing(self, area_id: str, now: datetime) -> bool:
        """People mostly walk through this room in the dark without light: coming in counts
        only after they stayed a while (E-270)."""
        passed = stayed = 0.0
        for measure in (VISIT, HISTORY + VISIT):
            if (found := self.levels.get((area_id, measure, "*", PASS))) is not None:
                passed += _decayed(found, now)
            if (found := self.levels.get((area_id, measure, "*", STAY))) is not None:
                stayed += _decayed(found, now)
        total = passed + stayed
        return total >= PASS_SAMPLES - EPS and passed / total >= PASS_SHARE

    def _level(self, area_id: str, levels: dict[str, float], outcome: str, now: datetime) -> None:
        for measure, value in levels.items():
            key = (area_id, measure, band(measure, value), outcome)
            current = self.levels.get(key)
            self.levels[key] = _Count((_decayed(current, now) if current else 0.0) + 1.0, now)
            self._levels_changed = True
            self._tables.pop((area_id, measure), None)

    def _recently(self, entity_ids: tuple[str, ...], state: str, since: datetime) -> float | None:
        """The weight of who set one of them to ``state`` since then (1 a person or switch,
        ½ an automation), or None."""
        found = [
            seen[2]
            for entity_id in entity_ids
            if (seen := self.switched.get(entity_id)) is not None
            and seen[0] == state
            and seen[1] >= since
        ]
        return max(found) if found else None

    def arrived(
        self,
        skill: str,
        area_id: str,
        lamps: list[str],
        now: datetime,
        levels: dict[str, float],
        dark: bool,
        lit: bool,
        context: str,
        group: str,
        since: datetime,
    ) -> None:
        """Someone came into a place where no lamp was on, or where one just went on (E-268).
        A lamp switched on in the moment before is people being quicker than the sensor: light
        wanted, and – when it was dark – right for light when coming in."""
        found = self._recently(tuple(lamps), "on", since)
        if found is not None:
            self._level(area_id, levels, LIGHT, now)
            if dark:
                self._add(skill, area_id, context, Result.HIT, now, found)
            return
        if lit:
            return  # lit by HouseMind or long before: nothing to learn about the dark
        self.arrivals = [a for a in self.arrivals if a.group != group]
        deadline = now + timedelta(seconds=WINDOWS["light"])
        self.arrivals.append(Arrival(area_id, tuple(lamps), now, deadline, dict(levels), group))

    # -- one open space or two rooms, as people show it (E-530) -----------------------

    def open_entry(self, area_id: str, lamps: list[str], others: list[str], now: datetime) -> None:
        """Someone came into this room in the dark, its own lamps off, while the rooms
        ``others`` of the same open space were lit: what people do now teaches whether that
        light does for this room."""
        if not lamps or not others:
            return
        self.open_entries = [e for e in self.open_entries if e.area_id != area_id]
        deadline = now + timedelta(seconds=WINDOWS["light"])
        self.open_entries.append(
            OpenEntry(area_id, tuple(lamps), tuple(sorted(others)), now, deadline)
        )

    def _settle_open(self, entry: OpenEntry, outcome: str, weight: float, now: datetime) -> None:
        if entry not in self.open_entries:
            return
        self.open_entries.remove(entry)
        for other in entry.others:
            key = (entry.area_id, OPEN, other, outcome)
            current = self.levels.get(key)
            self.levels[key] = _Count((_decayed(current, now) if current else 0.0) + weight, now)
        self._levels_changed = True

    def _open_counts(self, area_id: str, other: str, now: datetime) -> tuple[float, float]:
        """How often people let the light of ``other`` do for this room, and how often they
        wanted its own – what they did, and half of what the history said."""
        shared = apart = 0.0
        for kind in (OPEN, HISTORY + OPEN):
            if (found := self.levels.get((area_id, kind, other, SHARED))) is not None:
                shared += _decayed(found, now)
            if (found := self.levels.get((area_id, kind, other, APART))) is not None:
                apart += _decayed(found, now)
        return shared, apart

    def shares_light(self, area_id: str, other: str, now: datetime) -> bool:
        """The light of ``other`` does for this room, as people showed it (E-530). Until they
        did, often enough, every room has its own light."""
        shared, apart = self._open_counts(area_id, other, now)
        total = shared + apart
        return total >= OPEN_SAMPLES - EPS and shared / total >= OPEN_SHARE

    def shared_with(self, area_id: str, others: Iterable[str], now: datetime) -> dict[str, int]:
        """The rooms whose light does for this one, with how often people showed it."""
        out: dict[str, int] = {}
        for other in sorted(others):
            if self.shares_light(area_id, other, now):
                out[other] = round(sum(self._open_counts(area_id, other, now)))
        return out

    def _settle_arrival(self, arrival: Arrival, outcome: str | None, now: datetime) -> None:
        if arrival in self.arrivals:
            self.arrivals.remove(arrival)
            if outcome is not None:
                self._level(arrival.area_id, arrival.levels, outcome, now)

    def done_before(
        self,
        skill: str,
        area_id: str,
        entity_ids: list[str],
        state: str,
        now: datetime,
        context: str,
        within: float | None = None,
    ) -> bool:
        """The moment HouseMind would act came, and people or an automation had already done it
        in the window before (covers closed at sunset, E-268): right. ``within``: seconds, for a
        skill with its own window (the evening light outside, E-446)."""
        domain = entity_ids[0].split(".", 1)[0] if entity_ids else ""
        since = now - timedelta(seconds=within or WINDOWS.get(domain, 600))
        found = self._recently(tuple(entity_ids), state, since)
        if found is not None:
            self._add(skill, area_id, context, Result.HIT, now, found)
        return found is not None

    # -- predictions and observations ---------------------------------------------------

    def predicted(
        self, request: ActionRequest, decision: Decision, now: datetime, trial: bool = False
    ) -> None:
        if request.skill is None or request.area_id is None:
            return
        if decision.outcome not in (Outcome.OBSERVED, Outcome.PROPOSED, Outcome.EXECUTED):
            return
        domain = request.entity_id.split(".", 1)[0]
        desired = _desired(request.action, request.data)
        window: float = (OFF_WINDOWS if desired == "off" else WINDOWS).get(domain, 600)
        own = request.facts.get("window")
        if isinstance(own, int | float) and not isinstance(own, bool):
            window = float(own)  # a skill with its own window (E-446)
        seen = trial and self.someone_there(request.area_id)
        deadline = now + timedelta(seconds=window)
        until = request.facts.get("until")
        if isinstance(until, str):
            # Something that ends by itself, like waking up: switching the light off once the
            # person is up is the natural end, not "not like that".
            with contextlib.suppress(ValueError):
                deadline = min(deadline, datetime.fromisoformat(until))
        # One open prediction per entity: a newer one replaces the older.
        self.pending = [p for p in self.pending if p.entity_id != request.entity_id]
        self.pending.append(
            Prediction(
                request.skill,
                request.area_id,
                request.entity_id,
                request.action,
                dict(request.data),
                str(request.facts.get("context", "")),
                now,
                deadline,
                decision.outcome is Outcome.EXECUTED,
                trial,
                group if isinstance(group := request.facts.get("group"), str) else None,
                tuple(str(x) for x in request.facts.get("open_space") or ()),
                lit_nearby=bool(request.facts.get("open_lit")),
                proposed=decision.outcome is Outcome.PROPOSED,
                seen=seen,
                hold=float(hold)
                if isinstance(hold := request.facts.get("hold_minutes"), int | float)
                else None,
                nobody_home=bool(request.facts.get("nobody_home")),
            )
        )
        if decision.outcome in (Outcome.PROPOSED, Outcome.EXECUTED):
            if request.skill == "light_on_entry":
                for entry in self.open_entries:
                    if entry.area_id == request.area_id and request.entity_id in entry.lamps:
                        executed = decision.outcome is Outcome.EXECUTED
                        if entry.housemind != "executed":
                            entry.housemind = "executed" if executed else "proposed"
            for arrival in self.arrivals:
                if arrival.group == group:  # HouseMind's own part: the answer or a correction
                    executed = decision.outcome is Outcome.EXECUTED
                    arrival.housemind = "executed" if executed else "proposed"
                    if not executed:
                        arrival.deadline = now + timedelta(minutes=30)
        self.first_seen.setdefault((request.skill, request.area_id), now)

    def observe(
        self,
        change: StateChange,
        area_id: str | None,
        context: str,
        now: datetime,
        left_room: bool = False,
        sun: float | None = None,
        empty_minutes: float | None = None,
        still_there: bool = False,
    ) -> list[Result]:
        """A change by a person, wall switch or automation (E-41 counts automations too).
        ``left_room``: a lamp of a room where nobody is, as far as HouseMind can tell;
        ``sun``: the height of the sun now (covers closing teach the moment, E-268);
        ``empty_minutes``: how long that room was empty then (E-444);
        ``still_there``: a lamp of a room where the sensor still sees someone (E-460)."""
        results: list[Result] = []
        if change.source not in (Source.PERSON, Source.PHYSICAL, Source.AUTOMATION):
            return results
        self.expire(now)  # a prediction whose window has passed no longer counts
        domain = change.entity_id.split(".", 1)[0]
        if change.new is not None and change.new.state in ("opening", "closing"):
            self._moving[change.entity_id] = (change.source, now)
        actual = _actual(domain, change)
        if actual is None:
            return results
        source: Source = change.source
        moving = self._moving.pop(change.entity_id, None)
        if source is Source.PHYSICAL and moving is not None and now - moving[1] <= MOVING:
            source = moving[0]  # the end of what an automation or person started
        weight = 0.5 if source is Source.AUTOMATION else 1.0
        self.switched[change.entity_id] = (actual, now, weight)
        self._undone_at_once(change.entity_id, source, actual, now)
        for entry in [e for e in self.open_entries if change.entity_id in e.lamps]:
            if actual == "on":
                self._settle_open(entry, APART, weight, now)  # its own light wanted
            elif actual == "off" and entry.housemind == "executed":
                self._settle_open(entry, SHARED, weight, now)  # HouseMind's light put out
        for arrival in [a for a in self.arrivals if change.entity_id in a.lamps]:
            if actual == "on":
                self._settle_arrival(arrival, LIGHT, now)
            elif actual == "off" and arrival.housemind == "executed":
                self._settle_arrival(arrival, NONE, now)  # HouseMind's light put out
        if actual == "on":
            # A lamp of the open space went on: people chose their light (E-268) – the room
            # next to it staying dark is no "not like that" any more.
            groups = {
                p.group or p.entity_id
                for p in self.pending
                if change.entity_id in p.withdraw and not p.executed
            }
            for prediction in self.pending:
                if (prediction.group or prediction.entity_id) in groups:
                    prediction.lit_nearby = True
        for prediction in [p for p in self.pending if p.entity_id == change.entity_id]:
            if prediction not in self.pending:
                continue  # its place was settled already
            same = _desired(prediction.action, prediction.data) == actual
            if prediction.group is not None and not prediction.executed and not same:
                continue  # one lamp of the place doing something else answers nothing
            self.pending.remove(prediction)
            if prediction.executed and same:
                continue  # HouseMind's own change coming back (a cover that arrives later)
            if prediction.group is not None:  # the whole place counts once (B-116)
                self.pending = [p for p in self.pending if p.group != prediction.group]
            if prediction.executed and prediction.lit_nearby:
                continue  # put out with the room next door lit: that light does here (E-530)
            if prediction.executed:
                # HouseMind already did it: a person changing it back is a correction (E-92).
                result = Result.TRIAL_BAD if prediction.trial else Result.CORRECTION
                self.corrections.append(prediction)
                if (
                    prediction.skill in ("light_off_leave", "light_outside_arrival")
                    and prediction.hold is not None
                    and actual == "on"
                    and now - prediction.at <= HOLD_TOO_SOON
                ):
                    if prediction.skill == "light_off_leave":
                        self.held_too_short(prediction.area_id, prediction.hold, now)
                    else:  # the lamps at the entrance, out too soon (E-447)
                        self.arrival_minutes(prediction.area_id, 1.5 * prediction.hold, now, 2.0)
                self._add(
                    prediction.skill,
                    prediction.area_id,
                    prediction.context,
                    result,
                    now,
                    2.0 * weight,
                )
                results.append(result)
            else:
                result = Result.HIT if same else Result.MISS
                self._add(
                    prediction.skill, prediction.area_id, prediction.context, result, now, weight
                )
                results.append(result)
                if same and source is Source.AUTOMATION:
                    self._just_done[change.entity_id] = (
                        prediction.skill,
                        prediction.area_id,
                        prediction.context,
                        now,
                        actual,
                    )
                elif same and prediction.proposed:
                    # Done by hand: the runtime looks whether a question is open (E-460).
                    self.done_by_hand.append(
                        (prediction.skill, prediction.area_id, prediction.context)
                    )
        if (
            left_room
            and empty_minutes is not None
            and area_id is not None
            and actual == "off"
            and change.old is not None
            and change.old.state == "on"
        ):
            self._hold_seen(area_id, empty_minutes, now)
        if (
            left_room
            and not results
            and area_id is not None
            and actual == "off"
            and change.old is not None
            and change.old.state == "on"
        ):
            # A lamp switched off in an empty room before HouseMind's own wait was over: what
            # "light off when leaving" would do, only sooner – so an automation quicker than
            # HouseMind teaches it too, not only its misses.
            self._add("light_off_leave", area_id, context, Result.HIT, now, weight)
            results.append(Result.HIT)
            if source is Source.AUTOMATION:
                self._just_done[change.entity_id] = (
                    "light_off_leave",
                    area_id,
                    context,
                    now,
                    actual,
                )
        if area_id is not None and actual == "on":
            self.off_seen.pop(area_id, None)  # the lamp is back: nobody left
        if (
            still_there
            and not results
            and area_id is not None
            and source is not Source.AUTOMATION
            and actual == "off"
            and change.old is not None
            and change.old.state == "on"
        ):
            # Put out by someone the sensor still sees: if they leave, it was this (E-460).
            self.off_seen[area_id] = (now, context)
        if area_id is not None and change.new is not None:
            self._learn_from_person(change, area_id, context, now, sun)
        return results

    def _undone_at_once(self, entity_id: str, source: Source, actual: str, now: datetime) -> None:
        """Someone undid what an automation just did (E-460): it counts against the proof, with
        the weight of the automation's own count, so it is one against one."""
        done = self._just_done.get(entity_id)
        if done is None:
            return
        skill, area_id, context, at, state = done
        if actual == state or source is Source.AUTOMATION:
            return
        del self._just_done[entity_id]
        if now - at <= PUT_BACK_WINDOW:
            self._add(skill, area_id, context, Result.PUT_BACK, now, 0.5, plain=False)

    def settle_off(self, empty: Callable[[str], bool], now: datetime) -> None:
        """Who put the lamp out and then walked away did "light off when leaving", even though
        the sensor still saw them when the lamp went out (E-460)."""
        for area_id, (at, context) in list(self.off_seen.items()):
            if now - at > OFF_THEN_LEFT:
                del self.off_seen[area_id]
            elif empty(area_id):
                del self.off_seen[area_id]
                self._add("light_off_leave", area_id, context, Result.HIT, now)

    def _learn_from_person(
        self,
        change: StateChange,
        area_id: str,
        context: str,
        now: datetime,
        sun: float | None = None,
    ) -> None:
        assert change.new is not None
        attrs = change.new.attributes
        domain = change.entity_id.split(".", 1)[0]
        if domain == "cover" and change.new.state == "closed" and sun is not None:
            # Closed in the afternoon or evening around dusk: the moment for this room (C1).
            hour = context.split("|", 1)[1][:2] if "|" in context else ""
            last = self._closed_at.get(area_id)
            if (
                hour.isdigit()
                and int(hour) >= 12
                and CLOSE_SUN[0] <= sun <= CLOSE_SUN[1]
                and (last is None or now - last >= timedelta(hours=12))
            ):
                self._closed_at[area_id] = now
                self._learn_value("covers_close_dark", area_id, context, "sun_elevation", sun, now)
        if domain == "light" and change.new.state == "on":
            if change.entity_id not in self.lights_used[area_id]:
                self.lights_used[area_id].add(change.entity_id)
                self._lamps_changed = True
            brightness = attrs.get("brightness")
            if isinstance(brightness, int | float):
                felt = self.felt(change.entity_id, brightness * 100 / 255)  # B-410
                for skill in ("light_on_entry", "night_light"):
                    self._learn_value(skill, area_id, context, "brightness_pct", felt, now)
            kelvin = attrs.get("color_temp_kelvin")
            if isinstance(kelvin, int | float):
                for skill in ("light_on_entry", "night_light"):
                    self._learn_value(
                        skill, area_id, context, "color_temp_kelvin", float(kelvin), now
                    )
        if domain == "climate":
            # What people set while heating, and while cooling, are two wishes (E-315).
            wish = {"heat": "heat_on_presence", "cool": "cool_on_presence"}.get(change.new.state)
            target = attrs.get("temperature")
            if wish is not None and isinstance(target, int | float):
                self._learn_value(wish, area_id, context, "temperature", float(target), now)

    def expire(self, now: datetime) -> list[Result]:
        """Predictions nobody acted on: a miss for observed ones, a good trial for executed ones."""
        for arrival in [a for a in self.arrivals if a.deadline <= now]:
            # No lamp went on: no light wanted – unless HouseMind lit it and nobody put it out;
            # after a proposal the answer counts, and an unanswered one teaches nothing.
            outcome = {"executed": LIGHT, "proposed": None}.get(arrival.housemind, NONE)
            self._settle_arrival(arrival, outcome, now)
        for entry in [e for e in self.open_entries if e.deadline <= now]:
            # Still there without this room's own light, the room next door lit: that light
            # did – also with a question nobody answered. HouseMind's light left on teaches
            # nothing.
            if entry.housemind != "executed" and self.someone_there(entry.area_id):
                self._settle_open(entry, SHARED, 1.0, now)
            elif entry in self.open_entries:
                self.open_entries.remove(entry)
        results: list[Result] = []
        for prediction in [p for p in self.pending if p.deadline <= now]:
            if prediction not in self.pending:
                continue  # its place was counted already
            self.pending.remove(prediction)
            if prediction.group is not None:  # the whole place counts once (B-116)
                self.pending = [p for p in self.pending if p.group != prediction.group]
            if prediction.lit_nearby and not prediction.executed:
                continue  # the open space was lit: nothing to count (E-268)
            if not prediction.executed and prediction.skill in self.answers_only:
                self._situation(prediction.skill, prediction.area_id, now)
                continue
            if prediction.proposed and not prediction.executed:
                continue  # a question nobody answered, and nobody did otherwise: no "no" (B-178)
            if prediction.nobody_home and not prediction.executed:
                continue  # nobody home to do it: nothing to count (E-446)
            if prediction.executed and prediction.trial:
                # A trial counts only when someone was there to put it back (B-177).
                seen = prediction.seen or self.someone_there(prediction.area_id)
                if not seen:
                    continue
                result = Result.TRIAL_OK
            elif prediction.executed:
                # HouseMind's own action that nobody put back is no proof that people do it
                # so (B-175): kept apart, never a hit or a situation.
                result = Result.KEPT
            else:
                result = Result.MISS
            self._add(prediction.skill, prediction.area_id, prediction.context, result, now)
            results.append(result)
        return results

    def answered(
        self,
        skill: str,
        area_id: str,
        context: str,
        approved: bool,
        now: datetime,
        strong: bool = False,
        setback: bool = True,
    ) -> None:
        """A person answered a proposal (E-133). "Always" / "Never" count double. A "no"
        counts towards a step back (20.9), except "Not now" (``setback=False``): that only
        brings the question back later."""
        weight = 2.0 if strong else 1.0
        self.drop_pending(skill, area_id)  # answered: no miss for the same moment (B-178)
        for arrival in [a for a in self.arrivals if a.area_id == area_id and a.housemind]:
            if skill == "light_on_entry":  # light wanted, or not, when coming in (E-268)
                self._settle_arrival(arrival, LIGHT if approved else NONE, now)
        self._add(
            skill,
            area_id,
            context,
            Result.APPROVED if approved else Result.REJECTED,
            now,
            weight,
            setback,
        )
        self._add(skill, area_id, context, Result.HIT if approved else Result.MISS, now, weight)

    def answered_by_hand(self, skill: str, area_id: str, context: str, now: datetime) -> None:
        """Someone did what the open question asks (E-460): an answer, on top of the hit."""
        self._add(skill, area_id, context, Result.APPROVED, now)

    def drop_pending(self, skill: str, area_id: str) -> None:
        """A question about this moment was answered (or 'not now'): it counts no further."""
        self.pending = [
            p
            for p in self.pending
            if not (p.skill == skill and p.area_id == area_id and not p.executed)
        ]

    def liked(self, skill: str, area_id: str, context: str, now: datetime) -> None:
        """👍 on something HouseMind did (E-298): a light yes – no answer or situation of its
        own (QA 2, B-179)."""
        self._add(skill, area_id, context, Result.APPROVED, now, 0.5, plain=False)
        self._add(skill, area_id, context, Result.HIT, now, 0.5, plain=False)

    def corrected(self, skill: str, area_id: str, context: str, now: datetime) -> None:
        """Undo (E-92): a person put back what HouseMind did – a correction, as when they
        switch it back themselves (20.9)."""
        self._add(skill, area_id, context, Result.CORRECTION, now, 2.0)

    # -- steps --------------------------------------------------------------------------

    def may_try(self, skill: str, area_id: str, now: datetime) -> bool:
        """Whether HouseMind may do it without asking, as a trial (E-57): when it knows it by
        the thresholds of E-56, or – start knowledge – when the lower bound of its confidence is
        at least TRY_CONFIDENCE after TRY_DAYS of watching (QA 2, B-176)."""
        if self.knows(skill, area_id, now):
            return True
        _, _, days = self.score(skill, area_id, now)
        if skill in self.answers_only:
            approved, rejected, _ = self.approval(skill, area_id, now)
            sure = wilson_lower_bound(approved, approved + rejected)
        else:
            sure = self.confidence(skill, area_id, now)
        return sure >= TRY_CONFIDENCE and days >= TRY_DAYS

    def step(self, skill: str, area_id: str | None) -> Step:
        """The stored step only (what review decided)."""
        if area_id is None:
            return Step.OBSERVE
        return self.steps.get((skill, area_id), Step.OBSERVE)

    def step_at(self, skill: str, area_id: str | None, now: datetime) -> Step:
        """The step that applies now. Start knowledge may be proposed two hours after
        installation, at the very first situation (E-160, S-04), unless people already often
        did otherwise there."""
        if area_id is None:
            return Step.OBSERVE
        stored = self.steps.get((skill, area_id))
        if stored is not None:
            return stored
        if skill in self.start_knowledge and now - self.installed_at >= START_PROPOSE_AFTER:
            score, n, _ = self.score(skill, area_id, now)
            if (
                skill in self.answers_only
                or n < START_DOUBT_SITUATIONS - EPS
                or score >= START_PROPOSE_SCORE
            ):
                return Step.PROPOSE
        return Step.OBSERVE

    async def review_steps(
        self, now: datetime, lifted: dict[tuple[str, str], Step] | None = None
    ) -> list[StepChange]:
        """Move steps up or down (E-56, E-57, E-160, S-01, 20.9). ``lifted`` is the step an
        administrator set for a pair, where it is higher than what was earned (20.10): two
        corrections or "no" within a week also take that one step back, for this pair only."""
        changes: list[StepChange] = []
        lifted = lifted or {}
        pairs = {(s, a) for (s, a, _, _) in self.counts} | set(self.steps) | set(self.first_seen)
        for skill, area_id in sorted(pairs):
            if self.asleep(skill):
                continue  # its part is off: its step stays as it was (E-400)
            pair = (skill, area_id)
            earned = self.step_at(skill, area_id, now)
            if pair not in self.steps and earned is Step.PROPOSE:
                # Start knowledge proposed: kept as a step, so that falling back is a real
                # step back with a message, never a silent one (QA 2, B-176).
                self.steps[pair] = Step.PROPOSE
                await self._save_step(skill, area_id, Step.PROPOSE, now, "start_knowledge")
            current = max(earned, lifted.get(pair, Step.OBSERVE))
            limits = self.thresholds(skill)
            score, _, _ = self.score(skill, area_id, now)
            # The share fades (new habits win); the number of answers is a plain count.
            approved, rejected, _ = self.approval(skill, area_id, now)
            # "Mostly no" is about this room only: other rooms do not send it back (E-316).
            own_approved, own_rejected, _ = self.approval(skill, area_id, now, shared=False)
            n, answers, days = self.earned(skill, area_id, now)
            again = self.earn.get(pair)
            setbacks = self.recent_setbacks(skill, area_id, now)
            if current is not Step.OBSERVE and len(setbacks) >= SETBACKS_FOR_STEP:
                lower = Step(int(current) - 1)
                kinds = set(setbacks)
                reason = "mixed" if len(kinds) > 1 else kinds.pop()
                by_admin = current > earned
                if by_admin:
                    self.held[pair] = lower
                _, plain_n, _ = self.score(skill, area_id, now)
                _, _, plain_answers = self.approval(skill, area_id, now)
                self.earn[pair] = Earn(now, plain_n, plain_answers, lower)
                self.setbacks[pair] = []
                self._earn_dirty.add(pair)
                new = min(earned, lower)
                if new is not earned or pair not in self.steps:
                    self.steps[pair] = new
                    await self._save_step(skill, area_id, new, now, reason)
                changes.append(StepChange(skill, area_id, current, lower, reason, by_admin))
                continue
            if again is not None and (now - again.since).total_seconds() / 86400 < EARN_AGAIN_DAYS:
                continue  # earning it again takes at least a week (20.9)
            new, reason = earned, ""
            if earned is Step.OBSERVE:
                if (
                    score >= limits.propose_score
                    and n >= limits.propose_situations - EPS
                    and days >= limits.propose_days
                ):
                    new, reason = Step.PROPOSE, "learned"
                elif (
                    skill in self.start_knowledge
                    and now - self.installed_at >= START_PROPOSE_AFTER
                    and n >= START_PROPOSE_SITUATIONS - EPS
                    and (score >= START_PROPOSE_SCORE or skill in self.answers_only)
                    # After a step back, start knowledge comes back only with proof (B-176).
                    and (again is None or self.may_try(skill, area_id, now))
                ):
                    new, reason = Step.PROPOSE, "start_knowledge"
            elif earned is Step.PROPOSE and (
                answers >= limits.autonomous_answers - EPS
                and approved / (approved + rejected) >= limits.autonomous_score
                and days >= limits.autonomous_days
                # Shared answers alone never make a room act by itself (E-316).
                and (
                    skill not in self.answers_only
                    or self.own_answers(skill, area_id) >= OWN_ANSWERS - EPS
                )
            ):
                new, reason = Step.AUTONOMOUS, "approved"
            elif (
                earned is Step.PROPOSE
                and own_rejected >= MOSTLY_NO
                and own_approved / (own_approved + own_rejected) < 0.5
            ):
                # Mostly "no", also "Not now": back to watching, and earning it again.
                _, plain_n, _ = self.score(skill, area_id, now)
                _, _, plain_answers = self.approval(skill, area_id, now)
                self.earn[pair] = Earn(now, plain_n, plain_answers, Step.OBSERVE)
                self._earn_dirty.add(pair)
                new, reason = Step.OBSERVE, "mostly_no"
            if new is not earned:
                self.steps[pair] = new
                await self._save_step(skill, area_id, new, now, reason)
                changes.append(StepChange(skill, area_id, earned, new, reason))
        await self.save()
        return changes

    async def forget_pair(self, skill: str, area_id: str, now: datetime) -> None:
        """'Vergeet dit' (E-94, 20.13): what HouseMind learned about this skill in this room
        goes – the counts, the values, the evidence from the history – and it watches again."""
        pair = (skill, area_id)
        for key in [k for k in self.counts if k[:2] == pair]:
            del self.counts[key]
            self._dirty.discard(key)
        for key in [k for k in self.values if k[:2] == pair]:
            del self.values[key]
        for found in (self.first_seen, self.setbacks, self.earn, self.held, self.taught_by):
            found.pop(pair, None)
        self.off_seen.pop(area_id, None)
        self.pending = [p for p in self.pending if (p.skill, p.area_id) != pair]
        for table in (
            "skill_counts",
            "skill_values",
            "skill_seen",
            "skill_earn",
            "history_evidence",
        ):
            await self.db.execute(
                f"DELETE FROM {table} WHERE skill = ? AND area_id = ?",  # noqa: S608 – fixed names
                pair,
            )
        self.steps[pair] = Step.OBSERVE
        await self._save_step(skill, area_id, Step.OBSERVE, now, "forgotten")

    async def drop_pair(self, skill: str, area_id: str, now: datetime) -> None:
        """A rule whose devices are gone for 30 days (E-487, B-402): forgotten like 'Vergeet
        dit', and its step goes too – a device that comes later starts afresh."""
        await self.forget_pair(skill, area_id, now)
        self.steps.pop((skill, area_id), None)
        await self.db.execute(
            "DELETE FROM skill_steps WHERE skill = ? AND area_id = ?", (skill, area_id)
        )
