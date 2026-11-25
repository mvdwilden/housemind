"""The world view (H3.1): the single source of "what is going on now" for every decision."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from housemind.core.model import Entity, HouseModel, Source, State, StateChange
from housemind.knowledge.area_roles import PASSING_ROLES, SLEEPING_ROLES
from housemind.knowledge.roles import EntityRole, entity_role
from housemind.world.calendar import DayType, day_type, is_dark, time_bucket
from housemind.world.house_mode import HouseMode, HouseModeMachine, ModeInputs
from housemind.world.occupancy import AreaOccupancy, OccupancyModel
from housemind.world.shower import shower_sensors
from housemind.world.sleep import SleepView, SleepWatch
from housemind.world.sun import elevation

if TYPE_CHECKING:
    from housemind.brain.rhythm import Part

# A light sensor on these devices measures the device's own surroundings: a tablet's screen, a
# camera's night vision, a lamp's daylight sensor.
LUX_NOT_ROOM = frozenset({"camera", "light", "media_player", "switch", "climate"})
# A lamp in a bedroom that has been on this long without a break stands on day and night – a
# cupboard's lighting, found in the real house – and is no sign that someone there is awake.
ALWAYS_ON = timedelta(hours=12)
# Movement in a room where pets may be does not wake the house between its usual bedtime and
# half an hour before it usually wakes up – the clock's 23:00 and 06:00 while those are not
# learned (E-529): then it is perhaps the dog. Before bedtime people are still up.
PET_WAKE_EARLY = timedelta(minutes=30)
PET_WAKE_CLOCK = time(6)
PET_BED_CLOCK = time(23)
# A light on a device that senses people or listens is that device's own LED, not a lamp.
SENSING = frozenset({"motion", "presence", "occupancy"})


@dataclass(frozen=True, slots=True)
class WorldState:
    now: datetime
    local: datetime
    mode: HouseMode
    day_type: DayType
    bucket: str
    residents_home: int
    occupancy: dict[str, AreaOccupancy]
    dark: dict[str, bool]
    guests_unconfirmed: bool = False
    # Dark outside – for the covers: a closed shutter keeps a room dark, lamps keep it light,
    # so a room's own light level cannot decide when covers move.
    outside_dark: bool = False
    suggest_vacation: bool = False
    # The height of the sun (degrees) and each room's light level (lux) – what learning when
    # it is dark enough in a room looks at (E-268). None: not known.
    sun: float | None = None
    lux: dict[str, float | None] = field(default_factory=dict)
    # Since when the house is in this mode (E-315: lower only after 24 hours away).
    mode_since: datetime | None = None
    # The part of the day from the house's own rhythm (E-455): night, morning, day or evening,
    # since when and how HouseMind knows it; ``bedtime``: after the usual bedtime, before the
    # morning. Empty: not known (the history, a test) – then the clock decides, as before.
    part: str = ""
    part_since: datetime | None = None
    part_how: str = ""
    bedtime: bool = False
    # Who still sleeps in each bedroom (E-482, E-483, design 71.3), and when the house woke up
    # last – the start of its morning. Empty: not known (the history, a test).
    sleep: dict[str, SleepView] = field(default_factory=dict)
    woke: datetime | None = None
    # The bathrooms where someone showers now (E-531): the fan goes on at once.
    showers: frozenset[str] = frozenset()

    def occupied(self, area_id: str | None) -> bool:
        info = self.occupancy.get(area_id) if area_id else None
        return info is not None and info.occupied

    def context_key(self) -> str:
        """Learning context (B-08): day type × 30-minute slot."""
        return f"{self.day_type.value}|{self.bucket}"


@dataclass(slots=True)
class World:
    model: HouseModel
    time_zone: str
    country: str | None
    area_roles: dict[str, str]
    pet_areas: set[str] = field(default_factory=set)
    states: dict[str, State] = field(default_factory=dict)
    occupancy: OccupancyModel = field(init=False)
    modes: HouseModeMachine = field(default_factory=HouseModeMachine)
    _living_activity: datetime | None = None
    # When a resident last came home, and how many were home at the last round: in a room where
    # pets may be, someone who just came home is a person, not the dog (E-529).
    _arrived: datetime | None = None
    _residents_seen: int | None = None
    # The light sensors of each room, found once (S1, also replaying the history, E-257);
    # cleared when the house changes. None: looked up each time.
    lux_cache: dict[str, list[str]] | None = field(default_factory=dict)
    _deviant_days: set[str] = field(default_factory=set)
    # Where the house is (latitude, longitude): to work out the sun when Home Assistant does
    # not say it – as in its history, which keeps no height of the sun (E-268).
    location: tuple[float, float] | None = None
    # "The alarm" (brain/security.py): its state counts in the house mode (E-188).
    alarm_entity: str | None = None
    # When each lamp in a bedroom went on, kept over a restart; a moment 'unavailable' (a radio
    # network that reconnects) is no switching off (E-88).
    lamps_on_since: dict[str, datetime] = field(default_factory=dict)
    # What a person said where the rules doubted or HouseMind derived it (B-359, E-203): a
    # light that is (True) or is not (False) a device's status light, and the sensors they
    # confirmed measure light. Read from the house knowledge in memory, never waited for.
    status_leds: dict[str, bool] = field(default_factory=dict)
    light_sensors: set[str] = field(default_factory=set)
    # Words for a device's own light in its name ("LED", "ring", "status"), from every language
    # file (daily_words.status_light).
    status_words: tuple[str, ...] = ()
    # The lights that are a device's status light now – the rules' guess, or what a person
    # said (``index_status_leds``). No lamp: the lighting, the room's light, the bedtime check
    # and the question about a lamp on for long leave them alone (E-426).
    status_led_ids: set[str] = field(default_factory=set)
    # Every entity's function (E-427, design 51) and where it came from: "rules", "ai",
    # "behaviour" or "person" (a person said it, or confirmed it). Set by the understanding
    # part after every new picture of the house and every answer; the core only reads it.
    functions: dict[str, str] = field(default_factory=dict)
    function_sources: dict[str, str] = field(default_factory=dict)
    functions_open: set[str] = field(default_factory=set)  # the rules cannot tell (51.4)
    # The part of the day from the learned rhythm (E-455, ``Rhythm.part``), set by the runtime;
    # None: the clock. ``woke``: when the house woke up last – the start of its morning.
    part_of: Callable[..., Part] | None = None
    # The house's usual wake-up time for a day and its type (the rhythm), set by the runtime;
    # None: not learned, or no rhythm – then the clock's (E-529).
    wake_at: Callable[[date, DayType], time | None] | None = None
    # This evening's usual bedtime (the rhythm's ``bed_tonight``), set by the runtime (E-529).
    bed_at: Callable[[datetime, DayType, DayType], datetime | None] | None = None
    woke: datetime | None = None
    _seen_mode: HouseMode | None = None
    # Who still sleeps in each bedroom (E-483): its bed words come from every language file.
    sleep_watch: SleepWatch = field(default_factory=SleepWatch)

    def __post_init__(self) -> None:
        self.occupancy = OccupancyModel(self.model, self.pet_areas)

    def index_sleep(self) -> None:
        """Which sign each bedroom has of someone sleeping – after the zones are sorted; and
        which humidity sensors tell a shower in a bathroom (E-531)."""
        self.sleep_watch.index(
            self.model,
            self.occupancy.zones,
            [a for a in self.model.areas if self.area_roles.get(a) in SLEEPING_ROLES],
            {a: s.presence_ids for a, s in self.occupancy.areas.items()},
            self.occupancy.ignored,
        )
        bathrooms = [a for a in self.model.areas if self.area_roles.get(a) == "bathroom"]
        self.occupancy.showers.index(
            shower_sensors(self.model, bathrooms, self.occupancy.excluded | self.occupancy.ignored)
        )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.time_zone)

    def load_states(self, states: dict[str, State]) -> None:
        self.states = dict(states)

    def prime(self, now: datetime) -> None:
        """Who is where from what the sensors see now – after the states and the zones are
        read, at the start and after the house was read again."""
        self.occupancy.prime(self.states, now)

    def on_change(self, change: StateChange, now: datetime) -> None:
        if change.new is not None:
            self.states[change.entity_id] = change.new
        else:
            self.states.pop(change.entity_id, None)
        self.occupancy.update(change, now)
        area = self.occupancy.area_for(change.entity_id)  # a zone: its own room
        if (
            area is not None
            and self.indoors(area.id)
            and (self.area_roles.get(area.id) not in SLEEPING_ROLES)
        ):
            entity = self.model.entities.get(change.entity_id)
            # The dog in the hall at five in the morning wakes no one (E-529).
            perhaps_pet = not self._pets_may_wake(now) and self.occupancy.pets_only(
                area.id, now, self.modes.mode is HouseMode.SLEEP, self._arrived, self._indoor()
            )
            switched_on = (
                change.new is not None
                and change.new.state == "on"
                and change.old is not None
                and change.old.state == "off"
            )
            if (
                entity
                and entity.entity_id not in self.occupancy.ignored
                and switched_on
                and (
                    # A lamp someone switched on – not HouseMind's own night light or an
                    # automation, which must not wake the house at 05:00.
                    (entity.domain == "light" and change.source in (Source.PERSON, Source.PHYSICAL))
                    or (
                        entity.domain == "binary_sensor"
                        and entity.device_class in ("motion", "presence", "occupancy")
                        and not self.occupancy.robot_driving(now)  # the robot wakes no one
                        and not perhaps_pet
                    )
                )
            ):
                self._living_activity = now

    def _pets_may_wake(self, now: datetime) -> bool:
        """Whether movement in a room where pets may be wakes the house now (E-529): not between
        the usual bedtime and half an hour before the house usually wakes up – then it is
        perhaps the dog. Before bedtime people are still up; in the morning they get up – also
        where every room downstairs has pets."""
        local = now.astimezone(self.tz)
        day = local.date()
        today, tomorrow = self._day_type(day), self._day_type(day + timedelta(days=1))
        usual = self.wake_at(day, today) if self.wake_at else None
        morning = datetime.combine(day, usual or PET_WAKE_CLOCK, tzinfo=local.tzinfo)
        bed = self.bed_at(local, today, tomorrow) if self.bed_at else None
        if bed is None:  # the clock's: this evening, or last evening after midnight
            on = day if local.hour >= 12 else day - timedelta(days=1)
            bed = datetime.combine(on, PET_BED_CLOCK, tzinfo=local.tzinfo)
        if local.hour >= 12:
            return local < bed
        return not (bed <= local < morning - PET_WAKE_EARLY)

    def _indoor(self) -> frozenset[str]:
        """The rooms where people live (``indoors``)."""
        return frozenset(a for a in self.model.areas if self.indoors(a))

    def indoors(self, area_id: str | None) -> bool:
        """A room where people live: not the garden, the driveway or the garage. Someone
        there (a cat on camera, the postman) is no one in the house."""
        return area_id is not None and self.area_roles.get(area_id) not in ("outside", "garage")

    def _role_of(self, entity_id: str) -> str | None:
        area = self.model.area_of(entity_id)
        return self.area_roles.get(area.id) if area is not None else None

    def residents_home(self) -> int:
        return sum(
            1 for e, s in self.states.items() if e.startswith("person.") and s.state == "home"
        )

    def _area_lux(self, area_id: str) -> float | None:
        """The light in a room from what measures the room: also sensors that take their
        room from their device (most do), the first that has a value; never a tablet's, a
        camera's or a lamp's own light sensor."""
        if self.lux_cache is not None:
            if area_id not in self.lux_cache:
                self.lux_cache[area_id] = self._lux_sensors(area_id)
            for entity_id in self.lux_cache[area_id]:
                state = self.states.get(entity_id)
                try:
                    return float(state.state) if state else None
                except ValueError:
                    continue
            return None
        for entity in self.model.in_area(area_id):
            if not self._measures_light(entity) or self._on_a_device_that_acts(entity):
                continue
            state = self.states.get(entity.entity_id)
            try:
                return float(state.state) if state else None
            except ValueError:
                continue
        return None

    def _lux_sensors(self, area_id: str) -> list[str]:
        return [
            entity.entity_id
            for entity in self.model.in_area(area_id)
            if self._measures_light(entity) and not self._on_a_device_that_acts(entity)
        ]

    def _bedroom_awake(self, now: datetime) -> bool:
        """A lamp on in a bedroom that says someone there is awake (E-88). Not a sensor's or a
        speaker's own LED, not a setting, and not a lamp that has been on for more than twelve
        hours: in the real house these kept the house from ever going to sleep."""
        awake = False
        for entity_id, state in self.states.items():
            if not entity_id.startswith("light.") or entity_id in self.model.groups:
                continue
            entity = self.model.entities.get(entity_id)
            if entity is None or self._role_of(entity_id) not in SLEEPING_ROLES:
                continue
            if entity_role(entity) is not EntityRole.MAIN or entity_id in self.status_led_ids:
                continue
            if state.state == "off":
                self.lamps_on_since.pop(entity_id, None)
            elif state.state == "on":
                since = self.lamps_on_since.setdefault(entity_id, state.last_changed)
                awake = awake or now - since < ALWAYS_ON
        return awake

    def _measures_light(self, entity: Entity) -> bool:
        """A light sensor: of that class, or one a person confirmed follows the daylight."""
        if entity.domain != "sensor" or entity.hidden or entity.entity_category:
            return False
        return entity.device_class == "illuminance" or entity.entity_id in self.light_sensors

    def _status_led(self, entity: Entity) -> bool:
        """A presence sensor's LED or a voice assistant's ring: HA shows it as a light. Where
        a person said what it is (B-359), that holds; until then the rules' guess."""
        decided = self.status_leds.get(entity.entity_id)
        if decided is not None:
            return decided
        return self._on_a_sensing_device(entity) and self._named_status_light(entity)

    def _named_status_light(self, entity: Entity) -> bool:
        """Its name says it is a device's own light: "LED", "LED Ring", "Status LED" – not a
        lamp that also senses, which carries the device's name."""
        text = " ".join(re.findall(r"[a-z0-9]+", entity.name.lower()))
        return any(f" {word} " in f" {text} " for word in self.status_words)

    def index_status_leds(self) -> None:
        """Which lights are a device's status light, again after the house or what a person
        said changed (E-426)."""
        self.status_led_ids = {
            entity.entity_id
            for entity in self.model.entities.values()
            if entity.domain == "light"
            and entity.entity_id not in self.model.groups
            and self._status_led(entity)
        }

    def status_led_doubts(self) -> dict[str, bool]:
        """Lights on a device that senses (a presence or motion sensor, a voice assistant)
        while Home Assistant shows them as a lamp of their own, not as a setting: the rules are
        unsure (design 50.2, B-359). Their guess: a status light when its name says so."""
        return {
            entity.entity_id: self._named_status_light(entity)
            for entity in self.model.entities.values()
            if entity.domain == "light"
            and entity.entity_id not in self.model.groups
            and not (entity.hidden or entity.disabled)
            and entity_role(entity) is EntityRole.MAIN
            and self._on_a_sensing_device(entity)
        }

    def _on_a_sensing_device(self, entity: Entity) -> bool:
        if entity.device_id is None:
            return False
        return any(
            other.domain == "assist_satellite"
            or (other.domain == "binary_sensor" and other.device_class in SENSING)
            for other in self.model.on_device(entity.device_id)
        )

    def _on_a_device_that_acts(self, entity: Entity) -> bool:
        if entity.device_id is None:
            return False
        return any(other.domain in LUX_NOT_ROOM for other in self.model.on_device(entity.device_id))

    def _sun_elevation(self, now: datetime) -> float | None:
        sun = self.states.get("sun.sun")
        value = sun.attributes.get("elevation") if sun is not None else None
        if isinstance(value, int | float):
            return float(value)
        if self.location is not None:
            return elevation(*self.location, now)
        return None

    def outside_dark_at(self, when: datetime) -> tuple[bool, float | None]:
        """Whether it is dark outside at another moment, and the sun's height then (E-519):
        from the place of the house, else the season's guess. Not Home Assistant's sun, which
        only knows now."""
        if self.location is not None:
            sun = elevation(*self.location, when)
            return is_dark(sun, None, lux_threshold=20.0), sun
        return self._fallback_dark(when.astimezone(self.tz)), None

    def _fallback_dark(self, local: datetime) -> bool:
        # Without sun or light sensors: a conservative season-aware guess.
        month = local.month
        dusk = (
            17
            if month in (11, 12, 1)
            else 18
            if month in (2, 10)
            else 20
            if month in (3, 9)
            else 21
        )
        dawn = 8 if month in (11, 12, 1) else 7 if month in (2, 10) else 6
        return local.hour >= dusk or local.hour < dawn

    def snapshot(self, now: datetime) -> WorldState:
        """The picture of now for the brain's round: it also moves the house mode on (who is
        active, the house going to sleep or waking up). Only the brain's round calls it."""
        return self._picture(now, update=True)

    def look(self, now: datetime) -> WorldState:
        """The same picture, only looking (design 70.1): the house mode, the activity in the
        living areas and the morning stay as they are, so a screen – 'Wat ik nu zou doen', Test
        het huis – may look as often as it likes. Who is in a room still fades with the time."""
        return self._picture(now, update=False)

    def _picture(self, now: datetime, update: bool) -> WorldState:
        local = now.astimezone(self.tz)
        residents = self.residents_home()
        if update:
            if self._residents_seen is not None and residents > self._residents_seen:
                self._arrived = now  # someone came home
            self._residents_seen = residents
        # A room where pets may be counts its sensors much less while the house sleeps – not
        # at a time of the clock: someone up late in the living room is someone (E-529).
        asleep = self.modes.mode is HouseMode.SLEEP
        indoors = self._indoor()
        occupancy = self.occupancy.evaluate(
            now, asleep=asleep, nobody_home=residents == 0, arrived=self._arrived, indoors=indoors
        )
        if update:
            self._move_mode(now, local, residents, occupancy)
            if asleep and self.modes.mode is not HouseMode.SLEEP:  # woke up just now
                occupancy = self.occupancy.evaluate(
                    now, nobody_home=residents == 0, arrived=self._arrived, indoors=indoors
                )
        sun = self._sun_elevation(now)
        dark = {}
        light: dict[str, float | None] = {}
        for area_id in self.model.areas:
            lux = light[area_id] = self._area_lux(area_id)
            dark[area_id] = (
                is_dark(sun, lux)
                if (sun is not None or lux is not None)
                else self._fallback_dark(local)
            )
        outdoor = [
            lux
            for area_id in self.model.areas
            if self.area_roles.get(area_id) == "outside" and (lux := light[area_id]) is not None
        ]
        outside_dark = (
            is_dark(sun, max(outdoor) if outdoor else None, lux_threshold=20.0)
            if (sun is not None or outdoor)
            else self._fallback_dark(local)
        )
        dtype = self._day_type(local.date())
        part = self.part_now(now)
        if update:
            sleeping = self.modes.mode is HouseMode.SLEEP or (
                part.kind == "night" or part.bedtime if part else local.hour < 5
            )
            self.sleep_watch.update(self.states, now, local, sleeping)
        sleep = {
            area_id: view
            for area_id in self.sleep_watch.signals
            if (view := self.sleep_watch.view(area_id, local)) is not None
        }
        return WorldState(
            now=now,
            local=local,
            mode=self.modes.mode,
            day_type=dtype,
            bucket=time_bucket(local),
            residents_home=residents,
            occupancy=occupancy,
            dark=dark,
            outside_dark=outside_dark,
            guests_unconfirmed=self.modes.guests_unconfirmed,
            suggest_vacation=self.modes.suggest_vacation,
            sun=sun,
            lux=light,
            mode_since=self.modes.since,
            part=part.kind if part else "",
            part_since=part.since if part else None,
            part_how=part.how if part else "",
            bedtime=part.bedtime if part else False,
            sleep=sleep,
            woke=self.woke,
            showers=self.occupancy.showers.rooms(now),
        )

    def _move_mode(
        self, now: datetime, local: datetime, residents: int, occupancy: dict[str, AreaOccupancy]
    ) -> None:
        # Someone sitting on the sofa (a presence sensor that stays on) is activity too: the
        # house does not go to sleep while a living area is occupied. Not in a hall (E-520):
        # there coming in counts (on_change), staying does not – in the real house the hall's
        # sensor said "someone" for hours on many nights, and the house never slept.
        if any(
            info.occupied
            and self.indoors(area_id)
            and self.area_roles.get(area_id) not in SLEEPING_ROLES | PASSING_ROLES
            for area_id, info in occupancy.items()
        ):
            self._living_activity = now
        bedroom_lights_on = self._bedroom_awake(now)
        alarm = self.states.get(self.alarm_entity) if self.alarm_entity else None
        self.modes.update(
            ModeInputs(
                now_local=local,
                residents_home=residents,
                occupied_areas=frozenset(
                    a for a, o in occupancy.items() if o.occupied and self.indoors(a)
                ),
                living_activity=self._living_activity.astimezone(self.tz)
                if self._living_activity
                else None,
                bedroom_lights_on=bedroom_lights_on,
                alarm=alarm.state if alarm is not None else None,
            )
        )
        # Waking up – seen by the house, or said by a person – starts the morning (E-455).
        if self._seen_mode is HouseMode.SLEEP and self.modes.mode is not HouseMode.SLEEP:
            self.woke = self.modes.since or local
        self._seen_mode = self.modes.mode

    def _day_type(self, day: date) -> DayType:
        return (
            DayType.DEVIANT
            if day.isoformat() in self._deviant_days
            else day_type(day, self.country)
        )

    def part_now(self, now: datetime) -> Part | None:
        """The part of the day from the house's rhythm (E-455) – only looking: the house mode
        stays as it is, so Vesta and the screens can ask it at any time. None: no rhythm."""
        if self.part_of is None:
            return None
        local = now.astimezone(self.tz)
        today, tomorrow = (
            self._day_type(local.date()),
            self._day_type(local.date() + timedelta(days=1)),
        )
        return self.part_of(local, self.modes.mode, self.modes.since, today, tomorrow, self.woke)

    def mark_deviant_day(self, day_iso: str) -> None:
        """A day that differs from normal (E-89): behaviour that day is not learned as normal."""
        self._deviant_days.add(day_iso)

    def unmark_deviant_day(self, day_iso: str) -> None:
        self._deviant_days.discard(day_iso)
