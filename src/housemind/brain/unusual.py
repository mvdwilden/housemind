"""Something out of the ordinary, seen by the core itself (E-416, design 50.3).

A lamp that has been on for hours in a room where nobody has been for a while. The core only
sees it – it changes nothing: Vesta asks whether it should go off, and only a person's yes
switches it off. Without AI, without history: what HouseMind sees now.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from housemind.core.model import HouseModel, State
    from housemind.world.world import WorldState

LONG_ON = timedelta(hours=4)  # on this long without a break …
EMPTY_FOR = timedelta(hours=1)  # … while nobody has been in the room this long


@dataclass(frozen=True, slots=True)
class Unusual:
    kind: str  # "light_on"
    entity_id: str
    area_id: str
    hours: int  # how long it has been on, whole hours
    empty_hours: int  # how long nobody has been there, whole hours


def lamps_on_long(
    snapshot: WorldState,
    states: dict[str, State],
    model: HouseModel,
    not_lamps: set[str] | frozenset[str] = frozenset(),
) -> list[Unusual]:
    """Lamps on for hours in a room where nobody has been for a while, the longest first. Only
    in a room whose sensors say when someone was last there: elsewhere HouseMind cannot know.
    A device's status light (``not_lamps``, E-426) is no lamp."""
    out = []
    for entity_id, state in states.items():
        if not entity_id.startswith("light.") or state.state != "on":
            continue
        if entity_id in not_lamps:
            continue
        area = model.area_of(entity_id)
        info = snapshot.occupancy.get(area.id) if area else None
        if area is None or info is None or info.occupied or info.last_activity is None:
            continue
        on_for = snapshot.now - state.last_changed
        empty_for = snapshot.now - info.last_activity
        if on_for >= LONG_ON and empty_for >= EMPTY_FOR:
            hours = int(on_for.total_seconds() // 3600)
            empty = int(empty_for.total_seconds() // 3600)
            out.append(Unusual("light_on", entity_id, area.id, hours, empty))
    return sorted(out, key=lambda u: (-u.hours, u.entity_id))
