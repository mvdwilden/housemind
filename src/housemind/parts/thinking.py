"""The round of thinking (H2): every change from Home Assistant – world view, learning,
emergencies, skills, safety gate, then action, proposal or observation, and memory – and the
tick every 30 seconds for what time brings. Each action is timed from trigger to done (L8),
a change is worked out only where it matters (E-322, S2), and one failing step never stops
the round (QA 2, M-30)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time as timer
from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from housemind.brain import alarm_reaction, security
from housemind.brain.automations import Automations
from housemind.brain.briefing import (
    findings,
)
from housemind.brain.days import DayNotes
from housemind.brain.learning import Learning, StepChange
from housemind.brain.notify import (
    SAFETY_SKILLS,
    Kind,
    NotificationRouter,
    Person,
)
from housemind.brain.proposals import Answer, Proposals
from housemind.brain.rhythm import Rhythm
from housemind.brain.rules import RuleBook
from housemind.brain.skills import SkillEngine
from housemind.brain.speech import Speech
from housemind.core.clock import Clock
from housemind.core.model import Entity, HouseModel, Source, State, StateChange
from housemind.energy.manager import LEGACY_SKILLS, EnergyManager
from housemind.ha.adapter import HAConfig, HomeAssistant
from housemind.i18n.catalog import Languages
from housemind.knowledge.area_roles import SLEEPING_ROLES
from housemind.knowledge.catalog import Catalog, Step
from housemind.knowledge.matcher import recognise
from housemind.memory import snapshots
from housemind.memory.database import Database
from housemind.parts import Part
from housemind.safety.emergency import ALARMS, Emergency, EmergencyReactions
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome, SafetyGate
from housemind.world.climate import Temperatures
from housemind.world.house_mode import HouseMode
from housemind.world.occupancy import ZONE_SETTLE_S
from housemind.world.world import World, WorldState

if TYPE_CHECKING:
    from housemind.brain.lampday import LampDay
    from housemind.runtime import ActionListener, Paths, Status

_LOGGER = logging.getLogger(__name__)

# How long the Orb shows what the brain just did (E-36, B-23): violet a few seconds after it
# acted, proposed or answered; amber a while after it learned something.
LEARN_SHOW = timedelta(minutes=10)
# Registry changes come in bursts (an integration adds twenty entities): wait until quiet.
MODEL_SETTLE = timedelta(seconds=30)
# Weather in which a mower stays home (and without a forecast it does too).
RAINY = frozenset(
    {"rainy", "pouring", "lightning-rainy", "snowy", "snowy-rainy", "hail"}
)  # 'Was dat goed?' closes by itself after this (QA 2)
REPEAT_AFTER = timedelta(minutes=5)
REVIEW_EVERY = timedelta(minutes=15)


@dataclass(slots=True)
class Recent:
    """A recorded decision, with what the device looked like before (for undo, E-92)."""

    id: int
    decision: Decision
    previous: State | None
    undone: bool = False
    ms: float | None = None  # from what HouseMind saw to the request (E-478)


# A new or changed colour scheme file comes in by itself, without a restart (E-267). Files change
# in real time, so this is real seconds (not the time machine's).
SCHEMES_EVERY_S = 60.0
SENSOR_THINK_EVERY = timedelta(minutes=1)
# A light sensor in a room with someone and no lamp on: getting dark is the moment for light,
# so it thinks this often instead (E-443).
SENSOR_THINK_DARK = timedelta(seconds=5)
# Measurements the skills react to; every other sensor only updates the world view.
SKILL_SENSOR_CLASSES = {
    "illuminance",
    "temperature",
    "humidity",
    "carbon_dioxide",
    "co2",
    "pm25",
    "volatile_organic_compounds",
}
THUMBS_ASK_KEEP = timedelta(days=1)
TICK_SECONDS = 30
# Waking up (E-88): one "yes" the evening before holds for both skills that morning.
WAKE_SKILLS = ("wake_light", "wake_warmth")
WATCH_AGAIN = timedelta(minutes=15)  # the same thing only watched: one thought this often
SIGNAL_THINK_AGAIN = timedelta(minutes=2)  # a signal this short: look at once when it is over
ZWAVE_ON_OFF = frozenset({"light.turn_on", "light.turn_off", "switch.turn_on", "switch.turn_off"})
# The change being handled now (L8): when HA saw it and when HouseMind started on it, so an
# action it causes is timed from trigger to done. Per task, so a tick never borrows it.
_TRIGGER: ContextVar[tuple[float, float] | None] = ContextVar("trigger", default=None)


@contextlib.contextmanager
def _carry_on(step: str) -> Iterator[None]:
    """One step of the tick that fails never stops the steps after it, the clean-up included
    (QA 2, M-30): it is logged and the round goes on."""
    try:
        yield
    except Exception:
        _LOGGER.exception("Step %s of the tick failed", step)


class ThinkingPart(Part):
    """The round of thinking (H2): every change from Home Assistant – world view, learning,"""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        _action_listeners: list[ActionListener]
        _reactions_unsaved: bool
        knows: int
        lamp_day: LampDay
        _active: dict[str, Emergency]

        def _admin_lift(
            self, skill: str, area_id: str | None, entity_id: str | None = None
        ) -> Step | None: ...
        def part_off(self, request: ActionRequest) -> str | None: ...

        parts_off: dict[str, dict[str, str]]

        async def _ai_budget(self, snapshot: WorldState) -> None: ...

        _arrivals: dict[str, tuple[datetime, str | None]]

        async def _ask(self, proposal_id: int, decision: Decision) -> None: ...
        async def _ask_calendar(self, snapshot: WorldState) -> None: ...
        async def _ask_labels(self, snapshot: WorldState) -> None: ...
        async def _ask_was_it_good(self, snapshot: WorldState) -> None: ...
        def _asks_nobody(self, request: ActionRequest, planned: bool = False) -> bool: ...

        _automation_switched: Any

        async def _base_load_tip(self, snapshot: WorldState) -> None: ...
        async def _care(self, snapshot: WorldState) -> None: ...
        async def _unusual(self, snapshot: WorldState) -> None: ...
        async def _check_day(self, snapshot: WorldState) -> None: ...
        async def _check_speech(self, now: datetime) -> None: ...

        _check_version: Any

        async def _count(self, snapshot: WorldState) -> None: ...

        _device_heat: Any
        _done_text: Any
        _emergencies_lost: Any

        async def _expire_proposal_notifications(self, now: datetime) -> None: ...

        _finish_link: Any

        async def _follow_rhythm(self, snapshot: WorldState) -> None: ...

        _going_to_bed: Any
        _greet: Any
        _heat_cool_change: Any
        _heat_cool_dirty: Any
        _heat_cool_done: Any

        async def _household(self, snapshot: WorldState) -> None: ...
        async def _knowledge_update(self, snapshot: WorldState) -> None: ...
        def _language(self) -> str: ...

        _last_cleanup_day: str | None
        _last_request: dict[str, tuple[str, str, datetime]]
        _last_review: datetime | None
        _last_snapshot_day: str | None

        async def _learned(self, change: StepChange, snapshot: WorldState) -> None: ...
        def _learns_now(self) -> bool: ...
        def _test_touched(self, change: StateChange) -> None: ...
        async def _test_recorded(self, decision: Decision, previous: State | None) -> None: ...
        def in_test(self, entity_id: str, now: datetime) -> bool: ...
        def _lifted(self) -> dict[tuple[str, str], Step]: ...
        async def _lived_in(self, snapshot: WorldState) -> None: ...

        _lived_in_lit: set[str]
        _lost_since: dict[str, datetime]

        async def _maintenance(self, snapshot: WorldState) -> None: ...
        async def _tidy_beside(self, snapshot: WorldState) -> None: ...

        _measurements_changed: Any
        _energy_blocks: dict[str, str]
        _energy_asked: dict[str, str]
        _pauses_seen: tuple[Any, ...]

        def paused_until(self) -> datetime | None: ...
        def paused_domains(self) -> dict[str, datetime]: ...

        _model_dirty: datetime | None
        _morning: Any

        async def _notify(self, knows: bool = True) -> None: ...

        _occupied_before: set[str]
        _people: list[Person]

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
        ) -> int | None: ...

        _raised: dict[str, tuple[Kind, str, datetime]]
        _recover: Any
        _refresh_model: Any
        _refresh_orb: Any

        async def _refresh_people(self) -> None: ...
        async def _remember_house_mode(self, snapshot: WorldState) -> None: ...
        async def _remember_lamps(self) -> None: ...
        async def _resolve(self, topic: str) -> None: ...

        _review_automations: Any
        _review_proof: Any
        _save_heat_cool: Any
        _schemes_checked: Any

        async def _security_checks(self, snapshot: WorldState) -> None: ...

        _self_check: Any
        _sensor_thought: dict[str, datetime]

        async def _daylight_round(self, snapshot: WorldState) -> None: ...
        def _daylight_saw(self, change: StateChange, now: datetime) -> None: ...
        def _daylight_lit(self, request: ActionRequest, now: datetime) -> None: ...

        _recheck_task: asyncio.Task[Any] | None
        _recheck_at: datetime | None
        _set_emergency: Any
        _show_thinking: Any
        _side: set[asyncio.Task[Any]]

        async def _stepped_back(self, change: StepChange, snapshot: WorldState) -> None: ...

        _vesta_phones: Any
        _vesta_room: Any

        async def _wake_question(self, snapshot: WorldState) -> None: ...

        _watched: dict[tuple[str, str, str], datetime]

        async def _water(self, snapshot: WorldState) -> None: ...
        async def _weekly_report(self, snapshot: WorldState) -> None: ...

        activated: Any
        area_roles: dict[str, str]
        automations: Automations
        catalog: Catalog
        clock: Clock
        config: HAConfig | None
        days: DayNotes
        db: Database
        emergencies: EmergencyReactions | None
        energy: EnergyManager | None
        experimental: set[str]
        gate: SafetyGate | None
        ha: HomeAssistant
        languages: Languages
        last_not_done: dict[str, Decision]
        learning: Learning | None
        learning_allowed: Any

        def load_schemes(self, only_if_changed: bool = False) -> bool: ...

        model: HouseModel
        openings: set[str]
        paths: Paths
        proposals: Proposals
        recent: dict[int, Recent]
        rhythm: Rhythm
        router: NotificationRouter | None
        rules: RuleBook
        say: Any
        skill_params: dict[str, dict[str, Any]]
        skills: SkillEngine | None
        speech: Speech
        status: Status
        temperatures: Temperatures
        world: World | None

    def _beside(self, work: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
        """Slow work that need not hold up the next change from HA (M-31), or the round."""

        def done(task: asyncio.Task[Any]) -> None:
            self._side.discard(task)
            if not task.cancelled() and task.exception() is not None:
                _LOGGER.error("%s failed", name, exc_info=task.exception())

        task = asyncio.create_task(work, name=f"housemind-{name}")
        self._side.add(task)
        task.add_done_callback(done)
        return task

    async def settled(self) -> None:
        """Until the work beside the queue is done (for tests). Only work still running is
        waited for: a finished task leaves the set a moment later, and waiting for that
        without giving the loop a turn spins forever."""
        while running := [task for task in self._side if not task.done()]:
            await asyncio.gather(*running, return_exceptions=True)

    async def _tick_loop(self) -> None:
        while True:
            await self.clock.sleep(TICK_SECONDS)
            started = timer.perf_counter()
            try:
                await self.tick()
            except Exception:
                _LOGGER.exception("Tick failed")
            self.status.tick_ms = round((timer.perf_counter() - started) * 1000, 1)
            self.status.last_tick = self.clock.now()

    async def tick(self) -> None:
        assert self.world is not None and self.learning is not None
        now = self.clock.now()
        if self._reactions_unsaved:  # the reaction times survive a restart (code QA 3)
            self._reactions_unsaved = False
            await self.db.set_setting("reactions", self.status.reactions)
        if timer.monotonic() - self._schemes_checked >= SCHEMES_EVERY_S:
            self._schemes_checked = timer.monotonic()
            if self.load_schemes(only_if_changed=True):
                await self._notify()  # every screen gets the new list at once
        if self.status.ha_lost is not None:
            # Nothing new until Home Assistant is back (H7.4). Is it there, but did coming back
            # fail or hang, then try again now, so the tick never stops for good (M-29).
            if getattr(self.ha, "connected", False):
                await self._recover()
            return
        orb_before = self.status.orb
        if self._model_dirty is not None and now - self._model_dirty >= MODEL_SETTLE:
            self._model_dirty = None
            with _carry_on("model"):
                await self._refresh_model()
                await self._self_check()
        snapshot = self.world.snapshot(now)
        steps: list[tuple[str, Callable[[], Awaitable[Any]]]] = [
            ("emergencies_lost", lambda: self._emergencies_lost(snapshot)),
            ("link", lambda: self._finish_link(now)),
            ("bedtime", lambda: self._close_bedtime(snapshot)),
            ("rhythm", lambda: self._follow_rhythm(snapshot)),
            ("think", lambda: self._think(snapshot)),
            ("day_rhythm", lambda: self._daylight_round(snapshot)),  # E-466
            ("lived_in", lambda: self._lived_in(snapshot)),
            ("greet", lambda: self._greet(snapshot)),
            ("morning", lambda: self._morning(snapshot)),
            ("water", lambda: self._water(snapshot)),
            ("household", lambda: self._household(snapshot)),
            ("learning", lambda: self._expire_learning(now)),
            ("house_mode", lambda: self._remember_house_mode(snapshot)),
            ("lamps", self._remember_lamps),
            ("security_checks", lambda: self._security_checks(snapshot)),
            ("security", lambda: security.tick(self._hm, snapshot)),
            ("energy", lambda: self._energy_tick(now)),
            # An expired question leaves the list at once, not up to a quarter of an hour later.
            ("proposals", lambda: self._expire_proposal_notifications(now)),
        ]
        for name, step in steps:
            with _carry_on(name):
                await step()
        if self._measurements_changed:
            self._measurements_changed = False
            await self._notify(knows=False)  # new measurements reach the screens once per tick
        if self._last_review is None or now - self._last_review >= REVIEW_EVERY:
            self._last_review = now
            for name, step in self._review_steps(snapshot):
                with _carry_on(name):
                    await step()
        self._refresh_orb()  # what was raised, resolved or learned this round shows at once
        pauses = (self.paused_until(), tuple(sorted(self.paused_domains())))
        if self.status.orb != orb_before or pauses != self._pauses_seen:
            self._pauses_seen = pauses  # a pause that ended leaves the screens at once (E-83)
            await self._notify()
        local = snapshot.local
        if local.time() >= time(3, 0) and self._last_cleanup_day != local.date().isoformat():
            self._last_cleanup_day = local.date().isoformat()
            with _carry_on("cleanup"):
                await self.db.cleanup(now)
        if local.time() >= time(4, 0) and self._last_snapshot_day != local.date().isoformat():
            self._last_snapshot_day = local.date().isoformat()
            if self.activated:
                with _carry_on("snapshot"):
                    await snapshots.take(self.db, self.paths.data / "snapshots", local.date())

    async def _expire_learning(self, now: datetime) -> None:
        assert self.learning is not None
        if self._learns_now():
            self.learning.expire(now)
            if self.learning.off_seen and self.world is not None:
                occupancy = self.world.snapshot(now).occupancy
                self.learning.settle_off(
                    lambda a: (o := occupancy.get(a)) is not None and not o.occupied, now
                )
        else:
            self.learning.pending.clear()  # nothing of these days counts (E-286)
            self.learning.arrivals.clear()
            self.learning.off_seen.clear()

    async def _energy_tick(self, now: datetime) -> None:
        assert self.world is not None and self.gate is not None
        if self.energy is None:
            return
        states = self.world.states
        await self.energy.tick(now, states)
        self.energy.replan(states, now)
        self.energy.watch_own(states, now)  # somebody else changed it: let go (E-328)
        await self._info_only(now)
        everything, domains = self._energy_paused(now)
        if "energy" in self.parts_off:
            everything = True  # off (E-400): it gives back what it set itself, and plans nothing
        else:
            await self._energy_rest(now)
        requests: list[ActionRequest] = []
        if everything or domains:
            # A pause gives back what HouseMind set itself (E-343).
            only = None if everything else domains
            requests += self.energy.give_back(states, now, "paused", only, self.model.area_of)
        if not everything and self.status.emergency not in ALARMS:  # nothing during smoke/CO/gas
            requests += self.energy.requests(states, now, self.model.area_of)
        for request in requests:
            await self._energy_do(request, now)
        self.gate.state.owned = self.energy.owned()

    async def _energy_rest(self, now: datetime) -> None:
        """A part of energy that cost money for three weeks rests: the administrators hear why
        (E-378, B-276)."""
        assert self.world is not None and self.energy is not None
        ledger = self.energy.ledger
        if ledger is None or not ledger.news:
            return
        news, ledger.news = ledger.news, []
        snapshot = self.world.snapshot(now)
        for part, _amount in news:
            await self._raise(
                Kind.INFO,
                f"energy:rest:{part}",
                f"notify.energy_rest.{part}",
                {},
                snapshot,
                concerns="admins",
            )

    async def _info_only(self, now: datetime) -> None:
        """Once, after the update: what HouseMind still had set on the home battery or the
        charger goes back, and what it learned about steering them is wiped (B-248, K2). An
        entity that is unavailable now goes back as soon as it is there again."""
        assert self.world is not None and self.energy is not None
        energy = self.energy
        if energy.info_only:
            return
        for request in energy.legacy_give_back(self.world.states, now, self.model.area_of):
            await self._energy_do(request, now)
        if energy.legacy_left():
            return
        if self.learning is not None:
            await self.learning.forget_skills(set(LEGACY_SKILLS))
        for topic in (
            "battery_remote",
            "battery_calibrate",
            "battery_calibration_failed",
            "battery_not_working",
            "battery_recalibrate",
            "energy:car_overrun",
            "energy:car_not_charging",
        ):
            await self._resolve(topic)
        await energy.wipe_legacy()

    async def _energy_do(self, request: ActionRequest, now: datetime) -> None:
        """Carry out one energy request; ``_record`` keeps the register (B-203)."""
        assert self.world is not None and self.gate is not None and self.energy is not None
        self.gate.state.owned = self.energy.owned()
        if request.layer in (Layer.RESTORE, Layer.PERSON_ANSWER):
            before = self.world.states.get(request.entity_id)
            await self._record(await self.gate.submit(request), before)
        else:
            await self.consider(request)
        self.gate.state.owned = self.energy.owned()

    def _first_of_block(self, request: ActionRequest) -> bool:
        """Energy learns per step, not per adjustment (E-343)."""
        block = request.facts.get("block")
        if not block or not (request.skill or "").startswith("energy_"):
            return True
        if self._energy_blocks.get(str(request.skill)) == block:
            return False
        self._energy_blocks[str(request.skill)] = str(block)
        return True

    def _ask_in_block(self, request: ActionRequest) -> bool:
        """One question per step: after a yes, a no or an open question, the rest of that block
        asks nothing more (E-343)."""
        block = request.facts.get("block")
        if not block or not (request.skill or "").startswith("energy_"):
            return True
        if self._energy_asked.get(str(request.skill)) == block:
            return False
        self._energy_asked[str(request.skill)] = str(block)
        return True

    def _energy_paused(self, now: datetime) -> tuple[bool, set[str]]:
        """The whole house paused, or the energy devices whose kind is paused (E-83)."""
        assert self.gate is not None and self.energy is not None
        state = self.gate.state
        if state.paused_until is not None and now < state.paused_until:
            return True, set()
        paused = {d for d, until in state.paused_domains.items() if now < until}
        return False, {e for e in self.energy.owned() if e.split(".", 1)[0] in paused}

    async def give_back_energy(self, why: str) -> None:
        """HouseMind stops: everything it set itself goes back (E-329)."""
        if self.energy is None or self.world is None or self.gate is None:
            return
        now = self.clock.now()
        for request in self.energy.give_back(self.world.states, now, why, None, self.model.area_of):
            await self._energy_do(request, now)

    def _review_steps(self, snapshot: WorldState) -> list[tuple[str, Callable[[], Awaitable[Any]]]]:
        """Every quarter of an hour: people, steps, and what can wait that long."""
        now = snapshot.now
        sleeping = snapshot.mode.value == "sleep"
        router = self.router
        assert router is not None
        return [
            ("people", self._refresh_people),
            ("steps", lambda: self._review_learning(snapshot)),
            ("expire", lambda: self._expire_topics(now)),
            ("automations", lambda: self._review_automations(snapshot)),
            ("proof", lambda: self._review_proof(snapshot)),  # E-460
            ("ai_budget", lambda: self._ai_budget(snapshot)),
            ("base_load", lambda: self._base_load_tip(snapshot)),
            ("wake", lambda: self._wake_question(snapshot)),
            ("day", lambda: self._check_day(snapshot)),
            ("week", lambda: self._weekly_report(snapshot)),
            ("maintenance", lambda: self._maintenance(snapshot)),
            ("tidy", lambda: self._tidy_beside(snapshot)),  # E-351
            ("care", lambda: self._care(snapshot)),
            ("unusual", lambda: self._unusual(snapshot)),  # E-416
            ("knowledge", lambda: self._knowledge_update(snapshot)),
            ("calendar", lambda: self._ask_calendar(snapshot)),
            ("labels", lambda: self._ask_labels(snapshot)),
            ("was_it_good", lambda: self._ask_was_it_good(snapshot)),
            ("speech", lambda: self._check_speech(now)),
            ("version", lambda: self._check_version(snapshot)),
            ("escalate", lambda: router.escalate(self._people, now, snapshot.local, sleeping)),
            # What waited for someone's quiet hours goes out when they end (E-290).
            ("held", lambda: router.deliver_held(self._people, now, snapshot.local, sleeping)),
            ("bundle", lambda: router.bundle(self._people, snapshot.local)),
        ]

    async def _review_learning(self, snapshot: WorldState) -> None:
        assert self.learning is not None
        now = snapshot.now
        learns = self.learning_allowed and self.activated
        changes = await self.learning.review_steps(now, self._lifted()) if learns else []
        if not changes:
            return
        self.status.step_changes.extend(changes)
        del self.status.step_changes[:-50]
        self.status.learning_until = now + LEARN_SHOW
        for change in changes:
            if change.new > change.old:
                await self._learned(change, snapshot)
            else:
                await self._stepped_back(change, snapshot)
        await self._notify()

    async def _expire_topics(self, now: datetime) -> None:
        if self.router is None:
            return
        for topic in await self.router.expire_info(now):
            self._raised.pop(topic, None)
        # 'Was dat goed?' is about something just done: after a day it no longer matters.
        for topic in await self.router.expire_topics("thumbs:", THUMBS_ASK_KEEP, now):
            self._raised.pop(topic, None)

    async def _on_change(self, change: StateChange) -> None:
        """One change from HA. What it causes is timed from the moment HA saw it (L8); a
        moment more than 10 s ago is no trigger time (only details changed)."""
        lag = 0.0
        if change.new is not None:
            lag = (self.clock.now() - change.new.last_changed).total_seconds() * 1000
        token = _TRIGGER.set((timer.perf_counter(), lag if 0 <= lag < 10_000 else 0.0))
        try:
            await self._handle_change(change)
        finally:
            _TRIGGER.reset(token)

    async def _handle_change(self, change: StateChange) -> None:
        assert self.gate is not None and self.emergencies is not None
        assert self.world is not None and self.learning is not None
        now = self.clock.now()
        if change.entity_id.startswith("sensor."):
            await security.on_change(self._hm, change)  # a camera's counter (16.1)
            await self._on_measurement(change, now)
            return
        if change.entity_id.startswith("climate."):
            await self._heat_cool_change(change, now)  # before: a setpoint is only a detail
        self._daylight_saw(change, now)  # before: a dimmer on the wall is only a detail (E-467)
        # Vandaag met deze lamp (E-500): what it did and who went first.
        self.lamp_day.saw(
            change, (r.decision for r in self.recent.values()), now, self.model.groups
        )
        if (
            change.source is Source.PHYSICAL
            and change.old is not None
            and change.new is not None
            and change.old.state == change.new.state
        ):
            # Only details changed, and no person did it: an integration adjusting a lamp's
            # colour, a player's position, a switch reporting its attributes. That is nobody's
            # action – no presence, no "people win", nothing learned, no event row: such
            # updates come often and would make every room look occupied.
            self.world.states[change.entity_id] = change.new
            return
        # No raw event rows (QA 2, M-6): nothing needs them, and they would hold who was home
        # and where (person, tracker, zone). What HouseMind learns, it keeps as summaries.
        mode_before = self.world.modes.mode
        self.world.on_change(change, now)
        await security.on_change(self._hm, change)  # a camera's detection, the alarm (16.1, E-121)
        if change.entity_id.startswith("automation."):
            await self._automation_switched(change)
        snapshot = self.world.snapshot(now)
        area = self.model.area_of(change.entity_id)
        if not self._counts(change.entity_id):
            area = None  # learned through its group, or left alone: nothing of its own (E-458)
        self._test_touched(change)  # a hand on a device in a test: it stays so (E-476)
        # Not while "Nog niet actief" (E-275), nor on vacation (E-286), nor a device in a test
        # (E-479).
        if self._learns_now() and not self.in_test(change.entity_id, now):
            left = self._left_room(change, snapshot)
            occupancy = snapshot.occupancy.get(area.id) if left and area else None
            since = occupancy.last_activity if occupancy is not None else None
            self.learning.observe(
                change,
                area.id if area else None,
                snapshot.context_key(),
                now,
                left_room=left,
                sun=snapshot.sun,
                # How long the room was empty when the lamp went out (E-444).
                empty_minutes=(now - since).total_seconds() / 60 if since is not None else None,
                still_there=not left and self._still_there(change, snapshot),
            )
            if self.learning.done_by_hand:
                await self._done_by_hand(now)
            if (
                area is not None
                and self.area_roles.get(area.id) == "outside"
                and snapshot.mode is not HouseMode.SLEEP
            ):
                # The end of the evening light outside, as this house does it (E-446).
                self.learning.evening_off_seen(
                    change, area.id, snapshot.context_key(), snapshot.local, now
                )
            if area is not None and self.area_roles.get(area.id) in SLEEPING_ROLES:
                # Curtains opened in a bedroom: how long after getting up (E-484).
                view = snapshot.sleep.get(area.id)
                signal = view.signal if view is not None else "none"
                since = snapshot.woke if signal == "none" else view.empty_since if view else None
                awake = view is None or not view.asleep
                self.learning.wake_open_seen(
                    change,
                    area.id,
                    snapshot.context_key(),
                    snapshot.local,
                    now,
                    snapshot.sun,
                    (now - since).total_seconds() / 60 if since is not None and awake else None,
                    signal,
                )
            if (
                self.skills is not None
                and change.source in (Source.PERSON, Source.PHYSICAL)
                and change.new is not None
                and change.new.state == "off"
                and not change.online_only
            ):
                # Out sooner than HouseMind's minutes after coming home (E-447).
                self.skills.arrival_put_out(change.entity_id, now)
            await self._count_corrections(now)
        self._people_win(change, snapshot)
        if change.entity_id.startswith("person."):
            await self._refresh_people()  # who is home decides who gets a notification
            if (
                change.new is not None
                and change.new.state == "home"
                and change.old is not None
                and change.old.state not in ("home", "unavailable", "unknown")
            ):
                user = change.new.attributes.get("user_id")
                self._arrivals[change.entity_id] = (now, str(user) if user else None)
                if self.skills is not None:
                    self.skills.came_home(now)  # the lamps at the entrance, in the dark (E-447)
            if change.new is not None and change.new.state != "home" and change.old is not None:
                if change.old.state == "home":
                    self.days.resident_left(snapshot.local)
        event = self.emergencies.react(change, self.world.states)
        if event is not None and event.outside:
            # Only a message – but one that also reaches people at night – and "Let op" on
            # the Orb while it lasts, so the message is not next to a calm Orb.
            self.status.outside_alarms.add(event.sensor_entity_id)
            area = self.model.areas.get(event.area_id) if event.area_id else None
            await self._raise(
                Kind.EMERGENCY,
                f"emergency:{event.sensor_entity_id}",
                f"notify.outside_{event.emergency.value}",
                {"area": area.name if area else ""},
                snapshot,
            )
        elif event is not None and event.emergency is Emergency.FROST:
            # Frost is no alarm, which would freeze every skill, the heating too: a message,
            # and the heating in that room to at least 8 °C.
            executed = [await self.gate.submit(r) for r in event.requests]
            for decision in executed:
                await self._record(decision)
            area = self.model.areas.get(event.area_id) if event.area_id else None
            done = self._done_text(event.emergency, executed)
            await self._raise(
                Kind.IMPORTANT,
                f"emergency:{event.sensor_entity_id}",
                "notify.emergency_frost",
                {"area": area.name if area else "", "done": done},
                snapshot,
            )
        elif event is not None:
            self._active[event.sensor_entity_id] = event.emergency
            self._lost_since.pop(event.sensor_entity_id, None)
            self._set_emergency()
            fire = event.emergency in (Emergency.SMOKE, Emergency.CARBON_MONOXIDE)
            reason = f"emergency:{event.emergency.value}:{event.sensor_entity_id}"
            # At a fire also the chosen coverings up and the air off (E-546).
            extra = (
                alarm_reaction.fire_requests(
                    self._hm, event.emergency is Emergency.SMOKE, reason, now
                )
                if fire
                else []
            )
            executed = [await self.gate.submit(r) for r in (*event.requests, *extra)]
            for decision in executed:
                await self._record(decision)
            area = self.model.areas.get(event.area_id) if event.area_id else None
            # Say what really happened, never what should have: no "the main water valve is
            # closed" when there is no valve, or it refused.
            done = self._done_text(event.emergency, executed)
            topic = f"emergency:{event.sensor_entity_id}"
            alarming = event.emergency in ALARMS
            await self._raise(
                Kind.EMERGENCY,
                topic,
                f"notify.emergency_{event.emergency.value}",
                {"area": area.name if area else "", "done": done},
                snapshot,
                # The emergency number on the phone (E-545).
                push=alarm_reaction.links(self._hm, event.emergency.value) if alarming else None,
            )
            # A spoken warning on every speaker, also at night (E-19, E-121).
            text = self.languages.text(
                self._language(),
                f"notify.emergency_{event.emergency.value}",
                area=area.name if area else "",
                done=done,
            )
            spoken = await self._vesta_room(
                snapshot, "emergency", {"text": text}, f"emergency:{event.sensor_entity_id}", 100
            )  # her words around the fixed safety sentence (B-79)
            for area_id in list(self.speech.devices):
                await self.say(snapshot, area_id, spoken, "emergency")
            await self._vesta_phones(
                "emergency", {"text": text}, f"emergency:{event.sensor_entity_id}", 100, set()
            )
            if alarming:
                # Media quiet and the warning again (not at gas), the wall tablet (E-542, E-544).
                await alarm_reaction.begin(
                    self._hm,
                    event.emergency.value,
                    topic,
                    reason,
                    snapshot,
                    area.name if area else "",
                )
        elif change.entity_id in self._active and change.new is not None:
            if change.new.state == "off":
                del self._active[change.entity_id]
                self._lost_since.pop(change.entity_id, None)
                await self._resolve(f"emergency:{change.entity_id}")
                await alarm_reaction.over(self._hm, f"emergency:{change.entity_id}")
                self._set_emergency()
            elif change.new.state in ("unavailable", "unknown"):
                self._lost_since.setdefault(change.entity_id, self.clock.now())
        elif (
            change.new is not None
            and change.new.state == "off"
            and f"emergency:{change.entity_id}" in self._raised
        ):
            await self._resolve(f"emergency:{change.entity_id}")  # the alarm outside stopped
            self.status.outside_alarms.discard(change.entity_id)
        if change.source is not Source.HOUSEMIND:
            await self._think(snapshot, self._rooms_of(change.entity_id, mode_before, snapshot))
            await self._going_to_bed(change, snapshot)
        # Her greeting (with AI up to a few seconds) goes beside the queue: the next sensor
        # never waits for it (QA 2, M-31).
        self._beside(self._greet(self.world.snapshot(now)), "greet")
        if (
            self.world is not None
            and change.entity_id in self.world.occupancy.zones.general
            and change.new is not None
            and change.new.state == "on"
        ):
            self._think_soon(ZONE_SETTLE_S)  # in range: did one of its zones see someone?
        self._refresh_orb()
        await self._notify(knows=False)  # a decision on the way said so itself

    def _think_soon(self, seconds: float) -> None:
        """Think again in a moment (a sensor's zones report a little later)."""

        async def again() -> None:
            if self.world is not None:
                await self._think(self.world.snapshot(self.clock.now()))

        with contextlib.suppress(RuntimeError):  # no running loop: the next tick thinks
            loop = asyncio.get_running_loop()
            loop.call_later(seconds + 0.2, lambda: loop.create_task(again()))

    async def _on_measurement(self, change: StateChange, now: datetime) -> None:
        """Sensors can change every few seconds (power, energy): only the world view is
        updated – no event row, no learning, no screen refresh per value. Skills only think
        along for the measurements they use, at most once a minute per sensor."""
        assert self.world is not None
        self.world.on_change(change, now)
        self._measurements_changed = True
        if change.entity_id in self.temperatures.devices:
            await self._device_heat(change.entity_id)  # a socket's own warmth: fire risk only
            return
        entity = self.model.entities.get(change.entity_id)
        if entity is None or entity.device_class not in SKILL_SENSOR_CLASSES:
            return
        last = self._sensor_thought.get(change.entity_id)
        light = entity.device_class == "illuminance"
        if last is not None and now - last < (SENSOR_THINK_DARK if light else SENSOR_THINK_EVERY):
            return
        snapshot = self.world.snapshot(now)
        if (
            last is not None
            and now - last < SENSOR_THINK_EVERY
            and not self._dark_matters(change.entity_id, snapshot)
        ):
            return
        self._sensor_thought[change.entity_id] = now
        await self._think(snapshot, self._rooms_of(change.entity_id, snapshot.mode, snapshot))

    def _dark_matters(self, entity_id: str, snapshot: WorldState) -> bool:
        """Someone is in this sensor's room and no lamp there is on: getting dark now means
        light now (E-443)."""
        assert self.world is not None
        area = self.model.area_of(entity_id)
        if area is None or not snapshot.occupied(area.id):
            return False
        return not any(
            (state := self.world.states.get(e.entity_id)) is not None and state.state == "on"
            for e in self.model.in_area(area.id)
            if e.domain == "light"
        )

    def _plan_recheck(self, now: datetime) -> None:
        """Look again the moment a place's wait runs out – someone staying in a walk-through
        room – not only at the next tick, half a minute later (E-443)."""
        assert self.skills is not None
        due = self.skills.next_recheck(now)
        if due is None:
            return
        task = self._recheck_task
        if task is not None and not task.done():
            if self._recheck_at is not None and self._recheck_at <= due:
                return
            task.cancel()
        self._recheck_at = due
        self._recheck_task = asyncio.create_task(self._recheck(due), name="housemind-recheck")

    async def _recheck(self, due: datetime) -> None:
        # From when the task runs, not when it was made: a task starts a moment later.
        await self.clock.sleep((due - self.clock.now()).total_seconds())
        self._recheck_task = self._recheck_at = None
        if self.world is not None:
            await self._think(self.world.snapshot(self.clock.now()))

    def _rooms_of(
        self, entity_id: str, mode_before: HouseMode, snapshot: WorldState
    ) -> set[str] | None:
        """The rooms a change can matter for right now (S2): its own room, and the rooms it
        forms one open space with. None – the whole house – for a change without a room (a
        person, the sun, the weather) or when the house mode changed. What reaches across
        rooms otherwise, the tick sees within 30 seconds."""
        area = self.model.area_of(entity_id)
        if area is None or snapshot.mode is not mode_before or self.skills is None:
            return None
        return {area.id, *self.skills.open_space.get(area.id, ())}

    async def _close_bedtime(self, snapshot: WorldState) -> None:
        """The bedtime message goes away once everything is closed, and at the latest the
        next morning, so it never keeps the Orb orange."""
        open_topics = [t for t in self._raised if t.startswith("bedtime:")]
        if not open_topics:
            return
        assert self.world is not None
        left = [
            item
            for item in findings(
                self.model,
                self.world.states,
                {},
                set(),
                self.openings,
                not_lamps=self.world.status_led_ids,
            )
            if item[0] != "light"
        ]
        for topic in open_topics:
            age = self.clock.now() - self._raised[topic][2]
            if not left or age > timedelta(hours=10):
                await self._resolve(topic)

    def _counts(self, entity_id: str) -> bool:
        """Whether a change of this entity teaches HouseMind something of its own (E-458): not
        what is left alone, and not a lamp a group steers – the group's own change counts, once
        for all its lamps – nor a group that only gathers."""
        assert self.gate is not None
        lamp = entity_id.startswith("light.") or self.gate.state.roles.get(entity_id) == "light"
        if lamp:
            return self.model.unit(entity_id) == entity_id
        return entity_id not in self.model.left_alone

    def _left_room(self, change: StateChange, snapshot: WorldState) -> bool:
        """A lamp of a room where HouseMind can tell nobody is, and where "light off when
        leaving" may work: one that goes off there counts for that skill."""
        assert self.gate is not None and self.world is not None
        entity_id = change.entity_id
        lamp = entity_id.startswith("light.") or self.gate.state.roles.get(entity_id) == "light"
        area = self.model.area_of(entity_id)
        if (
            not lamp
            or area is None
            # What HouseMind addresses counts, once: a group that steers, not its lamps as
            # well; a lamp on its own; nothing left alone (E-458).
            or self.model.unit(entity_id) != entity_id
            or not self.world.occupancy.sensed(area.id)
        ):
            return False
        never = self.skill_params.get("light_off_leave", {}).get("never_in", ())
        occupancy = snapshot.occupancy.get(area.id)
        return (
            "light_off_leave" in self.skill_params
            and self.area_roles.get(area.id) not in never
            and occupancy is not None
            and not occupancy.occupied
        )

    def _still_there(self, change: StateChange, snapshot: WorldState) -> bool:
        """A lamp of a room where the sensor still sees someone: if they then leave, it was
        "light off when leaving" (E-460)."""
        assert self.gate is not None and self.world is not None
        entity_id = change.entity_id
        lamp = entity_id.startswith("light.") or self.gate.state.roles.get(entity_id) == "light"
        area = self.model.area_of(entity_id)
        if not lamp or area is None or self.model.unit(entity_id) != entity_id:
            return False
        never = self.skill_params.get("light_off_leave", {}).get("never_in", ())
        occupancy = snapshot.occupancy.get(area.id)
        return (
            self.world.occupancy.sensed(area.id)
            and self.area_roles.get(area.id) not in never
            and occupancy is not None
            and occupancy.occupied
        )

    async def _done_by_hand(self, now: datetime) -> None:
        """Someone did by hand what a question asks while it is open: the clearest yes, and the
        question is answered (E-460). Without an open question it was only a hit."""
        assert self.learning is not None and self.proposals is not None
        done, self.learning.done_by_hand = self.learning.done_by_hand, []
        closed = False
        for skill, area_id, context in done:
            asked = [
                p
                for p in await self.proposals.open(now)
                if p.skill == skill and p.area_id == area_id
            ]
            if asked:
                self.learning.answered_by_hand(skill, area_id, context, now)
            for proposal in asked:
                await self.proposals.answer(proposal.id, Answer.BY_HAND, None, now)
                await self._resolve(f"proposal:{proposal.id}")
                closed = True
        if closed:
            await self._notify()

    async def _think(self, snapshot: WorldState, rooms: set[str] | None = None) -> None:
        """What the skills want now and what the gate lets through: in these rooms (S2,
        after a change), or in every room (the tick, every 30 seconds)."""
        assert self.skills is not None and self.world is not None
        self._situation_changed(snapshot)
        if self.status.emergency in ALARMS:
            return  # smoke, CO or gas: the emergency reactions own the house now (E-80)
        requests = list(self.skills.evaluate(snapshot, self.world.states, rooms))
        self._plan_recheck(snapshot.now)
        if self._heat_cool_dirty:
            await self._save_heat_cool()  # a heat pump's wish, seen for the first time (E-318)
        await self._zwave_together(requests)
        try:
            for request in requests:
                await self.consider(request)
        finally:
            assert self.gate is not None
            self.gate.done_together.clear()

    async def _zwave_together(self, requests: list[ActionRequest]) -> None:
        """Z-Wave devices switched on or off together go in one radio message (a multicast),
        not one by one (E-312; Z-Wave has no groups in HA). Only plain on/off of the same
        skill in the same room, only what the gate lets through right now; each device keeps
        its own decision and line in the logbook. Without the multicast: one by one."""
        assert self.gate is not None
        batches: dict[tuple[str, str, str, str, int], list[ActionRequest]] = {}
        for request in requests:
            entity = self.model.entities.get(request.entity_id)
            if (
                entity is None
                or entity.platform != "zwave_js"
                or request.action not in ZWAVE_ON_OFF
                or request.data
                or self._skips(request)
            ):
                continue
            state = self.world.states.get(entity.entity_id) if self.world else None
            modes = (
                entity.capabilities.get("supported_color_modes")
                or (state.attributes.get("supported_color_modes") if state else None)
                or []
            )
            dims = entity.domain == "light" and any(m != "onoff" for m in modes)
            key = (
                request.skill or "",
                request.area_id or "",
                request.action,
                request.layer.value,
                38 if dims else 37,  # multilevel switch, else binary switch
            )
            batches.setdefault(key, []).append(request)
        for (_skill, _area, action, _layer, command_class), batch in batches.items():
            ready = [r for r in batch if self.gate.evaluate(r).outcome is Outcome.EXECUTED]
            if len(ready) < 2:
                continue
            on = action.endswith("turn_on")
            result = await self.ha.multicast(on, command_class, [r.entity_id for r in ready])
            if result.ok:
                for request in ready:
                    self.gate.done_together[request.entity_id] = result

    def _skips(self, request: ActionRequest) -> bool:
        """What ``consider`` leaves without asking the gate: a lamp that looks lived-in
        tonight, or the same request again within a short while."""
        if request.entity_id in self._lived_in_lit and request.layer is not Layer.EMERGENCY:
            return True
        last = self._last_request.get(request.entity_id)
        return (
            last is not None
            and last[0] == request.action
            and last[1] == json.dumps(request.data, sort_keys=True)
            and self.clock.now() - last[2] < REPEAT_AFTER
        )

    def _situation_changed(self, snapshot: WorldState) -> None:
        """People win until the situation changes (E-33): someone who switched a light on
        and then left the room gives that light back – it may go off again, and coming in again
        is a new moment (E-462)."""
        assert self.gate is not None
        occupied = {a for a, o in snapshot.occupancy.items() if o.occupied}
        left = self._occupied_before - occupied
        self._occupied_before = occupied
        if not left:
            return
        assert self.world is not None
        for entity_id in self.gate.state.people_win_until:
            entity = self.model.entities.get(entity_id)
            area = self.model.area_of(entity_id)
            if entity is None or area is None or area.id not in left:
                continue
            if not self.world.occupancy.sensed(area.id):
                continue  # without a sensor, someone lying still looks like nobody
            if entity.domain in ("light", "media_player") or (
                self.gate.state.roles.get(entity_id) == "light"
            ):
                self.gate.state.released.add(entity_id)
                self.gate.state.come_in.add(entity_id)

    async def consider(self, request: ActionRequest) -> Decision | None:
        """Pass one skill request through the gate, learning and proposals."""
        assert self.gate is not None and self.learning is not None
        now = self.clock.now()
        if self.part_off(request) is not None:
            return None  # its part is off: asleep, no thought, nothing learned (E-400)
        if self._skips(request):
            return None  # lived-in owns this lamp tonight (E-285), or asked a moment ago
        key = (request.action, json.dumps(request.data, sort_keys=True), now)
        self._last_request[request.entity_id] = key
        previous = self.world.states.get(request.entity_id) if self.world else None
        decision = await self.gate.submit(request)
        trial = False
        planned = bool(request.facts.get("planned"))
        # "Dim first, as a warning" (E-113) is only a courtesy before HouseMind itself turns a
        # light off. It is never a question of its own, and not what people would do.
        # Putting the light back after that warning is the same courtesy: no question, and
        # nothing to learn from – it is no "not like that".
        warning = bool(request.facts.get("warning") or request.facts.get("restored"))
        if (
            decision.outcome is Outcome.PROPOSED
            and self._asks_nobody(request, planned)
            and (not planned or request.facts.get("source") == "alarm")
            and self._may_try(request, now)
        ):
            # Only Carefree concerned, or nobody who could see a question now: a careful,
            # reversible try instead (E-57, E-287). Waking someone up only happens as a try
            # when their phone's alarm is set.
            trial_decision = await self.gate.submit(replace(request, layer=Layer.TRIAL))
            if trial_decision.outcome is Outcome.EXECUTED:
                decision, trial = trial_decision, True
        # Only watching: no dim warning (it only exists before HouseMind's own "off"), and the
        # same thought not every minute again (QA 2, B-181).
        if not (warning and decision.outcome is Outcome.OBSERVED) and not self._watched_lately(
            decision, now
        ):
            self.status.thoughts.append(decision)
            del self.status.thoughts[:-100]
        first_of_block = self._first_of_block(request)
        # A testkwartier counts for nothing (E-479): no prediction to be right or corrected.
        tested = bool(decision.request.facts.get("test"))
        if self._learns_now() and not warning and first_of_block and not tested:
            self.learning.predicted(request, decision, now, trial=trial)
        if decision.outcome is not Outcome.EXECUTED:
            self.last_not_done[request.entity_id] = decision
        else:
            self.last_not_done.pop(request.entity_id, None)
            self._daylight_lit(request, now)  # on the day's rhythm from here (E-466)
        if decision.outcome is Outcome.PROPOSED and warning:
            return decision  # the question comes with the light off itself, a minute later
        asked = False
        sleeping = self.world is not None and self.world.modes.mode is HouseMode.SLEEP
        if request.skill in SAFETY_SKILLS:
            sleeping = False  # a question about safety also comes at night (E-289)
        if decision.outcome is Outcome.PROPOSED and not self._ask_in_block(request):
            return decision  # E-343: one question per charge or step of the energy plan
        if decision.outcome is Outcome.PROPOSED and not planned:
            # A planned action (waking up) is asked the evening before, never at the moment.
            # A question that comes back never comes back while the house sleeps (E-294);
            # a new one waits for the end of the quiet hours (the router, E-290).
            proposal_id = await self.proposals.add(decision, sleeping=sleeping)
            proposal = await self.proposals.get(proposal_id) if proposal_id else None
            asked = proposal is not None
            # One question per skill and room: the other lamps join the open question.
            if proposal is not None and not await self.proposals.same_question(proposal):
                await self._ask(proposal.id, decision)
        if decision.outcome is Outcome.PROPOSED and not asked and not planned:
            # Only a real new question goes in the log, not one that is already open.
            return decision
        if decision.outcome is not Outcome.OBSERVED:
            await self._record(decision, previous)
        return decision

    async def _count_corrections(self, now: datetime) -> None:
        """HouseMind counts itself how often someone undoes what it did (S-01)."""
        assert self.learning is not None
        if self.learning.corrections:
            self.status.learning_until = now + LEARN_SHOW  # it learns from the correction
        for p in self.learning.corrections:
            await self._store_correction(now, p.skill, p.area_id, p.entity_id, p.trial)
        self.learning.corrections.clear()

    async def _store_correction(
        self, now: datetime, skill: str, area_id: str, entity_id: str, trial: bool = False
    ) -> None:
        """One correction for S-01: switched back by hand, Undo or 👎 (QA 2, M-16)."""
        await self.db.execute(
            "INSERT INTO corrections(at, skill, area_id, entity_id, trial) VALUES(?,?,?,?,?)",
            (now.isoformat(), skill, area_id, entity_id, int(trial)),
        )

    async def corrections_per_domain(self, days: int = 7) -> dict[str, int]:
        """Corrections in the last ``days`` per domain (lighting, covers, climate), S-01."""
        return {d: weeks[0] for d, weeks in (await self.corrections_weeks(1, days)).items()}

    async def corrections_weeks(self, weeks: int = 4, days: int = 7) -> dict[str, list[int]]:
        """Corrections per domain in each of the last ``weeks`` periods of ``days``, oldest
        first: the count and its trend that S-01 shows per part on levels 3 and 4 (M-16)."""
        now = self.clock.now()
        since = (now - timedelta(days=days * weeks)).isoformat()
        out: dict[str, list[int]] = {}
        for row in await self.db.fetchall(
            "SELECT skill, at FROM corrections WHERE at >= ?", (since,)
        ):
            domain = str(self.skill_params.get(str(row["skill"]), {}).get("domain", "other"))
            ago = (now - datetime.fromisoformat(str(row["at"]))).days // days
            out.setdefault(domain, [0] * weeks)[weeks - 1 - min(ago, weeks - 1)] += 1
        return out

    def _people_win(self, change: StateChange, snapshot: WorldState | None = None) -> None:
        """A person or wall switch operated a device: HouseMind keeps off it (E-33, B-15). A
        signal uses a lamp: as long as that lasts; anything else changing it ends it (E-461).
        A device that only dropped off the network or came back is nobody's hand (E-514): in a
        real house curtain motors went away for ten seconds a few times a day, and each return
        kept HouseMind off them for six hours, also on Autonomous."""
        assert self.gate is not None
        if change.online_only:
            return
        if change.source is Source.SIGNAL:
            rest = self.automations.rest_after(change.cause)
            self.gate.state.signal_until[change.entity_id] = self.clock.now() + rest
            if rest <= SIGNAL_THINK_AGAIN:
                self._think_soon(rest.total_seconds())  # someone who came in meanwhile
            return
        self.gate.state.signal_until.pop(change.entity_id, None)
        if change.source not in (Source.PERSON, Source.PHYSICAL):
            return
        entity = self.model.entities.get(change.entity_id)
        if entity is None:
            return
        kind = recognise(self.catalog, entity, self.gate.state.roles.get(change.entity_id)).kind
        if kind is None or not kind.actions:
            return
        hours = kind.people_win_max_hours
        self.gate.state.people_win_until[change.entity_id] = self.clock.now() + timedelta(
            hours=hours
        )
        self.gate.state.released.discard(change.entity_id)
        self.gate.state.come_in.discard(change.entity_id)
        # From afar, or a button that puts all lamps out: a presence sensor sees nobody here, so
        # nobody stays with this choice and someone who comes in later is a new moment (E-462).
        area = self.model.area_of(change.entity_id)
        occupancy = snapshot.occupancy.get(area.id) if snapshot and area else None
        if (
            occupancy is not None
            and occupancy.presence
            and not occupancy.occupied
            and (entity.domain == "light" or self.gate.state.roles.get(entity.entity_id) == "light")
        ):
            self.gate.state.come_in.add(change.entity_id)

    def _expect_followups(self, entity_id: str) -> None:
        """What HouseMind just asked of it reports back a little later: still HouseMind's."""
        tracker = getattr(self.ha, "tracker", None)
        if tracker is None:
            return
        if entity_id in self.model.groups:
            tracker.expect_followups(list(self.model.groups[entity_id]), 30)
        elif entity_id.startswith("cover."):
            tracker.expect_followups([entity_id], 120)  # the motor arrives later

    async def _record(self, decision: Decision, previous: State | None = None) -> int:
        if (
            decision.executed
            and self.energy is not None
            and (decision.request.skill or "").startswith("energy_")
        ):
            # Whatever way an energy step was carried out – by itself, after a "Do it" on a
            # proposal or as a try – it goes into the register of what HouseMind set (B-203).
            self.energy.done(decision.request, previous, decision.at)
            skill = str(decision.request.skill)
            if decision.request.layer in (Layer.PERSON_ANSWER, Layer.TRIAL) and self.gate:
                # E-343: the yes holds for this step – no question per adjustment.
                until = self.energy.block_end(skill, decision.at)
                if until is not None:
                    self.gate.state.leases[skill] = until
        trigger = _TRIGGER.get()
        ms = None
        if trigger is not None:
            started, lag = trigger
            ms = round(lag + (timer.perf_counter() - started) * 1000, 1)
        if decision.executed and ms is not None:
            self.status.reactions.append(ms)
            del self.status.reactions[:-50]
            self._reactions_unsaved = True  # kept at the next round, not in the hot path
        entity_id = decision.request.entity_id
        if decision.executed:
            self._expect_followups(entity_id)
        self.status.decisions.append(decision)
        del self.status.decisions[:-100]
        self.knows += 1  # a decision counts in "Klaar voor autonomie?"
        decision_id = await self.db.execute_insert(
            "INSERT INTO decisions(at, source_layer, entity_id, action, data, outcome, reason,"
            " trace) VALUES(?,?,?,?,?,?,?,?)",
            (
                decision.at.isoformat(),
                decision.request.layer.value,
                decision.request.entity_id,
                decision.request.action,
                json.dumps(decision.request.data),
                decision.outcome.value,
                decision.reason,
                json.dumps(
                    {
                        "checks": [[c.name, c.ok, c.detail] for c in decision.checks],
                        "skill": decision.request.skill,
                        "area": decision.request.area_id,
                        # Why it was asked – an emergency, the alarm (E-533): kept, so the
                        # Logboek says it after a restart too.
                        "why": decision.request.reason,
                        "facts": decision.request.facts,
                        "step": decision.step.name.lower() if decision.step is not None else None,
                    }
                ),
            ),
        )
        self.recent[decision_id] = Recent(decision_id, decision, previous, ms=ms)
        self.lamp_day.decided(decision, self.model.groups)  # its lamp may have changed already
        if decision.executed and decision.request.facts.get("test"):
            await self._test_recorded(decision, previous)  # what a testkwartier puts back
        for old in list(self.recent)[:-50]:
            del self.recent[old]
        if decision.executed and entity_id.startswith("climate."):
            await self._heat_cool_done(decision)
        if (
            decision.executed
            and self.skills is not None
            and decision.request.skill == "light_off_leave"
            and decision.request.action.endswith(".turn_off")
        ):
            # Coming back right after this is coming into the dark (QA 2, H-10).
            for lamp in [entity_id, *self.model.groups.get(entity_id, ())]:
                self.skills.turned_off(lamp, decision.at)
        if (
            decision.executed
            and self.skills is not None
            and decision.request.skill == "light_outside_arrival"
            and decision.request.action.endswith(".turn_on")
            and decision.request.area_id is not None
        ):
            # Lit for someone coming home: HouseMind's to put out again (E-447).
            self.skills.lit_for_arrival(entity_id, decision.request.area_id, decision.at)
            self._plan_recheck(decision.at)
            if decision.request.layer is Layer.PERSON_ANSWER and self.gate is not None:
                # The yes was for the light of this coming home: putting it out again a few
                # minutes later asks nothing more.
                skill = decision.request.skill
                most = float(self.skills.params.get(skill, {}).get("max_minutes", 30))
                self.gate.state.leases[skill] = decision.at + timedelta(minutes=most)
        if decision.executed and decision.request.layer not in (
            Layer.EMERGENCY,
            Layer.PERSON_ANSWER,
        ):
            self._show_thinking()
            for listener in list(self._action_listeners):
                try:
                    await listener(decision_id, decision)
                except Exception:
                    _LOGGER.exception("Action listener failed")
        return decision_id

    def _watched_lately(self, decision: Decision, now: datetime) -> bool:
        """The same thing only watched, again within WATCH_AGAIN (B-181)."""
        if decision.outcome is not Outcome.OBSERVED:
            return False
        r = decision.request
        key = (r.entity_id, r.skill or "", r.action)
        last = self._watched.get(key)
        if last is not None and now - last < WATCH_AGAIN:
            return True
        self._watched[key] = now
        return False

    def _someone_there(self, area_id: str) -> bool:
        """Someone in this room – or home, when the room has nothing that can tell – who could
        put a trial back (QA 2, B-177)."""
        if self.world is None:
            return False
        snapshot = self.world.snapshot(self.clock.now())
        info = snapshot.occupancy.get(area_id)
        if info is not None and (info.presence or info.last_activity is not None):
            return info.occupied
        return snapshot.mode not in (HouseMode.AWAY, HouseMode.VACATION)

    def _may_try(self, request: ActionRequest, now: datetime) -> bool:
        """A trial – doing it without asking – needs proof (QA 2, B-176); waking someone with
        a phone alarm set and planned actions keep their own rules."""
        if self.learning is None or not request.skill or not request.area_id:
            return False
        if request.facts.get("planned") or request.skill in WAKE_SKILLS:
            return True
        return self.learning.may_try(request.skill, request.area_id, now)

    def _start_conditions(self, request: ActionRequest, entity: Entity) -> str | None:
        """What a mower or robot vacuum needs at the moment it starts – from every source: a
        rule, a person's yes, a proposal of the AI (QA 2, B-172)."""
        if request.action not in ("lawn_mower.start_mowing", "vacuum.start") or self.world is None:
            return None
        skill = "mower_when_dry" if request.action.startswith("lawn_mower") else "vacuum_when_away"
        hours = self.skill_params.get(skill, {}).get(
            "hours", [10, 18] if "mower" in skill else [9, 18]
        )
        start, end = (int(x) for x in hours)
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        if not start <= self.clock.now().astimezone(tz).hour < end:
            return "outside_hours"
        if skill == "vacuum_when_away":
            return None
        area = self.model.area_of(entity.entity_id)
        if area is None:
            return "no_area"  # where it mows is unknown, so whether someone is there too
        snapshot = self.world.snapshot(self.clock.now())
        if snapshot.occupied(area.id):
            return "someone_outside"
        weather = next(
            (s for e, s in sorted(self.world.states.items()) if e.startswith("weather.")), None
        )
        if weather is None or weather.state in RAINY:
            return "raining"
        return None

    def _skill_step(self, skill: str, area_id: str | None, entity_id: str | None = None) -> Step:
        params = self.skill_params.get(skill, {})
        if params.get("experimental") and params.get("domain") not in self.experimental:
            # Experimental: only watching until an administrator says yes – also with a house
            # rule 'always' (QA 2, M-1).
            return Step.OBSERVE
        override = self.rules.step_override(skill, area_id)
        if override is not None:
            return override
        assert self.learning is not None
        step = self.learning.step_at(skill, area_id, self.clock.now())
        lift = self._admin_lift(skill, area_id, entity_id)
        if lift is not None and lift > step:
            step = lift  # an administrator set it higher (20.10); the gate still limits
        if step is Step.PROPOSE and skill in WAKE_SKILLS:
            # Someone said "yes" the evening before: this morning HouseMind may (E-88). The
            # gate still checks everything else – a light switched off stays off.
            tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
            if self.rhythm.is_granted(skill, self.clock.now().astimezone(tz).date()):
                return Step.AUTONOMOUS
        return step
