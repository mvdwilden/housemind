"""What holds a room, in a real browser (E-461, B-376). The administrator's yes (06-11-2026) to:
"Zal ik ook bouwen dat die automatiseringen ruimtes niet meer tegenhouden?" So on screen: a room
with only the alarm's colour on it asks its own question, an automation with only lamps is named
in the question of its rules, and a lamp that stays with an automation says so in the row.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_signals.py``
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
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_automations import HALL, HALL_CONFIG
from tests.test_signals_and_lamps import (
    ALARM,
    ALARM_CONFIG,
    AMPLIFIER,
    AMPLIFIER_CONFIG,
    TV_OFF,
    TV_OFF_CONFIG,
)
from tests.test_ui_proof import ADMIN, DIST, ROWS, SEEN, SUBTAB, TAB, WAITING, _prove

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
SHOTS = os.environ.get("HOUSEMIND_SHOTS")

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]


def _on(config: dict[str, Any], lamp: str, alias: str | None = None) -> dict[str, Any]:
    """The same automation on another lamp."""

    def moved(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: moved(v) for k, v in value.items()}
        if isinstance(value, list):
            return [moved(v) for v in value]
        return lamp if value == "light.hall" else value

    return {**moved(config), **({"alias": alias} if alias else {})}


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind, FakeHomeAssistant]]:
    clock = SimulatedClock(datetime(2026, 11, 2, 15, 0, tzinfo=UTC))
    demo = load_demo_house("family_home", clock, "nl")
    for entity_id, config in (
        (HALL, HALL_CONFIG),
        (TV_OFF, TV_OFF_CONFIG),
        (ALARM, _on(ALARM_CONFIG, "light.kitchen")),
        (AMPLIFIER, _on(AMPLIFIER_CONFIG, "light.living_reading")),
    ):
        await demo.ha.add_automation(entity_id, str(config["alias"]), config)
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    for pair in (
        ("light_on_entry", "hall"),
        ("light_off_leave", "hall"),
        ("light_on_entry", "kitchen"),
        ("light_on_entry", "living"),
    ):
        _prove(hm, pair)
    await hm._sync_automations()
    await clock.advance(16 * 60)
    await hm.tick()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm, demo.ha
    await runner.cleanup()
    await hm.stop()


async def test_a_signal_holds_nothing_and_a_lamp_stays_with_what_does_more(
    house: tuple[str, HouseMind, FakeHomeAssistant],
) -> None:
    url, hm, _ha = house

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

        await page.evaluate(TAB, t("tabs.brain"))
        await page.evaluate(SUBTAB, t("ui.ready.title"))
        rows = await page.evaluate(ROWS)
        kitchen = next(r for r in rows if "Keuken" in r["text"] and "binnenkomen" in r["text"])
        assert t("steps.propose") in kitchen["text"], kitchen
        assert t("ui.ready.watching") not in kitchen["text"], kitchen
        assert "Alarm disarmed green" not in kitchen["text"], kitchen
        living = next(r for r in rows if "Woonkamer" in r["text"] and "binnenkomen" in r["text"])
        assert "Amplifier follows" in living["text"], living
        assert "de rest doe ik zelf" in living["text"], living
        assert t("ui.ready.watching") not in living["text"], living
        if SHOTS:
            await page.locator('[data-section="readiness"] section.list').screenshot(
                path=f"{SHOTS}/signals-card.png"
            )

        await page.evaluate(TAB, t("tabs.logbook"))
        waiting = await page.evaluate(WAITING)
        automation = next(w for w in waiting if "Hall light at motion" in w["text"])
        assert "Lights out with the TV" in automation["text"], automation
        assert any("in de Keuken" in w["text"] for w in waiting), waiting
        assert any("in de Woonkamer" in w["text"] for w in waiting), waiting
        assert not any("Alarm disarmed green" in w["text"] for w in waiting), waiting
        if SHOTS:
            await page.screenshot(path=f"{SHOTS}/signals-waiting.png", full_page=False)
        await context.close()
        await browser.close()
