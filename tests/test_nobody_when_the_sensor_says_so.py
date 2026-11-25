"""A presence sensor that sees nobody is believed at once (E-425): a room was still "Iemand
aanwezig" after its sensor saw nobody, because a lamp switched off on the way out and the motion
from before added up to someone. Each test fails without the fix."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.core.clock import DemoClock
from housemind.core.model import Area, Entity, HouseModel, Source, State, StateChange
from housemind.demo.houses import load_demo_house
from housemind.demo.play import Play
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.world.occupancy import OccupancyModel

ROOT = Path(__file__).resolve().parents[1]
AFTERNOON = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
OFFICE = "binary_sensor.office_presence"


@contextlib.asynccontextmanager
async def _demo(tmp_path: Path) -> AsyncIterator[tuple[HouseMind, Play]]:
    """The extended demo house as the app starts it, its clock standing still (Stop)."""
    clock = DemoClock(AFTERNOON, running=False)
    house = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start()
    play = Play(house, clock, hm, "Europe/Amsterdam", tmp_path / "play.json")
    try:
        yield hm, play
    finally:
        await hm.stop()


async def _room(hm: HouseMind, area: str) -> dict[str, Any]:
    await hm.settled()
    return next(r for r in views.rooms(hm, "nl", 2) if r["id"] == area)


async def test_the_room_is_empty_as_soon_as_its_sensor_sees_nobody(tmp_path: Path) -> None:
    async with _demo(tmp_path) as (hm, play):
        await play.control({"do": "presence", "id": OFFICE, "on": True})
        await play.control({"do": "switch", "id": "light.office", "on": True, "by": "wall"})
        assert (await _room(hm, "office"))["occupied"]
        # On the way out: the lamp off at the wall, then the sensor sees nobody.
        await play.control({"do": "switch", "id": "light.office", "on": False, "by": "wall"})
        await play.control({"do": "presence", "id": OFFICE, "on": False})
        office = await _room(hm, "office")
        assert not office["occupied"]
        assert office["summary"].startswith("Leeg")


async def test_a_sensor_that_is_offline_does_not_say_nobody(tmp_path: Path) -> None:
    async with _demo(tmp_path) as (hm, play):
        await play.house.ha.simulate(OFFICE, "unavailable")
        await play.control({"do": "switch", "id": "light.office", "on": True, "by": "wall"})
        assert (await _room(hm, "office"))["occupied"]  # someone switched the desk lamp


async def test_the_play_panel_says_motion_where_only_motion_is_sensed(tmp_path: Path) -> None:
    async with _demo(tmp_path) as (_, play):
        kinds = {r["area"]: r["kind"] for r in play.controls()["rooms"]}
        assert kinds["office"] == "presence"
        assert kinds["bathroom"] == kinds["toilet"] == "motion"
        assert kinds["driveway"] == "motion"  # a camera's person: it fades like motion


def test_a_camera_does_not_outweigh_the_presence_sensor() -> None:
    model = HouseModel()
    model.areas["hall"] = Area("hall", "Hal")
    model.entities["binary_sensor.hall"] = Entity(
        "binary_sensor.hall", "r-hall", "Hal", area_id="hall", device_class="occupancy"
    )
    occ = OccupancyModel(model)
    t0 = AFTERNOON
    on = State("binary_sensor.hall", "on", {}, t0)
    occ.update(StateChange("binary_sensor.hall", None, on, Source.PHYSICAL), t0)
    occ.seen("hall", t0)  # a camera saw someone walk through
    off = State("binary_sensor.hall", "off", {}, t0)
    occ.update(StateChange("binary_sensor.hall", on, off, Source.PHYSICAL), t0)
    assert not occ.evaluate(t0 + timedelta(seconds=5))["hall"].occupied
