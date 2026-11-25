"""The home battery and the car are only watched (E-363, B-248): what HouseMind still had set on
them goes back once after the update, and what it learned about steering them is wiped (K2).
What it steers itself, such as the boiler, stays in the register. The Energy tab shows tips,
costs and the biggest users without steering (E-364), and Vesta explains with a tip (E-365)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.core.clock import SimulatedClock
from housemind.core.model import WHOLE_HOUSE, Source
from housemind.demo.houses import load_demo_house
from housemind.energy.manager import INFO_ONLY
from housemind.energy.model import Slot
from housemind.energy.own import Owned
from housemind.energy.prices import QUARTER
from housemind.energy.tips import cheapest_block, tips
from housemind.knowledge.catalog import Step as Autonomy
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
BATTERY = "select.home_battery_mode"
BOILER = "water_heater.boiler"


async def started(tmp_path: Path) -> tuple[HouseMind, Any]:
    clock = SimulatedClock(T0)
    house = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    return hm, house.ha


async def test_the_battery_goes_back_once_and_the_boiler_stays(tmp_path: Path) -> None:
    hm, ha = await started(tmp_path)
    assert hm.energy is not None and hm.energy.own is not None and hm.world is not None
    assert hm.learning is not None and hm.db is not None
    # As the earlier version left it: the battery held, the boiler warmer, and learned steps.
    await ha.simulate(BATTERY, "idle", by=Source.HOUSEMIND)
    own = hm.energy.own.items
    own[BATTERY] = Owned(
        BATTERY,
        "self_consumption",
        "idle",
        "select.select_option",
        {"option": "self_consumption"},
        "energy_battery",
        T0.isoformat(),
    )
    boiler = hm.world.states[BOILER]
    own[BOILER] = Owned(
        BOILER,
        str(boiler.attributes.get("temperature")),
        str(boiler.attributes.get("temperature")),
        "water_heater.set_temperature",
        {"temperature": 50},
        "energy_boiler",
        T0.isoformat(),
    )
    hm.learning.steps[("energy_battery", WHOLE_HOUSE)] = Autonomy.AUTONOMOUS
    hm.learning.steps[("energy_boiler", WHOLE_HOUSE)] = Autonomy.PROPOSE
    await hm.db.set_setting("battery_remote", True)
    await hm.db.set_setting("departures", {"workday": [450]})
    hm.energy.info_only = False
    hm.energy.since = hm.clock.now() - timedelta(days=15)  # past measuring: the boiler may stay

    await hm._energy_tick(hm.clock.now())
    assert hm.world.states[BATTERY].state == "self_consumption"  # back as it was
    assert BATTERY not in own and BOILER in own
    assert ("energy_battery", WHOLE_HOUSE) not in hm.learning.steps
    assert hm.learning.steps[("energy_boiler", WHOLE_HOUSE)] is Autonomy.PROPOSE
    assert await hm.db.get_setting("battery_remote") is None
    assert await hm.db.get_setting("departures") is None
    assert await hm.db.get_setting(INFO_ONLY) is True
    rows = await hm.db.fetchall("SELECT skill FROM skill_steps WHERE skill = 'energy_battery'")
    assert not rows
    # The Logbook says why, in plain words.
    from housemind.server.logbook import logbook
    from housemind.server.views import Viewer

    viewer = Viewer("demo-user-1", "Resident 1", 4, True, {"language": "nl"})
    book = await logbook(hm, viewer, "demo-user-1", "nl")
    assert "stuur de thuisbatterij en de auto niet meer" in str(book)
    await hm.stop()


async def test_an_unavailable_battery_goes_back_when_it_returns(tmp_path: Path) -> None:
    hm, ha = await started(tmp_path)
    assert hm.energy is not None and hm.energy.own is not None and hm.world is not None
    await ha.simulate(BATTERY, "unavailable", by=Source.PHYSICAL)
    hm.energy.own.items[BATTERY] = Owned(
        BATTERY,
        "self_consumption",
        "idle",
        "select.select_option",
        {"option": "self_consumption"},
        "energy_battery",
        T0.isoformat(),
    )
    hm.energy.info_only = False
    await hm._energy_tick(hm.clock.now())
    assert not hm.energy.info_only  # it waits
    await ha.simulate(BATTERY, "idle", by=Source.PHYSICAL)
    await hm.clock.advance(30)
    await hm._energy_tick(hm.clock.now())
    assert hm.world.states[BATTERY].state == "self_consumption"
    assert hm.energy.info_only
    await hm.stop()


async def test_a_new_house_is_info_only_from_the_start(tmp_path: Path) -> None:
    hm, _ha = await started(tmp_path)
    assert hm.energy is not None
    await hm._energy_tick(hm.clock.now())
    assert hm.energy.info_only
    await hm.stop()


# -- tips without steering (E-364, B-254) and Vesta (E-365) ----------------------------------


def _slots(prices: list[float | None], solar: float = 0.0, house: float = 400.0) -> list[Slot]:
    return [Slot(T0 + i * QUARTER, p, p, solar, house) for i, p in enumerate(prices)]


def test_the_cheapest_block_is_three_hours_with_known_prices() -> None:
    prices: list[float | None] = [0.30] * 8 + [0.10] * 12 + [0.30] * 8 + [None] * 4
    block = cheapest_block(_slots(prices), T0, T0 + timedelta(hours=8))
    assert block is not None
    start, end, average = block
    assert (start, end) == (T0 + 8 * QUARTER, T0 + 20 * QUARTER) and abs(average - 0.10) < 1e-9
    # Less time left than three hours: the whole time that is left.
    short = cheapest_block(_slots(prices), T0, T0 + timedelta(hours=1))
    assert short is not None and short[1] - short[0] == timedelta(hours=1)
    assert cheapest_block(_slots([None] * 8), T0, T0 + timedelta(hours=2)) is None


def test_tips_say_what_stands_out_without_steering() -> None:
    prices: list[float | None] = [0.40] * 4 + [0.20] * 20 + [-0.02] * 8 + [0.25] * 64
    found = tips(_slots(prices, solar=0.0), T0, True, 900.0, 1200.0)
    keys = [tip.key for tip in found]
    assert keys[0] == "battery_grid_dear"  # charging from the grid at a dear moment
    assert "cheap_car" in keys and "negative" in keys and len(found) <= 3
    sunny = tips(_slots([0.2] * 96, solar=3000.0), T0, False, None, None)
    assert [tip.key for tip in sunny] == ["sun_spare"]
    assert tips(_slots([0.2] * 96), T0, False, None, None) == []


async def test_vesta_explains_and_gives_a_tip(tmp_path: Path) -> None:
    hm, _ha = await started(tmp_path)
    said = (await hm.talk("laad de auto vol voor 7:00", "nl", 3, None))["text"]
    assert said.startswith("Het laden van de auto regel ik niet.")
    assert "Het goedkoopst voor 07:00 is tussen" in said
    battery = (await hm.talk("zet de batterij op laden", "nl", 3, None))["text"]
    assert battery == "De thuisbatterij stuur ik niet. Dat kan in Home Assistant."
    # A question about the battery is no command: it is not answered like one.
    asked = (await hm.talk("hoe werkt laad de batterij?", "nl", 3, None))["text"]
    assert asked != battery
    await hm.stop()


async def test_today_costs_and_the_biggest_users(tmp_path: Path) -> None:
    hm, ha = await started(tmp_path)
    assert hm.energy is not None and hm.world is not None
    hm.energy.setup.device_meters = ["sensor.washer_energy"]
    await ha.simulate("sensor.washer_energy", "12.0", {"unit_of_measurement": "kWh"})
    await hm.energy.tick(hm.clock.now(), hm.world.states)
    await ha.simulate("sensor.washer_energy", "13.5", {"unit_of_measurement": "kWh"})
    assert hm.energy.users_today(hm.world.states) == [("sensor.washer_energy", 1.5)]
    await hm.db.execute(
        "INSERT INTO energy_days(day, import_kwh, export_kwh, solar_kwh, car_kwh, cost) "
        "VALUES(?,?,?,?,?,?)",
        ("2026-10-01", 10.0, 0.0, 0.0, 0.0, 3.25),
    )
    assert await hm.energy.month_cost(hm.clock.now()) == 3.25
    from housemind.server.views import energy

    nothing = await energy(hm, "nl", 3)
    assert nothing is not None and "kostte" not in nothing["sentence"]  # nothing measured
    view = await energy(hm, "nl", 3)
    assert view is not None and view["costs"]["month"].endswith("3,25")
    assert "tips" in view and view["live"]["batteries"]
    await hm.stop()
