# ruff: noqa: E501 – the checks in the page are small JavaScript functions
"""What you do, you see at once (E-472, E-473), in a real browser, from the user's side. The
administrator's wishes (11-11-2026): "als ik de demo laat lopen, dan kan ik niet op de knoppen
drukken" and "ik zet een bewegingssensor aan, en dan zie ik geen verandering in apparaten
lijst". In the demo house's panel the buttons under 'Zelf spelen' work while the house plays,
and Brein › Apparaten follows what changes without opening the tab again – in the column
Status, which is off until you choose it under Kolommen (QA 3, schermen 21).

Runs with the screens: ``pytest -m ui tests/test_ui_live_devices.py`` (a built frontend and
Playwright's Chromium). Each test fails without the change: the buttons were off while the
house played, and the list kept the state of the moment its tab opened."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from housemind.server.web import create_app
from tests.test_live_devices import _expert
from tests.test_play import Demo, _demo
from tests.test_ui import AXE, DIST

playwright = pytest.importorskip("playwright.async_api")

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not AXE.exists() or not DIST.exists(), reason="frontend not built"),
]

MOTION = "binary_sensor.bedroom_motion"
# Brein › Apparaten, every room open; then the text of one device's row.
OPEN = """async ([brain, devices]) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const button = (text) => [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === text);
  button(brain).click(); await wait(300);
  button(devices).click(); await wait(500);
  for (const room of document.querySelectorAll(".devices details.group")) {
    if (!room.open) room.querySelector("summary").click();
  }
}"""
STATUS = """([id, title]) => {
  const li = document.querySelector(`.devices li[data-id="${id}"]`);
  const cell = [...(li?.querySelectorAll("dl.cells > div") ?? [])].find((d) => d.querySelector("dt")?.textContent.trim() === title);
  return cell ? cell.querySelector("dd").textContent.replace(/^[·\\s]+/, "").trim() : null;
}"""


@contextlib.asynccontextmanager
async def _served(tmp_path: Path, running: bool) -> AsyncIterator[tuple[str, Demo]]:
    async with _demo("simple", tmp_path, running=running) as demo:
        await _expert(demo.hm)
        runner = web.AppRunner(
            create_app(demo.hm, DIST, ingress_only=False, demo=True, play=demo.play)
        )
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        try:
            yield f"http://127.0.0.1:{runner.addresses[0][1]}/", demo
        finally:
            await runner.cleanup()


async def _page(browser: Any, url: str) -> Any:
    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        locale="nl-NL",
        color_scheme="dark",
        extra_http_headers={"X-Remote-User-Id": "user-4"},
    )
    page = await context.new_page()
    await page.goto(url)
    await page.wait_for_selector(".orb", timeout=10000)
    return page


async def test_the_list_follows_what_you_play(tmp_path: Path) -> None:
    """The house stands still: 'Beweging' in the panel shows in Brein › Apparaten at once,
    without opening the tab again – as a lamp does in the house."""
    async with _served(tmp_path, running=False) as (url, demo):
        t = demo.hm.languages.text
        async with playwright.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await _page(browser, url)
            await page.evaluate(OPEN, [t("nl", "tabs.brain"), t("nl", "ui.brain.tab.devices")])
            where = [MOTION, t("nl", "ui.list.status")]
            assert await page.evaluate(STATUS, where) is None  # not shown by default
            devices = page.locator("section.devices")
            await devices.get_by_role("button", name=t("nl", "ui.list.columns")).click()
            await devices.get_by_role("checkbox", name=t("nl", "ui.list.status")).check()
            assert await page.evaluate(STATUS, where) == "off"
            room = page.locator("aside.play-column").get_by_role("group", name="Slaapkamer")
            await room.get_by_role("button", name="Beweging", exact=True).click()
            # Without opening the tab again: the list follows by itself.
            await page.wait_for_function(f"(w) => ({STATUS})(w) === 'on'", arg=where, timeout=3000)
            await browser.close()


async def test_the_panel_works_while_the_house_plays(tmp_path: Path) -> None:
    """The house plays: the buttons under 'Zelf spelen' work, the time goes on, and a story
    still waits for Stop."""
    async with (
        _served(tmp_path, running=True) as (url, demo),
        playwright.async_playwright() as p,
    ):
        browser = await p.chromium.launch()
        page = await _page(browser, url)
        panel = page.locator("aside.play-column")
        room = panel.get_by_role("group", name="Slaapkamer")
        motion = room.get_by_role("button", name="Beweging", exact=True)
        await motion.wait_for(timeout=5000)
        assert await motion.is_enabled()
        await motion.click()
        await page.wait_for_function(
            "() => document.querySelector('aside.play-column [aria-label=\"Slaapkamer\"] button[aria-pressed=\"true\"]')?.textContent.trim() === 'Beweging'",
            timeout=3000,
        )
        assert demo.house.ha.states[MOTION].state == "on"
        assert demo.clock.running  # it goes on playing
        story = panel.locator("button.go").nth(1)  # a story's Play: it waits for Stop
        assert not await story.is_enabled()
        await browser.close()
