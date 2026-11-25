"""A boiler that does not heat (E-526, B-450), in a real browser: after the weekend as it went
in the house – the heat pump stopped heating on Sunday after 04:46, and on Monday the shower was
cold – the message is on top of Logboek, in "Wacht op jou", with the boiler and both
temperatures; on a phone and on a computer. Once the boiler heats again, it is gone.

Runs only on request, like tests/test_ui.py:
``uv run pytest -m ui tests/test_ui_boiler_cold.py`` (needs a built frontend and
``uv run playwright install chromium`` once)."""

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
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
SEEN = f'{{"onboarded": true, "tour": [1, 2, 3, 4], "seen_version": "{__version__}"}}'
ADMIN = "demo-user-1"
BOILER = "water_heater.boiler"

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

# Tap Logboek; the texts of the messages in "Wacht op jou".
WAITING = """async (name) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  [...document.querySelectorAll("nav button")]
    .find((b) => b.offsetParent && b.textContent.includes(name)).click();
  await wait(1200);
  const block = document.querySelector("main section.waiting");
  return [...(block?.querySelectorAll("li[data-notice]") ?? [])].map((li) =>
    li.textContent.replace(/\\s+/g, " ").trim());
}"""


async def quarter(hm: HouseMind, clock: SimulatedClock, ha: FakeHomeAssistant, temp: float) -> None:
    await clock.advance(15 * 60)
    await ha.simulate(BOILER, "heat_pump", {"current_temperature": temp})
    hm._last_review = None
    await hm.tick()


@pytest.fixture
async def house(
    tmp_path: Path,
) -> AsyncIterator[tuple[str, HouseMind, SimulatedClock, FakeHomeAssistant]]:
    # Sunday 15 November, 04:46 in the house: the boiler heated to 52 °C, its setting.
    clock = SimulatedClock(datetime(2026, 11, 15, 3, 46, tzinfo=UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    await demo.ha.simulate(BOILER, "heat_pump", {"temperature": 52, "current_temperature": 52})
    steps = 26 * 4 + 1  # until Monday a little after 06:45
    for i in range(1, steps + 1):
        await quarter(hm, clock, demo.ha, round(52 - 16 * i / steps))
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm, clock, demo.ha
    await runner.cleanup()
    await hm.stop()


async def test_the_cold_boiler_waits_on_top_of_logboek(
    house: tuple[str, HouseMind, SimulatedClock, FakeHomeAssistant],
) -> None:
    url, hm, clock, ha = house

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        for width, height in ((390, 844), (1280, 900)):  # a phone, then a computer
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                locale="nl-NL",
                extra_http_headers={"X-Remote-User-Id": ADMIN},
            )
            page: Any = await context.new_page()
            await page.goto(url)
            await page.wait_for_selector(".orb", timeout=10000)
            name = t("tabs.logbook") if width > 800 else t("tabs_short.logbook")
            waiting = await page.evaluate(WAITING, name)
            boiler = [w for w in waiting if "warmt het water al meer dan een dag niet op" in w]
            assert len(boiler) == 1, waiting
            assert "nog 36°" in boiler[0] and "op 52°" in boiler[0], boiler
            assert "storing" in boiler[0], boiler
            await context.close()

        # Someone switches on the quick heating: 23 → 29 °C, and the message goes by itself.
        await quarter(hm, clock, ha, 23)
        await quarter(hm, clock, ha, 29)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="nl-NL",
            extra_http_headers={"X-Remote-User-Id": ADMIN},
        )
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector(".orb", timeout=10000)
        waiting = await page.evaluate(WAITING, t("tabs.logbook"))
        assert not [w for w in waiting if "warmt het water" in w], waiting
        await context.close()
        await browser.close()
