"""Never "Unknown" in Home Assistant (E-234): while HouseMind still measures what the house uses
without steering, "Besparing" says so, with the days left; a house without energy gets no such
sensor. Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

from tests.test_automations import start as plain_house
from tests.test_energy_screens import ROOT, start

SENSOR = ROOT / "integration" / "custom_components" / "housemind" / "sensor.py"


async def test_saving_says_it_is_measuring_with_the_days_left(tmp_path: Path) -> None:
    """The first weeks: "Meten, nog 14 dagen" instead of a number; after: the amount."""
    hm, clock, _ = await start(tmp_path)
    state = await hm.bridge_state()
    assert state["energy"] is True and state["saving"] is None
    assert state["saving_text"] == "Meten, nog 14 dagen"
    assert hm.energy is not None
    hm.energy.since = clock.now() - timedelta(days=15)
    state = await hm.bridge_state()
    assert state["saving_text"] is None and isinstance(state["saving"], int | float)
    await hm.stop()


async def test_a_house_without_energy_gets_no_saving_sensor(tmp_path: Path) -> None:
    """No energy in the house: the bridge says so, and the integration leaves "Besparing" out."""
    hm, _, _ = await plain_house(tmp_path)
    state = await hm.bridge_state()
    assert state["energy"] is False and state["saving"] is None and state["saving_text"] is None
    await hm.stop()
    tree = ast.parse(SENSOR.read_text(encoding="utf-8"))
    setup = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef))
    assert "get('energy', True)" in ast.unparse(setup)
    saving = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HouseMindSaving"
    )
    assert "saving_text" in ast.unparse(saving)  # the words while measuring
