"""A heat pump that heats and cools with one setpoint (E-318, B-185): the wish a person set,
kept over a restart, what HouseMind itself changed, and the preset it had before 'away'."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain.skills import SkillEngine, one_setpoint
from housemind.core.model import Source, State, StateChange
from housemind.memory.database import Database
from housemind.parts import Part
from housemind.safety.gate import Decision

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
