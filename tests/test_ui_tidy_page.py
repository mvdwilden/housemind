"""Home Assistant netjes is a page of its own in the menu, only for an administrator on Expert
(E-449, B-363), in a real browser. The administrator's wish (02-11-2026): "Kan je ‘Home
Assistant netjes’ verplaatsen naar het menu links, en enkel zichtbaar voor de expert? Dan is
deze pagina duidelijker te lezen; nu staat het in een pop-up venster en het maakt alles zo
klein." So: on a computer in the menu on the left, on a phone a fifth tab, with the width of
the page; no longer a group in House settings. Below Expert, ‘Bekijken’ on one of its messages
still shows it, in a window of its own.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_tidy_page.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

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
ADMIN, RESIDENT = "demo-user-1", "demo-user-2"  # the demo house's administrator and a resident

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

# The menu as it is on screen: on a computer on the left, on a phone at the bottom.
MENU = """() => {
  const nav = [...document.querySelectorAll("nav.side, nav.tabs")]
    .find((n) => getComputedStyle(n).display !== "none");
  return [...nav.querySelectorAll("button")].map((b) => {
    const label = b.querySelector(".label");
    return {text: label.textContent.trim(), cut: label.scrollWidth > label.clientWidth + 1};
  });
}"""

# Tap a menu item; then where the page is, and how wide.
OPEN = """async (name) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  [...document.querySelectorAll("nav button")]
    .find((b) => b.offsetParent && b.textContent.trim() === name).click();
  await wait(600);
  const part = document.querySelector('[data-section="tidy"]');
  return {
    heading: document.querySelector("main > h2")?.textContent.trim() ?? null,
    found: Boolean(part), inSheet: Boolean(part?.closest(".sheet")),
    width: part ? part.getBoundingClientRect().width : 0,
    page: document.documentElement.scrollWidth, screen: innerWidth,
  };
}"""

# The groups of House settings, by their titles.
GROUPS = """async (label) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  document.querySelector(`button[aria-label="${label}"]`).click();
  await wait(800);
  const titles = [...document.querySelectorAll(".sheet button.group strong")]
    .map((s) => s.textContent.trim());
  document.querySelector(".sheet .x")?.click();
  await wait(300);
  return titles;
}"""

# On Huis, 'Bekijken' on the message of Home Assistant netjes; then what opened.
VIEW = """async (view) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  [...document.querySelectorAll("main button")].find((b) => b.textContent.trim() === view).click();
  await wait(1000);
  const sheet = document.querySelector(".sheet");
  return {title: sheet?.querySelector("h2")?.textContent.trim() ?? null,
          tidy: Boolean(sheet?.querySelector('[data-section="tidy"]'))};
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind]]:
    clock = SimulatedClock(datetime(2026, 10, 27, 11, 0, tzinfo=UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await clock.advance(16 * 60)  # the first look at Home Assistant: an old backup, and more
    hm._last_review = None
    await hm.tick()
    await hm.settled()
    for user_id in (ADMIN, RESIDENT):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, preferences, created) "
            "VALUES(?,?,?,?,?)",
            (user_id, user_id, 4, SEEN, "2026-10-01"),
        )
    await hm._refresh_people()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm
    await runner.cleanup()
    await hm.stop()


async def _level(hm: HouseMind, user_id: str, level: int) -> None:
    await hm.db.execute("UPDATE people SET level = ? WHERE user_id = ?", (level, user_id))
    await hm._refresh_people()


async def test_the_check_up_is_a_page_in_the_menu_only_on_expert(
    house: tuple[str, HouseMind],
) -> None:
    url, hm = house

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    # Its name since 04-11-2026, chosen by the administrator: Check-up, also on a phone.
    name, short = t("tabs.tidy"), t("tabs_short.tidy")
    assert name == short == "Check-up"
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()

        async def page_for(user_id: str, width: int, height: int) -> Any:
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                locale="nl-NL",
                extra_http_headers={"X-Remote-User-Id": user_id},
            )
            page = await context.new_page()
            await page.goto(url)
            await page.wait_for_selector(".orb", timeout=10000)
            return page

        # The administrator on Expert, on a computer: in the menu on the left, a whole page.
        page = await page_for(ADMIN, 1280, 900)
        assert name in [m["text"] for m in await page.evaluate(MENU)]
        shown = await page.evaluate(OPEN, name)
        assert shown["heading"] == name and shown["found"] and not shown["inSheet"], shown
        assert shown["width"] >= 800, shown  # the width of the page, not a narrow window
        assert shown["page"] <= shown["screen"], shown  # no sideways scrolling
        assert name not in await page.evaluate(GROUPS, t("ui.house_settings"))
        await page.context.close()

        # On a phone: a fifth tab at the bottom, its word whole, and the page fits. Since
        # 0.2.39 Test het huis follows it as a sixth (E-474), also only on Expert; every word
        # still whole.
        page = await page_for(ADMIN, 390, 844)
        menu = await page.evaluate(MENU)
        assert len(menu) == 6 and menu[4] == {"text": short, "cut": False}, menu
        assert menu[5]["text"] == t("tabs_short.test"), menu
        assert not any(m["cut"] for m in menu), menu
        shown = await page.evaluate(OPEN, short)
        assert shown["found"] and not shown["inSheet"] and shown["page"] <= shown["screen"], shown
        await page.context.close()

        # A resident on Expert who is not an administrator never sees it.
        page = await page_for(RESIDENT, 1280, 900)
        assert name not in [m["text"] for m in await page.evaluate(MENU)]
        await page.context.close()

        # The administrator on Betrokken: not in the menu, not in House settings, but
        # 'Bekijken' on its message opens it in a window of its own.
        await _level(hm, ADMIN, 3)
        page = await page_for(ADMIN, 1280, 900)
        assert name not in [m["text"] for m in await page.evaluate(MENU)]
        assert name not in await page.evaluate(GROUPS, t("ui.house_settings"))
        opened = await page.evaluate(VIEW, t("notify.answer.view"))
        assert opened == {"title": "Check-up van Home Assistant", "tidy": True}, opened
        await page.context.close()
        await browser.close()
