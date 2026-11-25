"""The Standen window holds its lamp, in a real browser (E-516, B-442, design 83).

Opening the Standen of a lamp: no tick box that writes the standen over all lamps of the same
type; HouseMind keeps off the lamp while the window is open, a Proef stays on it for minutes, the
window says it is still open every 20 seconds, and closing it puts the lamp back as it was.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_stands_hold.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from housemind.brain.levels import Standen
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_ui_icons import ADMIN, CLICK, DIST, SEEN

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
SHOTS = os.environ.get("HOUSEMIND_SHOTS")
LAMP = "light.living_ceiling"
TWIN = "light.kitchen"  # the same make and model: once the tick box was here

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

# The Standen button of one lamp, by its entity id in the row.
OPEN = """async (id) => {
  const row = [...document.querySelectorAll("main li")]
    .find((li) => li.querySelector(".mono")?.textContent.startsWith(id));
  row.querySelector("button.levels").click();
  await new Promise((r) => setTimeout(r, 800));
}"""
TEXT = """() => document.querySelector("div.levels")?.textContent ?? \"\""""
TRY = """async (stand) => {
  document.querySelectorAll("div.levels .stand .head button")[stand].click();
  await new Promise((r) => setTimeout(r, 800));
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind, Any, SimulatedClock]]:
    clock = SimulatedClock(datetime(2026, 11, 2, 19, 30, tzinfo=UTC))
    demo = load_demo_house("family_home", clock, "nl")
    for lamp in (LAMP, TWIN):
        device = demo.ha.model.devices[lamp]
        demo.ha.model.devices[lamp] = replace(device, manufacturer="Make", model="Bulb")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    await hm.levels_save(TWIN, Standen((3, 8, 14, 20)).to_json())
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm, demo, clock
    await runner.cleanup()
    await hm.stop()


async def test_the_window_holds_its_lamp_until_it_closes(
    house: tuple[str, HouseMind, Any, SimulatedClock],
) -> None:
    url, hm, demo, clock = house
    assert hm.gate is not None
    states = demo.ha.states

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="nl-NL",
            extra_http_headers={"X-Remote-User-Id": ADMIN},
        )
        page: Any = await context.new_page()
        await page.clock.install()  # the window's 20 seconds, without waiting for them
        await page.goto(url)
        await page.wait_for_selector(".orb", timeout=10000)
        await page.evaluate(CLICK, ["nav button", t("tabs.brain")])
        await page.evaluate(CLICK, ["main button", t("ui.brain.tab.devices")])
        await page.evaluate(OPEN, LAMP)
        await page.wait_for_selector(".levels .stand")

        # No tick box for the other lamp of the same type; what the window does, in words.
        text = await page.evaluate(TEXT)
        assert "hetzelfde type" not in text and "Ook voor" not in text
        assert "blijft HouseMind van deze lamp af" in text
        assert hm.gate.state.setting(LAMP, clock.now())

        # Proef Helder: on the lamp, and it stays so.
        assert states[LAMP].state == "off"
        await page.evaluate(TRY, 2)
        assert states[LAMP].state == "on"
        assert "Zo blijft hij tot je op Klaar tikt" in await page.evaluate(TEXT)
        if SHOTS:
            await page.screenshot(path=f"{SHOTS}/standen-houdt-vast.png")
        for _ in range(9):  # three minutes: the window says every 20 s that it is still open
            await clock.advance(20)
            await page.clock.run_for(20_000)
            await page.wait_for_timeout(100)
            await hm.tick()
        assert states[LAMP].state == "on"
        assert hm.gate.state.setting(LAMP, clock.now())

        # Closing the window: back as it was, and HouseMind's again.
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
        assert states[LAMP].state == "off"
        assert not hm.gate.state.setting(LAMP, clock.now())
        # The other lamp of the same type kept its own standen.
        assert hm.light_levels.of(TWIN)[0].pct == (3, 8, 14, 20)
        await context.close()
        await browser.close()
