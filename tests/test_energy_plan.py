"""The energy plan (H10.2): explainable rules, checked with invented quarter prices."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from housemind.energy.model import EnergySettings, Slot
from housemind.energy.plan import Start, make_plan

T0 = datetime(2026, 10, 19, 22, 0, tzinfo=UTC)  # midnight local
Q = timedelta(minutes=15)
ROOT = Path(__file__).resolve().parents[1]


def slots(
    prices: list[float], house: float = 400.0, solar: list[float] | None = None
) -> list[Slot]:
    """One slot per quarter; ``prices`` per hour."""
    out = []
    for h, price in enumerate(prices):
        for q in range(4):
            i = h * 4 + q
            out.append(
                Slot(T0 + i * Q, price, price * 0.8, (solar or [0.0] * len(prices))[h], house)
            )
    return out


NIGHT_CHEAP_EVENING_PEAK = [0.15] * 6 + [0.30] * 12 + [0.45] * 4 + [0.30] * 2 + [0.30] * 12


def test_giving_back_is_limited_at_a_negative_price() -> None:
    prices = [0.20] * 12 + [-0.05] * 3 + [0.20] * 21
    sun = [0.0] * 12 + [5000.0] * 3 + [0.0] * 21
    start = Start(has_export_limit=True)
    plan = make_plan(slots(prices, solar=sun), start, EnergySettings(), T0)
    limit = [s for s in plan.steps if s.device == "export"]
    assert limit and limit[0].value == 0.0 and limit[0].start == T0 + timedelta(hours=12)
    # The home battery and the car are never in the plan (E-363).
    assert {s.device for s in plan.steps} <= {"boiler", "heat_pump", "appliance", "export"}


def test_boiler_heats_in_the_cheapest_window_and_legionella_stays() -> None:
    start = Start(boiler_c=50.0, boiler_base=50.0, has_boiler=True)
    prices = [0.30] * 14 + [0.10] * 2 + [0.30] * 20
    plan = make_plan(slots(prices), start, EnergySettings(), T0)
    boiler = [s for s in plan.steps if s.device == "boiler"]
    assert boiler and boiler[0].start == T0 + timedelta(hours=14) and boiler[0].value == 60.0
    due = make_plan(
        slots([0.30] * 36),
        Start(boiler_c=50.0, boiler_base=50.0, has_boiler=True, legionella_due=True),
        EnergySettings(),
        T0,
    )
    assert [s.why for s in due.steps if s.device == "boiler"] == ["legionella"]


def test_heat_pump_preheats_before_the_evening_peak() -> None:
    plan = make_plan(
        slots(NIGHT_CHEAP_EVENING_PEAK),
        Start(heat_base=20.0, has_heat_pump=True),
        EnergySettings(),
        T0,
    )
    preheat = [s for s in plan.steps if s.device == "heat_pump"]
    assert preheat and preheat[0].end == T0 + timedelta(hours=18) and preheat[0].value == 1.0


def test_appliance_starts_in_the_cheapest_two_hours() -> None:
    prices = [0.30] * 5 + [0.12] * 2 + [0.30] * 29
    plan = make_plan(
        slots(prices), Start(appliances_ready=("button.washer_start",)), EnergySettings(), T0
    )
    start = [s for s in plan.steps if s.device == "appliance"]
    assert (
        start
        and start[0].start == T0 + timedelta(hours=5)
        and start[0].target == "button.washer_start"
    )


def test_peak_limit_holds_the_boiler_back() -> None:
    start = Start(
        boiler_c=50.0,
        boiler_base=50.0,
        has_boiler=True,
        legionella_due=True,
        capacity_minimum_kw=2.5,
    )
    plan = make_plan(slots([0.30] * 36, house=800.0), start, EnergySettings(), T0)
    assert max(plan.grid_w) <= 2500.0 + 1
    assert max(plan.boiler_w) <= 2500.0 - 800.0 + 1


async def test_plan_waits_for_the_baseline_then_acts_through_the_gate(tmp_path: Path) -> None:
    import re
    from zoneinfo import ZoneInfo

    from housemind.core.clock import SimulatedClock
    from housemind.demo.energy_sim import EnergySim
    from housemind.demo.houses import load_demo_house
    from housemind.knowledge.catalog import Step as Stage
    from housemind.runtime import HouseMind, Paths

    clock = SimulatedClock(datetime(2026, 10, 19, 21, 0, tzinfo=UTC))  # 23:00 local
    house = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    assert hm.energy is not None and hm.learning is not None and hm.explainer is not None
    sim = EnergySim(house.ha, ZoneInfo("Europe/Amsterdam"))

    async def run(hours: int) -> None:
        await sim.step(clock.now())
        for _ in range(hours * 12):
            await clock.advance(300)
            await sim.step(clock.now())
            await hm.tick()

    await run(6)
    assert hm.energy.plan is not None and hm.energy.plan.steps  # planned and shown …
    assert not hm.energy.planning(clock.now()) and hm.energy.baseline_days_left(clock.now()) == 14
    energy_rows = await hm.db.fetchall(
        "SELECT * FROM decisions WHERE source_layer = 'planner' AND trace LIKE '%energy_%'"
    )
    assert energy_rows == []  # … but the first two weeks HouseMind only measures (S-03)

    # Two weeks later, with the energy skills trusted.
    hm.energy.since = clock.now() - timedelta(days=15)
    for skill in ("energy_boiler", "energy_export"):
        hm.learning.steps[(skill, "utility")] = Stage.AUTONOMOUS
        hm.learning.steps[(skill, "garage")] = Stage.AUTONOMOUS
    await run(24)
    done = [
        d
        for d in hm.status.decisions
        if (d.request.skill or "").startswith("energy_") and d.executed
    ]
    assert done, "the plan should act after the baseline"
    assert any(d.request.entity_id == "water_heater.boiler" for d in done)
    assert not any(
        "battery" in d.request.entity_id or "charge" in d.request.entity_id for d in done
    )
    for decision in done:
        line = hm.explainer.explain(decision, 3, "nl", clock.now())
        text = " ".join(line)
        assert text.startswith("Ik heb") and not re.search(
            r"\b(energy|action|reason)\.[a-z_]+", text
        ), text
    await hm.stop()


SUNNY_DAY = [0.0] * 10 + [2500.0] * 6 + [0.0] * 20  # sun from 10:00 to 16:00 (hourly)
# Night cheap, the day expensive and giving back pays well (net metering): by cost the boiler
# heats at night; with "own power" it heats on the sun.
NET_METERING = [0.10] * 6 + [0.30] * 18 + [0.10] * 6 + [0.30] * 6


def test_the_goal_decides_between_the_cheap_night_and_own_sun() -> None:
    """E-66: the administrator's energy goal (found by the manual: the choice did nothing)."""

    def boiler(goal: str) -> list[int]:
        start = Start(boiler_c=50.0, boiler_base=50.0, has_boiler=True)
        settings = EnergySettings(goal=goal)
        rows = slots(NET_METERING, solar=SUNNY_DAY)
        rows = [Slot(r.start, r.buy, r.buy, r.solar_w, r.house_w) for r in rows]  # 1:1 back
        plan = make_plan(rows, start, settings, T0)
        return [(s.start - T0).seconds // 3600 for s in plan.steps if s.device == "boiler"]

    assert boiler("cost") and all(h < 6 for h in boiler("cost"))  # the cheap night
    assert boiler("own_power") and all(10 <= h < 16 for h in boiler("own_power"))  # the sun
    assert boiler("co2") == boiler("own_power")
