"""Which door and window contacts are openings of the house.

The class door, window, opening or garage door is not enough: the door of a dishwasher, fridge,
oven or car must not set a room's heating to 12 °C or be reported "open while nobody is home".
An opening of the house is a contact that

- is a window (always), or a door, opening or garage door on a device that only senses
  (a contact sensor), not on an appliance that also does something, and not on a vehicle;
- has no appliance word (fridge, oven, dishwasher … from every language file) in its name or
  its device's name;
- is not hidden, a setting or diagnostic, or excluded by an administrator.

Where the rules are unsure – a door on a device that also switches (a garage door's relay, or
an appliance), or the plain class "opening" in a kitchen or utility room, where appliances
have doors too – an administrator says whether it is an opening of the house (design 50.2,
B-359); from then on that holds.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from housemind.core.model import Entity, HouseModel
from housemind.knowledge.words import has_word as words_have

OPENING_CLASSES = frozenset({"door", "window", "opening", "garage_door"})
# A device with one of these does something itself: an appliance or a vehicle, not a contact.
NOT_A_CONTACT = frozenset(
    {
        "switch",
        "light",
        "climate",
        "fan",
        "media_player",
        "vacuum",
        "water_heater",
        "select",
        "device_tracker",
        "lawn_mower",
    }
)
# A device that only switches besides its contact may be a garage door's or a gate's relay:
# the rules are not sure. Everything else that acts is an appliance or a vehicle.
SWITCHING = frozenset({"switch", "light", "select"})
APPLIANCE_ROOMS = frozenset({"kitchen", "utility"})  # where a dishwasher's door may be "opening"


def _has_word(text: str, words: Iterable[str]) -> bool:
    return words_have(text, words)  # one rule for every language (code QA 3, punt 4)


def house_openings(
    model: HouseModel,
    appliance_words: Iterable[str],
    excluded: set[str] | None = None,
    decided: dict[str, bool] | None = None,
) -> set[str]:
    """The contacts that are openings of the house; ``decided``: what a person said where the
    rules doubted (B-359), which goes first."""
    words = list(appliance_words)
    families = _families(model)
    found: set[str] = set()
    for entity in model.entities.values():
        if entity.domain != "binary_sensor" or entity.device_class not in OPENING_CLASSES:
            continue
        if entity.hidden or entity.disabled or entity.entity_category:
            continue
        if excluded and entity.entity_id in excluded:
            continue
        if _has_word(_label(model, entity), words):
            continue
        family = families.get(entity.device_id or "", set())
        if entity.device_class != "window" and family & NOT_A_CONTACT:
            continue
        found.add(entity.entity_id)
    for entity_id, yes in (decided or {}).items():
        if not entity_id.startswith("binary_sensor.") or entity_id not in model.entities:
            continue
        if yes and not (excluded and entity_id in excluded):
            found.add(entity_id)
        else:
            found.discard(entity_id)
    return found


def opening_doubts(
    model: HouseModel,
    appliance_words: Iterable[str],
    excluded: set[str] | None = None,
    area_roles: Mapping[str, str] | None = None,
) -> dict[str, bool]:
    """Contacts where the rules are unsure (design 50.2, B-359), with their own guess: a door
    on a device that also switches is left out (False); the plain class "opening" in a kitchen
    or utility room counts (True). A window, a word for an appliance, a vehicle or an
    appliance that does more is sure, and not asked."""
    roles = area_roles or {}
    words = list(appliance_words)
    families = _families(model)
    out: dict[str, bool] = {}
    for entity in model.entities.values():
        if entity.domain != "binary_sensor" or entity.device_class not in OPENING_CLASSES:
            continue
        if entity.device_class == "window" or entity.hidden or entity.disabled:
            continue
        if entity.entity_category or (excluded and entity.entity_id in excluded):
            continue
        if _has_word(_label(model, entity), words):
            continue
        acts = families.get(entity.device_id or "", set()) & NOT_A_CONTACT
        area = model.area_of(entity.entity_id)
        if acts and not acts - SWITCHING:
            out[entity.entity_id] = False
        elif not acts and entity.device_class == "opening" and area is not None:
            if roles.get(area.id) in APPLIANCE_ROOMS:
                out[entity.entity_id] = True
    return out


def _families(model: HouseModel) -> dict[str, set[str]]:
    families: dict[str, set[str]] = {}
    for entity in model.entities.values():
        if entity.device_id is not None:
            families.setdefault(entity.device_id, set()).add(entity.domain)
    return families


def _label(model: HouseModel, entity: Entity) -> str:
    return model.words_of(entity)
