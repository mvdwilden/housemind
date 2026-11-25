"""The safety gate (H4.1): the only way any action reaches Home Assistant (E-110).

Every action request – from emergency reactions, reflexes, the planner, learning, AI, house
rules or a person's answer – passes the same six steps:

1. real and valid      known source, exactly one known entity, fresh context
2. allowed             catalogue allow-list, values, hard limits (in code), safe direction
3. may it now          pause, people win, house rules; another system: only watch
4. step                strictest of catalogue, administrator and the skill's own step
5. sensible            switching limits, battery devices
6. done                Home Assistant confirms

The outcome is always one of: executed, proposal, observed only, or denied *with a reason*
(that reason feeds "why not?", E-93).

A device that another system or an existing Home Assistant automation controls is only
watched (E-42, E-68): HouseMind keeps off it, but still learns from what happens to it (E-41).
An existing automation is only ever switched off after a person's yes, and back on when a
person or the give-back asks for it (E-42, E-112).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from housemind.core.clock import Clock
from housemind.core.model import Entity, HouseModel
from housemind.ha.adapter import ActionResult, HomeAssistant
from housemind.knowledge.catalog import Catalog, DeviceKind, SafetyClass, Step
from housemind.knowledge.matcher import recognise
from housemind.safety.hard_limits import AUTOMATION_SWITCH, hard_limit_reason

# Welterusten (E-517): what goes off and shut when the house goes to sleep.
GOODNIGHT = frozenset({"light_off_sleep", "covers_close_sleep"})


class Layer(StrEnum):
    """Where an action request comes from (H3.5)."""

    EMERGENCY = "emergency"
    REFLEX = "reflex"
    PLANNER = "planner"
    LEARNING = "learning"
    AI = "ai"
    HOUSE_RULE = "house_rule"
    PERSON_ANSWER = "person_answer"  # someone pressed "Do it" on a proposal
    RESTORE = "restore"  # an automation HouseMind switched off goes back on (E-42, E-112)
    TRIAL = "trial"  # level Carefree: a careful try instead of asking (E-57)
    TEST = "test"  # Test het huis: an administrator's Probeer nu (E-476, B-390)
    API = "api"


class Outcome(StrEnum):
    EXECUTED = "executed"
    PROPOSED = "proposed"
    OBSERVED = "observed"
    DENIED = "denied"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ActionRequest:
    layer: Layer
    entity_id: str
    action: str
    data: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    confidence: float | None = None
    skill: str | None = None
    area_id: str | None = None
    created_at: datetime | None = None
    user_id: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)  # for the explanation (H3.6)


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Decision:
    request: ActionRequest
    outcome: Outcome
    reason: str | None
    step: Step | None
    checks: tuple[Check, ...]
    at: datetime
    context_id: str | None = None

    @property
    def executed(self) -> bool:
        return self.outcome is Outcome.EXECUTED


def watched(other_system: set[str], entity_id: str, members: Iterable[str] = ()) -> bool:
    """HouseMind only watches this: another system or an automation steers it (E-42, E-68). A
    group only when it steers the group itself or at least half of its lamps; one lamp in a
    bigger group is that lamp's business, as with a lamp left alone (E-458, E-460)."""
    if entity_id in other_system:
        return True
    lamps = tuple(members)
    return bool(lamps) and 2 * sum(1 for lamp in lamps if lamp in other_system) >= len(lamps)


@dataclass(slots=True)
class GateState:
    """Everything outside the request that the gate needs to know."""

    paused_until: datetime | None = None
    paused_domains: dict[str, datetime] = field(default_factory=dict)
    people_win_until: dict[str, datetime] = field(default_factory=dict)
    # Lamps a signal uses (E-461): the alarm's colour, the doorbell's blink. HouseMind keeps off
    # them while it lasts; nobody's doing, so it teaches nothing and holds no room.
    signal_until: dict[str, datetime] = field(default_factory=dict)
    # Lamps whose standen an administrator is setting now (E-516): while that window is open,
    # HouseMind's own rules and proposals keep off them; the window renews it while it is open.
    setting_until: dict[str, datetime] = field(default_factory=dict)
    # Devices an administrator plays a situation out on in Test het huis (E-518, B-445): while
    # the minute runs, HouseMind's everyday rules keep off them, so the real moment does not
    # undo the test at once.
    testing_until: dict[str, datetime] = field(default_factory=dict)
    # Lights a person switched on and then left the room with (E-33): only turning them off
    # because nobody is there is HouseMind's again.
    released: set[str] = field(default_factory=set)
    # Lamps whose hand choice no longer holds for someone coming in (E-462): the one who chose
    # left, or nobody was there. Coming in is a new moment; putting them out is ``released``.
    come_in: set[str] = field(default_factory=set)
    other_system: set[str] = field(default_factory=set)
    # The switches of the day's rhythm HouseMind switched off itself (E-468): those it may put
    # back on, as with an automation it took over.
    day_rhythm_taken: set[str] = field(default_factory=set)
    untested_ha: bool = False
    # Actions Home Assistant no longer offers (the self-check after a start, E-152).
    broken_actions: set[str] = field(default_factory=set)
    protected_entities: set[str] = field(default_factory=set)
    roles: dict[str, str] = field(default_factory=dict)  # entity_id -> role (house knowledge)
    # What opens a garage door, gate or door (E-31), found from the house itself – a cover's
    # class or room, a switch's or button's words, a group with one in it. Kept apart from
    # ``roles``, so no label, AI finding or learned role can ever overwrite it (QA 3, gat 1).
    openers: set[str] = field(default_factory=set)
    # Roles only AI gave, not yet confirmed (E-201, design 50.8): HouseMind watches and learns
    # these devices as that kind, but proposes and does nothing with them until a Klopt.
    from_ai: set[str] = field(default_factory=set)
    # Roles HouseMind derived itself from behaviour (E-203), not yet confirmed: the same.
    derived: set[str] = field(default_factory=set)
    excluded: set[str] = field(default_factory=set)  # excluded by a user (level 3/4)
    # A setting or a status of a device, by its function (E-431, design 51.6): HouseMind never
    # switches it itself, nor on an AI's request; a person's "Do it" may.
    leave_alone: set[str] = field(default_factory=set)
    # The switches and buttons of the hand of the resident (E-448): the residents' own way to
    # work a lamp. HouseMind never switches one itself; a person's "Do it" may.
    hand_switches: set[str] = field(default_factory=set)
    admin_max_step: dict[str, Step] = field(default_factory=dict)  # "domain", "area:x", entity
    skill_step: Callable[[str, str | None], Step] = field(default=lambda skill, area: Step.OBSERVE)
    # The same for one device, when an administrator may set a step for it (20.10).
    device_step: Callable[[str, str | None, str], Step] | None = None
    house_rule_veto: Callable[[ActionRequest], str | None] = field(default=lambda request: None)
    # The part of a request when that part is off (E-400): then nothing of it, but emergencies.
    part_off: Callable[[ActionRequest], str | None] = field(default=lambda request: None)
    # Test het huis, the testkwartier (E-477, B-391): per (part, room) until when it stands on
    # Autonomous, and the test's id; ``part_of`` says the part of a request.
    tests: dict[tuple[str, str], tuple[datetime, int]] = field(default_factory=dict)
    part_of: Callable[[ActionRequest], str | None] = field(default=lambda request: None)
    # What a device needs at the moment it starts, whoever asks (QA 2, B-172): a mower only
    # with nobody outside, no rain and in its hours; a robot vacuum in its hours.
    conditions: Callable[[ActionRequest, Entity], str | None] = field(
        default=lambda request, entity: None
    )
    battery_powered: set[str] = field(default_factory=set)
    # Smoke, CO or gas is on: nothing but the emergency reactions switches (E-80).
    alarm: bool = False
    # "Nog niet actief" (E-275): after the factory settings and a new installation, until an
    # administrator finished the introduction – only emergencies and giving back pass.
    inactive: bool = False
    # Energy devices HouseMind set itself (B-203): putting them back is giving back (E-329).
    owned: set[str] = field(default_factory=set)
    # Entities HouseMind switched off in Home Assistant after a yes (Home Assistant netjes,
    # B-243): only these may be switched back on by it.
    tidy_off: set[str] = field(default_factory=set)
    # A "Do it" on a part of energy that proposes (E-343, B-217): until then the plan may
    # follow up within that step without asking again.
    leases: dict[str, datetime] = field(default_factory=dict)
    # Writing sparingly (E-340, K3): integrations that go through the cloud (one write a
    # minute for all of them together), integrations whose device wears its memory with every
    # write, and grid-code words that are never touched.
    cloud: set[str] = field(default_factory=set)
    flash: set[str] = field(default_factory=set)
    netcode: tuple[str, ...] = ()

    def signalling(self, entity_id: str, now: datetime) -> bool:
        """A signal is using this lamp now (E-461)."""
        until = self.signal_until.get(entity_id)
        return until is not None and now < until

    def setting(self, entity_id: str, now: datetime) -> bool:
        """Someone is setting this lamp's standen now (E-516)."""
        until = self.setting_until.get(entity_id)
        return until is not None and now < until

    def testing(self, entity_id: str, now: datetime) -> bool:
        """A test of Test het huis has this device now (E-518)."""
        until = self.testing_until.get(entity_id)
        return until is not None and now < until


_STALE_AFTER = timedelta(seconds=5)
# Home Assistant netjes (B-243): never switched off in the registry, whatever is wrong with it.
_TIDY_NEVER_DOMAINS = frozenset({"alarm_control_panel", "lock", "siren", "valve"})
_TIDY_NEVER_CLASSES = frozenset({"smoke", "carbon_monoxide", "gas", "moisture", "safety"})
# Never stopped by a pause, people win, house rules or switching limits: emergencies, and
# giving back what HouseMind took over ("switching it back on is always possible", E-42).
_PEOPLE_EXEMPT = frozenset({Layer.EMERGENCY, Layer.RESTORE})
_A_PERSON = frozenset({Layer.PERSON_ANSWER, Layer.RESTORE})  # a person's own "Do it"
# Asked for now by a person: no "people win" and no minimum interval (a test, B-390).
_ASKED = frozenset({Layer.PERSON_ANSWER, Layer.TEST})
# What HouseMind does by itself, from its skills: what a testkwartier lifts (B-391).
_EVERYDAY = frozenset({Layer.REFLEX, Layer.LEARNING, Layer.PLANNER, Layer.TRIAL})
# HouseMind's own doing: what waits while an administrator sets a lamp's standen (E-516).
_OWN_DOING = _EVERYDAY | {Layer.AI, Layer.HOUSE_RULE}
_RESTORE_ACTION = "automation.turn_on"
CLOUD_GAP = timedelta(minutes=1)  # E-340: via the cloud at most once a minute, all together
FLASH_GAP_S = 300.0  # a device that wears its memory: at least five minutes between writes


class SafetyGate:
    def __init__(
        self,
        clock: Clock,
        ha: HomeAssistant,
        catalog: Catalog,
        model: HouseModel,
        state: GateState | None = None,
    ) -> None:
        self._clock = clock
        self._ha = ha
        self.catalog = catalog
        self.model = model
        self.state = state or GateState()
        # Results of a Z-Wave multicast the runtime already sent for these devices (E-312).
        self.done_together: dict[str, ActionResult] = {}
        self._last_change: dict[str, datetime] = {}
        self._cloud_last: datetime | None = None
        self._changes_today: dict[tuple[str, str], int] = {}

    # -- public -------------------------------------------------------------------------

    async def submit(self, request: ActionRequest) -> Decision:
        decision = self.evaluate(request)
        if decision.outcome is not Outcome.EXECUTED:
            return decision
        request = decision.request  # a testkwartier marks it as a test (B-391)
        # Already done together with others in one radio message (Z-Wave multicast, E-312).
        together = self.done_together.pop(request.entity_id, None)
        result = together or await self._ha.call_action(
            request.action, request.entity_id, request.data
        )
        checks = (*decision.checks, Check("6_done", result.ok, result.error or ""))
        if not result.ok:
            return Decision(
                request,
                Outcome.FAILED,
                "device_did_not_respond",
                decision.step,
                checks,
                decision.at,
            )
        now = self._clock.now()
        self._last_change[request.entity_id] = now
        entity = self.model.entities.get(request.entity_id)
        if entity is not None and entity.platform in self.state.cloud:
            self._cloud_last = now
        key = (request.entity_id, now.date().isoformat())
        self._changes_today[key] = self._changes_today.get(key, 0) + 1
        return Decision(
            request, Outcome.EXECUTED, None, decision.step, checks, decision.at, result.context_id
        )

    def evaluate(self, request: ActionRequest, lifted: bool = False) -> Decision:
        """Steps 1–5 without executing anything (also used for "what would you do?").
        ``lifted``: as if the skill stood on Autonomous in that room – 'Op Autonoom …' in Test
        het huis (design 70.2); the catalogue and every other limit still hold."""
        now = self._clock.now()
        checks: list[Check] = []
        quarter = self._quarter(request, now)
        if quarter is not None:
            # A testkwartier (B-391): as on Autonomous, and the decision says it was a test.
            lifted = True
            request = replace(request, facts={**request.facts, "test": quarter})

        def deny(name: str, reason: str) -> Decision:
            checks.append(Check(name, False, reason))
            return Decision(request, Outcome.DENIED, reason, None, tuple(checks), now)

        # 1. Real and valid
        entity = self.model.entities.get(request.entity_id)
        if entity is None:
            return deny("1_valid", "unknown_entity")
        if request.action.split(".", 1)[0] != entity.domain:
            return deny("1_valid", "action_domain_mismatch")
        if entity.disabled:
            return deny("1_valid", "entity_disabled")
        if request.created_at is not None and now - request.created_at > _STALE_AFTER:
            return deny("1_valid", "stale_context")
        if (
            request.layer is Layer.RESTORE
            and request.action != _RESTORE_ACTION
            and request.entity_id not in self.state.owned
            and request.entity_id not in self.state.day_rhythm_taken
        ):
            return deny("1_valid", "restore_only_automations")
        checks.append(Check("1_valid", True))

        # 2. Allowed
        role = self.state.roles.get(entity.entity_id)
        limit = hard_limit_reason(
            entity,
            request.action,
            request.data,
            role,
            self.state.protected_entities,
            entity.entity_id in self.state.openers,
        )
        if limit is not None:
            return deny("2_allowed", f"hard_limit:{limit}")
        # A group answers for its lamps (E-312): one switched by hand, watched by another system
        # or under a house rule holds the whole group back. Met rust laten (E-458): what is
        # ticked is never addressed itself, and a group ticked holds all that is in it; a lamp
        # ticked by itself goes along with a group that steers it (the administrator's choice).
        members = self.model.groups.get(entity.entity_id, ())
        whole = (entity.entity_id, *members)
        held = self.model.held
        if request.layer is not Layer.RESTORE and (
            entity.entity_id in self.state.excluded
            or entity.entity_id in held
            or any(e in held for e in members)
        ):
            return deny("2_allowed", "excluded_by_user")
        if entity.entity_id in self.state.leave_alone and request.layer not in _A_PERSON:
            return deny("2_allowed", "device_setting")  # a setting or status (E-431)
        if entity.entity_id in self.state.hand_switches and request.layer not in _A_PERSON:
            return deny("2_allowed", "hand_switch")  # the residents' own switch (E-448)
        kind = recognise(self.catalog, entity, role).kind
        if (
            request.layer is Layer.RESTORE
            and entity.entity_id in self.state.owned
            and (kind is None or kind.allowed(request.action) is None)
        ):
            # What HouseMind set itself on a device it no longer steers – the home battery or
            # the charger (E-363, B-248) – goes back to exactly what it was before HouseMind.
            # Only that: the action and value come from HouseMind's own register.
            if self.state.alarm:
                return deny("3_now", "alarm")
            for name, detail in (("2_allowed", "own_restore"), ("3_now", ""), ("4_step", "")):
                checks.append(Check(name, True, detail))
            checks.append(Check("5_sensible", True))
            return Decision(request, Outcome.EXECUTED, None, Step.AUTONOMOUS, tuple(checks), now)
        if kind is None:
            return deny("2_allowed", "unknown_device_kind")
        if self.state.netcode and entity.domain in ("number", "select", "switch"):
            text = re.sub(r"[^a-z0-9]", "", f"{entity.entity_id}{entity.name}".lower())
            if any(word in text for word in self.state.netcode):
                return deny("2_allowed", "not_allowed")  # grid code settings: never (E-340)
        allowed = kind.allowed(request.action)
        if allowed is None:
            return deny("2_allowed", "action_not_allowed")
        if request.layer is Layer.TEST and kind.safety_class not in (
            SafetyClass.FREE,
            SafetyClass.CAREFUL,
        ):
            return deny("2_allowed", "test_not_allowed")  # only what may go back (E-480)
        value_problem = allowed.accepts(request.data)
        if value_problem is not None:
            return deny("2_allowed", value_problem)
        checks.append(Check("2_allowed", True, kind.key))

        # 3. May it now
        if self.state.alarm and request.layer is not Layer.EMERGENCY:
            return deny("3_now", "alarm")
        if request.layer is not Layer.EMERGENCY:
            unmet = self.state.conditions(request, entity)
            if unmet is not None:
                return deny("3_now", unmet)
        if entity.entity_category in ("config", "diagnostic") and (
            entity.entity_id not in self.state.roles
        ):
            # A device's own setting or diagnostic is never touched (E-55: an installation's
            # maximum current is no "charging current"), unless house knowledge gave it a
            # role – a home battery's work mode is often a setting.
            return deny("2_allowed", "not_allowed")
        exempt = request.layer in _PEOPLE_EXEMPT
        if not exempt:
            if self.state.inactive:
                return deny("3_now", "inactive")
            asleep = self.state.part_off(request)
            if asleep is not None:
                return deny("3_now", f"part_off:{asleep}")
            if self.state.paused_until is not None and now < self.state.paused_until:
                return deny("3_now", "paused")
            domain_pause = self.state.paused_domains.get(entity.domain)
            if domain_pause is not None and now < domain_pause:
                return deny("3_now", "paused")
            leaving = request.skill in ("light_off_leave", "media_off_empty")
            coming_in = request.skill in ("light_on_entry", "night_light")
            # Welterusten (E-517): the house went to sleep, so the evening's choices outside the
            # bedrooms are over – a lamp put on by hand at nine goes off at bedtime too.
            goodnight = request.skill in GOODNIGHT
            for one in whole:
                win_until = self.state.people_win_until.get(one)
                # A lamp on the day's rhythm that nobody chose a colour for (E-467): switched
                # on with a plain "on" it is someone's lamp, and still only gets the day's colour.
                released = (
                    (leaving and one in self.state.released)
                    or (coming_in and one in self.state.come_in)
                    or bool(request.facts.get("day_rhythm"))
                    or goodnight
                )
                if (
                    win_until is not None
                    and now < win_until
                    and request.layer not in _ASKED
                    and not released
                ):
                    return deny("3_now", "people_win")
            if request.layer != Layer.PERSON_ANSWER and any(
                self.state.signalling(one, now) for one in whole
            ):
                return deny("3_now", "signal")
            if request.layer in _OWN_DOING and any(self.state.setting(one, now) for one in whole):
                # The administrator is setting its standen (E-516): HouseMind's own doing waits.
                return deny("3_now", "setting_stands")
            if request.layer in _EVERYDAY and any(self.state.testing(one, now) for one in whole):
                # Doe het echt runs on it (E-518): the everyday rules wait for the minute.
                return deny("3_now", "in_test")
            if request.layer != Layer.PERSON_ANSWER:
                for one in whole:
                    asked = request if one == entity.entity_id else replace(request, entity_id=one)
                    veto = self.state.house_rule_veto(asked)
                    if veto is not None:
                        return deny("3_now", f"house_rule:{veto}")
        # Another system or an automation controls it: only watch (E-42, E-68). A group when it
        # steers the group itself or at least half of its lamps (E-460).
        watch_only = not exempt and watched(self.state.other_system, entity.entity_id, members)
        # What it is, only AI said so far or HouseMind derived it from behaviour (E-201, E-203,
        # 50.8): watched and learned as that kind – so not denied, which would teach nothing –,
        # but never proposed or done until a Klopt. A person's own request, an emergency and
        # giving back go as for any device.
        own = not exempt and request.layer is not Layer.PERSON_ANSWER
        from_ai = own and any(e in self.state.from_ai for e in whole)
        derived = own and any(e in self.state.derived for e in whole)
        unconfirmed = from_ai or derived
        why = "ai_unconfirmed" if from_ai else "behaviour_unconfirmed" if derived else ""
        why = "other_system" if watch_only else why
        checks.append(Check("3_now", True, why))

        # 4. Step
        step = self._effective_step(
            request, entity, kind, allowed.max_step, lifted, allowed.emergency
        )
        if watch_only or unconfirmed:
            step = Step.OBSERVE
        checks.append(Check("4_step", True, step.name.lower()))
        if step is Step.OBSERVE:
            return Decision(request, Outcome.OBSERVED, why or None, step, tuple(checks), now)
        if step is Step.PROPOSE and request.layer is not Layer.PERSON_ANSWER:
            return Decision(request, Outcome.PROPOSED, None, step, tuple(checks), now)

        # 5. Sensible
        if not exempt:
            last = self._last_change.get(entity.entity_id)
            gap = kind.min_interval_s
            if entity.platform in self.state.flash:
                gap = max(gap, FLASH_GAP_S)
            # A person's explicit "do it" or "undo" is not flapping: no minimum interval.
            if (
                last is not None
                and request.layer not in _ASKED
                and (now - last).total_seconds() < gap
            ):
                return deny("5_sensible", "rate_limit")
            if (
                entity.platform in self.state.cloud
                and request.layer not in _ASKED
                and self._cloud_last is not None
                and now - self._cloud_last < CLOUD_GAP
            ):
                return deny("5_sensible", "rate_limit")
            if kind.max_changes_per_day is not None:
                count = self._changes_today.get((entity.entity_id, now.date().isoformat()), 0)
                if count >= kind.max_changes_per_day:
                    return deny("5_sensible", "daily_limit")
            if (
                entity.entity_id in self.state.battery_powered
                and last is not None
                and (now - last) < timedelta(minutes=5)
            ):
                return deny("5_sensible", "battery_saving")
        checks.append(Check("5_sensible", True))
        return Decision(request, Outcome.EXECUTED, None, step, tuple(checks), now)

    # -- helpers ------------------------------------------------------------------------

    async def submit_tidy(
        self,
        entity_id: str,
        enable: bool,
        layer: Layer,
        created_at: datetime | None,
        user_id: str | None,
        reason: str,
    ) -> Decision:
        """Switch an entity off in Home Assistant's registry after an administrator's yes, or
        back on (Home Assistant netjes, B-243): the same six steps as every action. Never a
        safety sensor, an alarm, a lock, a siren, a valve or HouseMind's own; never what
        HouseMind steers now; not during a pause. Back on: only what HouseMind switched off,
        and always – also during a pause. Nothing is ever deleted."""
        now = self._clock.now()
        action = "registry.enable" if enable else "registry.disable"
        request = ActionRequest(
            layer, entity_id, action, reason=reason, created_at=created_at, user_id=user_id
        )
        checks: list[Check] = []

        def deny(name: str, why: str) -> Decision:
            checks.append(Check(name, False, why))
            return Decision(request, Outcome.DENIED, why, None, tuple(checks), now)

        # 1. Real and valid
        entity = self.model.entities.get(entity_id)
        if entity is None:
            return deny("1_valid", "unknown_entity")
        if created_at is not None and now - created_at > _STALE_AFTER:
            return deny("1_valid", "stale_context")
        if enable and entity_id not in self.state.tidy_off:
            return deny("1_valid", "restore_only_own")
        if not enable and entity.disabled:
            return deny("1_valid", "entity_disabled")
        checks.append(Check("1_valid", True))
        # 2. Allowed
        if not enable:
            if entity.domain in _TIDY_NEVER_DOMAINS or entity.platform == "housemind":
                return deny("2_allowed", "not_allowed")
            if entity.device_class in _TIDY_NEVER_CLASSES:
                return deny("2_allowed", "not_allowed")
            if entity_id in self.state.owned or entity_id in self.state.protected_entities:
                return deny("2_allowed", "in_use")
        checks.append(Check("2_allowed", True, "registry"))
        # 3. May it now: switching off waits for a pause to end; back on never waits.
        if not enable and (
            self.state.inactive
            or self.state.alarm
            or (self.state.paused_until is not None and now < self.state.paused_until)
        ):
            return deny("3_now", "paused")
        checks.append(Check("3_now", True))
        # 4. Step: only a person's yes switches off; back on is the give-back layer.
        if layer is not (Layer.RESTORE if enable else Layer.PERSON_ANSWER):
            return deny("4_step", "person_only")
        checks.append(Check("4_step", True, "person"))
        checks.append(Check("5_sensible", True))  # one entity per request
        # 6. Done: Home Assistant confirms.
        result = await self._ha.set_entity_enabled(entity_id, enable)
        checks.append(Check("6_done", result.ok, result.error or ""))
        if not result.ok:
            return Decision(
                request, Outcome.FAILED, "device_did_not_respond", None, tuple(checks), now
            )
        self._last_change[entity_id] = now
        return Decision(request, Outcome.EXECUTED, None, Step.AUTONOMOUS, tuple(checks), now)

    def _quarter(self, request: ActionRequest, now: datetime) -> int | None:
        """The testkwartier this everyday request falls in, if any (E-477)."""
        if not self.state.tests or request.area_id is None or request.layer not in _EVERYDAY:
            return None
        part = self.state.part_of(request)
        found = self.state.tests.get((part, request.area_id)) if part else None
        return found[1] if found is not None and now < found[0] else None

    def _effective_step(
        self,
        request: ActionRequest,
        entity: Entity,
        kind: DeviceKind,
        action_max: Step,
        lifted: bool = False,
        in_emergency: bool = False,
    ) -> Step:
        caps = [kind.max_step, action_max]
        area = self.model.area_of(entity.entity_id)
        # An administrator limits everyday autonomy, never the emergency reactions, and never
        # giving back what HouseMind took over.
        for key in (entity.domain, f"area:{area.id}" if area else "", entity.entity_id):
            # A test lifts the administrator's own limit – that is what it is for (B-391).
            if (
                key in self.state.admin_max_step
                and request.layer not in _PEOPLE_EXEMPT
                and request.layer is not Layer.TEST
                and not lifted
            ):
                caps.append(self.state.admin_max_step[key])
        if request.action in AUTOMATION_SWITCH:
            caps.append(Step.PROPOSE)  # E-42: an automation only goes off or on after a yes
        catalogue_max = min(caps)
        if request.layer in (Layer.PERSON_ANSWER, Layer.RESTORE, Layer.TEST):
            # A person's "Do it" may execute what HouseMind may at least propose; so may an
            # administrator's test (B-390).
            return Step.AUTONOMOUS if catalogue_max >= Step.PROPOSE else Step.OBSERVE
        if request.layer is Layer.EMERGENCY:
            # A fixed rule: the device's own limit and E-42 hold, not the cap one action has for
            # everyday autonomy (frost switches a thermostat that is off to heating, QA 2 L4).
            # The hard limits were checked before (step 2). An action the catalogue marks for
            # emergencies goes, whatever the kind's own step (E-542: music quiet).
            base = Step.AUTONOMOUS if in_emergency else kind.max_step
            return min([base, *caps[2:]])  # caps[1] is that one action's cap
        step = catalogue_max
        if request.layer is Layer.AI:
            step = min(step, Step.PROPOSE)  # B-17: AI never acts directly
        if self.state.untested_ha and kind.safety_class is not SafetyClass.FREE:
            step = min(step, Step.PROPOSE)  # E-155
        if request.action in self.state.broken_actions:
            step = Step.OBSERVE  # E-152: what no longer works is only watched
        if request.skill is not None and request.layer in (
            Layer.REFLEX,
            Layer.LEARNING,
            Layer.PLANNER,
            Layer.TRIAL,
        ):
            found = (
                self.state.device_step(request.skill, request.area_id, request.entity_id)
                if self.state.device_step is not None
                else self.state.skill_step(request.skill, request.area_id)
            )
            lease = self.state.leases.get(request.skill)
            if (
                request.layer is Layer.PLANNER
                and found is Step.PROPOSE
                and lease is not None
                and self._clock.now() < lease
            ):
                found = Step.AUTONOMOUS  # the yes holds for this block; other caps still do
            if lifted:
                found = Step.AUTONOMOUS  # a test: as on Autonomous (design 70.2, 70.4)
            step = min(step, found)
        careful_now = (
            self.state.untested_ha and kind.safety_class is not SafetyClass.FREE
        ) or request.action in self.state.broken_actions
        if (
            request.layer is Layer.TRIAL
            and step is Step.PROPOSE
            and kind.safety_class in (SafetyClass.FREE, SafetyClass.CAREFUL)
            and catalogue_max is Step.AUTONOMOUS
            and not careful_now  # a trial never goes around the untested version
        ):
            # Only reversible, low-risk actions are tried; everything else stays a proposal.
            step = Step.AUTONOMOUS
        return step
