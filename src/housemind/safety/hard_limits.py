"""Hard limits (E-31, B-26). Deliberately in code: no file, update or setting can loosen them.

HouseMind never, by itself:
- switches an alarm off,
- opens a lock,
- opens a garage door, gate or motorised door – also not when it hides behind a switch or
  button with that role (QA H1/H2),
- opens a water or gas valve: a valve only ever closes (QA 2, B-168),
- makes, runs, changes or deletes automations, scripts, scenes, blueprints or helpers (E-03).
  The only thing it may do with an existing automation is switch it off after a person's yes
  and on again (E-42, E-112) – the gate adds that it never does so by itself.

Administrators may *add* entities to the list (``protected``); HouseMind then never acts on
them at all. Nothing can remove an item.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import Any

from housemind.core.model import Entity, HouseModel
from housemind.knowledge.words import has_word as words_have

# What opens a garage door, gate or door: one list, which the alarm, a fire and the AI filter
# read from here too, so a kind added to the hard limit is never forgotten elsewhere.
OPENER_ROLES = frozenset({"garage_opener", "gate_opener", "door_opener"})
PROTECTED_COVER_CLASSES = frozenset({"garage", "gate", "door"})
_OPENING_ROLES = OPENER_ROLES | {"lock"}
# What can hide a garage door, gate or door opener besides a cover: a relay, a push button, a
# helper button, a valve that is not the main tap.
_OPENER_DOMAINS = frozenset({"switch", "button", "input_button", "valve"})
_SHORT_WORD = 4  # "hek", "tor", "gate": only as a whole word ("gateway" is no gate)
_COVER_OPENING = frozenset(
    {"cover.open_cover", "cover.toggle", "cover.open_cover_tilt", "cover.toggle_cover_tilt"}
)
_MAIN_VALVE_CLASSES = frozenset({"water", "gas"})
_VALVE_OPENING = frozenset({"valve.open_valve", "valve.toggle"})
_HA_OWN_LOGIC = frozenset({"automation", "script", "scene", "blueprint"})
AUTOMATION_SWITCH = frozenset({"automation.turn_off", "automation.turn_on"})


def hard_limit_reason(
    entity: Entity | None,
    action: str,
    data: Mapping[str, Any] | None = None,
    role: str | None = None,
    protected: Collection[str] = (),
    opener: bool = False,
) -> str | None:
    """Return a reason code when an action crosses a hard limit, else ``None``. ``opener``: the
    house knows this opens a garage door, gate or door, whatever role it has besides – a role
    from a label, an AI or behaviour never takes that away."""
    data = data or {}
    entity_id = entity.entity_id if entity else ""
    if entity_id in protected:
        return "protected_by_admin"
    if action == "alarm_control_panel.alarm_disarm" or (
        action.startswith("alarm_control_panel.") and "disarm" in action
    ):
        return "alarm_disarm"
    if action in ("lock.unlock", "lock.open"):
        return "lock_open"
    domain = action.split(".", 1)[0]
    own_logic = domain in _HA_OWN_LOGIC or domain.startswith("input_")
    if own_logic and action not in AUTOMATION_SWITCH:
        return "no_own_automations"
    if domain == "cover" and entity is not None:
        protected_cover = (
            opener or entity.device_class in PROTECTED_COVER_CLASSES or role in _OPENING_ROLES
        )
        if protected_cover:
            if action in _COVER_OPENING:
                return "garage_gate_door_open"
            if action == "cover.set_cover_position" and float(data.get("position", 0)) > 0:
                return "garage_gate_door_open"
    # A relay, button or switch that opens a door or gate: any pulse may open it, so only an
    # explicit close or lock is ever possible.
    if (opener or role in _OPENING_ROLES) and action not in ("cover.close_cover", "lock.lock"):
        return "garage_gate_door_open"
    # A water or gas valve only ever closes (E-80, E-212; QA 2, B-168): in code, so that no
    # knowledge file can let HouseMind open the main tap.
    if domain == "valve" and entity is not None and entity.device_class in _MAIN_VALVE_CLASSES:
        if action in _VALVE_OPENING or (
            action == "valve.set_valve_position" and float(data.get("position", 0)) > 0
        ):
            return "valve_open"
    return None


def find_openers(
    model: HouseModel,
    garage_rooms: Collection[str],
    cover_words: Iterable[str],
    opener_words: Iterable[str],
    roles: Mapping[str, str],
) -> set[str]:
    """Everything that opens a garage door, gate or door (E-31; QA 3, gat 1), from the house
    itself, generic for every integration:

    - a cover in a garage room, of class garage, gate or door, or with a garage-door or gate
      word in its name, its device's name or a label;
    - a switch, button or valve with an opener word ("garagedeur", "poort", "deuropener" …) in
      its name, its device's name or a label – the bare word "garage" is not enough there: a
      washing machine in the garage is no door – or on a device that reports a garage door;
    - anything with an opener role, and a group with any of these in it.

    Better once too often: HouseMind never opens what is in this set, whatever else it is."""
    covers = [w for w in cover_words if w]
    others = [w for w in opener_words if w]
    garage_door_devices = {
        e.device_id
        for e in model.entities.values()
        if e.device_id and e.domain == "binary_sensor" and e.device_class == "garage_door"
    }
    found = {e for e, role in roles.items() if role in OPENER_ROLES}
    for entity in model.entities.values():
        if entity.domain == "cover":
            area = model.area_of(entity.entity_id)
            if (
                (area is not None and area.id in garage_rooms)
                or entity.device_class in PROTECTED_COVER_CLASSES
                or _says(_label(model, entity), covers)
            ):
                found.add(entity.entity_id)
        elif entity.domain in _OPENER_DOMAINS:
            if entity.domain == "valve" and entity.device_class in _MAIN_VALVE_CLASSES:
                continue  # the main tap: its own hard limit, it only ever closes
            if entity.device_id in garage_door_devices or _says(_label(model, entity), others):
                found.add(entity.entity_id)
    # A group answers for its members (QA 2, M-3): "Alle rolluiken" with the garage door in it.
    for group, members in model.groups.items():
        if any(m in found for m in members):
            found.add(group)
    return found


def _label(model: HouseModel, entity: Entity) -> str:
    """Id, name, device name and labels as one text, with ``_`` and ``.`` as spaces."""
    return model.words_of(entity, labels=True)  # _says folds it (code QA 3, punten 4 en 8)


def _says(text: str, words: Iterable[str]) -> bool:
    # One rule for every language, the broadest: "Garáge" and "gate2" count (code QA 3, punt 4).
    return words_have(text, words, whole_up_to=_SHORT_WORD)
