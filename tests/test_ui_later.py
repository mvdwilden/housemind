"""Straks in a real browser. The administrator (14-11-2026) found 'Wat ik nu zou doen' a page
that says little, and chose the mockup: under the bars of Ritme the next moments of the day,
each with what HouseMind will then do and a chip with what it may. On Betrokken: a line of time
with a dot and an icon per moment, the time, the name, and per line the device's icon, the words
and the chip in words; on a phone the chip under its line and nothing scrolls sideways. On
Expert the page under Meer is gone, and since 0.3.26 Meer itself, with its trace and stream.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_later.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from housemind import __version__
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from housemind.world.calendar import DayType
from housemind.world.house_mode import HouseMode
from tests.test_rhythm import local

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
SEEN = f'{{"onboarded": true, "tour": [1, 2, 3, 4], "seen_version": "{__version__}"}}'
ADMIN = "demo-user-1"
SHOTS = os.environ.get("HOUSEMIND_SHOTS")  # a folder for screenshots, to look at by hand

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

TAB = """async (name) => {
  [...document.querySelectorAll("nav button")]
    .find((b) => b.offsetParent && b.textContent.includes(name)).click();
  await new Promise((r) => setTimeout(r, 1200));
}"""
SUBTAB = """async (name) => {
  [...document.querySelectorAll("main button")]
    .find((b) => b.textContent.trim().startsWith(name)).click();
  await new Promise((r) => setTimeout(r, 1500));
}"""
LATER = """() => {
  const card = document.querySelector("section.later");
  const box = (el) => el.getBoundingClientRect();
  return {
    title: card.querySelector("h2").textContent,
    afterBars: document.querySelector("section.rhythm .bars")
      .compareDocumentPosition(card) & Node.DOCUMENT_POSITION_FOLLOWING,
    moments: [...card.querySelectorAll(".moment")].map((m) => ({
      dot: !!m.querySelector(".dot svg"),
      when: m.querySelector(".when").textContent.replace(/\\s+/g, " ").trim(),
      lines: [...m.querySelectorAll(".lines li")].map((li) => ({
        icon: !!li.querySelector(".icon svg"),
        text: li.querySelector(".text").textContent,
        chip: li.querySelector(".chip").textContent,
        below: box(li.querySelector(".chip")).top >= box(li.querySelector(".text")).bottom - 1,
      })),
      nothing: m.querySelector(".nothing")?.textContent ?? null,
    })),
    page: [document.documentElement.scrollWidth, window.innerWidth],
  };
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind]]:
    # Tuesday 10 November, 14:20 in the house; a week of nights to bed at 23:10, up at 6:50.
    clock = SimulatedClock(datetime(2026, 11, 10, 13, 20, tzinfo=UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 3, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    assert hm.world is not None and hm.gate is not None
    hm.world.modes.mode = HouseMode.HOME
    hm.activated = True
    hm.gate.state.inactive = False
    hm.rhythm.wake.clear()
    hm.rhythm.bed.clear()
    for day in range(1, 8):
        for kind in (DayType.WORKDAY, DayType.FREE):
            hm.rhythm.record(HouseMode.SLEEP, HouseMode.HOME, local(day, 6, 50), kind, kind)
            hm.rhythm.record(HouseMode.HOME, HouseMode.SLEEP, local(day, 23, 10), kind, kind)
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm
    await runner.cleanup()
    await hm.stop()


async def test_straks_under_the_bars(house: tuple[str, HouseMind]) -> None:
    url, hm = house

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        for width, height in ((1280, 900), (390, 844)):
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                locale="nl-NL",
                extra_http_headers={"X-Remote-User-Id": ADMIN},
            )
            page: Any = await context.new_page()
            await page.goto(url)
            await page.wait_for_selector(".orb", timeout=10000)
            brain = t("tabs.brain") if width > 800 else t("tabs_short.brain")
            await page.evaluate(TAB, brain)
            await page.evaluate(SUBTAB, t("ui.brain.tab.rhythm"))
            await page.wait_for_selector("section.later .moment")
            card = await page.evaluate(LATER)
            assert card["title"] == "Straks" and card["afterBars"], card
            whens = [m["when"] for m in card["moments"]]
            assert re.fullmatch(r"± 1[67]:\d\d · Het wordt donker", whens[0]), card  # the season
            assert whens[1] == "± 23:10 · Het huis gaat slapen · zoals meestal", card
            assert whens[2] == "± 06:50 · Morgen opstaan · werkdag", card
            assert all(m["dot"] for m in card["moments"]), card
            dusk = card["moments"][0]["lines"]
            assert {"text": "Woonkamer gordijnen sluiten.", "chip": "Zou ik"}.items() <= next(
                line for line in dusk if line["text"].startswith("Woonkamer")
            ).items(), card
            lines = [line for m in card["moments"] for line in m["lines"]]
            assert lines and all(line["icon"] and line["chip"] for line in lines), card
            # On a phone the chip goes under its line; nothing scrolls sideways.
            assert all(line["below"] == (width < 560) for line in lines), card
            assert card["page"][0] <= card["page"][1], card
            if SHOTS:
                await page.locator("section.later").screenshot(path=f"{SHOTS}/later-{width}.png")
            await context.close()

        # Expert: no More menu any more (0.3.26) – no trace, no stream of thoughts, and no 'Wat ik
        # nu zou doen' anywhere in Brain.
        await hm.db.execute("UPDATE people SET level = 4 WHERE user_id = ?", (ADMIN,))
        await hm._refresh_people()
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="nl-NL",
            extra_http_headers={"X-Remote-User-Id": ADMIN},
        )
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector(".orb", timeout=10000)
        await page.evaluate(TAB, t("tabs.brain"))
        await page.wait_for_selector('[role="tablist"]')
        assert await page.locator("[data-more]").count() == 0
        brain = await page.locator("main").inner_text()
        assert not any(w in brain for w in ("Beslisspoor", "Gedachtestroom", "Wat ik nu zou doen"))
        await context.close()
        await browser.close()
