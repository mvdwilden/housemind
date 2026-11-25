"""Pets while the house sleeps (E-90, E-529). Found in the real house: an administrator put pets
in the hall downstairs, but its presence sensor sees the dog as well as a person. At night the
hall's lamp went on for the dog – once for three and a half hours – and one morning the house
'woke up' at 05:19 the second that sensor saw the dog. Now, in a room where pets may be, while
the house sleeps or nobody is home, what only a sensor sees is perhaps the pet: no one there, no
lamp, no waking. A sign of a person makes it someone: a camera that sees a person there, a hand
on a switch there, someone who just began to move in a room without pets (coming down the
stairs, out of the bathroom), or a resident who just came home. Each test fails without it; what
it checks is its docstring."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from housemind.brain.skills import SkillEngine, load_skill_params
from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Entity, HouseModel, Source, State, StateChange
from housemind.demo.houses import DemoHouse, load_demo_house
from housemind.i18n.catalog import load_languages
from housemind.knowledge.area_roles import area_role, role_words
from housemind.knowledge.catalog import Catalog
from housemind.safety.gate import ActionRequest
from housemind.world.house_mode import HouseMode
from housemind.world.occupancy import OccupancyModel
from housemind.world.world import World
from housemind.world.zones import Zone, Zones

KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"
# Friday 20 November 2026, 03:30 UTC = 04:30 in Amsterdam: dark, everyone in bed.
NIGHT = datetime(2026, 11, 20, 3, 30, tzinfo=UTC)
T0 = datetime(2026, 11, 20, 3, 30, tzinfo=UTC)


def _model() -> HouseModel:
    """A hall with a presence sensor that also sees a dog, and a landing upstairs."""
    return HouseModel(
        areas={
            "hall": Area("hall", "Hall"),
            "landing": Area("landing", "Landing"),
            "garden": Area("garden", "Garden"),
        },
        entities={
            "binary_sensor.hall_presence": Entity(
                "binary_sensor.hall_presence", "r1", "p", area_id="hall", device_class="occupancy"
            ),
            "binary_sensor.landing_motion": Entity(
                "binary_sensor.landing_motion", "r2", "m", area_id="landing", device_class="motion"
            ),
            "binary_sensor.garden_motion": Entity(
                "binary_sensor.garden_motion", "r3", "g", area_id="garden", device_class="motion"
            ),
            "binary_sensor.landing_door": Entity(
                "binary_sensor.landing_door", "r4", "d", area_id="landing", device_class="door"
            ),
            "light.hall": Entity("light.hall", "r5", "l", area_id="hall"),
        },
    )


def _change(
    entity_id: str, state: str, old: str = "off", by: Source = Source.PHYSICAL
) -> StateChange:
    return StateChange(
        entity_id, State(entity_id, old, {}, T0), State(entity_id, state, {}, T0), by
    )


INDOORS = frozenset({"hall", "landing"})


def test_a_presence_sensor_in_a_pet_room_is_no_one_while_the_house_sleeps() -> None:
    """The presence sensor in the hall sees the dog: while the house sleeps, or nobody is home,
    that is no one – also an hour later. Awake in the evening it is someone, as before: a
    person up late is someone, whatever the clock says."""
    occ = OccupancyModel(_model(), pet_areas={"hall"})
    occ.update(_change("binary_sensor.hall_presence", "on"), T0)
    assert not occ.evaluate(T0, asleep=True, indoors=INDOORS)["hall"].occupied
    later = T0 + timedelta(hours=1)
    assert not occ.evaluate(later, asleep=True, indoors=INDOORS)["hall"].occupied
    assert not occ.evaluate(later, nobody_home=True, indoors=INDOORS)["hall"].occupied
    assert occ.evaluate(later, asleep=False, indoors=INDOORS)["hall"].occupied
    # A room without pets is as it was.
    plain = OccupancyModel(_model())
    plain.update(_change("binary_sensor.hall_presence", "on"), T0)
    assert plain.evaluate(T0, asleep=True, indoors=INDOORS)["hall"].occupied


def test_someone_coming_down_the_stairs_is_a_person() -> None:
    """Movement on the landing upstairs – a room without pets – and then in the hall: someone
    coming down, so the hall is someone's. While they keep moving there it stays so; once only
    the sensor that stays on is left, after two quiet minutes, it is the dog again."""
    occ = OccupancyModel(_model(), pet_areas={"hall"})
    occ.update(_change("binary_sensor.landing_motion", "on"), T0)
    t = T0 + timedelta(seconds=20)
    occ.update(_change("binary_sensor.hall_presence", "on"), t)
    assert occ.evaluate(t, asleep=True, indoors=INDOORS)["hall"].occupied
    for minute in (2, 3, 4):  # walking around: the sensor sees someone new now and then
        at = T0 + timedelta(minutes=minute)
        occ.update(_change("binary_sensor.hall_presence", "off", "on"), at)
        occ.update(_change("binary_sensor.hall_presence", "on"), at + timedelta(seconds=5))
        assert occ.evaluate(at + timedelta(seconds=6), asleep=True, indoors=INDOORS)[
            "hall"
        ].occupied
    quiet = T0 + timedelta(minutes=7)
    assert not occ.evaluate(quiet, asleep=True, indoors=INDOORS)["hall"].occupied


def test_a_camera_a_hand_a_door_or_coming_home_is_a_person() -> None:
    """A camera that sees a person in the hall, a lamp switched there by hand (for a quarter of an
    hour), a door that opens upstairs, or a resident who just came home – each is a person."""
    model = _model()
    signs = {
        "camera": lambda occ, t: occ.seen("hall", t),
        "hand": lambda occ, t: occ.update(_change("light.hall", "on"), t - timedelta(minutes=10)),
        "door": lambda occ, t: occ.update(_change("binary_sensor.landing_door", "on"), t),
    }
    for name, sign in signs.items():
        occ = OccupancyModel(model, pet_areas={"hall"})
        occ.update(_change("binary_sensor.hall_presence", "on"), T0)
        sign(occ, T0)
        assert occ.evaluate(T0, asleep=True, indoors=INDOORS)["hall"].occupied, name
    occ = OccupancyModel(model, pet_areas={"hall"})
    occ.update(_change("binary_sensor.hall_presence", "on"), T0)
    home = T0 - timedelta(minutes=2)
    assert occ.evaluate(T0, asleep=True, arrived=home, indoors=INDOORS)["hall"].occupied


def test_the_garden_old_movement_and_turning_over_in_bed_are_no_sign() -> None:
    """Movement in the garden is no one coming down the stairs, movement upstairs ten minutes
    ago is no sign now, and the bed zone of a presence sensor that sees someone turn over in
    their sleep is no one walking."""
    model = _model()
    model.entities["binary_sensor.landing_bed"] = Entity(
        "binary_sensor.landing_bed", "r6", "b", area_id="landing", device_class="occupancy"
    )
    occ = OccupancyModel(model, pet_areas={"hall"})
    occ.zones = Zones(
        zones={
            "binary_sensor.landing_bed": Zone(
                "binary_sensor.landing_bed", "fp", "Bed", "landing", "landing", True, True
            )
        }
    )
    occ.index()
    occ.update(_change("binary_sensor.garden_motion", "on"), T0)
    occ.update(_change("binary_sensor.landing_motion", "on"), T0 - timedelta(minutes=10))
    occ.update(_change("binary_sensor.landing_bed", "off", "on"), T0 - timedelta(seconds=40))
    occ.update(_change("binary_sensor.landing_bed", "on"), T0 - timedelta(seconds=30))
    occ.update(_change("binary_sensor.hall_presence", "on"), T0)
    assert not occ.evaluate(T0, asleep=True, indoors=INDOORS)["hall"].occupied


class Rig:
    """The family demo house: pets in the living room (a presence sensor), the kitchen and the
    hall; a bathroom without pets."""

    def __init__(self, catalog: Catalog, start: datetime) -> None:
        self.clock = SimulatedClock(start)
        self.house: DemoHouse = load_demo_house("family_home", self.clock, "nl")
        words = role_words(load_languages(KNOWLEDGE / "languages"))
        roles = {a.id: area_role(a, words) for a in self.house.ha.model.areas.values()}
        self.world = World(
            self.house.ha.model, "Europe/Amsterdam", "NL", roles, set(self.house.pet_areas)
        )
        self.world.load_states(self.house.ha.states)
        self.engine = SkillEngine(
            catalog, self.house.ha.model, roles, load_skill_params([KNOWLEDGE / "skills"])
        )

    def asleep(self) -> None:
        """The house went to sleep by itself, as at night."""
        self.world.modes.mode = HouseMode.SLEEP
        self.world.modes.since = self.clock.now() - timedelta(hours=4)
        self.world.modes.manual = False
        self.world._seen_mode = HouseMode.SLEEP

    async def set(self, entity_id: str, state: str, by: Source = Source.PHYSICAL) -> None:
        old = self.house.ha.states.get(entity_id)
        await self.house.ha.simulate(entity_id, state, {}, by=by)
        new = self.house.ha.states[entity_id]
        self.world.on_change(StateChange(entity_id, old, new, by), self.clock.now())

    def run(self) -> list[ActionRequest]:
        snap = self.world.snapshot(self.clock.now())
        return self.engine.evaluate(snap, self.house.ha.states)


def _lit(requests: list[ActionRequest], entity_id: str) -> bool:
    return any(r.entity_id == entity_id and r.action.endswith("turn_on") for r in requests)


async def test_no_lamp_for_the_dog_but_for_a_person(catalog: Catalog) -> None:
    """While the house sleeps, the dog in the living room (a presence sensor) or the hall (a
    motion sensor) puts on no lamp. Someone coming out of the bathroom – a room without pets –
    into the hall gets the night light there."""
    rig = Rig(catalog, NIGHT)
    rig.asleep()
    await rig.set("binary_sensor.living_presence", "on")
    await rig.set("binary_sensor.hall_motion", "on")
    requests = rig.run()
    assert not _lit(requests, "light.hall") and not _lit(requests, "light.living_ceiling")
    await rig.clock.advance(10 * 60)
    await rig.set("binary_sensor.hall_motion", "off")
    await rig.clock.advance(60)
    await rig.set("binary_sensor.bathroom_motion", "on")
    await rig.clock.advance(15)
    await rig.set("binary_sensor.hall_motion", "on")
    assert _lit(rig.run(), "light.hall")


async def _walk(rig: Rig, sensor: str) -> None:
    await rig.set(sensor, "on")
    rig.run()
    await rig.clock.advance(60)
    rig.run()
    await rig.set(sensor, "off")
    await rig.clock.advance(60)


async def test_the_dog_does_not_wake_the_house_but_a_person_does(catalog: Catalog) -> None:
    """At 05:15 – as in the real house, where the house 'woke up' at 05:19 – the dog walks
    through the hall: the house sleeps on, so no morning starts. A person the camera sees there
    wakes it."""
    rig = Rig(catalog, datetime(2026, 11, 20, 4, 15, tzinfo=UTC))  # 05:15 in Amsterdam
    rig.asleep()
    rig.run()
    await _walk(rig, "binary_sensor.hall_motion")
    assert rig.world.modes.mode is HouseMode.SLEEP
    rig.world.occupancy.seen("hall", rig.clock.now())
    await _walk(rig, "binary_sensor.hall_motion")
    assert rig.world.modes.mode is not HouseMode.SLEEP


async def test_from_the_morning_on_people_wake_the_house_where_pets_are(catalog: Catalog) -> None:
    """From half an hour before the house usually wakes up – 06:00 by the clock while that is not
    learned – movement in the kitchen wakes the house, also where every room downstairs has
    pets: people get up. A learned wake-up time at 07:30 moves that to 07:00."""
    rig = Rig(catalog, datetime(2026, 11, 20, 5, 45, tzinfo=UTC))  # 06:45 in Amsterdam
    rig.asleep()
    rig.run()
    await _walk(rig, "binary_sensor.kitchen_motion")
    assert rig.world.modes.mode is not HouseMode.SLEEP
    late = Rig(catalog, datetime(2026, 11, 20, 5, 45, tzinfo=UTC))
    late.world.wake_at = lambda day, kind: time(7, 30)
    late.asleep()
    late.run()
    await _walk(late, "binary_sensor.kitchen_motion")
    assert late.world.modes.mode is HouseMode.SLEEP
    await late.clock.advance(20 * 60)  # 07:07
    await _walk(late, "binary_sensor.kitchen_motion")
    assert late.world.modes.mode is not HouseMode.SLEEP


async def test_before_bedtime_people_still_wake_the_house(catalog: Catalog) -> None:
    """At 21:30, before the usual bedtime – 23:00 by the clock while it is not learned – people
    in the kitchen wake a house that went to sleep too early, and get their light. At 23:30 the
    same movement is perhaps the dog: the house sleeps on."""
    rig = Rig(catalog, datetime(2026, 11, 20, 20, 30, tzinfo=UTC))  # 21:30 in Amsterdam
    rig.asleep()
    rig.run()
    await _walk(rig, "binary_sensor.kitchen_motion")
    assert rig.world.modes.mode is not HouseMode.SLEEP
    night = Rig(catalog, datetime(2026, 11, 20, 22, 30, tzinfo=UTC))  # 23:30 in Amsterdam
    night.asleep()
    night.run()
    await _walk(night, "binary_sensor.kitchen_motion")
    assert night.world.modes.mode is HouseMode.SLEEP
