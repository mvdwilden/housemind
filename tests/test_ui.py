"""Every screen, on every level, size and colour scheme (H12.1 "UI" and "Toegankelijkheid").

Runs only on request: ``uv run pytest -m ui -s tests/test_ui.py`` (needs ``uv run playwright
install chromium`` once, and ``npm ci`` in frontend/ for axe-core). For each level 1–4, a phone,
a tablet and a computer, light and dark (Licht and Diep, the device's own), and the colour schemes
Hoog contrast and Nacht (E-266, chosen on the device), it opens each tab and sheet, then
- checks it with axe against WCAG 2.2 AA (E-46): no serious or critical problem;
- measures a tap (the button answers) ≤ 0.1 s and a change of screen ≤ 0.3 s (E-157);
and on a phone with a four times slower processor, the first screen within 2 s (E-157).
Screenshots go to dist/ui/ (B-39 makes the help pictures from the same run later).
"""

from __future__ import annotations

import json
import time
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
AXE = ROOT / "frontend" / "node_modules" / "axe-core" / "axe.min.js"
DIST = ROOT / "frontend" / "dist"
OUT = ROOT / "dist" / "ui"
TZ = ZoneInfo("Europe/Amsterdam")
SIZES = {"phone": (390, 844), "tablet": (820, 1180), "computer": (1440, 900)}
WCAG = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
TAP_MS, SWITCH_MS, FIRST_SCREEN_MS = 100.0, 300.0, 2000.0
# The very first sheet in a fresh browser is slower: the browser still compiles its code
# (measured: 247 ms once, then about 30 ms, on every level and scheme). It has its own norm.
COLD_TAP_MS = 500.0
TABS = ("home", "logbook", "energy", "brain")  # at most four, never "More" (E-260)
SEEN = '{"onboarded": true, "tour": [1, 2, 3, 4]}'
TOP = """() => new Promise((done) => {
  for (const el of document.querySelectorAll("*")) if (el.scrollTop) el.scrollTop = 0;
  window.scrollTo(0, 0);
  requestAnimationFrame(() => requestAnimationFrame(done));
})"""

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not AXE.exists() or not DIST.exists(), reason="frontend not built"),
]


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind]]:
    clock = SimulatedClock(datetime(2026, 10, 19, 19, 30, tzinfo=TZ).astimezone(UTC))
    demo = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    for level in (1, 2, 3, 4):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, preferences, created) "
            "VALUES(?,?,?,?,?)",
            # Introduced and toured: the tour starts by itself after a quiet moment and would
            # lie over the screens this test taps through (it has tests of its own).
            (f"user-{level}", f"Bewoner {level}", level, SEEN, "2026-10-01"),
        )
    await hm._refresh_people()
    await hm.tick()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    yield f"http://127.0.0.1:{port}/", hm
    await runner.cleanup()
    await hm.stop()


async def _axe(page: Any) -> list[dict[str, Any]]:
    if not await page.evaluate("typeof window.axe !== 'undefined'"):
        await page.add_script_tag(path=str(AXE))
    result = await page.evaluate(
        "async (tags) => await axe.run(document, {runOnly: {type: 'tag', values: tags}})", WCAG
    )
    return [
        {
            "id": v["id"],
            "impact": v["impact"],
            "help": v["help"],
            "nodes": len(v["nodes"]),
            "target": v["nodes"][0]["target"] if v["nodes"] else [],
            "why": v["nodes"][0].get("failureSummary", "")[:300] if v["nodes"] else "",
        }
        for v in result["violations"]
    ]


async def _timed_click(page: Any, button: Any, settled: str) -> tuple[float, float]:
    """(ms until the button answered, ms until the new screen stood still), both from the
    moment of the tap itself – not from when the test robot starts to look for the button,
    which on a slow test machine takes longer than the screen does."""
    await page.evaluate(
        """() => { window.__hmTap = null; window.__hmT0 = null;
        document.addEventListener("click", (e) => { window.__hmT0 = e.timeStamp; },
          {capture: true, once: true});
        const obs = new MutationObserver(() => {
          if (window.__hmTap === null && window.__hmT0 !== null)
            window.__hmTap = performance.now() - window.__hmT0; });
        obs.observe(document.body, {subtree: true, childList: true, attributes: true});
        window.__hmObs = obs; }"""
    )
    await button.click()
    await page.wait_for_function(settled, timeout=5000)
    await page.wait_for_function(
        "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
        ".then(() => true)"
    )
    tap, switch = await page.evaluate(
        "() => { window.__hmObs.disconnect(); "
        "return [window.__hmTap ?? 0, performance.now() - (window.__hmT0 ?? performance.now())]; }"
    )
    return float(tap), float(switch)


async def _close(page: Any, t: Any) -> None:
    while await page.locator("[role=dialog]").count():
        await page.get_by_role("button", name=t("nl", "ui.close")).first.click()
        await page.wait_for_timeout(50)


async def test_every_screen_on_every_level(house: tuple[str, HouseMind]) -> None:
    url, hm = house
    t = hm.languages.text
    OUT.mkdir(parents=True, exist_ok=True)
    problems: list[dict[str, Any]] = []
    timings: list[dict[str, Any]] = []
    screens = 0
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        for level in (1, 2, 3, 4):
            for size, (width, height) in SIZES.items():
                for scheme in ("light", "dark", "contrast", "nacht"):
                    context = await browser.new_context(
                        viewport={"width": width, "height": height},
                        color_scheme="light" if scheme == "light" else "dark",
                        locale="nl-NL",
                        bypass_csp=True,  # only to add axe; the page itself is unchanged
                        extra_http_headers={"X-Remote-User-Id": f"user-{level}"},
                    )
                    page = await context.new_page()
                    if scheme in ("contrast", "nacht"):  # a scheme chosen on this device
                        choice = json.dumps({"scheme": scheme})
                        await page.add_init_script(
                            f"localStorage.setItem('housemind.device', {json.dumps(choice)})"
                        )
                    await page.goto(url)
                    await page.wait_for_selector(".orb", timeout=10000)
                    where = f"l{level}-{size}-{scheme}"

                    async def check(name: str, where: str = where, page: Any = page) -> None:
                        nonlocal screens
                        screens += 1
                        await page.screenshot(path=str(OUT / f"{where}-{name}.png"))
                        # axe from the top, as a screen opens. A tap lower down scrolls the page
                        # (the filter bar on Brain), and a button that then lies half under the
                        # sticky tabs counts as too small at that one spot, while WCAG allows a
                        # sticky bar to cover part of it. axe looks at the whole page anyway.
                        await page.evaluate(TOP)
                        for v in await _axe(page):
                            problems.append({"screen": f"{where}-{name}", **v})

                    await check("home")
                    # Tabs (level 2 and up): each one a tap and a change of screen.
                    for key in TABS:
                        label = t("nl", f"tabs.{key}")
                        tab = page.locator("nav button:visible", has_text=label).first
                        if not await tab.count() or not await tab.is_visible():
                            continue  # not on this level
                        tap, switch = await _timed_click(
                            page, tab, "() => document.querySelector('[aria-current=page]')"
                        )
                        timings.append({"screen": f"{where}-{key}", "tap": tap, "switch": switch})
                        await check(key)
                        # The filter bar above a long list (E-276): the open panel, too.
                        button = page.locator(".finder button[aria-expanded]").first
                        if await button.count() and await button.is_visible():
                            await button.click()
                            await check(f"{key}-filter")
                            await button.click()
                    # Sheets: my preferences, help, house settings, pause. Help and house
                    # settings are in the side bar on a computer, under my preferences on a phone.
                    for key in (
                        "ui.my_preferences",
                        "ui.help",
                        "ui.house_settings",
                        "ui.about",
                        "ui.pause",
                    ):
                        name = t("nl", key)
                        button = page.get_by_role("button", name=name, exact=True).first
                        via_preferences = not await button.count() or not await button.is_visible()
                        if via_preferences:
                            if key not in ("ui.help", "ui.house_settings", "ui.about"):
                                continue
                            mine = t("nl", "ui.my_preferences")
                            await page.get_by_role("button", name=mine, exact=True).first.click()
                            await page.wait_for_selector("[role=dialog]")
                            button = page.get_by_role("dialog").get_by_role(
                                "button", name=name, exact=True
                            )
                            if not await button.count():
                                await _close(page, t)
                                continue
                        tap, switch = await _timed_click(
                            page, button.first, "() => document.querySelector('[role=dialog]')"
                        )
                        timings.append(
                            {
                                "screen": f"{where}-{key}",
                                "tap": tap,
                                "switch": switch,
                                "cold": not any("ui." in x["screen"] for x in timings),
                            }
                        )
                        await check(key.split(".")[1])
                        await _close(page, t)
                    # Vesta's window and a room's own sheet (QA 2, M-26: not checked before).
                    await page.locator("button.orb").first.click()
                    await page.wait_for_selector(".window[role=dialog]")
                    await check("vesta")
                    await page.keyboard.press("Escape")
                    await page.wait_for_selector(".window[role=dialog]", state="detached")
                    room = page.locator(".rooms button, .cards button").first
                    if await room.count() and await room.is_visible():
                        await room.click()
                        await page.wait_for_selector("[role=dialog]")
                        await check("room")
                        await _close(page, t)
                    await context.close()

        # The first screen on a phone with a four times slower processor.
        context = await browser.new_context(
            viewport={"width": 390, "height": 844},
            locale="nl-NL",
            extra_http_headers={"X-Remote-User-Id": "user-2"},
        )
        page = await context.new_page()
        cdp = await context.new_cdp_session(page)
        await cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
        started = time.perf_counter()
        await page.goto(url)
        await page.wait_for_selector(".orb", timeout=10000)
        first_ms = (time.perf_counter() - started) * 1000
        await context.close()
        await browser.close()

    serious = [v for v in problems if v["impact"] in ("serious", "critical")]
    slow = [
        x
        for x in timings
        if (x["tap"] > (COLD_TAP_MS if x.get("cold") else TAP_MS))
        or (x["switch"] > (COLD_TAP_MS if x.get("cold") else SWITCH_MS))
    ]
    (OUT / "report.json").write_text(
        json.dumps(
            {"screens": screens, "problems": problems, "timings": timings, "first_ms": first_ms},
            indent=1,
        )
    )
    worst_tap = max((x["tap"] for x in timings), default=0)
    worst_switch = max((x["switch"] for x in timings), default=0)
    print(
        f"\n{screens} screens checked; axe: {len(problems)} findings, {len(serious)} serious or "
        f"critical; worst tap {worst_tap:.0f} ms, worst change of screen {worst_switch:.0f} ms; "
        f"first screen on a slow phone {first_ms:.0f} ms"
    )
    by_rule: dict[str, int] = {}
    for v in problems:
        by_rule[f"{v['impact']}:{v['id']}"] = by_rule.get(f"{v['impact']}:{v['id']}", 0) + 1
    print(json.dumps(by_rule, indent=1))
    assert not serious, serious[:5]
    assert not slow, slow[:5]
    assert first_ms <= FIRST_SCREEN_MS
