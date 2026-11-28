"""How far HouseMind is (E-256, B-100): "Klaar voor autonomie?".

Per part (lighting, covers, climate …), per skill × room and per existing automation: the step,
the target and the counters towards the next step, and a plain sentence – computed with the
very thresholds learning uses (E-56, E-57, E-160), so what the card shows is what HouseMind
does. The card only shows: steps are earned, and an administrator keeps the limit.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import TYPE_CHECKING, Any

from housemind.brain.automations import Reading
from housemind.brain.automations import Status as AutomationStatus
from housemind.brain.filters import causes_of
from housemind.brain.house_knowledge import REJECTED, SUGGESTED
from housemind.brain.learning import (
    EARN_AGAIN_DAYS,
    EPS,
    PUT_BACK_SHARE,
    SETBACKS_FOR_STEP,
    START_PROPOSE_AFTER,
    START_PROPOSE_SCORE,
    Result,
    Thresholds,
)
from housemind.brain.skills import PART_DEVICES
from housemind.brain.when import Seen, When
from housemind.core.model import WHOLE_HOUSE
from housemind.energy.manager import BASELINE
from housemind.knowledge.catalog import DeviceKind, Step
from housemind.knowledge.labels import category_names, label_names
from housemind.knowledge.matcher import recognise

if TYPE_CHECKING:
    from housemind.runtime import HouseMind

# The order of the parts, as under "Hoe ver HouseMind mag gaan"; the rest after them.
ORDER = (
    "lighting",
    "covers",
    "climate",
    "air",
    "energy",
    "water",
    "media",
    "household",
    "cleaning",
    "garden",
)
# Parts an administrator can limit, and the Home Assistant domain that has their devices.
DEVICES = PART_DEVICES
STEPS = {Step.OBSERVE: "observe", Step.PROPOSE: "propose", Step.AUTONOMOUS: "autonomous"}
BASELINE_DAYS = BASELINE.days  # Energie: the first two weeks HouseMind only measures (S-03)


def _share(value: float, target: float) -> float:
    """How far towards a target; a target of 0 is reached at once (E-297 allows it)."""
    return 1.0 if target <= 0 else min(value / target, 1.0)


def history_line(hm: HouseMind, lang: str, entity_id: str) -> str | None:
    """What the history says about an automation (E-257): how often it acted, was put back,
    and how well HouseMind copies it against the 85% of a takeover."""
    stats = hm.history_automations.get(entity_id)
    if stats is None or not stats.acted:
        return None
    return hm.languages.text(
        lang,
        "ui.automations.history",
        days=max(1, round(hm.history_status.days)),
        acted=stats.acted,
        undone=stats.undone,
        score=round(stats.matched * 100 / stats.acted),
    )


def what(hm: HouseMind, lang: str, skill: str, area_id: str) -> str:
    """ "licht bij binnenkomen in de keuken": a skill in a room, in plain words."""
    if area_id == WHOLE_HOUSE:  # an energy device without a room (E-343)
        return hm.languages.text(lang, f"skills.{skill}")
    where = hm.names.where(area_id, lang) if hm.names else ""
    if not where:
        area = hm.model.areas.get(area_id)
        where = area.name if area else area_id
    return f"{hm.languages.text(lang, f'skills.{skill}')} {where}".strip()


def _and(hm: HouseMind, lang: str, items: list[str]) -> str:
    if len(items) < 2:
        return "".join(items)
    return hm.languages.text(
        lang, "ui.ready.steer.and", first=", ".join(items[:-1]), last=items[-1]
    )


def steer_view(hm: HouseMind, lang: str, skill: str, area_id: str, admin: bool) -> dict[str, Any]:
    """The automation in Home Assistant that steers this row, and – for an administrator – the
    question about it (E-317): switch it off on Autonomous, back on after a step back."""
    t = hm.languages.text
    out: dict[str, Any] = {}
    # What really holds it, as the gate says: an automation on one lamp of a bigger group does
    # not (E-460); and one that holds its devices without teaching this rule does.
    _, holding = hm.rule_hold(skill, area_id)
    steering = hm.steering(skill, area_id)
    if holding:
        names = [f"‘{r.automation.name}’" for r in holding]
        out["steer"] = {
            "ids": [r.entity_id for r in holding],
            "text": t(lang, "ui.ready.steer.on_many", names=_and(hm, lang, names))
            if len(names) > 1
            else t(lang, "ui.ready.steer.on", name=holding[0].automation.name),
        }
    elif steering and not steering[0].automation.enabled:
        out["steer"] = {
            "ids": [r.entity_id for r in steering],
            "text": t(lang, "ui.ready.steer.taken", name=steering[0].automation.name),
        }
    else:
        kept = hm.lamps_kept(skill, area_id)  # a lamp stays with it, the rest is HouseMind's
        if kept:
            names = [f"‘{r.automation.name}’" for r in kept]
            out["steer"] = {
                "ids": [r.entity_id for r in kept],
                "text": t(lang, "ui.ready.steer.partly", names=_and(hm, lang, names)),
            }
    question = hm.automation_question(skill, area_id) if admin else None
    if question is None:
        return out
    name = str(question["name"])
    if question["kind"] == "off":
        lines = [t(lang, "ui.ready.steer.ask_off", name=name)]
        along = [what(hm, lang, s, a) for s, a in question["along"]]
        if along:
            lines.append(t(lang, "ui.ready.steer.along", what=_and(hm, lang, along)))
        stops = list(question["stops"])
        if question["other"]:
            stops.append(t(lang, "ui.ready.steer.other", count=int(question["other"])))
        if not question["readable"]:
            stops.append(t(lang, "ui.ready.steer.unreadable"))
        if stops:
            lines.append(t(lang, "ui.ready.steer.stops", what=_and(hm, lang, stops)))
        names = [f"‘{n}’" for n in question.get("with") or []]
        if names:  # on the same devices: off with it (E-538)
            lines.append(t(lang, "ui.ready.steer.with", names=_and(hm, lang, names)))
        held = [f"‘{n}’" for n in question.get("held") or []]
        if held:  # one that can never go keeps them all on (E-538): no question, a line
            said = t(lang, "ui.ready.steer.held", names=_and(hm, lang, held), name=name)
            steer = out.setdefault("steer", {"ids": [str(question["id"])], "text": ""})
            steer["text"] = f"{steer['text']} {said}".strip()
            return out
        button = t(lang, "ui.ready.steer.off_button", name=name)
    else:
        lines = [t(lang, "ui.ready.steer.ask_on", name=name)]
        button = t(lang, "ui.ready.steer.on_button", name=name)
    out["question"] = {
        "kind": question["kind"],
        "id": question["id"],
        "text": " ".join(lines),
        "declined": bool(question["declined"]),
        "button": button,
    }
    return out


def automation_state(hm: HouseMind, reading: Reading, given_back: bool) -> str:
    """Where an existing automation stands (E-41, E-42, E-112) – one word, as in the list."""
    status = hm.automations.status.get(reading.entity_id, AutomationStatus.SEEN)
    on = reading.automation.enabled
    if status is AutomationStatus.TURNED_OFF:
        return "still_off" if given_back else "taken_over"
    if not on:
        return "off"
    if not reading.automation.readable:
        return "unreadable"
    if reading.hand is not None:
        return "hand"  # a button or switch: the residents' own hand (E-448)
    if reading.signal:
        return "signal"  # a sign with a lamp: holds nothing (E-461)
    if status is AutomationStatus.OFFERED and not hm.ready_to_take_over(reading):
        return "asked"  # proven: the question is in Wacht op jou (E-460)
    if hm.lamp_rules(reading):
        return "along"  # goes off with the rules of its lamps (E-461)
    if not reading.whole:
        return "stays"
    if status is AutomationStatus.DECLINED:
        return "kept"
    return "ready" if hm.ready_to_take_over(reading) else "learning"


class Readiness:
    """The card's content for one viewer: language, level and whether they are an admin."""

    def __init__(self, hm: HouseMind, lang: str, level: int, admin: bool) -> None:
        assert hm.learning is not None
        self.hm = hm
        self.lang = lang
        self.level = level
        self.admin = admin
        self.now: datetime = hm.clock.now()
        self.t = hm.languages.text
        self._by_room: dict[str, list[tuple[str, DeviceKind]]] | None = None
        # Once per card (S4): what automations teach, and the step choices per limit – the
        # card has a row for every rule a room has the devices for (E-485).
        self._taught: set[tuple[str, str]] | None = None
        self._choices: dict[tuple[Step, Step | None], list[dict[str, Any]]] = {}
        self._step_names: dict[Step, str] = {}
        self.corrections: dict[str, list[int]] = {}  # per part, 4 weeks (S-01)
        # "Wanneer doe ik wat" (E-385): per row one sentence, from Betrokken; made once per card.
        self.when = When(hm, lang) if level >= 3 else None

    # -- words -----------------------------------------------------------------------------

    def _box_question(self, area_id: str) -> bool:
        """Whether this room waits for the question 'a small room with one door?' (E-504)."""
        found = self.hm.knowledge.get("area", area_id, "one_door")
        return found is not None and found.status == SUGGESTED and self.hm.still_open(found)

    def _step_name(self, step: Step) -> str:
        if step not in self._step_names:
            self._step_names[step] = self.t(self.lang, f"steps.{STEPS[step]}")
        return self._step_names[step]

    def _taught_pairs(self) -> set[tuple[str, str]]:
        if self._taught is None:
            self._taught = {p for r in self.hm.automations.readings.values() for p in r.learned}
        return self._taught

    def _what(self, skill: str, area_id: str) -> str:
        if self.when is None or area_id == WHOLE_HOUSE:
            return what(self.hm, self.lang, skill, area_id)
        # The same words, with the room's kept (S4: looking it up was most of the card).
        return f"{self.t(self.lang, f'skills.{skill}')} {self.when.where(area_id)}".strip()

    def _room(self, area_id: str) -> str:
        if area_id == WHOLE_HOUSE:
            return self.t(self.lang, "ui.ready.whole_house")
        area = self.hm.model.areas.get(area_id)
        return area.name if area else area_id

    def _needs(self, parts: list[tuple[str, float]]) -> str:
        """ "4 situaties en 2 dagen": what is still missing, rounded up."""
        said = [
            self.t(self.lang, f"ui.ready.n.{key}", count=math.ceil(left - EPS))
            for key, left in parts
            if left > EPS
        ]
        if len(said) > 1:
            return f"{', '.join(said[:-1])} {self.t(self.lang, 'ui.ready.and')} {said[-1]}"
        return said[0] if said else ""

    # -- one skill in one room -------------------------------------------------------------

    def pair(self, domain: str, skill: str, area_id: str, cap: Step | None) -> dict[str, Any]:
        hm, learning, now, t, lang = self.hm, self.hm.learning, self.now, self.t, self.lang
        assert learning is not None
        learned = hm._skill_step(skill, area_id)
        earned = learning.step_at(skill, area_id, now)
        step = min(learned, cap) if cap is not None else learned
        # An automation or another system steers it: HouseMind asks nothing there, so the card
        # does not call that Voorstellen (E-460).
        held, holding = hm.rule_hold(skill, area_id)
        blind = hm.blind_here(skill, area_id)  # no sensor to see whether someone is there (E-462)
        watching = (held and step is Step.PROPOSE) or (blind and step is not Step.OBSERVE)
        limits = learning.thresholds(skill)  # the part's own thresholds (E-297)
        scored = learning.score(skill, area_id, now)
        score, situations, _ = scored
        given = learning.approval(skill, area_id, now)
        # After a step back only what came since counts, and at least a week (20.9).
        n, answers, days = learning.earned(skill, area_id, now, scored, given)
        again = learning.earn.get((skill, area_id))
        approved, rejected, _ = given
        yes = approved / (approved + rejected) if approved + rejected else 0.0
        setbacks = len(learning.recent_setbacks(skill, area_id, now))
        params = hm.skill_params.get(skill, {})
        start = skill in learning.start_knowledge
        # Not seen yet (E-485): the room has the device, the situation never came, and no
        # automation taught it.
        unseen = (
            (skill, area_id) not in learning.first_seen
            and (skill, area_id) not in learning.steps
            and (skill, area_id) not in self._taught_pairs()
        )
        out: dict[str, Any] = {
            "skill": skill,
            "area": area_id,
            "what": self._what(skill, area_id),
            # Apart, for the list with filter and grouping (E-314).
            "skillName": t(lang, f"skills.{skill}"),
            "room": self._room(area_id),
            "step": "observe" if watching else STEPS[step],
            "stepName": t(lang, "ui.ready.watching") if watching else self._step_name(step),
            "heldBack": cap is not None and learned > cap,
            # How often it went well, whatever the step (E-460); None: nothing to go by yet.
            "good": round(score * 100) if learning.judged(skill, area_id) else None,
        }
        if watching:
            out["watching"] = True
        # Start knowledge (E-160) is proposed from two hours after installation, at the first
        # situation – unless people often did otherwise there: then from 40% again, not from
        # the thresholds of a habit HouseMind learns all by itself.
        early = start and now - learning.installed_at < START_PROPOSE_AFTER
        wait_propose = max(limits.propose_days, EARN_AGAIN_DAYS) if again else limits.propose_days
        wait_auto = (
            max(limits.autonomous_days, EARN_AGAIN_DAYS) if again else limits.autonomous_days
        )
        if step is Step.OBSERVE and start and not out["heldBack"] and again is None:
            out["bar"] = {"value": round(score * 100), "target": round(START_PROPOSE_SCORE * 100)}
            out["counters"] = [self._counter("situations", n, 1)]  # the first one (L7)
            ready = score >= START_PROPOSE_SCORE
            progress = _share(score, START_PROPOSE_SCORE)
        elif step is Step.OBSERVE:
            out["bar"] = {
                "value": round(score * 100),
                "target": round(limits.propose_score * 100),
            }
            out["counters"] = [
                self._counter("situations", n, limits.propose_situations),
                self._counter("days", days, wait_propose),
            ]
            ready = (
                score >= limits.propose_score
                and n >= limits.propose_situations - EPS
                and days >= wait_propose
            )
            progress = min(
                _share(score, limits.propose_score),
                _share(n, limits.propose_situations),
                _share(days, wait_propose),
            )
        elif step is Step.PROPOSE and skill not in learning.answers_only:
            # Proof (E-460): how often it went well, not the share of yes. The way through
            # answers stays, so its count shows when there are any.
            wait_proof = learning.proof_wait(skill, area_id)
            out["bar"] = {
                "value": round(score * 100),
                "target": round(limits.propose_score * 100),
            }
            out["counters"] = [
                self._counter("situations", n, limits.propose_situations),
                self._counter("days", days, wait_proof),
            ]
            if answers > EPS:
                out["counters"].append(self._counter("answers", answers, limits.autonomous_answers))
            ready = not blind and learning.proof_holds(skill, area_id, score, n, days, now)
            out["proven"] = ready
            progress = min(
                _share(score, limits.propose_score),
                _share(n, limits.propose_situations),
                _share(days, wait_proof),
            )
        elif step is Step.PROPOSE:
            out["bar"] = {
                "value": round(yes * 100),
                "target": round(limits.autonomous_score * 100),
            }
            out["counters"] = [
                self._counter("answers", answers, limits.autonomous_answers),
                self._counter("days", days, wait_auto),
            ]
            ready = (
                answers >= limits.autonomous_answers - EPS
                and yes >= limits.autonomous_score
                and days >= wait_auto
            )
            progress = min(
                _share(yes, limits.autonomous_score),
                _share(answers, limits.autonomous_answers),
                _share(days, wait_auto),
            )
        else:
            out["bar"] = {
                "value": round(min(setbacks, SETBACKS_FOR_STEP) * 100 / SETBACKS_FOR_STEP),
                "target": 100,
                "against": True,  # filling up is bad: at two in a week, a step back (20.9)
            }
            out["counters"] = [self._counter("corrections", setbacks, SETBACKS_FOR_STEP)]
            ready, progress = False, 1.0
        out["ready"] = ready
        out["progress"] = round(progress, 3)
        # What came from the history (E-257): hatched on the card.
        share = learning.history_share(skill, area_id, now)
        if share and not out["bar"].get("against"):
            out["bar"]["history"] = round(out["bar"]["value"] * share)
        past = learning.history_situations(skill, area_id)
        for counter in out["counters"]:
            if counter["key"] == "situations" and past and again is None:
                counter["history"] = math.floor(past + EPS)
        lift = hm._admin_lift(skill, area_id)
        if unseen and n < EPS:
            out["unseen"] = True  # nothing learned yet: no habit (E-485)
        world = hm.world
        if (
            params.get("needs_sensor")
            and world is not None
            and area_id in world.occupancy.boxes
            and not world.occupancy.sees_people(area_id)
        ):
            out["byDoor"] = t(lang, "ui.ready.proof.by_door")  # a small room with one door (E-505)
        if blind:
            # Your own step stays saved: it says so, and what it waits for (E-462) – in a
            # toilet or bathroom with only its door, the question that would help (E-504).
            box = "_box" if self._box_question(area_id) else ""
            if lift is not None and lift > Step.OBSERVE:
                text = t(lang, f"ui.ready.proof.no_sensor{box}_own", step=self._step_name(lift))
            else:
                text = t(lang, f"ui.ready.proof.no_sensor{box}")
        elif out["heldBack"]:
            text = t(
                lang,
                "ui.ready.held",
                step=self._step_name(learned),
                part=t(lang, f"ui.domain.{domain}"),
                cap=self._step_name(cap or Step.OBSERVE),
            )
        elif hm.rules.step_override(skill, area_id) is not None:
            text = t(lang, "ui.ready.rule")
        elif params.get("experimental") and params.get("domain") not in hm.experimental:
            text = t(lang, "ui.ready.experimental")
        elif lift is not None and lift > earned:
            text = t(lang, "ui.ready.own.text", step=self._step_name(lift))
        elif (
            again is not None
            and earned is again.back_to
            and step is not Step.AUTONOMOUS
            and not out.get("proven")
        ):
            proof = step is Step.PROPOSE and skill not in learning.answers_only
            situations_first = step is Step.OBSERVE or proof
            key = "again_observe" if situations_first else "again_propose"
            text = t(
                lang,
                f"ui.ready.{key}",
                count=math.floor((n if situations_first else answers) + EPS),
                target=round(
                    limits.propose_situations if situations_first else limits.autonomous_answers
                ),
                days=math.floor(days + EPS),
                target_days=round(wait_propose if situations_first else wait_auto),
            )
        elif out.get("unseen") and step is not Step.AUTONOMOUS:
            # Not seen yet (E-485); start knowledge asks the first time (E-160, E-488).
            text = t(lang, "ui.ready.not_seen" if step is Step.OBSERVE else "ui.ready.not_seen_ask")
        elif step is Step.OBSERVE and early:
            text = t(lang, "ui.ready.start_soon")
        elif step is Step.OBSERVE and start:
            text = t(lang, "ui.ready.start_doubt", score=round(score * 100))
        elif step is Step.OBSERVE:
            text = self._observe_text(score, n, days, ready, limits)
        elif step is Step.PROPOSE and (watching or skill not in learning.answers_only):
            text = self._proof_text(
                skill, area_id, score, n, days, held, bool(out.get("proven")), holding
            )
        elif step is Step.PROPOSE:
            text = self._propose_text(yes, answers, days, ready, start and answers < EPS, limits)
        else:
            text = (
                t(lang, "ui.ready.autonomous_corrected", count=setbacks)
                if setbacks
                else t(lang, "ui.ready.autonomous")
            )
        out["text"] = text
        hits = other = corrections = put_back = 0.0
        if self.level >= 4 and not out.get("unseen"):  # nothing counted yet: all naught
            hits, other, corrections, put_back = (
                learning.total(skill, area_id, r, now)
                for r in (Result.HIT, Result.MISS, Result.CORRECTION, Result.PUT_BACK)
            )
        if self.when is not None:  # when it does it, in its own words per step (E-385 – E-387)
            seen = Seen(situations, hits, other + corrections, approved, rejected)
            shown_step = Step.OBSERVE if watching else step
            out.update(self.when.row(skill, area_id, shown_step, self.level, seen))
        # The automation that steers it, and the question about it (E-317).
        out.update(steer_view(hm, lang, skill, area_id, self.admin))
        if self.admin and self.level >= 4:
            out["own"] = self._own(f"{skill}@{area_id}", domain, {skill}, area_id)
        if self.admin and self.level >= 3:
            out["forget"] = True  # "Vergeet dit" (E-94, 20.13)
        if self.level >= 4:
            first = learning.first_seen.get((skill, area_id))
            out["numbers"] = {
                "score": round(score * 100),
                "confidence": learning.confidence(skill, area_id, now),
                "hits": round(hits, 1),
                "misses": round(other, 1),
                "corrections": round(corrections + put_back, 1),
                "since": first.date().isoformat() if first else None,
            }
        return out

    def _own(self, key: str, domain: str, skills: set[str], area_id: str | None) -> dict[str, Any]:
        """'Zelf een stap zetten' (20.10): the three steps, with what may not and why – the
        catalogue's limit for these devices and the administrator's own limit."""
        hm, t, lang = self.hm, self.t, self.lang
        most = Step.OBSERVE
        by_room = self._kinds_by_room()
        rooms = [by_room.get(area_id, [])] if area_id is not None else list(by_room.values())
        devices = [device for room in rooms for device in room]
        ha = DEVICES.get(domain)
        for entity_domain, kind in devices:
            if set(kind.skills) & skills or (ha is not None and entity_domain == ha):
                most = max(most, kind.max_step)
        cap = hm.domain_cap(domain)
        found = hm.admin_steps.get(key)
        kept = self._choices.get((most, cap))
        if kept is not None:
            return {"key": key, "step": STEPS[found[0]] if found else None, "choices": kept}
        choices = []
        for step in (Step.OBSERVE, Step.PROPOSE, Step.AUTONOMOUS):
            why = None
            if step > most:
                why = t(lang, "ui.ready.own.never")
            elif cap is not None and step > cap:
                why = t(lang, "ui.ready.own.capped", cap=self._step_name(cap))
            choices.append(
                {
                    "step": STEPS[step],
                    "name": self._step_name(step),
                    "allowed": why is None,
                    "why": why,
                }
            )
        self._choices[(most, cap)] = choices
        return {"key": key, "step": STEPS[found[0]] if found else None, "choices": choices}

    def _kinds_by_room(self) -> dict[str, list[tuple[str, DeviceKind]]]:
        """Each room's devices with their kind, once per card (S4), not for every skill in every
        room: going through every device of the house takes most of a screen update."""
        if self._by_room is None:
            hm = self.hm
            assert hm.gate is not None
            kept = hm.skills.kinds_by_room if hm.skills is not None else None
            if kept is not None:  # until the house changes (SkillEngine.forget)
                self._by_room = kept
                return kept
            self._by_room = {}
            for entity in hm.model.entities.values():
                role = hm.gate.state.roles.get(entity.entity_id)
                kind = recognise(hm.catalog, entity, role).kind
                if kind is not None:
                    area = hm.model.area_of(entity.entity_id)
                    self._by_room.setdefault(area.id if area else "", []).append(
                        (entity.domain, kind)
                    )
            if hm.skills is not None:
                hm.skills.kinds_by_room = self._by_room
        return self._by_room

    def _counter(self, key: str, value: float, target: float) -> dict[str, Any]:
        """One counter on the card: "96 situaties gezien · 20 nodig" with a check when it is
        enough, instead of "96/20" that reads like a fraction (E-313). The
        corrections count against: "1 keer terug of nee deze week · bij 2 een stap terug"."""
        shown = math.floor(value + EPS)
        goal = round(target)
        if key == "corrections":
            said = str(shown)
        else:
            said = self.t(self.lang, f"ui.ready.n.{key}", count=shown)
        return {
            "key": key,
            "label": self.t(self.lang, f"ui.ready.counter.{key}"),
            "value": shown,
            "target": goal,
            "text": self.t(self.lang, f"ui.ready.count.{key}", n=said, target=goal),
            "met": key != "corrections" and value >= target - EPS,
        }

    def _observe_text(
        self, score: float, n: float, days: float, ready: bool, limits: Thresholds
    ) -> str:
        t, lang = self.t, self.lang
        if ready:
            return t(lang, "ui.ready.observe_ready")
        if n < EPS:
            return t(lang, "ui.ready.nothing")
        needs = self._needs(
            [("situations", limits.propose_situations - n), ("days", limits.propose_days - days)]
        )
        low = n >= 5 - EPS and score < limits.propose_score
        pct, target = round(score * 100), round(limits.propose_score * 100)
        if needs and low:
            return t(lang, "ui.ready.observe_needs_low", needs=needs, score=pct, target=target)
        if needs:
            return t(lang, "ui.ready.observe_needs", needs=needs)
        return t(lang, "ui.ready.observe_low", score=pct, target=target)

    def _proof_text(
        self,
        skill: str,
        area_id: str,
        score: float,
        n: float,
        days: float,
        held: bool,
        proven: bool,
        holding: list[Reading],
    ) -> str:
        """What is still needed for the one question (E-460), or where it stands. Held by an
        automation: the same, and whether HouseMind can take that over at all."""
        hm, t, lang, now = self.hm, self.t, self.lang, self.now
        assert hm.learning is not None
        limits = hm.learning.thresholds(skill)
        if held and not holding:
            return t(lang, "ui.ready.proof.other_system")
        # What learns from answers gets none while it is only watched, so it never proves itself.
        if skill in hm.learning.answers_only or any(not hm.can_go(r) for r in holding):
            return t(lang, "ui.ready.proof.cannot")
        topic = f"automation:{holding[0].entity_id}" if holding else f"ready:{skill}@{area_id}"
        said = hm.proof_said(topic, now)
        if said == "never" and not holding:
            return t(lang, "ui.ready.proof.never")
        if said == "later":
            return t(lang, "ui.ready.proof.later")
        if proven and hm.may_prove(skill, area_id, now):
            if not holding or any(r.entity_id in hm.offered_automations() for r in holding):
                return t(lang, "ui.ready.proof.asked")
            return t(lang, "ui.ready.proof.waits")
        if proven:
            return t(lang, "ui.ready.propose_ready")
        share = hm.learning.put_back_share(skill, area_id, now)
        if share > PUT_BACK_SHARE:
            return t(lang, "ui.ready.proof.put_back", pct=round(share * 100))
        pct, target = round(score * 100), round(limits.propose_score * 100)
        needs = self._needs(
            [
                ("situations", limits.propose_situations - n),
                ("days", hm.learning.proof_wait(skill, area_id) - days),
            ]
        )
        if needs and n >= 5 - EPS and score < limits.propose_score:
            return t(lang, "ui.ready.proof.needs_low", needs=needs, score=pct, target=target)
        if needs:
            return t(lang, "ui.ready.proof.needs", needs=needs)
        return t(lang, "ui.ready.proof.low", score=pct, target=target)

    def _propose_text(
        self,
        yes: float,
        answers: float,
        days: float,
        ready: bool,
        start: bool,
        limits: Thresholds,
    ) -> str:
        t, lang = self.t, self.lang
        if start:
            return t(lang, "ui.ready.start")
        if ready:
            return t(lang, "ui.ready.propose_ready")
        needs = self._needs(
            [
                ("answers", limits.autonomous_answers - answers),
                ("days", limits.autonomous_days - days),
            ]
        )
        low = answers >= 5 - EPS and yes < limits.autonomous_score
        pct, target = round(yes * 100), round(limits.autonomous_score * 100)
        if needs and low:
            return t(lang, "ui.ready.propose_needs_low", needs=needs, score=pct, target=target)
        if needs:
            return t(lang, "ui.ready.propose_needs", needs=needs)
        return t(lang, "ui.ready.propose_low", score=pct, target=target)

    # -- an existing automation ------------------------------------------------------------

    def automation(self, domain: str, reading: Reading, given_back: bool) -> dict[str, Any]:
        hm, learning, t, lang = self.hm, self.hm.learning, self.t, self.lang
        assert learning is not None
        pairs = [
            p for p in reading.learned if hm.skill_params.get(p[0], {}).get("domain") == domain
        ]
        # Ready means on Autonomous in every room it teaches, within the limit (20.12).
        known = [p for p in pairs if hm.autonomous_here(*p)]
        state = automation_state(hm, reading, given_back)
        text = t(lang, f"ui.automations.state.{state}")
        if state == "learning" and pairs and len(known) < len(pairs):
            cap = hm.domain_cap(domain)
            waiting = [self.pair(domain, s, a, cap) for s, a in pairs if (s, a) not in known]
            nearest = max(waiting, key=lambda p: (p["step"] == "propose", p["progress"]))
            text = t(
                lang,
                "ui.ready.automation_next",
                ready=len(known),
                total=len(pairs),
                what=nearest["what"],
                text=nearest["text"],
            )
        row: dict[str, Any] = {
            "id": reading.entity_id,
            "name": reading.automation.name,
            "state": state,
            "ready": len(known),
            "total": len(pairs),
            "rooms": sorted({self._room(a) for _, a in pairs}),  # for the room filter
            "text": text,
            "history": history_line(hm, lang, reading.entity_id),
        }
        # The one list of automations (E-523): what it teaches, what it does in plain words
        # (from AI, B-346), and for an administrator Uitzetten or Weer aanzetten (E-42).
        teaches = [
            f"{t(lang, f'skills.{skill}')} – {self._room(area_id)}"
            for skill, area_id in reading.learned
        ]
        if teaches:
            row["teaches"] = teaches
        does = does_view(hm, reading.entity_id)
        if does is not None:
            row["does"] = does
        # What the filters above the list choose on (E-258, E-345), as at Huisinstellingen.
        row["causes"] = causes_of(hm.model, reading.automation)
        entity = hm.model.entities.get(reading.entity_id)
        if entity is not None and (labels := label_names(hm.model, entity)):
            row["labels"] = labels
        if entity is not None and (categories := category_names(hm.model, entity)):
            row["categories"] = categories
        if self.admin:
            status = hm.automations.status.get(reading.entity_id, AutomationStatus.SEEN)
            off = status is AutomationStatus.TURNED_OFF
            whole = reading.automation.enabled and reading.whole
            row["canTurnOff"] = whole and hm.ready_to_take_over(reading) and not off
            row["canTurnOn"] = off
        return row

    def others(self, shown: list[dict[str, Any]], given_back: bool) -> list[dict[str, Any]]:
        """The automations no part lists, such as a message or one HouseMind cannot read: in
        the same list, under Overig, so it is the only one (E-523)."""
        listed = {a["id"] for part in shown for a in part.get("automations", [])}
        return [
            self.automation("", r, given_back)
            for r in sorted(self.hm.automations.readings.values(), key=lambda r: r.automation.name)
            if r.entity_id not in listed
        ]

    # -- a part ----------------------------------------------------------------------------

    def part(self, domain: str, given_back: bool) -> dict[str, Any] | None:
        hm, learning, t, lang = self.hm, self.hm.learning, self.t, self.lang
        assert learning is not None
        skills = {s for s, p in hm.skill_params.items() if p.get("domain") == domain}
        readings = sorted(
            (r for r in hm.automations.readings.values() if domain in r.domains),
            key=lambda r: r.automation.name,
        )
        # What HouseMind saw itself, and what an automation teaches it (E-41); and every rule
        # a room has the devices for, at once (E-485). A rule that follows the devices shows
        # only while the room has them (E-487).
        known = set(learning.steps) | set(learning.first_seen)
        known |= {p for r in readings for p in r.learned}
        possible = hm.rules_possible()
        pairs = sorted(
            (s, a)
            for s, a in known | possible
            if s in skills
            and a in hm.model.areas
            and (hm.household is None or hm.household.applies(s, a))  # E-63: no TV, no line
            and ((s, a) in possible or hm.rule_there(s, a))
        )
        devices = DEVICES.get(domain)
        has_devices = devices is not None and any(
            e.domain == devices for e in hm.model.entities.values()
        )
        energy = hm.energy if domain == "energy" else None
        if domain == "energy":
            if energy is None or not energy.setup.has_any:
                return None
        elif not pairs and not has_devices:
            return None
        cap = hm.domain_cap(domain)
        if domain == "energy":
            shown = self._energy(cap)
        else:
            shown = [self.pair(domain, s, a, cap) for s, a in pairs]
        shown.sort(key=lambda p: (0 if p["heldBack"] else 1 if p["ready"] else 2, -p["progress"]))
        counts = {key: sum(1 for p in shown if p["step"] == key) for key in STEPS.values()}
        name = t(lang, f"ui.domain.{domain}")
        moving = [p for p in shown if p["step"] != "autonomous" or p["heldBack"]]
        if energy is not None and not energy.planning(self.now):
            text = t(lang, "ui.ready.energy.measuring", count=energy.baseline_days_left(self.now))
        elif not shown:
            text = t(lang, "ui.ready.nothing")
        elif not moving:
            text = t(lang, "ui.ready.part_all")
        else:
            text = t(lang, "ui.ready.part_nearest", what=moving[0]["what"], text=moving[0]["text"])
        asleep = hm.parts_off.get(domain)
        if asleep is not None:
            # Off (E-400): grey in Brein, with what is kept; Vesta says the same.
            text = t(lang, "ui.parts.off_kept", part=name)
        out: dict[str, Any] = {
            "domain": domain,
            "name": name,
            "off": asleep is not None,
            "offSince": (asleep or {}).get("since")
            if (asleep or {}).get("by") != "default"
            else None,
            "counts": counts,
            "cap": STEPS[cap] if cap is not None else None,
            "capName": self._step_name(cap) if cap is not None else None,
            "text": text,
            "limit": self.admin and any(p["heldBack"] for p in shown),
        }
        if self.admin and self.level >= 3:
            out["own"] = self._own(domain, domain, skills, None)  # 20.10
        if self.level >= 3:
            weeks = self.corrections.get(domain)
            if weeks and any(weeks):
                # S-01: how often someone stepped in, per week, oldest first (QA 2, M-16).
                trend = " → ".join(str(n) for n in weeks)
                out["corrections"] = {
                    "weeks": weeks,
                    "text": t(lang, "ui.ready.corrections", weeks=trend),
                }
            out["skills"] = shown
            out["automations"] = [self.automation(domain, r, given_back) for r in readings]
        return out

    # -- energy for the whole house (E-347, B-226) ------------------------------------------

    def _energy(self, cap: Step | None) -> list[dict[str, Any]]:
        """Energie per skill the house has, for the whole house: the room where HouseMind learned
        the skill most (counted per plan block, E-343), else a quiet row; while it
        still measures, how many of the days are done."""
        hm, learning, t, lang, now = self.hm, self.hm.learning, self.t, self.lang, self.now
        assert learning is not None and hm.energy is not None
        e, s = hm.energy, hm.energy.setup
        have = [
            skill
            for skill, there in (
                ("energy_export", bool(s.export_limit)),
                ("energy_boiler", bool(s.boiler)),
                ("energy_heat_pump", bool(s.heat_pump)),
                ("energy_appliance", bool(s.appliances)),
            )
            if there and skill in hm.skill_params
        ]
        measuring = not e.planning(now)
        days = BASELINE_DAYS - e.baseline_days_left(now)
        known = set(learning.steps) | set(learning.first_seen)
        house = t(lang, "ui.ready.whole_house")
        out = []
        for skill in have:
            name = t(lang, f"skills.{skill}")
            name = name[:1].upper() + name[1:]
            rooms = [
                a for k, a in known if k == skill and (a in hm.model.areas or a == WHOLE_HOUSE)
            ]
            if rooms and not measuring:
                area = max(rooms, key=lambda a: learning.score(skill, a, now)[1])
                row = self.pair("energy", skill, area, cap)
                row.update(what=name, skillName=name, room=house)
            else:
                row = {
                    "skill": skill,
                    "area": "",
                    "what": name,
                    "skillName": name,
                    "room": house,
                    "step": "observe",
                    "stepName": self._step_name(Step.OBSERVE),
                    "heldBack": False,
                    "ready": False,
                    "progress": round(days / BASELINE_DAYS, 3) if measuring else 0.0,
                    "bar": {
                        "value": round(days * 100 / BASELINE_DAYS) if measuring else 0,
                        "target": 100,
                    },
                    "counters": [self._counter("days", days, BASELINE_DAYS)] if measuring else [],
                    "text": t(lang, "ui.ready.energy.wait" if measuring else "ui.ready.nothing"),
                }
                if self.when is not None:
                    row.update(self.when.row(skill, WHOLE_HOUSE, Step.OBSERVE, self.level))
            out.append(row)
        return out

    async def parts(self) -> list[dict[str, Any]]:
        given_back = bool(await self.hm.db.get_setting("given_back", False))
        if self.level >= 3:
            self.corrections = await self.hm.corrections_weeks()
        domains = {p.get("domain") for p in self.hm.skill_params.values()} - {None}
        ordered = [d for d in ORDER if d in domains] + sorted(str(d) for d in domains - set(ORDER))
        return [p for d in ordered if (p := self.part(d, given_back)) is not None]


async def readiness(hm: HouseMind, lang: str, level: int, admin: bool) -> list[dict[str, Any]]:
    """The card "Klaar voor autonomie?" for one viewer (E-256). Made again only when
    HouseMind decided, learned, answered or was set something, when it read the house again,
    when energy measuring ends, after a round, or a minute later; until then every screen of
    this language and level gets the same card (code QA 3, punt 11: two thirds of a screen
    update in a large house). Read it, never change it."""
    now = hm.clock.now()
    minute = now.replace(second=0, microsecond=0)
    energy = hm.energy.since if hm.energy is not None else None
    house = (id(hm.model), len(hm.model.entities))
    stamp = (hm.knows, hm.status.last_tick, hm.db.settings_changed, minute, energy, house)
    key = (lang, level, admin)
    made = hm.readiness_made.get(key)
    if made is not None and made[0] == stamp:
        return made[1]  # type: ignore[no-any-return]
    parts = await Readiness(hm, lang, level, admin).parts()
    hm.readiness_made[key] = (stamp, parts)
    return parts


async def readiness_others(
    hm: HouseMind, lang: str, level: int, admin: bool, shown: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The automations no part of ``shown`` lists (E-523), for Brein on Betrokken and Expert."""
    given_back = bool(await hm.db.get_setting("given_back", False))
    return Readiness(hm, lang, level, admin).others(shown, given_back)


def does_view(hm: HouseMind, entity_id: str) -> dict[str, Any] | None:
    """What an automation or script does in plain words, from AI (B-346): only to read."""
    finding = hm.knowledge.get("automation", entity_id, "summary")
    if finding is None or finding.status == REJECTED:
        return None
    value = finding.value
    return {
        "text": value.get("text"),
        "part": value.get("part"),
        "takeOver": value.get("take_over"),
    }
