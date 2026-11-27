"""Je huis begrijpen (E-201 – E-208, design 50.2, B-343 – B-346): what the catalogue and the
words could not place, AI reads in the slow loop beside the core, and a person says Klopt.

- Rooms (B-344): a room the words call "other" gets a suggested role. A room's role changes
  what HouseMind does there, so it counts only after Klopt – then as the person's own, like
  a role from the settings file. Until then it shows as "van AI".
- Switches (B-345): a switch HouseMind only watches because its purpose is unknown gets a
  suggested role from the catalogue's roles for a switch. It counts at once for watching and
  learning, as that kind; proposing and doing wait for Klopt – the gate holds it (50.8).
- Automations and scripts (B-346): what one does that the rules cannot place, in one plain
  sentence, at Bestaande automatiseringen. Only to read: AI never changes one (E-03).
- Afleiden uit gedrag (E-203, B-358): without AI, also when AI is off, a slow job reads what
  Home Assistant's recorder kept once a day and derives from it what a device is
  (``brain/behaviour.py``): a plug that runs one to three hours at about 2 kW, a switch that
  goes with a lamp, a sensor that goes dark at sunset. "Afgeleid uit gedrag": a switch's role
  counts at once for watching and learning, a sensor's kind only after Klopt. It goes first,
  so AI only gets what is left (E-201).
- Twijfel van de regels (design 50.2, B-359): which sensor is a room's temperature, whether a
  contact is a real door or window, whether a "light" is a status light. Where the rules are
  unsure, their own guess is offered for Klopt; with AI also AI's pick. Until an administrator
  answers, the rules hold; then what they said, from memory (E-414).
- Op Geïnformeerd (E-207, B-360): an administrator there does not see the page, so Vesta asks
  at most one question a day, with Klopt and Klopt niet, within the question budget (E-294).

Klopt niet is kept: that is never asked again (E-208); neither is what is confirmed. An AI
gets only neutral facts – labels, kinds, make and type – never a name a person gave (50.8),
and reading the house uses at most half of the month's budget (50.6). Everything goes through
the AI's own queue: the core never waits for it, and reads only the findings in memory
(E-414)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Coroutine
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from housemind.ai.jobs import AIJobs
from housemind.ai.privacy import entity_facts, plain_structure
from housemind.ai.service import AIService, Found
from housemind.brain import behaviour
from housemind.brain.automations import Automations
from housemind.brain.filters import PARTS, parts_of
from housemind.brain.functions import (
    FUNCTIONS,
    LESS,
    OTHER_DEVICE,
    SETTING,
    STATUS,
    SWITCH_CHOICES,
    SWITCH_ROLES,
    UNKNOWN,
    Context,
    by_rules,
    has_function,
    main_devices,
)
from housemind.brain.house_knowledge import (
    CONFIRMED,
    DOUBTS,
    REJECTED,
    SUGGESTED,
    Finding,
    HouseKnowledge,
    split_key,
)
from housemind.brain.names import Names, PlainNames
from housemind.brain.notify import Kind, Person
from housemind.brain.rhythm import daily_words
from housemind.brain.skills import SkillEngine
from housemind.core.clock import Clock
from housemind.core.model import Entity, HouseModel
from housemind.ha.adapter import HAScript, HAStep, HistoryChange, HomeAssistant
from housemind.knowledge.area_roles import ROLE_ORDER
from housemind.knowledge.catalog import Catalog
from housemind.knowledge.labels import LabelHints, automation_hint, entity_hint
from housemind.knowledge.matcher import MatchQuality, recognise
from housemind.memory.database import Database
from housemind.parts import Part
from housemind.parts.labels import LAMP
from housemind.safety.gate import SafetyGate
from housemind.world.boxes import box_rooms
from housemind.world.climate import Temperatures, temperature_doubts
from housemind.world.openings import opening_doubts
from housemind.world.world import World, WorldState

_LOGGER = logging.getLogger(__name__)

# The questions of reading the house, each a job of its own in the AI's queue: rooms,
# switches, automations, and where the rules doubt (B-359).
READING = ("roles", "functions", "switches", "automations", "doubts")
ASK_AGAIN = timedelta(days=7)  # the house once a week again; an AI's "not sure" too (E-208)
ASK_AGAIN_LOCAL = timedelta(days=1)  # with an AI in the house: every day (E-423)
PER_QUESTION = 20  # switches, automations and scripts in one question; the rest the next time
# What each entity is (design 51.4): 20 in one question, three questions in one go – the rest
# at the next quarter hour, so that one job stays well within its time limit. An answer that
# did not fit (cut off) is asked again at once in groups half as big, down to five.
PER_FUNCTIONS = 20
FUNCTION_QUESTIONS = 3
FEWEST_FUNCTIONS = 5
NOT_A_DEVICE = frozenset(
    {"automation", "script", "scene", "person", "device_tracker", "zone", "sun", "update"}
)
# Afleiden uit gedrag (E-203): once a day, over the last week the recorder kept, a day at a
# time – small enough for a Pi.
DERIVE_AGAIN = timedelta(days=1)
LOOK_BACK = timedelta(days=7)
WINDOW = timedelta(days=1)
LIGHT_UNITS = frozenset({None, "", "lx", "lux"})  # a sensor that may measure light
# Vesta's one question a day on Geïnformeerd (E-207, B-360), with Klopt and Klopt niet.
ASK = "knowledge:"
KLOPT = (("yes", "ui.knowledge.yes"), ("no", "ui.knowledge.no"))


class UnderstandingPart(Part):
    """Je huis begrijpen (E-201 – E-208, design 50.2, B-343 – B-346)."""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        ai: AIService
        ai_jobs: AIJobs
        area_roles: dict[str, str]
        automations: Automations
        catalog: Catalog
        clock: Clock
        db: Database
        gate: SafetyGate | None
        ha: HomeAssistant
        knowledge: HouseKnowledge
        label_hints: LabelHints
        languages: Any
        openings: set[str]
        learning_allowed: Any
        model: HouseModel
        names: Names
        scripts: list[HAScript]
        skills: SkillEngine | None
        temperatures: Temperatures
        world: World | None
        _knowledge_roles: dict[str, str]
        _knowledge_said: dict[str, str]
        _house_unread: set[str]
        _deriving: asyncio.Task[None] | None
        _people: list[Person]
        _raised: dict[str, Any]
        _side: set[asyncio.Task[Any]]

        def _language(self) -> str: ...
        async def _label_answers(self) -> dict[str, bool]: ...
        def _lamp_candidates(self, answers: dict[str, bool]) -> list[tuple[str, str]]: ...
        async def _load_area_roles(self) -> None: ...
        def _sort_temperatures(self) -> None: ...
        def _sort_openings(self) -> None: ...
        def _opening_words(self) -> list[str]: ...
        def place(self) -> tuple[float, float] | None: ...
        async def _refresh_labels(self) -> None: ...
        async def _notify(self) -> None: ...
        async def _apply_label_roles(self) -> None: ...
        async def _label_lamps_known(self) -> None: ...
        async def _label_role_back(self, entity_id: str, found: Finding | None) -> None: ...
        def _tracked(self, *prefixes: str) -> list[str]: ...
        async def _resolve(self, topic: str) -> None: ...

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

    # -- what the core reads: memory only (E-205, E-414) -------------------------------------

    async def _load_knowledge(self) -> None:
        """At the start: the findings into memory, what they say into the gate, the rooms'
        sensors and the openings again with what a person decided, and where the rules doubt."""
        await self.knowledge.load()
        await self._label_lamps_known()  # a label's lamp can be changed in Wat ik weet (E-580)
        if any(f.field in DOUBTS for f in self.knowledge.findings.values()):
            self._sort_temperatures()
            self._sort_openings()
        self._apply_knowledge()
        await self._note_doubts()

    def _apply_knowledge(self) -> None:
        """A switch's role from the findings (B-345). One only AI gave is watched and learned
        as that kind, never proposed or done, until a Klopt (50.8); Klopt niet takes it away.
        A role from anywhere else – the catalogue's words, a label someone said yes to, the
        energy setup – goes first. Again after every rebuild of the house and every answer."""
        assert self.gate is not None
        roles = self.gate.state.roles
        for entity_id, given in self._knowledge_roles.items():
            if roles.get(entity_id) == given:
                del roles[entity_id]
        applied: dict[str, str] = {}
        from_ai: set[str] = set()
        derived: set[str] = set()
        said: dict[str, str] = {}  # for Waarom?: where what a decision rests on came from
        for finding in self.knowledge.of("entity", "role"):
            role, entity_id = finding.value.get("role"), finding.subject
            if not isinstance(role, str) or entity_id not in self.model.entities:
                continue
            if entity_id in self.gate.state.openers:
                continue  # it opens a door: no other role can make it something else (QA 3)
            # What an administrator chose in Wat ik weet goes before a label's lamp and the
            # catalogue's words (E-580): another device is no role at all, Klopt niet on
            # what they chose themselves takes that away.
            theirs = finding.source == "person"
            if theirs and (role == OTHER_DEVICE or finding.status == REJECTED):
                if role == OTHER_DEVICE or roles.get(entity_id) == role:
                    roles.pop(entity_id, None)
                continue
            if finding.status == REJECTED or role == OTHER_DEVICE:
                continue
            if entity_id in roles and not (theirs and finding.status == CONFIRMED):
                continue
            if finding.status != CONFIRMED and self._less_than_a_switch(entity_id):
                continue  # what it is says a setting or a status: no switch (E-430)
            roles[entity_id] = role
            applied[entity_id] = role
            if finding.from_ai:
                from_ai.add(entity_id)
            elif finding.derived:
                derived.add(entity_id)  # afgeleid uit gedrag (E-203): the same until Klopt
            elif finding.status == CONFIRMED:
                said[entity_id] = finding.source
        for area_id, (_, use) in self.knowledge.chosen_temperatures().items():
            chosen = self.knowledge.get("area", area_id, "temperature")
            if use and chosen is not None:
                said[f"area:{area_id}"] = chosen.source
        self._knowledge_roles = applied
        self.gate.state.from_ai = from_ai
        self.gate.state.derived = derived
        self._knowledge_said.clear()
        self._knowledge_said.update(said)
        leds = self.knowledge.decided("status_led")
        if self.world is not None:
            self.world.status_leds = leds
            self.world.index_status_leds()  # the rules' guess, or what a person said (E-426)
            self.world.light_sensors = {
                f.subject for f in self.knowledge.of("entity", "measures") if f.status == CONFIRMED
            }
            if self.world.lux_cache is not None:
                self.world.lux_cache.clear()  # a light sensor more or less
        if self.skills is not None:
            self.skills.not_lamps = (
                set(self.world.status_led_ids)
                if self.world is not None
                else {entity_id for entity_id, led in leds.items() if led}
            )
            self.skills.forget()  # a lamp more or less: each room's devices again
        self._index_functions()
        self._sort_boxes()

    def _sort_boxes(self) -> None:
        """The small rooms with one door an administrator confirmed (E-504): their door says
        who is in (E-505) – as long as the room still has that one door and nothing in it sees
        people. Again after every rebuild of the house and every answer."""
        if self.world is None:
            return
        said = self.knowledge.decided("one_door")
        rooms = box_rooms(self.model, self.world.occupancy, self.openings)
        boxes = {area_id: door for area_id, door in rooms.items() if said.get(area_id)}
        self.world.occupancy.set_boxes(boxes, self.world.states, self.clock.now())

    def _less_than_a_switch(self, entity_id: str) -> bool:
        """An entity's function, from AI or a person, says it makes HouseMind do less – a
        setting, a status, the device itself (E-430)."""
        found = self.knowledge.get("entity", entity_id, "function")
        return (
            found is not None and found.status != REJECTED and found.value.get("function") in LESS
        )

    def _index_functions(self) -> None:
        """Every entity's function (E-427, E-428, design 51): what a person said wins; then the
        rules; what they cannot tell, from AI or behaviour – or unknown. A switch's role only AI
        or behaviour gave is not the rules': it counts as theirs."""
        if self.world is None or self.gate is None:
            return
        state = self.gate.state
        guessed = state.from_ai | state.derived
        temps = self.temperatures
        measuring = [*temps.room.values(), *temps.secondary.values()]
        ctx = Context(
            status_leds=self.world.status_led_ids,
            roles={e: r for e, r in state.roles.items() if e not in guessed},
            room_temperatures={e for sources in measuring for e in sources},
            device_temperatures=set(temps.devices),
            room_climates=set(temps.rooms_climate),
            openings=set(self.openings),
            outside_areas={a for a, role in self.area_roles.items() if role == "outside"},
            camera_devices={
                e.device_id
                for e in self.model.entities.values()
                if e.domain == "camera" and e.device_id is not None
            },
            main_devices=main_devices(self.model),
            water_words=tuple(daily_words(self.languages, "water_temperature")),
            outside_words=tuple(daily_words(self.languages, "outside")),
            not_room_words=tuple(daily_words(self.languages, "temperature_not_room")),
        )
        functions: dict[str, str] = {}
        sources: dict[str, str] = {}
        open_: set[str] = set()
        for entity in self.model.entities.values():
            if entity.disabled or not has_function(entity):
                continue
            eid = entity.entity_id
            said: tuple[str, str] | None = None
            finding = self.knowledge.get("entity", eid, "function")
            if finding is not None and finding.status != REJECTED:
                value = finding.value.get("function")
                if value in FUNCTIONS:
                    said = (value, "person" if finding.status == CONFIRMED else finding.source)
            rules = by_rules(entity, self.model, ctx)
            role = state.roles.get(eid)
            if said is not None and (said[1] == "person" or rules is None):
                functions[eid], sources[eid] = said
            elif rules is not None:
                functions[eid], sources[eid] = rules, "rules"
            elif eid in guessed and role is not None:
                source = "ai" if eid in state.from_ai else "behaviour"
                functions[eid], sources[eid] = SWITCH_ROLES.get(role, "appliance_power"), source
            else:
                functions[eid], sources[eid] = UNKNOWN, "rules"
            if rules is None and finding is None:
                open_.add(eid)  # for AI: the rules cannot tell, nothing said yet (51.4)
        self.world.functions = functions
        self.world.function_sources = sources
        self.world.functions_open = open_
        self._act_on_functions()

    def _act_on_functions(self) -> None:
        """Act on every entity's function everywhere (E-431, design 51.6). What makes
        HouseMind do less counts at once, also from AI; what makes it do more only from a
        person (E-430). The rules' own part is already in each place."""
        if self.world is None or self.gate is None:
            return
        functions, sources = self.world.functions, self.world.function_sources
        said = {e for e, s in sources.items() if s != "rules"}  # AI, behaviour or a person
        less = {e for e in said if functions[e] in LESS}
        person = {e for e, s in sources.items() if s == "person"}
        # Never switched by HouseMind itself: a setting or a status of a device.
        self.gate.state.leave_alone = {e for e, f in functions.items() if f in SETTING | STATUS}
        # Lighting only with lamps: no setting or status of a device, whatever role a guess
        # gave it ("Charging protection" in a list of lamps, E-458).
        if self.skills is not None:
            self.skills.not_lamps = (
                set(self.world.status_led_ids)
                | self.gate.state.leave_alone
                | {
                    e
                    for e, f in functions.items()
                    if e.startswith("light.") and f != "lamp" and (e in less or e in person)
                }
            )
            self.skills.forget()
        # Climate only with a room's thermostat.
        rooms = set(self.temperatures.rooms_climate) - less
        rooms |= {e for e in person if functions[e] == "room_climate"}
        if self.skills is not None:
            self.skills.room_climates = rooms
        # A room's temperature only from what measures its air.
        for sources_of in (self.temperatures.room, self.temperatures.secondary):
            for measuring in sources_of.values():
                measuring[:] = [e for e in measuring if e not in less]
        for entity_id in person:
            area = self.model.area_of(entity_id)
            if area is None or functions[entity_id] != "air_temperature":
                continue
            own = self.temperatures.room.setdefault(area.id, [])
            if entity_id not in own:
                own.append(entity_id)
        # Presence only from what senses people; openings only real doors and windows.
        self.world.occupancy.not_people = {e for e in less if e.startswith("binary_sensor.")}
        self.world.occupancy.index()
        self.world.index_sleep()  # who still sleeps: only from what senses people (E-483)
        self.openings -= less
        self.openings |= {e for e in person if functions[e] == "opening"}
        if self.skills is not None:
            self.skills.openings = self.openings

    async def _house_changed(self) -> None:
        """Home Assistant changed rooms, devices, entities or automations and the house was
        read again (E-208): the findings hold again, the rules' new doubts are noted, and what
        is new is read."""
        self._apply_knowledge()
        await self._note_doubts()
        self.read_house_soon()

    # -- when (E-208) ------------------------------------------------------------------------

    def read_house_soon(self, now: bool = False) -> bool:
        """Queue reading what is left, and return at once: the core never waits for it
        (E-414). ``now``: an administrator's "Nu bekijken", also within a day of the last
        question (the budget still holds). Nothing without AI."""
        if self.ai.status() != "on":
            return False
        if now:
            self.ai.ask_now("analysis")
        jobs = (
            ("roles", self._read_rooms),
            ("functions", self._read_functions),  # before switches: a setting is no switch
            ("switches", self._read_switches),
            ("automations", self._read_automations),
            ("doubts", self._read_doubts),
        )
        queued = False
        for kind, job in jobs:
            queued = self.ai_jobs.submit(kind, job) or queued
        return queued

    async def _read_house_when_due(self) -> None:
        """The quarter-hour look (E-208): what could not be asked yet – too soon after the
        last question, the budget's half used, an error – and the whole house once a week."""
        if self.ai.status() != "on":
            return
        read = await self.db.get_setting("house_read", {}) or {}
        now = self.clock.now()
        again = ASK_AGAIN_LOCAL if self.ai.local else ASK_AGAIN
        due = [
            kind
            for kind in READING
            if kind in self._house_unread
            or not isinstance(read.get(kind), str)
            or now - datetime.fromisoformat(read[kind]) >= again
        ]
        if due:
            self.read_house_soon()

    async def _read(self, kind: str, found: Found) -> None:
        """One question done: read, or to try again at the next quarter hour."""
        if found.reason is None:
            self._house_unread.discard(kind)
            read = dict(await self.db.get_setting("house_read", {}) or {})
            read[kind] = self.clock.now().isoformat()
            await self.db.set_setting("house_read", read)
        else:
            self._house_unread.add(kind)
        if found.answers:
            await self._notify()

    async def _unsure(self) -> dict[str, str]:
        """What an AI could not say ("unknown", "other"), and when: asked again after a week."""
        raw = await self.db.get_setting("knowledge_unsure", {}) or {}
        now = self.clock.now()
        return {
            str(k): str(v)
            for k, v in raw.items()
            if isinstance(v, str) and now - datetime.fromisoformat(v) < ASK_AGAIN
        }

    async def _new_unsure(self, keys: list[str]) -> None:
        if keys:
            unsure = await self._unsure()
            now = self.clock.now().isoformat()
            await self.db.set_setting("knowledge_unsure", {**unsure, **dict.fromkeys(keys, now)})

    def _plain_names(self) -> PlainNames:
        return PlainNames(self.names, self._language())

    # -- rooms (B-344) -------------------------------------------------------------------------

    async def _read_rooms(self) -> None:
        await self.suggest_area_roles()

    async def suggest_area_roles(self) -> dict[str, str]:
        """AI ideas for rooms the words did not recognise (H5.3, B-344): each waits for an
        administrator's Klopt; until then nothing changes in the house."""
        if self.ai.status() != "on":
            return {}
        unsure = await self._unsure()
        unknown = [
            area_id
            for area_id, role in sorted(self.area_roles.items())
            if role == "other"
            and area_id in self.model.areas
            and (not self.knowledge.known("area", area_id, "role") or self._kind_unknown(area_id))
            and f"area:{area_id}:role" not in unsure
        ]
        found = await self.ai.suggest_roles(unknown, self._language()) if unknown else Found()
        new: dict[str, str] = {}
        not_sure: list[str] = []
        for area_id, answer in found.answers.items():
            role = answer["role"]
            if role not in ROLE_ORDER:  # "other": the AI does not know either
                not_sure.append(f"area:{area_id}:role")
                continue
            value = {"role": role, "why": self.ai.plain(answer.get("why"), self._plain_names())}
            if await self.knowledge.suggest(
                "area",
                area_id,
                "role",
                value,
                _sure(answer),
                self.clock.now(),
                found.log_id,
                again=self._kind_unknown(area_id),  # AI's idea in place of 'soort onbekend'
            ):
                new[area_id] = role
        await self._new_unsure(not_sure)
        await self._read("roles", found)
        return new

    def _kind_unknown(self, area_id: str) -> bool:
        """Only the rules' 'soort onbekend' waits for this room, nobody answered it (E-486)."""
        found = self.knowledge.get("area", area_id, "role")
        return (
            found is not None
            and found.source == "rules"
            and found.status == SUGGESTED
            and found.value.get("role") == "other"
        )

    async def _set_room_role(self, area_id: str, role: str) -> None:
        """A room's role after Klopt: the person's own from now on (like the settings file),
        and everything that hangs on it again."""
        await self.db.execute(
            "UPDATE areas SET role = ?, role_source = 'person' WHERE area_id = ?", (role, area_id)
        )
        await self._load_area_roles()
        self._sort_temperatures()
        if self.skills is not None:
            self.skills.forget()
        await self._refresh_labels()

    async def _reset_room_role(self, area_id: str) -> None:
        """A room's kind taken back (E-525): the kind its name gives again, or 'onbekend'
        with its one question (E-486), and everything that hangs on it again."""
        await self.db.execute("UPDATE areas SET role_source = 'auto' WHERE area_id = ?", (area_id,))
        await self._load_area_roles()
        self._sort_temperatures()
        if self.skills is not None:
            self.skills.forget()
        await self._refresh_labels()

    # -- switches (B-345) ----------------------------------------------------------------------

    def switch_roles(self) -> list[str]:
        """The roles the catalogue knows for a switch ("light", "cooking_off" …)."""
        found = {kind.match.role for kind in self.catalog.for_domain("switch") if kind.match.role}
        return sorted(r for r in found if r is not None)

    async def unknown_switches(self) -> list[Entity]:
        """Switches HouseMind only watches because their purpose is unknown, for AI: what
        behaviour did not place, and not what an AI was unsure of this week."""
        unsure = await self._unsure()
        return [
            entity
            for entity in await self._purposeless()
            if f"entity:{entity.entity_id}:role" not in unsure
        ]

    async def _purposeless(self) -> list[Entity]:
        """Switches HouseMind only watches because their purpose is unknown: no role, not
        left alone, no setting of a device, no group, not asked already as a lamp from a
        label (B-220), and nothing the findings already hold."""
        assert self.gate is not None
        answers = await self._label_answers()
        asked = {e for e, _ in self._lamp_candidates(answers)}
        asked |= {k[len(LAMP) :] for k in answers if k.startswith(LAMP)}
        return [
            entity
            for entity in sorted(self.model.entities.values(), key=lambda e: e.entity_id)
            if entity.domain == "switch"
            and not (entity.hidden or entity.disabled or entity.entity_category)
            and entity.platform != "housemind"
            and entity.entity_id not in self.gate.state.roles
            and entity.entity_id not in self.gate.state.openers  # a door: no AI, no question
            and entity.entity_id not in self.gate.state.excluded
            and entity.entity_id not in self.model.groups
            and entity.entity_id not in asked
            and not self.knowledge.known("entity", entity.entity_id, "role")
            and not self._less_than_a_switch(entity.entity_id)
        ]

    async def unknown_functions(self) -> list[Entity]:
        """Entities whose function the rules cannot tell, for AI (design 51.4): not hidden,
        left out or a group, nothing in the findings, and not what an AI was unsure of this
        week."""
        if self.world is None or self.gate is None:
            return []
        unsure = await self._unsure()
        return [
            entity
            for entity in sorted(self.model.entities.values(), key=lambda e: e.entity_id)
            if entity.entity_id in self.world.functions_open
            and not (entity.hidden or entity.disabled)
            and entity.platform != "housemind"
            and entity.entity_id not in self.gate.state.excluded
            and entity.entity_id not in self.model.groups
            and not self.knowledge.known("entity", entity.entity_id, "function")
            and f"entity:{entity.entity_id}:function" not in unsure
        ]

    async def _read_functions(self) -> None:
        """What each entity is, where the rules cannot tell (E-429, design 51.4): neutral facts
        with what the integration calls it, never a name a person gave. A few groups at a time;
        the rest at the next quarter hour. Behaviour first: AI gets what is left (E-201)."""
        if self.ai.status() != "on" or self.world is None:
            return
        if self._deriving is not None and not self._deriving.done():
            await asyncio.wait({self._deriving})
        todo = await self.unknown_functions()
        found = Found()
        per = PER_FUNCTIONS
        for _ in range(FUNCTION_QUESTIONS):
            group, todo = todo[:per], todo[per:]
            if not group:
                break
            facts = {e.entity_id: entity_facts(self.model, self.ai.labels, e) for e in group}
            found = await self.ai.suggest_functions(facts, self._language())
            cut = found.reason == "cut_off"
            if cut and (per > FEWEST_FUNCTIONS or found.answers):
                # It did not fit: what was whole counts, the rest again at once, half as many
                # at a time (E-453).
                todo = [e for e in group if e.entity_id not in found.answers] + todo
                group = [e for e in group if e.entity_id in found.answers]
                per = max(FEWEST_FUNCTIONS, per // 2)
            elif found.reason is not None:
                todo = group + todo
                break
            not_sure = [f"entity:{e.entity_id}:function" for e in group]
            for entity_id, answer in found.answers.items():
                if answer["function"] == UNKNOWN:
                    continue
                not_sure.remove(f"entity:{entity_id}:function")
                why = self.ai.plain(answer.get("why"), self._plain_names())
                value = {"function": answer["function"], "why": why}
                await self.knowledge.suggest(
                    "entity",
                    entity_id,
                    "function",
                    value,
                    _sure(answer),
                    self.clock.now(),
                    found.log_id,
                )
            await self._new_unsure(not_sure)  # not sure, or not answered: again in a week
            self._apply_knowledge()  # a setting is left alone from now on (E-430)
        await self._read("functions", found)
        if todo and found.reason is None:
            self._house_unread.add("functions")  # more at the next quarter hour

    async def _read_switches(self) -> None:
        if self.ai.status() != "on" or self.gate is None:
            return
        if self._deriving is not None and not self._deriving.done():
            await asyncio.wait({self._deriving})  # behaviour first: AI gets what is left (E-201)
        switches = (await self.unknown_switches())[:PER_QUESTION]
        facts = {e.entity_id: entity_facts(self.model, self.ai.labels, e) for e in switches}
        found = (
            await self.ai.suggest_switches(facts, self.switch_roles(), self._language())
            if facts
            else Found()
        )
        not_sure: list[str] = []
        for entity_id, answer in found.answers.items():
            role = answer["role"]
            if role == "unknown":
                not_sure.append(f"entity:{entity_id}:role")
                continue
            value = {"role": role, "why": self.ai.plain(answer.get("why"), self._plain_names())}
            await self.knowledge.suggest(
                "entity", entity_id, "role", value, _sure(answer), self.clock.now(), found.log_id
            )
        await self._new_unsure(not_sure)
        if found.answers:
            self._apply_knowledge()  # watched and learned as that kind from now on
        await self._read("switches", found)

    # -- automations and scripts (B-346) -----------------------------------------------------

    def _placed(self, entity_id: str, steps: tuple[HAStep, ...]) -> bool:
        """The rules know what it is about: a part from what it acts on, or from its
        category or labels in Home Assistant (E-345)."""
        assert self.gate is not None
        parts = parts_of(self.model, self.gate.state.roles, steps)
        if parts and parts != ["other"]:
            return True
        entity = self.model.entities.get(entity_id)
        hint = automation_hint(self.model, self.label_hints, entity) if entity else None
        return hint is not None and hint[0] in PARTS and hint[0] != "other"

    async def unplaced(self) -> dict[str, dict[str, Any]]:
        """Automations and scripts the rules cannot place, not described yet: their structure
        only (``privacy.plain_structure``)."""
        assert self.gate is not None
        unsure = await self._unsure()
        found: dict[str, dict[str, Any]] = {}
        for reading in sorted(self.automations.readings.values(), key=lambda r: r.entity_id):
            a = reading.automation
            if a.readable and not self._placed(a.entity_id, a.steps):
                found[a.entity_id] = plain_structure(
                    self.model, self.ai.labels, a.triggers, a.conditions, a.steps, a.other
                )
        for script in sorted(self.scripts, key=lambda s: s.entity_id):
            if script.readable and not self._placed(script.entity_id, script.steps):
                found[script.entity_id] = plain_structure(
                    self.model, self.ai.labels, steps=script.steps
                )
        return {
            entity_id: structure
            for entity_id, structure in found.items()
            if not self.knowledge.known("automation", entity_id, "summary")
            and f"automation:{entity_id}:summary" not in unsure
        }

    async def _read_automations(self) -> None:
        if self.ai.status() != "on" or self.gate is None:
            return
        items = dict(list((await self.unplaced()).items())[:PER_QUESTION])
        found = await self.ai.explain_automations(items, self._language()) if items else Found()
        not_sure: list[str] = []
        for entity_id, answer in found.answers.items():
            text = self.ai.plain(answer["summary"], self._plain_names())
            if text is None:  # a label it made up, or no text: asked again after a week
                not_sure.append(f"automation:{entity_id}:summary")
                continue
            value = {"text": text, "part": answer["part"], "take_over": answer["take_over"]}
            await self.knowledge.suggest(
                "automation", entity_id, "summary", value, None, self.clock.now(), found.log_id
            )
        await self._new_unsure(not_sure)
        await self._read("automations", found)

    # -- afleiden uit gedrag (E-203, B-358) -----------------------------------------------

    def derive_soon(self) -> None:
        """Derive from behaviour beside the core when a day has passed (E-203): without AI
        too, and never waited for (E-414). The AI's question about switches waits for it, so
        it only gets what behaviour did not place (E-201)."""
        if self._deriving is not None and not self._deriving.done():
            return
        self._deriving = self._beside_kept(self._derive_when_due(), "behaviour")

    def _beside_kept(self, work: Coroutine[Any, Any, None], name: str) -> asyncio.Task[None]:
        task = asyncio.create_task(work, name=f"housemind-{name}")
        self._side.add(task)  # stopping cancels it; the tests wait for it (settled)

        def done(task: asyncio.Task[None]) -> None:
            self._side.discard(task)
            if not task.cancelled() and task.exception() is not None:
                _LOGGER.error("%s failed", name, exc_info=task.exception())

        task.add_done_callback(done)
        return task

    async def _derive_when_due(self) -> None:
        if not self.learning_allowed:
            return  # reading the history waits for the yes to learning, as E-257 does
        last = await self.db.get_setting("behaviour_read")
        now = self.clock.now()
        recent = isinstance(last, str) and now - datetime.fromisoformat(last) < DERIVE_AGAIN
        if recent and not self._counted_restarts():
            return  # a guess that counted restarts is looked at again at once (E-459)
        await self.derive_from_behaviour()

    def _counted_restarts(self) -> list[str]:
        """Switches derived as a lamp before HouseMind knew a restart is no switching (E-459):
        nobody said Klopt or Klopt niet, so they are looked at again with the real switching."""
        return [
            f.subject
            for f in self.knowledge.of("entity", "role")
            if f.derived and f.value.get("rule") == "with_lamp" and not f.value.get("real")
        ]

    async def derive_from_behaviour(self) -> int:
        """What devices of unknown purpose are, from what the recorder kept of the last week
        (E-203, B-358): read only, a day at a time, and it stays in the house (E-15). Each
        finding is a suggestion "afgeleid uit gedrag" with a certainty; how many are new.
        Home Assistant away: tried again at the next quarter hour."""
        if self.gate is None or self.world is None:
            return 0
        now = self.clock.now()
        new = 0
        again = self._counted_restarts()
        for entity_id in again:
            await self.knowledge.forget("entity", entity_id, "role")
        if again:
            self._apply_knowledge()  # no lamp any more until the real switching says so
        try:
            oldest = await self.ha.history_start()
            start = max(oldest, now - LOOK_BACK) if oldest is not None else now
            for switch in await self._purposeless() if now - start >= timedelta(hours=1) else []:
                found = await self._derive_switch(switch, start, now)
                if found is not None and await self._derived(switch, "role", *found):
                    new += 1
            place = self.place()
            for sensor in self._light_candidates() if now - start >= WINDOW else []:
                values = [
                    (change.at, number)
                    async for changes in self._windows([sensor.entity_id], start, now)
                    for change in changes
                    if (number := _number(change.state)) is not None
                ]
                light = behaviour.follows_sunset(values, place, now) if place else None
                if light is None:
                    continue
                value = {"measures": "illuminance", "rule": "sunset", "facts": light[1]}
                if await self._derived(sensor, "measures", value, light[0]):
                    new += 1
        except (ConnectionError, TimeoutError):
            _LOGGER.warning("Could not read the history to derive from behaviour; trying later")
            return 0
        await self.db.set_setting("behaviour_read", now.isoformat())
        if new:
            self._apply_knowledge()  # watched and learned as that kind from now on
            await self._notify()
        return new

    async def _derived(
        self, entity: Entity, field: str, value: dict[str, Any], certainty: float
    ) -> bool:
        found = await self.knowledge.suggest(
            "entity", entity.entity_id, field, value, certainty, self.clock.now(), None, "behaviour"
        )
        return found is not None

    async def _derive_switch(
        self, switch: Entity, start: datetime, end: datetime
    ) -> tuple[dict[str, Any], float] | None:
        """An appliance from its power sensor, or the lamp it goes on and off with."""
        power = next(
            (
                e
                for e in sorted(self.model.entities.values(), key=lambda e: e.entity_id)
                if switch.device_id is not None
                and e.device_id == switch.device_id
                and e.domain == "sensor"
                and e.device_class == "power"
                and not e.disabled
            ),
            None,
        )
        if power is not None:
            runs = behaviour.Runs()
            async for changes in self._windows([power.entity_id], start, end):
                for change in changes:
                    runs.feed(change.at, _number(change.state))
            runs.end(end)
            found = behaviour.appliance(runs.runs)
            if found is not None:
                certainty, ran = found
                return {"role": "appliance_start", "rule": "appliance", "facts": ran}, certainty
        hint = entity_hint(self.model, self.label_hints, switch)
        if hint is not None and hint[0] != "lighting":
            return None  # Home Assistant's labels say it is something else, heating say (E-459)
        area = self.model.area_of(switch.entity_id)
        lamps = [
            e.entity_id
            for e in (self.model.entities.values() if area is None else self.model.in_area(area.id))
            if e.domain == "light"
            and e.entity_id not in self.model.groups
            and not (e.hidden or e.disabled or e.entity_category)
        ]
        if not lamps:
            return None
        rows: dict[str, list[tuple[datetime, str]]] = {}
        async for changes in self._windows([switch.entity_id, *lamps], start, end):
            for change in changes:
                rows.setdefault(change.entity_id, []).append((change.at, change.state))
        switched = behaviour.flips(rows.get(switch.entity_id, []), start)  # no restarts
        lit = {lamp: behaviour.changes_of(rows.get(lamp, []), start) for lamp in lamps}
        together = behaviour.with_lamp(switched, lit)
        if together is None:
            return None
        lamp, share, times = together
        facts = {"lamp": lamp, "pct": round(share * 100), "times": times}
        # "real": counted only real switching, so never looked at again (E-459).
        value = {"role": "light", "rule": "with_lamp", "facts": facts, "real": True}
        return value, min(behaviour.SURE, share)

    async def _windows(
        self, entity_ids: list[str], start: datetime, end: datetime
    ) -> AsyncIterator[list[HistoryChange]]:
        """The recorder a day at a time, oldest first."""
        at = start
        while at < end:
            until = min(end, at + WINDOW)
            yield await self.ha.history(entity_ids, at, until)
            at = until

    def _light_candidates(self) -> list[Entity]:
        """Sensors in a room that say nothing about what they measure, with a number for a
        value: maybe light. Not on a device that does something itself (its own light), and
        only where HouseMind knows the place of the house, for when the sun sets."""
        assert self.world is not None and self.gate is not None
        if self.place() is None:
            return []
        return [
            entity
            for entity in sorted(self.model.entities.values(), key=lambda e: e.entity_id)
            if entity.domain == "sensor"
            and entity.device_class is None
            and entity.unit in LIGHT_UNITS
            and not (entity.hidden or entity.disabled or entity.entity_category)
            and entity.entity_id not in self.gate.state.excluded
            and self.model.area_of(entity.entity_id) is not None
            and not self.world._on_a_device_that_acts(entity)
            and _number(getattr(self.world.states.get(entity.entity_id), "state", None)) is not None
            and not self.knowledge.known("entity", entity.entity_id, "measures")
        ]

    # -- twijfel van de regels (design 50.2, B-359) --------------------------------------

    async def _note_doubts(self) -> None:
        """Where the rules are unsure: a suggestion with their own guess, for an
        administrator's Klopt, also without AI; with AI also its pick (a job beside the core).
        Until someone answers, the rules hold."""
        if self.gate is None or self.world is None:
            return
        now = self.clock.now()
        found: list[tuple[str, str, str, dict[str, Any]]] = []
        sensors = temperature_doubts(self.temperatures, self.world.states)
        for area_id, (guess, options) in sensors.items():
            found.append(("area", area_id, "temperature", {"sensor": guess, "options": options}))
        excluded = self.gate.state.excluded
        words = self._opening_words()
        contacts = opening_doubts(self.model, words, excluded, self.area_roles)
        for entity_id, yes in contacts.items():
            found.append(("entity", entity_id, "opening", {"yes": yes}))
        for entity_id, yes in self.world.status_led_doubts().items():
            if entity_id not in excluded:
                found.append(("entity", entity_id, "status_led", {"yes": yes}))
        # A toilet or bathroom with only its door (E-504): is it a small room with one door?
        rooms = box_rooms(self.model, self.world.occupancy, self.openings, self.area_roles)
        for area_id, door in rooms.items():
            found.append(("area", area_id, "one_door", {"yes": True, "door": door}))
        for kind, subject, field, value in found:
            await self.knowledge.suggest(kind, subject, field, value, None, now, None, "rules")

    async def _read_doubts(self) -> None:
        """AI's pick where the rules doubt (B-359): neutral facts only – the kinds of the
        devices, make and type, the kind of the room, and what the integration calls a device
        (E-429) –, never a name a person gave (50.8). Its pick is offered for Klopt in place of
        the rules' guess; until a Klopt the rules hold. A pick made before the AI got the
        integration's name (in the real house it called a sensor's LED a lamp) is asked once
        more."""
        if self.ai.status() != "on" or self.gate is None:
            return
        unsure = await self._unsure()
        waiting = [
            f
            for f in self._to_confirm()
            if f.field in DOUBTS
            and (f.source == "rules" or (f.source == "ai" and not f.value.get("named")))
            and f.key not in unsure
        ][:PER_QUESTION]
        questions = {f.key: self._doubt_facts(f) for f in waiting}
        found = await self.ai.pick_doubts(questions, self._language()) if questions else Found()
        not_sure: list[str] = []
        by_key = {f.key: f for f in waiting}
        for key, answer in found.answers.items():
            finding, pick = by_key[key], answer["pick"]
            if pick is None and finding.source == "ai":
                # An old guess made without the integration's name, and now the AI is not sure:
                # back to the rules' own guess, not asked again (a sensor's LED is no lamp).
                field_of = "sensor" if finding.field == "temperature" else "yes"
                guess = finding.value.get("rules", finding.value.get(field_of))
                back = {**finding.value, field_of: guess, "why": None, "named": True}
                await self.knowledge.suggest(
                    finding.kind,
                    finding.subject,
                    finding.field,
                    back,
                    None,
                    self.clock.now(),
                    None,
                    "rules",
                    again=True,
                )
                continue
            if pick is None:
                not_sure.append(key)
                continue
            guess = finding.value.get("sensor", finding.value.get("yes"))
            rules = finding.value.get("rules", guess)  # the rules' own guess, also when asked again
            value = {**finding.value, "sensor" if finding.field == "temperature" else "yes": pick}
            why = self.ai.plain(answer.get("why"), self._plain_names())
            value |= {"rules": rules, "why": why, "named": True}
            await self.knowledge.suggest(
                finding.kind,
                finding.subject,
                finding.field,
                value,
                _sure(answer),
                self.clock.now(),
                found.log_id,
                "ai",
                again=True,
            )
        await self._new_unsure(not_sure)
        await self._read("doubts", found)

    def _doubt_facts(self, finding: Finding) -> dict[str, Any]:
        """One doubt for an AI: the question, the kind of the room and per device only the
        neutral facts of ``privacy.entity_facts``."""
        labels = self.ai.labels
        if finding.field == "temperature":
            options = [e for e in finding.value.get("options", []) if e in self.model.entities]
            return {
                "question": "room_temperature",
                "room": self.area_roles.get(finding.subject, "other"),
                "options": {
                    e: entity_facts(self.model, labels, self.model.entities[e]) for e in options
                },
            }
        if finding.field == "one_door":  # the room's kind and its door, nothing more (E-504)
            door = self.model.entities.get(str(finding.value.get("door")))
            return {
                "question": "one_door_room",
                "room": self.area_roles.get(finding.subject, "other"),
                "device": entity_facts(self.model, labels, door) if door is not None else {},
            }
        entity = self.model.entities[finding.subject]
        area = self.model.area_of(entity.entity_id)
        return {
            "question": "house_opening" if finding.field == "opening" else "status_light",
            "room": self.area_roles.get(area.id, "other") if area else "unknown",
            "device": entity_facts(self.model, labels, entity),
        }

    # -- an administrator's answer (E-207) ---------------------------------------------------

    async def answer_knowledge(self, key: str, yes: bool, role: str | None = None) -> bool:
        """Klopt or Klopt niet; for a room also another role, for a room's temperature
        another of its sensors (``role``). Kept: never asked again. What an automation does
        is only to read – there is nothing to answer. The question Vesta asked about it, if
        any, goes away everywhere (E-252)."""
        parts = split_key(key)
        if parts is None:
            return False
        kind, subject, field = parts
        now = self.clock.now()
        found = self.knowledge.get(kind, subject, field)
        if kind == "area" and subject in self.model.areas and field == "role":
            if role is not None:
                if role not in ROLE_ORDER and role != "other":  # other: Geen van deze (E-486)
                    return False
                own: dict[str, Any] = {"role": role, "why": None}
                finding = await self.knowledge.answer(kind, subject, field, True, now, own)
            else:
                finding = await self.knowledge.answer(kind, subject, field, yes, now)
            if finding is None:
                return False
            if finding.status == CONFIRMED:
                await self._set_room_role(subject, str(finding.value["role"]))
                await self._resolve(f"room_kind:{subject}")  # its question is answered (E-486)
            elif found is not None and found.status == CONFIRMED:
                await self._reset_room_role(subject)  # Klopt toch niet (E-525)
        elif kind == "area" and subject in self.model.areas and field == "temperature":
            options = list(found.value.get("options", [])) if found is not None else []
            if role is not None and (role not in options or role not in self.model.entities):
                return False
            chosen = {"sensor": role, "options": options} if role is not None else None
            if await self.knowledge.answer(kind, subject, field, yes, now, chosen) is None:
                return False
            self._sort_temperatures()  # the room's own sensor from now on (B-359)
        elif kind == "area" and field == "one_door" and subject in self.model.areas:
            if role is not None:
                return False
            # A small room with one door, or not (E-504): its door counts from now on.
            if await self.knowledge.answer(kind, subject, field, yes, now) is None:
                return False
        elif kind == "entity" and subject in self.model.entities and field == "function" and role:
            # Anders…: what it is, in a person's own words (E-432); theirs from now on.
            if role not in FUNCTIONS or role == UNKNOWN:
                return False
            own_function = {"function": role, "why": None}
            if await self.knowledge.answer(kind, subject, field, True, now, own_function) is None:
                return False
        elif kind == "entity" and subject in self.model.entities and field == "role" and role:
            # Iets anders… for a switch (E-580): what it drives, in the administrator's words.
            if role not in SWITCH_CHOICES or not subject.startswith("switch."):
                return False
            own_role = {"role": role, "why": None}
            if await self.knowledge.answer(kind, subject, field, True, now, own_role) is None:
                return False
        elif kind == "entity" and subject in self.model.entities and role is None:
            if (
                field == "summary"
                or await self.knowledge.answer(kind, subject, field, yes, now) is None
            ):
                return False
            if field == "opening":
                self._sort_openings()
        else:
            return False
        self._apply_knowledge()
        await self._resolve(f"{ASK}{key}")
        await self._notify()
        return True

    async def reopen_knowledge(self, key: str) -> bool:
        """Terug naar te bevestigen (E-525, B-449): an answer taken back, from Bevestigd or
        Klopt niet. Until someone answers again, the rules hold – for a room the kind its name
        gives, or 'soort onbekend' with one question (E-486). Vesta may ask it again."""
        parts = split_key(key)
        if parts is None or parts[2] == "summary":
            return False
        kind, subject, field = parts
        if kind == "area" and subject not in self.model.areas:
            return False
        if kind == "entity" and subject not in self.model.entities:
            return False
        found = self.knowledge.get(kind, subject, field)
        if not await self.knowledge.reopen(kind, subject, field, self.clock.now()):
            return False
        if kind == "area" and field == "role" and found is not None:
            if found.status == CONFIRMED:
                await self._reset_room_role(subject)
        elif kind == "area" and field == "temperature":
            self._sort_temperatures()
        elif field == "opening":
            self._sort_openings()
        elif kind == "entity" and field == "role":
            await self._label_role_back(subject, found)  # a label's lamp, or its question
        self._apply_knowledge()
        await self._notify()
        return True

    # -- Vesta's one question a day on Geïnformeerd (E-207, E-294, B-360) ------------------

    async def _ask_knowledge(self, snapshot: WorldState) -> None:
        """An administrator on Geïnformeerd does not see the page: Vesta asks them at most one
        question a day about something to confirm, with Klopt and Klopt niet, within their
        question budget. Not for anyone else, nothing when there is nothing to confirm, and
        never a second while one is open. One that no longer fits is withdrawn."""
        # 'Soort onbekend' is no Klopt question: it has its own, with a list (E-486).
        waiting = {
            f.key: f
            for f in self._to_confirm()
            if not (f.kind == "area" and f.field == "role" and self._kind_unknown(f.subject))
        }
        for topic in self._tracked(ASK):
            if topic[len(ASK) :] not in waiting:
                await self._resolve(topic)
        informed = [p for p in self._people if p.is_admin and p.level == 2]
        if not waiting or not informed or any(t.startswith(ASK) for t in self._raised):
            return
        today = snapshot.local.date().isoformat()
        if await self.db.get_setting("knowledge_asked") == today:
            return
        finding = sorted(waiting.values(), key=lambda f: (-(f.certainty or 0.0), f.key))[0]
        text_key, params = self._question(finding)
        topic = f"{ASK}{finding.key}"
        raised = await self._raise(
            Kind.LEARNING, topic, text_key, params, snapshot, "admins", KLOPT, to=informed
        )
        if raised is not None:
            await self.db.set_setting("knowledge_asked", today)

    def _question(self, finding: Finding) -> tuple[str, dict[str, Any]]:
        """Her question, in words that Klopt and Klopt niet answer. A switch in her own words
        ("de schakelaar in de keuken"); a sensor, contact or light by its own name, so that it
        is clear which one."""
        if finding.kind == "area":
            area = self.model.areas[finding.subject].name
            if finding.field == "temperature":
                sensor = self.model.entities[str(finding.value["sensor"])].name
                return "notify.knowledge.temperature", {"name": sensor, "area": area}
            if finding.field == "one_door":
                return "notify.knowledge.one_door", {"area": area}
            what = {"t": f"ui.knowledge.room.{finding.value.get('role')}"}
            return "notify.knowledge.room", {"area": area, "what": what}
        name = self.model.entities[finding.subject].name
        if finding.field == "role":
            what = {"t": f"ui.knowledge.is.{finding.value.get('role')}"}
            return "notify.knowledge.switch", {"device": name, "what": what}
        if finding.field == "opening":
            yes = "yes" if finding.value.get("yes") else "no"
            return f"notify.knowledge.opening_{yes}", {"name": name}
        if finding.field == "function":
            what = {"t": f"ui.function.{finding.value.get('function')}"}
            return "notify.knowledge.function", {"name": name, "what": what}
        return f"notify.knowledge.{finding.field}", {"name": name}

    async def _understand_quarter(self, snapshot: WorldState) -> None:
        """The quarter-hour look of understanding the house, also without AI: deriving from
        behaviour once a day, beside the core, and Vesta's question on Geïnformeerd."""
        self.derive_soon()
        await self._ask_knowledge(snapshot)

    # -- for the page (E-207) ----------------------------------------------------------------

    def recognised_devices(self) -> int:
        """How many devices the catalogue recognised itself, without AI (E-202): a kind of
        its own, or a known domain HouseMind can operate."""
        assert self.gate is not None
        devices: set[str] = set()
        for entity in self.model.entities.values():
            if entity.hidden or entity.disabled or entity.entity_category:
                continue
            if entity.domain in NOT_A_DEVICE or entity.entity_id in self._knowledge_roles:
                continue
            match = recognise(self.catalog, entity, self.gate.state.roles.get(entity.entity_id))
            if match.kind is not None and (
                match.quality is MatchQuality.SPECIFIC or match.kind.actions
            ):
                devices.add(entity.device_id or entity.entity_id)
        return len(devices)

    def reading_now(self) -> bool:
        return any(kind in READING for kind in self.ai_jobs.busy)

    def still_open(self, finding: Finding) -> bool:
        """A suggestion that still matters: its room is still "other", its switch has no role
        from anywhere else, and what a doubt or a sensor is about is still there. One the house
        outgrew (a room renamed into a known word, a label someone said yes to) waits
        silently: it is never asked again either."""
        if finding.kind == "area" and finding.field == "temperature":
            sensor = finding.value.get("sensor")
            return finding.subject in self.model.areas and sensor in self.model.entities
        if finding.kind == "area" and finding.field == "one_door":
            # Still a toilet or bathroom with only its door (E-504): a motion sensor or a second
            # door added, and the question has gone.
            return self.world is not None and finding.subject in box_rooms(
                self.model, self.world.occupancy, self.openings, self.area_roles
            )
        if finding.kind == "area":
            return self.area_roles.get(finding.subject) == "other"
        if finding.kind == "entity" and finding.field == "role":
            return finding.subject in self._knowledge_roles
        if finding.kind == "entity" and finding.field == "status_led":
            # What it is says it already (design 51): one question, not two.
            return finding.subject in self.model.entities and not self.knowledge.known(
                "entity", finding.subject, "function"
            )
        if finding.kind == "entity":
            return finding.subject in self.model.entities
        return True

    def _to_confirm(self) -> list[Finding]:
        """Everything that waits for an administrator's Klopt and still matters."""
        return [
            f
            for f in sorted(self.knowledge.findings.values(), key=lambda f: f.key)
            if f.status == SUGGESTED and f.field != "summary" and self.still_open(f)
        ]


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sure(answer: dict[str, Any]) -> float | None:
    certainty = answer.get("certainty")
    if isinstance(certainty, int | float) and not isinstance(certainty, bool):
        return round(min(1.0, max(0.0, float(certainty))), 2)
    return None
