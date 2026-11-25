"""The energy screen per level (E-73, E-364) and its settings (E-66, E-71)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

from housemind.core.clock import SimulatedClock
from housemind.demo.energy_sim import EnergySim
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.server.web import create_app
from tests.helpers import next_message as _next

ROOT = Path(__file__).resolve().parents[1]


async def start(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, Any]:
    clock = SimulatedClock(datetime(2026, 10, 19, 21, 0, tzinfo=UTC))
    house = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    sim = EnergySim(house.ha, ZoneInfo("Europe/Amsterdam"))
    await sim.step(clock.now())
    for _ in range(12 * 3):
        await clock.advance(300)
        await sim.step(clock.now())
        await hm.tick()
    return hm, clock, house.ha


async def _snap(hm: HouseMind, level: int) -> dict[str, Any]:
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    viewer.level = level
    viewer.preferences["language"] = "nl"
    return await views.snapshot(hm, viewer)


async def test_energy_per_level(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    one, two, three, four = [await _snap(hm, level) for level in (1, 2, 3, 4)]
    assert one["features"]["energy"] is True and "energy" not in one  # Carefree sees nothing
    assert two["energy"]["sentence"].startswith("Ik meet nog 14 dagen")  # one sentence
    assert "chart" not in two["energy"]
    # Now and today from Informed (E-364): each battery and the car, only shown (E-363).
    live = two["energy"]["live"]
    assert live["batteries"] and live["batteries"][0]["pct"] is not None
    assert live["car"] is not None and live["car"]["plugged"] is True
    assert "charging" not in three["energy"] and "batteries" not in three["energy"]
    chart = three["energy"]["chart"]
    assert len([p for p in chart if not p["past"]]) == 72
    assert all(p["battery"] == 0 and p["car"] == 0 for p in chart if not p["past"])
    assert {"today", "month"} <= set(three["energy"]["costs"]) and "tips" in three["energy"]
    assert "table" not in three["energy"]
    # The table per quarter is gone (QA 3, schermen 4): the chart shows the plan.
    assert "table" not in four["energy"] and "users" in four["energy"]
    await hm.stop()


async def test_after_the_baseline_the_sentence_tells_the_next_step(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path)
    assert hm.energy is not None and hm.world is not None
    hm.energy.since = clock.now() - timedelta(days=15)
    hm.energy.replan(hm.world.states, clock.now(), force=True)
    snap = await _snap(hm, 3)
    sentence = snap["energy"]["sentence"]
    assert sentence.startswith(("Straks:", "Er staat nu niets")), sentence
    steps = snap["energy"]["steps"]
    assert steps and all(s["why"] and not s["why"].startswith("energy.") for s in steps)
    await hm.stop()


async def test_energy_settings_over_the_websocket(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    assert hm.energy is not None
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        # Charging is no longer set here (E-363): the old message does nothing.
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello"})
        await _next(ws, "snapshot")
        await ws.send_json({"type": "set_charging", "mode": "fast"})
        await ws.send_json({"type": "energy_setting", "goal": "own_power", "peakLimitKw": 5})
        await _next(ws, "snapshot")
        assert hm.energy.settings.goal == "own_power" and hm.energy.settings.peak_limit_kw == 5
        assert not hasattr(hm.energy.settings, "charge_mode")
        await ws.send_json({"type": "energy_setting", "peakLimitKw": 99})  # out of bounds
        await _next(ws, "snapshot")
        assert 1 <= (hm.energy.settings.peak_limit_kw or 0) <= 50  # never outside the bounds
        await ws.send_json({"type": "get", "what": "energy_settings"})
        data = (await _next(ws, "data"))["data"]
        assert data["country"] == "NL" and data["has"]["battery"]
        assert "batteryReservePct" not in data
        await ws.close()
    await hm.stop()


async def test_while_measuring_the_sentence_says_what_comes_after(tmp_path: Path) -> None:
    """Asked in the real house: "wat bedoel je met 'slim plannen'?" (E-388). The sentence names
    what HouseMind steers in this house, only when it pays – or that it steers nothing – and
    Lees meer opens the part of the manual that explains it."""
    hm, _, _ = await start(tmp_path)
    assert hm.energy is not None
    energy = (await _snap(hm, 2))["energy"]
    sentence = energy["sentence"]
    assert "slim plannen" not in sentence
    assert (
        "Daarna maak ik de boiler alleen extra warm als dat echt oplevert, verwarm ik" in sentence
    )
    assert "en lever ik minder terug als de prijs negatief is." in sentence
    manual = (ROOT / "knowledge" / "manual" / "nl.html").read_text(encoding="utf-8")
    assert f'id="{energy["more"]}"' in manual
    # A house where HouseMind steers nothing says so honestly.
    s = hm.energy.setup
    s.boiler = s.heat_pump = s.export_limit = None
    s.appliances = []
    nothing = (await _snap(hm, 2))["energy"]["sentence"]
    assert "Sturen doe ik in dit huis niets: ik laat zien wat het gebruikt en kost." in nothing
    english = await views.energy(hm, "en", 2)
    assert english is not None and "I don't steer anything in this house" in english["sentence"]
    await hm.stop()
