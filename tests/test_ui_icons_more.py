"""Icons in Energie, the Check-up and Test het huis, in a real browser (E-512, design 79, part
C). The boxes of the power flow show the sun, the grid, the battery and the house; the home
battery its streaks; a Check-up card the device it is about; Test het huis the chosen room and,
per line, the device it would move – a lamp at the percentage it would get.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_icons_more.py``
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

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

COUNT = """(selector) => document.querySelectorAll(selector).length"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind]]:
    clock = SimulatedClock(datetime(2026, 11, 2, 18, 30, tzinfo=UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await clock.advance(16 * 60)  # the first look at Home Assistant: the Check-up's cards
    hm._last_review = None
    await hm.tick()
    await hm.settled()
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


async def test_energy_check_up_and_test_show_their_devices(
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

        # Energie: every box of the flow with its icon, and the devices that use power now.
        await page.evaluate(CLICK, ["nav button", t("tabs.energy")])
        await page.wait_for_selector(".flow .box", timeout=10000)
        boxes = await page.evaluate(COUNT, ".flow .box")
        assert boxes >= 3 and await page.evaluate(COUNT, ".flow .box .ic svg") == boxes
        assert await page.evaluate(COUNT, ".users li .ic svg") >= 1
        if SHOTS:
            await page.locator(".flow").screenshot(path=f"{SHOTS}/energy-flow.png")

        # Check-up: a card shows the device it is about.
        await page.evaluate(CLICK, ["nav button", t("tabs.tidy")])
        await page.wait_for_selector(".card .title", state="attached", timeout=10000)
        assert await page.evaluate(COUNT, ".card .title .ic svg") >= 1

        # Test het huis: the room's icon, and per line the device it would move.
        await page.locator("header .corner").get_by_role("button", name=t("tabs.test")).click()
        panel = page.locator("aside.play-column")
        await panel.get_by_role("heading", name=t("tabs.test")).wait_for(timeout=5000)
        await panel.locator("select").select_option("living")
        await panel.get_by_role("button", name=t("test.pretend.someone")).click()
        await panel.get_by_role("button", name=t("test.pretend.dark"), exact=True).click()
        await panel.locator(".result li .ic svg").first.wait_for(timeout=5000)
        assert await page.evaluate(COUNT, "aside.play-column .room .ic svg") == 1
        assert await page.evaluate(COUNT, "aside.play-column .result li .ic.lamp svg") >= 1
        if SHOTS:
            await panel.screenshot(path=f"{SHOTS}/test-house.png")
        await context.close()
        await browser.close()
