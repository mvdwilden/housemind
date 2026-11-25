"""An icon per device and per room on Huis and in Apparaten, in a real browser (E-508 – E-510,
design 79, part A). The administrator (12-11-2026): "Overal een icoontje per soort apparaat",
with a lamp that shows its percentage. With the ceiling lamp of the living room at 35%: every
room card has the icon of its room and, small, what is on (the lamp with 35%); the floor plan
has the room's icon in its corner and the lamp; in the network every device shows what it is,
and every room its icon; a room opened shows each device with its icon; Brein › Apparaten shows
each device with its icon and, for an administrator, Openen in Home Assistant – where you
choose another icon – without a line of codes (QA 3, schermen 18). On a phone the network
keeps its plain dots and nothing scrolls sideways.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_icons.py``
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
ADMIN = "demo-user-1"
SHOTS = os.environ.get("HOUSEMIND_SHOTS")  # a folder for screenshots, to look at by hand

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

# Click a button by its text, anywhere on the page or in a part of it.
CLICK = """async ([selector, text]) => {
  [...document.querySelectorAll(selector)]
    .find((b) => b.offsetParent && b.textContent.trim().startsWith(text)).click();
  await new Promise((r) => setTimeout(r, 1200));
}"""

LOOK = """(what) => {
  const count = (s) => document.querySelectorAll(s).length;
  const text = (s) => [...document.querySelectorAll(s)].map((e) => e.textContent.trim());
  if (what === "cards") return {
    rooms: count(".cards li"), icons: count(".cards .tile svg"),
    lamps: text(".cards .active .item.lamp"),
  };
  if (what === "plan") return {
    tiles: count(".plan .tile"), corners: count(".plan .tile .corner svg"),
    lamps: text(".plan .tile .lamp"),
  };
  if (what === "net") return {
    rooms: count(".net button.room"), roomIcons: count(".net .room-icon svg"),
    devices: count(".net .dev"), lamps: count(".net .dev.lamp"), dots: count(".net circle.device"),
  };
  if (what === "sheet") return {
    rows: count("button.row"), icons: count("button.row .icon svg"),
    lamps: [...document.querySelectorAll("button.row")].filter((r) => r.querySelector(".icon.lamp"))
      .map((r) => r.querySelector(".name").firstChild.textContent.trim()),
  };
  if (what === "devices") return {
    rows: count("main li:has(> .main)"), icons: count("main li > .icon svg"),
    other: count("main .other-icon"), codes: count("main li .mono"),
    open: text("main .other-icon").filter((m) => m === "Openen in Home Assistant").length,
  };
  return { page: [document.documentElement.scrollWidth, window.innerWidth] };
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind, Any]]:
    clock = SimulatedClock(datetime(2026, 11, 3, 19, 0, tzinfo=UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    await demo.ha.simulate("light.living_ceiling", "on", {"brightness": 89})  # 35%
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm, demo
    await runner.cleanup()
    await hm.stop()


async def test_every_device_and_room_shows_its_icon(house: tuple[str, HouseMind, Any]) -> None:
    url, hm, _ = house

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

            # Huis › Kaarten: the room's icon, and the lamp that is on with its percentage.
            await page.evaluate(CLICK, ["main button", t("ui.view.cards")])
            cards = await page.evaluate(LOOK, "cards")
            assert cards["rooms"] > 10 and cards["icons"] == cards["rooms"], cards
            assert "35%" in cards["lamps"], cards
            if SHOTS:
                await page.locator(".cards").screenshot(path=f"{SHOTS}/cards-{width}.png")

            # Plattegrond: the room's icon in the corner, the lamp with 35%.
            await page.evaluate(CLICK, ["main button", t("ui.view.plan")])
            plan = await page.evaluate(LOOK, "plan")
            assert plan["tiles"] > 10 and plan["corners"] == plan["tiles"], plan
            assert "35%" in plan["lamps"], plan

            # Netwerk: on a computer every device shows what it is, every room its icon; on a
            # phone the rows are too close – plain dots.
            await page.evaluate(CLICK, ["main button", t("ui.view.network")])
            await page.wait_for_selector(".net button.room")
            net = await page.evaluate(LOOK, "net")
            if width > 800:
                assert net["roomIcons"] == net["rooms"] and net["devices"] > 20, net
                assert net["lamps"] >= 1, net
                if SHOTS:
                    await page.locator(".net").screenshot(path=f"{SHOTS}/network-{width}.png")
            else:
                assert net["roomIcons"] == 0 and net["devices"] == 0 and net["dots"] > 20, net

            # A room opened: each device with its icon, the ceiling lamp warm.
            await page.evaluate(CLICK, [".net button.room", "Woonkamer"])
            await page.wait_for_selector("button.row")
            sheet = await page.evaluate(LOOK, "sheet")
            assert sheet["rows"] > 3 and sheet["icons"] == sheet["rows"], sheet
            assert any("plafondlamp" in name.lower() for name in sheet["lamps"]), sheet
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
            assert (await page.evaluate(LOOK, "page"))["page"][0] <= width

            # Brein › Apparaten (Expert): each device with its icon, no codes, and Openen in
            # Home Assistant, where you choose another icon.
            if width > 800:
                await page.evaluate(CLICK, ["nav button", t("tabs.brain")])
                await page.evaluate(CLICK, ["main button", t("ui.brain.tab.devices")])
                await page.wait_for_selector("main li > .icon svg", state="attached")
                devices = await page.evaluate(LOOK, "devices")
                assert devices["rows"] > 10 and devices["icons"] == devices["rows"], devices
                assert devices["other"] == devices["rows"] == devices["open"], devices
                assert devices["codes"] == 0, devices
                if SHOTS:
                    await page.screenshot(path=f"{SHOTS}/devices-{width}.png")
            await context.close()
        await browser.close()
