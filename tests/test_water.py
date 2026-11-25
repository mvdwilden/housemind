"""Water (domain 11): usage, and a leak seen from water that keeps running while nobody uses it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.water import litres
from housemind.core.clock import SimulatedClock
from housemind.core.model import State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")


async def start(
    at: datetime, tmp_path: Path
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, clock, house.ha


async def run_water(
    hm: HouseMind, clock: SimulatedClock, ha: FakeHomeAssistant, minutes: int, l_per_5min: float
) -> None:
    for _ in range(minutes // 5):
        value = float(ha.states["sensor.water_meter"].state) + l_per_5min / 1000
        await ha.simulate("sensor.water_meter", f"{value:.4f}")
        for _ in range(10):
            await clock.advance(30)
            assert hm.world is not None
            hm.world.modes.mode = HouseMode.SLEEP
            await hm.tick()


def test_litres_from_any_unit() -> None:
    now = datetime(2026, 10, 19, tzinfo=UTC)
    assert litres(State("s", "1.5", {"unit_of_measurement": "m³"}, now)) == 1500
    assert litres(State("s", "20", {"unit_of_measurement": "L"}, now)) == 20
    assert litres(State("s", "unavailable", {}, now)) is None


async def test_water_running_at_night_is_reported_and_the_valve_closes(tmp_path: Path) -> None:
    hm, clock, ha = await start(datetime(2026, 10, 19, 1, 0, tzinfo=TZ), tmp_path)
    assert hm.water.meters == ["sensor.water_meter"] and hm.learning is not None
    hm.learning.steps[("water_leak_flow", "garage")] = Step.AUTONOMOUS
    await run_water(hm, clock, ha, 100, 0.6)  # a dripping tap, under two hours
    assert not [n for n in ha.notifications if "water" in n[2]]
    await run_water(hm, clock, ha, 40, 0.6)
    assert ha.states["valve.water_main"].state == "closed"
    closing = [d for d in hm.status.decisions if d.request.skill == "water_leak_flow"]
    assert "terwijl niemand het gebruikt" in " ".join(hm.explain(closing[-1], 2, "nl"))
    # Only emergencies go out at night: the message waits for the morning (E-19).
    assert not [n for n in ha.notifications if "dichtgedraaid" in n[2]]
    assert hm.world is not None
    await clock.advance(4 * 3600)  # 06:20
    await ha.simulate("binary_sensor.hall_motion", "on")  # the house wakes up
    await clock.advance(30)
    await hm.tick()
    told = [n for n in ha.notifications if "dichtgedraaid" in n[2]]
    assert told and "lek" in told[0][2]
    await hm.stop()


async def test_normal_use_is_no_leak_and_counts_for_the_report(tmp_path: Path) -> None:
    hm, clock, ha = await start(datetime(2026, 10, 19, 7, 0, tzinfo=TZ), tmp_path)
    for _ in range(3):  # a shower, then nothing for a while – three times
        await run_water(hm, clock, ha, 15, 20)
        await run_water(hm, clock, ha, 45, 0)
    assert not [n for n in ha.notifications if "minuten water" in n[2]]
    assert ha.states["valve.water_main"].state == "open"
    assert round(hm.water.today_l) == 180
    # The next days the total moves to the week.
    await clock.advance(timedelta(days=1).total_seconds())
    await hm.tick()
    data = await hm.week_data(clock.now())
    assert data["water_l"] == 180
    assert "Water deze week: 180 liter." in hm.report_text(data, 3, "nl")
    await hm.stop()
