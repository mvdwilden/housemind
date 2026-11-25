"""Icons in Energie, the Check-up and Test het huis (E-512, design 79, part C): the parts of the
energy plan and whoever uses power show their device; a Check-up card the device it is about,
also one that left Home Assistant; Test het huis the rooms to choose from and, per line, the
device it would move – a lamp at the percentage it would get. Each test fails without the
build."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server import tidy_view, views

ROOT = Path(__file__).resolve().parents[1]


async def _start(tmp_path: Path, house: str = "family_home") -> HouseMind:
    clock = SimulatedClock(datetime(2026, 11, 2, 18, 30, tzinfo=UTC))
    demo = load_demo_house(house, clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    return hm


async def test_a_check_up_card_shows_its_device(tmp_path: Path) -> None:
    hm = await _start(tmp_path)
    assert tidy_view._entity(hm, "light.living_ceiling")["icon"] == "lamp"
    assert tidy_view._entity(hm, "binary_sensor.gone_motion")["icon"] == "sensor"  # left HA
    assert tidy_view._entity(hm, "automation.gone")["icon"] == "device"
    await hm.stop()


async def test_test_the_house_shows_rooms_and_what_each_line_moves(tmp_path: Path) -> None:
    hm = await _start(tmp_path)
    view = hm.test_view("nl", "living", ["someone"])
    rooms = {r["id"]: r["icon"] for r in view["rooms"]}
    assert rooms["living"] == "sofa" and rooms["bedroom"] == "bed"
    lamps = [line for line in view["lines"] if line.get("icon") == "lamp"]
    assert lamps and all("pct" in line for line in lamps), view["lines"]
    await hm.stop()


async def test_the_energy_parts_show_their_device(tmp_path: Path) -> None:
    hm = await _start(tmp_path, "extended")
    view = await views.energy(hm, "nl", 3, True)
    assert view is not None
    icons = {p["icon"] for p in view.get("parts", [])}
    assert icons <= {"boiler", "heat_pump", "plug", "grid"} and icons, view.get("parts")
    for user in view.get("usersToday", []):
        assert user.get("icon"), user
    await hm.stop()
