"""Uit na … zonder beweging, in a real browser (E-575, E-582): in Brein › Apparaten each lamp's
row says how long it waits once nobody is there, and the lamp's window has a slider from 0 to
30 minutes; "Laat HouseMind het leren" gives it back. Like everything in the window it is kept
only with the one Bewaren; closing without it keeps nothing. Where the values and the colour
come from is one short block at the top.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_lamp_wait.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_ui_icons import ADMIN, CLICK, DIST, SEEN

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
SHOTS = os.environ.get("HOUSEMIND_SHOTS")
LAMP = "light.living_reading"

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

OPEN = """async (id) => {
  document.querySelector(`main li[data-id="${id}"] button.levels`).click();
  await new Promise((r) => setTimeout(r, 800));
}"""
ROW = (
    """(id) => document.querySelector(`main li[data-id="${id}"] .off-wait`)?.textContent ?? \"\""""
)
WAIT = """() => document.querySelector("div.levels .wait")?.textContent ?? \"\""""
# Let the slider go at a number of minutes, as a finger or the arrow keys do.
SLIDE = """async (minutes) => {
  const input = document.querySelector("div.levels .wait input[type=range]");
  input.value = String(minutes);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 900));
}"""
LEARN = """async () => {
  document.querySelector("div.levels .wait button").click();
  await new Promise((r) => setTimeout(r, 900));
}"""
SAVE = """async () => {
  document.querySelector("div.levels .buttons button.primary").click();
  await new Promise((r) => setTimeout(r, 900));
}"""
TOP = """() => {
  const levels = document.querySelector("div.levels");
  const from = levels.querySelector(".from");
  const kids = [...levels.children];
  const last = levels.lastElementChild.classList.contains("buttons");
  return [from?.textContent ?? "", kids.indexOf(from), last];
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind]]:
    clock = SimulatedClock(datetime(2026, 11, 2, 19, 30, tzinfo=UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm
    await runner.cleanup()
    await hm.stop()


async def test_a_lamps_wait_is_set_with_a_slider_and_given_back(
    house: tuple[str, HouseMind],
) -> None:
    url, hm = house

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
        await page.goto(url)
        await page.wait_for_selector(".orb", timeout=10000)
        await page.evaluate(CLICK, ["nav button", t("tabs.brain")])
        await page.evaluate(CLICK, ["main button", t("ui.brain.tab.devices")])
        await page.wait_for_selector(f'main li[data-id="{LAMP}"] .off-wait')
        # The row says it, next to the standen: the living room's start value.
        assert await page.evaluate(ROW, LAMP) == "Uit na 20 min zonder beweging · startwaarde"

        await page.evaluate(OPEN, LAMP)
        await page.wait_for_selector("div.levels .wait input[type=range]")
        text = await page.evaluate(WAIT)
        assert "Uit als er niemand meer is" in text and "startwaarde voor deze ruimte" in text

        # One block at the top says where the values and the colour come from; nothing below.
        top, place, last = await page.evaluate(TOP)
        assert "Startwaarden: Gedimd 15% en warm" in top and place == 1, (top, place)
        assert last is True, last
        if SHOTS:
            await page.screenshot(path=f"{SHOTS}/standen-boven.png")

        # 0: off the moment nobody is there – only in the window until Bewaren.
        await page.evaluate(SLIDE, 0)
        assert hm.light_levels.off_wait(LAMP) is None
        text = await page.evaluate(WAIT)
        assert "Meteen" in text and "Laat HouseMind het leren" in text
        await page.evaluate(SAVE)
        assert hm.light_levels.off_wait(LAMP) == 0
        await page.evaluate(SLIDE, 4)
        await page.evaluate(SAVE)
        assert hm.light_levels.off_wait(LAMP) == 4
        assert "4 min" in await page.evaluate(WAIT)
        if SHOTS:
            await page.evaluate(
                "() => document.querySelector('div.levels .wait').scrollIntoView({block: 'center'})"
            )
            await page.wait_for_timeout(300)
            await page.screenshot(path=f"{SHOTS}/uit-na-minuten.png")

        # Back to what the house teaches, with Bewaren.
        await page.evaluate(LEARN)
        assert "Laat HouseMind het leren" not in await page.evaluate(WAIT)
        assert hm.light_levels.off_wait(LAMP) == 4
        await page.evaluate(SAVE)
        assert hm.light_levels.off_wait(LAMP) is None

        # On a phone the slider fits the window.
        await page.set_viewport_size({"width": 390, "height": 844})
        box = await page.evaluate(
            """() => { const r = document.querySelector("div.levels .wait input[type=range]")
                         .getBoundingClientRect(); return [r.left, r.right, window.innerWidth]; }"""
        )
        assert box[0] >= 0 and box[1] <= box[2] and box[1] - box[0] > 100, box
        await browser.close()
