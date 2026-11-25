"""The lamps in a group stand indented under the group, in a real browser. The administrator's
wish (01-11-2026): "ik wil dat de apparaten dat een 'child' is van een parent, een beetje
inspringen naar binnen, zodat je ziet dat het om children gaat van de parent". The group's own
row stays at the top; its lamps sit further in, along a line, on a computer and on a phone.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_group_children.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from aiohttp import web

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
SEEN = '{"onboarded": true, "tour": [1, 2, 3, 4]}'

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

# Open Brein › Apparaten, the dining room and its Zigbee group; where the rows start.
MEASURE = """async ([brain, devices, room, group]) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const button = (text) => [...document.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === text);
  button(brain).click(); await wait(300);
  button(devices).click(); await wait(500);
  const rooms = [...document.querySelectorAll(".devices details.group")];
  const place = rooms.find((d) => d.querySelector("summary").textContent.includes(room));
  if (!place.open) place.querySelector("summary").click();
  await wait(200);
  const grp = [...place.querySelectorAll("details.grp")]
    .find((d) => d.querySelector(".gname").textContent === group);
  if (!grp.open) grp.querySelector("summary").click();
  await wait(200);
  const rows = [...grp.querySelectorAll("li")].map((li) => [
    li.querySelector("strong").textContent, li.getBoundingClientRect().left]);
  return {rows, page: document.documentElement.scrollWidth, width: innerWidth};
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind]]:
    when = datetime(2026, 10, 19, 19, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))
    clock = SimulatedClock(when.astimezone(UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        ("user-4", "Bewoner 4", 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm
    await runner.cleanup()
    await hm.stop()


async def test_the_lamps_of_a_group_stand_indented_under_it(house: tuple[str, HouseMind]) -> None:
    url, hm = house
    t = hm.languages.text
    words = [
        t("nl", "tabs.brain"),
        t("nl", "ui.brain.tab.devices"),
        hm.model.areas["dining"].name,
        hm.model.entities["light.dining_table"].name,
    ]
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        for width, height in ((1280, 900), (390, 844)):  # a computer and a phone
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                locale="nl-NL",
                extra_http_headers={"X-Remote-User-Id": "user-4"},
            )
            page = await context.new_page()
            await page.goto(url)
            await page.wait_for_selector(".orb", timeout=10000)
            seen: dict[str, Any] = await page.evaluate(MEASURE, words)
            group, *lamps = seen["rows"]
            assert group[0] == words[3] and len(lamps) == 2, seen
            assert all(left >= group[1] + 16 for _, left in lamps), (width, seen)
            assert seen["page"] <= seen["width"], (width, seen)  # no sideways scrolling
            await context.close()
        await browser.close()
