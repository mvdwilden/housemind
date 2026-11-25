"""The energy plan in pictures (E-346, B-222 – B-225) and Energie on "Klaar voor autonomie?"
(E-347, B-226), asked for in the real house: "meer informatie in de grafiek qua (positieve/
negatieve) stroomprijzen, forecast, mogelijke acties – geen tabellen, maar grafieken", and "is
Energie ook een domein, net zoals deze?". Since E-363 the home battery and the car are only
shown as measured, before now (B-253). Each test fails without it; what it checks is its
docstring."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from housemind.core.model import State
from housemind.energy.solar_margin import MIN_HOURS, SolarMargin
from housemind.runtime import HouseMind
from tests.test_energy_prices_shown import _his_price
from tests.test_energy_screens import _snap, start


def _day_with_negative_prices(hm: HouseMind, ha: Any) -> None:
    """Prices like the real house on 25-10-2026, as its integration gives them (nothing added):
    negative until the afternoon, dear after."""
    _his_price(hm, ha)
    old = ha.states["sensor.electricity_price"]
    attrs = dict(old.attributes)
    for key in ("raw_today", "raw_tomorrow"):
        items = attrs.get(key, [])
        cut = len(items) * 0.6
        attrs[key] = [
            {**item, "value": -0.006 if n < cut else 0.175} for n, item in enumerate(items)
        ]
    ha.states[old.entity_id] = State(old.entity_id, "-0.006", attrs, old.last_changed)
    assert hm.world is not None and hm.energy is not None
    hm.world.states[old.entity_id] = ha.states[old.entity_id]
    hm.energy.replan(hm.world.states, hm.clock.now(), force=True)


async def test_the_day_clock_from_informed(tmp_path: Path) -> None:
    """Geïnformeerd sees the day clock under the one sentence: the coming 24 hours per quarter
    (price, sun, what HouseMind does), the now and how full the battery is in the middle, and
    the clock in words for who cannot see the colours. Carefree sees no energy at all; the plan
    itself starts at Betrokken."""
    hm, _, _ = await start(tmp_path)
    one, two = await _snap(hm, 1), await _snap(hm, 2)
    assert "energy" not in one
    clock = two["energy"]["clock"]
    assert len(clock["points"]) == 96 and clock["now"].startswith("nu ")
    assert {"price", "solar", "doing"} <= set(clock["points"][0])
    assert "battery" not in clock["points"][0] and clock["soc"] is not None
    assert clock["summary"].startswith("Goedkoopst ") and "duurst" in clock["summary"]
    assert "chart" not in two["energy"]
    await hm.stop()


async def test_the_plan_shows_more_at_expert(tmp_path: Path) -> None:
    """Betrokken gets the plan per half hour with the timeline; Expert per quarter, with the
    price for giving back. Before now what was measured today, with the battery and the car."""
    hm, _, _ = await start(tmp_path)
    three, four = (await _snap(hm, 3))["energy"], (await _snap(hm, 4))["energy"]
    ahead = [p for p in three["chart"] if not p["past"]]
    assert len(ahead) == 72 and "sell" not in ahead[0]
    assert len([p for p in four["chart"] if not p["past"]]) == 144
    assert "sell" in next(p for p in four["chart"] if not p["past"])
    past = [p for p in four["chart"] if p["past"]]
    assert past and len(past) >= 2 * len([p for p in three["chart"] if p["past"]]) - 1
    assert "reserve" not in three and three["currency"] == "€"
    assert isinstance(three["blocks"], list) and isinstance(three["managed"], list)
    await hm.stop()


async def test_the_timeline_says_what_housemind_does_and_why(tmp_path: Path) -> None:
    """With negative prices HouseMind limits giving back: a stretch in the timeline (not a
    list), in the export row, with the reason and the price with its minus sign. The battery
    has no row: HouseMind does not steer it (E-363)."""
    hm, clock, ha = await start(tmp_path)
    assert hm.energy is not None and hm.world is not None
    hm.energy.since = clock.now() - timedelta(days=15)
    _day_with_negative_prices(hm, ha)
    energy = (await _snap(hm, 3))["energy"]
    rows = [b["row"] for b in energy["blocks"]]
    assert "battery" not in rows and "car" not in rows
    order = ["boiler", "heat_pump", "export", "appliance"]
    assert rows == sorted(rows, key=order.index)
    await hm.stop()


async def test_the_margin_of_the_sun_only_after_enough_hours(tmp_path: Path) -> None:
    """Home Assistant's forecast has no range. HouseMind keeps per hour what was forecast and
    measured; with fewer than 20 sunny hours there is no margin – nothing guessed. After that
    Expert sees it around the sun, Betrokken does not."""
    hm, clock, _ = await start(tmp_path)
    assert hm.energy is not None and hm.energy.solar_margin is not None
    margin = hm.energy.solar_margin
    assert margin.band() is None
    four = (await _snap(hm, 4))["energy"]
    assert "solarLow" not in four["chart"][0]
    hour = clock.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=MIN_HOURS + 2)
    for n in range(MIN_HOURS):
        start_of = hour + timedelta(hours=n)
        forecast = {start_of: 1000.0}
        for q in range(4):  # measured: 0.6 to 1.2 of the forecast
            quarter = start_of + timedelta(minutes=15 * q)
            await margin.quarter(quarter, (0.6 + n * 0.03) / 4, forecast)
    low, high = margin.band() or (0.0, 0.0)
    assert 0.6 <= low < 1.0 < high <= 1.2
    again = SolarMargin(hm.db)
    await again.load()
    assert again.band() == margin.band()  # kept over a restart
    four, three = (await _snap(hm, 4))["energy"], (await _snap(hm, 3))["energy"]
    sunny = [p for p in four["chart"] if p["solar"] > 0]
    assert sunny and all(p["solarLow"] <= p["solar"] <= p["solarHigh"] for p in sunny)
    assert "solarLow" not in three["chart"][0]
    await hm.stop()


async def test_energy_is_on_klaar_voor_autonomie(tmp_path: Path) -> None:
    """Energie has its own row on the card: while measuring, how many days are left and per
    skill the house has a row with the days counted; afterwards each skill with its step."""
    hm, clock, _ = await start(tmp_path)
    assert hm.energy is not None
    parts = {p["domain"]: p for p in (await _snap(hm, 3))["readiness"]}
    energy = parts["energy"]
    assert energy["name"] == "Energie" and energy["text"].startswith("Ik meet nog 14 dagen")
    skills = {s["skill"]: s for s in energy["skills"]}
    assert "energy_battery" not in skills and "energy_charging" not in skills  # E-363
    row = skills["energy_boiler"]
    assert row["step"] == "observe" and row["text"] == "Tijdens het meten doe ik nog niets."
    assert row["counters"][0]["key"] == "days" and row["counters"][0]["target"] == 14
    hm.energy.since = clock.now() - timedelta(days=15)
    after = {p["domain"]: p for p in (await _snap(hm, 3))["readiness"]}["energy"]
    assert not after["text"].startswith("Ik meet nog")
    assert all(s["counters"] != [] or s["text"] for s in after["skills"])
    await hm.stop()
