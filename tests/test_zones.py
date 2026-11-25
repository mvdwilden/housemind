"""Zones of a presence sensor (E-215) and the robot vacuum, from the user's side. Each test
fails without the change; what went wrong is in its docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Device, Entity, HouseModel, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.server.views import rooms
from housemind.world.house_mode import HouseMode
from housemind.world.zones import sort_zones
from tests.helpers import wait_seconds as _wait

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 10, 19, 19, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))
WORDS = (["zone", "entry", "ingang"], ["presence", "aanwezigheid", "sensor"], ["bed"])


def _sensor(model: HouseModel, eid: str, name: str, device: str, area: str | None = None) -> None:
    model.entities[eid] = Entity(
        eid, f"r-{eid}", name, device_id=device, area_id=area, device_class="occupancy"
    )


def test_zones_are_found_from_what_ha_reports() -> None:
    """Every zone counted for the room the sensor hangs in, also "Zone: Dining room" on the
    kitchen sensor; parts of a room and their lamps were unknown."""
    m = HouseModel()
    m.areas["kitchen"] = Area("kitchen", "Keuken", "ground")
    m.areas["dining"] = Area("dining", "Eethoek", "ground", ("Eetkamer",))
    m.areas["bedroom"] = Area("bedroom", "Slaapkamer Ouders", "first")
    m.areas["study"] = Area("study", "Eetkamer", "first")  # same name, another floor
    m.devices["k"] = Device("k", "Sensor Keuken", "kitchen")
    _sensor(m, "binary_sensor.k_1", "Aanwezigheidssensor", "k")
    _sensor(m, "binary_sensor.k_2", "Zone: Keuken", "k")
    _sensor(m, "binary_sensor.k_3", "Zone: Eetkamer", "k")
    m.devices["b"] = Device("b", "Sensor Slaapkamer", "bedroom")
    _sensor(m, "binary_sensor.b_1", "Sensor Slaapkamer Presence", "b")
    _sensor(m, "binary_sensor.b_2", "Sensor Slaapkamer Zone 1 Presence", "b")
    _sensor(m, "binary_sensor.b_3", "Zone: Bed", "b")
    _sensor(m, "binary_sensor.b_4", "Zone: Walk-in closet", "b")
    _sensor(m, "binary_sensor.b_5", "Zone: Slaapkamer", "b")
    _sensor(m, "binary_sensor.b_6", "Zone: Hobby", "b", area="study")  # placed in HA
    m.devices["l"] = Device("l", "Slaapkamer Ouders - Walk-in closet Lamp 1", "bedroom")
    m.entities["light.closet_1"] = Entity("light.closet_1", "r-l", "Lamp", device_id="l")
    m.entities["light.bed"] = Entity("light.bed", "r-lb", "Bedlamp", area_id="bedroom")
    m.devices["one"] = Device("one", "Losse sensor", "kitchen")
    _sensor(m, "binary_sensor.one", "Zone: Eetkamer", "one")  # alone on its device: no zone

    zones = sort_zones(m, *WORDS)
    z = zones.zones
    assert z["binary_sensor.k_3"].area_id == "dining"  # the alias, on the same floor
    assert z["binary_sensor.k_2"].area_id == "kitchen" and not z["binary_sensor.k_2"].part
    assert z["binary_sensor.b_2"].name == "Zone 1" and z["binary_sensor.b_2"].part
    assert z["binary_sensor.b_3"].bed and not z["binary_sensor.b_3"].lamps
    assert z["binary_sensor.b_4"].lamps == ("light.closet_1",)
    assert not z["binary_sensor.b_5"].part  # the room itself
    assert z["binary_sensor.b_6"].area_id == "study"  # the administrator's choice in HA
    assert set(zones.general) == {"binary_sensor.k_1", "binary_sensor.b_1"}
    assert "binary_sensor.one" not in z


async def _house(tmp_path: Path) -> tuple[HouseMind, object, SimulatedClock]:
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    m = ha.model
    m.areas["dining"] = Area("dining", "Eetkamer", "ground")
    m.devices["k"] = Device("k", "Sensor", "kitchen")
    m.devices["b"] = Device("b", "Sensor", "bedroom")
    added = {
        "binary_sensor.k_range": ("Aanwezigheidssensor", "k", None),
        "binary_sensor.k_kitchen": ("Zone: Keuken", "k", None),
        "binary_sensor.k_dining": ("Zone: Eetkamer", "k", None),
        "binary_sensor.b_range": ("Aanwezigheidssensor", "b", None),
        "binary_sensor.b_bed": ("Zone: Bed", "b", None),
        "binary_sensor.b_closet": ("Zone: Walk-in closet", "b", None),
    }
    for eid, (name, device, area) in added.items():
        m.entities[eid] = Entity(
            eid, f"r-{eid}", name, device_id=device, area_id=area, device_class="occupancy"
        )
        ha.states[eid] = State(eid, "off", {}, clock.now())
    lamps = {
        "light.dining": ("Eetkamer lamp", "dining", {}),
        "light.closet_1": ("Walk-in closet Lamp 1", "bedroom", {}),
        "light.closet_2": ("Walk-in closet Lamp 2", "bedroom", {}),
        "light.closet": (
            "Walk-in closet",
            "bedroom",
            {"group_entities": ["light.closet_1", "light.closet_2"]},
        ),
    }
    for eid, (name, area, attrs) in lamps.items():
        m.entities[eid] = Entity(eid, f"r-{eid}", name, area_id=area)
        ha.states[eid] = State(eid, "off", attrs, clock.now())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    assert hm.learning is not None and hm.world is not None
    for area in ("kitchen", "dining", "bedroom"):
        for skill in ("light_on_entry", "light_off_leave"):
            hm.learning.steps[(skill, area)] = Step.AUTONOMOUS
    hm.world.modes.mode = HouseMode.HOME
    return hm, ha, clock


async def test_a_zone_for_the_dining_room_lights_the_dining_room(tmp_path: Path) -> None:
    """Someone at the dining table – a zone of the kitchen sensor – kept the kitchen
    occupied and switched the kitchen light on; the dining room stayed empty. Still in range
    but in none of the zones is not in the kitchen, which has a zone of its own (E-530): the
    kitchen light stays as it was."""
    hm, ha, clock = await _house(tmp_path)
    assert hm.world is not None
    await ha.simulate("binary_sensor.k_range", "on")
    await ha.simulate("binary_sensor.k_dining", "on")
    await _wait(hm, clock, 10)
    snap = hm.world.snapshot(clock.now())
    assert snap.occupied("dining") and not snap.occupied("kitchen")
    assert ha.states["light.dining"].state == "on"
    assert ha.states["light.kitchen"].state == "off"
    await ha.simulate("binary_sensor.k_dining", "off")  # walks away, still in range
    await _wait(hm, clock, 10)
    assert ha.states["light.kitchen"].state == "off"  # no flash of the kitchen light
    await _wait(hm, clock, 40)
    assert hm.world.snapshot(clock.now()).occupied("kitchen")  # in range, in no zone
    assert ha.states["light.kitchen"].state == "off"  # not in the kitchen's own zone
    room = next(r for r in rooms(hm, "nl", 3) if r["id"] == "dining")
    assert room["zones"][0]["name"] == "Eetkamer"
    assert "Keuken" in room["zones"][0]["note"]  # "sensor in Keuken"
    await hm.stop()


async def test_a_part_with_its_own_lamps_and_a_bed(tmp_path: Path) -> None:
    """Lying in bed switched the bedroom light on; in the walk-in closet the whole bedroom
    lit up instead of the closet lamps; the room view showed no zones."""
    hm, ha, clock = await _house(tmp_path)
    assert hm.world is not None
    await ha.simulate("binary_sensor.b_range", "on")
    await ha.simulate("binary_sensor.b_bed", "on")
    await _wait(hm, clock, 10)
    assert hm.world.snapshot(clock.now()).occupied("bedroom")  # someone is there …
    assert ha.states["light.bedroom"].state == "off"  # … in bed: no light
    room = next(r for r in rooms(hm, "nl", 3) if r["id"] == "bedroom")
    assert room["summary"].startswith("Iemand aanwezig (Bed)")
    await ha.simulate("binary_sensor.b_bed", "off")
    await ha.simulate("binary_sensor.b_closet", "on")
    await _wait(hm, clock, 10)
    assert ha.states["light.closet"].state == "on"  # the closet's group
    assert ha.states["light.bedroom"].state == "off"
    await ha.simulate("binary_sensor.b_closet", "off")
    await _wait(hm, clock, 5 * 60)
    assert ha.states["light.closet"].state == "off"  # the part is empty: its lamps go off
    await hm.stop()


async def test_a_robot_vacuum_is_nobody(tmp_path: Path) -> None:
    """With everyone away, the robot vacuum seen by a presence sensor made the house
    "guests" – someone in the house while nobody is home."""
    hm, ha, clock = await _house(tmp_path)
    assert hm.world is not None
    for eid in list(ha.states):
        if eid.startswith("person."):
            await ha.simulate(eid, "not_home")
    await ha.simulate("binary_sensor.living_presence", "off")
    await _wait(hm, clock, 15 * 60)
    assert hm.world.modes.mode is HouseMode.AWAY
    await ha.simulate("vacuum.robot", "cleaning")
    await ha.simulate("binary_sensor.living_presence", "on")
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    await _wait(hm, clock, 10 * 60)
    assert hm.world.modes.mode is HouseMode.AWAY
    assert not hm.world.snapshot(clock.now()).occupied("living")
    await ha.simulate("binary_sensor.living_presence", "off")
    await ha.simulate("vacuum.robot", "docked")
    await _wait(hm, clock, 5 * 60)
    assert hm.world.modes.mode is HouseMode.AWAY
    await hm.stop()
