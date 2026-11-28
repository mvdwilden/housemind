"""A heat pump that heats and cools with one setpoint (E-318, B-185): the wish a person set,
kept over a restart, what HouseMind itself changed, and the preset it had before 'away'."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain import room_temps
from housemind.brain.room_temps import RoomTemps, after_from, limits, temps_from
from housemind.brain.skills import SkillEngine, one_setpoint
from housemind.core.model import Entity, HouseModel, Source, State, StateChange
from housemind.memory.database import Database
from housemind.parts import Part
from housemind.safety.gate import Decision, SafetyGate

if TYPE_CHECKING:
    from housemind.world.world import World

_LOGGER = logging.getLogger(__name__)

HEAT_COOL_ECHO = timedelta(minutes=15)


class HeatCoolPart(Part):
    """A heat pump that heats and cools with one setpoint (E-318, B-185): the wish a person set,"""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        _hc_sent: dict[str, tuple[str, Any, datetime]]
        _heat_cool: dict[str, dict[str, Any]]
        _heat_cool_dirty: Any
        db: Database
        skills: SkillEngine | None
        model: HouseModel
        world: World | None
        gate: SafetyGate | None

    # -- Temperaturen per ruimte (E-583) ----------------------------------------------------

    async def _load_room_temps(self) -> None:
        assert self.skills is not None
        self.skills.room_temps = room_temps.parse(await self.db.get_setting(room_temps.SETTING))

    def room_thermostats(self, area_id: str) -> list[Entity]:
        """The thermostats that heat or cool this room, not left alone (E-583)."""
        if self.skills is None:
            return []
        rooms = self.skills.room_climates
        excluded = self.gate.state.excluded if self.gate is not None else set()
        out: list[Entity] = []
        for entity in self.model.entities.values():
            area = self.model.area_of(entity.entity_id)
            if (
                entity.domain != "climate"
                or area is None
                or area.id != area_id
                or entity.disabled
                or entity.entity_id in excluded
                or entity.entity_id in self.model.held
                or (rooms is not None and entity.entity_id not in rooms)
            ):
                continue
            out.append(entity)
        return sorted(out, key=lambda e: e.entity_id)

    def room_temps_can(self, area_id: str) -> dict[str, Any]:
        """What the room's thermostats can: heat, cool, one setpoint for both, and how far the
        sliders go for each (E-583)."""
        states = self.world.states if self.world is not None else {}
        modes: set[str] = set()
        lows: list[float] = []
        highs: list[float] = []
        one = True
        found = False
        for entity in self.room_thermostats(area_id):
            state = states.get(entity.entity_id)
            if state is None:
                continue
            found = True
            modes.add(state.state)  # what it does now, also where the list is missing
            modes.update(str(m) for m in state.attributes.get("hvac_modes") or [])
            low, high = state.attributes.get("min_temp"), state.attributes.get("max_temp")
            if isinstance(low, int | float):
                lows.append(float(low))
            if isinstance(high, int | float):
                highs.append(float(high))
            one = one and state.state == "heat_cool" and one_setpoint(state)
        heat = bool(modes & {"heat", "heat_cool", "auto"})
        cool = bool(modes & {"cool", "heat_cool"}) and not one
        return {
            "found": found,
            "heat": heat or one,
            "cool": cool,
            "one": found and one,
            "limits": {mode: limits(lows, highs, mode) for mode in room_temps.MODES},
        }

    async def room_temps_save(self, area_id: str, raw: Any) -> bool:
        """Bewaren in the Temperaturen window (E-583): the room's Thuis, Slapen and Weg for
        what its thermostats can, and "Pas na". None: back to what HouseMind learns."""
        assert self.skills is not None
        if area_id not in self.model.areas:
            return False
        rooms = dict(self.skills.room_temps)
        if raw is None:
            if rooms.pop(area_id, None) is None:
                return True
        else:
            can = self.room_temps_can(area_id)
            if not isinstance(raw, dict) or not can["found"]:
                return False
            got: dict[str, Any] = {}
            for mode in room_temps.MODES:
                if not can[mode]:
                    continue
                temps = temps_from(raw.get(mode), mode, *can["limits"][mode])
                if temps is None:
                    return False
                got[mode] = temps
            after = after_from(raw.get("after_h"))
            if after is None or not got:
                return False
            rooms[area_id] = RoomTemps(got.get("heat"), got.get("cool"), after)
        self.skills.room_temps = rooms
        await self.db.set_setting(room_temps.SETTING, room_temps.dump(rooms))
        return True

    async def _load_heat_cool(self) -> None:
        raw = await self.db.get_setting("heat_cool")
        kept = raw if isinstance(raw, dict) else {}
        self._heat_cool = {
            "wish": {str(k): float(v) for k, v in (kept.get("wish") or {}).items()},
            "away": {str(k): str(v) for k, v in (kept.get("away") or {}).items()},
        }
        assert self.skills is not None
        self.skills.hc_wish = self._hc_wish
        self.skills.hc_before = lambda entity_id: self._heat_cool["away"].get(entity_id)

    async def _save_heat_cool(self) -> None:
        self._heat_cool_dirty = False
        await self.db.set_setting("heat_cool", self._heat_cool)

    def _hc_wish(self, entity_id: str, state: State) -> float | None:
        """The temperature a person set on it; the first time, what it is set to now."""
        wish = self._heat_cool["wish"].get(entity_id)
        if wish is None:
            target = state.attributes.get("temperature")
            if not isinstance(target, int | float):
                return None
            wish = self._heat_cool["wish"][entity_id] = float(target)
            self._heat_cool_dirty = True
        return float(wish)

    def _hc_echo(self, entity_id: str, what: str, value: Any, now: datetime) -> bool:
        """HouseMind's own change coming back (a cloud integration reports it later, without
        HouseMind's mark): it is no person's wish."""
        sent = self._hc_sent.get(entity_id)
        if sent is None or sent[0] != what or now - sent[2] > HEAT_COOL_ECHO:
            return False
        if isinstance(value, int | float) and isinstance(sent[1], int | float):
            return abs(float(value) - float(sent[1])) < 0.05
        return bool(value == sent[1])

    async def _heat_cool_change(self, change: StateChange, now: datetime) -> None:
        """What a person sets on a heat pump that heats and cools is the wish (E-318); a person
        choosing its preset takes over from HouseMind's "away"."""
        new, old = change.new, change.old
        if new is None or change.source not in (Source.PERSON, Source.PHYSICAL):
            return
        if new.state != "heat_cool" or not one_setpoint(new):
            return
        eid, changed = change.entity_id, False
        target = new.attributes.get("temperature")
        before = old.attributes.get("temperature") if old is not None else None
        if (
            isinstance(target, int | float)
            and target != before
            and not self._hc_echo(eid, "temperature", target, now)
        ):
            self._heat_cool["wish"][eid] = float(target)
            changed = True
        preset = new.attributes.get("preset_mode")
        was = old.attributes.get("preset_mode") if old is not None else None
        if (
            preset != was
            and eid in self._heat_cool["away"]
            and not self._hc_echo(eid, "preset_mode", preset, now)
        ):
            del self._heat_cool["away"][eid]  # a person chose: HouseMind does not put it back
            changed = True
        if changed:
            await self._save_heat_cool()

    async def _heat_cool_done(self, decision: Decision) -> None:
        """HouseMind changed a thermostat: remember it, so its echo is no person's wish; and
        the preset before its "away", to put back when someone is home (E-318)."""
        r = decision.request
        for what in ("temperature", "preset_mode"):
            if what in r.data:
                self._hc_sent[r.entity_id] = (what, r.data[what], decision.at)
        if r.action != "climate.set_preset_mode":
            return
        if r.data.get("preset_mode") == "away" and r.skill == "setback_away":
            self._heat_cool["away"][r.entity_id] = str(r.facts.get("before") or "none")
        elif r.skill == "keep_temperature":
            self._heat_cool["away"].pop(r.entity_id, None)
        else:
            return
        await self._save_heat_cool()
