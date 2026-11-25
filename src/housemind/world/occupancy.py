"""Who is where – anonymously (E-32, H3.1).

Occupancy per area is a probability built from several sensors, so a person who sits still
stays "present" (E-113) and a single sensor never decides alone:

- presence sensors (mmWave) are strong and hold while on; one that works and sees nobody is
  believed at once: motion, a switch, a door or a camera from before then each count only a
  little and never add up to someone; one that is offline counts as none;
- motion sensors (PIR) give a pulse that fades;
- a person using a switch or the HA app in an area is strong evidence;
- a door opening is weak evidence – except in a small room with one door an administrator
  confirmed (E-504, E-505): there the door says who is in. Closed after someone went in is
  someone there, for as long as it stays closed; open and closed again is nobody; an open door
  or one that is offline is not sure;
- in areas where pets may be, while the house sleeps or nobody is home, what only a sensor
  sees – motion, and a presence sensor too, which sees a dog as well as a person – is perhaps
  the pet: it counts much less, so it is no one there and wakes no one (E-90, E-529). A sign of
  a person makes it someone: a camera that sees a person there, a hand on a switch there, new
  movement or a door in a room without pets just before (someone coming down the stairs), or
  a resident who just came home;
- while a robot vacuum runs, and two minutes after, motion is ignored and presence that begins
  then is weak evidence – none at all when nobody is home – so a robot is never a person and
  never "someone in the house" while everyone is away; who was there already stays there;
- a person a camera sees (brain/cameras.py, E-181) counts like motion, but never less in a
  room where pets may be: a camera tells a person from a cat;
- zones of a presence sensor (world/zones.py, E-215) count for their own room; the sensor's
  "someone in range" counts only while none of its zones sees someone, and not for 30 seconds
  after; someone only in bed or only in a part with its own lamps is there, but is no reason
  for the room's other lamps (``for_lamps``). Someone in range but in none of the zones is not
  in a room that has a zone of its own: there for that room, no reason for its light (E-530);
- in a bathroom where someone just was, humidity that climbs fast is a shower: someone is
  there, also when the sensor loses them behind the curtain, until it falls again
  (world/shower.py, E-531) – and a person, never a pet.

Only what senses people in a room counts, as for the room temperature: a camera's motion is a
camera detection, not someone in the room; a hidden, setting or diagnostic sensor and one an
administrator excluded count for nothing. A person action is a real switch: a person in the
app, or a lamp or switch on the wall – not a player that stops by itself, a cover arriving, a
device coming back online or a group following its lamps.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from housemind.core.model import Area, HouseModel, Source, State, StateChange
from housemind.world.shower import SOMEONE_WITHIN, ShowerWatch
from housemind.world.zones import Zones

PRESENCE_CLASSES = frozenset({"presence", "occupancy"})
MOTION_CLASSES = frozenset({"motion"})
OPENING_CLASSES = frozenset({"door", "opening"})
ZONE_QUIET_S = 30.0  # a device's "someone in range" waits this long after one of its zones
ZONE_SETTLE_S = 1.0  # … and this long after it came on, so its zones can report first (E-443)
ROBOT_AFTER_S = 120.0  # a robot vacuum still drives around this long after it stopped
ROBOT_RUNNING = frozenset({"cleaning", "returning"})
# A room where pets may be, while the house sleeps or nobody is home (E-529): how long a sign
# of a person counts – someone seen by a camera there, or new movement or a door in a room
# without pets (coming down the stairs) – and a hand on a switch there, or a resident home.
PET_PERSON_S = 120.0
PET_HAND_S = 900.0
PET_ARRIVAL_S = 300.0
OFFLINE = frozenset({"unavailable", "unknown"})
# A small room with one door (E-505): where its door leaves it. "in" and "empty" are sure;
# "entering", "leaving" (the door is open) and "unsure" are not.
BOX_SURE = frozenset({"in", "empty"})
BOX_OPENED = {"empty": "entering", "in": "leaving", "entering": "entering", "leaving": "leaving"}
BOX_CLOSED = {"entering": "in", "leaving": "empty", "unsure": "in", "in": "in", "empty": "empty"}


@dataclass(slots=True)
class AreaSignals:
    presence_on: set[str] = field(default_factory=set)
    has_presence_sensor: bool = False
    presence_ids: set[str] = field(default_factory=set)  # its presence sensors and zones
    has_motion_sensor: bool = False
    last_motion: datetime | None = None
    motion_on: set[str] = field(default_factory=set)
    motion_since: dict[str, datetime] = field(default_factory=dict)  # when each came on
    # When a presence sensor last stopped seeing someone: motion that comes on after it is
    # someone coming in (E-443); the wait before the lamps go off starts here (E-445).
    presence_off: datetime | None = None
    last_person_action: datetime | None = None
    last_door: datetime | None = None
    general_on: set[str] = field(default_factory=set)  # "someone in range" of a zone device
    last_seen: datetime | None = None  # a camera saw a person here (E-181)
    robot_maybe: set[str] = field(default_factory=set)  # began while a robot vacuum drove
    # When a sensor that senses people here last came on – motion that starts, a presence
    # sensor or a zone that begins to see someone (E-529: someone walking from here).
    last_start: datetime | None = None
    # A room where pets may be (E-529): until when what its sensors see is a person's.
    person_until: datetime | None = None
    # A small room with one door (E-505): what its door says, and since when.
    box: str | None = None
    box_since: datetime | None = None


@dataclass(frozen=True, slots=True)
class AreaOccupancy:
    area_id: str
    probability: float
    occupied: bool
    still: bool  # present without recent motion (sitting still)
    last_activity: datetime | None
    # Someone the room's own lamps are for: not only in bed, not only in a part of the room
    # with its own lamps (E-215). None: the same as occupied.
    for_lamps: bool | None = None
    zones: tuple[ZoneNow, ...] = ()
    # A presence sensor watches the room: it says at once when nobody is there – and may blink
    # for a few seconds (E-268). Without one, being there fades out slowly by itself.
    presence: bool = False
    # A sensor can tell that nobody is here; a door or a switch only says someone was (E-462)
    # – unless it is a small room with one door whose door says so now (E-505).
    sensed: bool = True
    box: str | None = None  # in a small room with one door: what its door says (E-505)


@dataclass(frozen=True, slots=True)
class ZoneNow:
    """One zone of a presence sensor, now: for the room view and the lamps of a part."""

    entity_id: str
    name: str
    on: bool
    since: datetime | None  # when it last changed
    part: bool
    bed: bool
    lamps: tuple[str, ...]
    sensor_area: str


def _held(signals: AreaSignals) -> set[str]:
    return signals.presence_on | signals.general_on | signals.motion_on | signals.robot_maybe


def _fade(since: datetime | None, now: datetime, half_life_s: float) -> float:
    if since is None:
        return 0.0
    age = max(0.0, (now - since).total_seconds())
    return math.pow(0.5, age / half_life_s)


class OccupancyModel:
    def __init__(
        self,
        model: HouseModel,
        pet_areas: set[str] | None = None,
        motion_half_life_s: float = 600.0,
    ) -> None:
        self.model = model
        self.pet_areas = set(pet_areas or ())
        self.motion_half_life_s = motion_half_life_s
        self.areas: dict[str, AreaSignals] = {}
        self.excluded: set[str] = set()  # excluded by an administrator (level 3/4)
        # Sensors that sense no people by their function, from AI or a person (E-431).
        self.not_people: set[str] = set()
        self.ignored: set[str] = set()  # sensors that do not sense people in a room
        self.zones = Zones()  # set by the runtime (world/zones.py)
        self.showers = ShowerWatch()  # set by the world (world/shower.py, E-531)
        self._robots: set[str] = set()  # robot vacuums driving now
        self._robot_stopped: datetime | None = None
        self._zone_changed: dict[str, datetime] = {}
        self._device_zone_at: dict[str, datetime] = {}  # device → last time a zone was on
        self._general_since: dict[str, datetime] = {}
        self._offline: set[str] = set()  # presence sensors and box doors that are offline now
        # Small rooms with one door an administrator confirmed (E-504): room → its door.
        self.boxes: dict[str, str] = {}
        self.index()

    def robot_driving(self, now: datetime) -> bool:
        """A robot vacuum drives, or stopped less than two minutes ago."""
        return bool(self._robots) or (
            self._robot_stopped is not None
            and (now - self._robot_stopped).total_seconds() < ROBOT_AFTER_S
        )

    def area_for(self, entity_id: str) -> Area | None:
        """The room a sensor senses: a zone's own room, else its room in HA."""
        zone = self.zones.zones.get(entity_id)
        if zone is not None:
            return self.model.areas.get(zone.area_id)
        return self.model.area_of(entity_id)

    def _zone_active(self, device_id: str, now: datetime) -> bool:
        """One of the device's zones sees someone, or did so less than 30 seconds ago."""
        if any(
            z.entity_id in self.areas.get(z.area_id, AreaSignals()).presence_on
            or z.entity_id in self.areas.get(z.area_id, AreaSignals()).robot_maybe
            for z in self.zones.of_device(device_id)
        ):
            return True
        at = self._device_zone_at.get(device_id)
        return at is not None and (now - at).total_seconds() < ZONE_QUIET_S

    def index(self) -> None:
        """Which areas have a presence or motion sensor – again after the house changed in
        HA; what each area saw so far stays."""
        for area_id in self.model.areas:
            self.areas.setdefault(area_id, AreaSignals())
        for signals in self.areas.values():
            signals.has_presence_sensor = signals.has_motion_sensor = False
            signals.presence_ids = set()
        cameras = {e.device_id for e in self.model.entities.values() if e.domain == "camera"} - {
            None
        }
        self.ignored = {
            e.entity_id
            for e in self.model.entities.values()
            if e.hidden
            or e.disabled
            or e.entity_category
            or e.entity_id in self.excluded
            or e.entity_id in self.not_people
            or e.entity_id in self.model.groups
            or (e.domain == "binary_sensor" and e.device_id in cameras)
        }
        for entity in self.model.entities.values():
            if entity.entity_id in self.ignored:
                continue
            area = self.area_for(entity.entity_id)
            if area is not None and (
                entity.entity_id in self.zones.zones
                or (entity.domain == "binary_sensor" and entity.device_class in PRESENCE_CLASSES)
            ):
                signals = self.areas.setdefault(area.id, AreaSignals())
                signals.has_presence_sensor = True
                signals.presence_ids.add(entity.entity_id)
            elif (
                area is not None
                and entity.domain == "binary_sensor"
                and entity.device_class in MOTION_CLASSES
            ):
                self.areas.setdefault(area.id, AreaSignals()).has_motion_sensor = True

    def _watched(self, signals: AreaSignals) -> bool:
        """A presence sensor that works watches the room: when it sees nobody, nobody is
        there."""
        return any(e not in self._offline for e in signals.presence_ids)

    def _mark_offline(self, entity_id: str, state: str) -> None:
        if state in OFFLINE:
            self._offline.add(entity_id)
        else:
            self._offline.discard(entity_id)

    def sensed(self, area_id: str) -> bool:
        """Whether a sensor can tell that this area is empty (not only fading evidence): one
        that sees people, or the working door of a small room with one door (E-505)."""
        return self.sees_people(area_id) or self._box_door_works(area_id)

    def sees_people(self, area_id: str) -> bool:
        """A presence or motion sensor watches this area – its own, or one in the next room."""
        signals = self.areas.get(area_id)
        return signals is not None and (
            signals.has_presence_sensor
            or signals.has_motion_sensor
            or bool(self.zones.in_area(area_id))  # a sensor in the next room that watches it
        )

    def _box_door_works(self, area_id: str) -> bool:
        door = self.boxes.get(area_id)
        return door is not None and door not in self._offline and door not in self.ignored

    def set_boxes(
        self, boxes: Mapping[str, str], states: Mapping[str, State], now: datetime
    ) -> None:
        """The small rooms with one door an administrator confirmed (E-504). A new one starts
        from what its door and lamps say now; one that stays keeps what it knew."""
        before = self.boxes
        self.boxes = dict(boxes)
        for area_id, signals in self.areas.items():
            if area_id not in self.boxes:
                signals.box = signals.box_since = None
        for area_id, door in self.boxes.items():
            signals = self.areas.setdefault(area_id, AreaSignals())
            if before.get(area_id) != door or signals.box is None:
                self._start_box(area_id, door, states, now)

    def _start_box(
        self, area_id: str, door: str, states: Mapping[str, State], now: datetime
    ) -> None:
        """Where a small room with one door starts (E-505): its door open is not sure; closed
        with one of its lamps on is someone in (whoever does not know, leaves the light on);
        closed and dark is nobody."""
        signals = self.areas.setdefault(area_id, AreaSignals())
        state = states.get(door)
        self._mark_offline(door, state.state if state is not None else "unknown")
        if state is None or state.state != "off":
            box = "unsure"
        else:
            lit = any(
                e.domain == "light"
                and (area := self.model.area_of(e.entity_id)) is not None
                and area.id == area_id
                and getattr(states.get(e.entity_id), "state", None) == "on"
                for e in self.model.entities.values()
            )
            box = "in" if lit else "empty"
        signals.box, signals.box_since = box, now

    def _box_door(self, signals: AreaSignals, state: str, now: datetime) -> None:
        """The door of a small room with one door moved (E-505)."""
        if state in OFFLINE:
            box = "unsure"
        elif state == "on":
            box = BOX_OPENED.get(signals.box or "unsure", "unsure")
        else:
            box = BOX_CLOSED.get(signals.box or "unsure", "in")
        if box != signals.box:
            signals.box, signals.box_since = box, now

    def prime(self, states: Mapping[str, State], now: datetime) -> None:
        """What the sensors see right now, at the start and after the house was read again:
        someone already in a room is there before a sensor changes, so a lamp switched off
        there does not count against learning. On now counts from now; what went off while
        HouseMind did not listen is gone; what it already knew stays as it was."""
        for entity_id, state in states.items():
            entity = self.model.entities.get(entity_id)
            if entity is not None and entity.domain == "vacuum":
                self.update(StateChange(entity_id, None, state), now)
        for entity_id, state in states.items():
            entity = self.model.entities.get(entity_id)
            if entity is None or not (
                entity_id in self.zones.zones
                or entity_id in self.zones.general
                or (
                    entity.domain == "binary_sensor"
                    and entity.device_class in PRESENCE_CLASSES | MOTION_CLASSES
                )
            ):
                continue
            self._mark_offline(entity_id, state.state)
            area = self.area_for(entity_id)
            here = self.areas.get(area.id) if area is not None else None
            on = state.state == "on" and entity_id not in self.ignored
            if on and here is not None and entity_id in _held(here):
                continue
            for signals in self.areas.values():
                for held in (
                    signals.presence_on,
                    signals.general_on,
                    signals.motion_on,
                    signals.robot_maybe,
                ):
                    held.discard(entity_id)
            if on:
                self.update(StateChange(entity_id, None, state), now)
                if entity_id in self.zones.general:  # its zones are known already
                    self._general_since[entity_id] = now - timedelta(seconds=ZONE_SETTLE_S)

    def seen(self, area_id: str, now: datetime, person: bool = True) -> None:
        """A camera saw someone in this room (E-181) – or, ``person`` False, only its own
        motion inside, which counts as ordinary motion (16.2)."""
        signals = self.areas.setdefault(area_id, AreaSignals())
        if person:
            signals.last_seen = now
        elif not self.robot_driving(now):
            signals.last_motion = now

    def update(self, change: StateChange, now: datetime) -> None:
        entity = self.model.entities.get(change.entity_id)
        if entity is None or change.new is None:
            return
        if entity.entity_id in self.ignored:
            return
        bathroom = self.showers.sensors.get(entity.entity_id)
        if bathroom is not None:  # the humidity of a bathroom (E-531)
            someone = self._someone_was(bathroom, now)
            self.showers.see(entity.entity_id, change.new.state, now, someone)
            return
        if entity.domain == "vacuum":
            if change.new.state in ROBOT_RUNNING:
                self._robots.add(entity.entity_id)
            elif entity.entity_id in self._robots:
                self._robots.discard(entity.entity_id)
                if not self._robots:
                    self._robot_stopped = now
            return
        area = self.area_for(change.entity_id)
        if area is None:
            return
        signals = self.areas.setdefault(area.id, AreaSignals())
        on = change.new.state == "on"
        eid = entity.entity_id
        zone = self.zones.zones.get(eid)
        general = self.zones.general.get(eid)
        if (
            zone is not None
            or general is not None
            or (entity.domain == "binary_sensor" and entity.device_class in PRESENCE_CLASSES)
        ):
            self._mark_offline(eid, change.new.state)
        if zone is not None or general is not None:
            if zone is not None:
                self._zone_changed[eid] = now
                self._device_zone_at[zone.device_id] = now
            target = signals.presence_on if zone is not None else signals.general_on
            if not on:
                if eid in target:
                    self._seen_until(signals, now)
                target.discard(eid)
                signals.robot_maybe.discard(eid)
            elif self._robot_seen(signals, now):
                signals.robot_maybe.add(eid)
            else:
                target.add(eid)
                if zone is None or not zone.bed:  # turning over in bed is no one walking
                    self._started(area.id, signals, now)
                if general is not None:
                    self._general_since[eid] = now
                else:
                    signals.last_motion = now
            return
        if entity.domain == "binary_sensor" and entity.device_class in PRESENCE_CLASSES:
            if not on:
                if eid in signals.presence_on:
                    self._seen_until(signals, now)
                signals.presence_on.discard(eid)
                signals.robot_maybe.discard(eid)
            elif self._robot_seen(signals, now):
                signals.robot_maybe.add(eid)  # perhaps only the robot
            else:
                if eid not in signals.presence_on:
                    self._started(area.id, signals, now)
                signals.presence_on.add(eid)
                signals.last_motion = now
        elif entity.domain == "binary_sensor" and entity.device_class in MOTION_CLASSES:
            if self.robot_driving(now):
                return
            if on:
                if entity.entity_id not in signals.motion_on:
                    signals.motion_since[entity.entity_id] = now
                    self._started(area.id, signals, now)
                signals.motion_on.add(entity.entity_id)
                signals.last_motion = now
            else:
                signals.motion_on.discard(entity.entity_id)
                signals.motion_since.pop(entity.entity_id, None)
        elif entity.domain == "binary_sensor" and entity.device_class in OPENING_CLASSES:
            if on:
                signals.last_door = now
            if self.boxes.get(area.id) == eid:  # the door of a small room with one door
                self._mark_offline(eid, change.new.state)
                self._box_door(signals, change.new.state, now)
        elif self._person_switched(change, entity.domain):
            signals.last_person_action = now  # really switched, not only details reported
            if signals.box == "empty" and change.new.state == "on":
                # A lamp put on by hand while the door is shut: someone is in (E-505).
                signals.box, signals.box_since = "in", now

    def _started(self, area_id: str, signals: AreaSignals, now: datetime) -> None:
        """A sensor here began to see someone. In a room where pets may be, while what its
        sensors see is a person's, someone still walking there keeps it so (E-529)."""
        signals.last_start = now
        if (
            area_id in self.pet_areas
            and signals.person_until is not None
            and now <= signals.person_until
        ):
            signals.person_until = now + timedelta(seconds=PET_PERSON_S)

    def pets_only(
        self,
        area_id: str,
        now: datetime,
        quiet: bool,
        arrived: datetime | None = None,
        indoors: frozenset[str] | None = None,
    ) -> bool:
        """Whether what the sensors see in this room is perhaps only the pet (E-90, E-529): a
        room where pets may be, while the house sleeps or nobody is home (``quiet``), with no
        sign of a person. A sign makes the room a person's for two minutes, and longer while
        someone keeps walking there."""
        if area_id not in self.pet_areas or not quiet:
            return False
        signals = self.areas.get(area_id)
        if signals is None:
            return False
        if self._person_sign(area_id, signals, now, arrived, indoors):
            until = now + timedelta(seconds=PET_PERSON_S)
            if signals.person_until is None or signals.person_until < until:
                signals.person_until = until
        return signals.person_until is None or now > signals.person_until

    def _someone_was(self, area_id: str, now: datetime) -> bool:
        """Someone is in this room, or was a little while ago (E-531: a shower needs someone
        who went in)."""
        s = self.areas.get(area_id)
        if s is None:
            return False
        if s.presence_on or s.motion_on or s.box == "in":
            return True
        return any(
            at is not None and now - at <= SOMEONE_WITHIN
            for at in (s.last_motion, s.last_start, s.last_door, s.last_person_action, s.last_seen)
        )

    def _person_sign(
        self,
        area_id: str,
        signals: AreaSignals,
        now: datetime,
        arrived: datetime | None,
        indoors: frozenset[str] | None,
    ) -> bool:
        """A sign that a person is here, not a pet (E-529): a camera saw a person here, a person
        switched something here, a resident just came home, or someone just began to move – or
        opened a door – in a room where no pets are (coming down the stairs, out of bed)."""

        def within(at: datetime | None, seconds: float) -> bool:
            return at is not None and (now - at).total_seconds() <= seconds

        if within(signals.last_seen, PET_PERSON_S) or within(
            signals.last_person_action, PET_HAND_S
        ):
            return True
        if self.showers.showering(area_id, now):
            return True  # no dog takes a shower (E-531)
        if within(arrived, PET_ARRIVAL_S):
            return True
        return any(
            within(other.last_start, PET_PERSON_S) or within(other.last_door, PET_PERSON_S)
            for other_id, other in self.areas.items()
            if other_id != area_id
            and other_id not in self.pet_areas
            and (indoors is None or other_id in indoors)
        )

    @staticmethod
    def _seen_until(signals: AreaSignals, now: datetime) -> None:
        """A presence sensor saw someone until now: that is their last moment here, also after
        an hour without motion (E-445) – the wait before the lamps go off starts now."""
        signals.last_motion = now
        signals.presence_off = now

    @staticmethod
    def _person_switched(change: StateChange, domain: str) -> bool:
        old = change.old.state if change.old is not None else None
        new = change.new.state if change.new is not None else None
        if old is None or new is None or old == new or old in ("unavailable", "unknown"):
            return False  # nothing switched, or a device came back online
        if change.source is Source.PERSON:
            return domain in ("light", "switch", "cover", "media_player", "fan")
        # Without a person in HA: only a lamp or a switch on the wall – a player stopping, a
        # cover arriving after its run or a timer switching is the device itself.
        return (
            change.source is Source.PHYSICAL
            and domain in ("light", "switch")
            and new
            in (
                "on",
                "off",
            )
        )

    def _robot_seen(self, signals: AreaSignals, now: datetime) -> bool:
        """Presence that begins while a robot vacuum drives, in a room where nobody was:
        perhaps only the robot."""
        if not self.robot_driving(now):
            return False
        person = (
            signals.last_person_action is not None
            and (now - signals.last_person_action).total_seconds() < 900
        )
        return not (signals.presence_on or signals.general_on or person)

    def _settle(self, now: datetime) -> None:
        """The robot is gone: who is still seen, is someone."""
        if self.robot_driving(now):
            return
        for signals in self.areas.values():
            for eid in signals.robot_maybe:
                target = signals.general_on if eid in self.zones.general else signals.presence_on
                target.add(eid)
                signals.last_motion = now
            signals.robot_maybe.clear()

    def evaluate(
        self,
        now: datetime,
        asleep: bool = False,
        nobody_home: bool = False,
        arrived: datetime | None = None,
        indoors: frozenset[str] | None = None,
    ) -> dict[str, AreaOccupancy]:
        """Who is where now. ``asleep``: the house sleeps; ``arrived``: when a resident last
        came home; ``indoors``: the rooms where people live – for a room where pets may be
        (E-529)."""
        self._settle(now)
        result: dict[str, AreaOccupancy] = {}
        own_lamps = {z.entity_id for z in self.zones.zones.values() if z.bed or z.lamps}
        # A device with a zone for the room itself: someone in range but in none of its zones is
        # somewhere else – there for the room (its lamps stay on), not for its light (E-530).
        room_zone = {(z.area_id, z.device_id) for z in self.zones.zones.values() if not z.part}
        for area_id, s in self.areas.items():
            general = {
                g
                for g in s.general_on
                if not self._zone_active(self.zones.general[g], now)
                and (now - self._general_since.get(g, now)).total_seconds() >= ZONE_SETTLE_S
            }
            if general:
                s.last_motion = now  # someone in range, in none of its zones
            # Perhaps only the pet (E-90, E-529): what the sensors see here counts much less –
            # also a presence sensor, which sees a dog as well as a person.
            pets = self.pets_only(area_id, now, asleep or nobody_home, arrived, indoors)
            presence = set() if pets else s.presence_on | general
            in_range = {g for g in general if (area_id, self.zones.general[g]) not in room_zone}
            for_room = set() if pets else (s.presence_on - own_lamps) | in_range
            motion = (
                0.9 if s.motion_on else 0.85 * _fade(s.last_motion, now, self.motion_half_life_s)
            )
            person = 0.95 * _fade(s.last_person_action, now, 900.0)
            door = 0.4 * _fade(s.last_door, now, 120.0)
            robot = 0.0 if nobody_home or not s.robot_maybe else 0.3  # perhaps the robot
            seen = 0.9 * _fade(s.last_seen, now, self.motion_half_life_s)
            # A small room with one door whose door says it now (E-505): "empty" is believed
            # like a presence sensor that sees nobody; "in" is someone, however long.
            box = s.box if area_id in self.boxes and self._box_door_works(area_id) else None
            watched = self._watched(s)
            believed = watched or box == "empty"  # "nobody" is believed at once
            if believed:
                # A working presence sensor that says "nobody" is believed at once: motion, a
                # switch, a door or a camera from before each count only a little, and never
                # add up to someone (a lamp switched off on the way out, a door behind you).
                # Motion that came on after it said "nobody" is someone coming in, sooner than
                # the presence sensor sees them (E-443) – for as long as it is on.
                fresh = any(
                    s.presence_off is None or since > s.presence_off
                    for since in s.motion_since.values()
                )
                if pets:
                    fresh = False  # perhaps the cat
                other = max(
                    0.9 if fresh else 0.35 * motion, 0.35 * person, door, robot, 0.35 * seen
                )
            else:
                other = 1 - (1 - motion) * (1 - person) * (1 - door) * (1 - robot)
                if pets:
                    other *= 0.35
                other = max(other, seen)  # a camera that saw a person: no cat (E-90)
            # Someone in the shower, out of the sensor's sight (E-531).
            showering = self.showers.showering(area_id, now)
            p = 0.97 if presence or box == "in" or showering else other
            p_room = 0.97 if for_room or box == "in" or showering else other
            gone = s.box_since if box == "empty" else None  # the door shut behind them
            last = max(
                (
                    t
                    for t in (
                        s.last_motion,
                        s.last_person_action,
                        s.last_door,
                        s.last_seen,
                        gone,
                        now if showering else None,
                    )
                    if t is not None
                ),
                default=None,
            )
            still = bool(presence) and (
                s.last_motion is None or (now - s.last_motion).total_seconds() > 120
            )
            zones = tuple(
                ZoneNow(
                    z.entity_id,
                    z.name,
                    z.entity_id in s.presence_on,
                    self._zone_changed.get(z.entity_id),
                    z.part,
                    z.bed,
                    z.lamps,
                    z.sensor_area,
                )
                for z in self.zones.in_area(area_id)
                if z.entity_id not in self.ignored
            )
            result[area_id] = AreaOccupancy(
                area_id,
                round(p, 3),
                p >= 0.5,
                still,
                last,
                p_room >= 0.5,
                zones,
                watched or bool(general) or bool(self.zones.in_area(area_id)),
                self.sees_people(area_id) or box in BOX_SURE,
                s.box if area_id in self.boxes else None,
            )
        return result
