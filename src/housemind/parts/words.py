"""Learning and explaining in words (E-415, E-418, design 50.3).

What HouseMind learned per room, in the sentences of the readiness card (E-385), on Brein ›
Ritme – and with AI, once a day, the same as a short story per room. The weekly report, with
AI, as one paragraph of plain words. The AI only writes: the facts and every number come from
the core (E-162), and a text that says more is not used. All AI work runs in its own queue,
beside the core (E-414); without AI everything reads as before.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, time, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any

from housemind.brain.names import PlainNames
from housemind.brain.notify import Kind
from housemind.brain.readiness import readiness
from housemind.brain.rules import HouseRule, read_back, rule_text
from housemind.brain.unusual import Unusual, lamps_on_long
from housemind.knowledge.catalog import Step
from housemind.parts import Part
from housemind.safety.gate import ActionRequest, Decision, Layer

if TYPE_CHECKING:
    from housemind.ai.jobs import AIJobs
    from housemind.ai.service import AIService
    from housemind.brain.learning import Learning
    from housemind.brain.names import Names
    from housemind.brain.notify import Person
    from housemind.brain.proposals import Proposals
    from housemind.brain.rules import RuleBook
    from housemind.core.clock import Clock
    from housemind.core.model import HouseModel
    from housemind.i18n.catalog import Languages
    from housemind.memory.database import Database
    from housemind.safety.gate import SafetyGate
    from housemind.world.world import World, WorldState

_LOGGER = logging.getLogger(__name__)

HABITS_AGAIN = timedelta(days=1)  # the stories per room, once a day (E-415)
STORY_ROOMS = 3  # the stories of the rooms learned this week that go with the report
RULES_AGAIN = timedelta(days=7)  # house rules the AI suggests, once a week (E-417)
UNUSUAL_PER_DAY = 3  # questions about something unusual, at most, a day (E-416)
UNUSUAL_QUIET = timedelta(days=30)  # after 'Nee, laat aan': not about that lamp again
OFF_OR_KEEP = (("yes", "notify.answer.turn_off"), ("no", "notify.answer.keep_on"))


class WordsPart(Part):
    """Gewoontes per ruimte and the weekly report in words."""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        ai: AIService
        ai_jobs: AIJobs
        clock: Clock
        db: Database
        languages: Languages
        learning: Learning | None
        model: HouseModel
        names: Names
        _people: list[Person]
        _habit_words: dict[str, Any]
        _rule_suggest: dict[str, Any]
        rules: RuleBook

        activated: Any
        gate: SafetyGate | None
        proposals: Proposals
        world: World | None
        _raised: dict[str, Any]
        _unusual_busy: set[str]

        def _language(self) -> str: ...
        async def _notify(self) -> None: ...
        def report_text(self, data: dict[str, Any], level: int, lang: str) -> list[str]: ...
        def _tracked(self, *prefixes: str) -> list[str]: ...
        async def _resolve(self, topic: str) -> None: ...
        async def close_notice(self, topic: str) -> None: ...
        async def _record(self, decision: Decision, previous: Any = None) -> int: ...
        async def save_rule(
            self, rule: HouseRule, user_id: str | None, replace_ids: list[int] | None = None
        ) -> int: ...
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

    async def _load_habit_words(self) -> None:
        stored = await self.db.get_setting("habit_words", {})
        self._habit_words = stored if isinstance(stored, dict) else {}
        kept = await self.db.get_setting("rule_suggest", {})
        self._rule_suggest = kept if isinstance(kept, dict) else {}

    # -- your habits per room (E-415) -----------------------------------------------------

    @staticmethod
    def habit_facts(parts: list[dict[str, Any]]) -> dict[str, list[str]]:
        """Per room what HouseMind learned, in the sentences of the readiness card: only what it
        asks or does itself – what it is still watching, it has not learned yet, nor a rule
        that never saw its situation (E-485)."""
        out: dict[str, list[str]] = {}
        for part in parts:
            for row in part.get("skills", []):
                if row.get("unseen"):
                    continue
                if row.get("when") and row.get("step") != "observe" and row.get("area"):
                    out.setdefault(str(row["area"]), []).append(str(row["when"]))
        return out

    def habits_view(self, parts: list[dict[str, Any]], lang: str) -> list[dict[str, Any]]:
        """Brein › Ritme: per room the sentences of the core, and the AI's story when it was
        written for exactly these sentences (a story about what has since changed is not
        shown)."""
        rooms = (self._habit_words.get(lang) or {}).get("rooms") or {}
        out = []
        for area_id, lines in self.habit_facts(parts).items():
            area = self.model.areas.get(area_id)
            kept = rooms.get(area_id)
            story = kept["text"] if isinstance(kept, dict) and kept.get("facts") == lines else None
            out.append(
                {
                    "area": area_id,
                    "room": area.name if area else area_id,
                    "lines": lines,
                    "text": story,
                    "ai": story is not None,
                }
            )
        return out

    def _word_languages(self) -> list[str]:
        """The languages of the people who see Brein (level 3 and up), else the house's."""
        found = {self.languages.resolve(p.language) for p in self._people if p.level >= 3}
        return sorted(found) or [self.languages.resolve(self._language())]

    def habit_words_when_due(self) -> None:
        """From the quarter-hour look, with AI on: the stories per room, once a day per
        language, queued beside the core (E-414)."""
        assert self.learning is not None
        if all(step is Step.OBSERVE for step in self.learning.steps.values()):
            return  # nothing learned yet that it asks or does itself
        now = self.clock.now()
        for lang in self._word_languages():
            at = (self._habit_words.get(lang) or {}).get("at")
            if isinstance(at, str) and now - datetime.fromisoformat(at) < HABITS_AGAIN:
                continue
            self.ai_jobs.submit(f"habits:{lang}", partial(self._write_habits, lang))

    async def _write_habits(self, lang: str) -> None:
        facts = self.habit_facts(await readiness(self, lang, 3, False))  # type: ignore[arg-type]
        stored: dict[str, Any] = {"at": self.clock.now().isoformat(), "rooms": {}}
        rooms: dict[str, tuple[str, list[str]]] = {}
        for area_id, lines in facts.items():
            label = self.ai.labels.area(area_id)
            if label is not None:
                rooms[label] = (area_id, lines)
        if rooms:
            masked = {
                label: [self._masked(line, lang) for line in lines]
                for label, (_, lines) in rooms.items()
            }
            found = await self.ai.habit_words(masked, lang, PlainNames(self.names, lang))
            for label, (with_labels, plain) in found.items():
                area_id, lines = rooms[label]
                stored["rooms"][area_id] = {"facts": lines, "text": plain, "labels": with_labels}
        self._habit_words[lang] = stored
        await self.db.set_setting("habit_words", self._habit_words)
        await self._notify()

    def _masked(self, line: str, lang: str) -> str:
        """A sentence of the card with the rooms as labels: "de lamp in de gang" becomes "de lamp
        in hall_1", so that it comes back as "de lamp in de gang" (the privacy filter does the
        rest: Home Assistant's names, people, codes, E-15)."""
        pairs = []
        for area_id in self.model.areas:
            label = self.ai.labels.area(area_id)
            plain = self.names.area(area_id, lang)
            if label and plain:
                pairs.append((plain, label))
        for plain, label in sorted(pairs, key=lambda p: -len(p[0])):
            line = re.sub(rf"(?<!\w){re.escape(plain)}(?!\w)", label, line, flags=re.IGNORECASE)
        return line

    # -- the weekly report in words (E-418) -----------------------------------------------

    def report_words(self, data: dict[str, Any], level: int, lang: str) -> tuple[list[str], bool]:
        """The report for one person: from Involved, with AI, the AI's paragraph – Expert also
        keeps the core's line about what HouseMind thought and stopped; else the sentences of
        the core. True when the words are the AI's."""
        lines = self.report_text(data, level, lang)
        words = (data.get("words") or {}).get(lang)
        if level < 3 or not words:
            return lines, False
        return ([words, lines[-1]] if level >= 4 else [words]), True

    def wants_report_words(self, data: dict[str, Any], level: int, lang: str) -> bool:
        """Should this report wait for the AI's words? From Involved, with AI on, until the AI
        was asked once for this language (an empty answer: the core's sentences)."""
        return level >= 3 and self.ai.status() == "on" and lang not in (data.get("words") or {})

    def report_words_soon(self, week: str, data: dict[str, Any], lang: str) -> None:
        self.ai_jobs.submit(f"report:{lang}", partial(self._write_report, week, data, lang))

    async def _write_report(self, week: str, data: dict[str, Any], lang: str) -> None:
        await self._keep_report_words(
            week, lang, ""
        )  # asked: an answer that never comes waits no longer
        learned = {str(i.get("area")) for i in data.get("learned", [])}
        rooms = (self._habit_words.get(lang) or {}).get("rooms") or {}
        habits = [
            str(r["labels"])
            for a, r in rooms.items()
            if a in learned and isinstance(r, dict) and r.get("labels")
        ][:STORY_ROOMS]
        sentences = self.report_text(data, 3, lang)
        text = await self.ai.report_words(sentences, habits, lang, PlainNames(self.names, lang))
        if text:
            await self._keep_report_words(week, lang, text)

    async def _keep_report_words(self, week: str, lang: str, text: str) -> None:
        row = await self.db.fetchone("SELECT data FROM reports WHERE week = ?", (week,))
        if row is None:
            return
        data = json.loads(row["data"])
        data.setdefault("words", {})[lang] = text
        await self.db.execute(
            "UPDATE reports SET data = ? WHERE week = ?", (json.dumps(data), week)
        )

    # -- something unusual as a question (E-416) ------------------------------------------

    async def _unusual(self, snapshot: WorldState) -> None:
        """Every quarter of an hour: a lamp on for hours in a room where nobody has been. The
        core only sees it and asks; with AI, the AI words the question beside the core."""
        if self.world is None:
            return
        # Already a question about that lamp – a proposal of what HouseMind learned: that one
        # asks it, not a second question (and one asked before goes away).
        proposed = {p.entity_id for p in await self.proposals.open(snapshot.now)}
        found = [
            u
            for u in lamps_on_long(
                snapshot, self.world.states, self.model, self.world.status_led_ids
            )
            if u.entity_id not in proposed
        ]
        current = {_unusual_topic(u) for u in found}
        for topic in self._tracked("unusual:"):
            if topic not in current:
                await self._resolve(topic)  # the lamp went off, or someone came
        kept = await self.db.get_setting("unusual_keep", {}) or {}
        day = snapshot.local.date().isoformat()
        asked = await self.db.get_setting("unusual_asked", {}) or {}
        count = int(asked.get(day, 0))
        for unusual in found:
            topic = _unusual_topic(unusual)
            if topic in self._raised or topic in self._unusual_busy:
                continue
            since = kept.get(unusual.entity_id)
            if since and snapshot.now - datetime.fromisoformat(since) < UNUSUAL_QUIET:
                continue
            if count >= UNUSUAL_PER_DAY:
                break
            count += 1
            if self.ai.status() == "on":
                self._unusual_busy.add(topic)
                self.ai_jobs.submit(topic, partial(self._ask_unusual, unusual, topic))
            else:
                await self._raise_unusual(unusual, topic, None)
        await self.db.set_setting("unusual_asked", {day: count})

    async def _ask_unusual(self, unusual: Unusual, topic: str) -> None:
        """The AI's question: a possible reason, with the hours of the core (E-416)."""
        try:
            lang = self.languages.resolve(self._language())
            local = self.clock.now().astimezone(self.world.tz) if self.world else self.clock.now()
            facts = {
                "what": unusual.kind,
                "light": self.ai.labels.to_label.get(unusual.entity_id, "light"),
                "room": self.ai.labels.area(unusual.area_id) or "room",
                "hours": unusual.hours,
                "nobody_there_for_hours": unusual.empty_hours,
                "time": local.strftime("%H:%M"),
            }
            text = await self.ai.unusual(facts, lang, PlainNames(self.names, lang))
        finally:
            self._unusual_busy.discard(topic)
        await self._raise_unusual(unusual, topic, text)

    async def _raise_unusual(self, unusual: Unusual, topic: str, text: str | None) -> None:
        if self.world is None:
            return
        state = self.world.states.get(unusual.entity_id)
        if state is None or state.state != "on":
            return  # it went off meanwhile
        snapshot = self.world.snapshot(self.clock.now())
        params: dict[str, Any]
        if text:  # the AI's words, said as they are – not to an AI again (B-173)
            key, params = "notify.unusual.ai", {"text": text, "text_ai": ""}
        else:
            entity = self.model.entities.get(unusual.entity_id)
            device = entity.name if entity and entity.name else unusual.entity_id
            key, params = "notify.unusual.light_on", {"device": device, "hours": unusual.hours}
        await self._raise(Kind.INFO, topic, key, params, snapshot, actions=OFF_OR_KEEP)

    async def answer_unusual(self, topic: str, yes: bool, user_id: str | None) -> dict[str, Any]:
        """'Ja, zet uit': off through the safety gate, as a person's answer. 'Nee, laat aan':
        it stays on, and HouseMind does not ask about that lamp for a month (E-416)."""
        assert self.gate is not None
        entity_id = topic.split(":", 2)[2] if topic.count(":") >= 2 else ""
        if not entity_id.startswith("light."):
            return {"ok": False}
        now = self.clock.now()
        if not yes:
            kept = await self.db.get_setting("unusual_keep", {}) or {}
            kept[entity_id] = now.isoformat()
            await self.db.set_setting("unusual_keep", kept)
            await self.close_notice(topic)
            await self._notify()
            return {"ok": True}
        request = ActionRequest(
            Layer.PERSON_ANSWER,
            entity_id,
            "light.turn_off",
            reason="unusual",
            created_at=now,
            user_id=user_id,
        )
        decision = await self.gate.submit(request)
        await self._record(decision)
        if decision.executed:
            await self.close_notice(topic)
        await self._notify()
        return {"ok": decision.executed}

    # -- house rules the AI suggests (E-417) ----------------------------------------------

    def rule_suggestions_when_due(self) -> None:
        """From the quarter-hour look, with AI on: once a week the AI may suggest house rules
        from what HouseMind learned, queued beside the core (E-414)."""
        assert self.learning is not None
        if all(step is Step.OBSERVE for step in self.learning.steps.values()):
            return
        at = self._rule_suggest.get("at")
        if isinstance(at, str) and self.clock.now() - datetime.fromisoformat(at) < RULES_AGAIN:
            return
        self.ai_jobs.submit("suggest_rules", self._suggest_rules)

    async def _suggest_rules(self) -> None:
        lang = self.languages.resolve(self._language())
        state = self._rule_suggest
        state["at"] = self.clock.now().isoformat()
        await self.db.set_setting("rule_suggest", state)
        learned: dict[str, list[str]] = {}
        for area_id, lines in self.habit_facts(await readiness(self, lang, 3, False)).items():  # type: ignore[arg-type]
            label = self.ai.labels.area(area_id)
            if label is not None:
                learned[label] = [self._masked(line, lang) for line in lines]
        if not learned:
            return
        rules = [
            self._masked(rule_text(r, lang, self.languages, self.model), lang)
            for r in self.rules.rules
        ]
        refused = [self._masked(str(d["text"]), lang) for d in state.get("declined", [])]
        rows = await self.db.fetchall(
            "SELECT DISTINCT skill, area_id FROM proposals "
            "WHERE answer = 'never' AND skill IS NOT NULL"
        )
        for row in rows:  # 'Nooit zo' on a proposal: that, there, is not wanted (E-133)
            what = self.languages.text(lang, f"skills.{row['skill']}")
            refused.append(f"{what} {self.ai.labels.area(row['area_id']) or ''}".strip())
        found = await self.ai.suggest_rules(
            learned, rules, refused, lang, PlainNames(self.names, lang)
        )
        declined = {str(d["key"]) for d in state.get("declined", [])}
        waiting = state.setdefault("open", {})
        known = {_rule_key(r) for r in self.rules.rules} | {str(o["key"]) for o in waiting.values()}
        if self.world is None:
            return
        snapshot = self.world.snapshot(self.clock.now())
        for rule, why in found:
            key = _rule_key(rule)
            if key in declined or key in known or self.rules.conflicts(rule):
                continue  # refused before, there already, or against a rule there is
            known.add(key)
            number = int(state.get("next", 0)) + 1
            state["next"] = number
            text = read_back(rule, lang, self.languages, self.model)
            waiting[str(number)] = {"key": key, "rule": _rule_fields(rule), "text": text}
            await self._raise(
                Kind.LEARNING,
                f"rule_suggest:{number}",
                "notify.rule_suggest",
                {"rule": text, "why": why, "text_ai": ""},  # the AI's why: not to an AI again
                snapshot,
                concerns="admins",
                actions=(("yes", "notify.answer.yes"), ("no", "notify.answer.no")),
            )
        await self.db.set_setting("rule_suggest", state)
        await self._notify()

    async def answer_rule_suggestion(
        self, topic: str, yes: bool, user_id: str | None
    ) -> dict[str, Any]:
        """Yes: the house rule is made, as a person's own (E-87). No: it is not suggested
        again (E-417)."""
        number = topic.split(":", 1)[1]
        state = self._rule_suggest
        found = state.get("open", {}).pop(number, None)
        if found is None:
            return {"ok": False}
        if yes:
            await self.save_rule(_rule_back(found["rule"]), user_id)
        else:
            state.setdefault("declined", []).append({"key": found["key"], "text": found["text"]})
        await self.db.set_setting("rule_suggest", state)
        await self.close_notice(topic)
        await self._notify()
        return {"ok": True}


def _unusual_topic(unusual: Unusual) -> str:
    return f"unusual:{unusual.kind}:{unusual.entity_id}"


def _rule_fields(rule: HouseRule) -> dict[str, Any]:
    """A suggested rule as it is kept until someone answers."""
    return {
        "scope_type": rule.scope_type,
        "scope_id": rule.scope_id,
        "effect": rule.effect,
        "category": rule.category,
        "value": rule.value,
        "time_from": rule.time_from.isoformat("minutes") if rule.time_from else None,
        "time_to": rule.time_to.isoformat("minutes") if rule.time_to else None,
        "days": list(rule.days),
    }


def _rule_back(fields: dict[str, Any]) -> HouseRule:
    def at(value: Any) -> time | None:
        return time.fromisoformat(value) if isinstance(value, str) else None

    return HouseRule(
        scope_type=str(fields["scope_type"]),
        scope_id=str(fields["scope_id"]),
        effect=str(fields["effect"]),
        category=fields.get("category"),
        value=fields.get("value"),
        time_from=at(fields.get("time_from")),
        time_to=at(fields.get("time_to")),
        days=tuple(int(d) for d in fields.get("days") or ()),
    )


def _rule_key(rule: HouseRule) -> str:
    """What makes two rules the same rule: what it is about and what it says, not its id."""
    return json.dumps(_rule_fields(rule), sort_keys=True)
