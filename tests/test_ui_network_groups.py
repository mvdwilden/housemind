# ruff: noqa: E501 – the checks in the page are small JavaScript functions
"""A group is one dot for its devices in the network (E-515), in a real browser, from the user's
side. The administrator's wish (14-11-2026): "als er een groep is, zoals gedefinieerd in
apparaten, dan wil ik dat je maar één apparaat laat zien … Ik wil ook dat je, als je met de muis
eroverheen gaat, ziet om welk apparaat het gaat."

In the extended demo house the living room has a Hue room over its ceiling lamp and mood lamp:
the network shows the room's lamps as one dot, not the two lamps beside it. Every dot names its
device for the pointer, also where its icon lies over the circle – before, the name sat on the
circle under the icon, so the pointer never found it.

Runs with the screens: ``pytest -m ui tests/test_ui_network_groups.py`` (a built frontend and
Playwright's Chromium). Each test fails without the change."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_mind import EVENING
from tests.test_ui import AXE, DIST, SEEN

playwright = pytest.importorskip("playwright.async_api")

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not AXE.exists() or not DIST.exists(), reason="frontend not built"),
]
ROOT = Path(__file__).resolve().parents[1]
EXPERT = "user-4"  # Expert: Huis opens with the network
GROUP = "light.living_room"
MEMBERS = ("light.living_ceiling", "light.living_mood")


@pytest.fixture
async def extended(tmp_path: Path) -> AsyncIterator[str]:
    clock = SimulatedClock(EVENING.astimezone(UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (EXPERT, "Bewoner 4", 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/"
    await runner.cleanup()
    await hm.stop()


async def _network(browser: Any, url: str) -> Any:
    ctx = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        color_scheme="dark",
        locale="nl-NL",
        extra_http_headers={"X-Remote-User-Id": EXPERT},
    )
    page = await ctx.new_page()
    await page.goto(url)
    await page.wait_for_selector(".net .spot", timeout=10000)
    close = page.locator(".window[role=dialog] button[aria-label='Sluiten']")
    if await close.count():
        await close.first.click()
    return page


# What the pointer finds in the middle of a dot: the device it names, and the name it shows.
POINTER = """(id) => {
  const spot = document.querySelector(`.net .spot[data-device="${id}"]`);
  const r = spot.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  const under = hit && hit.closest('.spot');
  return {device: under ? under.dataset.device : null, title: under ? under.querySelector(':scope > title').textContent : null};
}"""


async def test_a_group_is_one_dot_and_the_pointer_names_it(extended: str) -> None:
    """The living room shows its Hue room as one dot and not its two lamps. The pointer on the
    middle of the dot – on its icon – finds that dot, and it says it is a group of two lamps."""
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _network(browser, extended)
        shown = await page.eval_on_selector_all(".net .spot", "s => s.map(e => e.dataset.device)")
        assert GROUP in shown
        assert not set(MEMBERS) & set(shown)
        found = await page.evaluate(POINTER, GROUP)
        assert found["device"] == GROUP
        assert re.search(r"groep van 2 apparaten: ", found["title"])
        await browser.close()


async def test_every_dot_names_its_device_for_the_pointer(extended: str) -> None:
    """A device on its own says its name and how it is, also under the pointer on its icon."""
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _network(browser, extended)
        curtain = await page.evaluate(POINTER, "cover.living_curtain")
        assert curtain["device"] == "cover.living_curtain"
        name, _, state = curtain["title"].partition(": ")
        assert name and state and "groep" not in name and not name.startswith("cover.")
        await browser.close()
