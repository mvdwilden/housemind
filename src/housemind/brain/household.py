"""Around the house: appliances, the fridge, the oven, media, cleaning and the garden.

Always on, because it is about safety or spoilage:
- a cooking appliance that is on while nobody is home (E-61) – reported; HouseMind never
  switches one on, and only switches it off after a person's yes, when a switch really cuts
  its power (B-327);
- a fridge or freezer door that stays open.
Experimental and off until an administrator turns the domain on (E-60); then each skill still
learns in steps (observe → propose → act):
- household: "the washing machine is ready";
- media: switching off a TV nobody watches – only a TV, only ever a proposal (E-63);
- cleaning: the robot vacuum while nobody is home;
- garden: the robot mower on a dry day while nobody is outside – a proposal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from housemind.core.model import Entity, HouseModel, State
from housemind.knowledge.words import has_word as words_have
from housemind.safety.gate import ActionRequest, Layer
from housemind.world.house_mode import HouseMode
from housemind.world.world import WorldState

EXPERIMENTAL = ("household", "media", "cleaning", "garden", "care")
# What an appliance's own state sensor says while it works (e.g. "run" for an oven program).
RUN_STATES = {"run", "running", "delayedstart", "heating", "preheating", "cooking", "washing"}


def _has_word(text: str, words: list[str]) -> bool:
    return words_have(text, words)  # one rule for every language (code QA 3, punt 4)


def _label(model: HouseModel, entity: Entity) -> str:
    return model.words_of(entity)


def _name(model: HouseModel, entity: Entity) -> str:
    return model.device_name(entity.entity_id)


def is_tv(model: HouseModel, entity: Entity, words: list[str]) -> bool:
    """E-63: a TV – what Home Assistant calls one, or a media player without a type with a TV
    word in its name. A speaker, an amplifier, a camera or a doorbell is a media player too,
    but nobody "watches" it."""
    if entity.domain != "media_player":
        return False
    if entity.device_class:
        return entity.device_class == "tv"
    return _has_word(_label(model, entity), words)


@dataclass(frozen=True, slots=True)
class Notice:
    topic: str
    key: str  # notify.house.<key>
    device: str
    important: bool = True
    switch: str | None = None  # what switches it off after a yes (E-61, B-327)


@dataclass(slots=True)
class Household:
    model: HouseModel
    words: dict[str, list[str]]
    params: dict[str, dict[str, Any]]
    _running_since: dict[str, datetime] = field(default_factory=dict)
    # Which entities matter, worked out once (a big house has thousands; E-157).
    _cooking: list[Entity] = field(default_factory=list)
    _fridges: list[Entity] = field(default_factory=list)
    _appliances: list[Entity] = field(default_factory=list)
    _machines: list[Entity] = field(default_factory=list)
    _off: dict[str, str] = field(default_factory=dict)  # cooking appliance -> its power switch

    def __post_init__(self) -> None:
        self.index()

    def index(self) -> None:
        """Work out which entities matter (again after the house changed in HA)."""
        for found in (self._cooking, self._fridges, self._appliances, self._machines):
            found.clear()
        for entity in self.model.entities.values():
            label = _label(self.model, entity)
            if _has_word(label, self.words.get("cooking", [])):
                self._cooking.append(entity)
            if entity.domain == "binary_sensor" and _has_word(label, self.words.get("fridge", [])):
                self._fridges.append(entity)
            if _has_word(label, self.words.get("appliance", [])):
                self._appliances.append(entity)
            if entity.domain in ("vacuum", "lawn_mower") or self.is_tv(entity):
                self._machines.append(entity)
        self._off = self._power_switches()

    def _power_switches(self) -> dict[str, str]:
        """E-61, B-327: the switch that cuts a cooking appliance's power – a smart plug (the one
        switch of a device that measures power), or a switch named for power. Never a child
        lock, a light or a setting: unsure means no switch, and only the notice."""
        power_words = self.words.get("power_switch", [])
        never = self.words.get("not_power_switch", [])
        by_device: dict[str, list[Entity]] = {}
        for entity in self.model.entities.values():
            if entity.device_id:
                by_device.setdefault(entity.device_id, []).append(entity)
        found: dict[str, str] = {}
        for cooking in self._cooking:
            key = cooking.device_id or cooking.entity_id
            if key in found:
                continue
            family = by_device.get(cooking.device_id or "", [cooking])
            switches = [
                e
                for e in family
                if e.domain == "switch"
                and not (e.hidden or e.disabled or e.entity_category)
                and not _has_word(f"{e.entity_id} {e.name}", never)
            ]
            named = [s for s in switches if _has_word(f"{s.entity_id} {s.name}", power_words)]
            measures = any(e.domain == "sensor" and e.device_class == "power" for e in family)
            all_switches = [e for e in family if e.domain == "switch"]
            if len(named) == 1:
                found[key] = named[0].entity_id
            elif measures and len(switches) == 1 and len(all_switches) == 1:
                found[key] = switches[0].entity_id  # a smart plug
        return found

    def off_switch(self, topic: str) -> str | None:
        """The power switch for a "house:cooking:…" notice, if there is one."""
        return self._off.get(topic.removeprefix("house:cooking:"))

    def off_switches(self) -> list[str]:
        return sorted(set(self._off.values()))

    def is_tv(self, entity: Entity) -> bool:
        return is_tv(self.model, entity, self.words.get("tv", []))

    def tvs(self, area_id: str) -> list[Entity]:
        """The TVs in a room: the only media players "TV off when nobody watches" is about."""
        return [
            e
            for e in self._machines
            if e.domain == "media_player"
            and (area := self.model.area_of(e.entity_id)) is not None
            and area.id == area_id
        ]

    def applies(self, skill: str, area_id: str) -> bool:
        """Whether a skill is about anything in this room. A room where HouseMind once saw a
        doorbell or speaker as a TV keeps no line for it on the card (E-63)."""
        return skill != "media_off_empty" or bool(self.tvs(area_id))

    _done_today: dict[str, str] = field(default_factory=dict)
    _away_since: datetime | None = None

    # -- always on: safety and spoilage -------------------------------------------------

    def cooking_on_while_away(self, world: WorldState, states: dict[str, State]) -> list[Notice]:
        """E-61: a cooking appliance on while nobody is home – reported, never switched on;
        with a power switch, the notice asks whether to switch it off (B-327)."""
        if world.mode not in (HouseMode.AWAY, HouseMode.VACATION):
            self._away_since = None
            return []
        self._away_since = self._away_since or world.now
        if world.now - self._away_since < timedelta(minutes=10):
            return []
        found = []
        for entity in self._cooking:
            if self._running(entity, states.get(entity.entity_id), watts=50):
                key = entity.device_id or entity.entity_id
                switch = self._off.get(key)
                if switch is not None and getattr(states.get(switch), "state", None) != "on":
                    switch = None  # already off, or unknown: nothing to switch
                found.append(
                    Notice(
                        f"house:cooking:{key}",
                        "cooking_away_off" if switch else "cooking_away",
                        _name(self.model, entity),
                        switch=switch,
                    )
                )
        return list({n.topic: n for n in found}.values())

    def fridge_open(self, world: WorldState, states: dict[str, State]) -> list[Notice]:
        found = []
        minutes = float(self.params.get("fridge_door", {}).get("after_minutes", 3))
        for entity in self._fridges:
            if entity.device_class not in ("door", "opening", None):
                continue
            state = states.get(entity.entity_id)
            if (
                state is not None
                and state.state == "on"
                and world.now - state.last_changed >= timedelta(minutes=minutes)
            ):
                found.append(
                    Notice(
                        f"house:fridge:{entity.entity_id}", "fridge_open", _name(self.model, entity)
                    )
                )
        return found

    # -- experimental -------------------------------------------------------------------

    def appliances_done(self, world: WorldState, states: dict[str, State]) -> list[Notice]:
        """A washing machine, dryer or dishwasher that ran for a while and stopped."""
        found = []
        for entity in self._appliances:
            running = self._running(entity, states.get(entity.entity_id))
            if running is None:
                continue
            key = entity.entity_id
            if running:
                self._running_since.setdefault(key, world.now)
                continue
            since = self._running_since.pop(key, None)
            if since is not None and world.now - since >= timedelta(minutes=10):
                found.append(
                    Notice(
                        f"house:done:{key}:{world.now.isoformat()[:16]}",
                        "appliance_done",
                        _name(self.model, entity),
                        important=True,
                    )
                )
        return found

    @staticmethod
    def _running(entity: Entity, state: State | None, watts: float = 10) -> bool | None:
        """Is the appliance working? Only from what really says so: a running or power
        sensor, its power use, or its program state – never from "remote control: on" or
        "child lock: on"."""
        if state is None or not state.available:
            return None
        if entity.domain == "binary_sensor" and entity.device_class in ("running", "power", "heat"):
            return state.state == "on"
        if entity.domain == "sensor" and entity.device_class == "power":
            try:
                return float(state.state) > watts
            except ValueError:
                return None
        if entity.domain == "sensor" and entity.device_class in ("enum", None):
            value = state.state.lower()
            if value in RUN_STATES:
                return True
            if value in ("ready", "finished", "inactive", "off", "idle", "standby"):
                return False
        return None

    def requests(self, world: WorldState, states: dict[str, State]) -> list[ActionRequest]:
        out: list[ActionRequest] = []
        for entity in self._machines:
            state = states.get(entity.entity_id)
            if state is None or not state.available:
                continue
            area = self.model.area_of(entity.entity_id)
            area_id = area.id if area else None
            if entity.domain == "media_player":
                out += self._media(world, entity, state, area_id)
            elif entity.domain == "vacuum":
                out += self._vacuum(world, entity, state, area_id)
            elif entity.domain == "lawn_mower":
                out += self._mower(world, entity, state, area_id, states)
        return out

    def _req(
        self,
        world: WorldState,
        entity: Entity,
        action: str,
        skill: str,
        area_id: str | None,
        **facts: Any,
    ) -> ActionRequest:
        return ActionRequest(
            Layer.PLANNER,
            entity.entity_id,
            action,
            {},
            reason=f"skill:{skill}",
            skill=skill,
            area_id=area_id,
            created_at=world.now,
            facts={"context": world.context_key(), **facts},
        )

    def _media(
        self, world: WorldState, entity: Entity, state: State, area_id: str | None
    ) -> list[ActionRequest]:
        """E-63: a TV nobody watches – switching it off is only ever a proposal."""
        if state.state not in ("on", "playing", "paused", "idle") or area_id is None:
            return []
        info = world.occupancy.get(area_id)
        if info is None or info.occupied or info.last_activity is None:
            return []
        minutes = float(self.params.get("media_off_empty", {}).get("after_minutes", 30))
        if world.now - info.last_activity < timedelta(minutes=minutes):
            return []
        return [self._req(world, entity, "media_player.turn_off", "media_off_empty", area_id)]

    def _once(self, key: str, world: WorldState) -> bool:
        """Not yet done today (marked by ``done`` once it really happened)."""
        return self._done_today.get(key) != world.local.date().isoformat()

    def done(self, entity_id: str, world: WorldState) -> None:
        for prefix in ("vacuum", "mower"):
            self._done_today[f"{prefix}:{entity_id}"] = world.local.date().isoformat()

    def _vacuum(
        self, world: WorldState, entity: Entity, state: State, area_id: str | None
    ) -> list[ActionRequest]:
        params = self.params.get("vacuum_when_away", {})
        start, end = (int(x) for x in params.get("hours", [9, 18]))
        if (
            state.state != "docked"
            or world.mode is not HouseMode.AWAY
            or not start <= world.local.hour < end
            or self._away_since is None
            or world.now - self._away_since < timedelta(minutes=10)
            or not self._once(f"vacuum:{entity.entity_id}", world)
        ):
            return []
        return [self._req(world, entity, "vacuum.start", "vacuum_when_away", area_id)]

    def _mower(
        self,
        world: WorldState,
        entity: Entity,
        state: State,
        area_id: str | None,
        states: dict[str, State],
    ) -> list[ActionRequest]:
        params = self.params.get("mower_when_dry", {})
        start, end = (int(x) for x in params.get("hours", [10, 18]))
        if state.state != "docked" or not start <= world.local.hour < end:
            return []
        if area_id is not None and world.occupied(area_id):
            return []  # somebody is outside
        weather = next((s for e, s in sorted(states.items()) if e.startswith("weather.")), None)
        if weather is None or weather.state in (
            "rainy",
            "pouring",
            "lightning-rainy",
            "snowy",
            "snowy-rainy",
            "hail",
        ):
            return []
        if not self._once(f"mower:{entity.entity_id}", world):
            return []
        return [self._req(world, entity, "lawn_mower.start_mowing", "mower_when_dry", area_id)]
