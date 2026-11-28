"""Core skills (H3.2) with start knowledge (E-160).

A skill looks at the world view and asks the safety gate for an action only when the wanted
state differs from the current one. Whether that becomes an action, a proposal or only an
observation is decided by the gate and the skill's learned step (H3.4). Parameters come from
``knowledge/skills/*.yaml``; learned preferences replace them (H3.3).
"""

from __future__ import annotations

import copy
import random
import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field, fields
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol

import yaml

from housemind.brain.daylight import DaylightBook
from housemind.brain.learning import HOLD_MAX, HOLD_MIN
from housemind.brain.levels import FELT, STANDS, LightLevels, colour_now, nearest, stand_of
from housemind.brain.room_temps import RoomTemps, Temps, half
from housemind.core.model import Entity, HouseModel, State, daypart
from housemind.knowledge.catalog import Catalog, DeviceKind
from housemind.knowledge.matcher import recognise
from housemind.knowledge.roles import EntityRole, entity_role
from housemind.safety.gate import ActionRequest, Layer
from housemind.world.house_mode import HouseMode
from housemind.world.world import WorldState


class Preferences(Protocol):
    """What learning knows (H3.3); ``None`` means: use the start knowledge."""

    def value(self, skill: str, area_id: str, context: str, key: str) -> float | None: ...

    def light_value(
        self, skill: str, area_id: str, context: str, part: str, key: str
    ) -> float | None: ...

    def day_shift(self, area_id: str, part: str, key: str) -> float | None: ...

    def usual_lights(self, area_id: str) -> set[str] | None: ...

    def dark(self, area_id: str, levels: dict[str, float], now: datetime) -> bool | None: ...

    def arrived(
        self,
        skill: str,
        area_id: str,
        lamps: list[str],
        now: datetime,
        levels: dict[str, float],
        dark: bool,
        lit: bool,
        context: str,
        group: str,
        since: datetime,
    ) -> None: ...

    def done_before(
        self,
        skill: str,
        area_id: str,
        entity_ids: list[str],
        state: str,
        now: datetime,
        context: str,
        within: float | None = None,
    ) -> bool: ...

    def passing(self, area_id: str, now: datetime) -> bool: ...

    def open_entry(
        self, area_id: str, lamps: list[str], others: list[str], now: datetime
    ) -> None: ...

    def shares_light(self, area_id: str, other: str, now: datetime) -> bool: ...

    def visited(self, area_id: str, passed: bool, now: datetime) -> None: ...

    def held_too_short(self, area_id: str, minutes: float, now: datetime) -> None: ...

    def known_here(self, skill: str, area_id: str) -> bool: ...

    def arrival_minutes(
        self, area_id: str, minutes: float, now: datetime, weight: float
    ) -> None: ...


class NoPreferences:
    def value(self, skill: str, area_id: str, context: str, key: str) -> float | None:
        return None

    def light_value(
        self, skill: str, area_id: str, context: str, part: str, key: str
    ) -> float | None:
        return None

    def day_shift(self, area_id: str, part: str, key: str) -> float | None:
        return None

    def usual_lights(self, area_id: str) -> set[str] | None:
        return None

    def dark(self, area_id: str, levels: dict[str, float], now: datetime) -> bool | None:
        return None

    def arrived(self, *args: Any) -> None:
        return None

    def done_before(self, *args: Any, **_kwargs: Any) -> bool:
        return False

    def passing(self, area_id: str, now: datetime) -> bool:
        return False

    def open_entry(self, *args: Any) -> None:
        return None

    def shares_light(self, area_id: str, other: str, now: datetime) -> bool:
        return False

    def visited(self, area_id: str, passed: bool, now: datetime) -> None:
        return None

    def held_too_short(self, area_id: str, minutes: float, now: datetime) -> None:
        return None

    def known_here(self, skill: str, area_id: str) -> bool:
        return False

    def arrival_minutes(self, area_id: str, minutes: float, now: datetime, weight: float) -> None:
        return None


def load_skill_params(directories: Iterable[Path]) -> dict[str, dict[str, Any]]:
    params: dict[str, dict[str, Any]] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for name, values in (raw.get("skills") or {}).items():
                params.setdefault(name, {}).update(values)
    return params


EVENING = "light_outside_evening"
ARRIVAL = "light_outside_arrival"


def _by_role(table: dict[str, Any], role: str, default: Any = None) -> Any:
    return table.get(role, table.get("other", default))


def one_setpoint(state: State) -> bool:
    """Heats and cools to one temperature, not between a lower and an upper limit (E-318)."""
    attrs = state.attributes
    return isinstance(attrs.get("temperature"), int | float) and not isinstance(
        attrs.get("target_temp_low"), int | float
    )


# Reaching the wish in the room itself (E-315, B-183).
REACH_GAP = 0.5  # this much too cold (or warm) …
REACH_THERE = 0.3  # … while the thermostat measures its own setting within this
REACH_STEP = 0.5
REACH_MAX = 2.0
REACH_EASE = 1.0  # a whole degree past the wish: half a degree back
REACH_PACE = timedelta(hours=1)


def _plausible(device_class: str, value: float) -> bool:
    if device_class == "humidity":
        return 1.0 <= value <= 99.0
    return 350.0 <= value <= 5000.0  # CO₂ in ppm


# Light on is about the moment of coming in or getting dark (B-116): HouseMind acts at once,
# and no longer than this after it.
ENTRY_WINDOW = timedelta(minutes=2)
# In a room where people mostly walk through, coming in counts after this long (E-270); it was
# 30 seconds, and then the next tick: up to a minute in the dark (E-443).
PASS_WAIT = timedelta(seconds=10)
# … counting only the time someone is there in one go: away longer than a sensor's blink starts
# it again, so there and back a minute apart is two short passes, not one stay (E-443).
PASS_GAP = timedelta(seconds=15)
# Where only motion senses people, someone sitting still is not seen: never off sooner (E-444).
HOLD_MOTION_ONLY = 3.0


def light_off_hold(
    params: dict[str, Any], prefs: Preferences, area_id: str, role: str, part: bool, watched: bool
) -> tuple[float, bool]:
    """How many minutes a place is empty before its lamps go off (E-444), and whether the
    house taught it: what people and automations showed in this room, else the start
    knowledge for its kind of room. A part of a room keeps its own start value."""
    holds = params.get("hold_minutes", {})
    start = float(holds.get("part" if part else role, _by_role(holds, role, 10)))
    learned = None if part else prefs.value("light_off_leave", area_id, "*", "hold_minutes")
    minutes = min(HOLD_MAX, max(HOLD_MIN, learned if learned is not None else start))
    if not watched:
        minutes = max(minutes, HOLD_MOTION_ONLY)
    return minutes, learned is not None


def arrival_minutes(params: dict[str, Any], prefs: Preferences, area_id: str) -> tuple[float, bool]:
    """How many minutes the lamps at the entrance stay on after coming home (E-447), and
    whether the house taught it: learned at this place, else the start knowledge."""
    learned = prefs.value(ARRIVAL, area_id, "*", "on_minutes")
    start = float(params.get("on_minutes", 5))
    return min(HOLD_MAX, max(HOLD_MIN, learned if learned is not None else start)), (
        learned is not None
    )


def _words(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.lower()))


# The parts that have a kind of device of their own in Home Assistant.
PART_DEVICES = {"lighting": "light", "covers": "cover", "climate": "climate"}
# What a rule that follows the devices of a room works on (E-485, design 72.2): the same
# lookups the skills themselves use – lamps, curtains and blinds, thermostats, fans.
ROOM_DEVICES: dict[str, tuple[str, str]] = {
    "lighting": ("light", ""),
    "covers": ("cover", "cover.window_covering"),
    "climate": ("climate", ""),
    "air": ("fan", ""),
}
# What a rule may need the room to measure as well (``needs`` in the skills knowledge).
MEASURES: dict[str, tuple[str, frozenset[str]]] = {
    "humidity": ("sensor", frozenset({"humidity"})),
    "carbon_dioxide": ("sensor", frozenset({"carbon_dioxide", "co2"})),
    "opening": ("binary_sensor", frozenset({"window", "door", "opening"})),
}


@dataclass(slots=True)
class SkillEngine:
    catalog: Catalog
    model: HouseModel
    area_roles: dict[str, str]
    params: dict[str, dict[str, Any]]
    roles: dict[str, str] = field(default_factory=dict)  # entity role assignments (switch=light)
    prefs: Preferences = field(default_factory=NoPreferences)
    daylight: DaylightBook | None = None  # the day's rhythm of the light (E-466)
    stands: LightLevels | None = None  # the standen of the lamps (E-490, design 73)
    _dimmed: dict[str, tuple[datetime, dict[str, int]]] = field(default_factory=dict)
    # A lamp with a wait of its own (E-575): its place, when its dim warning began, and its
    # brightness before it (None: nothing to dim) – apart from the room's warning above.
    _lamp_dimmed: dict[str, tuple[str, datetime, int | None]] = field(default_factory=dict)
    # The colour a lamp had before a dim warning in its Gedimd's colour, to give back (E-577).
    _dim_colour: dict[str, dict[str, Any]] = field(default_factory=dict)
    # When someone came into a dark place, or it got dark while they were there: light on is
    # about that moment, once – a lamp left off while someone sits in the dark is no new "not
    # like that" every five minutes.
    _entered: dict[str, datetime] = field(default_factory=dict)
    # When a lamp of a place was last seen on: someone who just switched it off is no one
    # coming into the dark (E-257).
    _lit_at: dict[str, datetime] = field(default_factory=dict)
    # … and a lamp of its own, or of a room whose light does for it (E-530): then people chose
    # their light, and HouseMind leaves it as it is.
    _own_lit_at: dict[str, datetime] = field(default_factory=dict)
    # When HouseMind itself asked to turn the lamps of a place off: then the lamp that was on
    # just before is no one's choice, and coming back is coming into the dark (QA 2, H-10).
    _own_off: dict[str, datetime] = field(default_factory=dict)
    _off_keys: dict[str, str] = field(default_factory=dict)  # lamp → its place, until it is done
    # Since when a lamp of a place is on; and how dark it was, and how light, the last time
    # none was (E-268: someone quicker than the sensor switched it on in the dark).
    _lit_since: dict[str, datetime] = field(default_factory=dict)
    _unlit: dict[str, tuple[bool, dict[str, float]]] = field(default_factory=dict)
    # Someone there since, and since when the place is empty: away shorter than ENTRY_WINDOW is
    # still the same visit (E-268: a presence sensor that blinks is nobody coming in again).
    _visit: dict[str, datetime] = field(default_factory=dict)
    _gone: dict[str, datetime] = field(default_factory=dict)
    # A visit that may teach (E-270): in the dark with the lamps off – whether it counts for
    # "dark enough", how dark and light it was, whether a lamp went on, and whether it was
    # told yet (walked through or stayed).
    _tally: dict[str, dict[str, Any]] = field(default_factory=dict)
    # How long someone was really there in this visit: seconds before, and since when now –
    # there and back through a room is no stay, however close together (E-270).
    _present: dict[str, list[Any]] = field(default_factory=dict)
    # When a place wants to be looked at again (E-443): someone in a walk-through room whose
    # wait runs out then – not only at the next tick.
    _recheck: dict[str, datetime] = field(default_factory=dict)
    # Rooms that share a presence sensor – an open kitchen and dining room (E-268, B-118):
    # world/zones.py open_spaces. Each is a room of its own; whether the light of the one does
    # for the other is learned (E-530).
    open_space: dict[str, set[str]] = field(default_factory=dict)
    # Which lamps, covers … a room has, by kind: found once and kept while the house does not
    # change, as looking them up goes through every entity of the house (E-257, S1). Cleared
    # when the house changes (forget()).
    entity_cache: dict[tuple[str, str, str], list[Entity]] | None = field(default_factory=dict)
    # Each room's devices with their kind, for the card Klaar voor autonomie? (S4): kept until
    # the house changes, not found again for every screen update.
    kinds_by_room: dict[str, list[tuple[str, DeviceKind]]] | None = None
    # Which rules each room has the devices for (E-485): found again when the house changes.
    can_cache: dict[tuple[str, str], bool] = field(default_factory=dict)
    has_cache: dict[tuple[str, str], bool] = field(default_factory=dict)
    possible_cache: frozenset[tuple[str, str]] | None = None
    follow_cache: frozenset[str] | None = None
    _done_today: dict[str, str] = field(default_factory=dict)
    _window_open_since: dict[str, datetime] = field(default_factory=dict)
    # Contacts that are openings of the house (world/openings.py); None: every contact.
    openings: set[str] | None = None
    # Climate entities that heat or cool a room (world/climate.py); None: every one.
    room_climates: set[str] | None = None
    # Lights a person confirmed are a device's status light, not a lamp (B-359): the lighting
    # leaves them alone.
    not_lamps: set[str] = field(default_factory=set)
    # The switches and buttons of the hand of the resident (E-448): their own way to work a
    # lamp, never one of HouseMind's lamps.
    hand_switches: set[str] = field(default_factory=set)
    # A signal is using this lamp now (E-461): no light of the room, and left alone.
    in_signal: Callable[[str, datetime], bool] = field(default=lambda entity_id, now: False)
    # What an automation does here: on, or switched off by HouseMind (E-460).
    with_automation: set[tuple[str, str]] = field(default_factory=set)
    # Energy plan (E-76): a small comfort-band shift for a heat pump, e.g. +1 °C to preheat
    # before an expensive peak. The climate skill applies it, so the two never fight.
    heat_offset: Callable[[str], float] = field(default=lambda entity_id: 0.0)
    # The air temperature of a room from its own sensors, not a thermostat (E-209, E-315);
    # None: not measured apart.
    room_air: Callable[[str, dict[str, State]], float | None] = field(
        default=lambda area_id, states: None
    )
    # How far HouseMind set a thermostat beyond the wish so that the room itself gets there
    # (E-315, B-183): a thermostat near a radiator, or an airco near the window, measures
    # otherwise than the room. Kept per thermostat and heat or cool, with when it last changed.
    _reach: dict[str, tuple[float, datetime]] = field(default_factory=dict)
    # A heat pump that heats and cools with one setpoint (E-318, B-185): the wish a person set
    # (kept by the runtime, also over a restart), and the preset it had before HouseMind chose
    # "away" (None: HouseMind did not).
    hc_wish: Callable[[str, State], float | None] = field(default=lambda entity_id, state: None)
    hc_before: Callable[[str], str | None] = field(default=lambda entity_id: None)
    # Temperaturen per ruimte (E-583): what an administrator set for a room goes first.
    room_temps: dict[str, RoomTemps] = field(default_factory=dict)
    _air_on: set[str] = field(default_factory=set)
    # Sleeping and waking up (E-88): today's wake-up time (with "alarm" or "usual") and
    # tonight's usual bedtime, from the rhythm. Without a clear rhythm they give None.
    wake_at: Callable[[WorldState], tuple[datetime, str] | None] = field(default=lambda world: None)
    bed_at: Callable[[WorldState], datetime | None] = field(default=lambda world: None)
    _wake_set: dict[str, str] = field(default_factory=dict)  # light → day HouseMind woke it
    _evening_done: dict[str, str] = field(default_factory=dict)  # "on:area" → evening:area
    # Welterusten (E-517): per skill and room, the going to sleep it was done for.
    _goodnight_done: dict[str, str] = field(default_factory=dict)
    # Outside in the evening (E-446): lamps a camera has on for a few minutes because it saw
    # someone (brain/security.py) – they go out by themselves, so they are not the evening
    # light yet. And when tonight's lamps go out, per outside place, fixed for the evening.
    camera_lit: Callable[[str], bool] = field(default=lambda entity_id: False)
    _evening_off: dict[str, tuple[date, bool, datetime, str]] = field(default_factory=dict)
    # Coming home in the dark (E-447): the words that make an outside place the entrance (from
    # every language file), when someone last came home, the coming home each place handled,
    # and the lamps HouseMind put on for it (lamp → place, since) – only those it puts out.
    entrance_words: tuple[str, ...] = ()
    _entrance: dict[str, bool] = field(default_factory=dict)
    _home_at: datetime | None = None
    _arrival_done: dict[str, datetime] = field(default_factory=dict)
    _arrival_lit: dict[str, tuple[str, datetime]] = field(default_factory=dict)
    _arrival_learned: dict[str, datetime] = field(default_factory=dict)

    # -- helpers ------------------------------------------------------------------------

    def forget(self) -> None:
        """The house changed (devices, rooms, groups or roles): find the devices again."""
        self.model.forget_rooms()
        if self.entity_cache is not None:
            self.entity_cache.clear()
        self.kinds_by_room = None
        self.can_cache.clear()
        self.has_cache.clear()
        self.possible_cache = None
        self.follow_cache = None
        self._entrance.clear()

    def usual_lights(self, area_id: str) -> set[str] | None:
        """The lamps people use here, as HouseMind addresses them (E-458): a group that steers
        counts when people used it or any lamp in it."""
        used = self.prefs.usual_lights(area_id)
        if used is None:
            return None
        return used | set(self.model.units(used))

    def _kind(self, entity: Entity) -> DeviceKind | None:
        return recognise(self.catalog, entity, self.roles.get(entity.entity_id)).kind

    def _entities(self, area_id: str, domain: str, kind_prefix: str = "") -> list[Entity]:
        cached = (
            self.entity_cache.get((area_id, domain, kind_prefix))
            if self.entity_cache is not None
            else None
        )
        if cached is not None:
            return list(cached)
        found = []
        for entity in self.model.in_area(area_id):
            if entity_role(entity) is not EntityRole.MAIN:
                continue
            if self.model.unit(entity.entity_id) != entity.entity_id:
                # A lamp steered through its group (the group has priority), a group that only
                # gathers ("all lights", several rooms), or what is left alone (E-458).
                continue
            if domain == "light" and entity.entity_id in self.not_lamps:
                continue  # a status light (B-359)
            if entity.entity_id in self.hand_switches:
                continue  # the residents' own switch (E-448)
            kind = self._kind(entity)
            if kind is None or not kind.actions:
                continue
            if (domain == "light" and (entity.domain == "light" or kind.key == "switch.light")) or (
                entity.domain == domain and kind.key.startswith(kind_prefix)
            ):
                found.append(entity)
        found.sort(key=lambda e: e.entity_id)
        if self.entity_cache is not None:
            self.entity_cache[(area_id, domain, kind_prefix)] = list(found)
        return found

    def units_of(self, skill: str, area_id: str) -> list[Entity]:
        """What this skill works on in this room, as HouseMind addresses it: a group for its
        lamps (E-458). For what the card and the question say holds a rule (E-460)."""
        cached = (
            self.entity_cache.get((area_id, skill, "*")) if self.entity_cache is not None else None
        )
        if cached is not None:
            return list(cached)
        device = PART_DEVICES.get(str(self.params.get(skill, {}).get("domain")))
        found = []
        for entity in self.model.in_area(area_id):
            if entity_role(entity) is not EntityRole.MAIN:
                continue
            if self.model.unit(entity.entity_id) != entity.entity_id:
                continue
            if entity.entity_id in self.not_lamps or entity.entity_id in self.hand_switches:
                continue
            kind = self._kind(entity)
            if kind is None or not kind.actions:
                continue
            if (
                skill in kind.skills
                or entity.domain == device
                or (device == "light" and kind.key == "switch.light")
            ):
                found.append(entity)
        if self.entity_cache is not None:
            self.entity_cache[(area_id, skill, "*")] = list(found)
        return found

    def _not_here(self, skill: str, area_id: str, role: str) -> bool:
        """This skill never works in a room like this, or only where an automation already
        does it (E-460: the light in a garage, ``only_with_automation``)."""
        params = self.params.get(skill, {})
        if role in params.get("never_in", ()):
            return True
        return (
            role in params.get("only_with_automation", ())
            and (skill, area_id) not in self.with_automation
        )

    def follows_devices(self, skill: str) -> bool:
        """A rule that comes and goes with the devices of a room (E-485, B-400): its part has
        devices in rooms, and a kind of device in the catalogue names it."""
        if self.follow_cache is None:
            named = {s for kind in self.catalog.kinds.values() for s in kind.skills}
            self.follow_cache = frozenset(
                s
                for s, params in self.params.items()
                if str(params.get("domain")) in ROOM_DEVICES and s in named
            )
        return skill in self.follow_cache

    def can_do(self, skill: str, area_id: str) -> bool:
        """This room has what this rule works on, the rule fits this kind of room, and the
        room measures what the rule needs (E-485, B-400). The same devices the skill itself
        would use: a lamp in a group counts through its group, a status light or a resident's
        own switch does not, a thermostat only with the modes the rule is about. A device that
        is unavailable is still there; one switched off or hidden in HA is not."""
        if not self.follows_devices(skill) or area_id not in self.model.areas:
            return False
        cached = self.can_cache.get((skill, area_id))
        if cached is not None:
            return cached
        params = self.params[skill]
        role = self.area_roles.get(area_id, "other")
        within = params.get("in") or params.get("only_in")
        fits = (not within or role in within) and not self._not_here(skill, area_id, role)
        found = (
            fits
            and bool(self._rule_devices(skill, area_id))
            and all(self._measures(area_id, str(need)) for need in params.get("needs", ()))
        )
        self.can_cache[(skill, area_id)] = found
        return found

    def still_has(self, skill: str, area_id: str) -> bool:
        """The room still has a device of this rule in Home Assistant (E-487): a lamp, curtain,
        thermostat or fan of the right kind, and what the rule measures – also one HouseMind
        leaves alone (a resident's own switch, a status light, a lamp in a group). Only a device
        taken out of Home Assistant, or switched off there, is gone."""
        if not self.follows_devices(skill) or area_id not in self.model.areas:
            return False
        cached = self.has_cache.get((skill, area_id))
        if cached is not None:
            return cached
        domain, prefix = ROOM_DEVICES[str(self.params[skill].get("domain"))]
        found = False
        for entity in self.model.entities.values():
            if entity.disabled:
                continue
            kind = self._kind(entity) if entity.domain in (domain, "switch") else None
            if kind is None:
                continue
            if domain == "light":
                same = entity.domain == "light" or kind.key == "switch.light"
            else:
                same = entity.domain == domain and kind.key.startswith(prefix)
            area = self.model.area_of(entity.entity_id) if same else None
            if area is not None and area.id == area_id:
                found = True
                break
        found = found and all(
            self._measures(area_id, str(need)) for need in self.params[skill].get("needs", ())
        )
        self.has_cache[(skill, area_id)] = found
        return found

    def _rule_devices(self, skill: str, area_id: str) -> list[Entity]:
        domain, prefix = ROOM_DEVICES[str(self.params[skill].get("domain"))]
        devices = self._entities(area_id, domain, prefix)
        if domain != "climate":
            return devices
        modes = set(self.params[skill].get("modes", ()))
        return [
            e
            for e in devices
            if (self.room_climates is None or e.entity_id in self.room_climates)
            and (not modes or modes & set(e.capabilities.get("hvac_modes") or ["heat"]))
        ]

    def _measures(self, area_id: str, need: str) -> bool:
        """The room has a sensor for this: humidity, CO₂, or an opening of the house."""
        domain, classes = MEASURES.get(need, ("", frozenset()))
        for entity in self.model.entities.values():
            if entity.domain != domain or entity.device_class not in classes:
                continue
            if entity_role(entity) is EntityRole.NOISE or entity.entity_category:
                continue
            if need == "opening" and self.openings is not None:
                if entity.entity_id not in self.openings:
                    continue  # a dishwasher's door is no window
            elif need != "opening" and self._inside_a_device(entity):
                continue  # a sensor in a humidifier measures the device
            area = self.model.area_of(entity.entity_id)
            if area is not None and area.id == area_id:
                return True
        return False

    def possible(self) -> frozenset[tuple[str, str]]:
        """Every rule the rooms have the devices for now (E-485): (skill, room)."""
        if self.possible_cache is None:
            self.possible_cache = frozenset(
                (skill, area_id)
                for skill in self.params
                if self.follows_devices(skill)
                for area_id in self.model.areas
                if self.can_do(skill, area_id)
            )
        return self.possible_cache

    def _inside_a_device(self, entity: Entity) -> bool:
        """A sensor in a humidifier, airco or boiler measures the device, not the room."""
        return self.model.on_an_air_device(entity)

    def dimmable_light(self, area_id: str) -> Entity | None:
        """The first light in a room that can come up slowly."""
        for entity in self._entities(area_id, "light"):
            kind = self._kind(entity)
            allowed = kind.allowed(f"{entity.domain}.turn_on") if kind else None
            if allowed is not None and "brightness_pct" in allowed.values:
                return entity
        return None

    def standen(self) -> LightLevels:
        """The standen of the lamps (B-406); without any, every lamp is the start."""
        if self.stands is None:
            self.stands = LightLevels(self.model)
        return self.stands

    def turned_off(self, entity_id: str, at: datetime) -> None:
        """HouseMind itself turned this lamp off after nobody was there (the gate executed it;
        on Observe nothing happens and nothing is noted)."""
        key = self._off_keys.pop(entity_id, None)
        if key is not None:
            self._own_off[key] = at

    def _signalling(self, entity_id: str, now: datetime) -> bool:
        return any(
            self.in_signal(lamp, now) for lamp in (entity_id, *self.model.groups.get(entity_id, ()))
        )

    def _people_lit_at(self, key: str, own: bool = False) -> datetime | None:
        """When a lamp of a place was last on by people's choice: a lamp that went off because
        HouseMind turned it off does not count (QA 2, H-10: coming back right after that is
        coming into the dark). ``own``: only its own lamps and those of a room whose light
        does for it (E-530)."""
        lit_at = (self._own_lit_at if own else self._lit_at).get(key)
        own_off = self._own_off.get(key)
        if lit_at is not None and own_off is not None and own_off >= lit_at:
            return None
        return lit_at

    def _open_lamps(self, area_id: str) -> list[Entity]:
        """The lamps of the rooms that are one open space with this one (E-268)."""
        return [
            lamp
            for other in sorted(self.open_space.get(area_id, ()))
            for lamp in self._entities(other, "light")
        ]

    def _shared_lamps(self, area_id: str, now: datetime) -> list[Entity]:
        """The lamps of the rooms next to this one whose light does for it, as people showed
        it (E-530) – none until they did."""
        return [
            lamp
            for other in sorted(self.open_space.get(area_id, ()))
            if self.prefs.shares_light(area_id, other, now)
            for lamp in self._entities(other, "light")
        ]

    def _lit_rooms(self, area_id: str, states: dict[str, State], now: datetime) -> list[str]:
        """The rooms of the open space around this one with a lamp on (E-530)."""
        return [
            other
            for other in sorted(self.open_space.get(area_id, ()))
            if any(
                (st := states.get(e.entity_id)) is not None
                and st.state == "on"
                and not self._signalling(e.entity_id, now)
                for e in self._entities(other, "light")
            )
        ]

    def _req(
        self,
        world: WorldState,
        layer: Layer,
        skill: str,
        area_id: str,
        entity: Entity,
        action: str,
        data: dict[str, Any],
        **facts: Any,
    ) -> ActionRequest:
        return ActionRequest(
            layer,
            entity.entity_id,
            action,
            data,
            reason=f"skill:{skill}",
            skill=skill,
            area_id=area_id,
            created_at=world.now,
            facts={"context": world.context_key(), "mode": world.mode.value, **facts},
        )

    def _light_data(
        self, world: WorldState, area_id: str, entity: Entity, skill: str
    ) -> dict[str, Any]:
        kind = self._kind(entity)
        if kind is None:
            return {}
        allowed = kind.allowed(f"{entity.domain}.turn_on")
        if allowed is None:
            return {}
        params = self.params.get(skill, {})
        part = world.part or daypart(world.local)  # the house's own rhythm (E-455)
        if self.daylight is not None and skill != "night_light":
            # A lamp on the day's rhythm (E-466): that integration or HouseMind's own working
            # out says it. Its night light stays what people chose for the night.
            ruled = self.daylight.on_data(
                entity.entity_id,
                allowed.values,
                world.now,
                lambda key: self.prefs.day_shift(area_id, part, key),
                skill,
                part,
            )
            if ruled is not None:
                return ruled
        # In felt percentages, each lamp its own (B-406, B-408): a rule that uses its start
        # value asks for that stand, and a fixed colour of that stand goes first.
        found: dict[str, tuple[float, bool]] = {}
        for key in ("brightness_pct", "color_temp_kelvin"):
            if key not in allowed.values:
                continue
            start = params.get(key)
            default = start.get(part) if isinstance(start, dict) else start
            learned = self.prefs.light_value(skill, area_id, world.context_key(), part, key)
            chosen = learned if learned is not None else default
            if isinstance(chosen, int | float):
                found[key] = (float(chosen), learned is not None)
        levels = self.standen()
        data: dict[str, Any] = {}
        stand: int | None = None
        if "brightness_pct" in found:
            felt, learned_it = found["brightness_pct"]
            if levels.has_stands(entity.entity_id):
                # Someone gave it standen: one of the four, also for what the house taught
                # (E-576) – its brightness and its colour.
                stand = nearest(felt)
                felt = FELT[stand]
            else:
                stand = None if learned_it else stand_of(felt)
            data["brightness_pct"] = levels.real(entity.entity_id, felt)
        kelvin = found["color_temp_kelvin"][0] if "color_temp_kelvin" in found else None
        if stand is not None:
            data.update(levels.colour_data(entity.entity_id, stand, allowed.values, None, kelvin))
        elif kelvin is not None:
            data["color_temp_kelvin"] = round(kelvin)
        return data

    def _done(self, key: str, world: WorldState) -> bool:
        return self._done_today.get(key) == world.local.date().isoformat()

    def _mark(self, key: str, world: WorldState) -> None:
        """Done for today – only once it really did something, so a grey afternoon that closes
        nothing does not stop the evening close."""
        self._done_today[key] = world.local.date().isoformat()

    def _once_today(self, key: str, world: WorldState) -> bool:
        today = world.local.date().isoformat()
        if self._done_today.get(key) == today:
            return False
        self._done_today[key] = today
        return True

    # -- the skills ---------------------------------------------------------------------

    def evaluate(
        self, world: WorldState, states: dict[str, State], rooms: Collection[str] | None = None
    ) -> list[ActionRequest]:
        """What each skill wants now, in these rooms (S2: after a change only where it can
        matter); None: every room."""
        requests: list[ActionRequest] = []
        wake = self.wake_at(world)
        progress = self._waking(world, wake)
        for area_id in self.model.areas if rooms is None else rooms:
            if area_id not in self.model.areas:
                continue
            role = self.area_roles.get(area_id, "other")
            waking = progress is not None and role in self.params.get("wake_light", {}).get(
                "in", ()
            )
            if waking and wake is not None and progress is not None:
                requests += self._wake(world, states, area_id, wake, progress)
            requests += self._lights(world, states, area_id, role, waking)
            requests += self._bedtime(world, states, area_id, role)
            requests += self._goodnight(world, states, area_id, role)
            evening = self._outside_evening(world, states, area_id, role)
            requests += evening
            requests += self._outside_arrival(world, states, area_id, role, bool(evening))
            requests += self._covers(world, states, area_id, role)
            requests += self._climate(world, states, area_id, role, wake)
            requests += self._air(world, states, area_id)
        return requests

    def dimming(self, area_id: str) -> bool:
        """A dim warning runs in this room or a part of it: its lamps are not to be touched."""
        keys = [*self._dimmed, *(place for place, _, _ in self._lamp_dimmed.values())]
        return any(key == area_id or key.startswith(f"{area_id}#") for key in keys)

    def next_recheck(self, now: datetime) -> datetime | None:
        """The first moment a place wants to be looked at again, if any (E-443)."""
        due = [at for at in self._recheck.values() if at > now]
        return min(due) if due else None

    # -- sleeping and waking up (E-88) --------------------------------------------------

    def _waking(self, world: WorldState, wake: tuple[datetime, str] | None) -> float | None:
        """How far waking up is (0 → 1) while the wake light runs, else None."""
        if wake is None or world.residents_home == 0:
            return None
        if world.mode not in (HouseMode.SLEEP, HouseMode.HOME):
            return None
        params = self.params.get("wake_light", {})
        ramp = timedelta(minutes=float(params.get("ramp_minutes", 20)))
        hold = timedelta(minutes=float(params.get("hold_minutes", 15)))
        at = wake[0]
        if not at - ramp <= world.local < at + hold:
            return None
        return min(1.0, (world.local - (at - ramp)) / ramp)

    def _wake(
        self,
        world: WorldState,
        states: dict[str, State],
        area_id: str,
        wake: tuple[datetime, str],
        progress: float,
    ) -> list[ActionRequest]:
        """Light that comes up slowly before the wake-up time, in steps (E-88). A light
        someone switched on stays as it is; one switched off during waking stays off (the
        safety gate keeps HouseMind off it, E-33)."""
        params = self.params.get("wake_light", {})
        steps = [int(x) for x in params.get("steps_pct", [3, 15, 35, 60])]
        kelvins = [int(x) for x in params.get("kelvin_steps", [2200, 2500, 2900, 3300])]
        index = min(len(steps) - 1, int(progress * (len(steps) - 1) + 1e-9))
        today = world.local.date().isoformat()
        at, source = wake
        out: list[ActionRequest] = []
        usual = self.usual_lights(area_id)
        for entity in self._entities(area_id, "light"):
            if usual is not None and entity.entity_id not in usual:
                continue
            kind = self._kind(entity)
            allowed = kind.allowed(f"{entity.domain}.turn_on") if kind else None
            state = states.get(entity.entity_id)
            if allowed is None or "brightness_pct" not in allowed.values or state is None:
                continue  # only lights that can come up slowly
            ours = self._wake_set.get(entity.entity_id) == today
            if state.state == "on" and not ours:
                continue  # someone switched it on already
            brightness = state.attributes.get("brightness")
            current = brightness * 100 / 255 if isinstance(brightness, int | float) else None
            levels = self.standen()
            sunrise = kelvins[min(index, len(kelvins) - 1)] if kelvins else None
            data: dict[str, Any]
            if levels.has_stands(entity.entity_id):
                # Its standen (E-576): up through them, each with its own colour; a stand on
                # the day's rhythm takes the warm-to-white of the sunrise.
                data = levels.stand_data(
                    entity.entity_id, nearest(steps[index]), allowed.values, None, sunrise
                )
            else:
                data = {"brightness_pct": levels.real(entity.entity_id, steps[index])}  # B-408
                if "color_temp_kelvin" in allowed.values and sunrise is not None:
                    data["color_temp_kelvin"] = sunrise
            target = data.get("brightness_pct", 0)
            if state.state == "on" and current is not None and current >= target - 1:
                continue
            self._wake_set[entity.entity_id] = today
            out.append(
                self._req(
                    world,
                    Layer.PLANNER,
                    "wake_light",
                    area_id,
                    entity,
                    f"{entity.domain}.turn_on",
                    data,
                    planned=True,
                    at=at.strftime("%H:%M"),
                    source=source,
                    until=at.isoformat(),  # after waking, switching it off is no correction
                )
            )
        return out

    def _bedtime(
        self, world: WorldState, states: dict[str, State], area_id: str, role: str
    ) -> list[ActionRequest]:
        """A calm evening: half an hour before the usual bedtime, bright lights in the living
        areas go warm and dim – once an evening (E-88)."""
        params = self.params.get("bedtime_dim", {})
        if (
            role not in params.get("in", ())
            or world.mode is not HouseMode.HOME
            or not world.occupied(area_id)
        ):
            return []
        bed = self.bed_at(world)
        before = timedelta(minutes=float(params.get("minutes_before", 30)))
        if bed is None or not bed - before <= world.local < bed:
            return []
        # The stand Gedimd (E-494): felt 15, each lamp its own brightness and colour.
        dim_to = float(params.get("dim_to_pct", FELT[0]))
        stand = stand_of(dim_to)
        kelvin = params.get("color_temp_kelvin")
        fallback = float(kelvin) if isinstance(kelvin, int | float) else None
        levels = self.standen()
        found: list[tuple[Entity, str, dict[str, Any]]] = []
        on_and_staying: list[Entity] = []
        plain: list[Entity] = []
        for entity in self._entities(area_id, "light"):
            state = states.get(entity.entity_id)
            kind = self._kind(entity)
            allowed = kind.allowed(f"{entity.domain}.turn_on") if kind else None
            if state is None or state.state != "on" or allowed is None:
                continue
            if "brightness_pct" not in allowed.values:
                plain.append(entity)  # only on and off: as it is, or off (E-492)
                continue
            on_and_staying.append(entity)
            brightness = state.attributes.get("brightness")
            if not isinstance(brightness, int | float):
                continue
            if levels.felt(entity.entity_id, brightness * 100 / 255) <= dim_to + 5:
                continue
            one = stand
            if one is None and levels.has_stands(entity.entity_id):
                one = nearest(dim_to)  # one of its four standen (E-576)
            if one is not None:
                rhythm = self._rhythm_kelvin(entity.entity_id, world.now)
                data = levels.stand_data(entity.entity_id, one, allowed.values, rhythm, fallback)
            else:
                data = {"brightness_pct": levels.real(entity.entity_id, dim_to)}
                if fallback is not None and "color_temp_kelvin" in allowed.values:
                    data["color_temp_kelvin"] = int(fallback)
            found.append((entity, f"{entity.domain}.turn_on", data))
        for entity in plain:
            if levels.dim_off(entity.entity_id) and (
                on_and_staying
                or any(o is not entity for o in plain if not levels.dim_off(o.entity_id))
            ):
                found.append((entity, f"{entity.domain}.turn_off", {}))  # another stays on
        if not found or not self._once_today(f"bed:{area_id}", world):
            return []
        # When, and how long before: the question names the room and why (E-494, E-499).
        facts: dict[str, Any] = {"at": bed.strftime("%H:%M"), "minutes": round(before.seconds / 60)}
        if stand is not None:
            facts["stand"] = STANDS[stand]
        return [
            self._req(world, Layer.PLANNER, "bedtime_dim", area_id, entity, action, data, **facts)
            for entity, action, data in found
        ]

    def _goodnight(
        self, world: WorldState, states: dict[str, State], area_id: str, role: str
    ) -> list[ActionRequest]:
        """Welterusten (E-517, the administrator's yes of 14-11-2026): when the house goes to
        sleep, what is still on or open goes off and shut, once a night, in the first half
        hour of sleep. The lamps of a room where nobody is – also one without a sensor that
        sees people: the house sleeps, so nobody is up there – but not in a bedroom (someone
        may read) and not outside (the evening light does that, E-446). And the window
        coverings that are still open, also in summer when it gets dark only after bedtime.
        What people already did themselves counts as done (E-268)."""
        if world.mode is not HouseMode.SLEEP or world.mode_since is None:
            return []
        # Once per going to sleep: Doe alsof 'Het huis slaapt' is a going to sleep of its own.
        evening = f"{world.mode_since.isoformat()}:{area_id}"
        out: list[ActionRequest] = []
        lamps = self.params.get("light_off_sleep", {})
        window = float(lamps.get("window_minutes", 30)) * 60
        since = (world.now - world.mode_since).total_seconds()
        if (
            lamps
            and role not in lamps.get("not_in", ())
            and since <= window
            and self._goodnight_done.get(f"light:{area_id}") != evening
            and not self._still_up(world, area_id)
        ):
            self._goodnight_done[f"light:{area_id}"] = evening
            lights = self._entities(area_id, "light")
            on = [
                e
                for e in lights
                if (s := states.get(e.entity_id)) is not None
                and s.state == "on"
                and not self._signalling(e.entity_id, world.now)
            ]
            if lights and not on:
                ids = [e.entity_id for e in lights]
                self.prefs.done_before(
                    "light_off_sleep",
                    area_id,
                    ids,
                    "off",
                    world.now,
                    world.context_key(),
                    within=window,
                )
            out += [
                self._req(
                    world,
                    Layer.PLANNER,
                    "light_off_sleep",
                    area_id,
                    entity,
                    f"{entity.domain}.turn_off",
                    {},
                )
                for entity in on
            ]
        covers = self.params.get("covers_close_sleep", {})
        window = float(covers.get("window_minutes", 30)) * 60
        if (
            covers
            and role not in covers.get("not_in", ())
            and since <= window
            and self._goodnight_done.get(f"cover:{area_id}") != evening
        ):
            self._goodnight_done[f"cover:{area_id}"] = evening
            found = self._entities(area_id, "cover", "cover.window_covering")
            still = [
                e for e in found if (s := states.get(e.entity_id)) is not None and s.state == "open"
            ]
            if found and not still:
                ids = [e.entity_id for e in found]
                self.prefs.done_before(
                    "covers_close_sleep",
                    area_id,
                    ids,
                    "closed",
                    world.now,
                    world.context_key(),
                    within=window,
                )
            out += [
                self._req(
                    world,
                    Layer.PLANNER,
                    "covers_close_sleep",
                    area_id,
                    entity,
                    "cover.close_cover",
                    {},
                )
                for entity in still
            ]
        return out

    @staticmethod
    def _still_up(world: WorldState, area_id: str) -> bool:
        """Someone still in this room at bedtime, as a sensor that sees people says: then
        Welterusten looks again in a moment. A room without one only knows someone was there
        (a lamp put on by hand) – the house sleeps, so nobody is up there now (E-517)."""
        occupancy = world.occupancy.get(area_id)
        return world.occupied(area_id) and (occupancy is None or occupancy.sensed)

    def _plain_off(self, lights: list[Entity], usual: set[str] | None) -> set[str]:
        """The lamps that only go on and off and are set to 'off' at Gedimd, when a lamp here
        that dims does go on (E-492): never dark, never a full lamp for a soft light."""
        levels = self.standen()
        dims, plain = [], set()
        for entity in lights:
            if usual is not None and entity.entity_id not in usual:
                continue
            kind = self._kind(entity)
            allowed = kind.allowed(f"{entity.domain}.turn_on") if kind else None
            if allowed is not None and "brightness_pct" in allowed.values:
                dims.append(entity)
            elif levels.dim_off(entity.entity_id):
                plain.add(entity.entity_id)
        return plain if dims else set()

    def _rhythm_kelvin(self, entity_id: str, now: datetime) -> float | None:
        """The colour the day's rhythm gives this lamp now, when HouseMind does it (B-407)."""
        book = self.daylight
        if book is None:
            return None
        rhythm = book.group_of(entity_id)
        if rhythm is None or not book.ours(rhythm):
            return None
        return book.level(rhythm, now)[1]

    # -- outside in the evening (E-446) ---------------------------------------------------

    def evening_off(self, world: WorldState, area_id: str) -> tuple[datetime, str]:
        """When the lamps outside go out this evening, and why: the latest time – learned from
        the house or an automation, else ``off_at`` – or, with nobody home, the usual bedtime
        if that is earlier, a little different each evening so the house looks lived in.
        Fixed once per evening; the house going to sleep comes before it (the skill)."""
        evening = (world.local - timedelta(hours=12)).date()
        away = world.mode in (HouseMode.AWAY, HouseMode.VACATION)
        kept = self._evening_off.get(area_id)
        if kept is not None and kept[:2] == (evening, away):
            return kept[2], kept[3]
        params = self.params.get(EVENING, {})
        tz = world.local.tzinfo
        noon = datetime.combine(evening, time(12), tz)
        learned = self.prefs.value(EVENING, area_id, "*", "off_minutes")
        if learned is not None:
            latest = noon + timedelta(minutes=learned)
        else:
            hh, mm = (int(x) for x in str(params.get("off_at", "23:30")).split(":"))
            latest = datetime.combine(evening, time(hh, mm), tz)
            if latest <= noon:
                latest += timedelta(days=1)  # after midnight
        at, why = latest, "late"
        if away:
            bed = self.bed_at(world)
            why = "away_late"
            if bed is not None and noon < bed.astimezone(tz) < latest:
                at, why = bed.astimezone(tz), "bedtime"
            vary = float(params.get("vary_minutes", 15))
            rng = random.Random(evening.toordinal())  # noqa: S311 - the same for every place
            at += timedelta(minutes=round(rng.uniform(-vary, vary)))
        self._evening_off[area_id] = (evening, away, at, why)
        return at, why

    def _outside_evening(
        self, world: WorldState, states: dict[str, State], area_id: str, role: str
    ) -> list[ActionRequest]:
        """The lamps outside on when it gets dark – also with nobody home, so the house looks
        lived in and keeps burglars at a distance – and off when the house goes to sleep, at
        the latest at the evening's own time (E-446). Once an evening each."""
        params = self.params.get(EVENING)
        if not params or role not in params.get("only_in", ()):
            return []
        lamps = self._entities(area_id, "light")
        if not lamps:
            return []
        if any(self._camera_lit(e.entity_id) for e in lamps):
            return []  # a camera's few minutes first: they go out by themselves
        evening = (world.local - timedelta(hours=12)).date()
        key = f"{evening.isoformat()}:{area_id}"
        off_at, why = self.evening_off(world, area_id)
        away = world.mode in (HouseMode.AWAY, HouseMode.VACATION)
        sleeping = world.mode is HouseMode.SLEEP
        window = float(params.get("window_minutes", 45)) * 60
        ids = [e.entity_id for e in lamps]
        on = [e for e in lamps if (s := states.get(e.entity_id)) is not None and s.state == "on"]
        off = [e for e in lamps if (s := states.get(e.entity_id)) is not None and s.state == "off"]
        # Lamps HouseMind put on for someone coming home (E-447) go out on that skill's time.
        arriving = [e for e in on if e.entity_id in self._arrival_lit]
        if (
            world.outside_dark
            and not sleeping
            and world.local < off_at
            and self._evening_done.get(f"on:{area_id}") != key
        ):
            self._evening_done[f"on:{area_id}"] = key
            for entity in arriving:
                self._arrival_lit.pop(entity.entity_id, None)  # the evening light now
            if not off:
                # On already – by people or an automation around dusk? Right (E-268).
                self.prefs.done_before(
                    EVENING, area_id, ids, "on", world.now, world.context_key(), within=window
                )
                return []
            facts: dict[str, Any] = {"window": window}
            if away:
                facts.update(variant="away", nobody_home=True)
            return [
                self._req(
                    world,
                    Layer.PLANNER,
                    EVENING,
                    area_id,
                    entity,
                    f"{entity.domain}.turn_on",
                    self._light_data(world, area_id, entity, EVENING),
                    **facts,
                )
                for entity in off
            ]
        late = off_at <= world.local < off_at + timedelta(hours=2)
        if (sleeping or late) and self._evening_done.get(f"off:{area_id}") != key:
            if world.occupied(area_id):
                return []  # someone outside now: not in the dark, try again in a moment
            self._evening_done[f"off:{area_id}"] = key
            on = [e for e in on if e not in arriving]
            if not on:
                if not arriving:
                    self.prefs.done_before(
                        EVENING, area_id, ids, "off", world.now, world.context_key(), within=window
                    )
                return []
            facts = {"variant": "sleep" if sleeping and not late else why, "window": window}
            facts["at"] = off_at.strftime("%H:%M")
            if away:
                facts["nobody_home"] = True
            return [
                self._req(
                    world,
                    Layer.PLANNER,
                    EVENING,
                    area_id,
                    entity,
                    f"{entity.domain}.turn_off",
                    {},
                    **facts,
                )
                for entity in on
            ]
        return []

    # -- coming home in the dark (E-447) ------------------------------------------------

    def came_home(self, now: datetime) -> None:
        """Someone came home (a person to "home"): the lamps at the entrance, if it is dark."""
        self._home_at = now

    def lit_for_arrival(self, entity_id: str, area_id: str, at: datetime) -> None:
        """HouseMind put this lamp on for someone coming home (the gate executed it): it is
        HouseMind's to put out again, and looked at again the moment its minutes are over."""
        self._arrival_lit[entity_id] = (area_id, at)
        minutes, _ = arrival_minutes(self.params.get(ARRIVAL, {}), self.prefs, area_id)
        self._recheck[f"arrival:{area_id}"] = at + timedelta(minutes=minutes)

    def arrival_put_out(self, entity_id: str, now: datetime) -> None:
        """Someone put out a lamp HouseMind lit for coming home before its time: that is how
        long this house wants it there. Several lamps at once are one moment."""
        held = self._arrival_lit.pop(entity_id, None)
        if held is None:
            return
        area_id, since = held
        last = self._arrival_learned.get(area_id)
        if last is not None and timedelta(0) <= now - last < timedelta(minutes=1):
            return
        self._arrival_learned[area_id] = now
        self.prefs.arrival_minutes(area_id, (now - since).total_seconds() / 60, now, 1.0)

    def arrival_place(self, area_id: str) -> bool:
        """An outside place at the entrance: its name says so (a driveway, a front door, a
        carport …), or this skill knows it there – an automation taught it, or it has a step.
        A garden or terrace at the back stays dark."""
        if self.prefs.known_here(ARRIVAL, area_id):
            return True
        found = self._entrance.get(area_id)
        if found is None:
            area = self.model.areas.get(area_id)
            names = [_words(n) for n in ((area.name, *area.aliases) if area else ())]
            words = [w for w in (_words(w) for w in self.entrance_words) if w]
            found = any(
                re.search(rf"(?<!\w){re.escape(word)}", name) for name in names for word in words
            )
            self._entrance[area_id] = found
        return found

    def _outside_arrival(
        self,
        world: WorldState,
        states: dict[str, State],
        area_id: str,
        role: str,
        evening_busy: bool,
    ) -> list[ActionRequest]:
        """Someone coming home in the dark: the lamps at the entrance on at once, and off again
        a few minutes later – later while someone is still outside there, at the latest after
        max_minutes, and at once when everyone left. Lamps already on – the evening light, a
        camera's, someone's – stay as they are, and HouseMind never puts those out."""
        params = self.params.get(ARRIVAL)
        if not params or role not in params.get("only_in", ()):
            return []
        out = self._arrival_off(world, states, area_id, params)
        home_at = self._home_at
        if home_at is None or self._arrival_done.get(area_id) == home_at:
            return out
        window = float(params.get("arrive_seconds", 120))
        if world.now - home_at > timedelta(seconds=window) or not world.outside_dark:
            return out
        if evening_busy or not self.arrival_place(area_id):
            return out  # the evening light first: the next round, if it did not happen
        lamps = self._entities(area_id, "light")
        if not lamps:
            return out
        self._arrival_done[area_id] = home_at
        held = [e for e in lamps if e.entity_id in self._arrival_lit]
        for entity in held:  # home again while they burn: the minutes start again
            self._arrival_lit[entity.entity_id] = (area_id, world.now)
        lit = [
            e
            for e in lamps
            if e not in held and (s := states.get(e.entity_id)) is not None and s.state == "on"
        ]
        if held or lit:
            if not held:  # on already – by people or an automation just now? Right.
                ids = [e.entity_id for e in lamps]
                self.prefs.done_before(
                    ARRIVAL, area_id, ids, "on", world.now, world.context_key(), within=window
                )
            return out
        off = [e for e in lamps if (s := states.get(e.entity_id)) is not None and s.state == "off"]
        return out + [
            self._req(
                world,
                Layer.REFLEX,
                ARRIVAL,
                area_id,
                entity,
                f"{entity.domain}.turn_on",
                self._light_data(world, area_id, entity, ARRIVAL),
                window=window,
            )
            for entity in off
        ]

    def _arrival_off(
        self, world: WorldState, states: dict[str, State], area_id: str, params: dict[str, Any]
    ) -> list[ActionRequest]:
        """The lamps HouseMind lit for coming home at this place, out when their minutes are
        over and nobody is outside there any more, after max_minutes anyway, and at once when
        everyone left."""
        held = [(lamp, at) for lamp, (area, at) in self._arrival_lit.items() if area == area_id]
        if not held:
            return []
        minutes, _ = arrival_minutes(params, self.prefs, area_id)
        longest = timedelta(minutes=float(params.get("max_minutes", 30)))
        out: list[ActionRequest] = []
        for lamp, since in held:
            state = states.get(lamp)
            entity = self.model.entities.get(lamp)
            if state is None or state.state != "on" or entity is None:
                self._arrival_lit.pop(lamp, None)  # out already, or gone: let go
                continue
            due = since + timedelta(minutes=minutes)
            if world.residents_home == 0:
                variant = "gone"
            elif world.now >= since + longest or (world.now >= due and not world.occupied(area_id)):
                variant = "done"
            else:
                if world.now < due:
                    self._recheck[f"arrival:{area_id}"] = due  # look again at that moment
                continue
            self._arrival_lit.pop(lamp, None)
            out.append(
                self._req(
                    world,
                    Layer.PLANNER,  # after a yes to the light, the yes holds for this (lease)
                    ARRIVAL,
                    area_id,
                    entity,
                    f"{entity.domain}.turn_off",
                    {},
                    variant=variant,
                    minutes=f"{round(minutes):g}",
                    hold_minutes=minutes,
                )
            )
        return out

    def _camera_lit(self, entity_id: str) -> bool:
        return any(
            self.camera_lit(lamp) for lamp in (entity_id, *self.model.groups.get(entity_id, ()))
        )

    def _air(
        self, world: WorldState, states: dict[str, State], area_id: str
    ) -> list[ActionRequest]:
        """Ventilate on humidity (after a shower) and on CO₂. Only fans HouseMind turned on
        are turned off again – a fan someone switched on stays on."""
        out: list[ActionRequest] = []
        fans = self._entities(area_id, "fan")
        if not fans:
            return out
        humid = self.params.get("ventilate_humidity", {})
        co2 = self.params.get("ventilate_co2", {})
        reasons: list[tuple[str, bool, bool]] = []  # skill, want on, may turn off
        for entity in self.model.in_area(area_id):  # also via the device (most sensors)
            if entity.domain != "sensor" or entity.hidden or entity.entity_category:
                continue
            if entity.device_class not in ("humidity", "carbon_dioxide", "co2"):
                continue
            if self._inside_a_device(entity):
                continue
            try:
                value = float(states[entity.entity_id].state)
            except (KeyError, ValueError):
                continue
            if not _plausible(entity.device_class or "", value):
                continue  # a broken or stuck sensor (E-43)
            if entity.device_class == "humidity" and humid:
                reasons.append(
                    (
                        "ventilate_humidity",
                        value >= float(humid.get("on_pct", 70)),
                        value <= float(humid.get("off_pct", 60)),
                    )
                )
            elif entity.device_class in ("carbon_dioxide", "co2") and co2:
                reasons.append(
                    (
                        "ventilate_co2",
                        value >= float(co2.get("on_ppm", 1000)),
                        value <= float(co2.get("off_ppm", 800)),
                    )
                )
        if area_id in world.showers:
            # Someone showers (E-531): the fan goes on at once, before the humidity is high.
            reasons.append(("ventilate_humidity", True, False))
        if not reasons:
            return out
        wanted = next((skill for skill, on, _ in reasons if on), None)
        all_low = all(off for _, _, off in reasons)
        for fan in fans:
            state = states.get(fan.entity_id)
            if state is None:
                continue
            if wanted and state.state == "off":
                self._air_on.add(fan.entity_id)
                out.append(self._req(world, Layer.REFLEX, wanted, area_id, fan, "fan.turn_on", {}))
            elif all_low and state.state == "on" and fan.entity_id in self._air_on:
                skill = reasons[0][0]
                out.append(
                    self._req(
                        world, Layer.REFLEX, skill, area_id, fan, "fan.turn_off", {}, done=True
                    )
                )
            elif state.state == "off":
                self._air_on.discard(fan.entity_id)
        return out

    def _lights(
        self,
        world: WorldState,
        states: dict[str, State],
        area_id: str,
        role: str,
        waking: bool = False,
    ) -> list[ActionRequest]:
        """The room's own lamps follow the room; the lamps of a part of the room (a zone of a
        presence sensor, E-215) follow only that part."""
        out: list[ActionRequest] = []
        lights = self._entities(area_id, "light")
        if not lights:
            return out
        occ = world.occupancy.get(area_id)
        parts = [z for z in (occ.zones if occ else ()) if z.lamps]
        part_of = {lamp: z for z in parts for lamp in z.lamps}
        for zone in parts:  # a group whose lamps are all in the zone follows it too (E-458)
            for group in {self.model.unit(lamp) for lamp in zone.lamps} - {None}:
                lamps = self.model.groups.get(str(group), ())
                if lamps and set(lamps) <= set(zone.lamps):
                    part_of.setdefault(str(group), zone)
        room = [e for e in lights if e.entity_id not in part_of]
        if room:
            occupied = world.occupied(area_id)
            lit = occ.for_lamps if occ is not None and occ.for_lamps is not None else occupied
            last = occ.last_activity if occ is not None else None
            out += self._light_place(
                world,
                states,
                area_id,
                role,
                room,
                area_id,
                occupied,
                lit,
                last,
                occ is not None and occ.sensed,  # without a sensor: never "nobody there" (E-462)
                waking,
                occ is not None and occ.presence,
            )
        for zone in parts:
            own = [e for e in lights if part_of.get(e.entity_id) is zone]
            if own:
                key = f"{area_id}#{zone.entity_id}"
                out += self._light_place(
                    world,
                    states,
                    area_id,
                    role,
                    own,
                    key,
                    zone.on,
                    zone.on,
                    zone.since,
                    True,
                    waking,
                    True,
                )
        return out

    def _light_place(
        self,
        world: WorldState,
        states: dict[str, State],
        area_id: str,
        role: str,
        lights: list[Entity],
        key: str,
        occupied: bool,
        lit: bool,
        last_activity: datetime | None,
        known: bool,
        waking: bool = False,
        blinks: bool = False,
    ) -> list[ActionRequest]:
        """Lamps of one place: a room, or a part of it. ``lit``: someone these lamps are for;
        ``occupied``: someone there at all (then they never go off); ``blinks``: a presence
        sensor watches it, which may lose someone for a few seconds."""
        out: list[ActionRequest] = []
        on_params = self.params.get("light_on_entry", {})
        off_params = self.params.get("light_off_leave", {})
        # No lamp in a bedroom from the usual bedtime until the morning – also while someone
        # else is still up (E-455); without a rhythm, the clock's night.
        night = world.bedtime if world.part else daypart(world.local) == "night"
        sleep = world.mode is HouseMode.SLEEP
        room = key == area_id
        # Dark enough, as people in this room showed it (E-268); else the general rule. The
        # night light and the parts of a room keep the general rule.
        levels = {
            measure: value
            for measure, value in (("sun", world.sun), ("lux", world.lux.get(area_id)))
            if value is not None
        }
        learned = self.prefs.dark(area_id, levels, world.now) if room and not sleep else None
        dark = learned if learned is not None else world.dark.get(area_id, False)
        # The open space around it counts for learning (E-268); for light only the rooms whose
        # light does for this one, as people showed it (E-530).
        nearby = self._open_lamps(area_id) if room else []
        shared = {e.entity_id for e in self._shared_lamps(area_id, world.now)} if room else set()
        mine = {e.entity_id for e in lights} | shared
        # The colour of the alarm or the blink of the doorbell is no light on in the room (E-461).
        lamps_now = {
            e.entity_id
            for e in [*lights, *nearby]
            if (st := states.get(e.entity_id))
            and st.state == "on"
            and not self._signalling(e.entity_id, world.now)
        }
        if lamps_now & mine:
            self._own_lit_at[key] = world.now
        if lamps_now:
            self._lit_at[key] = world.now
            self._lit_since.setdefault(key, world.now)
            lamps_on = True
        else:
            self._lit_since.pop(key, None)
            self._unlit[key] = (dark, levels)
            lamps_on = False
        counting = (
            room
            and not sleep
            and world.mode in (HouseMode.HOME, HouseMode.GUESTS)
            and not self._not_here("light_on_entry", area_id, role)
        )
        # A room where people mostly walk through in the dark without light: coming in counts
        # only after PASS_WAIT (E-270).
        wait = counting and self.prefs.passing(area_id, world.now)
        new_visit = False
        if lit:
            gone = self._gone.pop(key, None)
            if key not in self._visit or (gone is not None and world.now - gone >= ENTRY_WINDOW):
                self._visit[key] = world.now
                self._entered.pop(key, None)
                self._tally.pop(key, None)
                self._present[key] = [0.0, None]
                new_visit = True
            elif gone is not None and world.now - gone > PASS_GAP:
                self._present[key] = [0.0, None]
        else:
            gone = self._gone.setdefault(key, world.now)
            if not blinks or world.now - gone >= ENTRY_WINDOW:
                # Gone – after a presence sensor lost someone for longer than a blink.
                self._visit.pop(key, None)
                self._entered.pop(key, None)
                self._present.pop(key, None)
                tally = self._tally.pop(key, None)
                if tally is not None and not tally["told"]:
                    self.prefs.visited(area_id, not tally["lamp"], world.now)  # walked through
        start = self._visit.get(key)
        present = self._present.get(key)
        stayed = False
        if present is not None:
            if lit and present[1] is None:
                present[1] = world.now
            elif not lit and present[1] is not None:
                present[0] += (world.now - present[1]).total_seconds()
                present[1] = None
            there = present[0] + ((world.now - present[1]).total_seconds() if present[1] else 0)
            stayed = lit and there >= PASS_WAIT.total_seconds()
            if wait and lit and dark and not stayed and not lamps_on:
                self._recheck[key] = world.now + timedelta(
                    seconds=PASS_WAIT.total_seconds() - there
                )
            else:
                self._recheck.pop(key, None)
        else:
            self._recheck.pop(key, None)
        if new_visit and counting:
            lit_since = self._lit_since.get(key)
            lit_at = self._people_lit_at(key)
            if lamps_on:
                # Lit in the moment before: people quicker than the sensor; lit long before:
                # nothing to learn.
                counts = lit_since is not None and lit_since >= world.now - ENTRY_WINDOW
            else:
                # Someone who just switched the light off is no one coming into the dark.
                counts = lit_at is None or lit_at < world.now - ENTRY_WINDOW
            own_on = any(e.entity_id in lamps_now for e in lights)
            if dark and not own_on and (others := self._lit_rooms(area_id, states, world.now)):
                # Into the dark with the room next door lit: does that light do here (E-530)?
                self.prefs.open_entry(area_id, [e.entity_id for e in lights], others, world.now)
            if counts:
                was_dark, was = self._unlit.get(key, (dark, levels)) if lamps_on else (dark, levels)
                self._tally[key] = {
                    "dark": was_dark,
                    "levels": was,
                    "lamp": lamps_on,
                    "told": False,
                    "arrived": False,
                }
        tally = self._tally.get(key)
        if tally is not None:
            tally["lamp"] = tally["lamp"] or lamps_on
            if tally["dark"] and not tally["told"] and (stayed or tally["lamp"]):
                tally["told"] = True
                self.prefs.visited(area_id, False, world.now)  # stayed, or wanted light
            if not tally["arrived"] and (not wait or stayed) and start is not None:
                tally["arrived"] = True
                self.prefs.arrived(
                    "light_on_entry",
                    area_id,
                    [e.entity_id for e in [*lights, *nearby]],
                    world.now,
                    tally["levels"],
                    tally["dark"],
                    lamps_on,
                    world.context_key(),
                    f"light_on_entry|{key}|{world.now.isoformat()}",
                    start - ENTRY_WINDOW,
                )
            if not tally["dark"]:
                tally["told"] = True  # by daylight walking through teaches nothing about this
        if lit and dark and (not wait or stayed):
            self._entered.setdefault(key, world.now)
        elif lit and not dark:
            self._entered.pop(key, None)  # light now: getting dark again is a new moment

        # Coming back during a dim warning: restore the light (E-113) – only a lamp that was
        # really dimmed: on Observe nothing is dimmed, and "turn it back on" for a lamp that is
        # on would count as "not like that".
        own_dimmed = (
            [lamp for lamp, (place, _, _) in self._lamp_dimmed.items() if place == key]
            if occupied
            else []
        )
        if occupied and (key in self._dimmed or own_dimmed):
            _, kept = self._dimmed.pop(key, (world.now, {}))
            previous = dict(kept)
            for lamp in own_dimmed:  # a lamp with a wait of its own (E-575)
                brightness_before = self._lamp_dimmed.pop(lamp)[2]
                if brightness_before is not None:
                    previous[lamp] = brightness_before
            back = False
            for entity in lights:
                state = states.get(entity.entity_id)
                brightness = state.attributes.get("brightness") if state is not None else None
                dimmed = (
                    state is not None
                    and state.state == "on"
                    and isinstance(brightness, int | float)
                    and brightness * 100 / 255 < previous.get(entity.entity_id, 0) - 2
                )
                if dimmed:
                    # A wait of its own was set, not taught: it teaches the room nothing.
                    back = back or entity.entity_id not in own_dimmed
                    out.append(
                        self._req(
                            world,
                            Layer.REFLEX,
                            "light_off_leave",
                            area_id,
                            entity,
                            f"{entity.domain}.turn_on",
                            # … and its colour again, when the warning was in Gedimd's (E-577).
                            {
                                "brightness_pct": previous[entity.entity_id],
                                **self._dim_colour.pop(entity.entity_id, {}),
                            },
                            restored=True,
                        )
                    )
                self._dim_colour.pop(entity.entity_id, None)
            if back and room:
                # Someone was still there, or came back: this room's wait was too short (E-444).
                minutes, _ = light_off_hold(off_params, self.prefs, area_id, role, False, blinks)
                self.prefs.held_too_short(area_id, minutes, world.now)
            return out

        if lit and dark:
            skill = "night_light" if sleep else "light_on_entry"
            if skill == "night_light" and role not in self.params.get("night_light", {}).get(
                "only_in", ()
            ):
                return out
            if skill == "light_on_entry":
                if self._not_here("light_on_entry", area_id, role) or (
                    night and role in on_params.get("never_at_night_in", ())
                ):
                    return out
                if world.mode not in (HouseMode.HOME, HouseMode.GUESTS):
                    return out
            entered = self._entered.get(key)
            if entered is None:
                return out  # in a room people mostly walk through: not yet 30 seconds (E-270)
            if world.now - entered > ENTRY_WINDOW:
                return out  # long after coming in or getting dark: it stays as people left it
            lit_at = self._people_lit_at(key, own=True)
            if lit_at is not None and lit_at >= entered - ENTRY_WINDOW:
                return out  # lit now, in this moment or just before: people chose their light
            usual = self.usual_lights(area_id) if key == area_id else None
            # A night light is Gedimd: a lamp that only goes on and off and is set to 'off' at
            # Gedimd stays off when a lamp that dims goes on here (E-492).
            stays_off = self._plain_off(lights, usual) if skill == "night_light" else set()
            # One situation for the place, whatever the number of lamps (learning counts it
            # once: a hit when any lamp goes on).
            group = f"{skill}|{key}|{entered.isoformat()}"
            # The part of the day its light is for, for the Logboek (E-455).
            part = (
                {"part": world.part, "part_how": world.part_how}
                if skill == "light_on_entry" and world.part
                else {}
            )
            for entity in lights:
                if usual is not None and entity.entity_id not in usual:
                    continue
                if entity.entity_id in stays_off:
                    continue
                if self._signalling(entity.entity_id, world.now):
                    continue  # the next look, when the signal is over
                state = states.get(entity.entity_id)
                if state is not None and state.state == "off":
                    out.append(
                        self._req(
                            world,
                            Layer.REFLEX,
                            skill,
                            area_id,
                            entity,
                            f"{entity.domain}.turn_on",
                            self._light_data(world, area_id, entity, skill),
                            dark=True,
                            occupied=True,
                            group=group,
                            open_space=[e.entity_id for e in nearby],
                            # The room next door lit (E-530): this room left dark teaches
                            # whether that light does here – no "not like that".
                            open_lit=bool(lamps_now - mine),
                            **part,
                        )
                    )
            return out

        if not occupied and known and not waking:
            if role in off_params.get("never_in", ()):
                return out
            minutes, _ = light_off_hold(off_params, self.prefs, area_id, role, not room, blinks)
            hold = timedelta(minutes=minutes)
            empty_for = world.now - last_activity if last_activity else hold
            burning = [
                e
                for e in lights
                if states.get(e.entity_id)
                and states[e.entity_id].state == "on"
                and not self._signalling(e.entity_id, world.now)
            ]
            # A lamp with a wait of its own goes on its own time (E-575); the rest with the room.
            waits = self.standen()
            lit_now = {e.entity_id for e in burning}
            for lamp in [
                lamp
                for lamp, (place, _, _) in self._lamp_dimmed.items()
                if place == key and lamp not in lit_now
            ]:
                del self._lamp_dimmed[lamp]  # out by hand during its warning: nothing to give back
            own = [e for e in burning if waits.off_wait(e.entity_id) is not None]
            out += self._own_wait_off(world, states, area_id, key, own, last_activity, off_params)
            on_lights = [e for e in burning if e not in own]
            if not on_lights or empty_for < hold:
                return out
            # Never dimmed for longer than half the wait: a toilet's lamps that wait one minute
            # dim for half a minute (E-444).
            warning = timedelta(
                seconds=min(float(off_params.get("dim_warning_seconds", 60)), minutes * 30)
            )
            # Gedimd on each lamp (E-494): its colour as it is, or Gedimd's own on a lamp with
            # standen (E-577).
            dim_felt = float(off_params.get("dim_to_pct", FELT[0]))
            if key not in self._dimmed:
                before: dict[str, int] = {}
                for entity in on_lights:
                    data = self._dim_data(entity, states[entity.entity_id], dim_felt, world.now)
                    if data is not None:
                        brightness = states[entity.entity_id].attributes["brightness"]
                        before[entity.entity_id] = round(brightness * 100 / 255)
                        out.append(
                            self._req(
                                world,
                                Layer.REFLEX,
                                "light_off_leave",
                                area_id,
                                entity,
                                f"{entity.domain}.turn_on",
                                data,
                                warning=True,
                            )
                        )
                if before:
                    self._dimmed[key] = (world.now, before)
                    return out
                self._dimmed[key] = (world.now - warning, {})
            started, _ = self._dimmed[key]
            if world.now - started >= warning:
                for entity in on_lights:
                    self._off_keys[entity.entity_id] = key
                    self._dim_colour.pop(entity.entity_id, None)
                    out.append(
                        self._req(
                            world,
                            Layer.REFLEX,
                            "light_off_leave",
                            area_id,
                            entity,
                            f"{entity.domain}.turn_off",
                            {},
                            empty_minutes=round(empty_for.total_seconds() / 60),
                            hold_minutes=minutes,
                        )
                    )
                self._dimmed.pop(key, None)
        return out

    def _dim_data(
        self, entity: Entity, state: State, dim_felt: float, now: datetime
    ) -> dict[str, Any] | None:
        """What a lamp is told for its dim warning, or None when it does not dim: brighter than
        its Gedimd, and it can dim. A lamp with standen takes Gedimd's colour too, when the
        colour it has now can be given back (E-577)."""
        brightness = state.attributes.get("brightness")
        kind = self._kind(entity)
        turn_on = kind.allowed(f"{entity.domain}.turn_on") if kind else None
        levels = self.standen()
        dim_to = levels.real(entity.entity_id, dim_felt)
        if (
            turn_on is None
            or "brightness_pct" not in turn_on.values
            or not isinstance(brightness, int | float)
            or not brightness
            or brightness * 100 / 255 <= dim_to
        ):
            return None
        data: dict[str, Any] = {"brightness_pct": dim_to}
        stand = stand_of(dim_felt)
        if stand is not None and levels.has_stands(entity.entity_id):
            back = colour_now(state.attributes, turn_on.values)
            rhythm = self._rhythm_kelvin(entity.entity_id, now)
            colour = levels.colour_data(entity.entity_id, stand, turn_on.values, rhythm, None)
            if colour and back:
                data.update(colour)
                self._dim_colour[entity.entity_id] = back
        return data

    def _own_wait_off(
        self,
        world: WorldState,
        states: dict[str, State],
        area_id: str,
        key: str,
        lamps: list[Entity],
        last_activity: datetime | None,
        off_params: dict[str, Any],
    ) -> list[ActionRequest]:
        """Lamps with a wait of their own (E-575): each goes off exactly that many minutes after
        its place is empty, its dim warning inside that time – half of it at most, never longer
        than the room's warning; at 0 at once, without a warning."""
        out: list[ActionRequest] = []
        waits = self.standen()
        empty_since = last_activity or world.now - timedelta(days=1)
        dim_felt = float(off_params.get("dim_to_pct", FELT[0]))
        for entity in lamps:
            lamp = entity.entity_id
            minutes = waits.off_wait(lamp) or 0
            wait = timedelta(minutes=minutes)
            warning = timedelta(
                seconds=min(float(off_params.get("dim_warning_seconds", 60)), minutes * 30)
            )
            if world.now < empty_since + wait - warning:
                self._recheck[f"off:{lamp}"] = empty_since + wait - warning
                continue
            if world.now < empty_since + wait:
                if lamp not in self._lamp_dimmed:
                    state = states[lamp]
                    data = self._dim_data(entity, state, dim_felt, world.now)
                    was = None
                    if data is not None:
                        was = round(state.attributes["brightness"] * 100 / 255)
                        out.append(
                            self._req(
                                world,
                                Layer.REFLEX,
                                "light_off_leave",
                                area_id,
                                entity,
                                f"{entity.domain}.turn_on",
                                data,
                                warning=True,
                                own_wait=minutes,
                            )
                        )
                    self._lamp_dimmed[lamp] = (key, world.now, was)
                self._recheck[f"off:{lamp}"] = empty_since + wait
                continue
            self._lamp_dimmed.pop(lamp, None)
            self._dim_colour.pop(lamp, None)
            self._recheck.pop(f"off:{lamp}", None)
            self._off_keys[lamp] = key
            out.append(
                self._req(
                    world,
                    Layer.REFLEX,
                    "light_off_leave",
                    area_id,
                    entity,
                    f"{entity.domain}.turn_off",
                    {},
                    empty_minutes=round((world.now - empty_since).total_seconds() / 60),
                    own_wait=minutes,  # set, not taught: no hold_minutes to learn from (E-444)
                )
            )
        return out

    def _covers(
        self, world: WorldState, states: dict[str, State], area_id: str, role: str
    ) -> list[ActionRequest]:
        out: list[ActionRequest] = []
        covers = self._entities(area_id, "cover", "cover.window_covering")
        if not covers:
            return out
        dark = world.outside_dark
        # The moment people or an automation close them in this room, as a height of the sun
        # (E-268, C1); else the general "dark outside".
        learned = self.prefs.value("covers_close_dark", area_id, "*", "sun_elevation")
        if learned is not None and world.sun is not None:
            dark = world.sun <= learned
        close = self.params.get("covers_close_dark", {})
        open_ = self.params.get("covers_open_morning", {})
        ids = [c.entity_id for c in covers]
        if dark and role in close.get("in", ()) and world.local.hour >= 12:
            if not self._done(f"close:{area_id}", world):
                for entity in covers:
                    if states.get(entity.entity_id) and states[entity.entity_id].state == "open":
                        self._mark(f"close:{area_id}", world)
                        out.append(
                            self._req(
                                world,
                                Layer.PLANNER,
                                "covers_close_dark",
                                area_id,
                                entity,
                                "cover.close_cover",
                                {},
                                dark=True,
                            )
                        )
                if not out and self._once_today(f"closed-before:{area_id}", world):
                    # Closed already – by people or an automation just before? Right (E-268).
                    self.prefs.done_before(
                        "covers_close_dark", area_id, ids, "closed", world.now, world.context_key()
                    )
        not_before = open_.get("not_before", {}).get(world.day_type.value, "08:00")
        hh, mm = (int(x) for x in str(not_before).split(":"))
        if (
            role in open_.get("in", ())
            and not dark
            and world.local.time() >= time(hh, mm)
            and world.local.hour < 12
            and world.mode is HouseMode.HOME
            and not self._done(f"open:{area_id}", world)
        ):
            opening = []
            for entity in covers:
                if states.get(entity.entity_id) and states[entity.entity_id].state == "closed":
                    self._mark(f"open:{area_id}", world)
                    opening.append(
                        self._req(
                            world,
                            Layer.PLANNER,
                            "covers_open_morning",
                            area_id,
                            entity,
                            "cover.open_cover",
                            {},
                            after=not_before,
                        )
                    )
            out += opening
            if not opening and self._once_today(f"opened-before:{area_id}", world):
                self.prefs.done_before(
                    "covers_open_morning", area_id, ids, "open", world.now, world.context_key()
                )
        if role in self.params.get("covers_open_wake", {}).get("in", ()):
            out += self._covers_wake(world, states, area_id, covers)
        return out

    def _covers_wake(
        self, world: WorldState, states: dict[str, State], area_id: str, covers: list[Entity]
    ) -> list[ActionRequest]:
        """A bedroom's curtains open when the people there are up (E-482 – E-484, design
        71.4): the night is over, nobody sleeps in the room any more, and it is light. Per room:
        someone asleep in another bedroom holds nothing back."""
        params = self.params.get("covers_open_wake", {})
        key = f"wake-open:{area_id}"
        if world.mode not in (HouseMode.HOME, HouseMode.GUESTS, HouseMode.AWAY):
            return []  # asleep, or on vacation: then looking lived in does it (E-285)
        hh, mm = (int(x) for x in str(params.get("latest", "12:00")).split(":"))
        morning = world.part in ("morning", "day") if world.part else world.local.hour >= 5
        if not morning or world.local.time() >= time(hh, mm) or self._done(key, world):
            return []
        view = world.sleep.get(area_id)
        signal = view.signal if view is not None else "none"
        facts: dict[str, Any] = {"signal": signal}
        if world.mode is HouseMode.AWAY:
            facts["variant"] = "away"  # everyone is out: nobody sleeps
        elif view is not None and signal != "none":
            if view.asleep:
                return []
            if view.slept:
                learned = self.prefs.value("covers_open_wake", area_id, "*", "wait_minutes")
                wait = float(params.get("wait_minutes", 5)) if learned is None else learned
                if view.empty_since is None:
                    return []
                due = view.empty_since + timedelta(minutes=wait)
                if world.now < due:
                    self._recheck[key] = due  # look again the moment the wait is over
                    return []
                facts["minutes"] = round(wait)
            else:
                # Nobody slept here last night: with the rest of the house (E-484).
                not_before = (
                    self.params.get("covers_open_morning", {})
                    .get("not_before", {})
                    .get(world.day_type.value, "08:00")
                )
                bh, bm = (int(x) for x in str(not_before).split(":"))
                if world.local.time() < time(bh, bm):
                    return []
                facts.update(variant="nobody", after=not_before)
        else:
            # No sign of sleep: only a moment learned from this house, after it woke (71.5).
            learned = self.prefs.value("covers_open_wake", area_id, "*", "after_wake_minutes")
            if learned is None or world.woke is None:
                return []
            due = world.woke + timedelta(minutes=learned)
            if world.now < due:
                self._recheck[key] = due
                return []
            facts.update(variant="learned", minutes=round(learned))
        self._recheck.pop(key, None)
        learned_sun = self.prefs.value("covers_open_wake", area_id, "*", "sun_elevation")
        if learned_sun is not None and world.sun is not None:
            light = world.sun >= learned_sun
        else:
            light = not world.outside_dark
        if not light:
            return []  # up in the dark: the curtains wait until it is light
        opening = [
            self._req(
                world,
                Layer.PLANNER,
                "covers_open_wake",
                area_id,
                entity,
                "cover.open_cover",
                {},
                **facts,
            )
            for entity in covers
            if (state := states.get(entity.entity_id)) is not None and state.state == "closed"
        ]
        if opening:
            self._mark(key, world)
        elif self._once_today(f"opened-before-wake:{area_id}", world):
            self.prefs.done_before(
                "covers_open_wake",
                area_id,
                [c.entity_id for c in covers],
                "open",
                world.now,
                world.context_key(),
            )
        return opening

    def _climate(
        self,
        world: WorldState,
        states: dict[str, State],
        area_id: str,
        role: str,
        wake: tuple[datetime, str] | None = None,
    ) -> list[ActionRequest]:
        """Calm heating and cooling (E-315, B-183): the house, not one room, decides – leaving a
        room changes nothing; everyone away changes nothing for the first 24 hours; then lower
        (or for cooling: less cold). The room itself reaches the wish."""
        out: list[ActionRequest] = []
        thermostats = self._entities(area_id, "climate")
        if not thermostats:
            return out
        context = world.context_key()
        own = self.room_temps.get(area_id, RoomTemps())
        heat = (
            own.heat.home
            if own.heat is not None
            else self._wish(area_id, role, context, "heat_on_presence", "comfort_c", 19.0)
        )
        cool = (
            own.cool.home
            if own.cool is not None
            else self._wish(area_id, role, context, "cool_on_presence", "cool_c", 24.0)
        )
        long_away = self._long_away(world, own.after_h)
        away = self.params.get("setback_away", {})
        window = self._window_open(world, states, area_id)
        for entity in thermostats:
            state = states.get(entity.entity_id)
            if self.room_climates is not None and entity.entity_id not in self.room_climates:
                continue  # a pool, a wine cooler, a tank: not a room's thermostat
            if state is not None and state.state == "heat_cool" and one_setpoint(state):
                out += self._heat_cool(
                    world, states, area_id, entity, state, long_away, window, own.heat
                )
                continue
            if state is None or state.state not in ("heat", "cool"):
                # Auto, heat-and-cool with two limits, and off stay as people set them:
                # "window open → 12 °C" would mean full cooling on an airco.
                continue
            cooling = state.state == "cool"
            extra: dict[str, Any] = {}
            if cooling:
                if window or (world.mode is HouseMode.AWAY and not long_away):
                    continue  # an open window or a short absence changes nothing for cooling
                skill, wish = "cool_on_presence", cool
                target = cool
                if long_away:
                    skill = "setback_away"
                    target = (
                        own.cool.away
                        if own.cool is not None
                        else min(
                            float(away.get("maximum_c", 28)),
                            cool + float(away.get("setback_c", 3)),
                        )
                    )
                    if own.cool is not None:
                        extra = {"variant": "own"}
                elif own.cool is not None and world.mode is HouseMode.SLEEP:
                    skill, target, extra = "setback_sleep", own.cool.sleep, {"variant": "own"}
            else:
                found = self._heating(world, area_id, role, wake, heat, long_away, window, own.heat)
                if found is None:
                    continue
                skill, target, extra = found
                wish = heat
            if skill == "heat_on_presence":
                target += self.heat_offset(entity.entity_id)
                if self.heat_offset(entity.entity_id) > 0:
                    extra = {"preheat": True}
            wanted, how = target, ""
            if skill in ("heat_on_presence", "cool_on_presence"):
                wanted, how = self._reach_wish(world, states, area_id, entity, state, target, wish)
                if how:
                    extra = {**extra, "variant": how}
            current = state.attributes.get("temperature")
            if isinstance(current, int | float) and abs(float(current) - wanted) < 0.5:
                continue
            out.append(
                self._req(
                    world,
                    Layer.PLANNER,
                    skill,
                    area_id,
                    entity,
                    "climate.set_temperature",
                    {"temperature": round(wanted * 2) / 2},
                    target=wanted,
                    **extra,
                )
            )
        return out

    def _heat_cool(
        self,
        world: WorldState,
        states: dict[str, State],
        area_id: str,
        entity: Entity,
        state: State,
        long_away: bool,
        window: bool,
        own: Temps | None = None,
    ) -> list[ActionRequest]:
        """A heat pump that heats and cools with one setpoint (E-318, B-185): it keeps the
        wish a person set; a day away uses its own "away"; the room reaches the wish in both
        directions. Lowering it while away could mean more cooling in summer, so without an
        "away" of its own it stays as it is. With the room's own temperatures (E-583) Thuis is
        the wish, and Slapen and Weg count only while it heats: the room colder than Thuis."""
        if window:
            return []  # an open window changes nothing here, as for cooling
        presets = [str(p) for p in state.attributes.get("preset_modes") or []]
        preset = state.attributes.get("preset_mode")
        before = self.hc_before(entity.entity_id)
        if own is not None and preset != "away":
            return self._heat_cool_own(world, states, area_id, entity, state, long_away, own)
        if long_away:
            if "away" not in presets or preset == "away":
                return []
            data = {"preset_mode": "away"}
            return [
                self._req(
                    world, Layer.PLANNER, "setback_away", area_id, entity,
                    "climate.set_preset_mode", data, before=str(preset or "none"),
                )
            ]  # fmt: skip
        if world.mode is HouseMode.AWAY:
            return []  # the first 24 hours away: as it is (E-315)
        if preset == "away":
            # Back home: the preset it had before – only if HouseMind chose "away" itself.
            back = before if before in presets else ("none" if "none" in presets else None)
            if before is None or back is None:
                return []
            return [
                self._req(
                    world, Layer.PLANNER, "keep_temperature", area_id, entity,
                    "climate.set_preset_mode", {"preset_mode": back}, variant="home",
                )
            ]  # fmt: skip
        wish = self.hc_wish(entity.entity_id, state)
        if wish is None:
            return []
        wanted, how = self._reach_both(world, states, area_id, entity, state, wish)
        current = state.attributes.get("temperature")
        if isinstance(current, int | float) and abs(float(current) - wanted) < 0.5:
            return []
        facts: dict[str, Any] = {"target": wanted}
        if how:
            facts["variant"] = how
        return [
            self._req(
                world, Layer.PLANNER, "keep_temperature", area_id, entity,
                "climate.set_temperature", {"temperature": round(wanted * 2) / 2}, **facts,
            )
        ]  # fmt: skip

    def _heat_cool_own(
        self,
        world: WorldState,
        states: dict[str, State],
        area_id: str,
        entity: Entity,
        state: State,
        long_away: bool,
        own: Temps,
    ) -> list[ActionRequest]:
        """One setpoint, and the room's own temperatures (E-583): Thuis as the wish, reached in
        the room itself; Slapen and Weg only while it heats, so that in summer they never mean
        more cooling."""
        room = self.room_air(area_id, states)
        if room is None:
            measured = state.attributes.get("current_temperature")
            room = float(measured) if isinstance(measured, int | float) else None
        heating = room is not None and room < own.home
        skill, facts = "keep_temperature", {}
        if long_away and heating:
            skill, wanted, facts = "setback_away", own.away, {"variant": "own"}
        elif world.mode is HouseMode.AWAY and not long_away:
            return []  # the first hours away: as it is (E-315)
        elif world.mode is HouseMode.SLEEP and heating:
            skill, wanted, facts = "setback_sleep", own.sleep, {"variant": "own"}
        else:
            wanted, how = self._reach_both(world, states, area_id, entity, state, own.home)
            if how:
                facts = {"variant": how}
        current = state.attributes.get("temperature")
        if isinstance(current, int | float) and abs(float(current) - wanted) < 0.5:
            return []
        return [
            self._req(
                world, Layer.PLANNER, skill, area_id, entity,
                "climate.set_temperature", {"temperature": round(wanted * 2) / 2},
                target=wanted, **facts,
            )
        ]  # fmt: skip

    def _reach_both(
        self,
        world: WorldState,
        states: dict[str, State],
        area_id: str,
        entity: Entity,
        state: State,
        wish: float,
    ) -> tuple[float, str]:
        """Reaching the wish in the room itself, warmer or cooler (E-318): half a degree at a
        time, at most once an hour, up to 2 degrees from the wish; back towards the wish only
        once the room is a whole degree past it, so it does not go back and forth."""
        room = self.room_air(area_id, states)
        if room is None:
            return wish, ""
        setpoint = state.attributes.get("temperature")
        measured = state.attributes.get("current_temperature")
        key = f"{entity.entity_id}|heat_cool"
        known = self._reach.get(key)
        if known is None:
            # After a restart: what HouseMind set before still stands.
            extra = 0.0
            if isinstance(setpoint, int | float) and 0 < abs(float(setpoint) - wish) <= REACH_MAX:
                extra = float(setpoint) - wish
            known = (extra, world.now - REACH_PACE)
            self._reach[key] = known
        extra, changed = known
        how = ""
        if world.now - changed >= REACH_PACE:
            cold = wish - room  # above 0: colder than the wish
            gap = (  # what the device itself measures, against its own setting
                float(measured) - float(setpoint)
                if isinstance(measured, int | float) and isinstance(setpoint, int | float)
                else None
            )
            up = gap is not None and gap >= -REACH_THERE
            down = gap is not None and gap <= REACH_THERE
            if extra < REACH_MAX and (
                (extra >= 0 and cold >= REACH_GAP and up) or (extra < 0 and cold >= REACH_EASE)
            ):
                extra, how = min(REACH_MAX, extra + REACH_STEP), "warmer"
            elif extra > -REACH_MAX and (
                (extra <= 0 and -cold >= REACH_GAP and down) or (extra > 0 and -cold >= REACH_EASE)
            ):
                extra, how = max(-REACH_MAX, extra - REACH_STEP), "cooler"
            if how:
                self._reach[key] = (extra, world.now)
        return wish + extra, how

    def _wish(
        self, area_id: str, role: str, context: str, skill: str, table: str, default: float
    ) -> float:
        """What the residents want here: what they set themselves, else a usual value for this
        kind of room. Heating and cooling are learned apart (E-315)."""
        learned = self.prefs.value(skill, area_id, context, "temperature")
        if learned is not None:
            return learned
        return float(_by_role(self.params.get(skill, {}).get(table, {}), role, default))

    def climate_basis(
        self, area_id: str, role: str, context: str
    ) -> dict[str, dict[str, tuple[float, str]]]:
        """What a room gets without temperatures of its own (E-583), each with where it comes
        from: "learned" (what the residents set), "start" (a usual value for this kind of room)
        or "start_away" (the start's few degrees from Thuis). On half degrees, as the window
        shows them."""

        def wish(skill: str, table: str, default: float) -> tuple[float, str]:
            learned = self.prefs.value(skill, area_id, context, "temperature")
            if learned is not None:
                return half(learned), "learned"
            found = _by_role(self.params.get(skill, {}).get(table, {}), role, default)
            return half(float(found)), "start"

        heat = wish("heat_on_presence", "comfort_c", 19.0)
        cool = wish("cool_on_presence", "cool_c", 24.0)
        away = self.params.get("setback_away", {})
        step = float(away.get("setback_c", 3))
        sleep = _by_role(self.params.get("setback_sleep", {}).get("sleep_c", {}), role, 17.0)
        heat_away = max(float(away.get("minimum_c", 15)), heat[0] - step)
        cool_away = min(float(away.get("maximum_c", 28)), cool[0] + step)
        return {
            "heat": {
                "home": heat,
                "sleep": (min(half(float(sleep)), heat[0]), "start"),
                "away": (min(half(heat_away), heat[0]), "start_away"),
            },
            "cool": {
                "home": cool,
                "sleep": (cool[0], "start"),  # cooling kept the same at night (E-315)
                "away": (max(half(cool_away), cool[0]), "start_away"),
            },
        }

    def _long_away(self, world: WorldState, after_h: float | None = None) -> bool:
        """Away long enough to save (E-315): on vacation, or nobody home for over 24 hours – or
        as long as a room's own "Pas na" says (E-583)."""
        if world.mode is HouseMode.VACATION:
            return True
        hours = (
            after_h
            if after_h is not None
            else float(self.params.get("setback_away", {}).get("after_hours", 24))
        )
        return (
            world.mode is HouseMode.AWAY
            and world.mode_since is not None
            and world.local - world.mode_since.astimezone(world.local.tzinfo)
            >= timedelta(hours=hours)
        )

    def _heating(
        self,
        world: WorldState,
        area_id: str,
        role: str,
        wake: tuple[datetime, str] | None,
        comfort: float,
        long_away: bool,
        window: bool,
        own: Temps | None = None,
    ) -> tuple[str, float, dict[str, Any]] | None:
        """Which heating skill applies, and its temperature; None: leave it as it is. A room's
        own Slapen and Weg go before the start values (E-583)."""
        if window:
            setback = float(self.params.get("window_open_setback", {}).get("setback_to_c", 12.0))
            return "window_open_setback", setback, {}
        if long_away:
            if own is not None:
                return "setback_away", own.away, {"variant": "own"}
            p = self.params.get("setback_away", {})
            low = max(float(p.get("minimum_c", 15)), comfort - float(p.get("setback_c", 3)))
            return "setback_away", low, {}
        if world.mode is HouseMode.AWAY:
            return None  # the first 24 hours away: as it is – they will be back (E-315)
        warm = self.params.get("wake_warmth", {})
        if (
            wake is not None
            and role in warm.get("in", ())
            and world.mode in (HouseMode.SLEEP, HouseMode.HOME)
            and world.residents_home > 0
        ):
            at, source = wake
            lead = timedelta(minutes=float(warm.get("lead_minutes", 30)))
            hold = timedelta(minutes=float(warm.get("hold_minutes", 30)))
            if at - lead <= world.local < at + hold:
                return (
                    "wake_warmth",
                    comfort,
                    {
                        "planned": True,
                        "at": at.strftime("%H:%M"),
                        "source": source,
                        "until": at.isoformat(),
                    },
                )
        if world.mode is HouseMode.SLEEP:
            if own is not None:
                return "setback_sleep", own.sleep, {"variant": "own"}
            sleep = _by_role(self.params.get("setback_sleep", {}).get("sleep_c", {}), role, 17.0)
            return "setback_sleep", float(sleep), {}
        return "heat_on_presence", comfort, {}

    def _reach_wish(
        self,
        world: WorldState,
        states: dict[str, State],
        area_id: str,
        entity: Entity,
        state: State,
        base: float,
        wish: float,
    ) -> tuple[float, str]:
        """The setpoint that makes the room itself reach the wish (E-315, B-183). Too cold
        while the thermostat already reached its own setting: half a degree more, up to 2.
        Warm enough: it stays (no back and forth). A whole degree too warm: half a degree
        back. Cooling the other way round. Only with a room sensor apart from the thermostat;
        at most one change an hour, and the gate keeps the device whole (E-120)."""
        room = self.room_air(area_id, states)
        if room is None:
            return base, ""
        cooling = state.state == "cool"
        sign = -1.0 if cooling else 1.0
        setpoint = state.attributes.get("temperature")
        measured = state.attributes.get("current_temperature")
        key = f"{entity.entity_id}|{state.state}"  # heating and cooling each their own
        known = self._reach.get(key)
        if known is None:
            # After a restart: what HouseMind set before still stands (no step back and up).
            extra = 0.0
            if isinstance(setpoint, int | float):
                beyond = sign * (float(setpoint) - base)
                if 0 < beyond <= REACH_MAX and sign * (wish - room) > -REACH_EASE:
                    extra = beyond
            known = (extra, world.now - REACH_PACE)
            self._reach[key] = known
        extra, changed = known
        short = sign * (wish - room)  # above 0: not warm (or cool) enough yet
        there = (
            isinstance(measured, int | float)
            and isinstance(setpoint, int | float)
            and sign * (float(measured) - float(setpoint)) >= -REACH_THERE
        )
        how = ""
        if world.now - changed >= REACH_PACE:
            if short >= REACH_GAP and there and extra < REACH_MAX:
                extra, how = min(REACH_MAX, extra + REACH_STEP), "reach"
            elif short <= -REACH_EASE and extra > 0:
                extra, how = max(0.0, extra - REACH_STEP), "ease"
            if how:
                self._reach[key] = (extra, world.now)
        return base + sign * extra, how

    def _window_open(self, world: WorldState, states: dict[str, State], area_id: str) -> bool:
        after = timedelta(
            minutes=float(self.params.get("window_open_setback", {}).get("after_minutes", 3))
        )
        any_open = False
        for entity in self.model.in_area(area_id):
            if entity.domain != "binary_sensor" or entity.device_class not in (
                "window",
                "door",
                "opening",
            ):
                continue
            if self.openings is not None and entity.entity_id not in self.openings:
                continue  # a dishwasher's or fridge's door is no open window
            state = states.get(entity.entity_id)
            if state is not None and state.state == "on":
                since = self._window_open_since.setdefault(entity.entity_id, world.now)
                if world.now - since >= after:
                    any_open = True
            else:
                self._window_open_since.pop(entity.entity_id, None)
        return any_open


def quiet_copy(engine: SkillEngine, learning: Any) -> SkillEngine:
    """A copy of the skills with a learning that keeps nothing (E-295, 20.14): "what would I
    do now" may run as often as it likes – what the real skills remember (who came in, what
    happened already) and what learning counts stay as they are."""
    shadow = copy.copy(learning)
    for item in fields(learning):
        value = getattr(learning, item.name)
        if item.name in ("counts", "values", "levels"):
            setattr(shadow, item.name, dict(value))  # their entries are replaced, never changed
        elif isinstance(value, dict | list | set):
            setattr(shadow, item.name, copy.deepcopy(value))
    twin = copy.copy(engine)
    for item in fields(engine):
        if item.name.startswith("_"):
            setattr(twin, item.name, copy.deepcopy(getattr(engine, item.name)))
    twin.prefs = shadow
    return twin
