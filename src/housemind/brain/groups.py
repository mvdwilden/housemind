"""Groups of devices, and which kind (E-312).

A group in the radio network itself – Zigbee2MQTT, ZHA, a Hue room or zone, deCONZ – is one
radio message for all its lamps: they react together and much faster than through a group
made in Home Assistant, which sends one command per lamp (the Zigbee2MQTT and ZHA
documentation). So such a group goes first.

How each shows in HA (from the HA, Zigbee2MQTT and ZHA documentation and source):

- Zigbee2MQTT: a light/switch/cover/lock entity of the MQTT integration, unique id
  "<group id>_<type>_zigbee2mqtt" (a device's is "0x…_…"), its device made by "Zigbee2MQTT",
  model "Group". Members in ``group_entities`` (HA 2026.4+ with Zigbee2MQTT 2.10+); older:
  the retained MQTT topic "zigbee2mqtt/bridge/groups" (members by ieee address).
- ZHA: unique id "<platform>_zha_group_0x…", members in ``group_entities``.
- Hue room or zone: ``is_hue_group``, members in ``entity_id`` (off by default in HA).
- deCONZ: ``is_deconz_group``; HA does not tell its members.
- HA's own group helper: platform "group", members in ``entity_id`` (a lock group also in
  ``group_entities``). No speed gain: HA sends each member its own command.
"""

from __future__ import annotations

import re
from typing import Any

from housemind.core.model import Device, Entity, HouseModel

RADIO = ("zigbee2mqtt", "zha", "hue", "deconz")  # one message in the radio network
_Z2M_GROUP = re.compile(r"^\d+_[a-z_]+_zigbee2mqtt$")  # a device's starts with 0x


def source_of(entity: Entity | None, device: Device | None, attributes: dict[str, Any]) -> str:
    """Where a group lives: in the radio network (Zigbee2MQTT, ZHA, Hue, deCONZ) or in HA."""
    if attributes.get("is_hue_group"):
        return "hue"
    if attributes.get("is_deconz_group"):
        return "deconz"
    platform = entity.platform if entity else None
    unique_id = (entity.unique_id or "") if entity else ""
    if platform == "zha" and "_zha_group_" in unique_id:
        return "zha"
    made_by_z2m = device is not None and (device.manufacturer or "").lower() == "zigbee2mqtt"
    group_model = device is not None and (device.model or "").lower() == "group"
    if platform == "mqtt" and (_Z2M_GROUP.match(unique_id) or (made_by_z2m and group_model)):
        return "zigbee2mqtt"
    return "ha"


def members_of(attributes: dict[str, Any]) -> list[str] | None:
    """The members HA lists for a group: ``group_entities`` first (the newer way), else the
    group helper's and Hue's ``entity_id``."""
    for key in ("group_entities", "entity_id"):
        value = attributes.get(key)
        if isinstance(value, list) and value:
            return [str(x) for x in value]
    return None


def is_group(attributes: dict[str, Any]) -> bool:
    """Something HA shows as a group, also when it does not tell the members (deCONZ)."""
    return members_of(attributes) is not None or bool(
        attributes.get("is_hue_group") or attributes.get("is_deconz_group")
    )


def z2m_members(groups: Any, model: HouseModel) -> dict[str, list[str]]:
    """From Zigbee2MQTT's own list of groups (the retained topic "<base>/bridge/groups"):
    each group entity in HA → the entities of its member devices in the same domain. Used
    when HA does not list the members (older HA or Zigbee2MQTT)."""
    if not isinstance(groups, list):
        return {}
    by_ieee: dict[str, list[Entity]] = {}
    for entity in model.entities.values():
        device = model.devices.get(entity.device_id or "")
        for identifier in device.identifiers if device else ():
            if identifier.startswith("mqtt:zigbee2mqtt_0x"):
                by_ieee.setdefault(identifier.removeprefix("mqtt:zigbee2mqtt_"), []).append(entity)
    group_entities: dict[str, list[Entity]] = {}
    for entity in model.entities.values():
        unique_id = entity.unique_id or ""
        if entity.platform == "mqtt" and _Z2M_GROUP.match(unique_id):
            group_entities.setdefault(unique_id.split("_", 1)[0], []).append(entity)
    out: dict[str, list[str]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        ieees = [
            str(m.get("ieee_address"))
            for m in group.get("members") or []
            if isinstance(m, dict) and m.get("ieee_address")
        ]
        for entity in group_entities.get(str(group.get("id")), []):
            members = sorted(
                e.entity_id
                for ieee in ieees
                for e in by_ieee.get(ieee, [])
                if e.domain == entity.domain and e.entity_id != entity.entity_id
            )
            if members:
                out[entity.entity_id] = members
    return out
