"""Vesta, the voice of the house (E-220 … E-232, chapter 17): her words for each moment, her
voice and audio, her questions and her state – the Orb – and the morning briefing and the
bedtime check she speaks (E-19, E-107)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from housemind.ai.service import AIService
from housemind.brain.air import AirWatch, ppm_text
from housemind.brain.briefing import (
    PARTS,
    agenda_line,
    findings,
    findings_line,
    weather_entity,
    weather_line,
)
from housemind.brain.explain import Explainer
from housemind.brain.names import Names, PlainNames, sentence_case, tidy
from housemind.brain.notify import (
    SAFETY_TOPICS,
    Kind,
    NotificationRouter,
    Person,
    in_quiet,
)
from housemind.brain.proposals import Proposal, Proposals
from housemind.brain.speech import Speech, tts_engine
from housemind.brain.voice import EMERGENCY as EMERGENCY_WEIGHT
from housemind.brain.voice import (
    WEIGHTS,
    Line,
    Memory,
    Message,
    Moment,
    Phrases,
    choose,
    compose,
    daypart,
)
from housemind.brain.voice import Listener as VestaListener
from housemind.core.clock import Clock
from housemind.core.model import HouseModel, Source, StateChange
from housemind.energy.manager import EnergyManager
from housemind.ha.adapter import HAConfig, HomeAssistant, Voice
from housemind.i18n.catalog import Languages
from housemind.knowledge.area_roles import SLEEPING_ROLES
from housemind.knowledge.catalog import Step
from housemind.memory.database import Database
from housemind.parts import Part
from housemind.safety.gate import Decision, Layer, Outcome
from housemind.world.house_mode import HouseMode
from housemind.world.world import World, WorldState

if TYPE_CHECKING:
    from housemind.runtime import Status, VestaOut

_LOGGER = logging.getLogger(__name__)

# The usual form of a language for a voice, when the house's country has none (B-124).
VOICE_LOCALES = {"en": "en-US", "nl": "nl-NL", "de": "de-DE", "fr": "fr-FR"}

ARRIVAL_WINDOW = timedelta(minutes=5)  # a greeting comes this soon after coming home
ASK_AGAIN = timedelta(hours=1)  # the same question not twice within the hour (E-252)
GREET_GAP = timedelta(minutes=2)  # one greeting per room for people coming home together
QUESTION_SHOW = timedelta(hours=1)  # an open question colours the Orb amber this long
THINK_SHOW = timedelta(seconds=4)
VESTA_AI_WAIT = 2.5  # seconds: longer, and Vesta says it in her own words (B-79)
VESTA_AI_KEEP = timedelta(days=1)  # the AI's words for the same message, not asked again
VESTA_NOISE = frozenset({"rate_limit", "stale_context", "unknown_entity", "unknown_device_kind"})
_SENTENCE = re.compile(r"(?<=[.!?])\s+")  # the first sentence of a message, for one line
# Never as a reason in Home Assistant, where everyone can read it (E-389, B-300): what may not
# go to an AI either – a lock, an alarm and its code, what comes from the calendar (B-173).
HA_PRIVATE = ("lock", "alarm", "code", "calendar", "agenda", "waste")


def _for_ai(facts: dict[str, str]) -> dict[str, str]:
    """Vesta's facts for an AI: the names as in HA (the AI service turns them into labels and
    plain names come back, E-15, E-248), without the forms only her own words need."""
    out = {
        k: v
        for k, v in facts.items()
        if not k.endswith(("_ha", "_acc", "_dat", "_ai")) and k != "room"
    }
    for key in ("device", "area", "text"):
        if f"{key}_ha" in facts:
            out[key] = facts[f"{key}_ha"]
        if f"{key}_ai" in facts:
            out[key] = facts[f"{key}_ai"]  # the version without calendar titles or locks
    return out


def voice_locale(language: str, spoken: list[str], country: str | None) -> str | None:
    """The engine's form of ``language`` ("en" → "en-US"), or None when it does not speak it."""
    options = [code for code in spoken if code.split("-")[0].lower() == language]
    wanted = [f"{language}-{country}" if country else "", VOICE_LOCALES.get(language, ""), language]
    for want in wanted:
        for code in options:
            if want and code.lower() == want.lower():
                return code
    return sorted(options)[0] if options else None


class VestaPart(Part):
    """Vesta, the voice of the house (E-220 … E-232, chapter 17): her words for each moment, her"""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        _arrivals: dict[str, tuple[datetime, str | None]]
        _asked: dict[tuple[str | None, str], datetime]
        _briefing_data: tuple[datetime, list[dict[str, Any]], list[dict[str, Any]]] | None
        _air: AirWatch | None

        def _calendars(self) -> list[str]: ...

        _greeted: dict[str, datetime]
        _integration_record: Any

        def _language(self) -> str: ...
        def _levels_home(self) -> list[int]: ...
        async def _notify(self) -> None: ...

        _orb_timer: asyncio.TimerHandle | None
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
        _vesta_last: dict[tuple[str | None, str], int]
        _vesta_name: str | None
        _vesta_out: list[VestaOut]
        _vesta_people: list[VestaOut]
        _vesta_rng: random.Random
        _vesta_ai_said: dict[str, tuple[datetime, str]]
        _voices: dict[tuple[Voice, str], Voice]
        _woke: datetime | None
        activated: Any
        ai: AIService
        area_roles: dict[str, str]
        clock: Clock
        config: HAConfig | None
        db: Database

        async def setting(self, key: str) -> Any: ...

        energy: EnergyManager | None
        ha: HomeAssistant
        languages: Languages
        model: HouseModel
        names: Names

        async def notifications(self) -> list[dict[str, Any]]: ...
        async def open_proposals(self) -> list[Proposal]: ...

        openings: set[str]

        def paused_until(self) -> datetime | None: ...

        proposals: Proposals
        router: NotificationRouter | None
        speech: Speech
        status: Status
        tablet_areas: Callable[[], set[str]]
        vesta_explainer: Explainer | None
        vesta_phrases: Phrases
        world: World | None

    async def say(self, snapshot: WorldState, area_id: str, text: str, kind: str) -> bool:
        """Say something in a room – by the rules of E-19 unless it is an emergency."""
        if kind != "emergency" and not self.activated:
            return False  # "Nog niet actief" (E-275)
        if kind != "emergency":
            sleeping = snapshot.mode is HouseMode.SLEEP
            levels = self._levels_home()
            chosen = [
                int(p.preferences["spoken_per_day"])
                for p in self._people
                if p.home and p.level == 4 and isinstance(p.preferences.get("spoken_per_day"), int)
            ]
            if not await self.speech.allowed(
                area_id,
                levels,
                sleeping,
                snapshot.now,
                snapshot.local,
                min(chosen) if chosen else None,
            ):
                return False
            if not snapshot.occupied(area_id):
                return False
        elif not self.speech.can(area_id):
            return False
        return await self.speech.say(area_id, text, kind, snapshot.now)

    def _home_prefs(self) -> tuple[set[str], bool]:
        """What everyone at home wants in the briefing (only what all want), and whether
        anyone wants the bedtime check."""
        home = [p for p in self._people if p.home and p.level >= 2]
        parts = set(PARTS)
        check = False
        for person in home:
            chosen = person.preferences.get("briefing")
            if isinstance(chosen, dict):
                parts &= {k for k in PARTS if chosen.get(k, True)}
            check = check or bool(person.preferences.get("bedtime_check", True))
        return parts, check

    async def _briefing_reads(self, snapshot: WorldState) -> tuple[list[Any], list[Any]]:
        """The weather and today's events from HA – at most every half hour."""
        assert self.world is not None
        if self._briefing_data and snapshot.now - self._briefing_data[0] < timedelta(minutes=30):
            return self._briefing_data[1], self._briefing_data[2]
        weather = weather_entity(self.model, self.world.states)
        forecast = await self.ha.daily_forecast(weather) if weather else []
        start = snapshot.local.replace(hour=0, minute=0, second=0, microsecond=0)
        events: list[dict[str, Any]] = []
        for entity_id in self._calendars():
            events += await self.ha.calendar_events(entity_id, start, start + timedelta(days=1))
        self._briefing_data = (snapshot.now, forecast, events)
        return forecast, events

    async def briefing(
        self, lang: str, parts: set[str], snapshot: WorldState, for_ai: bool = False
    ) -> str | None:
        """The morning briefing in a few sentences (E-107). ``for_ai``: the version an AI may
        word – the calendar only as "something at 14:30", no lock (QA 2, B-173)."""
        assert self.world is not None
        t = self.languages.text
        forecast, events = await self._briefing_reads(snapshot)
        lines: list[str] = []
        if "weather" in parts:
            weather = weather_entity(self.model, self.world.states)
            current = self.world.states.get(weather) if weather else None
            line = weather_line(t, lang, forecast, current, snapshot.local)
            if line:
                lines.append(line)
        if "agenda" in parts:
            line = agenda_line(t, lang, events, snapshot.local.tzinfo, titles=not for_ai)
            if line:
                lines.append(line)
        if "energy" in parts and self.energy is not None and self.energy.setup.has_any:
            from housemind.server import views  # the same sentence as on the energy screen

            found = await views.energy(self._hm, lang, 2)
            if (
                found
                and found.get("sentence")
                and not (await self.energy.savings(snapshot.now))["measuring"]
            ):
                lines.append(str(found["sentence"]))
        everything = findings(
            self.model,
            self.world.states,
            {},
            set(),
            self.openings,
            not_lamps=self.world.status_led_ids,
        )
        open_now = [
            i for i in everything if i[0] != "light" and not (for_ai and i[0] == "unlocked")
        ]
        if open_now:
            lines.append(findings_line(t, lang, open_now, "briefing.attention"))
        worst = self._air.last_night(snapshot.local.date()) if self._air is not None else None
        if worst is not None:  # stale air last night (E-532)
            area = self.model.areas.get(worst[0])
            where = self.names.where(worst[0], lang) or (area.name if area else "")
            lines.append(t(lang, "briefing.air_night", where=where, value=ppm_text(worst[1], lang)))
        if not lines:
            return None
        return " ".join([t(lang, "briefing.greeting"), *lines])

    async def _morning(self, snapshot: WorldState) -> None:
        """Speak the briefing once, in the first occupied room with a speaker after waking."""
        if self._woke is None or self._woke.date() != snapshot.local.date():
            return
        if snapshot.local - self._woke > timedelta(minutes=90):
            return
        if await self.db.get_setting("briefing_said") == snapshot.local.date().isoformat():
            return
        parts, _ = self._home_prefs()
        rooms = [a for a in self.speech.devices if snapshot.occupied(a)]
        if not parts or not rooms:
            return
        text = await self.briefing(self._language(), parts, snapshot)
        if text:
            greeting = self.languages.text(self._language(), "briefing.greeting")
            safe = await self.briefing(self._language(), parts, snapshot, for_ai=True) or ""
            text = await self._vesta_room(
                snapshot,
                "morning",
                {
                    "text": text.removeprefix(greeting).strip(),
                    "text_ai": safe.removeprefix(greeting).strip(),  # B-173
                },
                f"morning:{snapshot.local.date().isoformat()}",
                40,
            )
        if text and await self.say(snapshot, rooms[0], text, "briefing"):
            await self.db.set_setting("briefing_said", snapshot.local.date().isoformat())

    async def _going_to_bed(self, change: StateChange, snapshot: WorldState) -> None:
        """Someone goes to bed: "is everything closed and off?" – once an evening (E-107)."""
        assert self.world is not None
        local = snapshot.local
        if not (local.hour >= 21 or local.hour < 3):
            return
        entity = self.model.entities.get(change.entity_id)
        area = self.model.area_of(change.entity_id)
        if entity is None or area is None or self.area_roles.get(area.id) not in SLEEPING_ROLES:
            return
        if change.new is None or change.new.state != "on":
            return
        by_person = change.source in (Source.PERSON, Source.PHYSICAL) and entity.domain == "light"
        sensed = entity.domain == "binary_sensor" and entity.device_class in (
            "motion",
            "presence",
            "occupancy",
        )
        if not (by_person or sensed):
            return
        evening = (local.date() - timedelta(days=1) if local.hour < 3 else local.date()).isoformat()
        if await self.db.get_setting("bedtime_checked") == evening:
            return
        _, wanted = self._home_prefs()
        if not wanted:
            return
        await self.db.set_setting("bedtime_checked", evening)
        occupied = {a for a, o in snapshot.occupancy.items() if o.occupied}
        items = findings(
            self.model,
            self.world.states,
            self.area_roles,
            occupied,
            self.openings,
            not_lamps=self.world.status_led_ids,
        )
        lang = self._language()
        t = self.languages.text
        if not items:
            ok = await self._vesta_room(snapshot, "bedtime_ok", {}, f"bedtime:{evening}", 40)
            await self.say(snapshot, area.id, ok, "bedtime")
            return
        text = findings_line(t, lang, items, "bedtime.found")
        # Never a lock to an AI (B-173): it words only the rest, or she keeps her own words.
        safe = [i for i in items if i[0] != "unlocked"]
        spoken = await self._vesta_room(
            snapshot,
            "bedtime_found",
            {
                "text": text,
                "text_ai": findings_line(t, lang, safe, "bedtime.found") if safe else "",
            },
            f"bedtime:{evening}",
            70,
        )
        if not await self.say(snapshot, area.id, spoken, "bedtime"):
            await self._raise(
                Kind.IMPORTANT, f"bedtime:{evening}", "notify.bedtime", {"text": text}, snapshot
            )

    def _refresh_orb(self) -> None:
        self.status.orb = self.orb_state()

    async def vesta_line(
        self,
        user_id: str | None,
        level: int,
        admin: bool,
        language: str,
        moment: Moment = Moment.TAP,
        listen: bool = False,
        offer: bool = False,
        choices: bool = False,
    ) -> Line:
        """What Vesta says to this person now: first what matters (B-78), then her words
        (B-79). What she said is remembered, so she never repeats herself. ``offer``: a tap
        where she can listen – like a butler she first asks what she can do (E-225);
        ``choices``: without an AI she offers her choices instead of an open question (E-349)."""
        lang = self.languages.resolve(language)
        row = await self.db.fetchone("SELECT preferences FROM people WHERE user_id = ?", (user_id,))
        prefs = json.loads(row["preferences"] or "{}") if row is not None else {}
        listener = VestaListener(user_id, level, admin, prefs.get("address") == "u", lang)
        now = self.clock.now()
        memory = await self._vesta_memory(user_id, now)
        messages = await self._vesta_messages(lang, level, user_id)
        person = self._person(user_id)
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        local = now.astimezone(tz)
        if person is not None and self.router is not None:
            # Her questions come out of the same budget as all others (E-294).
            router = self.router
            messages = [
                m
                for m in messages
                if m.proposal_id is None
                or router.may_ask(person, f"proposal:{m.proposal_id}", local, count=False)
            ]
            memory.budgeted = True
        chosen = choose(messages, listener, memory, moment, offer)
        state, reason = self.vesta_state(False, admin, level, user_id)
        if moment is Moment.TAP:
            # What she says on a tap always fits her colour (E-132, E-284).
            chosen = await self._fit_colour(chosen, messages, state, reason, lang)
        quiet = state if state in ("paused", "sleeping") else "calm"
        line = compose(
            chosen,
            listener,
            memory,
            self.vesta_phrases,
            moment,
            self._vesta_rng,
            quiet,
            listen,
            self._vesta_context(lang),
            offer,
            choices,
        )
        if (chosen or moment is Moment.ARRIVAL) and not any(m.weight >= 100 for m in chosen):
            better = await self._vesta_ai(chosen, listener, moment)
            if better:
                line = replace(line, text=better)
        if line.question is not None and person is not None and self.router is not None:
            self.router.may_ask(person, f"proposal:{line.question}", local)  # counted (E-294)
        for topic, variant, key, asks in line.said:
            await self.db.execute(
                "INSERT INTO vesta_said(at, user_id, topic, variant, key, question) "
                "VALUES(?,?,?,?,?,?)",
                (now.isoformat(), user_id, topic, variant, key, int(asks)),
            )
        return line

    def vesta_name(self, language: str) -> str:
        """Her name: the one the administrator gave her, else Vesta (E-220)."""
        return self._vesta_name or self.vesta_phrases.name(language)

    async def _vesta_room(
        self, snapshot: WorldState, topic: str, facts: dict[str, str], key: str, weight: int
    ) -> str:
        """Her words for a shared speaker (B-79, B-80): for the calmest level of who is home,
        "u" only when everyone home wants that, in the house's language; never the same line
        twice in a row; an AI only when it is not an emergency."""
        home = [p for p in self._people if p.home]
        formal = bool(home) and all(p.preferences.get("address") == "u" for p in home)
        level = min((p.level for p in home), default=2)
        listener = VestaListener(None, level, False, formal, self._language())
        return await self._vesta_one(listener, topic, facts, key, weight, snapshot.now)

    async def _vesta_one(
        self,
        listener: Any,
        topic: str,
        facts: dict[str, str],
        key: str,
        weight: int,
        now: datetime,
    ) -> str:
        message = Message(topic, weight, self._plain_facts(facts, listener.language), key)
        memory = await self._vesta_memory(listener.user_id, now)
        line = compose(
            [message],
            listener,
            memory,
            self.vesta_phrases,
            Moment.BY_ITSELF,
            self._vesta_rng,
            context=self._vesta_context(listener.language),
        )
        if weight < 100:
            better = await self._vesta_ai([message], listener, Moment.BY_ITSELF)
            if better:
                line = replace(line, text=better)
        for said_topic, variant, said_key, asks in line.said:
            await self.db.execute(
                "INSERT INTO vesta_said(at, user_id, topic, variant, key, question) "
                "VALUES(?,?,?,?,?,?)",
                (now.isoformat(), listener.user_id, said_topic, variant, said_key, int(asks)),
            )
        return line.text or facts.get("text", "")

    async def _vesta_now(
        self, text_key: str, params: dict[str, Any], topic: str, snapshot: WorldState
    ) -> None:
        """Something that asks for someone now (B-80): Vesta says it once, in her own words,
        in an occupied room with a speaker – within the day's limits – and on the open app of
        whoever did not hear it there (E-223). Only what she has her own words for."""
        if text_key in ("notify.bedtime", "notify.proposal"):
            return  # the bedtime check and questions have their own moments
        if self._hidden_for(None, topic, params):
            return  # the house switched it off with Niet meer tonen (E-412): she says nothing
        kind, facts = self._vesta_topic(self._language(), text_key, params)
        if kind == "other":
            return
        # In someone's quiet hours she only speaks about safety (E-290); on Carefree only
        # about safety, and only to who said yes to messages in danger (E-17, E-224, E-289).
        safety = topic.split(":", 1)[0] in SAFETY_TOPICS
        local = snapshot.local
        heard: set[str] = set()  # who does not get it on their phone, or already heard it
        for person in self._people:
            if self._hidden_for(person.user_id, topic, params):
                heard.add(person.user_id)  # put off with Later (E-372): not on their phone
            elif person.level < 2:
                if not (safety and person.consents.get("emergency_push") is True):
                    heard.add(person.user_id)
            elif not safety and in_quiet(person, local):
                heard.add(person.user_id)
        quiet_home = any(in_quiet(p, local) for p in self._people if p.home)
        rooms = [a for a in self.speech.devices if snapshot.occupied(a)]
        if quiet_home and not safety:
            rooms = []
        if rooms:
            text = await self._vesta_room(snapshot, kind, facts, f"now:{topic}", 70)
            if await self.say(snapshot, rooms[0], text, "now"):
                heard |= {p.user_id for p in self._people if p.home}
        await self._vesta_phones(kind, facts, f"now:{topic}", 70, heard)

    async def _vesta_phones(
        self, topic: str, facts: dict[str, str], key: str, weight: int, heard: set[str]
    ) -> None:
        """Vesta on the phones where HouseMind is open (E-223): each person in their own
        language, level and form; the phone itself only plays her voice when the app is
        open and visible – otherwise the push message does the work."""
        if not self._vesta_people:
            return
        now = self.clock.now()
        for person in self._people:
            if person.user_id in heard:
                continue
            listener = VestaListener(
                person.user_id,
                person.level,
                person.is_admin,
                person.preferences.get("address") == "u",
                self.languages.resolve(person.language),
            )
            text = await self._vesta_one(listener, topic, facts, key, weight, now)
            audio = await self.vesta_audio(text, listener.language)
            name = self.vesta_name(listener.language)
            for deliver in list(self._vesta_people):
                await deliver(person.user_id, text, audio, name)

    async def _greet(self, snapshot: WorldState) -> None:
        """Someone came home (E-224, B-81): Vesta greets in the first room where HouseMind
        sees them, within 5 minutes, on the speaker or wall tablet there – also on level 1,
        not counted for the day, never while the house sleeps. No speaker there: no greeting."""
        if not self._arrivals or self.world is None or not self.activated:
            return
        now = snapshot.now
        for person, (since, user_id) in list(self._arrivals.items()):
            if now - since > ARRIVAL_WINDOW or snapshot.mode is HouseMode.SLEEP:
                del self._arrivals[person]
                continue
            seen = [
                (o.last_activity, a)
                for a, o in snapshot.occupancy.items()
                if o.occupied
                and o.last_activity is not None
                and o.last_activity >= since - timedelta(seconds=30)
                and self.world.indoors(a)
            ]
            if not seen:
                continue  # not seen inside yet
            del self._arrivals[person]
            area = min(seen)[1]
            speaker, tablet = self.speech.can(area), area in self.tablet_areas()
            last = self._greeted.get(area)
            if not (speaker or tablet) or (last is not None and now - last < GREET_GAP):
                continue
            self._greeted[area] = now  # before her words: one greeting, also side by side
            who = next((p for p in self._people if p.user_id == user_id), None)
            line = await self.vesta_line(
                user_id,
                who.level if who else 2,
                who.is_admin if who else False,
                who.language if who else self._language(),
                Moment.ARRIVAL,
            )
            language = who.language if who else self._language()
            if speaker:
                voice = await self.voice_for(language)
                await self.speech.say(area, line.text, "greeting", now, voice)
            else:
                audio = await self.vesta_audio(line.text, language)
                name = self.vesta_name(self._language())
                for listener in list(self._vesta_out):
                    await listener(area, line.text, audio, name)

    async def vesta_voice(self) -> Voice | None:
        """Vesta's voice (E-230): the administrator's choice, else the voice of HouseMind's
        own Assist pipeline (or the preferred one), else the first engine in HA."""
        chosen = await self.setting("vesta_voice")
        if isinstance(chosen, dict) and chosen.get("engine"):
            return Voice(str(chosen["engine"]), chosen.get("language"), chosen.get("voice"))
        own = (await self._integration_record()).get("pipeline")  # her own pipeline (E-280)
        if own:
            mine = next((p for p in await self.ha.assist_pipelines() if p.get("id") == own), None)
            if mine and mine.get("tts_engine"):
                return Voice(
                    str(mine["tts_engine"]), mine.get("tts_language"), mine.get("tts_voice")
                )
        voice = await self.ha.assist_voice()
        if voice is not None:
            return voice
        engine = tts_engine(self.model, self.world.states if self.world else {})
        return Voice(engine) if engine else None

    async def hear(self, audio: bytes, language: str) -> str | None:
        """What someone said to Vesta, through HA's speech recognition (B-83)."""
        return await self.ha.speech_to_text(audio, self.languages.resolve(language))

    def vesta_says(self, language: str, topic: str, user_id: str | None = None) -> str:
        """One short line of hers about herself ("I did not catch that"), in this person's
        form, never the same twice in a row."""
        lang = self.languages.resolve(language)
        who = next((p for p in self._people if p.user_id == user_id), None)
        formal = bool(who and who.preferences.get("address") == "u")
        lines = self.vesta_phrases.variants(lang, topic, formal)
        if not lines:
            return ""
        last = self._vesta_last.get((user_id, topic))
        options = [i for i in range(len(lines)) if i != last] or [0]
        index = self._vesta_rng.choice(options)
        self._vesta_last[(user_id, topic)] = index
        return lines[index].replace("{name}", self.vesta_name(lang))

    async def vesta_asks(
        self,
        user_id: str | None,
        level: int,
        admin: bool,
        tablet: bool = False,
        every: bool = False,
    ) -> list[dict[str, Any]]:
        """The questions that wait for this person's answer, most important first (E-252):
        tasks (important messages), then proposals and, for administrators, automations.
        Not on level 1 (no messages there), never an emergency (that has its own way), and
        not what this person was asked in the last hour. A wall tablet: only the house's."""
        if level < 2:
            return []
        now = self.clock.now()
        tasks: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        for row in await self.notifications():
            topic, kind = str(row["topic"]), str(row["kind"])
            concerns = str(row.get("concerns") or "home")
            if kind == "emergency" or (
                tablet and concerns != "home" and not concerns.startswith("area:")
            ):
                continue
            if concerns.startswith("user:") and concerns != f"user:{user_id}":
                continue
            if concerns == "admins" and not admin:
                continue
            key = f"n{row['id']}"
            asked = self._asked.get((user_id, key))
            if not every and asked is not None and now - asked < ASK_AGAIN:
                continue  # "every": all of them, for "Moet ik nog iets doen?" (E-245)
            ask: dict[str, Any] = {
                "key": key,
                "notification": int(row["id"]),
                "proposal": None,
                "text_key": str(row["text_key"]),
                "params": json.loads(str(row["params"] or "{}")),
            }
            if topic.startswith("proposal:"):
                proposal = await self.proposals.get(int(topic.split(":", 1)[1]))
                if proposal is None:
                    continue
                person = None if tablet else self._person(user_id)
                if person is not None and self.router is not None:
                    tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
                    local = now.astimezone(tz)
                    # Within the person's question budget; shown counts (E-252, E-294).
                    if not self.router.may_ask(person, topic, local, count=not every):
                        continue
                rule = bool(proposal.skill and proposal.area_id)
                ask.update(
                    kind="proposal",
                    proposal=proposal.id,
                    actions=["do", "not_now", *(["from_now", "never"] if rule else [])],
                )
                decisions.append(ask)
            elif topic.startswith(("automation:", "restore:")):
                if admin:
                    kind = "automation" if topic.startswith("automation:") else "restore"
                    proof = str(row["text_key"]) == "notify.proof_automation"  # E-460
                    answers = ["yes", "later", "no"] if proof else ["yes", "no"]
                    decisions.append({**ask, "kind": kind, "actions": answers})
            elif topic.startswith("thumbs:"):
                decisions.append({**ask, "kind": "thumbs", "actions": ["good", "bad"]})
            elif topic.startswith("rule_suggest:"):
                # A house rule the AI suggests (E-417): for an administrator, within budget.
                person = self._person(user_id)
                if admin and person is not None and self.router is not None:
                    tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
                    if self.router.may_ask(person, topic, now.astimezone(tz), count=not every):
                        decisions.append({**ask, "kind": "rule_suggest", "actions": ["yes", "no"]})
            elif topic.startswith("unusual:"):
                # Something unusual (E-416): 'Zal ik hem uitzetten?', within the person's
                # question budget (E-294).
                person = self._person(user_id)
                if person is not None and self.router is not None:
                    tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
                    if not self.router.may_ask(person, topic, now.astimezone(tz), count=not every):
                        continue
                decisions.append({**ask, "kind": "unusual", "actions": ["yes", "no"]})
            elif topic.startswith("knowledge:"):
                # Klopt or Klopt niet: only for an administrator on Geïnformeerd, who has no
                # page for it, within their question budget (E-207, E-294, B-360).
                person = self._person(user_id)
                if admin and level == 2 and person is not None and self.router is not None:
                    tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
                    if self.router.may_ask(person, topic, now.astimezone(tz), count=not every):
                        decisions.append({**ask, "kind": "knowledge", "actions": ["yes", "no"]})
            elif (
                topic in ("vacation_suggestion", "care_consent")
                or (topic in ("calendar_consent", "battery_remote", "battery_calibrate") and admin)
                or (topic.startswith("alarm_arm:") and not str(row["text_key"]).endswith("_code"))
                or (topic.startswith("alarm_self_offer:") and admin)  # E-540
            ):
                decisions.append({**ask, "kind": "vacation", "actions": ["yes", "no"]})
            elif kind == "important":
                # The same as on the card and after a tap (E-403); Niet meer tonen only for an
                # administrator, as it is for the whole house (E-412).
                answers = ["seen", "snooze", *(["mute"] if admin else [])]
                tasks.append({**ask, "kind": "task", "actions": answers})
        return tasks + decisions

    async def vesta_ask_open(self, user_id: str | None, ask: dict[str, Any]) -> bool:
        """Whether a question she offered this person still waits for an answer (E-464). It
        stays until it is answered, put off or closed: that it now counts as asked, or that the
        person's questions for today are used up, takes nothing off the screen."""
        now = self.clock.now()
        row = await self.db.fetchone(
            "SELECT topic, params FROM notifications WHERE id = ? AND resolved_at IS NULL "
            "AND (snoozed_until IS NULL OR snoozed_until < ?)",
            (ask["notification"], now.isoformat()),
        )
        if row is None:
            return False
        if self._hidden_for(
            user_id, str(row["topic"]), json.loads(str(row["params"] or "{}")), now
        ):
            return False
        if ask.get("proposal") is None:
            return True
        proposal = await self.proposals.get(int(ask["proposal"]))
        return proposal is not None and proposal.status == "open"

    async def vesta_ask_text(self, user_id: str | None, language: str, ask: dict[str, Any]) -> str:
        """A question in Vesta's own words, in this person's form (E-252)."""
        lang = self.languages.resolve(language)
        who = next((p for p in self._people if p.user_id == user_id), None)
        listener = VestaListener(
            user_id,
            max(who.level if who else 2, 2),
            who.is_admin if who else False,
            bool(who and who.preferences.get("address") == "u"),
            lang,
        )
        if ask["kind"] == "proposal":
            proposal = await self.proposals.get(int(ask["proposal"]))
            assert proposal is not None and self.vesta_explainer is not None
            layer = Layer.AI if proposal.reason == "ai" else Layer.LEARNING  # you asked her
            request = proposal.request(layer, proposal.at, None)
            decision = Decision(
                request, Outcome.PROPOSED, "proposed", Step.PROPOSE, (), proposal.at
            )
            facts = {"question": self.vesta_explainer.question(lang, decision)}
            topic = "question"
        else:
            topic, facts = self._vesta_topic(lang, ask["text_key"], ask["params"])
            if ask["kind"] == "knowledge":
                # Her question as asked, in her words; about the house, so no AI (B-360).
                topic, facts = "question", {"question": facts["text"], "text_ai": ""}
        return await self._vesta_one(
            listener, topic, facts, f"ask:{ask['key']}", 80, self.clock.now()
        )

    async def proposal_actions(self, proposal_id: int) -> list[str] | None:
        """The buttons of a proposal she asks (E-252), with Waarom? last (E-349, B-228)."""
        proposal = await self.proposals.get(proposal_id)
        if proposal is None or proposal.status != "open":
            return None
        rule = bool(proposal.skill and proposal.area_id)
        return ["do", "not_now", *(["from_now", "never"] if rule else []), "why"]

    async def vesta_why_asked(self, user_id: str | None, language: str, proposal_id: int) -> str:
        """Waarom? under her question (E-349, B-228): why she asks it and what she saw, then
        the question again – nothing she has to understand."""
        proposal = await self.proposals.get(proposal_id)
        if proposal is None or self.vesta_explainer is None:
            return ""
        lang = self.languages.resolve(language)
        layer = Layer.AI if proposal.reason == "ai" else Layer.LEARNING  # you asked her (B-233)
        request = proposal.request(layer, proposal.at, None)
        decision = Decision(request, Outcome.PROPOSED, "proposed", Step.PROPOSE, (), proposal.at)
        why = self.vesta_explainer.why_asked(lang, decision, self.clock.now())
        said = self.vesta_fact(lang, "ask_why", user_id, reason=why["reason"], Reason=why["Reason"])
        return " ".join(p for p in (said, why["evidence"], why["question"]) if p)

    def vesta_fact(self, language: str, topic: str, user_id: str | None, **facts: str) -> str:
        """One of her answers with its facts filled in (E-243 – E-245), in this person's form,
        never the same twice in a row."""
        lang = self.languages.resolve(language)
        line = self.vesta_says(lang, topic, user_id)
        try:
            return tidy(lang, sentence_case(line.format(**facts)))
        except (KeyError, IndexError):
            return ""

    def asked(self, user_id: str | None, key: str) -> None:
        """This person saw this question in Vesta's window: not again within the hour."""
        self._asked[(user_id, key)] = self.clock.now()

    async def voice_for(self, language: str | None, base: Voice | None = None) -> Voice | None:
        """Vesta's voice in the language she speaks (B-124), never a voice made for another
        language. The chosen or the pipeline's voice when it speaks that language; else a voice
        of the same engine in that language; else the first engine in HA that speaks it; else
        the voice as it is."""
        base = base or self.speech.voice or await self.vesta_voice()
        lang = self.languages.resolve(language or self._language()).split("-")[0].lower()
        if base is None or (base.language or "").split("-")[0].lower() == lang:
            return base
        if (base, lang) not in self._voices:
            found = base
            others = [e for e in await self.ha.tts_engines(lang) if e != base.engine]
            country = self.config.country if self.config else None
            for engine in [base.engine, *others]:
                locale = voice_locale(lang, await self.ha.tts_languages(engine), country)
                if locale is not None:
                    voices = await self.ha.tts_voices(engine, locale)
                    found = Voice(engine, locale, voices[0][0] if voices else None)
                    break
            self._voices[(base, lang)] = found
        return self._voices[(base, lang)]

    async def vesta_audio(self, text: str, language: str | None = None) -> str | None:
        """Where a browser can play this line in Vesta's voice (B-82), in the language of the
        line (B-124), or None."""
        voice = await self.voice_for(language)
        if voice is None or not text.strip():
            return None
        return await self.ha.tts_url(text, voice)

    async def _vesta_ai(self, chosen: list[Message], listener: Any, moment: Moment) -> str | None:
        """Her line from the AI, when one is on (E-222, B-79): only the chosen messages go
        along; too slow or not right, and she keeps her own words. Something important – and
        an emergency – she says exactly as HouseMind does, without the AI. The same message
        for the same way of speaking gets the words the AI gave before, for a day."""
        if self.ai.status() != "on":
            return None
        if any(m.facts.get("text_ai") == "" for m in chosen):
            return None  # nothing an AI may see (B-173): her own words
        if any(m.weight >= WEIGHTS["important"] and m.topic != "question" for m in chosen):
            return None  # something important: HouseMind's own words (a question she asks
            # in a conversation stays hers to word)
        messages: list[dict[str, Any]] = []
        if moment is Moment.ARRIVAL:
            messages.append({"kind": "greeting", "facts": {}})
        messages += [
            {"kind": m.topic, "important": m.weight >= 70, "facts": _for_ai(m.facts)}
            for m in chosen
        ]
        persona = {
            "name": self.vesta_name(listener.language),
            "formal": listener.formal,
            "sentences": 1 if listener.level <= 1 else 2,
            "moment": moment.value,
        }
        names = PlainNames(self.names, listener.language)  # "de lamp in de keuken" (E-248)
        now = self.clock.now()
        key = json.dumps([persona, messages, listener.language], sort_keys=True)
        kept = self._vesta_ai_said.get(key)
        if kept is not None and now - kept[0] < VESTA_AI_KEEP:
            return kept[1]
        try:
            said = await asyncio.wait_for(
                self.ai.vesta(persona, messages, listener.language, names), VESTA_AI_WAIT
            )
        except TimeoutError:
            return None
        if not said:
            return said
        said = sentence_case(said)
        self._vesta_ai_said = {
            k: v for k, v in self._vesta_ai_said.items() if now - v[0] < VESTA_AI_KEEP
        }
        self._vesta_ai_said[key] = (now, said)
        return said

    def _vesta_context(self, lang: str) -> dict[str, str]:
        """What is true now for her words: the time of day in the house's time zone."""
        tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
        local = self.clock.now().astimezone(tz)
        part = daypart(local.hour)
        house = self.world.part_now(self.clock.now()) if self.world is not None else None
        if house is not None and house.how != "clock":
            # Her "goedemorgen" and "goedenavond" follow the house's rhythm (E-455).
            part = {"day": "morning" if local.hour < 12 else "afternoon"}.get(
                house.kind, house.kind
            )
        found = {
            "moment": self.vesta_phrases.moment(lang, part),
            "salute": self.vesta_phrases.salute(lang, part),
        }
        return {k: v for k, v in found.items() if v}

    async def _vesta_memory(self, user_id: str | None, now: datetime) -> Memory:
        """What this person heard from Vesta: told in the last 12 hours is told."""
        rows = await self.db.fetchall(
            "SELECT at, topic, variant, key, question FROM vesta_said "
            "WHERE user_id IS ? AND at >= ? ORDER BY id",
            (user_id, (now - timedelta(days=1)).isoformat()),
        )
        midnight = now.astimezone(ZoneInfo(self.config.time_zone if self.config else "UTC"))
        midnight = midnight.replace(hour=0, minute=0, second=0, microsecond=0)
        memory = Memory()
        for r in rows:
            at = datetime.fromisoformat(str(r["at"]))
            if now - at <= timedelta(hours=12):
                memory.told.add(str(r["key"]))
            memory.recent.setdefault(str(r["topic"]), []).append(int(r["variant"]))
            if r["topic"] in ("calm", "calm_light"):
                memory.calm_lines += 1
            if r["question"]:
                memory.last_question_kind = str(r["key"]).rsplit(":", 1)[-1]
                if at >= midnight:
                    memory.questions_today += 1
        return memory

    def _person(self, user_id: str | None) -> Person | None:
        return next((p for p in self._people if p.user_id == user_id), None)

    async def _fit_colour(
        self,
        chosen: list[Message],
        messages: list[Message],
        state: str,
        reason: str,
        lang: str,
    ) -> list[Message]:
        """Orange, red or amber: what makes her that colour is in what she says – also
        when she told it before, or when it is a question on level 2 (E-284)."""
        if state not in ("emergency", "attention", "learning"):
            return chosen
        floor = {"emergency": EMERGENCY_WEIGHT, "attention": WEIGHTS["important"]}.get(state)
        if floor is not None and any(m.weight >= floor for m in chosen):
            return chosen
        if state == "learning" and any(m.topic == "question" for m in chosen):
            return chosen
        found: Message | None = None
        if reason == "ha_lost":
            found = Message("ha_lost", WEIGHTS["important"], {}, "ha_lost")
        elif reason == "outside_alarm":
            found = next((m for m in messages if m.topic == "outside_alarm"), None)
        elif reason == "learned":
            found = next((m for m in messages if m.topic == "learned"), None)
        elif reason.startswith("proposal:"):
            proposal = next(
                (p for p in await self.open_proposals() if f"proposal:{p.id}" == reason), None
            )
            if proposal is not None:
                found = self._question_message(lang, proposal)
        else:
            row = await self.db.fetchone(
                "SELECT id FROM notifications WHERE topic = ? AND resolved_at IS NULL", (reason,)
            )
            if row is not None:
                found = next((m for m in messages if m.key.startswith(f"n:{row['id']}:")), None)
        return [found, *chosen] if found is not None else chosen

    def _question_message(self, lang: str, proposal: Proposal) -> Message:
        assert self.vesta_explainer is not None
        request = proposal.request(Layer.LEARNING, proposal.at, None)
        decision = Decision(request, Outcome.PROPOSED, "proposed", Step.PROPOSE, (), proposal.at)
        kind = proposal.skill or proposal.action
        return Message(
            "question",
            WEIGHTS["learning"],
            {"question": self.vesta_explainer.question(lang, decision)},
            f"p:{proposal.id}:{kind}",
            question_kind=kind,
            proposal_id=proposal.id,
        )

    async def _vesta_messages(
        self, lang: str, level: int, user_id: str | None = None
    ) -> list[Message]:
        """Everything the brain could tell now, as messages for Vesta (17.1): open
        notifications – not the ones this person put off or switched off (E-383) – from
        level 3 open questions, and what it just did or left."""
        assert self.vesta_explainer is not None
        out: list[Message] = []
        now = self.clock.now()
        for note in await self.notifications():
            kind = str(note["kind"])
            key = str(note["text_key"])
            if kind == "learning" or key == "notify.proposal":
                continue  # questions come from the proposals themselves
            params = json.loads(str(note["params"]) or "{}")
            if kind != "emergency" and self._hidden_for(user_id, str(note["topic"]), params, now):
                continue
            topic, facts = self._vesta_topic(lang, key, params)
            # An emergency is for everyone, also one that went to administrators (E-284).
            concerns = "home" if kind == "emergency" else str(note.get("concerns") or "home")
            out.append(
                Message(
                    topic,
                    WEIGHTS.get(kind, 40),
                    facts,
                    f"n:{note['id']}:{note['params']}",
                    concerns,
                )
            )
        if self.status.emergency is not None and not any(m.weight >= 100 for m in out):
            what = self.languages.text(lang, f"emergency.{self.status.emergency.value}")
            text = self.languages.text(lang, "say.emergency", what=what)
            out.append(Message("emergency", WEIGHTS["emergency"], {"text": text}, "emergency"))
        if level >= 3:
            for proposal in await self.open_proposals():
                out.append(self._question_message(lang, proposal))
        did = left = False
        for decision in reversed(self.status.thoughts):
            age = now - decision.at
            if age > timedelta(hours=3):
                break
            code = (decision.reason or "").split(":", 1)[0]
            key = f"m:{decision.request.entity_id}:{decision.at.isoformat()}"
            text = self.vesta_explainer.thought(lang, decision, now)
            if decision.outcome is Outcome.EXECUTED and not did and age <= timedelta(minutes=30):
                out.append(Message("did", 20, {"text": text}, key))
                did = True
            elif (
                decision.outcome not in (Outcome.EXECUTED, Outcome.PROPOSED)
                and not left
                and code not in VESTA_NOISE
            ):
                out.append(Message("considered", 10, {"text": text}, key))
                left = True
        return out

    def _hidden_for(
        self,
        user_id: str | None,
        topic: str,
        params: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> bool:
        """This person put it off with Later, or the house switched it off (E-372, E-412):
        Vesta neither names it nor colours for it (E-383)."""
        if self.router is None:
            return False
        return bool(self.router.hidden(user_id, topic, params or {}, now or self.clock.now()))

    def _vesta_topic(
        self, lang: str, key: str, params: dict[str, Any]
    ) -> tuple[str, dict[str, str]]:
        """The kind of line for a notification, and its facts – the notification itself is
        never read out (E-220); without a line of her own, its text goes in "other"."""
        t = self.languages.text
        values = {k: self.languages.render(lang, v) for k, v in params.items()}
        count = values.pop("count", None)
        name = key.rsplit(".", 1)[-1]
        name = {"cooking_away_off": "cooking_away"}.get(name, name)  # her words are the same
        if name in ("device_hot", "device_very_hot"):
            values["device"] = values.get("name", "")
        facts = self._plain_facts({k: str(v) for k, v in values.items()}, lang)
        plain: dict[str, Any] = {k: v for k, v in facts.items() if k in values}
        if "room" in facts:
            plain["area"] = facts["room"]  # texts that say "in de {area}" themselves
        if "name" in values and "device" in facts:
            plain["name"] = facts["device"]
        facts["text"] = sentence_case(t(lang, key, count=count, **plain))
        facts["text_ha"] = t(lang, key, count=count, **values)  # for an AI: names become labels
        if name.startswith("emergency_") and name != "emergency_sensor_lost":
            return "emergency", facts
        if name.startswith("outside_"):
            facts["what"] = t(lang, f"emergency.{name.removeprefix('outside_')}")
            return "outside_alarm", facts
        if name in ("device_hot", "device_very_hot"):
            return "device_hot", facts
        topic = {"battery_pct": "battery"}.get(name, name)  # a safety sensor has its own words
        if self.vesta_phrases.variants(lang, topic, False):
            return topic, facts
        return "other", facts

    def _plain_facts(self, facts: dict[str, str], language: str) -> dict[str, str]:
        """Devices and rooms in Vesta's words as a person says them (E-248): "de lamp in de
        slaapkamer", never the name from HA; with the forms German needs after "von"."""
        out = dict(facts)
        if out.get("device") and "device_dat" not in out:
            out["device_ha"] = out["device"]  # an AI gets a label for it, never this (E-15)
            out.update(self.names.by_name(out["device"], language))
        if out.get("area") and "area_dat" not in out:
            out["area_ha"] = out["area"]
            out.update(self.names.area_forms(out["area"], language))
        return out

    def orb_state(
        self, thinking: bool = True, admin: bool = True, level: int = 4, user_id: str | None = None
    ) -> str:
        """Vesta's state for a viewer (E-284); see ``vesta_state``."""
        return self.vesta_state(thinking, admin, level, user_id)[0]

    def vesta_state(
        self, thinking: bool = True, admin: bool = True, level: int = 4, user_id: str | None = None
    ) -> tuple[str, str]:
        """The states of Vesta (E-284, B-132, E-539) and why, for one viewer, most urgent
        first: emergency; attention for danger; pause; attention for an open message that
        concerns this viewer; thinking; sleeping (reason "secured" while the alarm is on);
        learning; secured – the alarm is on; calm. Her colour, her word, what
        she says on a tap and HA's entity all come from here, so they always agree.

        The reason is a notification topic, or one of: emergency, ha_lost, outside_alarm,
        inactive, paused, thinking, sleeping, learned, calm. Without ``thinking``: the state
        of the house, for words about it and for HA's entity (no flicker at every action)."""
        now = self.clock.now()
        mode = self.world.modes.mode.value if self.world else "home"
        snoozed = self.router.snoozed if self.router is not None else {}
        open_now = sorted(
            (
                (topic, kind, concerns, at)
                for topic, (kind, concerns, at) in self._raised.items()
                if not (topic in snoozed and snoozed[topic] > now)
            ),
            key=lambda item: item[3],
        )

        def mine(concerns: str) -> bool:
            if concerns == "admins":
                return admin
            if concerns.startswith("user:"):
                return user_id is not None and concerns[5:] == user_id
            return True

        if self.status.emergency is not None:
            return "emergency", "emergency"
        for topic, kind, _concerns, _at in open_now:
            # An emergency message inside, also when HouseMind switches nothing itself (a
            # very hot socket, E-210), for everyone. The messages of the emergency reactions
            # follow those reactions; an alarm heard outside is "let op" (E-178).
            if kind is Kind.EMERGENCY and not topic.startswith("emergency:"):
                return "emergency", topic
        # Danger: no connection with HA (from level 2, H7.4) or an alarm outside (E-178).
        if self.status.ha_lost is not None and level >= 2:
            return "attention", "ha_lost"
        if self.status.outside_alarms:
            return "attention", "outside_alarm"
        if not self.activated:
            return "inactive", "inactive"  # grey, until set up (E-277)
        if self.paused_until() is not None:
            # A pause someone just chose shows, also while a message waits; danger above still wins.
            return "paused", "paused"
        for topic, kind, concerns, _at in open_now:
            if kind is Kind.IMPORTANT and mine(concerns) and not self._hidden_for(user_id, topic):
                # Something waits for this viewer – also at night (E-284); a snoozed or solved
                # message does not count, one for administrators only for them.
                return "attention", topic
        answering = self.status.answering
        if (
            thinking
            and (mode != "sleep" or answering)  # no violet flashes at night, unless asked
            and self.status.thinking_until is not None
            and now < self.status.thinking_until
        ):
            return "thinking", "thinking"
        armed = self.alarm_armed()
        if mode == "sleep":  # asleep she stays asleep, with "secured" beside it (E-539)
            return "sleeping", "secured" if armed else "sleeping"
        if level >= 2:
            person = self._person(user_id)
            tz = ZoneInfo(self.config.time_zone if self.config else "UTC")
            local = now.astimezone(tz)
            for topic, kind, concerns, at in reversed(open_now):
                if kind is not Kind.LEARNING or not mine(concerns) or now - at >= QUESTION_SHOW:
                    continue
                if person is not None and self.router is not None:
                    if not self.router.may_ask(person, topic, local, count=False):
                        continue  # not within this person's budget: not their question now
                return "learning", topic  # a new question, amber for at most an hour
            if self.status.learning_until is not None and now < self.status.learning_until:
                return "learning", "learned"
        if armed:
            return "secured", "secured"  # the alarm is on (E-539)
        return "calm", "calm"

    def alarm_armed(self) -> bool:
        """The alarm in use is on – away, at home, at night or another way (E-539)."""
        if self.world is None or not self.world.alarm_entity:
            return False
        state = self.world.states.get(self.world.alarm_entity)
        return state is not None and state.state.startswith("armed")

    async def vesta_why(
        self,
        language: str,
        level: int,
        admin: bool,
        user_id: str | None,
        for_ha: bool = False,
    ) -> dict[str, Any] | None:
        """Why she has her colour, for the line under her word (E-389, B-296): red, orange or
        amber with a reason that is not in that line already. The same state as her colour
        (``vesta_state``), so the two never disagree; None: the usual line. ``for_ha``: for
        HA's Toestand sensor, nothing of a lock, an alarm or the calendar (B-300)."""
        state, reason = self.vesta_state(False, admin, level, user_id)
        if state not in ("emergency", "attention", "learning") or reason == "ha_lost":
            return None
        lang = self.languages.resolve(language)
        why: dict[str, Any] = {"text": "", "notification": None, "proposal": None}
        if reason == "learned":
            return {**why, "text": self.languages.text(lang, "status.why.learned")}
        if reason in ("emergency", "outside_alarm"):
            # The newest open message of the emergency or of the alarm outside says what.
            like = "notify.outside_%" if reason == "outside_alarm" else "notify.emergency%"
            row = await self.db.fetchone(
                "SELECT id, topic, text_key, params FROM notifications WHERE text_key LIKE ? "
                "AND resolved_at IS NULL ORDER BY id DESC",
                (like,),
            )
            if row is None and self.status.emergency is not None:
                what = self.languages.text(lang, f"emergency.{self.status.emergency.value}")
                return {**why, "text": sentence_case(what)}
        else:
            row = await self.db.fetchone(
                "SELECT id, topic, text_key, params FROM notifications WHERE topic = ? "
                "AND resolved_at IS NULL ORDER BY id DESC",
                (reason,),
            )
        if row is None:
            return None
        if for_ha and any(word in str(row["text_key"]) for word in HA_PRIVATE):
            return None
        why["notification"] = int(row["id"])
        topic = str(row["topic"])
        if topic.startswith("proposal:"):
            # Her question, without its reason (B-228): "Zal ik de leeslamp … uitdoen?"
            proposal = await self.proposals.get(int(topic.split(":", 1)[1]))
            if proposal is None or self.vesta_explainer is None:
                return None
            why["proposal"] = proposal.id
            layer = Layer.AI if proposal.reason == "ai" else Layer.LEARNING
            request = proposal.request(layer, proposal.at, None)
            decision = Decision(
                request, Outcome.PROPOSED, "proposed", Step.PROPOSE, (), proposal.at
            )
            asked = self.vesta_explainer.why_asked(lang, decision, self.clock.now())
            return {**why, "text": asked["question"]}
        params = json.loads(str(row["params"] or "{}"))
        _, facts = self._vesta_topic(lang, str(row["text_key"]), params)
        first = _SENTENCE.split(facts["text"].strip(), maxsplit=1)[0]  # one short line
        return {**why, "text": first}

    def _show_thinking(self, answering: bool = False) -> None:
        """Violet for a moment; the screens hear when it is over."""
        self.status.thinking_until = self.clock.now() + THINK_SHOW
        self.status.answering = answering
        self._refresh_orb()
        with contextlib.suppress(RuntimeError):  # no running loop: nothing on a screen
            loop = asyncio.get_running_loop()
            if self._orb_timer is not None:
                self._orb_timer.cancel()
            self._orb_timer = loop.call_later(
                THINK_SHOW.total_seconds() + 0.2, lambda: loop.create_task(self._orb_again())
            )

    async def _orb_again(self) -> None:
        before = self.status.orb
        self._refresh_orb()
        if self.status.orb != before:
            await self._notify()
