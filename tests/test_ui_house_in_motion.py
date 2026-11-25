# ruff: noqa: E501 – the checks in the page are small JavaScript functions
"""Het huis in beweging (E-439, design 52.6), in a real browser, from the user's side: the
network on Huis › Ruimtes as in the design – Vesta large and breathing in the middle with her
word, the AI ring when AI is on, the rooms as named blocks with their devices as dots, what it
does flowing room → Vesta → device for twenty seconds, a question that waits as a dotted
line with its answers, and a legend. No line under it with what it did last: Wat ik bedacht,
just above, says that (QA 3, schermen 16).
Upright on a phone with 44 px rooms, and still with less motion.

Runs with the screens: ``pytest -m ui tests/test_ui_house_in_motion.py`` (a built frontend and
Playwright's Chromium). Each test fails without the change: the network had a 26 px circle,
rooms as small circles and no legend, no word, no ring and no 'Waarom?'; the line then flowed
for ten minutes, then for five seconds – too short to see – and a question could only be
answered elsewhere."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from housemind.world.house_mode import HouseMode
from tests.test_mind import EVENING
from tests.test_ui import AXE, DIST, SEEN, _axe

playwright = pytest.importorskip("playwright.async_api")

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not AXE.exists() or not DIST.exists(), reason="frontend not built"),
]
ROOT = Path(__file__).resolve().parents[1]
EXPERT = "user-4"  # Expert: Huis opens with the network
COMPUTER, PHONE = (1440, 900), (390, 844)


@pytest.fixture
async def moving(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind]]:
    """An evening at home: someone comes into the living room in the dark and HouseMind turns
    the light on by itself – the newest thing it did."""
    clock = SimulatedClock(EVENING.astimezone(UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (EXPERT, "Bewoner 4", 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    assert hm.learning is not None and hm.world is not None
    hm.learning.steps[("light_on_entry", "living")] = Step.AUTONOMOUS
    hm.world.modes.mode = HouseMode.HOME
    await demo.ha.simulate("binary_sensor.living_presence", "on")
    for _ in range(3):  # until it does it: what it just did flows, for twenty seconds
        if any(d.outcome.value == "executed" for d in hm.status.thoughts):
            break
        await clock.advance(20)
        await hm.tick()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm
    await runner.cleanup()
    await hm.stop()


async def _network(browser: Any, url: str, size: tuple[int, int], **context: Any) -> Any:
    ctx = await browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        color_scheme="dark",
        locale="nl-NL",
        bypass_csp=True,  # only to add axe; the page itself is unchanged
        extra_http_headers={"X-Remote-User-Id": EXPERT},
        **context,
    )
    page = await ctx.new_page()
    await page.goto(url)
    await page.wait_for_selector(".net .room", timeout=10000)
    # Vesta may open her window to tell something; it lies over the screen.
    close = page.locator(".window[role=dialog] button[aria-label='Sluiten']")
    if await close.count():
        await close.first.click()
    return page


# Every room's place, the panel and Vesta's ring, as the screen shows them.
PLACES = """() => {
  const box = (e) => { const r = e.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height}; };
  const stage = box(document.querySelector('.net .stage'));
  const brain = box(document.querySelector('.net .brain .orb .core'));
  const rooms = [...document.querySelectorAll('.net .room')].map(b => {
    const n = b.querySelector('.name');
    return {...box(b), name: n.textContent, clipped: n.scrollWidth > n.clientWidth + 1 || n.scrollHeight > n.clientHeight + 1};
  });
  return {stage, panel: box(document.querySelector('.net')), brain, rooms};
}"""


def _apart(a: dict[str, float], b: dict[str, float]) -> bool:
    return a["r"] <= b["l"] or b["r"] <= a["l"] or a["b"] <= b["t"] or b["b"] <= a["t"]


async def test_vesta_breathes_in_the_middle_of_the_network(moving: tuple[str, HouseMind]) -> None:
    """On a computer: Vesta large in the middle, breathing in her colour, her word under her;
    the rooms as named blocks around her, none on another; the devices as dots; a legend with
    the two lines of the core. No AI ring when AI is off. No problems for axe."""
    url, hm = moving
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _network(browser, url, COMPUTER)
        places = await page.evaluate(PLACES)
        stage, brain = places["stage"], places["brain"]
        assert brain["w"] >= 80  # large: the old brain was 26 px
        assert abs((brain["l"] + brain["r"]) / 2 - (stage["l"] + stage["r"]) / 2) < 2
        breath = await page.evaluate(
            "() => getComputedStyle(document.querySelector('.net .brain .core')).animationName"
        )
        assert breath.endswith("breathe")  # Svelte puts its own mark before the name
        word = await page.locator(".net .word").inner_text()
        assert word.strip() == hm.languages.text("nl", f"orb.{hm.status.orb}")
        rooms = places["rooms"]
        assert {r["name"] for r in rooms} >= {"Woonkamer", "Keuken", "Gang"}
        for i, room in enumerate(rooms):
            assert room["h"] >= 44 and room["w"] >= 44, room  # a 44 px target
            assert not room["clipped"], room  # the whole name
            assert _apart(room, brain), room
            for other in rooms[i + 1 :]:
                assert _apart(room, other), (room, other)
        assert await page.locator(".net svg circle.device").count() > len(rooms)
        legend = await page.locator(".net .legend li").all_inner_texts()
        assert legend == ["stroomt nu", "wacht op jou"]
        assert await page.locator(".net circle.ring").count() == 0
        assert [v for v in await _axe(page) if v["impact"] in ("serious", "critical")] == []
        await browser.close()


async def test_the_newest_decision_flows(moving: tuple[str, HouseMind]) -> None:
    """What HouseMind just did flows: a line from the room into Vesta and out to the lamp, the
    room lit, the lamp with a glow and its name. No "Laatst om …" line under it any more: Wat
    ik bedacht, just above, says what it did and why."""
    url, _hm = moving
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _network(browser, url, COMPUTER)
        flows = page.locator(".net path.flow")
        assert await flows.count() == 2
        assert await page.locator(".net path.flow.wait").count() == 0  # done, not asked
        motion = await flows.first.evaluate(
            "e => [getComputedStyle(e).animationName, getComputedStyle(e).strokeDasharray]"
        )
        assert motion[0].endswith("flow") and motion[1] != "none"
        assert await page.locator(".net .room.path .name").inner_text() == "Woonkamer"
        assert await page.locator(".net circle.device.target").count() == 1
        assert await page.locator(".net text.label").text_content()  # the lamp's name
        assert await page.locator(".net .live").count() == 0
        assert "Laatst om" not in await page.locator(".net").inner_text()
        await browser.close()


def _ai_on(hm: HouseMind, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type(hm.ai), "status", lambda _self: "on")


async def test_less_motion_stands_still(
    moving: tuple[str, HouseMind], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With less motion on the device, the line, the ring and Vesta's breath stand still; the
    line is still there, only it does not move."""
    url, hm = moving
    _ai_on(hm, monkeypatch)  # the ring shows too
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _network(browser, url, COMPUTER, reduced_motion="reduce")
        names = await page.evaluate(
            """() => ['.net path.flow', '.net circle.ring', '.net .brain .core']
              .map(s => getComputedStyle(document.querySelector(s)).animationName)"""
        )
        assert names == ["none", "none", "none"]
        await browser.close()


async def test_the_ai_ring_only_when_ai_is_on(
    moving: tuple[str, HouseMind], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI on: a dotted ring round Vesta, with its name, and a third line in the legend – the AI
    lane, apart from the core."""
    url, hm = moving
    _ai_on(hm, monkeypatch)
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _network(browser, url, COMPUTER)
        assert await page.locator(".net circle.ring").count() == 1
        assert await page.locator(".net span.ai").inner_text() == "AI-rij"
        legend = await page.locator(".net .legend li").all_inner_texts()
        assert legend == ["stroomt nu", "wacht op jou", "AI-rij, apart van de kern"]
        await browser.close()


async def test_upright_on_a_phone(moving: tuple[str, HouseMind]) -> None:
    """On a phone the drawing stands upright; every room is a 44 px target with its whole name,
    none on another or outside the panel, and the line under it fits the screen."""
    url, _hm = moving
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _network(browser, url, PHONE, is_mobile=True, has_touch=True)
        places = await page.evaluate(PLACES)
        stage, panel, rooms = places["stage"], places["panel"], places["rooms"]
        assert stage["h"] > stage["w"]  # upright
        for i, room in enumerate(rooms):
            assert room["h"] >= 44 and room["w"] >= 44, room
            assert not room["clipped"], room
            assert room["l"] >= panel["l"] and room["r"] <= panel["r"], room
            for other in rooms[i + 1 :]:
                assert _apart(room, other), (room, other)
        wide = await page.evaluate("() => document.documentElement.scrollWidth")
        assert wide <= PHONE[0]
        await browser.close()


async def test_the_line_flows_twenty_seconds_and_goes_out(moving: tuple[str, HouseMind]) -> None:
    """The line says 'stroomt nu': it flows for twenty seconds after HouseMind did something, so
    you can see it when you look a moment later, and then goes out by itself – the house sends
    no new picture for that."""
    url, _hm = moving
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _network(browser, url, COMPUTER)
        assert await page.locator(".net path.flow").count() == 2
        start = time.monotonic()
        await page.wait_for_timeout(15000)
        assert await page.locator(".net path.flow").count() == 2  # five seconds was too short
        await page.wait_for_function(
            "() => !document.querySelector('.net path.flow')", timeout=9000
        )
        assert 15 < time.monotonic() - start < 23  # twenty seconds, not ten minutes
        assert await page.locator(".net .room.path").count() == 0
        assert await page.locator(".net circle.device.target").count() == 0
        await browser.close()


async def test_a_question_waits_with_its_answers(moving: tuple[str, HouseMind]) -> None:
    """A question stays a dotted line from its room through Vesta to the lamp for as long as
    it is open – also after the line that flows is gone – with the question and its two quick
    answers under the drawing. 'Doe het' answers it right there: the lamp goes on, the line
    goes out."""
    url, hm = moving
    assert hm.learning is not None
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the drawing
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE  # it asks
    await hm.ha.simulate("binary_sensor.kitchen_motion", "on")
    await hm.clock.advance(20)
    await hm.tick()
    asked = await hm.open_proposals()
    assert asked
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _network(browser, url, COMPUTER)
        dotted = page.locator(".net path.flow.wait")
        assert await dotted.count() == 2
        assert await page.locator(".net .room.path.wait .name").inner_text() == "Keuken"
        assert await page.locator(".net circle.device.target.wait").count() == 1
        ask = page.locator(".net .live.asking")
        assert await ask.get_attribute("role") == "group"
        text = await ask.inner_text()
        assert text.startswith("Wacht op jou") and "keuken" in text.lower() and "?" in text
        await page.wait_for_timeout(6000)
        assert await dotted.count() == 2  # it still waits
        answers = ask.get_by_role("button")
        assert await answers.all_inner_texts() == ["Doe het", "Nu niet"]
        for box in [await b.bounding_box() for b in await answers.all()]:
            assert box is not None and box["height"] >= 44  # a 44 px target
        await ask.get_by_role("button", name="Doe het").click()
        await page.wait_for_function(
            "() => !document.querySelector('.net .live.asking')", timeout=5000
        )
        assert await dotted.count() == 0
        assert not await hm.open_proposals()
        assert (await hm.ha.get_states())[asked[-1].entity_id].state == "on"
        await browser.close()


async def test_quiet_before_it_did_anything_today(moving: tuple[str, HouseMind]) -> None:
    """A new day, nothing done or asked yet: no line, and no sentence under the drawing – the
    legend says what flows and what waits."""
    url, hm = moving
    await hm.clock.advance(24 * 3600)
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _network(browser, url, COMPUTER)
        assert await page.locator(".net path.flow").count() == 0
        assert await page.locator(".net .live").count() == 0
        assert await page.locator(".net .legend li").all_inner_texts() == [
            "stroomt nu",
            "wacht op jou",
        ]
        await browser.close()


async def test_the_screens_stay_apart(moving: tuple[str, HouseMind]) -> None:
    """The network's own Vesta is only to see: the header keeps the one Vesta button, so a
    screen reader hears her once and a tap in the middle of the network does nothing odd."""
    url, _hm = moving
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        page = await _network(browser, url, COMPUTER)
        assert await page.locator(".net .orb").evaluate("e => e.tagName") == "SPAN"
        assert await page.locator(".net .orb").get_attribute("aria-hidden") == "true"
        assert await page.locator("button.orb").count() == 1
        await browser.close()
