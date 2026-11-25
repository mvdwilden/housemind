"""The existing automations in Home Assistant (E-03, E-41, E-42, E-112, E-317): what each one
teaches, which device it steers, taking one over when HouseMind can do it all itself – after a
yes – and giving it back."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from housemind.brain.automations import (
    TURN_OFF,
    TURN_ON,
    Automations,
    HandWords,
    Reading,
    can_take_over,
    read_automation,
)
from housemind.brain.automations import Status as AutomationStatus
from housemind.brain.learning import Learning
from housemind.brain.notify import (
    Kind,
)
from housemind.brain.skills import SkillEngine
from housemind.core.clock import Clock
from housemind.core.model import Entity, HouseModel, Source, State, StateChange
from housemind.ha.adapter import HAScript, HomeAssistant
from housemind.knowledge.catalog import Catalog, Step
from housemind.memory.database import Database
from housemind.parts import Part
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome, SafetyGate
from housemind.world.world import World, WorldState

_LOGGER = logging.getLogger(__name__)


class AutomationsPart(Part):
    """The existing automations in Home Assistant (E-03, E-41, E-42, E-112, E-317): what each one"""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)

        def _admin_lift(
            self, skill: str, area_id: str | None, entity_id: str | None = None
        ) -> Step | None: ...

        _keep_off_chosen: set[str]
        _kept_off: set[str]

        async def _notify(self) -> None: ...
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

        async def _record(self, decision: Decision, previous: State | None = None) -> int: ...
        async def _resolve(self, topic: str) -> None: ...
        def _skill_step(
            self, skill: str, area_id: str | None, entity_id: str | None = None
        ) -> Step: ...

        area_roles: dict[str, str]
        automations: Automations
        catalog: Catalog
        clock: Clock
        db: Database

        def domain_cap(self, domain: str) -> Step | None: ...
        def domain_step(self, domain: str) -> Step: ...

        gate: SafetyGate | None
        ha: HomeAssistant
        hand_words: HandWords
        learning: Learning | None
        learning_allowed: Any
        model: HouseModel
        scripts: list[HAScript]

        async def set_admin_step(
            self, key: str, step: str | None, user_id: str | None, match: bool = True
        ) -> bool: ...

        parts_off: dict[str, dict[str, str]]

        skill_params: dict[str, dict[str, Any]]
        skills: SkillEngine | None
        world: World | None

    def ready_to_take_over(self, reading: Reading, whole: bool = True) -> bool:
        """HouseMind can do everything this automation does, and no administrator holds one
        of its domains back (E-42). ``whole=False``: an administrator heard what else stops
        with it and said yes (E-317)."""
        if not self.learning_allowed:
            return False  # B-20: without a yes to learning, HouseMind takes nothing over
        if any(self.domain_cap(d) not in (None, Step.AUTONOMOUS) for d in reading.domains):
            return False
        if not reading.learned and not whole:
            # It only switches lamps of these rules (E-461): theirs when they are HouseMind's.
            rules = self._hm.lamp_rules(reading)
            return bool(rules) and all(self.autonomous_here(*rule) for rule in rules)
        return can_take_over(
            reading,
            lambda domain: self.domain_step(domain) is Step.AUTONOMOUS,
            self.autonomous_here,
            lambda skill: self.skill_params.get(skill, {}).get("domain"),
            whole=whole,
        )

    def steering(self, skill: str, area_id: str) -> list[Reading]:
        """The automations in Home Assistant that steer this skill in this room (E-317): on,
        or switched off by HouseMind."""
        readings = sorted(self.automations.readings.values(), key=lambda r: r.automation.name)
        return [
            reading
            for reading in readings
            if (skill, area_id) in reading.learned
            and (
                reading.automation.enabled
                or self.automations.status.get(reading.entity_id) is AutomationStatus.TURNED_OFF
            )
        ]

    def automation_question(self, skill: str, area_id: str) -> dict[str, Any] | None:
        """The question in the row (E-317). On Autonomous while an automation still steers
        it: may it go off, so HouseMind does it itself – with the other rows that go along and
        what else would stop. Below Autonomous while HouseMind switched one off: may it go on
        again, so nothing is left undone. After a "no" a button stays instead of the question."""
        here = self.autonomous_here(skill, area_id)
        for reading in self.steering(skill, area_id):
            entity_id = reading.entity_id
            status = self.automations.status.get(entity_id, AutomationStatus.SEEN)
            if here and reading.automation.enabled and status is not AutomationStatus.TURNED_OFF:
                family = self.family(reading)
                return {
                    # On the same devices (E-538): they go with it, or hold it all on.
                    "with": [r.automation.name for r in family if self._hm.can_go(r)],
                    "held": [r.automation.name for r in family if not self._hm.can_go(r)],
                    "kind": "off",
                    "id": entity_id,
                    "name": reading.automation.name,
                    "along": [
                        p
                        for p in reading.learned
                        if p != (skill, area_id) and not self.autonomous_here(*p)
                    ],
                    "stops": list(reading.stops),
                    "other": reading.stops_other,
                    "readable": reading.automation.readable,
                    "declined": status is AutomationStatus.DECLINED,
                }
            if not here and status is AutomationStatus.TURNED_OFF:
                return {
                    "kind": "on",
                    "id": entity_id,
                    "name": reading.automation.name,
                    "declined": entity_id in self._keep_off_chosen,
                }
        return None

    def _holds(self, a: Reading, b: Reading) -> bool:
        """Two automations act on the same device, the way the gate sees it (E-42, E-460):
        the same lamp, or a group and at least half of its lamps."""
        if a.controls & b.controls:
            return True
        for one, other in ((a, b), (b, a)):
            for group in one.controls:
                members = self.model.groups.get(group, ())
                if members and 2 * len(set(members) & other.controls) >= len(members):
                    return True
        return False

    def family(self, reading: Reading, on: bool = True) -> list[Reading]:
        """The other automations on the same devices, as far as that reaches (E-538): on
        (``on``) or switched off by HouseMind. They go off together and on together: one left
        on keeps HouseMind off those devices, and the work of the ones that went off would be
        left undone. A button or switch of the residents (E-448) or a signal (E-461) holds no
        device and is never part of it."""

        def member(other: Reading) -> bool:
            if other.hand is not None or other.signal:
                return False
            if on:
                return other.automation.enabled
            return self.automations.status.get(other.entity_id) is AutomationStatus.TURNED_OFF

        found = {reading.entity_id: reading}
        grew = True
        while grew:
            grew = False
            for other in self.automations.readings.values():
                if other.entity_id in found or not member(other):
                    continue
                if any(self._holds(other, r) for r in found.values()):
                    found[other.entity_id] = other
                    grew = True
        return [r for entity_id, r in sorted(found.items()) if entity_id != reading.entity_id]

    def autonomous_here(self, skill: str, area_id: str) -> bool:
        """This skill in this room is on Autonomous, and the limit allows it (20.12)."""
        cap = self.domain_cap(str(self.skill_params.get(skill, {}).get("domain")))
        step = self._skill_step(skill, area_id)
        return (min(step, cap) if cap is not None else step) is Step.AUTONOMOUS

    def _still_ours(self, reading: Reading) -> bool:
        """HouseMind still does what it took over of this automation: every room it teaches
        on Autonomous within the limit, and none of its parts off (E-255, E-400)."""
        if any(domain in self.parts_off for domain in reading.domains):
            return False
        return self.ready_to_take_over(reading, whole=False)

    def pairs_for_key(self, key: str) -> set[tuple[str, str]]:
        """The rows (skill × room) an administrator's step for this key covers, as far as an
        automation teaches them: one row, a room, a part or a device."""
        pairs = {p for r in self.automations.readings.values() for p in r.learned}
        if "@" in key:
            skill, _, area_id = key.partition("@")
            return {(skill, area_id)} & pairs
        if key.startswith("area:"):
            return {p for p in pairs if p[1] == key[5:]}
        if key in self.model.entities:
            return {
                p
                for r in self.automations.readings.values()
                if key in r.controls
                for p in r.learned
            }
        return {p for p in pairs if self.skill_params.get(p[0], {}).get("domain") == key}

    async def match_automations(
        self, user_id: str | None, chosen: set[tuple[str, str]] | None = None
    ) -> None:
        """Autonomous means HouseMind does it, so the automation is off; anything less means
        the automation does it again (E-42, E-255, E-317; bijgesteld 01-11-2026).
        ``chosen``: the rows an administrator just put on Autonomous – that choice is the yes
        for an automation HouseMind can wholly take over (one that does more still asks, in
        the row). Back from Autonomous – by an administrator, a part limited or off, or a step
        back – what HouseMind took over goes back on at once, so nothing is left undone."""
        given_back: list[str] = []
        for entity_id in self.automations.turned_off():
            reading = self.automations.readings.get(entity_id)
            if reading is None or entity_id in self._keep_off_chosen:
                continue  # not read yet, or an administrator chose to keep it off
            if self._still_ours(reading):
                continue
            result = await self.restore_automation(entity_id, user_id, reason="step_down")
            if result["ok"]:
                given_back.append(reading.automation.name)
                given_back += await self._back_with_it(user_id)
        for reading in sorted(self.automations.readings.values(), key=lambda r: r.automation.name):
            if not chosen or not chosen & set(reading.learned):
                continue
            status = self.automations.status.get(reading.entity_id, AutomationStatus.SEEN)
            if not reading.automation.enabled or status is AutomationStatus.TURNED_OFF:
                continue
            if reading.whole and self.ready_to_take_over(reading):
                await self.turn_off_automation(reading.entity_id, user_id)
        if given_back and self.world is not None:
            now = self.clock.now()
            await self._raise(
                Kind.INFO,
                f"given_back:{now.isoformat()}",
                "notify.automation_given_back",
                {"names": ", ".join(f"‘{n}’" for n in given_back), "count": len(given_back)},
                self.world.snapshot(now),
                concerns="admins",
            )

    async def _back_with_it(self, user_id: str | None) -> list[str]:
        """One came back on: what HouseMind took over with it on the same devices it can no
        longer do, so that goes back on too – or its work stays undone (E-460)."""
        back: list[str] = []
        again = True
        while again:
            again = False
            for entity_id in self.automations.turned_off():
                reading = self.automations.readings.get(entity_id)
                if reading is None or entity_id in self._keep_off_chosen:
                    continue
                if not any(self._hm.rule_hold(s, a)[0] for s, a in self._hm.rules_of(reading)):
                    continue
                result = await self.restore_automation(
                    entity_id, user_id, reason="step_down", family=False
                )
                if result["ok"]:
                    back.append(reading.automation.name)
                    again = True
        return back

    def _keep_off(self) -> None:
        """While an automation is on, HouseMind only watches its devices (E-42, E-68). The
        switches of the hand of the resident are theirs (E-448)."""
        assert self.gate is not None
        controlled = self.automations.controlled()
        self.gate.state.other_system = (self.gate.state.other_system - self._kept_off) | controlled
        self._kept_off = controlled
        switches = self.automations.hand_switches()
        self.gate.state.hand_switches = switches
        if self.skills is not None and self.skills.hand_switches != switches:
            self.skills.hand_switches = switches  # never one of its lamps
            self.skills.forget()

    async def _sync_automations(self) -> None:
        """Read the existing automations again: what they control and what they teach."""
        assert self.gate is not None and self.learning is not None
        try:
            found = await self.ha.automations()
        except (ConnectionError, TimeoutError):
            _LOGGER.warning("Could not read the automations from Home Assistant")
            return
        try:
            self.scripts = await self.ha.scripts()  # only to show where they act (E-258)
        except (ConnectionError, TimeoutError):
            _LOGGER.warning("Could not read the scripts from Home Assistant")
        for automation in found:
            if automation.entity_id not in self.model.entities:  # made after HouseMind started
                self.model.entities[automation.entity_id] = Entity(
                    automation.entity_id,
                    automation.config_id or automation.entity_id,
                    automation.name,
                )
        readings = [
            read_automation(
                a,
                self.model,
                self.catalog,
                self.gate.state.roles,
                self.skill_params,
                self.area_roles,
                self.hand_words,
            )
            for a in found
        ]
        now = self.clock.now()
        await self.automations.update(readings, now)
        self._keep_off()
        tracker = getattr(self.ha, "tracker", None)
        if tracker is not None:  # what a button does is whoever pressed it (E-448)
            tracker.hands = self.automations.hands()
            tracker.signals = self.automations.signals()  # what a signal does is nobody's (E-461)
        self._hm.forget_lamp_rules()
        self.learning.taught_by = {}
        for reading in sorted(readings, key=lambda r: r.automation.name):
            for pair in reading.learned:
                self.learning.taught_by.setdefault(pair, reading.automation.name)
        if self.skills is not None:  # the light in a garage only then (E-460)
            self.skills.with_automation = {
                pair
                for reading in readings
                if reading.automation.enabled
                or self.automations.status.get(reading.entity_id) is AutomationStatus.TURNED_OFF
                for pair in reading.learned
            }
        if not self.learning_allowed:
            return  # B-20: learning only after a yes
        for reading in readings:
            status = self.automations.status.get(reading.entity_id)
            current = reading.automation.enabled or status is AutomationStatus.TURNED_OFF
            if current and reading.entity_id in self.automations.seeded:
                # Read before HouseMind looked at its wait: only the wait, once (E-444).
                for (skill, area_id), values in reading.values.items():
                    if skill == "light_off_leave" and "hold_minutes" in values:
                        self.learning.seed_hold(area_id, values["hold_minutes"], now)
            if reading.entity_id in self.automations.seeded or not current or not reading.learned:
                continue
            for skill, area_id in reading.learned:
                self.learning.from_automation(
                    skill, area_id, reading.values.get((skill, area_id), {}), now
                )
            await self.automations.mark_seeded(reading.entity_id)

    async def _review_automations(self, snapshot: WorldState) -> None:
        """Once HouseMind can do what an automation does, ask an administrator – yes or no –
        whether it may switch it off (E-42). The gate makes this a proposal (E-110)."""
        assert self.gate is not None
        await self._sync_automations()
        if await self.db.get_setting("given_back", False):
            return
        await self.match_automations(None)  # a step back by learning, a part off (E-255)
        if not self.learning_allowed:
            return
        now = self.clock.now()
        changed = False
        for reading in list(self.automations.readings.values()):
            entity_id, name = reading.entity_id, reading.automation.name
            status = self.automations.status.get(entity_id, AutomationStatus.SEEN)
            if status is AutomationStatus.OFFERED and not reading.automation.enabled:
                await self.automations.set_status(entity_id, AutomationStatus.SEEN, now, None)
                await self._resolve(f"automation:{entity_id}")
                changed = True
            if status is not AutomationStatus.SEEN or not reading.automation.enabled:
                continue
            if not self.ready_to_take_over(reading):
                continue
            if any(self._admin_lift(s, a) is Step.AUTONOMOUS for s, a in reading.learned):
                # An administrator set it to Autonomous: the question is in the row or in the
                # conversation with Vesta, where they are – not once more as a message (E-317).
                continue
            request = ActionRequest(
                Layer.LEARNING,
                entity_id,
                TURN_OFF,
                reason="take_over",
                created_at=now,
                facts={"automation": name, "domains": sorted(reading.domains)},
            )
            decision = await self.gate.submit(request)
            await self._record(decision)
            if decision.outcome is not Outcome.PROPOSED:
                continue
            await self.automations.set_status(entity_id, AutomationStatus.OFFERED, now, None)
            await self._raise(
                Kind.LEARNING,
                f"automation:{entity_id}",
                "notify.automation_take_over",
                {"name": name},
                snapshot,
                concerns="admins",
                actions=(("yes", "notify.answer.turn_off"), ("no", "notify.answer.keep_on")),
            )
            changed = True
        if changed:
            await self._notify()

    def _taken_over(self, domain: str) -> list[str]:
        """The automations HouseMind switched off that touch a part (lighting, covers …)."""
        return [
            entity_id
            for entity_id in self.automations.turned_off()
            if (reading := self.automations.readings.get(entity_id)) and domain in reading.domains
        ]

    async def _after_step_change(
        self,
        before: dict[str, Step | None],
        user_id: str | None = None,
        woke: set[str] | None = None,
    ) -> None:
        """A part's limit changed (E-255, B-99; bijgesteld 01-11-2026). Set back from
        Autonomous, or off: what HouseMind took over for it goes back on at once, so nobody's
        work is left undone. Lifted to Autonomous, or ``woke`` (on again on Autonomous): the
        administrator's yes for what HouseMind can wholly take over there. A question from
        before goes."""
        chosen: set[tuple[str, str]] = set()
        for part in woke or ():
            chosen |= self.pairs_for_key(part)
        for domain, was in before.items():
            topic = f"restore:{domain}"
            if topic in self._raised:
                await self._resolve(topic)
            if self.domain_cap(domain) in (None, Step.AUTONOMOUS) and was not in (
                None,
                Step.AUTONOMOUS,
            ):
                chosen |= self.pairs_for_key(domain)
        await self.match_automations(user_id, chosen)

    async def answer_restore(self, domain: str, yes: bool, user_id: str | None) -> dict[str, Any]:
        """An administrator's answer (E-255): yes switches the taken-over automations of that
        part back on (each through the gate); no leaves them off, with "Weer aanzetten"."""
        topic = f"restore:{domain}"
        if topic not in self._raised:
            return {"ok": False, "reason": "already_answered"}
        failed: list[str] = []
        if yes:
            for entity_id in self._taken_over(domain):
                result = await self.restore_automation(entity_id, user_id, reason="step_down")
                if not result["ok"]:
                    failed.append(entity_id)
        await self._resolve(topic)
        await self._notify()
        return {"ok": not failed, "failed": failed}

    async def answer_automation(
        self, entity_id: str, yes: bool, user_id: str | None, later: bool = False
    ) -> dict[str, Any]:
        """An administrator's yes or no to HouseMind's question (E-42). The first answer
        counts (E-81): a late phone button changes nothing. ``later``: "Not yet", in the
        question that came from proof (E-460)."""
        if self.automations.status.get(entity_id) is not AutomationStatus.OFFERED:
            return {"ok": False, "reason": "already_answered"}
        offered = self._hm._proof_offers.get(entity_id)
        if offered is not None:
            action = "yes" if yes else "later" if later else "no"
            return await self._hm.answer_offer(entity_id, offered, action, user_id)
        if yes:
            return await self.turn_off_automation(entity_id, user_id)
        await self.automations.set_status(
            entity_id, AutomationStatus.DECLINED, self.clock.now(), user_id
        )
        await self._resolve(f"automation:{entity_id}")
        await self._notify()
        return {"ok": True}

    async def turn_off_automation(
        self, entity_id: str, user_id: str | None, along: bool = False
    ) -> dict[str, Any]:
        """Switch an automation off after an administrator's yes – in the question or in the
        house settings (E-42). Only when HouseMind can do all of it; never deleted, and
        HouseMind remembers it to switch it back on (E-112). ``along`` (E-317, the question in
        the row or Vesta's): the yes covered its other rows – they go to Autonomous as the
        administrator's own step – and what else stops with it, which the question named."""
        reading = self.automations.readings.get(entity_id)
        if reading is None:
            return {"ok": False, "reason": "unknown_automation"}
        if self.automations.status.get(entity_id) is AutomationStatus.TURNED_OFF:
            return {"ok": False, "reason": "already_off"}
        if not reading.automation.enabled:
            return {"ok": False, "reason": "already_off"}
        if not reading.whole and not along:
            return {"ok": False, "reason": "not_whole"}  # HouseMind would not do all of it
        if along and not self._hm.rules_of(reading):
            return {"ok": False, "reason": "not_whole"}  # nothing of it HouseMind does
        # The others on the same devices go with it (E-538) – or none goes: one left on keeps
        # HouseMind off those devices, and the work of this one would be left undone.
        family = self.family(reading)
        kept = [r.automation.name for r in family if not self._hm.can_go(r)]
        if kept:
            return {"ok": False, "reason": "held_by", "by": kept}
        if along:
            for one in (reading, *family):
                for skill, area_id in one.learned:
                    if not self.autonomous_here(skill, area_id):
                        await self.set_admin_step(
                            f"{skill}@{area_id}", "autonomous", user_id, match=False
                        )
        if not all(self.ready_to_take_over(r, whole=not along) for r in (reading, *family)):
            return {"ok": False, "reason": "not_ready"}
        self._keep_off_chosen.discard(entity_id)
        assert self.gate is not None
        now = self.clock.now()
        topic = f"automation:{entity_id}"
        name = reading.automation.name
        decision = await self.gate.submit(
            ActionRequest(
                Layer.PERSON_ANSWER,
                entity_id,
                TURN_OFF,
                reason="take_over",
                created_at=now,
                user_id=user_id,
                facts={"automation": reading.automation.name},
            )
        )
        await self._record(decision)
        if decision.executed:
            await self.automations.set_status(entity_id, AutomationStatus.TURNED_OFF, now, user_id)
            self.automations.switched(entity_id, on=False)
            for other in family:  # on the same devices: off with it (E-538)
                done = await self.gate.submit(
                    ActionRequest(
                        Layer.PERSON_ANSWER,
                        other.entity_id,
                        TURN_OFF,
                        reason="take_over",
                        created_at=now,
                        user_id=user_id,
                        facts={"automation": other.automation.name, "with": name},
                    )
                )
                await self._record(done)
                if done.executed:
                    await self.automations.set_status(
                        other.entity_id, AutomationStatus.TURNED_OFF, now, user_id
                    )
                    self.automations.switched(other.entity_id, on=False)
                    await self._resolve(f"automation:{other.entity_id}")
            self._keep_off()
            await self._resolve(topic)
        await self._notify()
        return {"ok": decision.executed, "reason": decision.reason}

    async def leave_automation(self, entity_id: str, user_id: str | None) -> dict[str, Any]:
        """'No' to switching it off (E-317, in the row or to Vesta): it stays on and HouseMind
        keeps watching its devices; the question goes, a button stays."""
        if entity_id not in self.automations.readings:
            return {"ok": False, "reason": "unknown_automation"}
        if self.automations.status.get(entity_id) is AutomationStatus.TURNED_OFF:
            return {"ok": False, "reason": "already_off"}
        await self.automations.set_status(
            entity_id, AutomationStatus.DECLINED, self.clock.now(), user_id
        )
        await self._resolve(f"automation:{entity_id}")
        await self._notify()
        return {"ok": True}

    async def keep_automation_off(self, entity_id: str, user_id: str | None) -> dict[str, Any]:
        """'No' to switching it on again after a step back (E-317): it stays off; the question
        goes and 'Weer aanzetten' stays."""
        if self.automations.status.get(entity_id) is not AutomationStatus.TURNED_OFF:
            return {"ok": False, "reason": "not_turned_off"}
        self._keep_off_chosen.add(entity_id)
        await self.db.set_setting("automation_keep_off", sorted(self._keep_off_chosen))
        await self._notify()
        return {"ok": True}

    async def restore_automation(
        self, entity_id: str, user_id: str | None, reason: str = "undo", family: bool = True
    ) -> dict[str, Any]:
        """Switch an automation HouseMind switched off back on – always possible, also during
        a pause (E-42, E-112). After an undo HouseMind does not ask about it again. The ones it
        switched off on the same devices come back with it (E-538): with this one on, HouseMind
        only watches those devices, so their work would be left undone."""
        if self.automations.status.get(entity_id) is not AutomationStatus.TURNED_OFF:
            return {"ok": False, "reason": "not_turned_off"}
        reading = self.automations.readings.get(entity_id)
        others = self.family(reading, on=False) if family and reading is not None else []
        assert self.gate is not None
        now = self.clock.now()
        decision = await self.gate.submit(
            ActionRequest(
                Layer.RESTORE,
                entity_id,
                TURN_ON,
                reason=reason,
                created_at=now,
                user_id=user_id,
            )
        )
        await self._record(decision)
        if decision.executed:
            # Given back, or a part set back from Autonomous (E-255): not a "no" to this
            # automation, so HouseMind may ask again later. An undo is a "no".
            back = reason in ("give_back", "step_down")
            status = AutomationStatus.SEEN if back else AutomationStatus.DECLINED
            await self.automations.set_status(entity_id, status, now, user_id)
            self.automations.switched(entity_id, on=True)
            if entity_id in self._keep_off_chosen:
                self._keep_off_chosen.discard(entity_id)
                await self.db.set_setting("automation_keep_off", sorted(self._keep_off_chosen))
            for other in others:  # on the same devices: back with it (E-538)
                await self.restore_automation(other.entity_id, user_id, reason, family=False)
            self._keep_off()
            await self._notify()
        return {"ok": decision.executed, "reason": decision.reason}

    async def _automation_switched(self, change: StateChange) -> None:
        """An automation went on or off. Someone switching one back on that HouseMind switched
        off means "keep it": HouseMind keeps off its devices again and asks no more."""
        if change.new is None or change.new.state not in ("on", "off"):
            return
        on = change.new.state == "on"
        entity_id = change.entity_id
        self.automations.switched(entity_id, on)
        self._keep_off()
        if change.source is Source.HOUSEMIND:
            return
        status = self.automations.status.get(entity_id)
        now = self.clock.now()
        if on and status is AutomationStatus.TURNED_OFF:
            await self.automations.set_status(entity_id, AutomationStatus.DECLINED, now, None)
            reading = self.automations.readings.get(entity_id)
            for other in self.family(reading, on=False) if reading is not None else []:
                # Switched on by hand in HA: the others on its devices come back too (E-538).
                await self.restore_automation(other.entity_id, None, "step_down", family=False)
            await self._back_with_it(None)  # what went with it cannot do its work now (E-460)
        elif not on and status is AutomationStatus.OFFERED:
            await self.automations.set_status(entity_id, AutomationStatus.SEEN, now, None)
            await self._resolve(f"automation:{entity_id}")
