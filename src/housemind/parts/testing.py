"""Test het huis (E-474 – E-481, E-518, E-519, design 70, 85): an administrator on Expert tests
HouseMind in the real house. Speel het na: a situation over the picture of now, through a quiet
copy of the skills and the gate's own evaluation – nothing is switched, kept, learned or said –
with per line how it would go for real; Doe het echt then does all of it at once in the house,
through the gate as a 'Do it' of the test layer, and puts it back as it was after a minute
(B-390, B-444). While that minute runs, the everyday rules keep off those devices (B-445); what
goes back is kept in the database, so a restart still puts it back (B-394). Loop zelf rond: a
part of a room on Autonomous with the real sensors (the testkwartier). A test counts for
nothing."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from housemind.brain import security
from housemind.brain.icons import Icons, area_icon
from housemind.brain.levels import FELT, STANDS, WAIT_SETTING, LightLevels, from_json, wait_from
from housemind.brain.levels import SETTING as LEVELS_SETTING
from housemind.brain.notify import Kind, Person
from housemind.brain.pretend import (
    ALARM,
    EMERGENCIES,
    Room,
    apply,
    available,
    choose,
    implied,
    room_of,
)
from housemind.brain.skills import quiet_copy
from housemind.brain.undo import inverse
from housemind.core.model import Source, State, StateChange, daypart
from housemind.knowledge.matcher import recognise
from housemind.parts import Part
from housemind.safety.emergency import ALARMS
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from housemind.brain.explain import Explainer
    from housemind.brain.learning import Learning
    from housemind.brain.skills import SkillEngine
    from housemind.brain.speech import Speech
    from housemind.core.clock import Clock
    from housemind.core.model import HouseModel
    from housemind.i18n.catalog import Languages
    from housemind.knowledge.catalog import Catalog
    from housemind.memory.database import Database
    from housemind.parts.thinking import Recent
    from housemind.safety.emergency import EmergencyReactions
    from housemind.safety.gate import SafetyGate
    from housemind.world.climate import Temperatures
    from housemind.world.world import World, WorldState

HAPPENED = 12  # lines under 'Wat er net gebeurde'
TRY_S = 60.0  # Doe het echt goes back after a minute (E-476, E-518)
HOLD_S = 60.0  # the Standen window says it is still open every 20 s (E-516)
PLAYS_SHOWN = 3  # the last plays of a room in the panel
QUARTER_MINUTES = (5, 15, 60)  # a testkwartier, standard 15 minutes (E-477, B-391)
QUARTER_DEFAULT = 15
# The parts that may be tried for real, and their devices (E-481): the rest only Doe alsof.
TESTABLE = {"lighting": ("light",), "covers": ("cover",), "climate": ("climate",), "air": ("fan",)}


@dataclass(slots=True)
class Try:
    """One Probeer nu: what was done, until when, and the way back."""

    id: int
    area_id: str
    entity_id: str
    text: str
    started: datetime
    until: datetime
    back: ActionRequest
    members: frozenset[str]  # the lamps of a group: a hand on one of them counts too
    done_state: str  # what HouseMind set: after a restart, only that goes back
    kept: bool = False
    touched: bool = False  # a person changed it since: it stays as that person left it
    over: bool = False
    stand: bool = False  # a Proef of a stand (E-491): quiet, the lamp itself shows it


@dataclass(slots=True)
class Play:
    """One Doe het echt (E-518, B-444): everything HouseMind would do in the chosen situation,
    done at once – a ``Try`` per device – and what it did not do, with why."""

    id: int
    area_id: str
    until: datetime
    # The tries themselves: the database gives a number again once its row is gone.
    tries: list[Try]
    icons: dict[int, dict[str, Any]]  # per try the device's icon as it went (E-512)
    skipped: list[dict[str, Any]]  # per line not done: its icon, text and why
    notes: list[str] = field(default_factory=list)  # done besides the devices (E-541)


@dataclass(slots=True)
class Quarter:
    """One testkwartier: a part of a room on Autonomous until ``until`` (E-477, B-391)."""

    id: int
    area_id: str
    part: str
    until: datetime
    changed: dict[str, ActionRequest]  # per device the way back to before the test
    done: dict[str, str]  # per device what the test left it in (a restart, B-394)
    touched: set[str]  # a person changed it since: it stays as that person left it
    over: bool = False


class TestingPart(Part):
    """The test panel: what the room measures, Doe alsof and what just happened."""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        area_roles: dict[str, str]
        icons: Icons
        clock: Clock
        emergencies: EmergencyReactions | None
        explainer: Explainer | None
        gate: SafetyGate | None
        languages: Languages
        learning: Learning | None
        model: HouseModel
        recent: dict[int, Recent]
        skills: SkillEngine | None
        world: World | None
        temperatures: Temperatures
        db: Database
        _tries: dict[int, Try]
        _plays: dict[int, Play]
        _quarters: dict[int, Quarter]
        _stand_tries: dict[str, int]  # a lamp → its Proef of a stand (E-491)
        light_levels: LightLevels
        skill_params: dict[str, dict[str, Any]]
        catalog: Catalog
        parts_off: dict[str, dict[str, str]]
        speech: Speech

        def _beside(self, work: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]: ...
        async def _record(self, decision: Decision, previous: State | None = None) -> int: ...
        async def _notify(self) -> None: ...
        async def _resolve(self, topic: str) -> None: ...
        def _person(self, user_id: str | None) -> Person | None: ...
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
            to: list[Person] | None = None,
        ) -> int | None: ...

        def explain(
            self, decision: Decision, level: int, language: str, plain: bool = False
        ) -> list[str]: ...

    def _test_room(self, area_id: str) -> Room:
        found = self.emergencies.emergency_for if self.emergencies is not None else None
        signals = self.world.sleep_watch.signals if self.world is not None else {}
        sleep = signals.get(area_id, ("none", ()))[0]  # 'In bed', 'Uit bed' (E-483)
        area_for = self.world.occupancy.area_for if self.world is not None else None
        room = room_of(self.model, area_id, found or (lambda entity_id: None), sleep, area_for)
        # What measures the room as HouseMind knows it – not an oven's or a socket's own
        # temperature in the same room (E-514).
        return replace(room, temperature=tuple(self.temperatures.sources(area_id)))

    def _test_persons(self) -> list[str]:
        return sorted(e for e in self.model.entities if e.startswith("person."))

    def _test_allowed(self, room: Room) -> list[str]:
        """The situations of a room – the alarm going off in every room of a house that has
        one (E-541)."""
        alarm = security.the_alarm(self._hm) is not None
        return available(room, bool(self._test_persons()), alarm)

    def test_choose(self, area_id: str, chosen: list[str], key: str, on: bool) -> list[str]:
        """The situations of Doe alsof after a tap (E-475)."""
        return choose(chosen, key, on, self._test_allowed(self._test_room(area_id)))

    def test_view(self, lang: str, area_id: str | None, chosen: list[str]) -> dict[str, Any]:
        """Everything for the panel (E-474): the rooms, what the chosen room measures now, the
        situations it can have, what HouseMind would do in the chosen one, and what just
        happened there."""
        assert self.world is not None
        lang = self.languages.resolve(lang)
        t = self.languages.text
        areas = sorted(self.model.areas.values(), key=lambda a: a.name.lower())
        if area_id not in self.model.areas:
            area_id = areas[0].id if areas else None
        out: dict[str, Any] = {
            "rooms": [
                {"id": a.id, "name": a.name, "icon": area_icon(a, self.area_roles.get(a.id))}
                for a in areas
            ],
            "area": area_id,
            "now": "",
            "situations": [],
            "lines": [],
            "nothing": None,
            "happened": [],
            "blocked": None,
            "plays": [],
            "quarter": {"parts": [], "minutes": list(QUARTER_MINUTES), "standard": QUARTER_DEFAULT},
            "anyRunning": False,
        }
        if area_id is None:
            return out
        room = self._test_room(area_id)
        allowed = self._test_allowed(room)
        chosen = [k for k in allowed if k in chosen]
        out["now"] = self._test_now(lang, area_id, room)
        # A part of the day brings its own light (E-519): that button is half on.
        brought = implied(self.world.look(self.clock.now()), chosen, self.world.outside_dark_at)
        out["situations"] = [
            {"key": k, "on": k in chosen, "implied": k == brought} for k in allowed
        ]
        out["happened"] = self._test_happened(lang, area_id)
        out["plays"] = self._test_plays(lang, area_id)
        out["quarter"] = self._test_quarter_view(lang, area_id)
        out["anyRunning"] = any(not t.over for t in self._tries.values()) or any(
            not q.over for q in self._quarters.values()
        )
        if self._hm.status.emergency in ALARMS:
            out["blocked"] = t(lang, "test.alarm")  # the emergency reactions rule (E-480)
            return out
        # Without a situation: what HouseMind would do now – also something to play (E-476).
        out["lines"] = self._test_pretend(lang, area_id, room, chosen)
        if not out["lines"]:
            out["nothing"] = t(lang, "test.pretend.nothing" if chosen else "ui.simulate.nothing")
        return out

    def _test_plays(self, lang: str, area_id: str) -> list[dict[str, Any]]:
        """Doe het echt in this room (E-518): running, or over in the last ten minutes – per
        line done, back, kept, left as a person left it, or not done and why."""
        zone = ZoneInfo(self.world.time_zone if self.world else "UTC")
        t = self.languages.text
        now = self.clock.now()
        out: list[dict[str, Any]] = []
        for play in sorted(self._plays.values(), key=lambda x: x.id, reverse=True):
            if play.area_id != area_id:
                continue
            trials = play.tries
            running = any(not trial.over for trial in trials)
            if not running and now - play.until > timedelta(minutes=10):
                continue
            lines: list[dict[str, Any]] = []
            for trial in trials:
                state = (
                    "done"
                    if not trial.over
                    else "kept"
                    if trial.kept
                    else "left"
                    if trial.touched
                    else "back"
                )
                lines.append(
                    {
                        **play.icons.get(trial.id, {}),
                        "text": trial.text[:1].upper() + trial.text[1:],
                        "state": state,
                        "say": t(lang, f"test.play.state.{state}"),
                    }
                )
            for skipped in play.skipped:
                lines.append(
                    {
                        **{k: v for k, v in skipped.items() if k != "why"},
                        "state": "skipped",
                        "say": t(lang, "test.play.state.skipped", why=skipped["why"]),
                    }
                )
            for note in play.notes:  # Vesta here, the message to the tester (E-541)
                lines.append(
                    {"text": note, "state": "note", "say": t(lang, "test.play.state.note")}
                )
            out.append(
                {
                    "id": play.id,
                    "until": play.until.astimezone(zone).strftime("%H:%M:%S"),
                    "running": running,
                    "lines": lines,
                }
            )
        return out[:PLAYS_SHOWN]

    def _test_now(self, lang: str, area_id: str, room: Room) -> str:
        """What the room measures now, in one short line."""
        assert self.world is not None
        t = self.languages.text
        look = self.world.look(self.clock.now())
        bits: list[str] = []
        if room.presence:
            bits.append(
                t(lang, "test.now.someone" if look.occupied(area_id) else "test.now.nobody")
            )
        bits.append(t(lang, "test.now.dark" if look.dark.get(area_id) else "test.now.light"))
        lux = look.lux.get(area_id)
        if lux is not None:
            bits.append(f"{lux:.0f} lux")
        temperature = self.temperatures.of_room(area_id, self.world.states)
        if temperature is not None:
            bits.append(f"{self.languages.number(lang, temperature, 1)} °C")
        for entity_id in room.humidity:
            state = self.world.states.get(entity_id)
            try:
                bits.append(f"{float(state.state if state else ''):.0f}%")
                break
            except ValueError:
                continue
        if room.openings:
            opened = any(
                (s := self.world.states.get(e)) is not None and s.state == "on"
                for e in room.openings
            )
            bits.append(t(lang, "test.now.open" if opened else "test.now.closed"))
        return t(lang, "test.now.line", what=" · ".join(bits))

    def _test_pretend(
        self, lang: str, area_id: str, room: Room, chosen: list[str]
    ) -> list[dict[str, Any]]:
        """Doe alsof (E-475, B-389): what HouseMind would do in this situation and what the
        gate says now and on Autonomous. Nothing is switched, kept, learned or said."""
        assert self.world is not None and self.gate is not None and self.skills is not None
        assert self.learning is not None and self.explainer is not None
        t = self.languages.text
        picture, states = apply(
            self.world.look(self.clock.now()),
            dict(self.world.states),
            area_id,
            chosen,
            room,
            self._test_persons(),
            self.world.outside_dark_at,
        )
        emergency = next((k for k in EMERGENCIES if k in chosen), None)
        lines: list[dict[str, Any]] = []
        elsewhere = 0
        if emergency is None and ALARM in chosen:
            return self._test_alarm_lines(lang, area_id)  # E-541
        if emergency is not None and self.emergencies is not None:
            requests = self.emergencies.requests_for(
                EMERGENCIES[emergency], room.emergencies[emergency], area_id, states
            )
            here = []
            for request in requests:
                area = self.model.area_of(request.entity_id)
                if area is not None and area.id == area_id:
                    here.append(request)
                elif request.action == "light.turn_on":
                    elsewhere += 1
                else:
                    here.append(request)  # a main tap or the ventilation: always worth a line
            requests = here
        else:
            twin = quiet_copy(self.skills, self.learning)
            requests = twin.evaluate(picture, states, rooms=[area_id])
        for request in requests:
            lines.append(self._test_line(lang, request, tryable=emergency is None))
        if emergency is None:
            arming = self._test_arming(lang, chosen, picture, states)
            if arming is not None:
                lines.append(arming)
        if emergency is not None:
            if elsewhere:
                lines.append(
                    self._test_note(lang, t(lang, "test.emergency.house", count=elsewhere))
                )
            lines.append(self._test_note(lang, t(lang, "test.emergency.notify")))
        return lines

    def _test_note(
        self, lang: str, text: str, real: str = "test.real.emergency", skip: str = ""
    ) -> dict[str, Any]:
        """A line of an emergency that is no device of this room: said, never played."""
        t = self.languages.text
        return {
            "text": text,
            "real": t(lang, real),
            "kind": "emergency",
            "outcome": "executed",
            "will": False,
            "skip": t(lang, skip or "test.play.emergency"),
        }

    def _test_alarm_lines(self, lang: str, area_id: str) -> list[dict[str, Any]]:
        """Het alarm gaat af (E-541): this room's lamps and coverings as the alarm does it –
        those Doe het echt does for real, and puts back after a minute – and what happens
        elsewhere: lamps, Vesta on every speaker, the emergency message to everyone. The alarm
        itself is never switched by a test."""
        t = self.languages.text
        found = security.test_reaction(self._hm, area_id, self.clock.now())
        if found is None:
            return []
        _, here, elsewhere = found
        lines: list[dict[str, Any]] = []
        for request in here:
            line = self._test_line(lang, request, tryable=True)
            lines.append({**line, "kind": "emergency", "real": t(lang, "test.real.alarm")})
        if elsewhere:
            lines.append(
                self._test_note(
                    lang,
                    t(lang, "test.emergency.house", count=elsewhere),
                    "test.real.alarm",
                    "test.play.alarm_house",
                )
            )
        speaker = (
            "test.alarm_test.speak_here" if self.speech.can(area_id) else "test.play.alarm_say"
        )
        for text, skip in (
            ("test.alarm_test.speak", speaker),
            ("test.alarm_test.tablet", "test.play.alarm_tablet"),
            ("test.alarm_test.notify", "test.play.alarm_notify"),
            ("test.alarm_test.itself", "test.play.alarm_itself"),
        ):
            lines.append(self._test_note(lang, t(lang, text), "test.real.alarm", skip))
        return lines

    def _test_arming(
        self, lang: str, chosen: list[str], picture: Any, states: dict[str, State]
    ) -> dict[str, Any] | None:
        """Iedereen weg or Het huis slaapt (E-541): would HouseMind switch the alarm on
        itself, ask, or neither – said, never done in a test."""
        moment = "away" if "away" in chosen else "night" if "sleep" in chosen else None
        if moment is None:
            return None
        found = security.test_arming(self._hm, moment, picture, states, lang)
        if found is None:
            return None
        key, values = found
        t = self.languages.text
        line = self._test_note(lang, t(lang, key, **values), "test.real.arm", "test.play.arm")
        return {**line, "icon": "alarm", "kind": "arm"}

    def _test_line(
        self, lang: str, request: ActionRequest, tryable: bool = False
    ) -> dict[str, Any]:
        """One line of Speel het na (E-518): what HouseMind would do, how that goes for real
        – on the step it stands on now, or why not – and whether Doe het echt does it."""
        assert self.gate is not None and self.explainer is not None
        t = self.languages.text
        decision = self.gate.evaluate(request)
        reason = self.explainer.reason_phrase(lang, decision)
        text = t(
            lang,
            "ui.simulate.line",
            action=self.explainer.wanted(lang, decision),
            reason=reason,
            Reason=reason[:1].upper() + reason[1:] + ".",
        )
        why_real = self.explainer.why_not(decision, lang)
        if not tryable:
            kind, real = "emergency", t(lang, "test.real.emergency")
        elif decision.outcome is Outcome.EXECUTED:
            kind, real = "auto", t(lang, "test.real.auto")
        elif decision.outcome is Outcome.PROPOSED:
            kind, real = "ask", t(lang, "test.real.ask")
        elif decision.outcome is Outcome.OBSERVED:
            # Watched only: the step, or another system or AI alone says what it is (E-480).
            kind = "other" if decision.reason else "watch"
            real = why_real if decision.reason else t(lang, "test.real.watch")
        elif decision.reason == "people_win":
            kind, real = "hand", t(lang, "test.real.hand")
        else:
            kind, real = "not", t(lang, "test.real.not", why=why_real)
        will = tryable and self._test_can_try(request)
        skip: str | None = None
        if not tryable:
            skip = t(lang, "test.play.emergency")
        elif not will:
            why = self._test_why_not(lang, request)
            same = kind in ("other", "not") and why == why_real
            skip = t(lang, "test.play.skip_same") if same else t(lang, "test.play.skip", why=why)
        return {
            **self._test_icon(request),  # E-512
            "text": text,
            "real": real,
            "kind": kind,
            "outcome": decision.outcome.value,
            "will": will,
            "skip": skip,
        }

    def _test_icon(self, request: ActionRequest) -> dict[str, Any]:
        """The device a line is about, as it would be (E-512): a lamp going on at its stand."""
        entity = self.model.entities.get(request.entity_id)
        if entity is None:
            return {}
        function = self.world.functions.get(entity.entity_id) if self.world else None
        device = self.model.devices.get(entity.device_id) if entity.device_id else None
        icon = self.icons.of(entity, function, device.name if device else None).icon
        if icon == "lamp" and request.action.endswith("turn_on"):
            pct = request.data.get("brightness_pct", 100)
            return {"icon": icon, "pct": round(pct) if isinstance(pct, int | float) else 100}
        return {"icon": icon}

    def _test_happened(self, lang: str, area_id: str) -> list[dict[str, Any]]:
        """What HouseMind decided in this room lately (E-478): the line of the Logboek, the
        step of the gate that said no and why, and how fast it was."""
        t = self.languages.text
        zone = ZoneInfo(self.world.time_zone if self.world else "UTC")
        out: list[dict[str, Any]] = []
        for recent in sorted(self.recent.values(), key=lambda r: r.id, reverse=True):
            decision = recent.decision
            request = decision.request
            area = request.area_id or (
                found.id if (found := self.model.area_of(request.entity_id)) else None
            )
            if area != area_id:
                continue
            try:
                text = self.explain(decision, 2, lang)[0]
            except (IndexError, KeyError, ValueError):
                continue
            check = next((c for c in decision.checks if not c.ok), None)
            said_no = None
            if check is not None and self.explainer is not None:
                said_no = t(
                    lang,
                    "test.happened.check",
                    step=t(lang, f"ui.gate_step.{check.name}"),
                    why=self.explainer.why_not(decision, lang),
                )
            out.append(
                {
                    "id": recent.id,
                    "at": decision.at.astimezone(zone).strftime("%H:%M:%S"),
                    "text": text,
                    "outcome": decision.outcome.value,
                    "check": said_no,
                    "ms": round(recent.ms) if recent.ms is not None else None,
                    "test": bool(request.facts.get("test")),
                }
            )
            if len(out) >= HAPPENED:
                break
        return out

    # -- Probeer nu (E-476, B-390, B-394) ------------------------------------------------

    def _test_requests(self, area_id: str, chosen: list[str]) -> list[ActionRequest]:
        """What HouseMind would do in this room – in the chosen situation, or now."""
        assert self.world is not None and self.skills is not None and self.learning is not None
        if ALARM in chosen:  # the alarm goes off: this room's part of it (E-541)
            found = security.test_reaction(self._hm, area_id, self.clock.now())
            return found[1] if found is not None else []
        room = self._test_room(area_id)
        picture, states = apply(
            self.world.look(self.clock.now()),
            dict(self.world.states),
            area_id,
            [k for k in chosen if k not in EMERGENCIES],
            room,
            self._test_persons(),
            self.world.outside_dark_at,
        )
        twin = quiet_copy(self.skills, self.learning)
        return twin.evaluate(picture, states, rooms=[area_id])

    def _test_way_back(self, request: ActionRequest) -> ActionRequest | None:
        """How it goes back: None when it cannot – then there is no Probeer nu (E-480)."""
        assert self.world is not None
        previous = self.world.states.get(request.entity_id)
        if previous is None:
            return None
        return inverse(request, previous, self.clock.now(), None)

    def _test_can_try(self, request: ActionRequest) -> bool:
        """For real only where it may go and can come back: the gate lets a test through
        (Vrij or Voorzichtig, nothing else steers it, no house rule against it, not off or
        paused) and there is a way back (E-480)."""
        assert self.gate is not None
        if self._test_way_back(request) is None:
            return False
        asked = replace(request, layer=Layer.TEST, created_at=self.clock.now())
        return self.gate.evaluate(asked).outcome is Outcome.EXECUTED

    def _test_why_not(self, lang: str, request: ActionRequest) -> str:
        """Why Doe het echt leaves this line: no way back, or what the gate says to a test."""
        assert self.gate is not None and self.explainer is not None
        if self._test_way_back(request) is None:
            return self.languages.text(lang, "test.try.no_way_back")
        asked = replace(request, layer=Layer.TEST, created_at=self.clock.now())
        return self.explainer.why_not(self.gate.evaluate(asked), lang)

    async def test_play(
        self, lang: str, area_id: str, chosen: list[str], user_id: str | None
    ) -> str | None:
        """Doe het echt (E-518, B-444): everything HouseMind would do here in the chosen
        situation – or now – at once for real, each back as it was after a minute; what may
        not go, or cannot come back, stays with why. Returns what to say when there was
        nothing to do."""
        assert self.gate is not None and self.explainer is not None
        t = self.languages.text
        if self._hm.status.emergency in ALARMS:
            return t(lang, "test.alarm")
        if any(key in EMERGENCIES for key in chosen):
            return t(lang, "test.play.emergency")  # an emergency is only ever pretended (E-480)
        requests = self._test_requests(area_id, chosen)
        if not requests:
            return t(lang, "test.try.gone")
        now = self.clock.now()
        for old in [p for p in self._plays.values() if now - p.until > timedelta(hours=1)]:
            del self._plays[old.id]  # long over: the panel shows ten minutes back
        play = Play(
            max(self._plays, default=0) + 1, area_id, now + timedelta(seconds=TRY_S), [], {}, []
        )
        self._plays[play.id] = play
        for request in requests:
            icon = self._test_icon(request)
            test_id: int | None = None
            if not self._test_can_try(request):
                said: str | None = self._test_why_not(lang, request)
            else:
                test_id, said = await self._test_do(lang, area_id, request, user_id, play.until)
            if test_id is None:
                wanted = self.explainer.wanted(lang, self.gate.evaluate(request))
                text = wanted[:1].upper() + wanted[1:]
                play.skipped.append({**icon, "text": text, "why": said or ""})
                continue
            play.tries.append(self._tries[test_id])
            play.icons[test_id] = icon
        if ALARM in chosen:
            await self._test_alarm_rest(lang, area_id, play, user_id)
        self._test_hold()
        await self._notify()
        return None

    async def _test_alarm_rest(
        self, lang: str, area_id: str, play: Play, user_id: str | None
    ) -> None:
        """Doe het echt with the alarm (E-541): besides this room's devices, Vesta says it
        here – if this room has a speaker – and only the tester gets the message, as a test,
        for a minute. Not the rest of the house, not everyone's phone, never the alarm."""
        assert self.world is not None
        t = self.languages.text
        now = self.clock.now()
        snapshot = self.world.snapshot(now)
        message = security.test_message(self._hm)
        if message is None:
            return
        key, values, push = message
        text = t(lang, key, **values)
        if self.speech.can(area_id) and await self.speech.say(area_id, text, "test", now):
            play.notes.append(t(lang, "test.alarm_test.said"))
        person = self._person(user_id)
        if person is not None:
            topic = f"test_alarm:{play.id}"
            await self._raise(
                Kind.IMPORTANT,
                topic,
                key,
                values,
                snapshot,
                f"user:{user_id}",
                push=push,
                to=[person],
            )
            play.notes.append(t(lang, "test.alarm_test.sent"))
            self._beside(self._test_message_gone(topic, play.until), "test-alarm-message")

    async def _test_message_gone(self, topic: str, until: datetime) -> None:
        """The test message closes with the minute of the test (E-541) – counted from the
        test itself, not from when this task got its turn."""
        while self.clock.now() < until:
            await self.clock.sleep(max(0.0, (until - self.clock.now()).total_seconds()))
        await self._resolve(topic)
        await self._notify()

    async def _test_do(
        self,
        lang: str,
        area_id: str,
        request: ActionRequest,
        user_id: str | None,
        until: datetime,
    ) -> tuple[int | None, str | None]:
        """One device of Doe het echt, as a person's 'Do it' of the test layer: kept in the
        database with its way back first, so a restart still puts it back (B-394)."""
        assert self.gate is not None and self.world is not None and self.explainer is not None
        t = self.languages.text
        back = self._test_way_back(request)
        previous = self.world.states.get(request.entity_id)
        if back is None or previous is None:
            return None, t(lang, "test.try.no_way_back")
        now = self.clock.now()
        test_id = await self.db.execute_insert(
            "INSERT INTO tests(kind, area_id, entity_id, user_id, started, until, back)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                "try",
                area_id,
                request.entity_id,
                user_id,
                now.isoformat(),
                until.isoformat(),
                json.dumps({"action": back.action, "data": back.data, "done": ""}),
            ),
        )
        facts = {**request.facts, "test": test_id}
        asked = replace(request, layer=Layer.TEST, created_at=now, user_id=user_id, facts=facts)
        decision = await self.gate.submit(asked)
        await self._record(decision, previous)
        if not decision.executed:
            await self.db.execute("DELETE FROM tests WHERE id = ?", (test_id,))
            return None, self.explainer.why_not(decision, lang)
        done = _desired_state(request)
        await self.db.execute(
            "UPDATE tests SET back = ? WHERE id = ?",
            (json.dumps({"action": back.action, "data": back.data, "done": done}), test_id),
        )
        self._tries[test_id] = Try(
            test_id,
            area_id,
            request.entity_id,
            self.explainer.wanted(lang, decision),
            now,
            until,
            replace(back, layer=Layer.TEST, skill=request.skill, area_id=area_id),
            frozenset(self.model.groups.get(request.entity_id, ())),
            done,
        )
        # In the network the line flows to the room and the device, as for real work (E-518).
        self._hm.status.thoughts.append(decision)
        del self._hm.status.thoughts[:-100]
        self._beside(self._test_back_later(test_id), "test-back")
        return test_id, None

    def _test_hold(self) -> None:
        """While a Doe het echt runs, the everyday rules keep off its devices (E-518, B-445):
        the device and the lamps of its group, until its minute is over."""
        if self.gate is None:
            return
        held: dict[str, datetime] = {}
        for play in self._plays.values():
            for trial in play.tries:
                if trial.over:
                    continue
                for one in (trial.entity_id, *trial.members):
                    held[one] = max(held.get(one, trial.until), trial.until)
        self.gate.state.testing_until = held

    async def test_play_keep(self, play_id: int) -> None:
        """Laat zo: everything of this Doe het echt stays as the test left it."""
        play = self._plays.get(play_id)
        for trial in play.tries if play is not None else []:
            if self._tries.get(trial.id) is trial:
                await self.test_keep(trial.id)

    async def test_play_back(self, play_id: int) -> None:
        """Zet terug: everything of this Doe het echt back as it was, at once."""
        play = self._plays.get(play_id)
        for trial in play.tries if play is not None else []:
            if self._tries.get(trial.id) is trial:
                await self.test_back(trial.id)

    async def _test_back_later(self, test_id: int) -> None:
        """Until the minute is over – counted from the try itself, not from when this task
        got its turn."""
        trial = self._tries.get(test_id)
        # A Proef of another stand on the same lamp gives it a new minute (E-491).
        while trial is not None and not trial.over and self.clock.now() < trial.until:
            await self.clock.sleep(max(0.0, (trial.until - self.clock.now()).total_seconds()))
        # Only this try: a number whose row is gone can belong to a newer one by now.
        if trial is not None and self._tries.get(test_id) is trial:
            await self.test_back(test_id)

    async def test_keep(self, test_id: int) -> None:
        """Laat zo: it stays as the test left it."""
        trial = self._tries.get(test_id)
        if trial is not None and not trial.over:
            trial.kept = trial.over = True
            self._test_hold()
            await self.db.execute("DELETE FROM tests WHERE id = ?", (test_id,))
            await self._notify()

    async def test_back(self, test_id: int) -> None:
        """Back as it was – after the minute, or at once with Zet terug; not when someone
        changed it since: then it stays as that person left it."""
        assert self.gate is not None
        trial = self._tries.get(test_id)
        if trial is None or trial.over:
            return
        trial.over = True
        self._test_hold()
        await self.db.execute("DELETE FROM tests WHERE id = ?", (test_id,))
        if not trial.touched:
            await self._test_put_back(trial.back, test_id, trial.stand)
        await self._notify()

    async def _test_put_back(self, back: ActionRequest, test_id: int, stand: bool = False) -> None:
        assert self.gate is not None and self.world is not None
        now = self.clock.now()
        facts: dict[str, Any] = {"test": test_id, "back": True}
        if stand:
            facts["tried"] = True  # the end of a Proef: no message (E-491)
        request = replace(back, created_at=now, reason="test_back", facts=facts)
        previous = self.world.states.get(request.entity_id)
        await self._record(await self.gate.submit(request), previous)

    def _test_touched(self, change: StateChange) -> None:
        """A person or a switch changed a device of a running test: it stays as they left it,
        and it counts for nothing (E-476, E-479). Dropping off the network and coming back
        is nobody's hand (E-514)."""
        if change.source not in (Source.PERSON, Source.PHYSICAL) or change.online_only:
            return
        for trial in self._tries.values():
            if not trial.over and (
                change.entity_id == trial.entity_id or change.entity_id in trial.members
            ):
                trial.touched = True
        for quarter in self._quarters.values():
            if not quarter.over and self._in_quarter(change.entity_id, quarter):
                quarter.touched.add(change.entity_id)
                # The group it is in, too: its way back would undo this hand.
                quarter.touched |= {
                    g for g, lamps in self.model.groups.items() if change.entity_id in lamps
                }

    def _in_quarter(self, entity_id: str, quarter: Quarter) -> bool:
        area = self.model.area_of(entity_id)
        domain = entity_id.split(".", 1)[0]
        return area is not None and area.id == quarter.area_id and domain in TESTABLE[quarter.part]

    def in_test(self, entity_id: str, now: datetime) -> bool:
        """A device in a running test, or a lamp whose standen are being set: what happens to
        it counts for nothing (E-479, E-516)."""
        if self.gate is not None and self.gate.state.setting(entity_id, now):
            return True
        return any(
            not trial.over
            and now <= trial.until
            and (entity_id == trial.entity_id or entity_id in trial.members)
            for trial in self._tries.values()
        ) or any(
            not quarter.over and now <= quarter.until and self._in_quarter(entity_id, quarter)
            for quarter in self._quarters.values()
        )

    # -- Proef: a stand on the lamp itself (E-491, B-412) ---------------------------------

    def _stand_request(
        self, lamp: str, stand: int, now: datetime, levels: LightLevels | None = None
    ) -> ActionRequest | None:
        """What a lamp is told for one of its standen: its brightness and colour; a lamp that
        only goes on and off is on, or off at Gedimd when it is set so (E-492)."""
        assert self.gate is not None and self.world is not None and self.skills is not None
        entity = self.model.entities.get(lamp)
        if entity is None:
            return None
        kind = recognise(self.catalog, entity, self.gate.state.roles.get(lamp)).kind
        allowed = kind.allowed(f"{entity.domain}.turn_on") if kind is not None else None
        if allowed is None:
            return None
        levels = levels or self.light_levels
        area = self.model.area_of(lamp)
        facts: dict[str, Any] = {"stand": STANDS[stand], "tried": True}
        if "brightness_pct" not in allowed.values:
            off = stand == 0 and levels.dim_off(lamp)
            action = f"{entity.domain}.turn_off" if off else f"{entity.domain}.turn_on"
            return ActionRequest(
                Layer.TEST,
                lamp,
                action,
                {},
                reason="levels_try",
                created_at=now,
                area_id=area.id if area else None,
                facts=facts,
            )
        # A colour that follows the day's rhythm: what the rhythm says now, else what a lamp
        # gets on coming in at this part of the day.
        start = self.skill_params.get("light_on_entry", {}).get("color_temp_kelvin")
        snapshot = self.world.snapshot(now)
        part = snapshot.part or daypart(snapshot.local)
        fallback = start.get(part) if isinstance(start, dict) else start
        data = levels.stand_data(
            lamp,
            stand,
            allowed.values,
            self.skills._rhythm_kelvin(lamp, now),
            float(fallback) if isinstance(fallback, int | float) else None,
        )
        return ActionRequest(
            Layer.TEST,
            lamp,
            f"{entity.domain}.turn_on",
            data,
            reason="levels_try",
            created_at=now,
            area_id=area.id if area else None,
            facts=facts,
        )

    def stand_lamps(self, area_id: str) -> list[str]:
        """The lamps of a room as HouseMind addresses them: a group that steers, once."""
        out: list[str] = []
        for entity in sorted(self.model.entities.values(), key=lambda e: e.entity_id):
            if entity.domain != "light" or entity.hidden or entity.disabled:
                continue
            area = self.model.area_of(entity.entity_id)
            unit = self.model.unit(entity.entity_id)
            if area is None or area.id != area_id or unit is None or unit in out:
                continue
            if unit in self.gate.state.excluded if self.gate else False:
                continue
            out.append(unit)
        return out

    async def levels_try(
        self, lang: str, lamps: list[str], stand: int, user_id: str | None, draft: Any = None
    ) -> str | None:
        """Proef: these lamps on a stand, each its own way, back as they were after a minute
        or with Klaar (E-491). Another stand on a lamp being tried keeps the way back from
        before the first. Counts for nothing (E-479). Returns what to say when it did not go."""
        assert self.gate is not None and self.world is not None and self.explainer is not None
        if not 0 <= stand < len(FELT):
            return None
        now = self.clock.now()
        said: str | None = None
        levels = self.light_levels
        if draft is not None and len(lamps) == 1:
            # What the screen shows, not yet saved: the lamp shows it as it is set now.
            found = from_json(draft, levels.kelvin(lamps[0]))
            if found is not None:
                levels = LightLevels(self.model, {**levels.own, lamps[0]: found})
        for lamp in lamps:
            request = self._stand_request(lamp, stand, now, levels)
            previous = self.world.states.get(lamp)
            if request is None or previous is None:
                continue
            running = self._tries.get(self._stand_tries.get(lamp, 0))
            if running is not None and not running.over and not running.touched:
                facts = {**request.facts, "test": running.id}
                decision = await self.gate.submit(replace(request, user_id=user_id, facts=facts))
                await self._record(decision, previous)
                if decision.executed:
                    running.until = self._try_until(lamp, now)
                    running.done_state = _desired_state(request)
                else:
                    said = self.explainer.why_not(decision, lang)
                continue
            back = inverse(request, previous, now, None)
            if back is None:
                said = self.languages.text(lang, "test.try.no_way_back")
                continue
            until = self._try_until(lamp, now)
            test_id = await self.db.execute_insert(
                "INSERT INTO tests(kind, area_id, entity_id, user_id, started, until, back)"
                " VALUES(?,?,?,?,?,?,?)",
                (
                    "stand",
                    request.area_id or "",
                    lamp,
                    user_id,
                    now.isoformat(),
                    until.isoformat(),
                    json.dumps({"action": back.action, "data": back.data, "done": ""}),
                ),
            )
            facts = {**request.facts, "test": test_id}
            decision = await self.gate.submit(replace(request, user_id=user_id, facts=facts))
            await self._record(decision, previous)
            if not decision.executed:
                await self.db.execute("DELETE FROM tests WHERE id = ?", (test_id,))
                said = self.explainer.why_not(decision, lang)
                continue
            done = _desired_state(request)
            await self.db.execute(
                "UPDATE tests SET back = ? WHERE id = ?",
                (json.dumps({"action": back.action, "data": back.data, "done": done}), test_id),
            )
            self._tries[test_id] = Try(
                test_id,
                request.area_id or "",
                lamp,
                self.explainer.wanted(lang, decision),
                now,
                until,
                replace(back, layer=Layer.TEST, area_id=request.area_id),
                frozenset(self.model.groups.get(lamp, ())),
                done,
                stand=True,
            )
            self._stand_tries[lamp] = test_id
            self._beside(self._test_back_later(test_id), "test-back")
        return said

    async def levels_save(self, lamp: str, raw: Any) -> bool:
        """Bewaren (E-490): checked against what this lamp can – for this lamp only; a lamp of
        the same type without standen of its own takes them (E-493). A lamp in a group that
        steers follows its group."""
        levels = self.light_levels
        entity = self.model.entities.get(lamp)
        steered = self.model.steered_by.get(lamp)
        if entity is None or entity.domain != "light" or (steered and steered != lamp):
            return False
        found = from_json(raw, levels.kelvin(lamp))
        if found is None:
            return False
        levels.own[lamp] = found
        await self.db.set_setting(LEVELS_SETTING, levels.dump())
        return True

    async def off_wait_save(self, lamp: str, raw: Any) -> bool:
        """Uit na … zonder beweging (E-575): this lamp's own wait in whole minutes from 0 to 30,
        or None to let the room's learned wait count again. A lamp in a group that steers it
        follows its group."""
        levels = self.light_levels
        entity = self.model.entities.get(lamp)
        steered = self.model.steered_by.get(lamp)
        if entity is None or entity.domain != "light" or (steered and steered != lamp):
            return False
        if raw is None:
            if levels.waits.pop(lamp, None) is None:
                return True
        else:
            minutes = wait_from(raw)
            if minutes is None:
                return False
            levels.waits[lamp] = minutes
        await self.db.set_setting(WAIT_SETTING, levels.dump_waits())
        return True

    async def levels_reset(self, lamp: str) -> None:
        """Terug naar startwaarden: its own standen go; the group's, the same type's or the
        start count again."""
        if self.light_levels.own.pop(lamp, None) is not None:
            await self.db.set_setting(LEVELS_SETTING, self.light_levels.dump())

    async def levels_back(self, lamps: list[str]) -> None:
        """Klaar: the lamps that are being tried, back as they were."""
        for lamp in lamps:
            test_id = self._stand_tries.pop(lamp, None)
            if test_id is not None:
                await self.test_back(test_id)

    # -- the Standen window holds its lamp (E-516, B-442) ----------------------------------

    def _try_until(self, lamp: str, now: datetime) -> datetime:
        """How long a Proef lasts: a minute, or as long as the Standen window holds the lamp."""
        until = now + timedelta(seconds=TRY_S)
        held = self.gate.state.setting_until.get(lamp) if self.gate is not None else None
        return max(until, held) if held is not None else until

    def _held_lamps(self, lamp: str) -> tuple[str, ...]:
        return (lamp, *self.model.groups.get(lamp, ()))

    def levels_hold(self, lamp: str) -> None:
        """The Standen window of this lamp is open (E-516): HouseMind's own rules, proposals
        and house rules keep off it, and a Proef stays on it until Klaar or until the window
        closes. The window says so every 20 seconds; a screen that went away lets go by itself
        after a minute, and the lamp goes back as it was."""
        if self.gate is None or lamp not in self.model.entities:
            return
        now = self.clock.now()
        until = now + timedelta(seconds=HOLD_S)
        state = self.gate.state
        for one in [e for e, end in state.setting_until.items() if end <= now]:
            del state.setting_until[one]
        for one in self._held_lamps(lamp):
            state.setting_until[one] = until
        running = self._tries.get(self._stand_tries.get(lamp, 0))
        if running is not None and not running.over:
            running.until = max(running.until, until)

    async def levels_release(self, lamp: str) -> None:
        """The window closed: the lamp back as it was, and HouseMind takes it over again."""
        if self.gate is not None:
            for one in self._held_lamps(lamp):
                self.gate.state.setting_until.pop(one, None)
        await self.levels_back([lamp])

    # -- the testkwartier (E-477, B-391) ---------------------------------------------------

    def _test_parts(self, area_id: str) -> list[str]:
        """The parts this room can test for real: it has their devices, and they are on."""
        domains = {
            e.split(".", 1)[0]
            for e, entity in self.model.entities.items()
            if not (entity.hidden or entity.disabled)
            and (area := self.model.area_of(e)) is not None
            and area.id == area_id
        }
        return [
            part
            for part, own in TESTABLE.items()
            if domains & set(own) and part not in self.parts_off
        ]

    async def test_quarter(
        self, area_id: str, part: str, minutes: int, user_id: str | None
    ) -> None:
        """Start a testkwartier: this part of this room on Autonomous, with the real sensors."""
        assert self.gate is not None
        if part not in self._test_parts(area_id) or minutes not in QUARTER_MINUTES:
            return
        if any(
            not q.over and q.area_id == area_id and q.part == part for q in self._quarters.values()
        ):
            return
        now = self.clock.now()
        until = now + timedelta(minutes=minutes)
        test_id = await self.db.execute_insert(
            "INSERT INTO tests(kind, area_id, entity_id, user_id, started, until, back)"
            " VALUES(?,?,?,?,?,?,?)",
            ("quarter", area_id, part, user_id, now.isoformat(), until.isoformat(), "{}"),
        )
        self._quarters[test_id] = Quarter(test_id, area_id, part, until, {}, {}, set())
        self.gate.state.tests[(part, area_id)] = (until, test_id)
        self._beside(self._quarter_later(test_id), "test-quarter")
        await self._notify()

    async def _quarter_later(self, test_id: int) -> None:
        quarter = self._quarters.get(test_id)
        if quarter is not None:
            await self.clock.sleep(max(0.0, (quarter.until - self.clock.now()).total_seconds()))
        await self.test_quarter_end(test_id)

    async def test_quarter_end(self, test_id: int) -> None:
        """The end of a testkwartier – its time, or Stop: the step goes back, and every device
        HouseMind changed in it and nobody changed since goes back as it was."""
        assert self.gate is not None
        quarter = self._quarters.get(test_id)
        if quarter is None or quarter.over:
            return
        quarter.over = True
        if self.gate.state.tests.get((quarter.part, quarter.area_id), (None, None))[1] == test_id:
            del self.gate.state.tests[(quarter.part, quarter.area_id)]
        await self.db.execute("DELETE FROM tests WHERE id = ?", (test_id,))
        for entity_id, back in quarter.changed.items():
            if entity_id not in quarter.touched:
                await self._test_put_back(back, test_id)
        await self._notify()

    async def _test_recorded(self, decision: Decision, previous: State | None) -> None:
        """HouseMind changed a device in a testkwartier: remember how it was before, once."""
        request = decision.request
        quarter = self._quarters.get(int(request.facts.get("test") or 0))
        if quarter is None or quarter.over or request.facts.get("back") or previous is None:
            return
        if request.entity_id in quarter.changed:
            return
        back = inverse(request, previous, self.clock.now(), None)
        if back is None:
            return
        quarter.changed[request.entity_id] = replace(
            back, layer=Layer.TEST, skill=request.skill, area_id=quarter.area_id
        )
        quarter.done[request.entity_id] = _desired_state(request)
        devices = {
            e: {"action": b.action, "data": b.data, "done": quarter.done[e]}
            for e, b in quarter.changed.items()
        }
        await self.db.execute(
            "UPDATE tests SET back = ? WHERE id = ?",
            (json.dumps({"devices": devices}), quarter.id),
        )

    async def test_stop_all(self) -> None:
        """Stop test en zet alles terug (E-478)."""
        for test_id in [t.id for t in self._tries.values() if not t.over]:
            await self.test_back(test_id)
        for test_id in [q.id for q in self._quarters.values() if not q.over]:
            await self.test_quarter_end(test_id)

    def _test_halt(self) -> None:
        """An emergency (E-480): every test stops at once, and nothing is put back – the
        emergency reactions go first."""
        halted = [t for t in self._tries.values() if not t.over]
        halted += [q for q in self._quarters.values() if not q.over]  # type: ignore[misc]
        if not halted:
            return
        for test in halted:
            test.over = True
        if self.gate is not None:
            self.gate.state.tests.clear()
            self.gate.state.testing_until.clear()
        self._beside(self.db.execute("DELETE FROM tests"), "test-halt")

    def test_running(self, lang: str) -> list[dict[str, Any]]:
        """The testkwartieren that run, for the bar at the top (E-477)."""
        zone = ZoneInfo(self.world.time_zone if self.world else "UTC")
        t = self.languages.text
        return [
            {
                "id": q.id,
                "room": self.model.areas[q.area_id].name if q.area_id in self.model.areas else "",
                "part": t(lang, f"ui.domain.{q.part}"),
                "until": q.until.astimezone(zone).strftime("%H:%M"),
            }
            for q in sorted(self._quarters.values(), key=lambda q: q.until)
            if not q.over
        ]

    def _test_quarter_view(self, lang: str, area_id: str) -> dict[str, Any]:
        zone = ZoneInfo(self.world.time_zone if self.world else "UTC")
        t = self.languages.text
        parts = []
        for part in self._test_parts(area_id):
            running = next(
                (
                    q
                    for q in self._quarters.values()
                    if not q.over and q.area_id == area_id and q.part == part
                ),
                None,
            )
            parts.append(
                {
                    "key": part,
                    "name": t(lang, f"ui.domain.{part}"),
                    "running": (
                        {
                            "id": running.id,
                            "until": running.until.astimezone(zone).strftime("%H:%M"),
                        }
                        if running is not None
                        else None
                    ),
                }
            )
        return {"parts": parts, "minutes": list(QUARTER_MINUTES), "standard": QUARTER_DEFAULT}

    async def _test_start(self) -> None:
        """After a restart (B-394): a test that was still open is over, and what it did goes
        back – only where the device still stands as the test left it."""
        assert self.world is not None
        rows = await self.db.fetchall("SELECT id, entity_id, back FROM tests")
        for row in rows:
            await self.db.execute("DELETE FROM tests WHERE id = ?", (row["id"],))
            try:
                back = json.loads(str(row["back"]))
            except ValueError:
                continue
            devices = back.get("devices") or {str(row["entity_id"]): back}
            for entity_id, way in devices.items():
                state = self.world.states.get(entity_id)
                if not way.get("done") or state is None or state.state != way["done"]:
                    continue
                request = ActionRequest(
                    Layer.TEST,
                    entity_id,
                    str(way["action"]),
                    dict(way.get("data") or {}),
                    area_id=(found.id if (found := self.model.area_of(entity_id)) else None),
                )
                await self._test_put_back(request, int(row["id"]))


def _desired_state(request: ActionRequest) -> str:
    """The state a test leaves the device in: after a restart only that goes back (B-394)."""
    verb = request.action.split(".", 1)[1]
    if verb == "turn_on":
        return "on"
    if verb == "turn_off":
        return "off"
    if verb == "open_cover":
        return "open"
    if verb == "close_cover":
        return "closed"
    if verb == "set_cover_position":
        position = request.data.get("position")
        return "closed" if position == 0 else "open"
    return ""
