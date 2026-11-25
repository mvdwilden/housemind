"""Icons in Brein, the Logboek and the Standen of a lamp, in a real browser (E-511, design 79,
part B). With someone in the living room in the dark and a house rule for the bedroom: the parts
of 'Klaar voor autonomie?' and each rule or automation show what they move; the house rule shows
its room; the Logboek's lines
too; and the Standen of a lamp show the lamp as it is in each stand, its percentage in it.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_icons_brain.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from housemind.brain.rules import HouseRule
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_ui_icons import ADMIN, CLICK, DIST, SEEN

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
SHOTS = os.environ.get("HOUSEMIND_SHOTS")

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

COUNT = """(selector) => document.querySelectorAll(selector).length"""
NUMBERS = (
    """() => [...document.querySelectorAll('.stand-lamp svg text')].map((t) => t.textContent)"""
)


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind, Any]]:
    clock = SimulatedClock(datetime(2026, 11, 2, 18, 30, tzinfo=UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    await hm.save_rule(HouseRule("area", "bedroom", "never", "open"), ADMIN)
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm, demo
    await runner.cleanup()
    await hm.stop()


async def test_brain_and_the_logbook_show_what_they_are_about(
    house: tuple[str, HouseMind, Any],
) -> None:
    url, hm, demo = house

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
        # Someone comes into the living room in the dark: HouseMind thinks and decides.
        assert hm.learning is not None
        hm.learning.steps[("light_on_entry", "living")] = Step.AUTONOMOUS
        await demo.ha.simulate("binary_sensor.living_presence", "on")
        await hm.tick()
        await page.evaluate(CLICK, ["nav button", t("tabs.brain")])

        # Klaar voor autonomie?: every part and every rule with the icon of what it moves.
        await page.evaluate(CLICK, ["main button", t("ui.brain.tab.readiness")])
        await page.wait_for_selector('[data-section="readiness"] > .tiles > .tile')
        tiles = await page.evaluate(COUNT, '[data-section="readiness"] > .tiles > .tile')
        assert tiles >= 2
        assert (
            await page.evaluate(COUNT, '[data-section="readiness"] > .tiles > .tile .part-icon svg')
            == tiles
        )
        assert await page.evaluate(COUNT, ".row .title .part-icon svg") >= 3
        assert await page.evaluate(COUNT, ".row .title .room svg") >= 3
        if SHOTS:
            await page.screenshot(path=f"{SHOTS}/readiness.png")

        # Huisregels: the rule for the bedroom shows the bedroom.
        await page.evaluate(CLICK, ["main button", t("ui.brain.tab.rules")])
        assert await page.evaluate(COUNT, ".rules .rule-icon svg") == 1

        # Apparaten › Standen of a lamp: each stand the lamp with its percentage in it.
        await page.evaluate(CLICK, ["main button", t("ui.brain.tab.devices")])
        await page.evaluate(CLICK, ["main button", t("ui.stands.button")])
        await page.wait_for_selector(".stand-lamp svg text")
        assert await page.evaluate(NUMBERS) == ["15", "35", "60", "100"]
        if SHOTS:
            await page.screenshot(path=f"{SHOTS}/standen.png")
        await page.keyboard.press("Escape")

        # Logboek: what HouseMind did or thought, with the icon of its device.
        await page.evaluate(CLICK, ["nav button", t("tabs.logbook")])
        await page.wait_for_selector(".line .dev-icon svg", state="attached")
        assert await page.evaluate(COUNT, ".line .dev-icon svg") >= 1
        await context.close()
        await browser.close()
