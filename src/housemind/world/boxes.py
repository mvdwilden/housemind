"""A small room with one door (E-504, design 76): a room where nothing sees people, with one
door of the house and a lamp. There the door alone can say who is in – once an administrator
said Klopt to it (E-505). Generic: only the room's kind and its sensors count, in any language,
never a name of one house."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from housemind.core.model import HouseModel
from housemind.world.occupancy import OccupancyModel

# The kinds of room HouseMind asks about: one goes in and shuts the door behind them.
BOX_ROLES = frozenset({"toilet", "bathroom"})
DOOR_CLASSES = frozenset({"door", "opening"})  # not a window or a garage door


def box_rooms(
    model: HouseModel,
    occupancy: OccupancyModel,
    openings: Iterable[str],
    roles: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Per room its one door, where it can be a small room with one door: nothing in it sees
    people, exactly one door of the house, and a lamp. With ``roles`` only toilets and
    bathrooms – the rooms HouseMind asks about."""
    doors: dict[str, list[str]] = {}
    for entity_id in sorted(openings):
        entity = model.entities.get(entity_id)
        if entity is None or entity.device_class not in DOOR_CLASSES:
            continue
        if entity_id in occupancy.ignored:
            continue
        area = model.area_of(entity_id)
        if area is not None:
            doors.setdefault(area.id, []).append(entity_id)
    lit = {
        area.id
        for e in model.entities.values()
        if e.domain == "light"
        and not (e.hidden or e.disabled or e.entity_category)
        and (area := model.area_of(e.entity_id)) is not None
    }
    out: dict[str, str] = {}
    for area_id, found in doors.items():
        if len(found) != 1 or area_id not in lit or occupancy.sees_people(area_id):
            continue
        if roles is not None and roles.get(area_id) not in BOX_ROLES:
            continue
        out[area_id] = found[0]
    return out
