"""Wanneer doe ik wat (E-385 – E-387, B-287 – B-292).

Per skill in a room one plain sentence: when HouseMind does it, with what it learned – which
lamps, how bright per part of the day, the usual time, the wait. Each step has its own words:
Autonomous "Ik doe …", Propose "Ik vraag of …", Observe "Ik zou …" (``when.<skill>.do``,
``.ask`` and ``.would`` in the language files). What it does not know yet, the sentence says
("welke lampen je meestal gebruikt, leer ik nog"); a house rule or a fixed limit that takes part
follows as a clause (", maar nooit voor 7:00"). On Expert ``whenDetail``: the sensors and
devices by their names in Home Assistant, the thresholds and the wait, where it comes from and
how sure it is ("14 van de 16 keer goed").

It only reads what the skills and learning use (knowledge/skills/*.yaml, brain/skills.py,
brain/learning.py, brain/rules.py) and changes nothing; "Wat ik nu zou doen" (E-295) says what
HouseMind would do right now, this says when.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import time
from functools import partial
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from housemind.brain.daylight import is_own
from housemind.brain.learning import (
    BANDS,
    EPS,
    HISTORY,
    HISTORY_WEIGHT,
    LEVEL_BAND,
    LEVEL_SAMPLES,
    Result,
    band,
    part_of_slot,
)
from housemind.brain.levels import FELT, STANDS, between, stand_of
from housemind.brain.rules import CATEGORIES, HouseRule, rule_text
from housemind.brain.skills import (
    ENTRY_WINDOW,
    PASS_WAIT,
    arrival_minutes,
    light_off_hold,
)
from housemind.core.model import WHOLE_HOUSE, Entity
from housemind.ha.adapter import HALightRhythm
from housemind.knowledge.catalog import Step
from housemind.world.calendar import DayType
from housemind.world.occupancy import MOTION_CLASSES, PRESENCE_CLASSES

if TYPE_CHECKING:
    from housemind.brain.names import Names
    from housemind.runtime import HouseMind

# The words per step: what it does itself, what it asks, what it would do.
STEP_KEYS = {Step.AUTONOMOUS: "do", Step.PROPOSE: "ask", Step.OBSERVE: "would"}
# The parts of the day in the order a sentence says them: the dark ones first.
PARTS = ("evening", "night", "morning", "day")
DAY_TYPES = (DayType.WORKDAY, DayType.FREE, DayType.HOLIDAY, DayType.DEVIANT)
# What a skill asks of a device, as a house rule names it (brain/rules.py): a rule "never
# turn on in the hall before 7:00" takes part in light on entry there.
CATEGORY = {
    "light_on_entry": "on",
    "night_light": "on",
    "light_on_person_outside": "on",
    "light_outside_evening": "on",
    "light_outside_arrival": "on",
    "wake_light": "on",
    "bedtime_dim": "on",
    "light_off_sleep": "off",
    "light_off_leave": "off",
    "ventilate_humidity": "on",
    "ventilate_co2": "on",
    "covers_close_dark": "close",
    "covers_close_sleep": "close",
    "covers_open_morning": "open",
    "heat_on_presence": "heat",
    "cool_on_presence": "heat",
    "keep_temperature": "heat",
    "setback_away": "heat",
    "setback_sleep": "heat",
    "window_open_setback": "heat",
    "wake_warmth": "heat",
    "energy_boiler": "heat",
    "energy_heat_pump": "heat",
}
# The Home Assistant domains of the devices a part steers, for a rule on a whole domain.
DOMAINS = {
    "lighting": ("light", "switch"),
    "covers": ("cover",),
    "climate": ("climate",),
    "air": ("fan",),
    "water": ("valve",),
    "media": ("media_player",),
    "cleaning": ("vacuum",),
    "garden": ("lawn_mower",),
    "energy": ("water_heater", "climate"),
}
SUN_DARK, LUX_DARK, LUX_DARK_OUTSIDE = 3.0, 40.0, 20.0  # world/calendar.py is_dark
# How the sentences name rooms and devices ("in de gang", "de lamp in de gang"), kept until the
# house changes (Names.reset): looking that up again at every screen update took most of the
# card in a big house (S4).
_KEPT: WeakKeyDictionary[Names, tuple[int, dict[tuple[str, ...], str]]] = WeakKeyDictionary()
# The sensors per room for the Expert line, kept the same way: a change in the house refreshes
# the names, its light sensors and each room's devices together.
_SENSED: WeakKeyDictionary[Names, tuple[int, dict[str, dict[str, list[str]]]]] = WeakKeyDictionary()


def _hhmm(moment: time) -> str:
    """7:00, not 07:00: as people say a time."""
    return f"{moment.hour}:{moment.minute:02d}"


class When:
    """The sentences for one viewer's language, made once per card: what is looked up for every
    row (the learned values, the sensors per room) is gathered once (S4)."""

    def __init__(self, hm: HouseMind, lang: str) -> None:
        assert hm.learning is not None and hm.skills is not None
        self.hm = hm
        self.lang = lang
        self.learning = hm.learning
        self.skills = hm.skills
        self.now = hm.clock.now()
        self._values: dict[tuple[str, str], list[tuple[str, str, float, float]]] | None = None
        self._sensors: dict[str, dict[str, list[str]]] | None = None
        self._turn_on: dict[str, frozenset[str]] = {}  # what a lamp can do when it goes on
        self._texts: dict[tuple[Any, ...], str] = {}  # many rows say the same pieces
        self.expert = False  # the Expert line is made only for Expert (S4)
        kept = _KEPT.get(hm.names)
        if kept is None or kept[0] != hm.names.generation:
            kept = (hm.names.generation, {})
            _KEPT[hm.names] = kept
        self._kept = kept[1]

    # -- words -----------------------------------------------------------------------------

    def t(self, key: str, count: int | None = None, **params: Any) -> str:
        seen = (key, count, *sorted(params.items()))
        found = self._texts.get(seen)
        if found is None:
            found = self._texts[seen] = self.hm.languages.text(
                self.lang, key, count=count, **params
            )
        return found

    def _and(self, items: list[str]) -> str:
        if len(items) < 2:
            return "".join(items)
        return f"{', '.join(items[:-1])} {self.t('ui.and')} {items[-1]}"

    def _or(self, items: list[str]) -> str:
        if len(items) < 2:
            return "".join(items)
        return f"{', '.join(items[:-1])} {self.t('when.or')} {items[-1]}"

    def _minutes(self, minutes: float) -> str:
        if minutes >= 60 and minutes % 60 == 0:
            return self.t("when.n.hours", count=int(minutes // 60))
        return self.t("when.n.minutes", count=round(minutes))

    def _seconds(self, seconds: float) -> str:
        if seconds >= 60 and seconds % 60 == 0:
            return self._minutes(seconds / 60)
        return self.t("when.n.seconds", count=round(seconds))

    def _temp(self, value: float) -> str:
        shown = self.hm.languages.number(self.lang, round(value * 2) / 2)
        return f"{shown} °C"

    def where(self, area_id: str) -> str:
        """ "in de gang", kept until the house changes; the card's own names use it too."""
        key = ("where", self.lang, area_id)
        found = self._kept.get(key)
        if found is None:
            found = self.hm.names.where(area_id, self.lang)
            if not found and area_id != WHOLE_HOUSE:
                area = self.hm.model.areas.get(area_id)
                found = area.name if area else ""
            self._kept[key] = found
        return found

    def _things(self, entities: list[Entity], fallback: str, area_id: str) -> str:
        """Devices as Vesta says them: "de lamp in de gang"; one as an object (German case)."""
        if not entities:
            return self.t(f"when.thing.{fallback}", where=self.where(area_id)).strip()
        key = ("things", self.lang, *(e.entity_id for e in entities))
        found = self._kept.get(key)
        if found is None:
            names = self.hm.names
            if len(entities) == 1:
                found = names.device(entities[0].entity_id, self.lang, "acc")
            else:
                found = names.group([e.entity_id for e in entities], self.lang)
            self._kept[key] = found
        return found

    @staticmethod
    def _ha(entities: Iterable[Entity]) -> str:
        """Names as Home Assistant shows them (Expert, E-387)."""
        return ", ".join(e.name for e in entities)

    # -- what the house has ----------------------------------------------------------------

    def _role(self, area_id: str) -> str:
        return self.hm.area_roles.get(area_id, "other")

    def _devices(self, area_id: str, domain: str, kind: str = "") -> list[Entity]:
        if area_id == WHOLE_HOUSE or area_id not in self.hm.model.areas:
            return []
        return self.skills._entities(area_id, domain, kind)

    def _in_room(self, area_id: str, domains: tuple[str, ...]) -> list[Entity]:
        model = self.hm.model
        found = [
            e
            for e in model.in_area(area_id)
            if e.domain in domains and not e.hidden and not e.entity_category
        ]
        return sorted(found, key=lambda e: e.entity_id)

    def _can(self, entity: Entity) -> frozenset[str]:
        """What a lamp can be given when it goes on (brightness, colour), once per card."""
        found = self._turn_on.get(entity.entity_id)
        if found is None:
            kind = self.skills._kind(entity)
            allowed = kind.allowed(f"{entity.domain}.turn_on") if kind else None
            found = frozenset(allowed.values) if allowed is not None else frozenset()
            self._turn_on[entity.entity_id] = found
        return found

    def _dims(self, entity: Entity) -> bool:
        return "brightness_pct" in self._can(entity)

    def _kelvin(self, entities: list[Entity]) -> bool:
        return any("color_temp_kelvin" in self._can(e) for e in entities)

    def _sensed(self, area_id: str) -> dict[str, list[str]]:
        """Per room the sensors that tell what goes on there, by kind, kept until the house
        changes."""
        if not self.expert:
            return {}
        if self._sensors is None:
            names = self.hm.names
            kept = _SENSED.get(names)
            if kept is not None and kept[0] == names.generation:
                self._sensors = kept[1]
                return self._sensors.get(area_id, {})
            self._sensors = {}
            _SENSED[names] = (names.generation, self._sensors)
            world = self.hm.world
            occupancy = world.occupancy if world is not None else None
            ignored = occupancy.ignored if occupancy is not None else set()
            for entity in sorted(self.hm.model.entities.values(), key=lambda e: e.entity_id):
                if entity.entity_id in ignored or entity.hidden or entity.entity_category:
                    continue
                if occupancy is not None:
                    area = occupancy.area_for(entity.entity_id)
                else:
                    area = self.hm.model.area_of(entity.entity_id)
                if area is None:
                    continue
                kind = None
                cls = entity.device_class
                if entity.domain == "binary_sensor" and cls in PRESENCE_CLASSES | MOTION_CLASSES:
                    kind = "presence"
                elif entity.domain == "binary_sensor" and cls in ("window", "door", "opening"):
                    openings = self.skills.openings
                    kind = "opening" if openings is None or entity.entity_id in openings else None
                elif entity.domain == "sensor" and cls == "humidity":
                    kind = "humidity"
                elif entity.domain == "sensor" and cls in ("carbon_dioxide", "co2"):
                    kind = "co2"
                elif entity.domain == "camera":
                    kind = "camera"
                if kind is not None:
                    self._sensors.setdefault(area.id, {}).setdefault(kind, []).append(entity.name)
            if world is not None:
                for area_id_ in self.hm.model.areas:
                    found = world.lux_cache.get(area_id_) if world.lux_cache is not None else None
                    lux = found if found is not None else world._lux_sensors(area_id_)
                    if lux:
                        self._sensors.setdefault(area_id_, {})["lux"] = [
                            e.name if (e := self.hm.model.entities.get(i)) else i for i in lux
                        ]
        return self._sensors.get(area_id, {})

    # -- what it learned -------------------------------------------------------------------

    def _learned(self, skill: str, area_id: str) -> list[tuple[str, str, float, float]]:
        if self._values is None:
            self._values = {}
            for (s, a, bucket, key), (mean, weight, _) in self.learning.values.items():
                self._values.setdefault((s, a), []).append((bucket, key, mean, weight))
        return self._values.get((skill, area_id), [])

    def _value(self, skill: str, area_id: str, key: str) -> float | None:
        """The value learned for the whole day, as the skill falls back to it (H3.3)."""
        return self.learning.value(skill, area_id, "*", key)

    def _levels(
        self, skill: str, area_id: str, key: str, parts: tuple[str, ...]
    ) -> dict[str, tuple[float, bool]]:
        """Per part of the day the value a skill uses (brightness …): what it learned in that
        part of the day, else for the whole day, else the start knowledge – and whether it was
        learned."""
        start = self.hm.skill_params.get(skill, {}).get(key)
        values = self._learned(skill, area_id)
        whole = self._value(skill, area_id, key)
        out: dict[str, tuple[float, bool]] = {}
        for part in parts:
            own: list[tuple[float, float]] = []
            past: list[tuple[float, float]] = []
            for bucket, found, mean, weight in values:
                history = bucket.startswith(HISTORY)
                context = bucket.removeprefix(HISTORY)
                if found != key or context in ("*", "automation") or part_of_slot(context) != part:
                    continue
                if history and weight >= 2.0 * HISTORY_WEIGHT - EPS:
                    past.append((mean, weight))
                elif not history and weight >= 2.0 - EPS:
                    own.append((mean, weight))
            chosen = own or past
            if chosen:
                total = sum(w for _, w in chosen)
                out[part] = (sum(m * w for m, w in chosen) / total, True)
            elif whole is not None:
                out[part] = (whole, True)
            elif isinstance(start, dict) and isinstance(start.get(part), int | float):
                out[part] = (float(start[part]), False)
            elif isinstance(start, int | float):
                out[part] = (float(start), False)
        return out

    def _in_part(self, skill: str, area_id: str, key: str) -> set[str]:
        """The parts of the day it learned something of its own in."""
        found = set()
        for bucket, k, _, weight in self._learned(skill, area_id):
            context = bucket.removeprefix(HISTORY)
            part = part_of_slot(context)
            if k == key and part is not None and weight >= 2.0 * HISTORY_WEIGHT - EPS:
                found.add(part)
        return found

    def _dark(self, area_id: str) -> tuple[str, float, int] | None:
        """When it is dark enough here as people showed it (E-268): the measure that fits
        best, below which value a lamp went on, and how many times – as learning decides it."""
        best: tuple[float, str, dict[str, list[float]], float] | None = None
        for measure in ("sun", "lux"):
            table = self.learning._table(area_id, measure, self.now)
            total = sum(light + none for light, none in table.values())
            if total < LEVEL_SAMPLES - EPS:
                continue
            fit = sum(max(light, none) for light, none in table.values()) / total
            if best is None or fit > best[0] + 1e-9:
                best = (fit, measure, table, total)
        if best is None:
            return None
        _, measure, table, total = best
        edges = BANDS[measure]
        below: float | None = None
        for index in range(len(edges) + 1):
            low = edges[0] - 1.0 if index == 0 else edges[index - 1]
            light, none = table.get(band(measure, low), [0.0, 0.0])
            if light + none < LEVEL_BAND - EPS:
                continue
            if light < none:
                break
            below = edges[index] if index < len(edges) else None
            if below is None:
                break
        if below is None:
            return None
        return measure, below, round(total)

    # -- house rules and limits ------------------------------------------------------------

    def _rules(
        self, skill: str, area_id: str, devices: list[Entity]
    ) -> tuple[list[str], list[str], bool]:
        """The clauses of the house rules that take part here, their own words for Expert, and
        whether a rule sets the step."""
        hm = self.hm
        clauses: list[str] = []
        texts: list[str] = []
        by_rule = False
        category = CATEGORY.get(skill)
        actions = CATEGORIES.get(category, set()) if category else set()
        ids = {e.entity_id for e in devices}
        domains = set(DOMAINS.get(str(hm.skill_params.get(skill, {}).get("domain")), ()))
        for rule in hm.rules.rules:
            if rule.skill is not None:
                if rule.skill != skill or rule.scope_type != "area" or rule.scope_id != area_id:
                    continue
                by_rule = True
                key = "when.rule.always" if rule.effect == "always" else "when.rule.skill_never"
                clauses.append(self.t(key))
            elif self._applies(rule, area_id, ids, domains, actions):
                clauses.append(self._clause(rule))
            else:
                continue
            texts.append(rule_text(rule, self.lang, hm.languages, hm.model))
        return list(dict.fromkeys(clauses)), texts, by_rule

    @staticmethod
    def _applies(
        rule: HouseRule, area_id: str, ids: set[str], domains: set[str], actions: set[str]
    ) -> bool:
        if not actions or rule.category is None:
            return False
        if rule.effect == "limit" and rule.category != "heat":
            return False
        if rule.effect not in ("never", "limit"):
            return False
        if not actions & CATEGORIES.get(rule.category, set()):
            return False
        if rule.scope_type == "house":
            return True
        if rule.scope_type == "area":
            return rule.scope_id == area_id
        if rule.scope_type == "domain":
            return rule.scope_id in domains or any(i.startswith(rule.scope_id + ".") for i in ids)
        return rule.scope_type == "entity" and rule.scope_id in ids

    def _clause(self, rule: HouseRule) -> str:
        if rule.effect == "limit" and rule.value is not None:
            return self.t("when.rule.limit", value=self._temp(rule.value))
        if rule.time_from is not None and rule.time_to is not None:
            return self.t("when.rule.between", start=_hhmm(rule.time_from), end=_hhmm(rule.time_to))
        if rule.time_to is not None:
            return self.t("when.rule.before", time=_hhmm(rule.time_to))
        if rule.time_from is not None:
            return self.t("when.rule.after", time=_hhmm(rule.time_from))
        return self.t("when.rule.never")

    # -- where it comes from and how sure ----------------------------------------------------

    def _source(self, skill: str, area_id: str, by_rule: bool, situations: float) -> list[str]:
        learning = self.learning
        said: list[str] = []
        if by_rule:
            said.append(self.t("when.source.rule"))
        if skill in learning.start_knowledge:
            said.append(self.t("when.source.start"))
        taught = learning.taught_by.get((skill, area_id))
        if taught:
            said.append(self.t("when.source.automation", name=taught))
        if learning.history_situations(skill, area_id) > EPS:
            said.append(self.t("when.source.history"))
        own = situations - learning.history_situations(skill, area_id)
        if own > EPS or learning.own_answers(skill, area_id) > EPS:
            said.append(self.t("when.source.learned"))
        return said

    def _seen(self, skill: str, area_id: str) -> Seen:
        learning, now = self.learning, self.now
        _, situations, _ = learning.score(skill, area_id, now)
        hits, misses, corrections = (
            learning.total(skill, area_id, r, now)
            for r in (Result.HIT, Result.MISS, Result.CORRECTION)
        )
        approved, rejected, _ = learning.approval(skill, area_id, now)
        return Seen(situations, hits, misses + corrections, approved, rejected)

    def _sure(self, seen: Seen) -> list[str]:
        hits, judged = seen.hits, seen.hits + seen.other
        approved, rejected = seen.approved, seen.rejected
        said = []
        if round(judged) > 0:
            said.append(self.t("when.detail.sure", hits=round(hits), total=round(judged)))
        if round(approved + rejected) > 0:
            said.append(
                self.t("when.detail.answers", yes=round(approved), total=round(approved + rejected))
            )
        return said or [self.t("when.detail.nothing")]

    # -- one skill in one room -------------------------------------------------------------

    def row(
        self, skill: str, area_id: str, step: Step, level: int, seen: Seen | None = None
    ) -> dict[str, str]:
        """``when``, and on Expert ``whenDetail``, for one skill in one room. ``seen``: what
        the card already counted for this row, so it is not counted twice (S4)."""
        params = self.hm.skill_params.get(skill, {})
        self.expert = level >= 4
        said = Said(self.expert)
        found = getattr(self, f"_say_{skill}", None)
        if found is not None:
            found(said, area_id, params)
        else:
            self._say_energy(said, area_id, params)
        clauses, texts, by_rule = self._rules(skill, area_id, said.devices)
        fill: dict[str, Any] = {"where": self.where(area_id), **said.fill}
        core = self.t(f"when.{skill}.{STEP_KEYS[step]}", **fill)
        tail = "".join([*said.limits, *clauses, *said.unknown])
        out = {"when": " ".join(f"{core}{tail}".split()) + "."}
        if level >= 4:
            detail = [*said.detail]
            seen = seen or self._seen(skill, area_id)
            source = self._source(skill, area_id, by_rule, seen.situations)
            if source:
                detail.append(self.t("when.detail.source", what=", ".join(source)))
            detail += self._sure(seen)
            detail += [self.t("when.detail.rule", text=text) for text in texts]
            out["whenDetail"] = " · ".join(d for d in detail if d)
        return out

    # -- lighting --------------------------------------------------------------------------

    def _lamps(self, said: Said, area_id: str, usual: bool = True) -> list[Entity]:
        """The lamps it turns on here: those people use, else all – honestly said."""
        lamps = self._devices(area_id, "light")
        used = self.skills.usual_lights(area_id) if usual else None
        mine = [e for e in lamps if used and e.entity_id in used]
        if mine:
            said.fill["lamps"] = self._things(mine, "lamps", area_id)
            lamps = mine
        elif len(lamps) > 1:
            said.fill["lamps"] = self.t("when.thing.lamps", where=self.where(area_id)).strip()
            if usual:
                said.unknown.append(self.t("when.unknown.lamps"))
        else:
            said.fill["lamps"] = self._things(lamps, "lamps", area_id)
        said.devices = lamps
        if lamps:
            said.note(lambda: self.t("when.detail.lamps", names=self._ha(lamps)))
        return lamps

    def _say_light_on_entry(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        skill = "light_on_entry"
        lamps = self._lamps(said, area_id)
        sensed = self._sensed(area_id)
        if sensed.get("presence"):
            said.note(
                first=True,
                make=lambda: self.t("when.detail.sensors", names=", ".join(sensed["presence"])),
            )
        passing = self.learning.passing(area_id, self.now)
        said.fill["entry"] = self.t("when.entry.stay" if passing else "when.entry.in")
        said.fill["levels"] = ""
        night = self._role(area_id) not in params.get("never_at_night_in", ())
        if not night:
            said.limits.append(self.t("when.limit.not_at_night"))
        groups = self._day_groups(lamps)
        if groups:
            # The day's rhythm says it (E-466): as it really goes, also on the card (E-470).
            said.fill["levels"] = self.t(
                "when.day_rhythm",
                high=round(max(r.max_brightness for r in groups)),
                low=round(min(r.min_brightness for r in groups)),
            )
            for rhythm in groups:
                said.note(partial(self._detail_day_group, rhythm))
        elif any(self._dims(e) for e in lamps):
            own = next(
                (r for e in lamps if (r := self.hm.daylight.group_of(e.entity_id)) is not None),
                None,
            )
            if own is not None and is_own(own):
                said.note(partial(self._detail_own_rhythm, own))  # E-471
            extra = self._in_part(skill, area_id, "brightness_pct")
            parts = tuple(
                p
                for p in PARTS
                if (p in ("evening", "night") or p in extra) and (night or p != "night")
            )
            levels = self._levels(skill, area_id, "brightness_pct", parts)
            said.fill["levels"] = self._said_levels(levels)
            said.note(
                lambda: self._detail_levels(levels, self._kelvins(skill, area_id, lamps, parts))
            )
        learned = self._dark(area_id) if self.expert else None
        if learned is not None:
            measure, below, count = learned
            said.note(
                lambda: self.t(
                    "when.detail.dark_learned",
                    measure=self.t(f"when.detail.measure.{measure}"),
                    value=f"{below:g}",
                    count=count,
                )
            )
        elif sensed.get("lux"):
            said.note(
                lambda: self.t(
                    "when.detail.dark_lux", value=f"{LUX_DARK:g}", names=", ".join(sensed["lux"])
                )
            )
        else:
            said.note(lambda: self.t("when.detail.dark_sun", value=f"{SUN_DARK:g}"))
        self._open_space(said, area_id)
        if passing:
            said.note(lambda: self.t("when.detail.passing", time=self._seconds(PASS_WAIT.seconds)))
        else:
            said.note(lambda: self.t("when.detail.entry", time=self._seconds(ENTRY_WINDOW.seconds)))

    def _open_space(self, said: Said, area_id: str) -> None:
        """A room that shares a sensor with the room next to it (E-530): its own light, or the
        light there does for it, as people showed it."""
        skills = self.hm.skills
        others = skills.open_space.get(area_id, set()) if skills is not None else set()
        if not others:
            return
        shared = self.learning.shared_with(area_id, others, self.now)
        own = sorted(o for o in others if o not in shared)

        def rooms(ids: list[str]) -> str:
            return ", ".join(a.name for o in ids if (a := self.hm.model.areas.get(o)) is not None)

        if shared:
            count = sum(shared.values())
            said.note(
                lambda: self.t("when.detail.shared_light", rooms=rooms(sorted(shared)), count=count)
            )
        if own:
            said.note(lambda: self.t("when.detail.own_light", rooms=rooms(own)))

    def _day_groups(self, lamps: list[Entity]) -> list[HALightRhythm]:
        """The groups of the day's rhythm that give these lamps their light now."""
        book = self.hm.daylight
        found: dict[str, HALightRhythm] = {}
        for lamp in lamps:
            rhythm = book.group_of(lamp.entity_id)
            if rhythm is None or is_own(rhythm):
                continue  # its own rhythm is between the levels this card already shows
            if book.is_on(rhythm) or book.ours(rhythm):
                found[rhythm.entry_id] = rhythm
        return sorted(found.values(), key=lambda r: r.title)

    def _detail_own_rhythm(self, rhythm: HALightRhythm) -> str:
        return self.t(
            "when.detail.day_rhythm_own",
            low=self._stand(rhythm.min_brightness),  # in standen (E-494)
            high=self._stand(rhythm.max_brightness),
            warm=round(rhythm.min_kelvin),
            white=round(rhythm.max_kelvin),
        )

    def _detail_day_group(self, rhythm: HALightRhythm) -> str:
        who = "ours" if self.hm.daylight.ours(rhythm) else "theirs"
        return self.t(
            f"when.detail.day_rhythm_{who}",
            name=rhythm.title,
            low=round(rhythm.min_brightness),
            high=round(rhythm.max_brightness),
            warm=round(rhythm.min_kelvin),
            white=round(rhythm.max_kelvin),
        )

    def _said_levels(self, levels: dict[str, tuple[float, bool]]) -> str:
        """ ": ’s avonds op 55%, ’s nachts op 15%": equal neighbours together."""
        groups: list[tuple[list[str], str, bool]] = []
        for part, (value, _) in levels.items():
            stand = self._stand(value)  # in standen, each lamp its own (E-494)
            exact = stand_of(value) is not None or value > FELT[-1]
            if groups and groups[-1][1] == stand:
                groups[-1][0].append(part)
            else:
                groups.append(([part], stand, exact))
        if not groups:
            return ""
        items = [
            self.t(
                "when.level" if exact else "when.level_free",
                parts=self._and([self.t(f"when.part.{p}") for p in parts]),
                stand=stand,
            )
            for parts, stand, exact in groups
        ]
        return self.t("when.levels", list=", ".join(items))

    def _stand(self, felt: float) -> str:
        """A felt percentage in words of the standen: ‘Normaal’, ‘tussen Normaal en Helder’,
        ‘nog zachter dan Gedimd’ (E-494)."""
        low, high = between(felt)

        def name(index: int) -> str:
            return self.t(f"stands.stand.{STANDS[index]}")

        if low is None:
            return self.t("stands.softer", stand=name(0))
        if low == high or high is None:
            return name(low)
        return self.t("stands.between", low=name(low), high=name(high))

    def _kelvins(
        self, skill: str, area_id: str, lamps: list[Entity], parts: tuple[str, ...]
    ) -> dict[str, tuple[float, bool]]:
        if not self._kelvin(lamps):
            return {}
        return self._levels(skill, area_id, "color_temp_kelvin", parts)

    def _detail_levels(
        self, levels: dict[str, tuple[float, bool]], kelvins: dict[str, tuple[float, bool]]
    ) -> str:
        items = []
        for part, (value, learned) in levels.items():
            text = f"{self.t(f'when.part.{part}')} {self._stand(value)}"
            if part in kelvins:
                text += f" {round(kelvins[part][0])} K"
            if learned:
                text += f" ({self.t('when.detail.learned')})"
            items.append(text)
        return self.t("when.detail.levels", list=", ".join(items))

    def _say_light_off_leave(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        lamps = self._lamps(said, area_id, usual=False)
        sensed = self._sensed(area_id)
        if sensed.get("presence"):
            said.note(
                first=True,
                make=lambda: self.t("when.detail.sensors", names=", ".join(sensed["presence"])),
            )
        # The wait this room taught, else the start knowledge for its kind (E-444).
        hold, learned = light_off_hold(
            params, self.learning, area_id, self._role(area_id), False, bool(sensed.get("presence"))
        )
        hold = round(hold * 2) / 2  # to the half minute: "1,5 min", not "1,37 min"
        said.fill["hold"] = self._minutes(hold) if hold == int(hold) else self._seconds(hold * 60)
        shown = self.hm.languages.number(self.lang, hold)
        said.note(
            lambda: (
                self.t("when.detail.hold", minutes=shown)
                + (f" ({self.t('when.detail.learned')})" if learned else "")
            )
        )
        # A lamp with a wait of its own goes on its own time (E-575).
        waits = self.hm.light_levels
        own = [(e, w) for e in lamps if (w := waits.off_wait(e.entity_id)) is not None]
        if own:
            said.note(
                lambda: self.t(
                    "when.detail.own_wait",
                    list=", ".join(
                        f"{e.name} "
                        + (self.t("when.detail.own_wait_now") if w == 0 else self._minutes(w))
                        for e, w in own
                    ),
                )
            )
        said.fill["dim"] = ""
        if any(self._dims(e) for e in lamps):
            seconds = float(params.get("dim_warning_seconds", 60))
            stand = self._stand(float(params.get("dim_to_pct", 15)))
            said.fill["dim"] = self.t("when.dim", time=self._seconds(seconds))
            said.note(lambda: self.t("when.detail.dim", seconds=f"{seconds:g}", stand=stand))

    def _say_light_on_person_outside(
        self, said: Said, area_id: str, params: dict[str, Any]
    ) -> None:
        self._lamps(said, area_id, usual=False)
        minutes = float(self.hm.security.words.light_minutes)
        said.fill["hold"] = self._minutes(minutes)
        cameras = self._sensed(area_id).get("camera")
        if cameras:
            said.note(
                first=True, make=lambda: self.t("when.detail.cameras", names=", ".join(cameras))
            )
        said.note(lambda: self.t("when.detail.hold", minutes=f"{minutes:g}"))

    def _say_light_outside_evening(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._lamps(said, area_id, usual=False)
        learned = self._value("light_outside_evening", area_id, "off_minutes")
        if learned is not None:  # minutes after noon
            clock = (720 + round(learned)) % 1440
            shown = _hhmm(time(clock // 60, clock % 60))
        else:
            hour, _, minute = str(params.get("off_at", "23:30")).partition(":")
            shown = _hhmm(time(int(hour), int(minute or 0)))
        said.fill["off"] = shown
        said.note(
            lambda: self.t(
                "when.detail.dark_outside", sun=f"{SUN_DARK:g}", lux=f"{LUX_DARK_OUTSIDE:g}"
            )
        )
        if learned is not None:
            said.note(lambda: self.t("when.detail.evening_off", time=shown))
        vary = float(params.get("vary_minutes", 15))
        said.note(lambda: self.t("when.detail.evening_away", minutes=f"{vary:g}"))

    def _say_light_outside_arrival(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._lamps(said, area_id, usual=False)
        minutes, learned = arrival_minutes(params, self.learning, area_id)
        minutes = round(minutes * 2) / 2  # to the half minute; the sentence says whole ones
        said.fill["hold"] = self._minutes(max(1, round(minutes)))
        shown = self.hm.languages.number(self.lang, minutes)
        said.note(
            lambda: self.t(
                "when.detail.dark_outside", sun=f"{SUN_DARK:g}", lux=f"{LUX_DARK_OUTSIDE:g}"
            )
        )
        longest = float(params.get("max_minutes", 30))
        mark = f" ({self.t('when.detail.learned')})" if learned else ""
        said.note(
            lambda: self.t(
                "when.detail.arrival_on", minutes=shown, learned=mark, most=f"{longest:g}"
            )
        )
        said.note(lambda: self.t("when.detail.arrival_where"))

    def _say_night_light(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._lamps(said, area_id, usual=False)
        said.fill["stand"] = self._stand(float(params.get("brightness_pct", 15)))
        sensed = self._sensed(area_id)
        if sensed.get("presence"):
            said.note(
                first=True,
                make=lambda: self.t("when.detail.sensors", names=", ".join(sensed["presence"])),
            )

    def _usual_times(self, kind: str, said: Said, phrase: str) -> None:
        """ ": op een werkdag rond 6:40, …" from the rhythm of the house; else honest."""
        times: dict[str, list[str]] = {}
        for day in DAY_TYPES[:3]:
            found = self.hm.rhythm.usual(kind, day)
            if found is not None:
                times.setdefault(_hhmm(found), []).append(self.t(f"when.day.{day.value}"))
        if not times:
            said.fill["time"] = ""
            said.unknown.append(self.t(f"when.unknown.{kind}"))
            return
        items = [self.t(phrase, days=self._or(days), time=at) for at, days in times.items()]
        said.fill["time"] = self.t("when.levels", list=", ".join(items))

    def _say_wake_light(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._lamps(said, area_id)
        ramp = float(params.get("ramp_minutes", 20))
        said.fill["ramp"] = self._minutes(ramp)
        self._usual_times("wake", said, "when.on_day")
        steps = ", ".join(self._stand(float(p)) for p in params.get("steps_pct", []))
        said.note(lambda: self.t("when.detail.ramp", minutes=f"{ramp:g}", steps=steps))
        said.note(lambda: self.t("when.detail.alarm"))

    def _say_bedtime_dim(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._lamps(said, area_id, usual=False)
        said.fill["stand"] = self._stand(float(params.get("dim_to_pct", 15)))
        said.fill["before"] = self._minutes(float(params.get("minutes_before", 30)))
        self._usual_times("bed", said, "when.before_day")

    def _say_light_off_sleep(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._lamps(said, area_id, usual=False)
        self._usual_times("bed", said, "when.on_day")  # Welterusten (E-517)

    # -- covers ----------------------------------------------------------------------------

    def _covers(self, said: Said, area_id: str) -> None:
        covers = self._devices(area_id, "cover", "cover.window_covering")
        said.devices = covers
        said.fill["covers"] = self._things(covers, "covers", area_id)
        if covers:
            said.note(lambda: self.t("when.detail.devices", names=self._ha(covers)))

    def _say_covers_close_dark(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._covers(said, area_id)
        learned = self._value("covers_close_dark", area_id, "sun_elevation")
        if learned is not None:
            said.fill["moment"] = self.t("when.moment.learned")
            value = self.hm.languages.number(self.lang, learned)
            said.note(lambda: self.t("when.detail.close_sun", value=value))
        else:
            said.fill["moment"] = self.t("when.moment.dark")
            said.note(
                lambda: self.t(
                    "when.detail.dark_outside", sun=f"{SUN_DARK:g}", lux=f"{LUX_DARK_OUTSIDE:g}"
                )
            )

    def _say_covers_close_sleep(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._covers(said, area_id)
        self._usual_times("bed", said, "when.on_day")  # Welterusten (E-517)

    def _say_covers_open_morning(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._covers(said, area_id)
        times: dict[str, list[str]] = {}
        for day in DAY_TYPES:
            at = str(params.get("not_before", {}).get(day.value, "08:00"))
            hour, _, minute = at.partition(":")
            shown = _hhmm(time(int(hour), int(minute or 0)))
            times.setdefault(shown, []).append(self.t(f"when.day.{day.value}"))
        said.fill["not_before"] = ", ".join(
            self.t("when.not_before", days=self._or(days), time=at) for at, days in times.items()
        )

    def _say_covers_open_wake(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        """A bedroom's curtains when the people there are up, with what HouseMind sees there
        (E-482, E-483): someone in bed, someone in the room, or nothing."""
        self._covers(said, area_id)
        world = self.hm.world
        signal, sensors = (
            ("none", ()) if world is None else world.sleep_watch.signals.get(area_id, ("none", ()))
        )
        if signal == "none":
            after = self._value("covers_open_wake", area_id, "after_wake_minutes")
            if after is not None:
                said.fill["moment"] = self.t("when.moment.wake_learned", minutes=f"{after:.0f}")
            else:
                said.fill["moment"] = self.t("when.moment.wake_none")
        else:
            said.fill["moment"] = self.t("when.moment.wake_up")
            seen = [self.hm.model.entities[e] for e in sensors if e in self.hm.model.entities]
            said.note(lambda: self.t(f"when.detail.sleep_{signal}", names=self._ha(seen)))
            wait = self._value("covers_open_wake", area_id, "wait_minutes")
            minutes = f"{float(params.get('wait_minutes', 5)) if wait is None else wait:.0f}"
            said.note(lambda: self.t("when.detail.wake_wait", minutes=minutes))
        sun = self._value("covers_open_wake", area_id, "sun_elevation")
        if sun is not None:
            value = self.hm.languages.number(self.lang, sun)
            said.note(lambda: self.t("when.detail.open_sun", value=value))

    # -- climate ---------------------------------------------------------------------------

    def _heating(self, said: Said, area_id: str) -> list[Entity]:
        room = self.skills.room_climates
        found = [
            e for e in self._devices(area_id, "climate") if room is None or e.entity_id in room
        ]
        said.devices = found
        said.fill["heating"] = self._things(found, "heating", area_id)
        if found:
            said.note(lambda: self.t("when.detail.devices", names=self._ha(found)))
        return found

    def _wish(self, skill: str, area_id: str, table: str, default: float) -> tuple[float, bool]:
        learned = self._value(skill, area_id, "temperature")
        if learned is not None:
            return learned, True
        values = self.hm.skill_params.get(skill, {}).get(table, {})
        role = self._role(area_id)
        return float(values.get(role, values.get("other", default))), False

    def _say_temp(self, said: Said, area_id: str, skill: str, table: str, default: float) -> float:
        self._heating(said, area_id)
        value, learned = self._wish(skill, area_id, table, default)
        said.fill["temp"] = self._temp(value)
        if not learned:
            said.unknown.append(self.t("when.unknown.temp"))
        mark = self.t("when.detail.learned" if learned else "when.source.start")
        said.note(lambda: self.t("when.detail.temp", temp=self._temp(value), how=mark))
        return value

    def _say_heat_on_presence(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._say_temp(said, area_id, "heat_on_presence", "comfort_c", 19.0)

    def _say_cool_on_presence(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._say_temp(said, area_id, "cool_on_presence", "cool_c", 24.0)

    def _say_keep_temperature(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._heating(said, area_id)

    def _say_setback_away(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._heating(said, area_id)
        comfort, _ = self._wish("heat_on_presence", area_id, "comfort_c", 19.0)
        minimum = float(params.get("minimum_c", 15))
        said.fill["temp"] = self._temp(max(minimum, comfort - float(params.get("setback_c", 3))))
        said.fill["hours"] = self._minutes(float(params.get("after_hours", 24)) * 60)
        said.limits.append(self.t("when.limit.minimum", temp=self._temp(minimum)))

    def _say_setback_sleep(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._heating(said, area_id)
        values = params.get("sleep_c", {})
        role = self._role(area_id)
        said.fill["temp"] = self._temp(float(values.get(role, values.get("other", 17.0))))

    def _say_window_open_setback(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._heating(said, area_id)
        said.fill["temp"] = self._temp(float(params.get("setback_to_c", 12)))
        said.fill["minutes"] = self._minutes(float(params.get("after_minutes", 3)))
        openings = self._sensed(area_id).get("opening")
        if openings:
            said.note(
                first=True, make=lambda: self.t("when.detail.sensors", names=", ".join(openings))
            )

    def _say_wake_warmth(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._heating(said, area_id)
        comfort, _ = self._wish("heat_on_presence", area_id, "comfort_c", 19.0)
        said.fill["temp"] = self._temp(comfort)
        said.fill["lead"] = self._minutes(float(params.get("lead_minutes", 30)))
        self._usual_times("wake", said, "when.on_day")

    # -- air, water and the household ------------------------------------------------------

    def _fans(self, said: Said, area_id: str, kind: str) -> None:
        fans = self._devices(area_id, "fan")
        said.devices = fans
        said.fill["fans"] = self._things(fans, "fans", area_id)
        sensors = self._sensed(area_id).get(kind)
        if sensors:
            said.note(lambda: self.t("when.detail.sensors", names=", ".join(sensors)))
        if fans:
            said.note(lambda: self.t("when.detail.devices", names=self._ha(fans)))

    def _say_ventilate_humidity(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._fans(said, area_id, "humidity")
        said.fill["on"] = f"{params.get('on_pct', 70):g}"
        said.fill["off"] = f"{params.get('off_pct', 60):g}"

    def _say_ventilate_co2(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._fans(said, area_id, "co2")
        said.fill["on"] = f"{params.get('on_ppm', 1000):g}"
        said.fill["off"] = f"{params.get('off_ppm', 800):g}"

    def _say_water_leak_flow(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        valves = [
            e for e in self._in_room(area_id, ("valve",)) if e.device_class in ("water", None)
        ]
        said.devices = valves
        said.fill["valve"] = self._things(valves, "valve", area_id)
        said.fill["sleep"] = self._minutes(float(params.get("sleep_hours", 2)) * 60)
        said.fill["away"] = self._minutes(float(params.get("away_minutes", 30)))
        said.fill["home"] = self._minutes(float(params.get("home_hours", 6)) * 60)
        said.limits.append(self.t("when.limit.valve"))
        if valves:
            said.note(lambda: self.t("when.detail.devices", names=self._ha(valves)))

    def _household(
        self,
        said: Said,
        area_id: str,
        domain: str,
        fallback: str,
        found: list[Entity] | None = None,
    ) -> None:
        found = self._in_room(area_id, (domain,)) if found is None else found
        said.devices = found
        said.fill["device"] = self._things(found, fallback, area_id)
        if found:
            said.note(lambda: self.t("when.detail.devices", names=self._ha(found)))

    def _say_media_off_empty(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        household = self.hm.household  # only the TVs, not a speaker or doorbell (E-63)
        tvs = household.tvs(area_id) if household is not None else None
        self._household(said, area_id, "media_player", "tv", tvs)
        said.fill["minutes"] = self._minutes(float(params.get("after_minutes", 30)))

    def _hours(self, said: Said, params: dict[str, Any], default: tuple[int, int]) -> None:
        start, end = (int(x) for x in params.get("hours", default))
        said.fill["start"], said.fill["end"] = _hhmm(time(start)), _hhmm(time(end % 24))

    def _say_vacuum_when_away(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._household(said, area_id, "vacuum", "vacuum")
        self._hours(said, params, (9, 18))

    def _say_mower_when_dry(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        self._household(said, area_id, "lawn_mower", "mower")
        self._hours(said, params, (10, 18))

    def _say_fridge_door(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        said.fill["device"] = self.t("when.thing.fridge")
        said.fill["minutes"] = self._minutes(float(params.get("after_minutes", 3)))

    # -- energy (E-347): for the whole house -----------------------------------------------

    def _say_energy(self, said: Said, area_id: str, params: dict[str, Any]) -> None:
        energy = self.hm.energy
        if (
            params.get("domain") == "energy"
            and energy is not None
            and not energy.planning(self.now)
        ):
            said.limits.append(self.t("when.limit.measuring"))


@dataclass(frozen=True, slots=True)
class Seen:
    """What learning counted for one skill in one room: situations, right, different or put
    back (faded), and yes and no to its proposals – the same numbers as the card."""

    situations: float
    hits: float
    other: float
    approved: float
    rejected: float


class Said:
    """The pieces of one sentence: what fills its places, the clauses after it, the devices
    it is about (for the house rules) and the Expert line."""

    __slots__ = ("detail", "devices", "expert", "fill", "limits", "unknown")

    def __init__(self, expert: bool) -> None:
        self.fill: dict[str, str] = {}
        self.limits: list[str] = []
        self.unknown: list[str] = []
        self.devices: list[Entity] = []
        self.detail: list[str] = []
        self.expert = expert

    def note(self, make: Callable[[], str], first: bool = False) -> None:
        """A piece of the Expert line, only made for Expert."""
        if self.expert and first:
            self.detail.insert(0, make())
        elif self.expert:
            self.detail.append(make())


def when(
    hm: HouseMind, lang: str, skill: str, area_id: str, step: Step, level: int
) -> dict[str, str]:
    """One skill in one room: ``when``, and on Expert ``whenDetail`` (B-287, B-288)."""
    return When(hm, lang).row(skill, area_id, step, level)
