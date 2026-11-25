"""Existing Home Assistant automations: learn, keep off, take over, give back (E-03, E-41,
E-42, E-112, E-160).

HouseMind never makes, changes or deletes an automation (E-03). It reads them:

- **Learn** (E-41): what an automation does is start knowledge for the matching skill and
  room (E-160). "The hall light goes on at motion" teaches *light when entering* in the hall,
  with the brightness the automation uses. Which trigger and action fit which skill is
  knowledge (``from_automations`` in ``knowledge/skills``), not code.
- **Keep off** (E-42): while an automation is on, HouseMind only watches the devices it
  controls – the gate's "another system" (E-68) – and learns from what happens to them.
- **Take over** (E-42): once HouseMind can do everything an automation does – it is autonomous
  in that domain, or it predicts the automation well enough (the thresholds of E-56) – it asks
  an administrator, yes or no, whether it may switch the automation off. Never delete;
  switching it back on is always possible.
- **Give back** (E-112): before HouseMind is removed, every automation it switched off goes
  back on.
- **The hand of the resident** (E-448): an automation that only starts when a person presses a
  button or flips a switch, and only switches or dims lamps and switches, is no other system.
  Its lamps are not only watched, HouseMind never takes it over, and what it does counts as the
  person who pressed (``Reading.hand``; which triggers are a person's act is knowledge,
  ``knowledge/hand.yaml``).
- **A signal** (E-461): an automation that nothing of daily life starts and that only uses lamps
  to show something – the colour of the alarm, the blink of the doorbell – does not light a room.
  It holds nothing, teaches nothing and stays on. HouseMind keeps off the lamp while the signal
  lasts; one that leaves the lamp on, until that lamp changes (``Reading.signal``).
- **Along with its lamps** (E-461): an automation that teaches nothing but only switches lamps of
  rules goes off with those rules, in the same question (``Reading.lamps_only``).

Every switch is an action request through the safety gate (E-110). This module only reads,
decides what to ask, and remembers what HouseMind switched off.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from housemind.core.model import Entity, HouseModel, Source, daypart
from housemind.ha.adapter import HAAutomation, HAStep, HATrigger
from housemind.knowledge.catalog import Catalog
from housemind.knowledge.matcher import recognise
from housemind.memory.database import Database

TURN_OFF = "automation.turn_off"
TURN_ON = "automation.turn_on"
PRESENCE = frozenset({"motion", "occupancy", "presence"})
OPENINGS = frozenset({"door", "window", "opening", "garage_door"})
# Device triggers name what happened instead of the new state.
_ON_TYPES = frozenset({"motion", "occupied", "present", "opened", "turned_on"})
_OFF_TYPES = frozenset({"no_motion", "not_occupied", "not_present", "not_opened", "turned_off"})
_HEAT = frozenset({"set_temperature", "set_preset_mode", "set_hvac_mode", "turn_on", "turn_off"})
_VALUE_KEYS = {
    "brightness_pct": "brightness_pct",
    "color_temp_kelvin": "color_temp_kelvin",
    "kelvin": "color_temp_kelvin",
    "temperature": "temperature",
}
# Home Assistant's own trigger words (E-461): "occupancy.detected", "door.closed".
_SENSED = {
    "motion": "presence",
    "occupancy": "presence",
    "presence": "presence",
    "door": "opening",
    "window": "opening",
    "garage_door": "opening",
    "opening": "opening",
}
_SENSED_STATE = {"detected": "on", "opened": "on", "cleared": "off", "closed": "off"}
# A sign on a lamp (E-461): a colour or effect, not a brightness or warmth a room is lit with; or
# a lamp that goes on and within this time off again.
_SIGN_DATA = frozenset(
    {
        "rgb_color",
        "rgbw_color",
        "rgbww_color",
        "hs_color",
        "xy_color",
        "color_name",
        "effect",
        "flash",
    }
)
_BLINK_SECONDS = 60.0
SIGNAL_MARGIN = timedelta(seconds=10)  # on top of its longest wait: the signal is really over
SIGNAL_LEFT_ON = timedelta(hours=24)  # a signal that stays: at the latest then it is the lamp's
# What a button may do and still be a person's hand (E-448): lamps and switches on, off,
# toggled or dimmed.
_HAND_DOES = frozenset(
    {"turn_on", "turn_off", "toggle", "brightness_increase", "brightness_decrease"}
)


class Status(StrEnum):
    SEEN = "seen"  # known; on, or switched off by someone else
    OFFERED = "offered"  # HouseMind asked whether it may switch it off
    DECLINED = "declined"  # someone said no or switched it back on: HouseMind asks no more
    TURNED_OFF = "turned_off"  # off after a yes; goes back on when HouseMind gives back


@dataclass(frozen=True, slots=True)
class Reading:
    """What HouseMind understood from one automation."""

    automation: HAAutomation
    controls: frozenset[str] = frozenset()  # the devices it acts on
    domains: frozenset[str] = frozenset()  # lighting, covers, climate …
    learned: tuple[tuple[str, str], ...] = ()  # skill × area it teaches (E-41)
    values: dict[tuple[str, str], dict[str, float]] = field(default_factory=dict)
    whole: bool = False  # HouseMind does everything it does, so it could take it over
    # What would stop with it, because HouseMind does not take it over (E-317): device and
    # scene or script names, and how many other actions (a message …) it has without one.
    stops: tuple[str, ...] = ()
    stops_other: int = 0
    # The hand of the resident (E-448): whose act starts it – a person in the app or a switch,
    # button or remote – and the switches and buttons that start it; None for any other.
    hand: Source | None = None
    switches: frozenset[str] = frozenset()
    # A signal, and one that only switches lamps without teaching a rule (E-461); ``does``: what
    # it does with devices, in the words of ``from_automations``.
    signal: bool = False
    lamps_only: bool = False
    does: frozenset[str] = frozenset()

    @property
    def entity_id(self) -> str:
        return self.automation.entity_id


@dataclass(slots=True)
class HandWords:
    """Which triggers are a person's own act (E-448, ``knowledge/hand.yaml``)."""

    app: set[str] = field(default_factory=set)  # events of an action in the phone app
    buttons: set[str] = field(default_factory=set)  # events of a button or remote
    words: set[str] = field(default_factory=set)  # a press, in a device trigger's type
    not_words: set[str] = field(default_factory=set)  # … unless it is a sensor's
    domains: set[str] = field(default_factory=set)  # a switch or button in Home Assistant


def load_hand_words(directories: Iterable[Path]) -> HandWords:
    """The words of every knowledge folder together (a house may add its own)."""
    words = HandWords()
    for directory in directories:
        path = directory / "hand.yaml"
        if not path.is_file():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        events = raw.get("events") or {}
        words.app.update(str(e) for e in events.get("app") or ())
        words.buttons.update(str(e) for e in events.get("button") or ())
        words.words.update(str(w).lower() for w in raw.get("device_words") or ())
        words.not_words.update(str(w).lower() for w in raw.get("not_words") or ())
        words.domains.update(str(d) for d in raw.get("domains") or ())
    return words


# -- reading ------------------------------------------------------------------------------


def _hour(at: str) -> int | None:
    head = at.split(":", 1)[0]
    return int(head) if head.isdigit() and int(head) < 24 else None


def _after_noon(at: str) -> float | None:
    """An evening or night time as minutes after noon ("23:59" → 719, "00:30" → 750); a
    morning or afternoon time is no time to put the evening light out (E-446)."""
    hour = _hour(at)
    parts = at.split(":")
    if hour is None or len(parts) < 2 or not parts[1][:2].isdigit():
        return None
    minute = int(parts[1][:2])
    if hour >= 17:
        return (hour - 12) * 60 + minute
    if hour < 4:
        return (hour + 12) * 60 + minute
    return None


def _entity(model: HouseModel, registry: dict[str, Entity], ref: str) -> Entity | None:
    """An entity by its id, or by its registry id (device triggers and actions use that)."""
    return model.entities.get(ref) or registry.get(ref)


def trigger_kinds(
    trigger: HATrigger, model: HouseModel, registry: dict[str, Entity] | None = None
) -> set[str]:
    """What starts an automation, in the words of ``from_automations``."""
    if registry is None:
        registry = {e.registry_id: e for e in model.entities.values()}
    to = trigger.to or ""
    kinds: set[str] = set()
    if trigger.platform == "sun":
        kinds |= {"sunset": {"sun_down"}, "sunrise": {"sun_up"}}.get(to, set())
    elif trigger.platform == "zone":
        kinds |= {"enter": {"people_arrive"}, "leave": {"people_leave"}}.get(to, set())
    elif trigger.platform == "time" and trigger.at:
        hour = _hour(trigger.at)
        if hour is not None:
            kinds.add(f"time_{daypart(datetime(2000, 1, 1, hour))}")
    elif trigger.platform == "numeric_state":
        if "sun.sun" in trigger.entity_ids and trigger.attribute == "elevation":
            kinds.add("sun_down" if trigger.below is not None else "sun_up")
        elif "zone.home" in trigger.entity_ids:  # the number of people at home
            kinds.add("people_leave" if trigger.below is not None else "people_arrive")
    elif trigger.platform in ("state", "device"):
        for ref in trigger.entity_ids:
            entity = _entity(model, registry, ref)
            entity_id = entity.entity_id if entity else ref
            domain = entity_id.split(".", 1)[0]
            on = to in _ON_TYPES if trigger.platform == "device" else to == "on"
            off = to in _OFF_TYPES if trigger.platform == "device" else to == "off"
            if domain in ("person", "device_tracker"):
                if to == "home":
                    kinds.add("people_arrive")
                elif to in ("not_home", "away"):
                    kinds.add("people_leave")
            elif entity_id == "sun.sun":
                kinds |= {"below_horizon": {"sun_down"}, "above_horizon": {"sun_up"}}.get(to, set())
            elif domain == "binary_sensor" and entity is not None and (on or off):
                state = "on" if on else "off"
                if entity.device_class in PRESENCE:
                    kinds.add(f"presence_{state}")
                elif entity.device_class in OPENINGS:
                    kinds.add(f"opening_{state}")
    else:
        family, _, what = trigger.platform.partition(".")
        if family in _SENSED and what in _SENSED_STATE:
            kinds.add(f"{_SENSED[family]}_{_SENSED_STATE[what]}")
    return kinds


def verb(step: HAStep) -> str | None:
    """What a device action does, in the words of ``from_automations``."""
    domain, _, service = step.action.partition(".")
    if domain == "climate":
        return "heat" if service in _HEAT else None
    if domain == "cover":
        if service in ("open_cover", "close_cover"):
            return service.removesuffix("_cover")
        position = step.data.get("position")
        if service == "set_cover_position" and isinstance(position, int | float):
            return "open" if position > 0 else "close"
        return None
    return service if service in ("turn_on", "turn_off") else None


def _targets(step: HAStep, model: HouseModel, registry: dict[str, Entity]) -> tuple[set[str], bool]:
    """The entities a step acts on, and whether HouseMind knows them exactly."""
    domain = step.action.split(".", 1)[0]
    found: set[str] = set()
    exact = bool(step.entity_ids or step.area_ids or step.device_ids)
    for ref in step.entity_ids:
        if ref == "all":
            found |= {e for e in model.entities if e.split(".", 1)[0] == domain}
            continue
        entity = _entity(model, registry, ref)
        if entity is None:
            exact = False
        else:
            found.add(entity.entity_id)
    for entity in model.entities.values():
        if entity.domain != domain:
            continue
        area = model.area_of(entity.entity_id)
        if (area is not None and area.id in step.area_ids) or (
            entity.device_id is not None and entity.device_id in step.device_ids
        ):
            found.add(entity.entity_id)
    for group in [e for e in found if e in model.groups]:
        found |= {m for m in model.groups[group] if m in model.entities}  # and its lamps
    return found, exact


def trigger_areas(
    triggers: tuple[HATrigger, ...], model: HouseModel, registry: dict[str, Entity]
) -> set[str]:
    """The rooms whose presence or doors start an automation."""
    areas: set[str] = set()
    for trigger in triggers:
        sensed = trigger.platform.partition(".")[0] in _SENSED and "." in trigger.platform
        if trigger.platform not in ("state", "device") and not sensed:
            continue
        for ref in trigger.entity_ids:
            entity = _entity(model, registry, ref)
            if entity is None or entity.domain != "binary_sensor":
                continue
            if sensed or entity.device_class in PRESENCE or entity.device_class in OPENINGS:
                area = model.area_of(entity.entity_id)
                if area is not None:
                    areas.add(area.id)
    return areas


def _hand_trigger(
    trigger: HATrigger, model: HouseModel, registry: dict[str, Entity], words: HandWords
) -> Source | None:
    """Whose act this trigger is (E-448): a person in the app, a switch, button or remote, or
    None – a time, the sun, motion, a door, a number, another event."""
    if trigger.platform == "event":
        if trigger.to in words.app:
            return Source.PERSON
        return Source.PHYSICAL if trigger.to in words.buttons else None
    refs = [_entity(model, registry, ref) for ref in trigger.entity_ids]
    if trigger.platform in ("state", "device") and refs:
        domains = {e.domain for e in refs if e is not None}
        known = all(e is not None for e in refs)
        return Source.PHYSICAL if known and domains <= words.domains else None
    if trigger.platform == "device":  # a remote or button: no entity, what was pressed
        said = set(re.split(r"[^a-z0-9]+", f"{trigger.to or ''} {trigger.subtype or ''}".lower()))
        return Source.PHYSICAL if said & words.words and not said & words.not_words else None
    domain = trigger.platform.split(".", 1)[0]  # a trigger like "switch.turned_on"
    if "." in trigger.platform and domain in words.domains and refs:
        return Source.PHYSICAL
    return None


def _hand(
    automation: HAAutomation, model: HouseModel, registry: dict[str, Entity], words: HandWords
) -> tuple[Source | None, frozenset[str]]:
    """The hand of the resident (E-448): every trigger is a person's act, and it only switches
    or dims lamps and switches – and may put its own button back. Whose hand, and its own
    switches and buttons."""
    if not automation.readable or automation.other or automation.starts or not automation.steps:
        return None, frozenset()
    sources = [_hand_trigger(t, model, registry, words) for t in automation.triggers]
    if not sources or None in sources:
        return None, frozenset()
    own = {
        entity.entity_id
        for trigger in automation.triggers
        for ref in trigger.entity_ids
        if (entity := _entity(model, registry, ref)) is not None
    }
    for step in automation.steps:
        domain, _, service = step.action.partition(".")
        if domain in ("light", "switch") and service in _HAND_DOES:
            continue
        targets, exact = _targets(step, model, registry)
        if domain == "input_boolean" and service == "turn_off" and exact and targets <= own:
            continue  # it puts its own button back
        return None, frozenset()
    person = all(s is Source.PERSON for s in sources)
    return (Source.PERSON if person else Source.PHYSICAL), frozenset(own)


def house_domain(
    entity: Entity, catalog: Catalog, roles: dict[str, str], params: dict[str, dict[str, Any]]
) -> str | None:
    """The HouseMind domain (lighting, covers, climate …) a device belongs to, if any."""
    kind = recognise(catalog, entity, roles.get(entity.entity_id)).kind
    if kind is None or not kind.actions:
        return None
    for skill in kind.skills:
        domain = params.get(skill, {}).get("domain")
        if isinstance(domain, str):
            return domain
    return None


def _fits(params: dict[str, Any], does: str, kinds: set[str], role: str) -> bool:
    hint = params.get("from_automations")
    if not isinstance(hint, dict) or does not in hint.get("does", ()):
        return False
    if not kinds & set(hint.get("when", ())):
        return False
    within = params.get("in") or params.get("only_in")
    return (not within or role in within) and role not in params.get("never_in", ())


def _values(step: HAStep, keys: Iterable[str]) -> dict[str, float]:
    wanted = set(keys)
    out: dict[str, float] = {}
    for raw_key, value in step.data.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        key, number = _VALUE_KEYS.get(raw_key), float(value)
        if raw_key == "brightness":
            key, number = "brightness_pct", number * 100 / 255
        if key in wanted:
            out[key] = round(number, 1)
    return out


def _is_lamp(entity_id: str, roles: dict[str, str]) -> bool:
    return entity_id.split(".", 1)[0] == "light" or roles.get(entity_id) == "light"


def _gives_a_sign(automation: HAAutomation) -> bool:
    """It shows something with a lamp (E-461): a colour or effect, or on and soon off again."""
    lit = [step for step in automation.steps if step.action == "light.turn_on"]
    if any(_SIGN_DATA & set(step.data) for step in lit):
        return True
    return bool(lit) and automation.ends and automation.wait_s <= _BLINK_SECONDS


def read_automation(
    automation: HAAutomation,
    model: HouseModel,
    catalog: Catalog,
    roles: dict[str, str],
    params: dict[str, dict[str, Any]],
    area_roles: dict[str, str],
    words: HandWords | None = None,
) -> Reading:
    """Understand one automation: which devices, which skills × rooms, whether HouseMind
    does everything it does – and whether it is the hand of the resident (E-448)."""
    registry = {e.registry_id: e for e in model.entities.values()}
    hand, switches = _hand(automation, model, registry, words) if words else (None, frozenset())
    kinds: set[str] = set()
    waits: list[float] = []  # how long "nobody" holds before it puts the lamps out (E-444)
    late: list[float] = []  # when it puts the evening light out, after noon (E-446)
    for trigger in automation.triggers:
        found_kinds = trigger_kinds(trigger, model, registry)
        kinds |= found_kinds
        if "presence_off" in found_kinds:
            waits.append(trigger.for_s or 0.0)
        if trigger.platform == "time" and trigger.at and (after := _after_noon(trigger.at)):
            late.append(after)
    rooms = trigger_areas(automation.triggers, model, registry)
    controls: set[str] = set()
    domains: set[str] = set()
    learned: list[tuple[str, str]] = []
    values: dict[tuple[str, str], dict[str, float]] = {}
    whole = automation.readable and automation.other == 0 and bool(automation.steps)
    stops: list[str] = []
    unnamed = 0
    plain = automation.readable and not automation.other and not automation.starts
    verbs: set[str] = set()
    theirs: set[str] = set()  # the parts of HouseMind its devices belong to
    for step in automation.steps:
        targets, exact = _targets(step, model, registry)
        controls |= targets
        does = verb(step)
        if does is not None:
            verbs.add(does)
        if not exact or not targets or does is None:
            whole = False
            plain = False
            if not targets:
                unnamed += 1
        for entity_id in sorted(targets):
            domain = house_domain(model.entities[entity_id], catalog, roles, params)
            area = model.area_of(entity_id)
            if domain is not None:
                theirs.add(domain)
            skills = []
            if domain is not None and area is not None and does is not None:
                role = area_roles.get(area.id, "other")
                skills = [
                    s
                    for s, p in params.items()
                    if p.get("domain") == domain and _fits(p, does, kinds, role)
                ]
            if rooms and area is not None and area.id not in rooms:
                # Presence in one room switching a lamp in another: HouseMind's skills work
                # per room, so this teaches nothing about either room.
                skills = []
            if domain is None or area is None or not skills:
                whole = False  # HouseMind would not do this part
                name = model.entities[entity_id].name
                if name not in stops:
                    stops.append(name)
                continue
            domains.add(domain)
            for skill in skills:
                pair = (skill, area.id)
                if pair not in learned:
                    learned.append(pair)
                found = _values(step, params[skill]["from_automations"].get("values", ()))
                if skill == "light_off_leave" and waits and does == "turn_off":
                    found["hold_minutes"] = round(max(waits) / 60, 2)
                if skill == "light_outside_evening" and late and does == "turn_off":
                    found["off_minutes"] = max(late)
                if found:
                    values.setdefault(pair, {}).update(found)
    for started in automation.starts:  # the scenes and scripts it starts (E-258)
        entity = model.entities.get(started)
        name = entity.name if entity is not None else started
        if name not in stops:
            stops.append(name)
    unnamed += max(0, automation.other - len(automation.starts))
    lamps = {e for e in controls if _is_lamp(e, roles)}
    own = whole and bool(learned) and hand is None
    # Nothing of daily life starts it, and of what HouseMind steers it only touches lamps.
    signal = (
        hand is None
        and not learned
        and not kinds
        and automation.readable
        and bool(automation.triggers)
        and bool(lamps)
        and theirs <= {"lighting"}
        and _gives_a_sign(automation)
    )
    lamps_only = (
        plain
        and hand is None
        and not signal
        and not learned
        and bool(automation.steps)
        and controls == lamps
        and bool(lamps)
    )
    return Reading(
        automation,
        frozenset(controls),
        frozenset(domains | theirs if lamps_only else domains),
        tuple(learned),
        values,
        own,
        tuple(stops),
        unnamed,
        hand,
        switches,
        signal,
        lamps_only,
        frozenset(verbs),
    )


def can_take_over(
    reading: Reading,
    autonomous: Callable[[str], bool],
    autonomous_here: Callable[[str, str], bool],
    skill_domain: Callable[[str], str | None],
    whole: bool = True,
) -> bool:
    """HouseMind can do everything this automation does (E-42, 20.12): every skill in every
    room it teaches is on Autonomous, within the limit. A part on Autonomous somewhere else is
    not enough; only a part the automation teaches nothing in counts as a whole. With
    ``whole=False`` an administrator already heard what else stops with it (E-317). The hand
    of the resident is never taken over (E-448)."""
    if reading.hand is not None:
        return False
    if whole and not reading.whole:
        return False
    if not reading.learned:
        return False
    for domain in reading.domains:
        pairs = [p for p in reading.learned if skill_domain(p[0]) == domain]
        if not (all(autonomous_here(*p) for p in pairs) if pairs else autonomous(domain)):
            return False
    return True


# -- memory -------------------------------------------------------------------------------


@dataclass(slots=True)
class Automations:
    """The automations HouseMind knows, and which of them it switched off (E-42, E-112)."""

    db: Database
    readings: dict[str, Reading] = field(default_factory=dict)
    status: dict[str, Status] = field(default_factory=dict)
    seeded: set[str] = field(default_factory=set)  # start knowledge given to learning

    async def load(self) -> None:
        self.status.clear()
        self.seeded.clear()
        for row in await self.db.fetchall("SELECT * FROM automations"):
            self.status[str(row["entity_id"])] = Status(row["status"])
            if row["seeded"]:
                self.seeded.add(str(row["entity_id"]))

    async def update(self, readings: Iterable[Reading], now: datetime) -> None:
        """A fresh reading of all automations. A renamed automation keeps what HouseMind
        knows about it (E-116); one that no longer exists is forgotten."""
        fresh = {r.entity_id: r for r in readings}
        rows = await self.db.fetchall("SELECT * FROM automations")
        known = {str(r["entity_id"]) for r in rows}
        by_config = {str(r["config_id"]): str(r["entity_id"]) for r in rows if r["config_id"]}
        renamed: set[str] = set()
        for entity_id, reading in fresh.items():
            config_id = reading.automation.config_id
            old = by_config.get(config_id) if config_id else None
            if entity_id not in known and old is not None and old not in fresh:
                await self.db.execute(
                    "UPDATE automations SET entity_id = ? WHERE entity_id = ?", (entity_id, old)
                )
                renamed.add(old)
            elif entity_id not in known:
                await self.db.execute(
                    "INSERT INTO automations(entity_id, config_id, name, status, first_seen) "
                    "VALUES(?,?,?,?,?)",
                    (
                        entity_id,
                        config_id,
                        reading.automation.name,
                        Status.SEEN.value,
                        now.isoformat(),
                    ),
                )
            await self.db.execute(
                "UPDATE automations SET name = ?, config_id = ? WHERE entity_id = ?",
                (reading.automation.name, config_id, entity_id),
            )
        for entity_id in known - set(fresh) - renamed:
            await self.db.execute("DELETE FROM automations WHERE entity_id = ?", (entity_id,))
        self.readings = fresh
        await self.load()

    async def set_status(
        self, entity_id: str, status: Status, now: datetime, user_id: str | None
    ) -> None:
        await self.db.execute(
            "UPDATE automations SET status = ?, changed_at = ?, changed_by = ? WHERE entity_id = ?",
            (status.value, now.isoformat(), user_id, entity_id),
        )
        self.status[entity_id] = status

    async def mark_seeded(self, entity_id: str) -> None:
        await self.db.execute("UPDATE automations SET seeded = 1 WHERE entity_id = ?", (entity_id,))
        self.seeded.add(entity_id)

    def switched(self, entity_id: str, on: bool) -> None:
        reading = self.readings.get(entity_id)
        if reading is not None:
            self.readings[entity_id] = replace(
                reading, automation=replace(reading.automation, enabled=on)
            )

    def controlled(self) -> set[str]:
        """Devices an automation that is on controls: HouseMind only watches them (E-42). The
        hand of the resident – a button or switch – is no other system (E-448), nor is a signal
        (E-461)."""
        out: set[str] = set()
        for reading in self.readings.values():
            if reading.automation.enabled and reading.hand is None and not reading.signal:
                out |= reading.controls
        return out

    def signals(self) -> set[str]:
        """The automations that give a sign with a lamp (E-461)."""
        return {e for e, r in self.readings.items() if r.signal}

    def rest_after(self, automation_id: str | None) -> timedelta:
        """How long HouseMind keeps off a lamp this signal just changed (E-461): as long as the
        signal lasts; one that does not end by itself keeps the lamp until that lamp changes."""
        reading = self.readings.get(automation_id or "")
        if reading is None:
            return SIGNAL_MARGIN
        if not reading.automation.ends:
            return SIGNAL_LEFT_ON
        return timedelta(seconds=reading.automation.wait_s) + SIGNAL_MARGIN

    def controller_of(self, entity_id: str) -> str | None:
        for reading in sorted(self.readings.values(), key=lambda r: r.automation.name):
            if (
                reading.automation.enabled
                and reading.hand is None
                and not reading.signal
                and entity_id in reading.controls
            ):
                return reading.automation.name
        return None

    def hands(self) -> dict[str, Source]:
        """The automations that are the hand of the resident, and whose (E-448)."""
        return {e: r.hand for e, r in self.readings.items() if r.hand is not None}

    def hand_switches(self) -> set[str]:
        """The switches and buttons of a hand that is on: the residents' own (E-448)."""
        out: set[str] = set()
        for reading in self.readings.values():
            if reading.automation.enabled and reading.hand is not None:
                out |= reading.switches
        return out

    def turned_off(self) -> list[str]:
        return sorted(e for e, s in self.status.items() if s is Status.TURNED_OFF)
