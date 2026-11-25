"""The house as HouseMind sees it, in HouseMind terms (not Home Assistant terms).

The HA translation layer (E-151) converts Home Assistant registries and states into these
types; nothing else in HouseMind reads raw Home Assistant data.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


def daypart(local: datetime) -> str:
    """Night, morning, day or evening, by the clock."""
    hour = local.hour
    if hour >= 23 or hour < 6:
        return "night"
    if hour < 9:
        return "morning"
    if hour < 17:
        return "day"
    return "evening"


class Source(StrEnum):
    """Who caused a change (E-115)."""

    HOUSEMIND = "housemind"
    PERSON = "person"  # a logged-in user via an app (HA or HouseMind)
    AUTOMATION = "automation"  # a Home Assistant automation or script
    SIGNAL = "signal"  # an automation that gives a sign with a lamp: teaches nothing (E-461)
    PHYSICAL = "physical"  # wall switch, button or the device itself
    OTHER_SYSTEM = "other_system"  # another controller (E-68)
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Floor:
    id: str
    name: str
    level: int | None = None


@dataclass(frozen=True, slots=True)
class Area:
    id: str
    name: str
    floor_id: str | None = None
    aliases: tuple[str, ...] = ()  # other names the room has in HA
    labels: tuple[str, ...] = ()  # label ids (E-345)
    icon: str | None = None  # the icon a person chose for the room in HA ("mdi:sofa", E-509)


# Where an energy device without a room in Home Assistant learns its step (E-343): the whole
# house. Never a room of the model; screens say "het hele huis".
WHOLE_HOUSE = "_house"


@dataclass(frozen=True, slots=True)
class Label:
    """A label someone gave things in Home Assistant: a hint, never leading (E-345)."""

    id: str
    name: str
    icon: str | None = None


@dataclass(frozen=True, slots=True)
class Category:
    """A category of automations, scripts, scenes or helpers in Home Assistant (E-345)."""

    id: str
    scope: str  # automation, script, scene, helpers
    name: str
    icon: str | None = None


@dataclass(frozen=True, slots=True)
class Device:
    id: str
    name: str
    area_id: str | None = None
    integration: str | None = None
    manufacturer: str | None = None  # left out of Vesta's plain names (E-248)
    model: str | None = None
    # HA's identifiers ("mqtt:zigbee2mqtt_0x…"): how a Zigbee2MQTT group's members are found
    # when HA does not list them (E-312).
    identifiers: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()  # label ids (E-345)


@dataclass(frozen=True, slots=True)
class Entity:
    """One Home Assistant entity, described by HA's generic terms only (no brands)."""

    entity_id: str
    registry_id: str  # stable id that survives renames (E-116)
    name: str
    device_id: str | None = None
    area_id: str | None = None
    device_class: str | None = None
    supported_features: int = 0
    unit: str | None = None
    entity_category: str | None = None  # None, "config" or "diagnostic"
    disabled: bool = False
    hidden: bool = False
    platform: str | None = None  # the integration that provides it
    capabilities: dict[str, Any] = field(default_factory=dict)
    unique_id: str | None = None  # the integration's own id ("8_light_zigbee2mqtt": a group)
    # The integration's fixed name for what it is, the same in every language ("min_soc").
    translation_key: str | None = None
    labels: tuple[str, ...] = ()  # label ids (E-345)
    categories: dict[str, str] = field(default_factory=dict)  # scope -> category id (E-345)
    config_entry_id: str | None = None  # the integration that gives it (B-236)
    # What the integration itself calls it ("Child Lock"), and whether a person gave it a name
    # of their own: then that name never goes to an AI (E-429).
    original_name: str | None = None
    named_by_person: bool = False
    icon: str | None = None  # the icon a person chose in HA ("mdi:fridge"; E-509), not HA's own
    # "light", "sensor" …: worked out once, not at each of the many times a round asks for it
    # (code QA 3, punt 14).
    domain: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", self.entity_id.split(".", 1)[0])


@dataclass(frozen=True, slots=True)
class Context:
    """Home Assistant context of a change, used to find out who did it."""

    id: str
    user_id: str | None = None
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class State:
    entity_id: str
    state: str
    attributes: dict[str, Any]
    last_changed: datetime
    context: Context | None = None

    @property
    def available(self) -> bool:
        return self.state not in ("unavailable", "unknown")


@dataclass(frozen=True, slots=True)
class StateChange:
    entity_id: str
    old: State | None
    new: State | None
    source: Source = Source.UNKNOWN
    cause: str | None = None  # the automation whose run did it, if HouseMind heard it start

    @property
    def online_only(self) -> bool:
        """The device dropped off the network or came back on it (a radio that reconnects, a
        curtain motor a few seconds away): nobody's hand – as for presence and learning."""
        return (self.old is not None and self.old.state in ("unavailable", "unknown")) or (
            self.new is not None and self.new.state in ("unavailable", "unknown")
        )


# A sensor on one of these measures the device, not the room's air (code QA 3, punt 5: this
# list stood in three places).
AIR_DEVICES = frozenset({"humidifier", "climate", "water_heater"})


@dataclass(slots=True)
class HouseModel:
    """Everything the registries told us, indexed for quick lookup."""

    floors: dict[str, Floor] = field(default_factory=dict)
    areas: dict[str, Area] = field(default_factory=dict)
    devices: dict[str, Device] = field(default_factory=dict)
    entities: dict[str, Entity] = field(default_factory=dict)
    labels: dict[str, Label] = field(default_factory=dict)  # by id (E-345)
    categories: dict[str, Category] = field(default_factory=dict)  # by id (E-345)
    # Groups made in HA (a light group, a switch group …) → their members. HouseMind works
    # with the members: a group can sit in another room than its lamps (the hall's lamps as a
    # group in the kitchen), and controlling both does everything twice.
    groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Which group steers a lamp (E-312): a group in the radio network itself (Zigbee2MQTT,
    # ZHA, Hue, deCONZ) goes first, the biggest one in one room – one radio message for the
    # whole room; then HA's own groups, the smallest one; a group over several rooms only
    # gathers. A group that steers lamps is a unit.
    steered_by: dict[str, str] = field(default_factory=dict)
    group_area: dict[str, str] = field(default_factory=dict)  # a group's room: its lamps' room
    # Which room each entity is in, remembered with the entity it was for (S1: asked millions
    # of times; a replaced entity or device is looked up again).
    _room_of: dict[str, tuple[Entity, Device | None, str | None]] = field(
        default_factory=dict, repr=False, compare=False
    )
    # Every room's entities and every device's, found once for the whole house (code QA 3,
    # punten 5 en 13): asking "what is in this room" went through every entity of the house,
    # per room – a house twelve times as big was 28 times as slow. Made again when entities,
    # devices or groups come or go, and after forget_rooms().
    _rooms: dict[str, tuple[Entity, ...]] | None = field(default=None, repr=False, compare=False)
    _on_devices: dict[str, tuple[Entity, ...]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _rooms_seen: tuple[int, ...] = field(default=(), repr=False, compare=False)
    # Where a group lives: "zigbee2mqtt", "zha", "hue", "deconz" (in the radio network) or
    # "ha" (HA's group helper or another integration's) – E-312.
    group_source: dict[str, str] = field(default_factory=dict)
    # Met rust laten (E-458, the administrator's choice of 04-11-2026): what HouseMind never
    # addresses on its own – what an administrator ticked, and everything in a group that is
    # ticked. ``held``: what may not move at all, a ticked group and all that is in it; a group
    # with any of that in it does not steer. A lamp ticked by itself in a group that steers
    # goes along with its group.
    left_alone: set[str] = field(default_factory=set)
    held: set[str] = field(default_factory=set)
    steering: set[str] = field(default_factory=set)  # the groups that steer their lamps

    def unit(self, entity_id: str) -> str | None:
        """What HouseMind addresses for this entity (E-458): the group that steers it, else the
        entity itself; None for what is left alone and for a group that only gathers. Every
        place that switches, counts, learns or shows a lamp goes through this, so a lamp in a
        group never stands on its own anywhere."""
        steering = self.steered_by.get(entity_id)
        if steering is not None:
            return steering
        if entity_id in self.left_alone:
            return None
        if entity_id in self.groups:
            return entity_id if entity_id in self.steering else None
        return entity_id

    def units(self, entity_ids: Iterable[str]) -> list[str]:
        """The units of these entities, each once, in their order (E-458)."""
        out: dict[str, None] = {}
        for entity_id in entity_ids:
            unit = self.unit(entity_id)
            if unit is not None:
                out.setdefault(unit)
        return list(out)

    def words_of(self, entity: Entity, labels: bool = False) -> str:
        """Its id, name and device's name as one text, with its labels and its device's when
        asked: where HouseMind looks for a word (code QA 3, punt 8 – it was put together at
        eight places)."""
        device = self.devices.get(entity.device_id or "")
        parts = [entity.entity_id, entity.name, device.name if device else ""]
        if labels:
            ids = (*entity.labels, *(device.labels if device else ()))
            parts += [self.labels[i].name for i in ids if i in self.labels]
        return " ".join(parts)

    def device_name(self, entity_id: str) -> str:
        """What people call it: its device's name, else its own, else its id."""
        entity = self.entities.get(entity_id)
        if entity is None:
            return entity_id
        device = self.devices.get(entity.device_id or "")
        return device.name if device else entity.name

    def in_area(self, area_id: str) -> tuple[Entity, ...]:
        """Every entity in this room – in it itself, through its device, or a group through its
        lamps – in the order of the house."""
        return self._index().get(area_id, ())

    def on_device(self, device_id: str) -> tuple[Entity, ...]:
        """Every entity of this device."""
        self._index()
        return self._on_devices.get(device_id, ())

    def air_sensors(self, classes: Iterable[str], excluded: Iterable[str] = ()) -> dict[str, str]:
        """The sensors of these classes that measure a room's air, each with its room: not
        hidden, off or a setting, not left out, and not on a humidifier, airco or boiler – that
        measures the device (code QA 3, punt 5: built at three places)."""
        wanted, skip = set(classes), set(excluded)
        found: dict[str, str] = {}
        for entity in self.entities.values():
            if entity.domain != "sensor" or entity.device_class not in wanted:
                continue
            if entity.hidden or entity.disabled or entity.entity_category:
                continue
            if entity.entity_id in skip or self.on_an_air_device(entity):
                continue
            area = self.area_of(entity.entity_id)
            if area is not None:
                found[entity.entity_id] = area.id
        return found

    def on_an_air_device(self, entity: Entity) -> bool:
        """A sensor in a humidifier, airco or boiler measures the device, not the room."""
        return entity.device_id is not None and any(
            other.domain in AIR_DEVICES for other in self.on_device(entity.device_id)
        )

    def forget_rooms(self) -> None:
        """An entity or device changed in its place (another room, hidden): find them again."""
        self._rooms = None

    def _index(self) -> dict[str, tuple[Entity, ...]]:
        seen = (
            id(self.entities),
            len(self.entities),
            id(self.devices),
            len(self.devices),
            id(self.group_area),
            len(self.group_area),
        )
        if self._rooms is not None and self._rooms_seen == seen:
            return self._rooms
        rooms: dict[str, list[Entity]] = {}
        devices: dict[str, list[Entity]] = {}
        for entity in self.entities.values():
            area = self.area_of(entity.entity_id)
            if area is not None:
                rooms.setdefault(area.id, []).append(entity)
            if entity.device_id is not None:
                devices.setdefault(entity.device_id, []).append(entity)
        self._rooms = {area_id: tuple(found) for area_id, found in rooms.items()}
        self._on_devices = {device_id: tuple(found) for device_id, found in devices.items()}
        self._rooms_seen = seen
        return self._rooms

    def area_of(self, entity_id: str) -> Area | None:
        if entity_id in self.group_area:
            return self.areas.get(self.group_area[entity_id])
        entity = self.entities.get(entity_id)
        if entity is None:
            return None
        device = self.devices.get(entity.device_id) if entity.device_id is not None else None
        known = self._room_of.get(entity_id)
        if known is not None and known[0] is entity and known[1] is device:
            area_id = known[2]
        else:
            area_id = entity.area_id or (device.area_id if device else None)
            self._room_of[entity_id] = (entity, device, area_id)
        return self.areas.get(area_id) if area_id else None
