"""People go first while they are there, in a real browser (E-462, B-377). The administrator's
yes (06-11-2026) to: "Zal ik bouwen dat ‘mensen gaan voor’ stopt zodra iemand de ruimte heeft
verlaten?" So on screen: put the lamp out by hand, leave, come back – the Logbook says HouseMind
put it on. And a room without a sensor says why HouseMind does not put its light out.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_new_moment.py``
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
from housemind.core.model import Source
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_ui_proof import ADMIN, DIST, ROWS, SEEN, SUBTAB, TAB, WAITING, _prove

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
SHOTS = os.environ.get("HOUSEMIND_SHOTS")
LIVING = "binary_sensor.living_presence"
LAMP = "light.living_ceiling"

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind, FakeHomeAssistant]]:
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
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "living")] = Step.AUTONOMOUS
    hm.learning.steps[("light_off_leave", "living")] = Step.AUTONOMOUS
    _prove(hm, ("light_off_leave", "toilet"))
    # Someone puts the lamp out by hand, leaves, and comes back a while later.
    await demo.ha.simulate(LIVING, "on")
    await hm.tick()
    await demo.ha.simulate(LAMP, "on", {"brightness": 200}, by=Source.PHYSICAL)
    await demo.ha.simulate(LAMP, "off", by=Source.PHYSICAL)
    await demo.ha.simulate(LIVING, "off")
    for _ in range(70):  # long enough for the room's other lamp to have gone out
        await clock.advance(30)
        await hm.tick()
    await demo.ha.simulate(LIVING, "on")
    await clock.advance(30)
    await hm.tick()
    assert demo.ha.states[LAMP].state == "on"
    await clock.advance(16 * 60)
    await hm.tick()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm, demo.ha
    await runner.cleanup()
    await hm.stop()


async def test_coming_back_gives_light_and_no_sensor_is_explained(
    house: tuple[str, HouseMind, FakeHomeAssistant],
) -> None:
    url, hm, ha = house

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    assert ha.states[LAMP].state == "on"
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

        await page.evaluate(TAB, t("tabs.logbook"))
        body = await page.evaluate("() => document.querySelector('main').innerText")
        assert "aangedaan" in body and "woonkamer" in body.lower(), body[:1500]
        waiting = await page.evaluate(WAITING)
        assert not any("Toilet" in w["text"] and "uit" in w["text"] for w in waiting), waiting
        if SHOTS:
            await page.screenshot(path=f"{SHOTS}/moment-logbook.png", full_page=False)

        await page.evaluate(TAB, t("tabs.brain"))
        await page.evaluate(SUBTAB, t("ui.ready.title"))
        rows = await page.evaluate(ROWS)
        blind = [r for r in rows if t("ui.ready.proof.no_sensor") in r["text"]]
        assert blind, rows
        # Each says it only watches: still on Observeren, or Alleen meekijken where it would
        # otherwise be further – the toilet, proven, would be on Voorstellen.
        assert all(r["pill"] in (t("ui.ready.watching"), t("steps.observe")) for r in blind), blind
        toilet = next(r for r in blind if "Toilet" in r["text"])
        assert toilet["pill"] == t("ui.ready.watching"), toilet
        if SHOTS:
            await page.locator('[data-section="readiness"] section.list').screenshot(
                path=f"{SHOTS}/moment-card.png"
            )
        await context.close()
        await browser.close()
