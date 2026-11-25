"""Emergency reactions (E-80): fixed rules, active from minute one, highest priority.

They only *ask*: every reaction is an action request that still passes the safety gate
(hard limits stay – no lock ever opens, B-21). People-win, pause and switching limits do
not block an emergency.

- smoke / carbon monoxide: all lights fully on (to see the way out), ventilation off (smoke)
- gas: close gas valves; do **not** switch lights or fans (no sparks)
- leak: close water valves
- frost: a message and the heating in that room to at least 8 °C – frost is no emergency
  that stops the house (a freezer's "cold" sensor would freeze every skill)

Only what really is the danger counts, as for the room temperature: a rain, soil or plant
sensor is no leak, a fridge or freezer is no frost, and a sensor HA marks as a setting or
diagnostic, or hides, is no emergency sensor.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from housemind.brain.levels import LightLevels, white
from housemind.core.clock import Clock
from housemind.core.model import Entity, HouseModel, State, StateChange
from housemind.knowledge.catalog import Catalog
from housemind.knowledge.matcher import recognise
from housemind.knowledge.words import has_word as words_have
from housemind.safety.gate import ActionRequest, Layer


class Emergency(StrEnum):
    SMOKE = "smoke"
    CARBON_MONOXIDE = "carbon_monoxide"
    GAS = "gas"
    LEAK = "leak"
    FROST = "frost"


_SKILL_TO_EMERGENCY = {
    "emergency_smoke": Emergency.SMOKE,
    "emergency_co": Emergency.CARBON_MONOXIDE,
    "emergency_gas": Emergency.GAS,
    "emergency_leak": Emergency.LEAK,
    "emergency_frost": Emergency.FROST,
}


# In code, whatever a knowledge file says (QA 2, B-168): Home Assistant's own classes for these
# sensors always mean an emergency.
_CLASS_TO_EMERGENCY = {
    "smoke": Emergency.SMOKE,
    "carbon_monoxide": Emergency.CARBON_MONOXIDE,
    "gas": Emergency.GAS,
    "moisture": Emergency.LEAK,
}

# The three that are an alarm: everything else stops while they last (E-80).
ALARMS = frozenset({Emergency.SMOKE, Emergency.CARBON_MONOXIDE, Emergency.GAS})
SEVERITY = (Emergency.SMOKE, Emergency.GAS, Emergency.CARBON_MONOXIDE, Emergency.LEAK)
FROST_MIN_C = 8.0
# Measurements that show a moisture sensor is about rain, soil or a plant, not a leak.
NOT_A_LEAK_CLASSES = frozenset(
    {"moisture", "conductivity", "precipitation", "precipitation_intensity", "wind_speed"}
)


def _has_word(text: str, words: Iterable[str]) -> bool:
    return words_have(text, words)  # one rule for every language (code QA 3, punt 4)


@dataclass(frozen=True, slots=True)
class EmergencyEvent:
    emergency: Emergency
    sensor_entity_id: str
    area_id: str | None
    requests: tuple[ActionRequest, ...] = field(default_factory=tuple)
    # Heard or measured outside (a camera on the terrace hearing a smoke alarm, a moisture
    # sensor in the garden): a message, no reaction in the house.
    outside: bool = False


class EmergencyReactions:
    def __init__(
        self,
        clock: Clock,
        catalog: Catalog,
        model: HouseModel,
        area_roles: dict[str, str] | None = None,
    ) -> None:
        self._clock = clock
        self.catalog = catalog
        self.model = model
        self.area_roles = area_roles if area_roles is not None else {}
        self.not_leak: list[str] = []  # rain, soil, plant … (every language file)
        self.fridge: list[str] = []  # fridge, freezer … (every language file)
        # A camera's own "smoke alarm heard" or "CO alarm heard" sensor without HA's smoke or
        # CO class (brain/cameras.py, 16.2): an emergency sensor like any other.
        self.heard: dict[str, Emergency] = {}
        self.stands: LightLevels | None = None  # white where a lamp can show it (E-495)

    def emergency_for(self, entity_id: str) -> Emergency | None:
        entity = self.model.entities.get(entity_id)
        if entity is None or entity.hidden or entity.disabled or entity.entity_category:
            return None
        if entity_id in self.heard:
            return self.heard[entity_id]
        kind = recognise(self.catalog, entity).kind
        found = next(
            (
                _SKILL_TO_EMERGENCY[s]
                for s in (kind.skills if kind else ())
                if s in _SKILL_TO_EMERGENCY
            ),
            None,
        )
        if found is None and entity.domain == "binary_sensor":
            found = _CLASS_TO_EMERGENCY.get(entity.device_class or "")
        if found is None:
            return None
        if found is Emergency.LEAK and self._rain_or_plant(entity):
            return None
        if found is Emergency.FROST and _has_word(self._label(entity), self.fridge):
            return None  # a freezer that is cold is a freezer
        return found

    def _label(self, entity: Entity) -> str:
        return self.model.words_of(entity)

    def _rain_or_plant(self, entity: Entity) -> bool:
        if _has_word(self._label(entity), self.not_leak):
            return True
        return entity.device_id is not None and any(
            other.domain == "sensor" and other.device_class in NOT_A_LEAK_CLASSES
            for other in self.model.on_device(entity.device_id)
        )

    def react(
        self, change: StateChange, states: Mapping[str, State] | None = None
    ) -> EmergencyEvent | None:
        """Return the reactions for a change, or ``None`` if it is not a new emergency."""
        emergency = self.emergency_for(change.entity_id)
        if emergency is None or change.new is None or change.new.state != "on":
            return None
        if change.old is not None and change.old.state == "on":
            return None  # already known
        area = self.model.area_of(change.entity_id)
        if area is not None and self.area_roles.get(area.id) == "outside":
            if emergency in (Emergency.FROST, Emergency.LEAK):
                return None  # frost or water outside is the weather, not an emergency
            return EmergencyEvent(emergency, change.entity_id, area.id, (), outside=True)
        return EmergencyEvent(
            emergency,
            change.entity_id,
            area.id if area else None,
            tuple(self._requests(emergency, change.entity_id, area.id if area else None, states)),
        )

    def requests_for(
        self,
        emergency: Emergency,
        sensor: str,
        area_id: str | None,
        states: Mapping[str, State] | None = None,
    ) -> list[ActionRequest]:
        """The reactions to an emergency that a camera heard (an event, no sensor that stays
        on): the same lights and ventilation as for a sensor."""
        return self._requests(emergency, sensor, area_id, states)

    def _requests(
        self,
        emergency: Emergency,
        sensor: str,
        area_id: str | None = None,
        states: Mapping[str, State] | None = None,
    ) -> list[ActionRequest]:
        now = self._clock.now()
        reason = f"emergency:{emergency.value}:{sensor}"
        requests: list[ActionRequest] = []
        if emergency is Emergency.FROST:
            return self._frost(reason, area_id, states or {})
        lit: set[str] = set()
        for entity_id, entity in sorted(self.model.entities.items()):
            if entity.hidden or entity.entity_category or entity_id in self.model.groups:
                continue  # an indicator LED or a group is not a lamp to see the way out by
            kind = recognise(self.catalog, entity).kind
            if kind is None:
                continue
            if emergency in (Emergency.SMOKE, Emergency.CARBON_MONOXIDE):
                # A relay shown as a lamp ("switch as x") may power anything – a heater, a
                # door opener – so it stays as it is (QA 2, M-3).
                if entity.domain == "light" and entity.platform != "switch_as_x":
                    # Through the group that steers it, once – one radio message for a room –
                    # and nothing left alone (E-458); a group with a relay in it is no group.
                    unit = self.model.unit(entity_id)
                    if unit is not None and unit != entity_id:
                        members = self.model.groups.get(unit, ())
                        if any(
                            (m := self.model.entities.get(e)) is not None
                            and m.platform == "switch_as_x"
                            for e in members
                        ):
                            unit = None if entity_id in self.model.left_alone else entity_id
                    if unit is None or unit in lit:
                        continue
                    lit.add(unit)
                    entity_id = unit
                    turn_on = kind.allowed("light.turn_on")  # full and white (E-495)
                    data = white(entity_id, self.stands, turn_on.values) if turn_on else {}
                    requests.append(
                        ActionRequest(
                            Layer.EMERGENCY,
                            entity_id,
                            "light.turn_on",
                            data,
                            reason,
                            created_at=now,
                        )
                    )
                if emergency is Emergency.SMOKE and kind.key == "fan.ventilation":
                    requests.append(
                        ActionRequest(
                            Layer.EMERGENCY, entity_id, "fan.turn_off", {}, reason, created_at=now
                        )
                    )
            elif (emergency is Emergency.GAS and kind.key == "valve.gas") or (
                emergency is Emergency.LEAK and kind.key == "valve.water"
            ):
                requests.append(
                    ActionRequest(
                        Layer.EMERGENCY, entity_id, "valve.close_valve", {}, reason, created_at=now
                    )
                )
        return requests

    def _frost(
        self, reason: str, area_id: str | None, states: Mapping[str, State]
    ) -> list[ActionRequest]:
        """At least 8 °C on the heating in that room (or the house, if the room is unknown):
        never lower than it is. A thermostat that is off goes to heating first, when it can
        heat – frost does not wait for someone to switch it on (E-212, QA 2, L4)."""
        now = self._clock.now()
        out = []
        for entity_id, entity in sorted(self.model.entities.items()):
            if entity.domain != "climate" or entity_id in self.model.groups:
                continue
            area = self.model.area_of(entity_id)
            if area_id is not None and (area is None or area.id != area_id):
                continue
            state = states.get(entity_id)
            target = state.attributes.get("temperature") if state is not None else None
            if state is None or state.state in ("unavailable", "unknown"):
                continue
            if state.state == "off":
                if "heat" not in (state.attributes.get("hvac_modes") or ()):
                    continue  # it cannot heat (an air conditioner that only cools)
                out.append(
                    ActionRequest(
                        Layer.EMERGENCY,
                        entity_id,
                        "climate.set_hvac_mode",
                        {"hvac_mode": "heat"},
                        reason,
                        created_at=now,
                    )
                )
                target = None  # its own number counts no more: it was off
            if isinstance(target, int | float) and target >= FROST_MIN_C:
                continue
            out.append(
                ActionRequest(
                    Layer.EMERGENCY,
                    entity_id,
                    "climate.set_temperature",
                    {"temperature": FROST_MIN_C},
                    reason,
                    created_at=now,
                )
            )
        return out
