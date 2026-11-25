"""Camera detections (E-180, E-181, E-185, E-189; design 16.1, 16.2).

HouseMind never looks at images: it reads what cameras, doorbells and recorders already report
in Home Assistant, in three forms –

- an event entity (``event.*``): its type (``event_type``) says what was seen or heard; a
  doorbell's event entity rings whatever its type is called;
- a binary sensor of a camera that turns on: "Person detected", "Dier gedetecteerd";
- a counter of a camera: "number of people" going from 0 to more.

Which type or word means which detection is knowledge (``knowledge/security/cameras.yaml``),
not a brand in the code. The camera that goes with a detection is the one on the same device
(a second camera such as a package camera only without a main one), else one in the same room.

Never used: who it is – a recognised face is "a person", a licence plate "a vehicle" (E-32) –,
access by NFC or fingerprint, talking, and every camera setting (E-189). A smoke or CO sensor
that Home Assistant already marks as one stays with the emergency reactions (E-80).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from housemind.core.model import Entity, HouseModel, State, StateChange

# The sounds and detections HouseMind knows; what each means is in brain/security.py (16.2).
# Device classes Home Assistant itself gives an emergency sensor: those stay with E-80.
EMERGENCY_CLASSES = frozenset({"smoke", "carbon_monoxide", "co", "gas", "moisture", "cold"})
MOTION_CLASSES = frozenset({"motion", "occupancy", "presence", "moving"})
NOT_A_STATE = frozenset({"unavailable", "unknown", ""})


@dataclass(frozen=True, slots=True)
class CameraWords:
    """The knowledge: event types and words per detection, in which order words win."""

    event_types: dict[str, str] = field(default_factory=dict)  # type → detection
    words: dict[str, tuple[str, ...]] = field(default_factory=dict)  # detection → words
    order: tuple[str, ...] = ()
    ignore_types: frozenset[str] = frozenset()
    ignore_words: tuple[str, ...] = ()
    second_camera: tuple[str, ...] = ()
    window_minutes: float = 10.0
    bundle_minutes: float = 30.0  # the cameras while away: a new message after this quiet
    doorbell_seconds: float = 30.0
    light_minutes: float = 5.0
    event_fresh_seconds: float = 120.0
    same_seconds: float = 5.0
    optional_sounds: frozenset[str] = frozenset()


def load_camera_words(directories: Iterable[Path]) -> CameraWords:
    """The camera knowledge from every knowledge folder (an update adds to what is there)."""
    types: dict[str, str] = {}
    words: dict[str, list[str]] = {}
    order: list[str] = []
    ignore_types: set[str] = set()
    ignore_words: list[str] = []
    second: list[str] = []
    numbers: dict[str, float] = {}
    optional: set[str] = set()
    for directory in directories:
        path = directory / "cameras.yaml"
        if not path.exists():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for kind, entry in (raw.get("detections") or {}).items():
            for event_type in (entry or {}).get("event_types") or []:
                types[_norm(str(event_type))] = str(kind)
            words.setdefault(str(kind), []).extend(
                str(w).lower() for w in (entry or {}).get("words") or []
            )
        order = [str(k) for k in raw.get("order") or []] or order
        ignore = raw.get("ignore") or {}
        ignore_types |= {_norm(str(x)) for x in ignore.get("event_types") or []}
        ignore_words += [str(x).lower() for x in ignore.get("words") or []]
        second += [str(x).lower() for x in raw.get("second_camera") or []]
        for key in (
            "window_minutes",
            "bundle_minutes",
            "doorbell_seconds",
            "light_minutes",
            "event_fresh_seconds",
            "same_seconds",
        ):
            if isinstance(raw.get(key), int | float):
                numbers[key] = float(raw[key])
        optional |= {str(x) for x in raw.get("optional_sounds") or []}
    return CameraWords(
        types,
        {k: tuple(v) for k, v in words.items()},
        tuple(order or sorted(words)),
        frozenset(ignore_types),
        tuple(ignore_words),
        tuple(second),
        optional_sounds=frozenset(optional),
        **numbers,
    )


def _norm(text: str) -> str:
    return re.sub(r"[\s\-]+", "_", text.strip().lower())


def _has(text: str, words: Iterable[str]) -> bool:
    haystack = f" {text.lower().replace('_', ' ')} "
    return any(re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", haystack) for w in words if w)


@dataclass(frozen=True, slots=True)
class Source:
    """An entity that reports detections. ``kind`` is fixed for a binary sensor or a counter;
    an event entity says it with each event (``default`` when its type is not known)."""

    entity_id: str
    form: str  # "event", "binary" or "count"
    kind: str | None
    camera: str | None
    area_id: str | None
    default: str | None = None
    kinds: tuple[str, ...] = ()  # what an event entity can report (its event types)


@dataclass(frozen=True, slots=True)
class Detection:
    kind: str
    entity_id: str
    camera: str | None
    area_id: str | None
    at: datetime


class CameraIndex:
    """Which entity reports which detection, and the camera that goes with it."""

    def __init__(
        self, model: HouseModel, words: CameraWords, states: Mapping[str, State] | None = None
    ) -> None:
        self.model = model
        self.words = words
        self.sources: dict[str, Source] = {}
        self.smoke: dict[str, str] = {}  # a camera's own smoke/CO binary sensor → "smoke"/"co"
        self.build(states or {})

    # -- building -------------------------------------------------------------------------

    def build(self, states: Mapping[str, State]) -> None:
        """Again after the house changed in HA (the event types may only be in a state)."""
        self._states = states
        self.sources, self.smoke = {}, {}
        cameras = self._cameras_by_device()
        for entity in self.model.entities.values():
            if entity.hidden or entity.disabled or entity.entity_category:
                continue  # settings and diagnostics are no detections (E-189)
            on_camera = entity.device_id is not None and entity.device_id in cameras
            source = self._source(entity, on_camera)
            if source is None:
                continue
            if source.form == "binary" and source.kind in ("smoke", "co"):
                self.smoke[entity.entity_id] = source.kind  # the emergency reactions (E-80)
                continue
            self.sources[entity.entity_id] = source

    def _cameras_by_device(self) -> dict[str, list[Entity]]:
        found: dict[str, list[Entity]] = {}
        for entity in self.model.entities.values():
            if entity.domain == "camera" and entity.device_id and not entity.disabled:
                found.setdefault(entity.device_id, []).append(entity)
        return found

    def _label(self, entity: Entity) -> str:
        """The entity's own name, without its device's name: a camera in the "Babykamer"
        does not make every detection a crying baby."""
        device = self.model.devices.get(entity.device_id or "")
        name, object_id = entity.name, entity.entity_id.split(".", 1)[-1]
        if device is not None and device.name:
            if name.lower().startswith(device.name.lower()):
                name = name[len(device.name) :]
            slug = _norm(device.name)
            if object_id.startswith(slug):
                object_id = object_id[len(slug) :]
        own = f"{name} {object_id}".strip(" _")
        return own or f"{entity.name} {entity.entity_id}"

    def kind_of(self, text: str) -> str | None:
        """The detection a name or an event type means, in the order of the knowledge."""
        if _has(text, self.words.ignore_words):
            return None
        for kind in self.words.order:
            if _has(text, self.words.words.get(kind, ())):
                return kind
        return None

    def _source(self, entity: Entity, on_camera: bool) -> Source | None:
        area = self.model.area_of(entity.entity_id)
        area_id = area.id if area else None
        if entity.domain == "event":
            doorbell = entity.device_class == "doorbell"
            if not (on_camera or doorbell or entity.device_class == "motion"):
                return None
            state = self._states.get(entity.entity_id)
            listed = entity.capabilities.get("event_types") or (
                state.attributes.get("event_types") if state is not None else None
            )
            types = [_norm(str(x)) for x in listed or []]
            kinds = tuple(
                dict.fromkeys(
                    k
                    for k in (self._event_kind(t, entity.device_class) for t in types)
                    if k is not None
                )
            )
            # A type that says nothing ("detected"): the entity's own name says what it is.
            default = (
                "doorbell"
                if doorbell
                else "motion"
                if entity.device_class == "motion"
                else self.kind_of(self._label(entity))
            )
            if not kinds and default is None:
                return None
            camera = self._camera_for(entity)
            return Source(entity.entity_id, "event", None, camera, area_id, default, kinds)
        if not on_camera:
            return None
        if entity.domain == "binary_sensor":
            if entity.device_class in EMERGENCY_CLASSES:
                return None  # HA marks it as an emergency sensor: E-80 has it
            kind = self.kind_of(self._label(entity))
            if kind is None and entity.device_class in MOTION_CLASSES:
                kind = "motion"
            if kind is None:
                return None
            return Source(entity.entity_id, "binary", kind, self._camera_for(entity), area_id)
        if entity.domain == "sensor" and entity.device_class is None:
            kind = self.kind_of(self._label(entity))
            if kind not in ("person", "vehicle", "animal", "package"):
                return None
            return Source(entity.entity_id, "count", kind, self._camera_for(entity), area_id)
        return None

    def _event_kind(self, event_type: str, device_class: str | None) -> str | None:
        if event_type in self.words.ignore_types:
            return None
        found = self.words.event_types.get(event_type) or self.kind_of(event_type)
        if found is None and device_class == "doorbell":
            return "doorbell"
        return found

    def _camera_for(self, entity: Entity) -> str | None:
        """The camera on the same device – a package camera only without a main one – else
        a camera in the same room."""

        def usable(e: Entity) -> bool:
            return e.domain == "camera" and not e.disabled and not e.hidden

        device = entity.device_id
        same = sorted(
            (e for e in self.model.entities.values() if usable(e) and e.device_id == device)
            if device
            else [],
            key=lambda e: (_has(self._label(e), self.words.second_camera), e.entity_id),
        )
        if same:
            return same[0].entity_id
        area = self.model.area_of(entity.entity_id)
        if area is None:
            return None
        here = sorted(e.entity_id for e in self.model.in_area(area.id) if usable(e))
        return here[0] if here else None

    # -- what a camera follows (level 4, E-190) -------------------------------------------

    def follows(self, camera: str) -> list[tuple[str, str]]:
        """(detection, entity) pairs this camera's detections come from."""
        out = []
        for source in sorted(self.sources.values(), key=lambda s: s.entity_id):
            if source.camera != camera:
                continue
            kinds = (source.kind,) if source.kind else source.kinds or (source.default,)
            out += [(k, source.entity_id) for k in kinds if k]
        return out

    # -- a change ------------------------------------------------------------------------

    def detect(self, change: StateChange, now: datetime) -> Detection | None:
        """The detection a change is, or None."""
        source = self.sources.get(change.entity_id)
        new, old = change.new, change.old
        if source is None or new is None or new.state in NOT_A_STATE:
            return None
        if source.form == "event":
            if old is None or old.state == new.state or old.state == "unavailable":
                return None  # no new event: a restart, or the entity coming back
            try:
                fired = datetime.fromisoformat(new.state)
            except ValueError:
                return None
            if fired.tzinfo is None or abs((now - fired).total_seconds()) > (
                self.words.event_fresh_seconds
            ):
                return None  # an old event that Home Assistant shows again
            event_type = _norm(str(new.attributes.get("event_type") or ""))
            if event_type in self.words.ignore_types:
                return None  # NFC, a fingerprint, talking (16.1)
            entity = self.model.entities.get(change.entity_id)
            kind = (
                self._event_kind(event_type, entity.device_class if entity else None)
                if event_type
                else None
            ) or source.default
        elif source.form == "binary":
            if new.state != "on" or old is None or old.state != "off":
                return None
            kind = source.kind
        else:
            if _number(new.state) <= 0 or old is None or _number(old.state) != 0:
                return None
            kind = source.kind
        if kind is None:
            return None
        return Detection(kind, change.entity_id, source.camera, source.area_id, now)


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0
