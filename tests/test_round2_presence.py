"""Round 2 of the device audit: presence, the outside and doors. Each test fails without the
fix; the finding it comes from is in its docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Device, Entity, HouseModel, Source, State, StateChange
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.i18n.catalog import load_languages
from housemind.knowledge.area_roles import area_role, role_words
from housemind.runtime import HouseMind, Paths
from housemind.world.occupancy import OccupancyModel
from housemind.world.openings import house_openings

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 19, 19, 0, tzinfo=UTC)
NOON = datetime(2026, 10, 19, 12, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


def test_outside_names_in_compounds_are_outside() -> None:
    """ "Voortuin", "Achtertuin" and "Backyard" counted as rooms indoors."""
    words = role_words(load_languages([ROOT / "knowledge" / "languages"]))
    for name in ("Voortuin", "Achtertuin", "Moestuin", "Backyard", "Dakterras", "Deck"):
        assert area_role(Area(name.lower(), name), words) == "outside", name
    assert area_role(Area("woonkamer", "Woonkamer"), words) == "living"


def _change(eid: str, old: str | None, new: str, source: Source) -> StateChange:
    before = State(eid, old, {}, T0) if old is not None else None
    return StateChange(eid, before, State(eid, new, {}, T0), source)


def test_only_people_switching_count_as_someone_there() -> None:
    """A player stopping by itself, a cover arriving, a device coming back online and a
    camera's motion all counted as a person in the room."""
    model = HouseModel()
    model.areas["living"] = Area("living", "Living")
    model.devices["cam"] = Device("cam", "Camera", "living")
    for eid, device, cls in (
        ("media_player.tv", None, None),
        ("cover.curtain", None, "curtain"),
        ("light.lamp", None, None),
        ("camera.cam", "cam", None),
        ("binary_sensor.cam_motion", "cam", "motion"),
    ):
        model.entities[eid] = Entity(
            eid, f"r-{eid}", eid, device_id=device, area_id="living", device_class=cls
        )
    occ = OccupancyModel(model)
    for change in (
        _change("media_player.tv", "playing", "idle", Source.PHYSICAL),
        _change("cover.curtain", "closing", "closed", Source.PHYSICAL),
        _change("light.lamp", "unavailable", "on", Source.PHYSICAL),
        _change("binary_sensor.cam_motion", "off", "on", Source.PHYSICAL),
    ):
        occ.update(change, T0)
    assert not occ.evaluate(T0 + timedelta(seconds=30))["living"].occupied
    occ.update(_change("light.lamp", "off", "on", Source.PHYSICAL), T0)  # a wall switch
    assert occ.evaluate(T0 + timedelta(seconds=30))["living"].occupied


def test_an_appliance_door_is_no_opening_of_the_house() -> None:
    """A dishwasher's door set the room's heating to 12 °C and was "open while away"."""
    model = HouseModel()
    model.devices["dw"] = Device("dw", "Vaatwasser", "kitchen")
    model.devices["contact"] = Device("contact", "Deursensor", "kitchen")
    model.devices["car"] = Device("car", "Auto", None)
    model.entities["switch.dw_power"] = Entity("switch.dw_power", "r1", "Aan", device_id="dw")
    model.entities["binary_sensor.dw_door"] = Entity(
        "binary_sensor.dw_door", "r2", "Deur", device_id="dw", device_class="door"
    )
    model.entities["binary_sensor.back_door"] = Entity(
        "binary_sensor.back_door", "r3", "Achterdeur", device_id="contact", device_class="door"
    )
    model.entities["device_tracker.car"] = Entity(
        "device_tracker.car", "r4", "Auto", device_id="car"
    )
    model.entities["binary_sensor.car_door"] = Entity(
        "binary_sensor.car_door", "r5", "Portier", device_id="car", device_class="door"
    )
    model.entities["binary_sensor.fridge_door"] = Entity(
        "binary_sensor.fridge_door", "r6", "Koelkast deur", device_class="door"
    )
    found = house_openings(model, ["koelkast", "vaatwasser"])
    assert found == {"binary_sensor.back_door"}


async def test_someone_outside_or_on_camera_is_no_one_in_the_house(tmp_path: Path) -> None:
    """While everyone was away, a cat on a driveway camera made the house "guests"."""
    clock = SimulatedClock(NOON.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha: FakeHomeAssistant = house.ha
    ha.model.entities["binary_sensor.garden_presence"] = Entity(
        "binary_sensor.garden_presence",
        "r-gp",
        "Tuin",
        area_id="garden",
        device_class="occupancy",
    )
    ha.states["binary_sensor.garden_presence"] = State(
        "binary_sensor.garden_presence", "off", {}, clock.now()
    )
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    assert hm.world is not None
    for person in [e for e in ha.states if e.startswith("person.")]:
        await ha.simulate(person, "not_home")
    for _ in range(20):
        await clock.advance(60)
        await hm.tick()
    assert hm.world.snapshot(clock.now()).mode.value == "away"
    await ha.simulate("binary_sensor.garden_presence", "on")
    for _ in range(10):
        await clock.advance(60)
        await hm.tick()
    assert hm.world.snapshot(clock.now()).mode.value == "away"  # not "guests"
    await hm.stop()
