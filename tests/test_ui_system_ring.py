"""The system of Home Assistant netjes comes first on the page, as a ring (E-451, B-365), in a
real browser. The administrator's wish (02-11-2026): "kan je deze bovenaan de pagina zetten? en
indien van toepassing een pie-chart maken of zo'n klok-grafiek maken van de systeemtechnische
waardes (van vandaag, database, schijf, backups)". So: Systeem right under 'Nu nakijken', with
a ring like the day clock of Energy – outside the disk in 100 parts (the database, the backups,
the old loose backups, the rest, what is free), inside the days since the newest backup – and
pointing at a part says it in words. Since 25-11-2026 (QA 3, schermen 26) only when something
is wrong: when the backup, the database and the disk are well, Systeem is one sentence.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_system_ring.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

import os
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

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
SEEN = f'{{"onboarded": true, "tour": [1, 2, 3, 4], "seen_version": "{__version__}"}}'
ADMIN = "demo-user-1"  # the demo house's administrator
SHOT = os.environ.get("HM_SHOT")  # where to keep a picture of the page, when asked

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

# Open Home Assistant netjes from the menu; then the order of its headings and the ring.
LOOK = """async (name) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  [...document.querySelectorAll("nav button")]
    .find((b) => b.offsetParent && b.textContent.trim() === name).click();
  await wait(800);
  const page = document.querySelector('[data-section="tidy"]');
  const ring = page.querySelector(".ring svg");
  const before = ring ? ring.closest(".ring").nextElementSibling?.textContent : "";
  return {
    headings: [...page.querySelectorAll("h3")].map((h) => h.textContent.trim()),
    ring: Boolean(ring), label: ring?.getAttribute("aria-label") ?? null,
    parts: ring ? ring.querySelectorAll("path.part").length : 0,
    days: ring ? ring.querySelectorAll("path.day").length : 0,
    legend: [...page.querySelectorAll(".ring .legend li")].map((li) => li.textContent.trim()),
    line: page.querySelector(".ring .line")?.textContent.trim() ?? null,
    page: document.documentElement.scrollWidth, screen: innerWidth, before,
  };
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind]]:
    clock = SimulatedClock(datetime(2026, 10, 27, 11, 0, tzinfo=UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await clock.advance(16 * 60)  # the first look at Home Assistant: backups, database, disk
    hm._last_review = None
    await hm.tick()
    await hm.settled()
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her questions wait; the page stays clear
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm
    await runner.cleanup()
    await hm.stop()


async def test_the_system_comes_first_as_a_ring(house: tuple[str, HouseMind]) -> None:
    url, hm = house

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    assert hm.tidy_system is not None and hm.tidy_system["disk"].get("backups")
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        for width, height in ((1280, 900), (390, 844)):  # a computer and a phone
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                locale="nl-NL",
                extra_http_headers={"X-Remote-User-Id": ADMIN},
            )
            page = await context.new_page()
            await page.goto(url)
            await page.wait_for_selector(".orb", timeout=10000)
            name = t("tabs.tidy") if width > 900 else t("tabs_short.tidy")
            seen: dict[str, Any] = await page.evaluate(LOOK, name)
            assert seen["headings"][0] == t("ui.tidy.system.title"), seen["headings"]  # first
            assert seen["ring"] and seen["label"] == t("ui.tidy.system.ring.title"), seen
            assert seen["parts"] == 100 and seen["days"] == 7, seen
            names = [t(f"ui.tidy.system.ring.part.{p}") for p in ("database", "backups", "free")]
            assert all(any(li.startswith(n) for li in seen["legend"]) for n in names), seen
            assert seen["line"] == t("ui.tidy.system.ring.hint")
            assert seen["page"] <= seen["screen"], (width, seen)  # no sideways scrolling
            # Pointing at the outer ring at the top: the first part, the database, in words.
            ring = page.locator(".ring svg")
            await ring.scroll_into_view_if_needed()
            box = await ring.bounding_box()
            assert box is not None
            await page.mouse.move(
                box["x"] + box["width"] * (177 / 348), box["y"] + box["height"] * (30 / 332)
            )
            line = await page.locator(".ring .line").text_content()
            assert (line or "").startswith(t("ui.tidy.system.ring.part.database")), (width, line)
            if SHOT and width > 900:
                await page.locator('[data-section="tidy"]').screenshot(path=SHOT)
            await context.close()
        await browser.close()


async def test_when_all_is_well_the_system_is_one_sentence(house: tuple[str, HouseMind]) -> None:
    """A recent backup, a database that does not grow, a disk with room: no ring and no tiles,
    one sentence – Home Assistant shows backups and storage itself; here a tile is a reason to
    act."""
    url, hm = house
    assert hm.tidy_system is not None
    hm.tidy_system["backup"].update(state="ok", heavy=False)
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="nl-NL",
            extra_http_headers={"X-Remote-User-Id": ADMIN},
        )
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector(".orb", timeout=10000)
        seen: dict[str, Any] = await page.evaluate(LOOK, hm.languages.text("nl", "tabs.tidy"))
        assert seen["headings"][0] == hm.languages.text("nl", "ui.tidy.system.title")
        assert not seen["ring"], seen
        after = await page.evaluate(
            """(title) => [...document.querySelectorAll('[data-section="tidy"] h3')]
              .find((h) => h.textContent.trim() === title).nextElementSibling.textContent.trim()""",
            hm.languages.text("nl", "ui.tidy.system.title"),
        )
        assert after == hm.languages.text("nl", "ui.tidy.system.all_well")
        await browser.close()
