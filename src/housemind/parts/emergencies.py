"""Emergencies (E-80, E-212): which one is the worst now, what HouseMind really did in words,
an emergency sensor already on at the start, a sensor lost during its alarm, and a socket
or dimmer that gets too hot (E-210)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from housemind.brain import alarm_reaction
from housemind.brain.notify import (
    Kind,
)
from housemind.core.clock import Clock
from housemind.core.model import HouseModel, Source, StateChange
from housemind.parts import Part
from housemind.safety.emergency import ALARMS, SEVERITY, Emergency, EmergencyReactions
from housemind.safety.gate import Decision, Outcome, SafetyGate
from housemind.world.world import World, WorldState

if TYPE_CHECKING:
    from housemind.runtime import Status

_LOGGER = logging.getLogger(__name__)

# A socket, relay or dimmer that gets this hot can be a fire risk (E-210): a message, and
# above the second one also at night. Below COOL it is over.
DEVICE_COOL_C = 60.0
DEVICE_HOT_C = 70.0
DEVICE_VERY_HOT_C = 85.0
# An emergency sensor that stops responding during its alarm keeps the house careful this
# long, then HouseMind says so and carries on: the house never stays frozen.
EMERGENCY_LOST_AFTER = timedelta(hours=2)


class EmergenciesPart(Part):
    """Emergencies (E-80, E-212): which one is the worst now, what HouseMind really did in words,"""

    if TYPE_CHECKING:  # what this part uses of HouseMind (it is mixed into HouseMind)
        _active: dict[str, Emergency]
        _lost_since: dict[str, datetime]

        async def _on_change(self, change: StateChange) -> None: ...
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

        async def _resolve(self, topic: str) -> None: ...
        def _test_halt(self) -> None: ...

        clock: Clock
        emergencies: EmergencyReactions | None
        gate: SafetyGate | None
        model: HouseModel
        status: Status
        world: World | None

    async def _device_heat(self, entity_id: str) -> None:
        """A socket, relay or dimmer that gets too hot (E-210): a message, never an action."""
        assert self.world is not None
        state = self.world.states.get(entity_id)
        try:
            value = float(state.state) if state is not None else None
        except ValueError:
            value = None
        topic = f"device_hot:{entity_id}"
        if value is None or value < DEVICE_COOL_C:
            await self._resolve(topic)
            return
        if value < DEVICE_HOT_C and topic not in self._raised:
            return
        entity = self.model.entities.get(entity_id)
        device = self.model.devices.get(entity.device_id or "") if entity else None
        name = device.name if device else (entity.name if entity else entity_id)
        very = value >= DEVICE_VERY_HOT_C
        kind, key = (
            (Kind.EMERGENCY, "notify.device_very_hot")
            if very
            else (Kind.IMPORTANT, "notify.device_hot")
        )
        if topic in self._raised and self._raised[topic][0] is not kind:
            await self._resolve(topic)  # hotter (or cooling down): the right kind of message
        await self._raise(
            kind,
            topic,
            key,
            {"name": name, "temperature": round(value)},
            self.world.snapshot(self.clock.now()),
            concerns="admins",
        )

    def _set_emergency(self) -> None:
        """The most severe emergency that is on now; the gate stops everything else during
        smoke, CO and gas – also a boiler boost or charging."""
        assert self.gate is not None
        worst = next((e for e in SEVERITY if e in self._active.values()), None)
        self.status.emergency = worst
        self.status.emergency_sensor = next(
            (sensor for sensor, e in self._active.items() if e is worst), None
        )
        self.gate.state.alarm = worst in ALARMS
        if worst is not None:
            self._test_halt()  # an emergency stops every test at once (E-480)

    def _done_text(self, emergency: Emergency, executed: list[Decision]) -> dict[str, Any]:
        """What was done, kept for later and written in the reader's language."""
        done = {
            d.request.action.split(".", 1)[0] for d in executed if d.outcome is Outcome.EXECUTED
        }
        parts: list[dict[str, Any]] = []
        lamps = sum(  # the lamps themselves: a group counts its lamps (E-458)
            len(self.model.groups.get(d.request.entity_id, ())) or 1
            for d in executed
            if d.outcome is Outcome.EXECUTED and d.request.action == "light.turn_on"
        )
        if emergency in (Emergency.SMOKE, Emergency.CARBON_MONOXIDE) and lamps:
            # What really happened, counted (QA 2, M-15): one lamp is not "the light is on".
            parts.append({"t": "notify.done.lights", "p": {"count": lamps}})
        actions = {d.request.action for d in executed if d.outcome is Outcome.EXECUTED}
        if emergency in (Emergency.SMOKE, Emergency.CARBON_MONOXIDE):
            if "cover.open_cover" in actions:
                parts.append({"t": "notify.done.fire_covers"})  # to get out (E-546)
            if "climate.set_hvac_mode" in actions:
                parts.append({"t": "notify.done.air_off"})  # no smoke blown around (E-546)
            locked = alarm_reaction.locked_doors(self._hm)
            if locked:
                # Never opened: said, so nobody runs into a locked door (E-546).
                parts.append({"t": "notify.done.locked", "p": {"names": ", ".join(locked)}})
        if emergency is Emergency.GAS:
            key = "notify.done.gas_valve" if "valve" in done else "notify.todo.gas_valve"
            parts.append({"t": key})
        if emergency is Emergency.LEAK:
            key = "notify.done.water_valve" if "valve" in done else "notify.todo.water_valve"
            parts.append({"t": key})
        if emergency is Emergency.FROST and "climate" in done:
            parts.append({"t": "notify.done.frost_heat"})
        return {"join": parts, "sep": ""}

    async def _emergencies_now(self) -> None:
        """An emergency sensor that is already on when HouseMind starts, or when Home
        Assistant comes back, gets its reaction too, such as a leak that began during a
        restart."""
        assert self.world is not None and self.emergencies is not None
        for entity_id, state in list(self.world.states.items()):
            if state.state != "on" or entity_id in self._active:
                continue
            if f"emergency:{entity_id}" in self._raised:
                continue
            if self.emergencies.emergency_for(entity_id) is None:
                continue
            await self._on_change(StateChange(entity_id, None, state, Source.PHYSICAL))

    async def _emergencies_lost(self, snapshot: WorldState) -> None:
        """A sensor that stopped responding during its alarm: careful for two hours, then
        a word and back to normal."""
        now = self.clock.now()
        for sensor, since in list(self._lost_since.items()):
            if now - since < EMERGENCY_LOST_AFTER or sensor not in self._active:
                continue
            del self._active[sensor]
            del self._lost_since[sensor]
            await self._resolve(f"emergency:{sensor}")
            await alarm_reaction.over(self._hm, f"emergency:{sensor}")
            self._set_emergency()
            entity = self.model.entities.get(sensor)
            await self._raise(
                Kind.IMPORTANT,
                f"emergency_lost:{sensor}",
                "notify.emergency_sensor_lost",
                {"name": entity.name if entity else sensor},
                snapshot,
                concerns="admins",
            )
