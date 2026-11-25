from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Entity, HouseModel, Source, State, StateChange
from housemind.demo.houses import load_demo_house
from housemind.i18n.catalog import load_languages
from housemind.knowledge.area_roles import area_role, role_words
from housemind.knowledge.roles import EntityRole, entity_role
from housemind.world.calendar import DayType, day_type, is_dark, time_bucket
from housemind.world.house_mode import HouseMode, HouseModeMachine, ModeInputs
from housemind.world.occupancy import OccupancyModel

KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"
T0 = datetime(2026, 10, 19, 19, 0, tzinfo=UTC)


def _model() -> HouseModel:
    return HouseModel(
        areas={"living": Area("living", "Living"), "hall": Area("hall", "Hall")},
        entities={
            "binary_sensor.living_presence": Entity(
                "binary_sensor.living_presence",
                "r1",
                "p",
                area_id="living",
                device_class="presence",
            ),
            "binary_sensor.hall_motion": Entity(
                "binary_sensor.hall_motion", "r2", "m", area_id="hall", device_class="motion"
            ),
            "light.hall": Entity("light.hall", "r3", "l", area_id="hall"),
            "vacuum.robot": Entity("vacuum.robot", "r4", "v", area_id="living"),
        },
    )


def _change(
    entity_id: str, state: str, source: Source = Source.PHYSICAL, old: str | None = None
) -> StateChange:
    before = State(entity_id, old, {}, T0) if old is not None else None
    return StateChange(entity_id, before, State(entity_id, state, {}, T0), source)


def test_presence_holds_while_sitting_still() -> None:
    occ = OccupancyModel(_model())
    occ.update(_change("binary_sensor.living_presence", "on"), T0)
    later = occ.evaluate(T0 + timedelta(hours=2))["living"]
    assert later.occupied and later.still


def test_motion_fades_and_switch_counts() -> None:
    occ = OccupancyModel(_model())
    occ.update(_change("binary_sensor.hall_motion", "on"), T0)
    occ.update(_change("binary_sensor.hall_motion", "off"), T0 + timedelta(seconds=30))
    assert occ.evaluate(T0 + timedelta(minutes=2))["hall"].occupied
    assert not occ.evaluate(T0 + timedelta(minutes=25))["hall"].occupied
    occ.update(_change("light.hall", "on", Source.PHYSICAL, "off"), T0 + timedelta(minutes=30))
    assert occ.evaluate(T0 + timedelta(minutes=31))["hall"].occupied


def test_pets_and_vacuum_do_not_count_as_people() -> None:
    occ = OccupancyModel(_model(), pet_areas={"hall"})
    occ.update(_change("binary_sensor.hall_motion", "on"), T0)
    assert occ.evaluate(T0, asleep=False)["hall"].occupied
    assert not occ.evaluate(T0, asleep=True)["hall"].occupied
    occ2 = OccupancyModel(_model())
    occ2.update(_change("vacuum.robot", "cleaning"), T0)
    occ2.update(_change("binary_sensor.hall_motion", "on"), T0)
    assert not occ2.evaluate(T0)["hall"].occupied


def _inputs(
    now: datetime,
    residents: int,
    areas: set[str],
    activity: datetime | None = None,
    bed: bool = False,
) -> ModeInputs:
    return ModeInputs(now, residents, frozenset(areas), activity, bed)


def test_house_mode_away_guests_home_sleep() -> None:
    m = HouseModeMachine()
    t = T0
    assert not m.update(_inputs(t, 0, set()))
    assert m.update(_inputs(t + timedelta(minutes=11), 0, set()))
    assert m.mode is HouseMode.AWAY
    assert not m.update(_inputs(t + timedelta(minutes=12), 0, {"living"}))
    assert m.update(_inputs(t + timedelta(minutes=16), 0, {"living"}))
    assert m.mode is HouseMode.GUESTS and m.guests_unconfirmed
    assert m.update(_inputs(t + timedelta(minutes=20), 1, {"living"}))
    assert m.mode is HouseMode.HOME
    night = datetime(2026, 10, 19, 23, 30, tzinfo=UTC)
    assert m.update(_inputs(night, 1, set(), activity=night - timedelta(minutes=25)))
    assert m.mode is HouseMode.SLEEP
    morning = datetime(2026, 10, 20, 6, 50, tzinfo=UTC)
    assert m.update(_inputs(morning, 1, {"living"}, activity=morning))
    assert m.mode is HouseMode.HOME


def test_vacation_is_only_suggested() -> None:
    m = HouseModeMachine()
    m.update(_inputs(T0, 0, set()))
    m.update(_inputs(T0 + timedelta(minutes=11), 0, set()))
    m.update(_inputs(T0 + timedelta(hours=26), 0, set()))
    assert m.mode is HouseMode.AWAY and m.suggest_vacation


def test_day_types_and_buckets() -> None:
    assert day_type(date(2026, 12, 25), "NL") is DayType.HOLIDAY
    assert day_type(date(2026, 10, 17), "NL") is DayType.FREE
    assert day_type(date(2026, 10, 19), "NL") is DayType.WORKDAY
    assert day_type(date(2026, 10, 19), None) is DayType.WORKDAY
    assert time_bucket(datetime(2026, 1, 1, 18, 44)) == "18:30"
    assert is_dark(-5, None) and not is_dark(30, None) and is_dark(30, 10) and not is_dark(-5, 300)


@pytest.mark.parametrize("language", ["en", "nl", "de", "fr"])
def test_area_roles_from_names(language: str) -> None:
    words = role_words(load_languages(KNOWLEDGE / "languages"))
    house = load_demo_house("family_home", SimulatedClock(T0), language)
    roles = {a.id: area_role(a, words) for a in house.ha.model.areas.values()}
    assert roles["bedroom"] == "bedroom"
    assert roles["bedroom_child"] == "child_bedroom"
    assert roles["guest_room"] == "guest_room"
    assert roles["kitchen"] == "kitchen"
    assert roles["garden"] == "outside"
    assert area_role(Area("x", "Zolder"), words) == "other"


def test_entity_roles() -> None:
    assert entity_role(Entity("light.a", "r", "a")) is EntityRole.MAIN
    assert (
        entity_role(Entity("sensor.t", "r", "t", device_class="temperature"))
        is EntityRole.OBSERVATION
    )
    assert (
        entity_role(
            Entity("sensor.b", "r", "b", device_class="battery", entity_category="diagnostic")
        )
        is EntityRole.HEALTH
    )
    assert (
        entity_role(Entity("sensor.soc", "r", "s", device_class="battery"))
        is EntityRole.OBSERVATION
    )
    assert (
        entity_role(Entity("switch.led", "r", "l", entity_category="config")) is EntityRole.SETTING
    )
    assert (
        entity_role(Entity("sensor.uptime", "r", "u", device_class="duration")) is EntityRole.NOISE
    )
    assert entity_role(Entity("light.x", "r", "x", hidden=True)) is EntityRole.NOISE


def test_details_reported_without_a_person_are_nobody_in_the_room() -> None:
    """Found in a real house: a switch of a lighting integration reports its attributes every
    90 seconds (state stays "on"), and every room with one looked occupied."""
    occ = OccupancyModel(_model())
    old = State("light.hall", "on", {"brightness": 120}, T0)
    for minute in range(0, 60, 2):
        new = State("light.hall", "on", {"brightness": 120 + minute}, T0)
        occ.update(
            StateChange("light.hall", old, new, Source.PHYSICAL), T0 + timedelta(minutes=minute)
        )
    assert not occ.evaluate(T0 + timedelta(minutes=60))["hall"].occupied
    # Really switched is still someone using it.
    off = State("light.hall", "off", {}, T0)
    occ.update(StateChange("light.hall", old, off, Source.PHYSICAL), T0 + timedelta(minutes=61))
    assert occ.evaluate(T0 + timedelta(minutes=62))["hall"].occupied


def test_a_presence_sensor_saying_nobody_outweighs_an_old_switch() -> None:
    model = _model()
    model.entities["light.living"] = Entity("light.living", "r5", "l", area_id="living")
    occ = OccupancyModel(model)
    occ.update(_change("binary_sensor.living_presence", "on"), T0)
    occ.update(_change("light.living", "on"), T0)
    occ.update(_change("binary_sensor.living_presence", "off"), T0 + timedelta(minutes=1))
    assert not occ.evaluate(T0 + timedelta(minutes=3))["living"].occupied
