"""Explanations in plain language, per level, also without AI (S-02, E-93, E-162, H3.6).

Level 1 gets one short sentence only when asked; level 2 one sentence; level 3 the sentence
plus the evidence ("on workdays around 21:30 you usually do this – 18 of 20 times");
level 4 also the full trace (checks, step, certainty, context). Where a decision rests on what
HouseMind knows of the house – a role or a room's sensor a person confirmed or chose – it says
where that came from; AI is only ever a source of knowledge, never the decider (E-414).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from housemind.brain.learning import Learning, Result, _decayed
from housemind.brain.levels import STANDS
from housemind.core.model import HouseModel
from housemind.i18n.catalog import Languages
from housemind.safety.gate import Decision, Outcome

PARTS = ("night", "morning", "day", "evening")  # the parts of the day (E-455)


@dataclass(slots=True)
class Explainer:
    languages: Languages
    model: HouseModel
    learning: Learning | None = None
    # Vesta's own explainer says plain names (E-248): "de lamp in de slaapkamer"; lists and
    # screens keep the name from HA.
    names: Any = None
    # Only for a room with its article in a question about a stand (E-499): "de eetkamer".
    rooms: Any = None
    # What reading the history taught (E-257): (skill, area, bucket) → (days it happened, days)
    history: dict[tuple[str, str, str], tuple[int, int]] = field(default_factory=dict)
    # What a decision may rest on of the house knowledge, confirmed by a person (E-414): per
    # entity (its role) or "area:<id>" (the room's own temperature sensor), where it came
    # from – ai, behaviour, rules or person. Filled from memory by parts/understanding.py.
    known: dict[str, str] = field(default_factory=dict)

    def _t(self, lang: str, key: str, **params: Any) -> str:
        return self.languages.text(lang, key, **params)

    def _device(self, entity_id: str, lang: str = "") -> str:
        if self.names is not None and lang:
            return str(self.names.device(entity_id, lang, "acc"))  # "Ich habe den Lüfter …"
        entity = self.model.entities.get(entity_id)
        return entity.name if entity else entity_id

    def _area(self, area_id: str | None, entity_id: str, lang: str = "") -> str:
        if area_id and area_id in self.model.areas:
            chosen = area_id
        else:
            area = self.model.area_of(entity_id)
            chosen = area.id if area else ""
        if not chosen:
            return ""
        if self.names is not None and lang:
            return str(self.names.room(chosen, lang))  # "keuken" for "in de {area}"
        return self.model.areas[chosen].name

    def _degrees(self, lang: str, value: Any) -> str:
        shown = self.languages.number(lang, value) if isinstance(value, float) else value
        return f"{shown} °C"

    def _key(self, decision: Decision) -> str:
        r = decision.request
        # Before E-363 HouseMind steered the home battery: those lines stay readable in the
        # Logbook for as long as decisions are kept (E-18).
        if r.skill == "energy_battery" and r.facts.get("battery_mode"):
            return f"energy_battery_{r.facts['battery_mode']}"
        if r.action.endswith("turn_on") and r.facts.get("warning"):
            return "light_dim"
        if r.action.endswith("turn_on") and r.facts.get("stand") in STANDS:
            return "light_stand"  # a stand on the lamp, in words (E-494)
        if r.action == "climate.set_preset_mode":  # a heat pump's own "away" (E-318)
            return (
                "climate_preset_away"
                if r.data.get("preset_mode") == "away"
                else "climate_preset_back"
            )
        return r.action.replace(".", "_")

    def _params(self, lang: str, decision: Decision) -> dict[str, Any]:
        r = decision.request
        params: dict[str, Any] = {"device": self._device(r.entity_id, lang)}
        if "temperature" in r.data:
            params["temperature"] = self._degrees(lang, r.data["temperature"])
        if "value" in r.data:
            unit = r.facts.get("unit") or ""
            params["value"] = f"{r.data['value']:g} {unit}".strip()
        if "option" in r.data:
            params["option"] = str(r.data["option"])
        if r.facts.get("stand") in STANDS:
            params["stand"] = self._t(lang, f"stands.stand.{r.facts['stand']}")
        return params

    def action_phrase(self, lang: str, decision: Decision) -> str:
        key = self._key(decision)
        params = self._params(lang, decision)
        text = self._t(lang, f"action.{key}", **params)
        if text == f"action.{key}":
            text = self._t(lang, "action.other", **params)
        return text

    def stand_question(self, lang: str, decision: Decision) -> tuple[str, str] | None:
        """A stand for a whole room (E-499): "Mag ik de eetkamer op Gedimd zetten?" and its
        reason as a sentence of its own. None when it is no question about a stand."""
        r = decision.request
        stand = r.facts.get("stand")
        if stand not in STANDS or not r.area_id or r.area_id not in self.model.areas:
            return None
        words = self.names or self.rooms
        area = (
            words.area(r.area_id, lang, "acc")
            if words is not None
            else self.model.areas[r.area_id].name
        )
        question = self._t(
            lang, "ask.stand_room", area=area, stand=self._t(lang, f"stands.stand.{stand}")
        )
        key = f"ask.stand_reason.{r.skill}"
        reason = self._t(lang, key, at=r.facts.get("at", ""), minutes=r.facts.get("minutes", ""))
        if reason == key:
            said = self.reason_phrase(lang, decision)
            reason = said[:1].upper() + said[1:] + "."
        return question, reason

    def question(self, lang: str, decision: Decision) -> str:
        """A proposal as a question, e.g. "Shall I close the curtains? It is getting dark." """
        r = decision.request
        if (stand := self.stand_question(lang, decision)) is not None:
            return f"{stand[0]} {stand[1]}"
        if r.facts.get("planned_for"):
            # A question about later, e.g. the evening before waking up (E-88).
            key = f"ask.planned.{r.skill}" + ("_warmth" if r.facts.get("warmth") else "")
            return self._t(
                lang,
                key,
                at=r.facts.get("at", ""),
                area=self._area(r.area_id, r.entity_id, lang),
                when=self._t(lang, f"ask.planned.when.{r.facts.get('when', 'tomorrow')}"),
            )
        reason = self.reason_phrase(lang, decision)
        # Two forms, so every language can build a right sentence: Dutch joins with "omdat"
        # (the reason is a subordinate clause), the others add it as a sentence of its own.
        return self._t(
            lang,
            "ask.question",
            action=self._asked_action(lang, decision),
            reason=reason,
            Reason=reason[:1].upper() + reason[1:] + ".",
        )

    def _asked_action(self, lang: str, decision: Decision) -> str:
        key = self._key(decision)
        params = self._params(lang, decision)
        action = self._t(lang, f"ask.{key}", **params)
        return self._t(lang, "ask.other", **params) if action == f"ask.{key}" else action

    def why_asked(self, lang: str, decision: Decision, now: datetime) -> dict[str, str]:
        """Why she asks it, for the button Waarom? (E-349, B-228): the reason in both forms,
        what she saw, and the question again without its reason."""
        reason = self.reason_phrase(lang, decision)
        r = decision.request
        if r.facts.get("planned_for"):
            again = self.question(lang, decision)
        else:
            again = self._t(lang, "ask.question_short", action=self._asked_action(lang, decision))
        return {
            "reason": reason,
            "Reason": reason[:1].upper() + reason[1:] + ".",
            "evidence": self.evidence(lang, decision, now) or "",
            "question": again,
        }

    def reason_phrase(self, lang: str, decision: Decision) -> str:
        r = decision.request
        area = self._area(r.area_id, r.entity_id, lang)
        if r.facts.get("alarm_self"):  # on by itself, with the standing yes (E-540)
            return self._t(lang, f"reason.alarm_self_{r.facts['alarm_self']}")
        if r.facts.get("alarm"):  # the alarm went off, or is off again (E-533, E-534)
            return self._t(lang, "reason.alarm_back" if r.facts.get("back") else "reason.alarm")
        if r.reason.startswith("emergency:"):
            what = r.reason.split(":")[1]
            return self._t(lang, "reason.emergency", what=self._t(lang, f"emergency.{what}"))
        if r.facts.get("own_restore") and r.facts.get("why"):
            return self.energy_reason(lang, r.facts)  # energy given back (B-203)
        if r.layer.value == "restore":
            if r.reason in ("give_back", "step_down"):  # step_down: no longer itself (E-255)
                return self._t(lang, f"reason.{r.reason}")
            return self._t(lang, "reason.person")
        if r.layer.value == "person_answer":
            return self._t(lang, "reason.person")
        if r.layer.value == "test" and r.facts.get("tried") and not r.facts.get("back"):
            return self._t(lang, "reason.stand_try")  # Proef in Brein › Apparaten (E-491)
        if r.layer.value == "test":  # Probeer nu in Test het huis (E-476)
            return self._t(lang, "reason.test_back" if r.facts.get("back") else "reason.test")
        if r.reason == "take_over":
            return self._t(lang, "reason.take_over")
        if r.reason == "lived_in":
            return self._t(lang, "reason.lived_in")  # on vacation (E-285)
        if r.layer.value == "ai":
            return self._t(lang, "reason.ai")
        if (r.skill or "").startswith("energy_") and r.facts.get("why"):
            return self.energy_reason(lang, r.facts)
        skill = r.skill or "unknown"
        key = f"reason.{skill}"
        if r.facts.get("warning"):
            key = f"reason.{skill}_warning"
        elif r.facts.get("restored"):
            key = f"reason.{skill}_restored"
        elif r.facts.get("variant"):
            key = f"reason.{skill}_{r.facts['variant']}"  # e.g. the room reaching its wish (E-315)
        elif r.facts.get("preheat"):
            key = f"reason.{skill}_preheat"
        elif r.facts.get("done"):
            key = f"reason.{skill}_done"
        elif r.facts.get("source") == "usual":
            key = f"reason.{skill}_usual"
        text = self._t(
            lang, key, area=area, at=r.facts.get("at", ""), minutes=r.facts.get("minutes", "")
        )
        return text if text != key else self._t(lang, "reason.unknown")

    def evidence(self, lang: str, decision: Decision, now: datetime) -> str | None:
        r = decision.request
        if self.learning is None or not r.skill or not r.area_id:
            return None
        context = str(r.facts.get("context", ""))
        if r.skill in self.learning.answers_only or not context.startswith(
            ("workday|", "free|", "holiday|", "deviant|")
        ):
            # Skills that learn from answers: the evidence is what people answered.
            yes = self.learning.total(r.skill, r.area_id, Result.APPROVED, now)
            yes += self.learning.total(r.skill, r.area_id, Result.TRIAL_OK, now)
            no = self.learning.total(r.skill, r.area_id, Result.REJECTED, now)
            no += self.learning.total(r.skill, r.area_id, Result.TRIAL_BAD, now)
            if yes + no < 1:
                return self._t(lang, "explain.evidence_start")
            return self._t(lang, "explain.evidence_answers", hits=round(yes), total=round(yes + no))
        hits = total = 0.0
        for (skill, area, ctx, result), count in self.learning.counts.items():
            if skill != r.skill or area != r.area_id or ctx != context:
                continue
            weight = _decayed(count, now)
            if result == Result.HIT.value:
                hits += weight
            if result in (Result.HIT.value, Result.MISS.value, Result.CORRECTION.value):
                total += weight
        day_type, _, time = context.partition("|")
        past = self.history.get((r.skill, r.area_id, context))
        told = (
            self._t(
                lang,
                "explain.evidence_history",
                hits=past[0],
                total=past[1],
                days=self._t(lang, f"explain.days.{day_type}"),
                time=time,
            )
            if past and past[0]
            else None
        )
        if total < 2:
            if told:
                return told  # "9 van de 10 werkdagen gebeurde dit hier rond 18:30"
            taught_by = self.learning.taught_by.get((r.skill, r.area_id))
            if taught_by:
                return self._t(lang, "explain.evidence_automation", name=taught_by)
            return self._t(lang, "explain.evidence_start")
        when = self._t(lang, f"explain.when.{day_type}", time=time)
        said = self._t(lang, "explain.evidence", when=when, hits=round(hits), total=round(total))
        return f"{said} {told}" if told else said

    def explain(self, decision: Decision, level: int, lang: str, now: datetime) -> list[str]:
        """Lines of explanation for an action HouseMind took – or wanted to take: then what it
        wanted and why, and what stopped it – not only the reason for wanting it."""
        reason = self.reason_phrase(lang, decision)
        if decision.outcome is Outcome.EXECUTED:
            sentence = (
                self._t(lang, "explain.did", action=self.action_phrase(lang, decision))
                + ", "
                + self._t(lang, "explain.because", reason=reason)
                + "."
            )
            lines = [sentence]
        else:
            lines = [
                self._t(lang, "explain.wanted", action=self.wanted(lang, decision), reason=reason)
            ]
            if decision.outcome is Outcome.PROPOSED:
                lines.append(self._t(lang, "explain.step_propose"))
            else:
                lines.append(self.why_not(decision, lang))
        if level >= 3:
            evidence = self.evidence(lang, decision, now)
            if evidence:
                lines.append(evidence)
            known = self.known_from(decision)
            if known:  # "Volgens wat HouseMind van je huis weet (van AI, bevestigd door jou)."
                lines.append(self._t(lang, f"explain.known.{known}"))
        if level >= 4:
            # In words, never codes (QA 3, schermen 1): how sure, and the gate's checks – all
            # passed, or where it stopped.
            r = decision.request
            if self.learning is not None and r.skill and r.area_id:
                sure = self.learning.confidence(r.skill, r.area_id, now)
                lines.append(self._t(lang, "explain.confidence", value=round(sure * 100)))
            stopped = next((c for c in decision.checks if not c.ok), None)
            if stopped is not None:
                step = self._t(lang, f"ui.gate_step.{stopped.name}")
                lines.append(self._t(lang, "explain.checks_stop", step=step))
            elif decision.checks:
                lines.append(self._t(lang, "explain.checks_ok", count=len(decision.checks)))
        return lines

    def known_from(self, decision: Decision) -> str | None:
        """Where the house knowledge a decision rests on came from: the device's role, or for
        heating and cooling the room's own temperature sensor (B-361)."""
        r = decision.request
        found = self.known.get(r.entity_id)
        if found is None and r.entity_id.startswith("climate.") and r.area_id:
            found = self.known.get(f"area:{r.area_id}")
        return found

    def wanted(self, lang: str, decision: Decision) -> str:
        """What HouseMind wanted to do, as in a question: "turn on the kitchen lamp"."""
        key = self._key(decision)
        params = self._params(lang, decision)
        text = self._t(lang, f"ask.{key}", **params)
        return text if text != f"ask.{key}" else self._t(lang, "ask.other", **params)

    def observations(self, lang: str, decision: Decision) -> list[str]:
        """What HouseMind saw when it decided (E-218): the first step of its train of thought,
        from what the decision itself carries – nothing guessed afterwards."""
        r = decision.request
        facts = r.facts
        area = self._area(r.area_id, r.entity_id, lang)
        seen: list[str] = []
        if r.reason.startswith("emergency:"):
            what = self._t(lang, f"emergency.{r.reason.split(':')[1]}")
            seen.append(self._t(lang, "mind.saw.emergency", what=what))
        if (
            r.layer.value in ("person_answer", "restore") and not facts.get("own_restore")
        ) or facts.get("undo"):
            seen.append(self._t(lang, "mind.saw.person"))
        if facts.get("restored"):
            seen.append(self._t(lang, "mind.saw.restored", area=area))
        elif facts.get("occupied"):
            seen.append(self._t(lang, "mind.saw.occupied", area=area))
        if facts.get("empty_minutes") is not None:
            seen.append(self._t(lang, "mind.saw.empty", area=area, minutes=facts["empty_minutes"]))
        if facts.get("dark"):
            seen.append(self._t(lang, "mind.saw.dark"))
        if facts.get("at"):
            seen.append(self._t(lang, "mind.saw.at", at=facts["at"]))
        if facts.get("after"):
            seen.append(self._t(lang, "mind.saw.after", after=facts["after"]))
        if isinstance(facts.get("target"), int | float):
            target = self._degrees(lang, facts["target"])
            seen.append(self._t(lang, "mind.saw.target", target=target))
        if facts.get("automation"):
            seen.append(self._t(lang, "mind.saw.automation", name=facts["automation"]))
        if (r.skill or "").startswith("energy_"):
            seen.append(self._t(lang, "mind.saw.energy"))
        mode = facts.get("mode")
        if mode in ("sleep", "away", "guests", "vacation"):
            seen.append(self._t(lang, f"mind.saw.mode_{mode}"))
        # The part of the day its light is for, from the house's rhythm – not the clock (E-455).
        part = facts.get("part")
        if part in PARTS and facts.get("part_how") in ("woke", "rhythm", "late"):
            word = self._t(lang, f"rhythm.now.part.{part}")
            seen.append(self._t(lang, "mind.saw.part", part=word))
        return seen

    def thought(self, lang: str, decision: Decision, now: datetime) -> str:
        """One sentence: what HouseMind did, asked or left, and why (E-218)."""
        if decision.outcome is Outcome.EXECUTED:
            return self.explain(decision, 2, lang, now)[0]
        if decision.outcome is Outcome.PROPOSED:
            return self._t(lang, "mind.asked", question=self.question(lang, decision))
        return self._t(
            lang,
            "mind.not_done",
            action=self.wanted(lang, decision),
            why=self.why_not(decision, lang),
        )

    def energy_reason(self, lang: str, facts: dict[str, Any]) -> str:
        """The energy plan's reason, with times and the expected saving (E-73)."""
        params = dict(facts.get("params") or {})
        saving = params.get("saving")
        if isinstance(saving, int | float):
            amount = self.languages.number(lang, saving, 2)
            params["saving"] = f"{facts.get('currency', '€')} {amount}"
        if isinstance(params.get("temperature"), int | float):
            params["temperature"] = self._degrees(lang, float(params["temperature"]))
        text = self._t(lang, f"energy.why.{facts['why']}", **params)
        return text if not text.startswith("energy.why.") else self._t(lang, "reason.unknown")

    def why_not(self, decision: Decision | None, lang: str) -> str:
        """Why something did *not* happen (E-93)."""
        if decision is None:
            return self._t(lang, "gate.reason.nothing")
        if decision.outcome is Outcome.OBSERVED and not decision.reason:
            return self._t(lang, "gate.reason.observed")
        if decision.outcome is Outcome.PROPOSED:
            return self._t(lang, "gate.reason.proposed")
        reason = decision.reason or "nothing"
        code, _, detail = reason.partition(":")
        if code == "hard_limit":
            code = "hard_limit_any"
        if code == "part_off":  # "Verlichting staat uit." (E-400)
            return self._t(lang, "gate.reason.part_off", part=self._t(lang, f"ui.domain.{detail}"))
        text = self._t(lang, f"gate.reason.{code}")
        return text if text != f"gate.reason.{code}" else self._t(lang, "gate.reason.nothing")
