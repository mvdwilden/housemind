"""Ordering and filtering (E-258, B-106): where an automation, scene, script or device acts,
which part of the house it is about, what starts it and how far HouseMind goes with it – in a
few plain groups, for the buttons above a list. Showing only: nothing here changes the house.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from housemind.brain.automations import OPENINGS, PRESENCE
from housemind.core.model import HouseModel
from housemind.ha.adapter import HAAutomation, HAStep, HATrigger

# The part of the house a Home Assistant domain is about, in the words of the card (15.16).
PARTS_BY_DOMAIN = {
    "light": "lighting",
    "cover": "covers",
    "climate": "climate",
    "fan": "air",
    "humidifier": "air",
    "media_player": "media",
    "remote": "media",
    "vacuum": "cleaning",
    "lawn_mower": "garden",
    "valve": "water",
    "water_heater": "water",
    "lock": "security",
    "alarm_control_panel": "security",
    "siren": "security",
    "camera": "security",
    "notify": "messages",
    "tts": "messages",
    "persistent_notification": "messages",
}
PARTS = (
    "lighting",
    "covers",
    "climate",
    "air",
    "media",
    "cleaning",
    "garden",
    "water",
    "security",
    "messages",
    "other",
)
CAUSES = ("time", "sun", "presence", "people", "opening", "button", "other")
STEPS = ("autonomous", "propose", "observe", "measure", "left_alone")
_BUTTON_WORDS = ("press", "button", "click", "tap", "action", "scene_", "rotate")


def part_of(entity_id: str, roles: Mapping[str, str]) -> str:
    """Lighting for a lamp, also a switch with the role lamp; Other for what fits nowhere."""
    if roles.get(entity_id) == "light":
        return "lighting"
    return PARTS_BY_DOMAIN.get(entity_id.split(".", 1)[0], "other")


def _targets(model: HouseModel, step: HAStep) -> list[str]:
    """The entities a step acts on, also through the devices it names."""
    found = list(step.entity_ids)
    for device_id in step.device_ids:
        found += [e.entity_id for e in model.on_device(device_id)]
    return found


def rooms_of(model: HouseModel, steps: Iterable[HAStep], entities: Iterable[str] = ()) -> list[str]:
    """The rooms an automation or script acts in: of its devices and the rooms it names."""
    found: set[str] = set()
    for step in steps:
        found |= {a for a in step.area_ids if a in model.areas}
        for entity_id in _targets(model, step):
            area = model.area_of(entity_id)
            if area is not None:
                found.add(area.id)
    for entity_id in entities:
        area = model.area_of(entity_id)
        if area is not None:
            found.add(area.id)
    return sorted(found)


def parts_of(model: HouseModel, roles: Mapping[str, str], steps: Iterable[HAStep]) -> list[str]:
    """What an automation or script is about, from what it acts on."""
    found: set[str] = set()
    for step in steps:
        domain = step.action.split(".", 1)[0]
        if domain in ("scene", "script"):
            continue  # what the scene or script does, it says itself
        targets = _targets(model, step)
        if targets:
            found |= {part_of(e, roles) for e in targets}
        else:
            found.add(PARTS_BY_DOMAIN.get(domain, "other"))
    return [p for p in PARTS if p in found]


def _cause(model: HouseModel, trigger: HATrigger) -> str:
    platform, to = trigger.platform, (trigger.to or "").lower()
    if platform in ("time", "time_pattern", "calendar"):
        return "time"
    if platform == "sun" or "sun.sun" in trigger.entity_ids:
        return "sun"
    if platform == "zone" or "zone.home" in trigger.entity_ids:
        return "people"
    if platform in ("event", "tag") or (
        platform == "device" and any(w in to for w in _BUTTON_WORDS)
    ):
        return "button"
    for ref in trigger.entity_ids:
        entity = model.entities.get(ref) or next(
            (e for e in model.entities.values() if e.registry_id == ref), None
        )
        entity_id = entity.entity_id if entity else ref
        domain = entity_id.split(".", 1)[0]
        if domain in ("person", "device_tracker"):
            return "people"
        if domain in ("event", "button", "input_button"):
            return "button"
        if domain == "binary_sensor" and entity is not None:
            if entity.device_class in PRESENCE:
                return "presence"
            if entity.device_class in OPENINGS:
                return "opening"
    if platform == "device" and to in ("motion", "occupied", "present", "no_motion"):
        return "presence"
    if platform == "device" and to in ("opened", "not_opened"):
        return "opening"
    return "other"


def causes_of(model: HouseModel, automation: HAAutomation) -> list[str]:
    """What starts an automation, in a few plain groups."""
    found = {_cause(model, trigger) for trigger in automation.triggers}
    return [c for c in CAUSES if c in found]
