"""The screen connects again at once (E-456, B-371), in a real browser. The administrator's
question (03-11-2026): "ik zie af en toe, als ik HM open in HA, en deze stond daar nog, dan zie
ik deze melding: 'Geen verbinding – opnieuw proberen…'. Gaat dat vanzelf weer opnieuw
verbinden?" It did, but each try waited twice as long as the one before, up to half a minute,
and in a tab in the background longer still. Now the screen tries at once when you come back
to it or the network is back, and while you look at it never waits more than ten seconds.

Runs only on request, like tests/test_ui.py:
``uv run pytest -m ui tests/test_ui_reconnect.py`` (needs a built frontend and
``uv run playwright install chromium`` once). Each test fails without the change."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp import WSCloseCode, web

from housemind import __version__
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import SESSIONS_KEY, create_app

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
SEEN = f'{{"onboarded": true, "tour": [1, 2, 3, 4], "seen_version": "{__version__}"}}'
PERSON = "demo-user-1"
OFFLINE = "p.offline"  # 'Geen verbinding – opnieuw proberen…'

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]


class Served:
    """HouseMind behind a port that can go away and come back, as during an update or when
    the network drops."""

    def __init__(self, app: web.Application, runner: web.AppRunner, site: web.TCPSite) -> None:
        self.app, self.runner, self.site = app, runner, site
        self.port = runner.addresses[0][1]
        self.url = f"http://127.0.0.1:{self.port}/"

    async def down(self) -> None:
        await self.site.stop()
        for session in [*self.app[SESSIONS_KEY]]:
            await session.ws.close(code=WSCloseCode.GOING_AWAY)

    async def up(self) -> None:
        self.site = web.TCPSite(self.runner, "127.0.0.1", self.port)
        await self.site.start()


@pytest.fixture
async def served(tmp_path: Path) -> AsyncIterator[Served]:
    clock = SimulatedClock(datetime(2026, 11, 3, 15, 0, tzinfo=UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (PERSON, PERSON, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    app = create_app(hm, DIST, ingress_only=False, demo=True)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield Served(app, runner, site)
    await runner.cleanup()
    await hm.stop()


async def _lost(browser: Any, served: Served, gone: float) -> tuple[Any, Any]:
    """The screen open and connected; then HouseMind is away for ``gone`` seconds, and back.
    The screen says it has no connection; its tries grew longer in the meantime."""
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        locale="nl-NL",
        extra_http_headers={"X-Remote-User-Id": PERSON},
    )
    page = await ctx.new_page()
    await page.goto(served.url)
    await page.wait_for_selector("nav button", timeout=10000)
    await page.wait_for_selector(OFFLINE, state="detached", timeout=10000)
    await served.down()
    await page.wait_for_selector(OFFLINE, timeout=5000)
    assert "opnieuw proberen" in await page.locator(OFFLINE).inner_text()
    await page.wait_for_timeout(gone * 1000)
    await served.up()
    return ctx, page


async def test_back_on_the_screen_it_connects_at_once(served: Served) -> None:
    """Coming back to the screen: it tries at once. Before, the next try came only after the
    wait, which had grown to eight seconds here – and to half a minute after a while."""
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        _ctx, page = await _lost(browser, served, 9)  # tries at 1, 3 and 7 s; the next at 15
        start = time.monotonic()
        await page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")
        await page.wait_for_selector(OFFLINE, state="detached", timeout=2500)
        assert time.monotonic() - start < 2.5
        await browser.close()


async def test_the_network_back_it_connects_at_once(served: Served) -> None:
    """The network was away and is back: the screen tries at once."""
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        ctx, page = await _lost(browser, served, 9)
        await ctx.set_offline(True)
        start = time.monotonic()
        await ctx.set_offline(False)  # the browser says the network is back
        await page.wait_for_selector(OFFLINE, state="detached", timeout=2500)
        assert time.monotonic() - start < 2.5
        await browser.close()


async def test_on_the_screen_it_never_waits_more_than_ten_seconds(served: Served) -> None:
    """While you look at it, at most ten seconds between two tries, also after a long time
    away (an update takes about a minute); before, half a minute."""
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        _ctx, page = await _lost(browser, served, 16)  # tries at 1, 3, 7 and 15 s
        start = time.monotonic()
        await page.wait_for_selector(OFFLINE, state="detached", timeout=12000)
        assert time.monotonic() - start < 10.5  # the next at 25 s; before at 31 s
        await browser.close()
