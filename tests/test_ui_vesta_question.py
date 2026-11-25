"""One question at a time, with her buttons, and she listens after a tap (E-464, B-379), in a real
browser with a pretend microphone. The administrator's wish (07-11-2026): "ik wil gewoon dat de
gebruiker weer op Vesta klikt om een nieuwe vraag te stellen".

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_vesta_question.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from housemind import __version__
from housemind.brain.notify import Kind
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_vesta import _raise

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
SEEN = f'{{"onboarded": true, "tour": [1, 2, 3, 4], "seen_version": "{__version__}"}}'
ADMIN = "demo-user-1"  # the demo house's administrator

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind, Any]]:
    clock = SimulatedClock(datetime(2026, 10, 27, 11, 0, tzinfo=UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    hm.activated = True
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 3, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm, demo.ha
    await runner.cleanup()
    await hm.stop()


async def _page(p: Any, url: str) -> tuple[Any, Any]:
    browser = await p.chromium.launch(
        args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="nl-NL",
        extra_http_headers={"X-Remote-User-Id": ADMIN},
    )
    page = await context.new_page()
    await page.goto(url)
    return browser, page


async def test_a_question_stays_with_its_buttons_until_you_answer(
    house: tuple[str, HouseMind, Any],
) -> None:
    """Two questions wait. The first stays with its buttons while the house changes and the
    microphone stays shut; a tap on her opens it and the buttons stay; only after your answer
    and her words comes the next."""
    url, hm, _ha = house
    t = hm.languages.text
    for code, device in (("x", "Gang rookmelder"), ("y", "Keuken rookmelder")):
        await _raise(
            hm, Kind.IMPORTANT, f"battery:{code}", "notify.maintenance.battery", {"device": device}
        )
    answer = t("nl", "ui.notifications.seen")
    async with playwright.async_playwright() as p:
        browser, page = await _page(p, url)
        await page.get_by_role("dialog").get_by_role("button", name=answer).wait_for(timeout=15000)
        first = await page.locator(".window .subtitle").inner_text()
        for _ in range(4):
            await hm._notify()  # the house changes while the question is open
            await asyncio.sleep(1.2)
        assert await page.locator(".window .subtitle").inner_text() == first
        assert await page.get_by_role("dialog").get_by_role("button", name=answer).is_visible()
        status = page.locator(".window .status")
        assert await status.count() == 0  # she does not listen by herself

        await page.locator(".window div.orb button").click()  # a tap on her
        await page.get_by_text(t("nl", "ui.vesta.listening")).wait_for(timeout=5000)
        assert (
            await page.get_by_role("dialog").get_by_role("button", name=answer).is_visible()
        )  # the buttons stay

        await page.get_by_role("dialog").get_by_role("button", name=answer).click()
        await page.locator(".window .status").wait_for(state="detached", timeout=5000)
        await page.wait_for_function(
            "(first) => document.querySelector('.window .subtitle').textContent !== first",
            arg=first,
            timeout=5000,
        )
        reply = await page.locator(".window .subtitle").inner_text()
        assert "?" not in reply  # her words first, not the next question over them
        await page.wait_for_function(
            "(reply) => document.querySelector('.window .subtitle').textContent !== reply",
            arg=reply,
            timeout=15000,
        )
        second = await page.locator(".window .subtitle").inner_text()
        assert second != first
        assert await page.get_by_role("dialog").get_by_role("button", name=answer).is_visible()
        await browser.close()


async def test_after_her_answer_you_tap_on_her_for_the_next_thing(
    house: tuple[str, HouseMind, Any],
) -> None:
    """A tap, your question, her answer: then she stops listening, and says how to go on. A tap
    on her in the window opens the microphone again, in the same conversation."""
    url, hm, ha = house
    t = hm.languages.text
    async with playwright.async_playwright() as p:
        browser, page = await _page(p, url)
        await page.wait_for_selector(".orb", timeout=10000)
        await page.locator("header .orb").first.click()
        listening = page.get_by_text(t("nl", "ui.vesta.listening"))
        await listening.wait_for(timeout=10000)
        ha.heard = ["Hoe heet je?"]  # her time runs out: what was heard goes on
        said = page.get_by_role("dialog").get_by_text("Hoe heet je?")
        await said.wait_for(timeout=15000)
        await asyncio.sleep(5)  # she used to start listening again in this time
        assert await listening.count() == 0
        hint = page.locator(".window .again")
        assert await hint.inner_text() == t("nl", "orb.tap_hint")
        await page.locator(".window div.orb button").click()
        await listening.wait_for(timeout=5000)
        assert await hint.count() == 0
        await page.get_by_role("button", name=t("nl", "ui.vesta.stop")).click()
        await listening.wait_for(state="detached", timeout=5000)
        assert await said.count() == 1  # the same conversation
        await browser.close()


async def test_close_and_her_voice_are_big_enough_to_see_and_hit(
    house: tuple[str, HouseMind, Any],
) -> None:
    """The administrator (11-11-2026): "kan je deze knoppen iets groter maken?" – the cross and
    the loudspeaker in the corners of her window were small, pale signs. Now each is a round
    button of 52 pixels with a sign of 26, on a phone too."""
    url, hm, _ha = house
    t = hm.languages.text
    async with playwright.async_playwright() as p:
        browser, page = await _page(p, url)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.wait_for_selector(".orb", timeout=10000)
        await page.locator("header .orb").first.click()
        dialog = page.get_by_role("dialog")
        for name in (t("nl", "ui.close"), t("nl", "ui.vesta.mute")):
            button = dialog.get_by_role("button", name=name, exact=True)
            box = await button.bounding_box()
            sign = await button.locator("svg").bounding_box()
            assert box and box["width"] >= 52 and box["height"] >= 52, (name, box)
            assert sign and sign["width"] >= 26, (name, sign)
        await browser.close()
