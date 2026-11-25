"""Proof and one yes (E-460, B-375): repetition brings HouseMind to doing it itself.

A rule (a skill in a room) that was right often enough is *proven* (``Learning.proven``, only
counting, no AI). It gets one question that stays until it is answered: yes, not yet, or never.
A yes puts it on Autonomous at once. A rule an automation steers cannot ask: then the question
is about the automation, and about every automation holding the same devices, so nothing is
left undone; only when all their rules are proven, and only an administrator answers. A step
back brings the automation back on (``AutomationsPart.match_automations``).

Every switch still goes through the safety gate; people first and the catalogue's limits stay.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain.automations import Automations, Reading
from housemind.brain.automations import Status as AutomationStatus
from housemind.brain.learning import Learning
from housemind.brain.notify import SAFETY_SKILLS, Kind
from housemind.brain.rules import RuleBook
from housemind.brain.skills import SkillEngine
from housemind.core.clock import Clock
from housemind.core.model import HouseModel
from housemind.knowledge.catalog import Step
from housemind.memory.database import Database
from housemind.parts import Part
from housemind.safety.gate import SafetyGate, watched
from housemind.world.world import World, WorldState

READY = "ready:"
AUTOMATION = "automation:"
PROOF_ASK_AGAIN = timedelta(days=7)  # "Not yet": a week, the last step of asking again (E-294)
# The answers: yes, not yet, never.
PROOF_ACTIONS = (
    ("yes", "notify.answer.from_now_yes"),
    ("later", "notify.answer.later"),
    ("no", "notify.answer.not_ever"),
)
PROOF_KEYS = ("notify.proof_rule", "notify.proof_automation")


class ProofPart(Part):
    """Proof and one yes (E-460)."""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        _lamp_rules: dict[str, list[tuple[str, str]]]
        _proof_answers: dict[str, dict[str, str]]
        _proof_memo: dict[tuple[str, str], bool] | None
        _proof_offers: dict[str, list[str]]
        _raised: dict[str, Any]
        activated: Any
        area_roles: dict[str, str]
        automations: Automations
        clock: Clock
        db: Database
        experimental: set[str]
        gate: SafetyGate | None
        household: Any
        learning: Learning | None
        learning_allowed: Any
        model: HouseModel
        rules: RuleBook
        skill_params: dict[str, dict[str, Any]]
        skills: SkillEngine | None
        world: World | None

        def autonomous_here(self, skill: str, area_id: str) -> bool: ...
        def domain_cap(self, domain: str) -> Step | None: ...
        def _skill_step(
            self, skill: str, area_id: str | None, entity_id: str | None = None
        ) -> Step: ...
        def _tracked(self, *prefixes: str) -> list[str]: ...
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
        async def _resolve(self, topic: str) -> None: ...
        async def turn_off_automation(
            self, entity_id: str, user_id: str | None, along: bool = False
        ) -> dict[str, Any]: ...

    # -- what holds a rule -----------------------------------------------------------------

    def rule_hold(self, skill: str, area_id: str) -> tuple[bool, list[Reading]]:
        """Whether HouseMind only watches this rule, and which automations hold it: the same
        words as the gate, so what the card says is what happens (E-460). Held with no
        automation: another system steers it (E-68). An automation that can never go keeps only
        its own lamp, not the room, as long as something else here can be HouseMind's (E-461)."""
        go, _, every, free = self._holders(skill, area_id)
        if free:
            return bool(go), sorted(go, key=lambda r: r.automation.name)
        return True, sorted(every, key=lambda r: r.automation.name)

    def lamps_kept(self, skill: str, area_id: str) -> list[Reading]:
        """What keeps a lamp here while the rest of this rule is HouseMind's (E-461)."""
        _, stay, _, free = self._holders(skill, area_id)
        return sorted(stay, key=lambda r: r.automation.name) if free else []

    def _holders(
        self, skill: str, area_id: str
    ) -> tuple[list[Reading], list[Reading], list[Reading], bool]:
        """The automations on this rule's devices. The ones that have to go with the rule, the
        ones that cannot go, all of them; and whether something of the rule can be HouseMind's:
        a device nothing holds, or only what can go."""
        if self.gate is None or self.skills is None:
            return [], [], [], True
        other = self.gate.state.other_system
        if not other:
            return [], [], [], True
        go: dict[str, Reading] = {}
        stay: dict[str, Reading] = {}
        every: dict[str, Reading] = {}
        units = self.skills.units_of(skill, area_id)
        free = not units
        for entity in units:
            members = self.model.groups.get(entity.entity_id, ())
            if not watched(other, entity.entity_id, members):
                free = True
                continue
            devices = {entity.entity_id, *members}
            here = [
                reading
                for reading in self.automations.readings.values()
                if reading.automation.enabled
                and reading.hand is None
                and not reading.signal
                and reading.controls & devices
            ]
            every.update((r.entity_id, r) for r in here)
            kept = [r for r in here if not self.can_go(r)]
            stay.update((r.entity_id, r) for r in kept)
            if here and not kept:
                free = True
                go.update((r.entity_id, r) for r in here)
        return list(go.values()), list(stay.values()), list(every.values()), free

    # -- what an automation that teaches nothing does with its lamps -------------------------

    def rules_of(self, reading: Reading) -> list[tuple[str, str]]:
        """The rules that do what this automation does: the ones it teaches, or the ones of
        its lamps (E-461)."""
        return list(reading.learned) or self.lamp_rules(reading)

    def can_go(self, reading: Reading) -> bool:
        """HouseMind could do what this automation does, once its rules are proven."""
        return bool(self.rules_of(reading))

    def forget_lamp_rules(self) -> None:
        self._lamp_rules = {}

    def lamp_rules(self, reading: Reading) -> list[tuple[str, str]]:
        """It teaches nothing and only switches lamps (E-461): the rules that do the same with
        each of them. Empty when one of its lamps has no such rule: then it stays on."""
        if not reading.lamps_only or self.learning is None or self.skills is None:
            return []
        found = self._lamp_rules.get(reading.entity_id)
        if found is None:
            found = self._lamp_rules[reading.entity_id] = self._find_lamp_rules(reading)
        return found

    def _find_lamp_rules(self, reading: Reading) -> list[tuple[str, str]]:
        assert self.learning is not None and self.skills is not None
        known = set(self.learning.steps) | set(self.learning.first_seen)
        rules: list[tuple[str, str]] = []
        for entity_id in sorted(reading.controls):
            if entity_id in self.model.groups:
                continue  # its lamps are there too
            unit = self.model.unit(entity_id)
            area = self.model.area_of(unit or entity_id)
            if unit is None or area is None:
                return []  # left alone: nothing HouseMind would do
            role = self.area_roles.get(area.id, "other")
            for does in sorted(reading.does):
                fit = [
                    (skill, area.id)
                    for skill, params in self.skill_params.items()
                    if params.get("domain") == "lighting"
                    and does in (params.get("from_automations") or {}).get("does", ())
                    and (skill, area.id) in known
                    and not self.skills._not_here(skill, area.id, role)
                    and any(e.entity_id == unit for e in self.skills.units_of(skill, area.id))
                ]
                if not fit:
                    return []
                rules.extend(rule for rule in fit if rule not in rules)
        return rules

    # -- whether one question may come -----------------------------------------------------

    def may_prove(self, skill: str, area_id: str, now: datetime) -> bool:
        """A question may come about this rule: on Proposing, proven, and nothing that says
        HouseMind may not do it itself here – its part off or limited, a house rule, an
        experimental part, or a device the catalogue only lets it propose (a lock, an alarm)."""
        assert self.learning is not None and self.skills is not None
        if (
            skill not in self.skill_params
            or skill in SAFETY_SKILLS
            or self.learning.asleep(skill)
            or area_id not in self.model.areas
            or self.rules.step_override(skill, area_id) is not None
        ):
            return False
        params = self.skill_params.get(skill, {})
        part = params.get("domain")
        if params.get("experimental") and part not in self.experimental:
            return False
        if self.domain_cap(str(part)) not in (None, Step.AUTONOMOUS):
            return False
        if self.household is not None and not self.household.applies(skill, area_id):
            return False
        if self._skill_step(skill, area_id) is not Step.PROPOSE:
            return False
        if self.blind_here(skill, area_id) or not self._may_do_itself(skill, area_id):
            return False
        # Once per review: the same rule comes by for each automation that teaches it.
        memo = self._proof_memo
        if memo is None:
            return self.learning.proven(skill, area_id, now)
        if (skill, area_id) not in memo:
            memo[(skill, area_id)] = self.learning.proven(skill, area_id, now)
        return memo[(skill, area_id)]

    def blind_here(self, skill: str, area_id: str) -> bool:
        """This rule has to see whether someone is still there, and no sensor here can (E-462)."""
        if not self.skill_params.get(skill, {}).get("needs_sensor") or self.world is None:
            return False
        return not self.world.occupancy.sensed(area_id)

    def _may_do_itself(self, skill: str, area_id: str) -> bool:
        """At least one device of this rule may be Autonomous by the catalogue."""
        assert self.skills is not None
        for entity in self.skills.units_of(skill, area_id):
            kind = self.skills._kind(entity)
            if kind is not None and kind.max_step is Step.AUTONOMOUS:
                return True
        return False

    def _may_ask(self, topic: str, now: datetime) -> bool:
        """Not after "Never"; after "Not yet" only when its week is over."""
        said = self._proof_answers.get(topic)
        if said is None:
            return True
        if said.get("answer") != "later":
            return False
        return now - datetime.fromisoformat(said["at"]) >= PROOF_ASK_AGAIN

    def offered_automations(self) -> set[str]:
        """The automations a question about them is open for."""
        return {e for ids in self._proof_offers.values() for e in ids}

    def proof_said(self, topic: str, now: datetime) -> str | None:
        """What someone answered that still stands: "later" or "never"."""
        if self._may_ask(topic, now):
            return None
        return str(self._proof_answers[topic]["answer"])

    def _can_offer(self, reading: Reading, now: datetime, ours: set[str]) -> bool:
        """This automation can go with a yes: a button is no other system, and it has to teach
        something HouseMind then does; every rule of it proven or on Autonomous already."""
        status = self.automations.status.get(reading.entity_id, AutomationStatus.SEEN)
        if status is AutomationStatus.OFFERED:
            if reading.entity_id not in ours:
                return False  # the question from when it was all on Autonomous is still open
        elif status is not AutomationStatus.SEEN:
            return False
        rules = self.rules_of(reading)
        if reading.hand is not None or not reading.automation.enabled or not rules:
            return False
        if not self._may_ask(f"{AUTOMATION}{reading.entity_id}", now):
            return False
        if any(self.domain_cap(d) not in (None, Step.AUTONOMOUS) for d in reading.domains):
            return False
        return all(self.autonomous_here(*p) or self.may_prove(*p, now) for p in rules)

    def linked_automations(self, reading: Reading) -> list[Reading] | None:
        """This automation and every automation holding one of its rules, and theirs: what has
        to go together, or something stays undone. None: another system holds one of them."""
        found: dict[str, Reading] = {}
        waiting = [reading]
        while waiting:
            one = waiting.pop()
            if one.entity_id in found:
                continue
            found[one.entity_id] = one
            for skill, area_id in self.rules_of(one):
                held, holders = self.rule_hold(skill, area_id)
                if held and not holders:
                    return None
                waiting.extend(holders)
        return sorted(found.values(), key=lambda r: r.automation.name)

    def take_over_bundle(self, reading: Reading, now: datetime) -> list[Reading] | None:
        """The automations one question is about, or None when no question may come yet."""
        ours = self.offered_automations()
        bundle = self.linked_automations(reading)
        if bundle is None or not all(self._can_offer(r, now, ours) for r in bundle):
            return None
        if all(r.learned for r in bundle) and all(
            self.autonomous_here(*p) for r in bundle for p in r.learned
        ):
            return None  # all on Autonomous already: the question of E-42 asks that
        return bundle

    def goes_along(self, reading: Reading) -> list[tuple[str, str]]:
        """What an automation does without teaching it, under the same lamps (``goes_with``):
        the night light of the room. Left behind, no lamp goes on at night any more."""
        assert self.skills is not None
        out = []
        for skill, area_id in self.rules_of(reading):
            role = self.area_roles.get(area_id, "other")
            for other, params in self.skill_params.items():
                within = params.get("only_in") or params.get("in") or ()
                if (
                    params.get("goes_with") == skill
                    and (not within or role in within)
                    and self.skills.units_of(other, area_id)
                    and not self.autonomous_here(other, area_id)
                ):
                    out.append((other, area_id))
        return out

    # -- the words -------------------------------------------------------------------------

    def _rule_words(self, skill: str, area_id: str) -> dict[str, Any]:
        area = self.model.areas.get(area_id)
        return {
            "t": "skills.in_room",
            "p": {"skill": {"t": f"skills.{skill}"}, "area": area.name if area else ""},
        }

    def _rule_params(self, skill: str, area_id: str, now: datetime) -> dict[str, Any]:
        assert self.learning is not None
        score, _, days = self.learning.score(skill, area_id, now)
        return {
            "what": self._rule_words(skill, area_id),
            "days": int(days),
            "pct": round(score * 100),
        }

    def _bundle_params(self, bundle: list[Reading], now: datetime) -> dict[str, Any]:
        assert self.learning is not None
        pairs: list[tuple[str, str]] = []
        for reading in bundle:
            for pair in [*self.rules_of(reading), *self.goes_along(reading)]:
                if pair not in pairs:
                    pairs.append(pair)
        own = [
            self.learning.score(s, a, now)[2]
            for r in bundle
            for s, a in self.rules_of(r)
            if not self.autonomous_here(s, a)
        ]
        stops: list[Any] = []
        for reading in bundle:
            if reading.lamps_only:
                continue  # its lamps are in the rules: it is named, and nothing else stops
            stops.extend(name for name in reading.stops if name not in stops)
            if reading.stops_other:
                stops.append(
                    {
                        "t": "ui.ready.steer.other",
                        "p": {"count": reading.stops_other},
                    }
                )
            if not reading.automation.readable:
                stops.append({"t": "ui.ready.steer.unreadable"})
        return {
            "count": len(bundle),
            "what": {"list": [self._rule_words(s, a) for s, a in pairs]},
            "names": {"list": [f"‘{r.automation.name}’" for r in bundle]},
            "days": int(min(own)) if own else 0,
            "stops": {"t": "notify.proof_stops", "p": {"what": {"list": stops}}} if stops else "",
        }

    # -- every quarter of an hour ----------------------------------------------------------

    async def _review_proof(self, snapshot: WorldState) -> None:
        """Ask once what is proven; a question that no longer holds goes."""
        assert self.learning is not None
        if not self.learning_allowed or not self.activated:
            return
        if await self.db.get_setting("given_back", False):
            return
        now = snapshot.now
        self._proof_memo = {}
        try:
            changed = await self._review_proof_now(snapshot, now)
        finally:
            self._proof_memo = None
        if changed:
            await self._notify()

    async def _review_proof_now(self, snapshot: WorldState, now: datetime) -> bool:
        assert self.learning is not None
        changed = await self._review_automation_offers(snapshot, now)
        wanted: set[str] = set()
        known = set(self.learning.steps) | set(self.learning.first_seen)
        for skill, area_id in sorted(known):
            if not self.may_prove(skill, area_id, now) or not self._hm.rule_there(skill, area_id):
                continue  # also not about a rule whose devices went (E-487)
            held, _ = self.rule_hold(skill, area_id)
            topic = f"{READY}{skill}@{area_id}"
            if held or not self._may_ask(topic, now):
                continue  # an automation's question, or after "Not yet" or "Never"
            wanted.add(topic)
            changed = changed or topic not in self._raised
            await self._raise(
                Kind.LEARNING,
                topic,
                "notify.proof_rule",
                self._rule_params(skill, area_id, now),
                snapshot,
                actions=PROOF_ACTIONS,
            )
        for topic in self._tracked(READY):
            if topic not in wanted:
                await self._resolve(topic)
                changed = True
        return changed

    async def _review_automation_offers(self, snapshot: WorldState, now: datetime) -> bool:
        changed = False
        offers: dict[str, list[Reading]] = {}
        covered: set[str] = set()
        for reading in sorted(self.automations.readings.values(), key=lambda r: r.automation.name):
            if reading.entity_id in covered:
                continue
            bundle = self.take_over_bundle(reading, now)
            if bundle is not None:
                offers[bundle[0].entity_id] = bundle
                covered |= {r.entity_id for r in bundle}
        for head, ids in list(self._proof_offers.items()):
            if head in offers and [r.entity_id for r in offers[head]] == ids:
                continue
            for entity_id in ids:  # no longer so, or another set: it may be asked anew
                if self.automations.status.get(entity_id) is AutomationStatus.OFFERED:
                    await self.automations.set_status(entity_id, AutomationStatus.SEEN, now, None)
            await self._resolve(f"{AUTOMATION}{head}")
            del self._proof_offers[head]
            changed = True
        for head, bundle in offers.items():
            ids = [r.entity_id for r in bundle]
            if head not in self._proof_offers:
                self._proof_offers[head] = ids
                changed = True
                for entity_id in ids:
                    await self.automations.set_status(
                        entity_id, AutomationStatus.OFFERED, now, None
                    )
            await self._raise(
                Kind.LEARNING,
                f"{AUTOMATION}{head}",
                "notify.proof_automation",
                self._bundle_params(bundle, now),
                snapshot,
                concerns="admins",
                actions=PROOF_ACTIONS,
            )
        if changed:
            await self.db.set_setting("proof_offers", self._proof_offers)
        return changed

    # -- the answers -----------------------------------------------------------------------

    async def _remember(self, topic: str, answer: str | None, now: datetime) -> None:
        if answer is None:
            self._proof_answers.pop(topic, None)
        else:
            self._proof_answers[topic] = {"answer": answer, "at": now.isoformat()}
        await self.db.set_setting("proof_answers", self._proof_answers)

    async def answer_ready(self, topic: str, action: str, user_id: str | None) -> dict[str, Any]:
        """The answer to the question about one rule. The first answer counts (E-81)."""
        assert self.learning is not None
        if topic not in self._raised or action not in ("yes", "later", "no"):
            return {"ok": False, "reason": "already_answered"}
        skill, _, area_id = topic[len(READY) :].partition("@")
        now = self.clock.now()
        await self._resolve(topic)
        if action == "yes":
            await self._remember(topic, None, now)
            await self.learning.grant(skill, area_id, now, "proven_yes")
        else:
            await self._remember(topic, "later" if action == "later" else "never", now)
        await self._notify()
        return {"ok": True}

    async def answer_offer(
        self, head: str, ids: list[str], action: str, user_id: str | None
    ) -> dict[str, Any]:
        """An administrator's answer to the question about automations: yes puts all their
        rules on Autonomous and them off, each through the gate; not yet comes back in a week;
        never leaves them on and is not asked again."""
        assert self.learning is not None
        now = self.clock.now()
        self._proof_offers.pop(head, None)
        await self.db.set_setting("proof_offers", self._proof_offers)
        await self._resolve(f"{AUTOMATION}{head}")
        if action == "yes":
            return await self._take_over(ids, user_id, now)
        for entity_id in ids:
            if action == "later":
                await self._remember(f"{AUTOMATION}{entity_id}", "later", now)
                status = AutomationStatus.SEEN
            else:
                status = AutomationStatus.DECLINED
            await self.automations.set_status(entity_id, status, now, user_id)
        await self._notify()
        return {"ok": True}

    async def _take_over(
        self, ids: list[str], user_id: str | None, now: datetime
    ) -> dict[str, Any]:
        assert self.learning is not None
        readings = [self.automations.readings[e] for e in ids if e in self.automations.readings]
        for reading in readings:
            await self._remember(f"{AUTOMATION}{reading.entity_id}", None, now)
            for skill, area_id in [*self.rules_of(reading), *self.goes_along(reading)]:
                if not self.autonomous_here(skill, area_id):
                    await self.learning.grant(skill, area_id, now, "proven_yes")
        ok = True
        for reading in readings:
            if self.automations.status.get(reading.entity_id) is AutomationStatus.TURNED_OFF:
                continue  # it went along with one on the same devices (E-538)
            # Asked again later if it does not go off (the gate decides).
            await self.automations.set_status(
                reading.entity_id, AutomationStatus.SEEN, now, user_id
            )
            result = await self.turn_off_automation(reading.entity_id, user_id, along=True)
            ok = ok and bool(result["ok"])
        await self._notify()
        return {"ok": ok}

    async def forget_proof(self, skill: str, area_id: str) -> None:
        """'Vergeet dit' (E-94): what someone said about this rule goes with it."""
        topic = f"{READY}{skill}@{area_id}"
        if topic in self._proof_answers:
            del self._proof_answers[topic]
            await self.db.set_setting("proof_answers", self._proof_answers)
        if topic in self._raised:
            await self._resolve(topic)

    async def forget_all_proof(self) -> None:
        """Learning again (E-273): nothing someone said about what was learned stays."""
        self._proof_answers = {}
        self._proof_offers = {}
        await self.db.set_setting("proof_answers", {})
        await self.db.set_setting("proof_offers", {})
        for topic in self._tracked(READY):
            await self._resolve(topic)
