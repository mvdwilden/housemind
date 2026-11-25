"""House rules (E-87, E-103, E-104, H3.8).

A house rule is always structured, however it was entered (text, voice, builder, an
"always"/"never" answer). Without AI, text and speech use fixed sentence patterns from the
language files; the rule is always read back in plain language before it is saved.

Effects
- never   HouseMind never does this action on this scope (within the time window)
- always  for a skill × area: HouseMind may do this itself (rule beats learning, never beats
          the catalogue's maximum step or the hard limits)
- limit   a value limit, e.g. never warmer than 21 °C

Order (E-104): safety and hard limits → house rules → learned behaviour.

A rule may hold only on some days of the week ("in het weekend", E-420); they are kept with its
other conditions, so it needs no new column.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

from housemind.core.model import HouseModel
from housemind.i18n.catalog import Languages
from housemind.knowledge.catalog import Step
from housemind.memory.database import Database
from housemind.safety.gate import ActionRequest

# Action categories, so "turn on" covers light.turn_on and switch.turn_on.
CATEGORIES: dict[str, set[str]] = {
    "on": {"light.turn_on", "switch.turn_on", "fan.turn_on", "humidifier.turn_on"},
    "off": {"light.turn_off", "switch.turn_off", "fan.turn_off", "humidifier.turn_off"},
    "open": {"cover.open_cover", "cover.set_cover_position"},
    "close": {"cover.close_cover"},
    "heat": {"climate.set_temperature", "water_heater.set_temperature"},
}
WEEKDAYS = (0, 1, 2, 3, 4)  # Monday to Friday
WEEKEND = (5, 6)


@dataclass(frozen=True, slots=True)
class HouseRule:
    scope_type: str  # "entity" | "area" | "domain" | "house"
    scope_id: str
    effect: str  # "never" | "always" | "limit"
    category: str | None = None  # key of CATEGORIES
    value: float | None = None
    time_from: time | None = None
    time_to: time | None = None
    skill: str | None = None
    modes: tuple[str, ...] = ()
    id: int | None = None
    source_text: str | None = None
    days: tuple[int, ...] = ()  # weekdays it holds on, Monday 0; none: every day (E-420)

    def in_window(self, local: datetime) -> bool:
        if self.days and local.weekday() not in self.days:
            return False
        if self.time_from is None and self.time_to is None:
            return True
        now = local.time()
        start = self.time_from or time(0, 0)
        end = self.time_to or time(23, 59, 59)
        return start <= now < end if start <= end else (now >= start or now < end)


@dataclass(slots=True)
class RuleBook:
    model: HouseModel
    rules: list[HouseRule] = field(default_factory=list)

    def _scope_matches(self, rule: HouseRule, entity_id: str, area_id: str | None) -> bool:
        if rule.scope_type == "house":
            return True
        if rule.scope_type == "entity":
            return rule.scope_id == entity_id
        if rule.scope_type == "domain":
            return entity_id.startswith(rule.scope_id + ".")
        area = area_id
        if area is None:
            found = self.model.area_of(entity_id)
            area = found.id if found else None
        return rule.scope_type == "area" and rule.scope_id == area

    def veto(self, request: ActionRequest, local: datetime, mode: str) -> str | None:
        """A reason when a rule forbids this request, else ``None`` (used by the gate)."""
        for rule in self.rules:
            if rule.modes and mode not in rule.modes:
                continue
            if not self._scope_matches(rule, request.entity_id, request.area_id):
                continue
            if not rule.in_window(local):
                continue
            if (
                rule.effect == "never"
                and rule.category
                and request.action in CATEGORIES.get(rule.category, set())
            ):
                return f"rule_{rule.id}"
            if rule.effect == "limit" and rule.category == "heat" and rule.value is not None:
                temperature = request.data.get("temperature")
                if isinstance(temperature, int | float) and temperature > rule.value:
                    return f"rule_{rule.id}"
        return None

    def step_override(self, skill: str, area_id: str | None) -> Step | None:
        for rule in self.rules:
            if (
                rule.effect == "always"
                and rule.skill == skill
                and rule.scope_type == "area"
                and rule.scope_id == area_id
            ):
                return Step.AUTONOMOUS
            if (
                rule.effect == "never"
                and rule.skill == skill
                and rule.scope_type == "area"
                and rule.scope_id == area_id
            ):
                return Step.OBSERVE
        return None

    def conflicts(self, new: HouseRule) -> list[HouseRule]:
        """Existing rules with the opposite effect on the same scope and action (E-104)."""
        opposite = {"never": "always", "always": "never"}
        found = []
        for rule in self.rules:
            if rule.scope_type != new.scope_type or rule.scope_id != new.scope_id:
                continue
            same_target = (new.skill and rule.skill == new.skill) or (
                new.category and rule.category == new.category
            )
            apart = bool(new.days and rule.days and not set(new.days) & set(rule.days))
            if same_target and opposite.get(rule.effect) == new.effect and not apart:
                found.append(rule)
        return found

    # -- storage ------------------------------------------------------------------------

    async def load(self, db: Database) -> None:
        self.rules = []
        for row in await db.fetchall("SELECT * FROM house_rules WHERE enabled = 1 ORDER BY id"):
            conditions: dict[str, Any] = json.loads(row["conditions"] or "{}")
            self.rules.append(
                HouseRule(
                    scope_type=row["scope_type"],
                    scope_id=row["scope_id"],
                    effect=row["effect"],
                    category=row["action"],
                    value=row["value"],
                    time_from=time.fromisoformat(row["time_from"]) if row["time_from"] else None,
                    time_to=time.fromisoformat(row["time_to"]) if row["time_to"] else None,
                    skill=conditions.get("skill"),
                    modes=tuple(conditions.get("modes", ())),
                    days=tuple(int(d) for d in conditions.get("days", ()) or ()),
                    id=int(row["id"]),
                    source_text=row["source_text"],
                )
            )

    async def add(
        self, db: Database, rule: HouseRule, created_by: str | None, now: datetime
    ) -> int:
        await db.execute(
            "INSERT INTO house_rules(created, created_by, scope_type, scope_id, effect, action, "
            "value, time_from, time_to, conditions, source_text) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                now.isoformat(),
                created_by,
                rule.scope_type,
                rule.scope_id,
                rule.effect,
                rule.category,
                rule.value,
                rule.time_from.isoformat("minutes") if rule.time_from else None,
                rule.time_to.isoformat("minutes") if rule.time_to else None,
                json.dumps(
                    {"skill": rule.skill, "modes": list(rule.modes)}
                    | ({"days": list(rule.days)} if rule.days else {})
                ),
                rule.source_text,
            ),
        )
        row = await db.fetchone("SELECT max(id) AS id FROM house_rules")
        rule_id = int(row["id"]) if row else 0
        await self.load(db)
        return rule_id

    async def remove(self, db: Database, rule_id: int) -> None:
        await db.execute("UPDATE house_rules SET enabled = 0 WHERE id = ?", (rule_id,))
        await self.load(db)


# -- text without AI: fixed sentence patterns ----------------------------------------------

_TIME = r"(\d{1,2})[:.]?(\d{2})?"


def _words(languages: Languages, lang: str, key: str) -> list[str]:
    raw = languages.text(lang, key)
    return [w.strip().lower() for w in raw.split(",") if w.strip() and raw != key]


def parse_rule(text: str, lang: str, languages: Languages, model: HouseModel) -> HouseRule | None:
    """Understand a house rule from fixed patterns (E-87), e.g.

    nl: "nooit de gordijnen in de woonkamer open voor 7:00", "nooit warmer dan 21 graden"
    en: "never open the curtains in the living room before 7:00", "never warmer than 21 degrees"
    """
    t = " ".join(text.lower().strip().rstrip(".!").split())
    never = _words(languages, lang, "rules.words.never")
    if not any(t.startswith(w + " ") for w in never):
        return None
    # Temperature limit
    warmer = _words(languages, lang, "rules.words.warmer_than")
    for w in warmer:
        m = re.search(re.escape(w) + r"\s+(\d{1,2}(?:[.,]\d)?)", t)
        if m:
            value = float(m.group(1).replace(",", "."))
            area = _find_area(t, model)
            return HouseRule(
                "area" if area else "house", area or "*", "limit", "heat", value, source_text=text
            )
    category = None
    for cat in ("on", "off", "open", "close"):
        if any(
            re.search(rf"\b{re.escape(w)}\b", t)
            for w in _words(languages, lang, f"rules.words.{cat}")
        ):
            category = cat
            break
    if category is None:
        return None
    time_from = time_to = None
    before = _words(languages, lang, "rules.words.before")
    after = _words(languages, lang, "rules.words.after")
    for w in before:
        m = re.search(rf"\b{re.escape(w)}\s+{_TIME}", t)
        if m:
            time_to = time(int(m.group(1)) % 24, int(m.group(2) or 0))
    for w in after:
        m = re.search(rf"\b{re.escape(w)}\s+{_TIME}", t)
        if m:
            time_from = time(int(m.group(1)) % 24, int(m.group(2) or 0))
    entity = _find_entity(t, model, category)
    area = _find_area(t, model)
    if entity:
        scope_type, scope_id = "entity", entity
    elif area:
        scope_type, scope_id = "area", area
    else:
        scope_type, scope_id = "house", "*"
    return HouseRule(
        scope_type, scope_id, "never", category, None, time_from, time_to, source_text=text
    )


def _find_area(text: str, model: HouseModel) -> str | None:
    for area in sorted(model.areas.values(), key=lambda a: -len(a.name)):
        if area.name.lower() in text:
            return area.id
    return None


def _find_entity(text: str, model: HouseModel, category: str) -> str | None:
    domains = {a.split(".")[0] for a in CATEGORIES[category]}
    for entity in sorted(model.entities.values(), key=lambda e: -len(e.name)):
        if entity.domain in domains and entity.name.lower() in text:
            return entity.entity_id
    return None


def rule_text(rule: HouseRule, lang: str, languages: Languages, model: HouseModel) -> str:
    """A house rule as the list of house rules shows it: a skill off or on in a room, or the
    rule read back."""
    if rule.skill:
        area = model.areas.get(rule.scope_id)
        return languages.text(
            lang,
            f"rules.skill_{rule.effect}",
            skill=languages.text(lang, f"skills.{rule.skill}"),
            area=area.name if area else rule.scope_id,
        )
    return read_back(rule, lang, languages, model)


def read_back(rule: HouseRule, lang: str, languages: Languages, model: HouseModel) -> str:
    """The rule in plain language, for the yes/no confirmation (E-87)."""
    if rule.scope_type == "entity":
        entity = model.entities.get(rule.scope_id)
        scope = entity.name if entity else rule.scope_id
    elif rule.scope_type == "area":
        scope = (
            languages.text(lang, "rules.in_area", area=model.areas[rule.scope_id].name)
            if rule.scope_id in model.areas
            else ""
        )
    else:
        scope = languages.text(lang, "rules.whole_house")
    when = ""
    if rule.time_to and not rule.time_from:
        when = languages.text(lang, "rules.before", time=rule.time_to.strftime("%H:%M"))
    elif rule.time_from and not rule.time_to:
        when = languages.text(lang, "rules.after", time=rule.time_from.strftime("%H:%M"))
    elif rule.time_from and rule.time_to:
        when = languages.text(
            lang,
            "rules.between",
            start=rule.time_from.strftime("%H:%M"),
            end=rule.time_to.strftime("%H:%M"),
        )
    days = _days(rule.days, lang, languages)
    when = f"{when} {days}".strip()
    if rule.effect == "limit":
        value = languages.number(lang, rule.value or 0)
        said = languages.text(lang, "rules.readback_limit", value=value, scope=scope, when=when)
        return " ".join(said.split())
    group = "rules.action_device" if rule.scope_type == "entity" else "rules.action"
    action = languages.text(lang, f"{group}.{rule.category}")
    key = "rules.readback_never" if rule.effect == "never" else "rules.readback_always"
    return " ".join(languages.text(lang, key, action=action, scope=scope, when=when).split())


def _days(days: tuple[int, ...], lang: str, languages: Languages) -> str:
    """The days a rule holds on, in words (E-420): "in het weekend", "op werkdagen", or the
    days by name; nothing when it holds every day."""
    found = tuple(sorted(set(days)))
    if not found or found == tuple(range(7)):
        return ""
    if found == WEEKEND:
        return languages.text(lang, "rules.days.weekend")
    if found == WEEKDAYS:
        return languages.text(lang, "rules.days.weekdays")
    names = ", ".join(languages.text(lang, f"ui.weekday.{d}") for d in found)
    return languages.text(lang, "rules.days.some", days=names)
