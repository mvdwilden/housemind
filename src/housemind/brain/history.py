"""Learning from the history in Home Assistant (E-257, B-101).

Home Assistant's recorder keeps what happened, usually ten days. HouseMind reads those days
once – after the yes to learning, read only and local, never to an AI (E-15) – and plays them
again through a quiet copy of its brain: its own world (who is where, from the sensors, filled
from the start as in 15.3), the same skills and the same counting, on a played clock. It
switches nothing, asks nothing and says nothing. What it learns is taken over marked as
history, at half weight, fading from its own day; it can bring a skill in a room to Propose at
most, because Autonomous always needs real answers. Only the days before HouseMind learned
itself are read, so nothing counts twice; reading again replaces the earlier reading.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain.learning import NO_ACTION, Learning, Result, _desired
from housemind.brain.skills import SkillEngine
from housemind.core.model import Source, State, StateChange
from housemind.knowledge.catalog import Step
from housemind.safety.gate import Decision, Outcome
from housemind.world.calendar import day_type
from housemind.world.world import World

if TYPE_CHECKING:
    from housemind.ha.adapter import HistoryChange
    from housemind.runtime import HouseMind

_LOGGER = logging.getLogger(__name__)

# What the history may teach: the parts people steer by hand every day.
SKILL_DOMAINS = frozenset({"lighting", "covers", "climate", "air"})
SENSING = frozenset({"presence", "occupancy", "motion", "door", "window", "opening", "garage_door"})
TICK = timedelta(minutes=2)  # the played clock ticks between changes: waits come out right
WINDOW = timedelta(hours=6)  # read a quarter of a day at a time, small enough for a Pi
CHUNK = 60  # entities per question to Home Assistant
REPEAT_AFTER = timedelta(minutes=5)  # the same request again, as live
UNDO_WITHIN = timedelta(minutes=10)
# "Goed na" (E-268): what an automation switches in one room within a minute – a group and its
# lamps – is one thing it did; only what a skill could do counts.
SAME_ACTION = timedelta(minutes=1)
COPYABLE = frozenset({"light", "cover", "climate", "fan"})
# An automation sets a cover moving; the cover reports the end itself (without the automation).
TRANSIT = {"closing": "closed", "opening": "open"}
COPY_WINDOW = timedelta(minutes=30)  # right within the covers' window counts as copied
MAX_DAYS = 30


@dataclass
class AutomationHistory:
    """What the history says about one automation: how often it did something, how often
    someone put it back within ten minutes, and how often HouseMind would have done the same."""

    acted: int = 0
    undone: int = 0
    matched: int = 0


@dataclass
class HistoryResult:
    start: datetime
    end: datetime
    changes: int
    learning: Learning
    automations: dict[str, AutomationHistory]
    evidence: dict[tuple[str, str, str], tuple[int, int]]  # (skill, area, bucket) → (hit, of)

    @property
    def days(self) -> float:
        return (self.end - self.start).total_seconds() / 86400


class _ReplayLearning(Learning):
    """Learning that also notes on which days something went right, for "Waarom?"."""

    hit_days: dict[tuple[str, str, str], set[date]]
    tz: Any
    credited: Callable[[str, str, datetime], None] | None = None

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
        found = super().done_before(skill, area_id, entity_ids, state, now, context, within)
        if found and self.credited is not None:
            self.credited(area_id, state, now)  # an automation was quicker: copied
        return found

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
        super()._add(skill, area_id, context, result, now, weight, setback, plain)
        if result is Result.HIT and context:
            self.hit_days[(skill, area_id, context)].add(now.astimezone(self.tz).date())


def entities_to_read(hm: HouseMind) -> list[str]:
    """Lamps (also switches with the role lamp), covers, climate, fans, what senses people
    and openings, people, the sun and light sensors – nothing else."""
    assert hm.gate is not None and hm.world is not None
    roles = hm.gate.state.roles
    zones = set(hm.world.occupancy.zones.zones) | set(hm.world.occupancy.zones.general)
    found = []
    for entity in hm.model.entities.values():
        if entity.hidden or entity.disabled or entity.entity_category:
            continue
        domain, eid = entity.domain, entity.entity_id
        if (
            domain in ("light", "cover", "climate", "fan", "person")
            or roles.get(eid) == "light"
            or eid in zones
            or (domain == "binary_sensor" and entity.device_class in SENSING)
            or (domain == "sensor" and entity.device_class == "illuminance")
            or eid == "sun.sun"
        ):
            found.append(eid)
    if "sun.sun" not in found and "sun.sun" in (hm.world.states or {}):
        found.append("sun.sun")
    return sorted(found)


class Replay:
    """Play the history again through a quiet copy of the brain."""

    def __init__(
        self,
        hm: HouseMind,
        start: datetime,
        end: datetime,
        progress: Callable[[float], None] = lambda done: None,
    ) -> None:
        assert hm.learning is not None and hm.gate is not None and hm.world is not None
        assert hm.config is not None and hm.skills is not None
        self.hm, self.start, self.end, self.progress = hm, start, end, progress
        self.world = World(
            hm.model, hm.config.time_zone, hm.config.country, hm.area_roles, set(hm.pet_areas)
        )
        self.world.location = hm.place()  # HA keeps no height of the sun in its history
        self.world.occupancy.excluded = set(hm.gate.state.excluded)
        self.world.occupancy.zones = hm.world.occupancy.zones
        self.world.occupancy.index()
        self.world.lux_cache = {}  # the house does not change while replaying
        self.world.status_leds = hm.world.status_leds  # what a person said (B-359)
        self.world.status_led_ids = hm.world.status_led_ids  # and the rules' guess (E-426)
        self.world.functions = hm.world.functions  # every entity's function (E-427)
        self.world.function_sources = hm.world.function_sources
        self.world.light_sensors = hm.world.light_sensors
        learning = _ReplayLearning(hm.db, start, set(), answers_only=set(hm.learning.answers_only))
        learning.hit_days = defaultdict(set)
        learning.tz = self.world.tz
        learning.credited = self._credit
        self.learning = learning
        self.skills = SkillEngine(
            hm.catalog,
            hm.model,
            hm.area_roles,
            hm.skill_params,
            hm.gate.state.roles,
            learning,
        )
        self.skills.entity_cache = {}
        self.skills.openings = hm.skills.openings
        self.skills.open_space = hm.skills.open_space
        self.skills.room_climates = hm.skills.room_climates
        self.skills.not_lamps = hm.skills.not_lamps
        self.skills.hand_switches = hm.skills.hand_switches
        self.automations: dict[str, AutomationHistory] = defaultdict(AutomationHistory)
        self._last_request: dict[str, tuple[str, str, datetime]] = {}
        self._by_automation: dict[str, tuple[str, datetime, str | None, tuple[Any, ...]]] = {}
        self._actions: dict[tuple[str, str, str], list[Any]] = {}  # → [since, copied, undone]
        self._clock = start
        self._steps = 0
        self.changes = 0
        self._hands = hm.automations.hands()  # a button is a person (E-448)
        self._signals = hm.automations.signals()  # a signal teaches nothing (E-461)

    # -- the played brain -----------------------------------------------------------------

    def _domain(self, skill: str | None) -> str | None:
        return self.hm.skill_params.get(skill or "", {}).get("domain")

    def _think(self, now: datetime) -> None:
        snapshot = self.world.snapshot(now)
        for request in self.skills.evaluate(snapshot, self.world.states):
            if self._domain(request.skill) not in SKILL_DOMAINS - set(self.hm.parts_off):
                continue  # what HouseMind learns from the history, not of a part that is off
            key = (request.action, repr(sorted(request.data.items())))
            last = self._last_request.get(request.entity_id)
            if last is not None and last[:2] == key and now - last[2] < REPEAT_AFTER:
                continue
            self._last_request[request.entity_id] = (*key, now)
            decision = Decision(request, Outcome.OBSERVED, None, Step.OBSERVE, (), now)
            self.learning.predicted(request, decision, now)
        self.learning.expire(now)

    async def _tick_until(self, moment: datetime) -> None:
        while self._clock + TICK <= moment:
            self._clock += TICK
            self._think(self._clock)
            self._steps += 1
            await asyncio.sleep(0)  # room for the house itself after every step

    def _apply(self, change: HistoryChange) -> None:
        hand = self._hands.get(change.cause or "")
        if change.source is Source.AUTOMATION and hand is not None:
            # A button or switch did it, through an automation: the hand of the resident
            # (E-448) – a person, not that automation's doing.
            change = replace(change, source=hand, cause=None)
        elif change.source is Source.AUTOMATION and change.cause in self._signals:
            change = replace(change, source=Source.SIGNAL, cause=None)
        eid, now = change.entity_id, change.at
        old = self.world.states.get(eid)
        new = State(eid, change.state, dict(change.attributes), now)
        if old is not None and old.state == new.state and old.attributes == new.attributes:
            return
        if eid.startswith("sensor.") or eid == "sun.sun" or old is None:
            self.world.states[eid] = new  # a measurement, or the first we hear of it
            return
        if change.source is Source.PHYSICAL and old.state == new.state:
            self.world.states[eid] = new  # only details changed: nobody's action (B-50)
            return
        self.changes += 1
        moved = StateChange(eid, old, new, change.source)
        self.world.on_change(moved, now)
        if change.source is Source.HOUSEMIND:
            # HouseMind's own doing teaches nothing – not even a "not like that" for what it
            # predicted and then did itself.
            groups = {p.group for p in self.learning.pending if p.entity_id == eid and p.group}
            self.learning.pending = [
                p
                for p in self.learning.pending
                if p.entity_id != eid and (p.group is None or p.group not in groups)
            ]
            return
        snapshot = self.world.snapshot(now)
        left_room = self.hm._left_room(moved, snapshot)
        self._automation(change, old, now, left_room)
        # Learned through its group, or left alone: nothing of its own, as live (E-458).
        area = self.hm.model.area_of(eid) if self.hm._counts(eid) else None
        results = self.learning.observe(
            moved,
            area.id if area else None,
            snapshot.context_key(),
            now,
            left_room=left_room,
            sun=snapshot.sun,
        )
        if area is not None and Result.HIT in results:
            self._credit(area.id, change.state, now)
        self._think(now)

    def _automation(
        self, change: HistoryChange, old: State, now: datetime, left_room: bool
    ) -> None:
        """How often an automation acted, was put back, and what HouseMind would have done –
        per time and room, not per lamp (E-268)."""
        assert self.hm.gate is not None
        eid = change.entity_id
        area = self.hm.model.area_of(eid)
        wanted = TRANSIT.get(change.state, change.state)
        copyable = (
            area is not None
            and (eid.split(".", 1)[0] in COPYABLE or self.hm.gate.state.roles.get(eid) == "light")
            and wanted not in NO_ACTION
            and old.state not in ("unavailable", "unknown")
        )
        if change.source is Source.AUTOMATION and change.cause:
            if not copyable or area is None:
                return
            key = (change.cause, area.id, wanted)
            action = self._actions.get(key)
            if action is None or now - action[0] > SAME_ACTION:
                self.automations[change.cause].acted += 1
                action = self._actions[key] = [now, False, False]
            if not action[1] and self._copied(area.id, wanted, left_room):
                self.automations[change.cause].matched += 1
                action[1] = True
            self._by_automation[eid] = (change.cause, now, old.state, key)
            return
        done = self._by_automation.pop(eid, None)
        if (
            done is not None
            and change.source in (Source.PERSON, Source.PHYSICAL)
            and now - done[1] <= UNDO_WITHIN
            and change.state == done[2]
        ):
            action = self._actions.get(done[3])
            if action is not None and not action[2]:
                action[2] = True
                self.automations[done[0]].undone += 1

    def _credit(self, area_id: str, state: str, now: datetime) -> None:
        """HouseMind counted it right a little later – the cover arrived, or its own moment came
        just after the automation's: what the automation did in that room was copied."""
        for (cause, area, wanted), action in self._actions.items():
            if area == area_id and wanted == state and not action[1]:
                if now - action[0] <= COPY_WINDOW:
                    action[1] = True
                    self.automations[cause].matched += 1

    def _copied(self, area_id: str, state: str, left_room: bool) -> bool:
        """HouseMind expected the same for that room then – or, for "off", saw the room empty:
        what "light off when leaving" does, only after its own wait."""
        if any(
            p.area_id == area_id and _desired(p.action, p.data) == state
            for p in self.learning.pending
        ):
            return True
        return state == "off" and left_room

    # -- reading ---------------------------------------------------------------------------

    async def run(self) -> HistoryResult:
        hm = self.hm
        entities = entities_to_read(hm)
        windows: list[tuple[datetime, datetime]] = []
        at = self.start
        while at < self.end:
            windows.append((at, min(at + WINDOW, self.end)))
            at += WINDOW
        first = True
        for number, (begin, finish) in enumerate(windows):
            found: list[HistoryChange] = []
            for i in range(0, len(entities), CHUNK):
                found += await hm.ha.history(entities[i : i + CHUNK], begin, finish)
            found.sort(key=lambda c: c.at)
            if first:
                # The state at the start, and who is where from it (15.3).
                self.world.load_states(
                    {
                        c.entity_id: State(c.entity_id, c.state, dict(c.attributes), c.at)
                        for c in found
                        if c.at <= begin
                    }
                )
                self.world.prime(begin)
                self._think(begin)  # someone already coming in at the very start counts too
                first = False
            for change in found:
                if change.at <= begin and change.entity_id in self.world.states:
                    if self.world.states[change.entity_id].state == change.state:
                        continue
                await self._tick_until(change.at)
                self._apply(change)
                self._steps += 1
                await asyncio.sleep(0)  # room for the house itself
            await self._tick_until(finish)
            self.progress((number + 1) / len(windows))
            await asyncio.sleep(0)
        self.learning.pending = [p for p in self.learning.pending if p.deadline <= self.end]
        self.learning.expire(self.end)
        return HistoryResult(
            self.start,
            self.end,
            self.changes,
            self.learning,
            dict(self.automations),
            self._evidence(),
        )

    def _evidence(self) -> dict[tuple[str, str, str], tuple[int, int]]:
        """Per skill × room × kind of day × half hour: on how many of those days it happened
        ("9 of the 10 workdays around 18:30", E-162)."""
        days: dict[str, int] = defaultdict(int)
        day = self.start.astimezone(self.world.tz).date()
        last = self.end.astimezone(self.world.tz).date()
        country = self.hm.config.country if self.hm.config else None
        while day <= last:
            days[day_type(day, country).value] += 1
            day += timedelta(days=1)
        out: dict[tuple[str, str, str], tuple[int, int]] = {}
        assert isinstance(self.learning, _ReplayLearning)
        for (skill, area, bucket), hit in self.learning.hit_days.items():
            kind = bucket.split("|", 1)[0]
            if days.get(kind):
                out[(skill, area, bucket)] = (len(hit), days[kind])
        return out


@dataclass
class HistoryStatus:
    """What the screens show while and after reading (E-257)."""

    state: str = "idle"  # idle, reading, done, off, failed
    progress: float = 0.0
    days: float = 0.0
    available: float | None = None  # days Home Assistant keeps
    read_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    moved_at: datetime | None = None  # when the reading last went on (E-396)

    def view(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "progress": round(self.progress, 3),
            "days": round(self.days),
            "available": round(self.available) if self.available is not None else None,
            "readAt": self.read_at,
        }


# -- the job: read, take over, keep, tell -------------------------------------------------


async def load(hm: HouseMind) -> None:
    """At the start: what an earlier reading taught for "Waarom?" and the automations."""
    status = hm.history_status
    status.read_at = await hm.db.get_setting("history_read_at")
    status.days = float(await hm.db.get_setting("history_days", 0) or 0)
    if await hm.setting("history_learning") is False:
        status.state = "off"
    elif status.read_at:
        status.state = "done"
    evidence = {
        (str(r["skill"]), str(r["area_id"]), str(r["bucket"])): (int(r["hit"]), int(r["of_days"]))
        for r in await hm.db.fetchall("SELECT * FROM history_evidence")
    }
    for explainer in (hm.explainer, hm.vesta_explainer):
        if explainer is not None:
            explainer.history = evidence
    hm.history_automations = {
        str(r["automation_id"]): AutomationHistory(
            int(r["acted"]), int(r["undone"]), int(r["matched"])
        )
        for r in await hm.db.fetchall("SELECT * FROM history_automations")
    }
    hm._history_seen = set(await hm.db.get_setting("history_entities", []) or [])


async def _window(hm: HouseMind) -> tuple[datetime, datetime, float] | None:
    """From the oldest day Home Assistant keeps to the moment HouseMind began learning."""
    assert hm.learning is not None
    oldest = await hm.ha.history_start()
    now = hm.clock.now()
    if oldest is None:
        hm.history_status.available = 0
        return None
    hm.history_status.available = (now - oldest).total_seconds() / 86400
    since = await hm.db.get_setting("learning_since")
    end = min(datetime.fromisoformat(since) if since else hm.learning.installed_at, now)
    start = max(oldest, end - timedelta(days=MAX_DAYS))
    return (start, end, hm.history_status.available) if end - start >= timedelta(hours=1) else None


async def run(hm: HouseMind) -> HistoryResult | None:
    """Read the history once and take over what it taught (E-257)."""
    assert hm.learning is not None
    status = hm.history_status
    if not hm.learning_allowed or await hm.setting("history_learning") is False:
        status.state = "off" if hm.learning_allowed else "idle"
        return None
    window = await _window(hm)
    entities = entities_to_read(hm)
    now = hm.clock.now().isoformat()
    if window is None:
        status.state, status.days, status.read_at = "done", 0.0, now
        await hm.db.set_setting("history_read_at", now)
        await hm.db.set_setting("history_days", 0)
        await hm.db.set_setting("history_entities", entities)
        hm._history_seen = set(entities)
        return None
    start, end, _available = window
    status.state, status.progress, status.moved_at = "reading", 0.0, hm.clock.now()
    await hm._notify()
    told = [hm.clock.now()]

    def progress(done: float) -> None:
        now = hm.clock.now()
        if done > status.progress:
            status.moved_at = now  # Klaar counts its three minutes again (E-396)
        status.progress = done
        if now - told[0] >= PROGRESS_TOLD:
            told[0] = now  # the screens see the bar move, and Klaar wait on
            hm._beside(hm._notify(), "history-progress")

    result = await Replay(hm, start, end, progress).run()
    awake = SKILL_DOMAINS - set(hm.parts_off)  # a part that is off keeps what it had (E-400)
    skills = {s for s, p in hm.skill_params.items() if p.get("domain") in awake}
    await hm.learning.forget_history()
    hm.learning.absorb(result.learning, result.days, skills)
    await hm.learning.save()
    await hm.db.execute("DELETE FROM history_evidence")
    await hm.db.executemany(
        "INSERT INTO history_evidence(skill, area_id, bucket, hit, of_days) VALUES(?,?,?,?,?)",
        [(*key, hit, of) for key, (hit, of) in result.evidence.items()],
    )
    await hm.db.execute("DELETE FROM history_automations")
    await hm.db.executemany(
        "INSERT INTO history_automations(automation_id, acted, undone, matched) VALUES(?,?,?,?)",
        [(a, s.acted, s.undone, s.matched) for a, s in result.automations.items()],
    )
    for explainer in (hm.explainer, hm.vesta_explainer):
        if explainer is not None:
            explainer.history = dict(result.evidence)
    hm.history_automations = dict(result.automations)
    now = hm.clock.now().isoformat()
    status.state, status.progress, status.days, status.read_at = "done", 1.0, result.days, now
    await hm.db.set_setting("history_read_at", now)
    await hm.db.set_setting("history_days", round(result.days, 2))
    await hm.db.set_setting("history_entities", entities)
    hm._history_seen = set(entities)
    await hm.history_told(summary(hm, result))
    return result


PROGRESS_TOLD = timedelta(seconds=10)  # the screens hear how far the reading is, at most this often


def summary(hm: HouseMind, result: HistoryResult) -> dict[str, Any]:
    """Two or three of Vesta's sentences: how many days, what she knows well, what not yet –
    each written in every language, so whoever reads the message reads it in their own. They
    were written in the house's language: "Ik heb 4 dagen … Light when entering in the badkamer
    I already know well …" (found in the real house)."""
    t = hm.languages.text
    learning, now = result.learning, result.end
    best: tuple[float, str, str] | None = None
    seen: dict[str, float] = defaultdict(float)
    for skill, area in {(s, a) for (s, a, _, _) in learning.counts}:
        domain = hm.skill_params.get(skill, {}).get("domain")
        if domain not in SKILL_DOMAINS:
            continue
        score, n, _days = learning.score(skill, area, now)
        seen[str(domain)] += n
        if n >= 5 and score >= 0.85 and (best is None or score > best[0]):
            best = (score, skill, area)
    weak_parts = [d for d in ("lighting", "covers", "climate") if seen.get(d, 0) < 5]
    good: dict[str, str] = {}
    weak: dict[str, str] = {}
    for lang in hm.languages.languages:
        if best is not None:
            where = hm.names.where(best[2], lang) if hm.names else ""
            what = f"{t(lang, f'skills.{best[1]}')} {where}".strip()
            good[lang] = t(lang, "notify.history_good", what=what[:1].upper() + what[1:])
        if weak_parts:
            part = t(lang, f"ui.domain.{weak_parts[0]}").lower()
            weak[lang] = t(lang, "notify.history_weak", part=part)
    return {"days": str(round(result.days)), "good": {"lang": good}, "weak": {"lang": weak}}
