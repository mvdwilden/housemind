"""Only steer what pays (design 36, E-374 – E-380): extra warmth only when it is worth more later
after what leaks away, only as warm as needed, legionella only when the boiler does not do it
itself, preheating only when the house keeps its warmth long enough, and a part that cost money
for three weeks rests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.core.clock import SimulatedClock
from housemind.core.model import State
from housemind.demo.houses import load_demo_house
from housemind.energy.heat import HeatModel, kept, worth
from housemind.energy.ledger import Ledger
from housemind.energy.model import EnergySettings, Slot
from housemind.energy.plan import Start, make_plan
from housemind.runtime import HouseMind, Paths

T0 = datetime(2026, 10, 19, 22, 0, tzinfo=UTC)  # midnight local
Q = timedelta(minutes=15)
ROOT = Path(__file__).resolve().parents[1]


def slots(prices: list[float], solar: list[float] | None = None, sell: float = 0.8) -> list[Slot]:
    """One slot per quarter; ``prices`` per hour."""
    out = []
    for h, price in enumerate(prices):
        for q in range(4):
            sun = (solar or [0.0] * len(prices))[h]
            out.append(Slot(T0 + (h * 4 + q) * Q, price, price * sell, sun, 400.0))
    return out


def evening_heat(hours: int = 36, at: tuple[int, ...] = (21,)) -> tuple[float, ...]:
    """The boiler heats by itself in these hours (°C per quarter)."""
    return tuple(3.0 if (i // 4) % 24 in at else 0.0 for i in range(hours * 4))


def use(per_day: float, hours: int = 36) -> tuple[float, ...]:
    return tuple(per_day / 96 for _ in range(hours * 4))


BOILER = Start(boiler_c=50.0, boiler_base=50.0, has_boiler=True, young=False)
NIGHT_CHEAP = [0.10] * 5 + [0.30] * 31  # cheap until 05:00, dear the rest


def boiler_steps(prices: list[float], start: Start, **kw: Any) -> list[Any]:
    plan = make_plan(slots(prices, **kw), start, EnergySettings(), T0)
    return [s for s in plan.steps if s.device == "boiler"]


# -- the rule (B-268) ------------------------------------------------------------------------


def test_the_rule_counts_what_leaks_away() -> None:
    assert worth(0.10, 0.30, kept(0.01, 10), 2.0).ok  # clearly cheaper, little lost
    assert not worth(0.28, 0.30, 0.95, 2.0).ok  # the loss eats the difference
    assert not worth(0.10, 0.30, kept(0.5, 10), 2.0).ok  # it leaks away before it is used
    assert not worth(0.20, 0.23, 1.0, 2.0, young=True).ok  # a young model wants more margin
    assert worth(-0.02, 0.10, 0.5, 2.0).ok  # a negative price always pays
    assert not worth(0.10, 0.30, 1.0, 0.05).ok  # less than two cents is not worth a block


# -- the boiler (E-374, E-375, B-270, B-271) -----------------------------------------------------


def test_the_boiler_only_when_it_pays() -> None:
    start = replace(BOILER, boiler_heat=evening_heat(), boiler_use=use(18))
    steps = boiler_steps(NIGHT_CHEAP, start)
    assert steps and steps[0].why == "boiler_cheap" and steps[0].start == T0
    assert steps[0].saving > 0 and steps[0].params["kept_pct"] < 100
    # A small difference: not worth the warmth that leaks away.
    flat = [0.29] * 5 + [0.30] * 31
    assert not boiler_steps(flat, start)
    # Sun to spare that would earn (almost) nothing: worth it.
    sun = [0.0] * 11 + [4000.0] * 4 + [0.0] * 21
    sunny = boiler_steps([0.30] * 36, start, solar=sun, sell=0.05)
    assert sunny and sunny[0].why == "boiler_sun"
    # A negative price: always, up to the limit.
    negative = boiler_steps([-0.02] * 2 + [0.30] * 34, start)
    assert negative and negative[0].why == "negative_price" and negative[0].value == 60.0


def test_as_warm_as_needed_until_the_next_cheap_moment() -> None:
    little = boiler_steps(
        NIGHT_CHEAP, replace(BOILER, boiler_heat=evening_heat(), boiler_use=use(4))
    )
    much = boiler_steps(
        NIGHT_CHEAP, replace(BOILER, boiler_heat=evening_heat(), boiler_use=use(18))
    )
    assert little and much and 50 < little[0].value < much[0].value <= 60.0
    capped = boiler_steps(
        NIGHT_CHEAP,
        replace(BOILER, boiler_heat=evening_heat(), boiler_use=use(40), boiler_max_c=55.0),
    )
    assert capped and capped[0].value == 55.0  # never above the boiler's own limit


def test_without_the_boilers_temperature_only_sun_or_a_negative_price() -> None:
    blind = replace(BOILER, boiler_c=None, boiler_sensed=False)
    assert not boiler_steps(NIGHT_CHEAP, blind)
    sun = [0.0] * 11 + [4000.0] * 4 + [0.0] * 21
    assert boiler_steps([0.30] * 36, blind, solar=sun, sell=0.05)


def test_legionella_reaches_60_whatever_pays() -> None:
    due = replace(BOILER, legionella_due=True)
    steps = boiler_steps([0.30] * 36, due)
    assert steps and steps[0].why == "legionella" and steps[0].value == 60.0
    assert steps[0].saving == 0.0  # not counted as a saving


async def test_a_boiler_with_its_own_legionella_programme(tmp_path: Path) -> None:
    clock = SimulatedClock(T0)
    house = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    assert hm.energy is not None and hm.world is not None
    states = hm.world.states
    assert hm.energy.start_state(states, clock.now()).legionella_due  # never seen 60 °C
    boiler = states["water_heater.boiler"]
    attrs = {**boiler.attributes, "operation_list": ["eco", "performance", "Legionella"]}
    states["water_heater.boiler"] = State(boiler.entity_id, boiler.state, attrs, clock.now())
    assert not hm.energy.start_state(states, clock.now()).legionella_due
    await hm.stop()


# -- preheating (E-376, B-273, B-274) ------------------------------------------------------------

EVENING_PEAK = [0.20] * 16 + [0.45] * 4 + [0.20] * 16


def preheats(house_loss: float) -> list[Any]:
    start = Start(heat_base=20.0, has_heat_pump=True, young=False, house_loss=house_loss)
    plan = make_plan(slots(EVENING_PEAK), start, EnergySettings(), T0)
    return [s for s in plan.steps if s.device == "heat_pump"]


def test_preheat_only_when_the_house_keeps_its_warmth() -> None:
    slow = preheats(0.02)
    assert slow and slow[0].end == T0 + timedelta(hours=16) and slow[0].saving > 0
    assert slow[0].params["peak_end"] == T0 + timedelta(hours=20)
    assert not preheats(0.6)  # it would leak away before the peak


# -- learning (B-269, B-274) ----------------------------------------------------------------------


async def test_learning_how_fast_the_boiler_and_the_house_cool_down(tmp_path: Path) -> None:
    clock = SimulatedClock(T0)
    house = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    assert hm.db is not None
    model = HeatModel(hm.db)
    now, temp = T0, 60.0
    for _ in range(16):  # a quiet night: 0.5 °C an hour at 60 °C (twice the standard)
        model.boiler_quarter(now, temp, False, True)
        now, temp = now + Q, temp - 0.125
    assert 0.0105 < model.boiler_loss < 0.0130
    used = model.boiler_quarter(now, temp - 9.0, False, False)
    now += Q
    assert used is not None and used[0] > 8.5  # a shower
    inside = 22.0
    for _ in range(12):  # 2 °C above the wish, 10 °C inside-outside, cooling 0.6 °C an hour
        model.room_quarter(now, inside, 12.0, 20.0)
        now, inside = now + Q, inside - 0.15
    assert 0.05 < model.house_loss < 0.07
    await model.save()
    again = HeatModel(hm.db)
    await again.load()
    assert again.boiler_loss == model.boiler_loss and again.house_loss_n == model.house_loss_n
    await hm.stop()


# -- the ledger and resting (E-378, B-275, B-276) -----------------------------------------------


async def test_a_part_that_cost_money_for_three_weeks_rests(tmp_path: Path) -> None:
    clock = SimulatedClock(T0)
    house = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    assert hm.db is not None
    ledger = Ledger(hm.db)
    for day in (0, 8, 16):
        start = T0 + timedelta(days=day)
        block = ledger.open("boiler", f"b{day}", start, start + timedelta(hours=2), 0.10)
        ledger.close(block, -0.05, start + timedelta(days=1))
    assert "boiler" in ledger.rest and ledger.news == [("boiler", -0.15000000000000002)]
    now = T0 + timedelta(days=18)
    assert "boiler" in ledger.resting(now)
    # After four weeks: one careful try a week; when it pays, it is back.
    later = T0 + timedelta(days=17 + 29)
    assert "boiler" not in ledger.resting(later)
    trial = ledger.open("boiler", "try", later, later + timedelta(hours=2), 0.08)
    assert "boiler" in ledger.resting(later + timedelta(days=1))  # one a week
    ledger.close(trial, 0.04, later + timedelta(days=1))
    assert "boiler" not in ledger.rest
    # One plan made again each quarter is one block, not one per quarter.
    a = ledger.open("heat_pump", "x1", now, now + timedelta(hours=2), 0.05)
    b = ledger.open("heat_pump", "x2", now + Q, now + timedelta(hours=2), 0.05)
    assert a is b
    await ledger.save(now)
    await hm.stop()


async def test_resting_parts_stay_out_of_the_plan() -> None:
    start = replace(
        BOILER, boiler_heat=evening_heat(), boiler_use=use(18), resting=frozenset({"boiler"})
    )
    assert not boiler_steps(NIGHT_CHEAP, start)
    assert boiler_steps(NIGHT_CHEAP, replace(start, legionella_due=True))  # legionella stays


async def test_the_screen_shows_what_each_part_brought(tmp_path: Path) -> None:
    from tests.test_energy_screens import _snap, start

    hm, clock, _ = await start(tmp_path)
    assert hm.energy is not None and hm.energy.ledger is not None
    block = hm.energy.ledger.open("boiler", "k", clock.now(), clock.now() + Q, 0.2)
    hm.energy.ledger.close(block, 0.12, clock.now())
    three = (await _snap(hm, 3))["energy"]
    parts = {p["name"]: p for p in three["parts"]}
    assert parts["Extra warm water"]["text"] == "€ 0,12 bespaard"
    assert parts["Voorverwarmen"]["text"] == "nog niets nagemeten"
    two = (await _snap(hm, 2))["energy"]
    assert "parts" not in two
    await hm.stop()
