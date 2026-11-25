"""The prices in the energy plan (E-304), asked for in the real house: "Plan voor de komende uren"
showed no prices, and whether a price is bare or with energy tax may only come from what the
integration states – never a guess. Each test fails without it; what it checks is its
docstring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from housemind.core.model import State
from housemind.energy.prices import PriceWords, load_price_words, price_basis
from housemind.runtime import HouseMind
from tests.test_energy_screens import ROOT, _snap, start

WORDS = load_price_words([ROOT / "knowledge" / "energy"])


def _his_price(hm: HouseMind, ha: Any, low: bool = True) -> None:
    """A price entity like the one in the real house: euro per kWh, today's prices per quarter,
    "additional costs 0" and nothing in its name about what is in it."""
    old = ha.states["sensor.electricity_price"]
    attrs = {
        **old.attributes,
        "friendly_name": "nordpool_kwh_nl_eur_3_10_021",
        "additional_costs_current_hour": 0,
        "price_in_cents": False,
    }
    if low:  # cheap enough for the old guess to call it a bare market price
        attrs["raw_today"] = [
            {**item, "value": 0.12} for item in old.attributes.get("raw_today", [])
        ]
        attrs["raw_tomorrow"] = [
            {**item, "value": 0.11} for item in old.attributes.get("raw_tomorrow", [])
        ]
    ha.states["sensor.electricity_price"] = State(old.entity_id, "0.12", attrs, old.last_changed)
    assert hm.world is not None and hm.energy is not None
    hm.world.states["sensor.electricity_price"] = ha.states["sensor.electricity_price"]
    hm.energy.replan(hm.world.states, hm.clock.now(), force=True)


async def test_the_plan_shows_its_prices_and_what_is_in_them(tmp_path: Path) -> None:
    """Under the plan: the lowest and highest price per kWh with their times, and one line on
    what is in the price; every block of "Wat ik ga doen" has its price."""
    hm, _, _ = await start(tmp_path)
    energy = (await _snap(hm, 3))["energy"]
    assert energy["priceRange"].startswith("Prijs per kWh: van € ")
    assert energy["priceBasis"] == hm.languages.text("nl", "ui.energy.basis.market")
    assert energy["steps"] and all(s["price"].endswith("/kWh") for s in energy["steps"])
    await hm.stop()


async def test_a_low_price_is_not_taken_for_a_bare_one(tmp_path: Path) -> None:
    """Found in the real house: prices below € 0.15 were taken for bare market prices and got
    energy tax and VAT added. Now only what the integration says counts: "no additional costs"
    is shown as it is, and HouseMind adds nothing."""
    hm, _, ha = await start(tmp_path)
    _his_price(hm, ha)
    assert hm.energy is not None and hm.energy.plan is not None
    known = [s.buy for s in hm.energy.plan.slots[:96] if s.buy is not None]
    assert known and max(known) <= 0.12 + 1e-9  # nothing added
    energy = (await _snap(hm, 3))["energy"]
    assert energy["priceBasis"] == hm.languages.text("nl", "ui.energy.basis.no_extra")
    assert "€ 0,12" in energy["priceRange"]
    await hm.stop()


async def test_the_administrator_may_say_what_the_price_is(tmp_path: Path) -> None:
    """In House settings the administrator can still choose: a market price (HouseMind adds the
    country's tax) – and the line says that it was chosen."""
    hm, _, ha = await start(tmp_path)
    _his_price(hm, ha)
    assert hm.energy is not None and hm.world is not None
    hm.energy.settings.price_mode = "market"
    hm.energy.replan(hm.world.states, hm.clock.now(), force=True)
    assert hm.energy.plan is not None
    known = [s.buy for s in hm.energy.plan.slots[:96] if s.buy is not None]
    assert min(known) > 0.12  # tax and VAT added
    energy = (await _snap(hm, 3))["energy"]
    assert energy["priceBasis"] == hm.languages.text("nl", "ui.energy.basis.set_market")
    await hm.stop()


def test_what_a_price_entity_says_about_itself() -> None:
    """All-in or market from its own name, extra costs and VAT from its attributes; else
    unknown – never from how high the prices are."""

    def state(name: str, **attrs: Any) -> State:
        return State("sensor.price", "0.1", {"friendly_name": name, **attrs}, None)  # type: ignore[arg-type]

    assert price_basis(state("Current electricity price (All-in)"), WORDS).kind == "all_in"
    assert price_basis(state("Electricity market price"), WORDS).kind == "market"
    assert price_basis(state("Stroom", additional_costs_current_hour=0.13), WORDS).kind == "extra"
    basis = price_basis(state("Stroom", additional_costs_current_hour=0, include_vat=True), WORDS)
    assert (basis.kind, basis.vat) == ("no_extra", True)
    assert price_basis(state("Stroom"), WORDS).kind == "unknown"
    assert price_basis(state("Electricity market price"), PriceWords()).kind == "unknown"


async def test_a_negative_price_keeps_its_minus(tmp_path: Path) -> None:
    """Found in the real house: the lowest price of the day, −€ 0,006 at 12:45, was shown as
    "van € 0,01 (12:45)", and a block charged "omdat de stroomprijs negatief is" said
    "€ 0,00/kWh". Now a price keeps its sign, and three decimals where two say too little."""
    hm, _, ha = await start(tmp_path)
    _his_price(hm, ha)
    old = ha.states["sensor.electricity_price"]
    attrs = dict(old.attributes)
    today = attrs.get("raw_today", [])
    attrs["raw_today"] = [
        {**item, "value": -0.006 if n < len(today) - 8 else 0.175} for n, item in enumerate(today)
    ]
    ha.states[old.entity_id] = State(old.entity_id, "-0.006", attrs, old.last_changed)
    assert hm.world is not None and hm.energy is not None
    hm.world.states[old.entity_id] = ha.states[old.entity_id]
    hm.energy.replan(hm.world.states, hm.clock.now(), force=True)
    energy = (await _snap(hm, 3))["energy"]
    assert "van −€ 0,006 (" in energy["priceRange"] and "tot € 0,175 (" in energy["priceRange"]
    negative = [s for s in energy["steps"] if s["price"] and s["price"].startswith("−")]
    assert negative and negative[0]["price"] == "−€ 0,006/kWh"
    await hm.stop()
