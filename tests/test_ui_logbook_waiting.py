"""On top of Logboek, "Wacht op jou": the messages the number at Logboek counts (E-452,
B-366), in a real browser. The administrator's question (02-11-2026): "In de linkerkant staat
er een cijfer (bijvoorbeeld: 1) dat aangeeft hoeveel nieuwe meldingen er zijn? klopt dat? Maar
als klik op Logboek, dan zie ik niet meteen welke dan nieuw zijn?" So: the number counts what
still waits for you; a tap on Logboek shows exactly those first, with their buttons, and not a
second time in the timeline; once handled, a message leaves the block and the number, and is in
the timeline as handled.

Runs only on request, like tests/test_ui.py:
``uv run pytest -m ui tests/test_ui_logbook_waiting.py`` (needs a built frontend and
``uv run playwright install chromium`` once)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from aiohttp import web

from housemind import __version__
from housemind.brain.notify import Kind
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
SEEN = f'{{"onboarded": true, "tour": [1, 2, 3, 4], "seen_version": "{__version__}"}}'
ADMIN = "demo-user-1"

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

# The number at Logboek in the menu that is on screen, or 0 without one.
NUMBER = """(name) => {
  const nav = [...document.querySelectorAll("nav.side, nav.tabs")]
    .find((n) => getComputedStyle(n).display !== "none");
  const tab = [...nav.querySelectorAll("button")].find((b) => b.textContent.includes(name));
  return Number(tab.querySelector(".count")?.textContent ?? 0);
}"""

# Tap Logboek; then the first heading of the page, the messages in "Wacht op jou" with their
# buttons, and which messages the timeline has – and whether as handled.
OPEN = """async (name) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  [...document.querySelectorAll("nav button")]
    .find((b) => b.offsetParent && b.textContent.includes(name)).click();
  await wait(1200);
  const block = document.querySelector("main section.waiting");
  return {
    first: document.querySelector("main h2")?.firstChild?.textContent.trim() ?? null,
    count: Number(block?.querySelector("h2 .n")?.textContent ?? 0),
    waiting: [...(block?.querySelectorAll("li[data-notice]") ?? [])].map((li) => ({
      id: Number(li.dataset.notice),
      buttons: [...li.querySelectorAll(".actions button")].map((b) => b.textContent.trim()),
    })),
    timeline: [...document.querySelectorAll("main li[data-notice]")]
      .filter((li) => !li.closest("section.waiting"))
      .map((li) => ({
        id: Number(li.dataset.notice),
        handled: Boolean(li.querySelector(".notice.handled")),
      })),
  };
}"""

# In "Wacht op jou", tap a button of one message.
TAP = """async ([id, label]) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const li = document.querySelector(`section.waiting li[data-notice="${id}"]`);
  [...li.querySelectorAll(".actions button")].find((b) => b.textContent.trim() === label).click();
  await wait(1500);
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind, list[int]]]:
    clock = SimulatedClock(datetime(2026, 11, 2, 15, 0, tzinfo=UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    assert hm.router is not None
    local = clock.now().astimezone(ZoneInfo("Europe/Amsterdam"))
    ids = []
    for topic, key in (
        ("note:one", "notify.ai_budget_warn"),
        ("note:two", "notify.vacation_suggestion"),
    ):
        await hm.router.raise_(
            Kind.IMPORTANT,
            topic,
            key,
            {},
            hm._people,
            clock.now(),
            local,
            False,
            actions=(("dismiss", "dismiss"),),
        )
        row = await hm.db.fetchone("SELECT id FROM notifications WHERE topic = ?", (topic,))
        ids.append(int(row["id"]))
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm, ids
    await runner.cleanup()
    await hm.stop()


async def test_logbook_shows_first_what_the_number_counts(
    house: tuple[str, HouseMind, list[int]],
) -> None:
    url, hm, ids = house

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    name, dismiss = t("tabs.logbook"), t("ui.notifications.dismiss")
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
            short = name if width > 800 else t("tabs_short.logbook")
            number = await page.evaluate(NUMBER, short)
            assert number >= 2, number

            # A tap on Logboek: "Wacht op jou" first, as many as the number, with buttons, and
            # those messages not a second time in the timeline.
            shown = await page.evaluate(OPEN, short)
            assert shown["first"] == t("ui.logbook.waiting"), shown
            assert shown["count"] == number == len(shown["waiting"]), (number, shown)
            waiting = {w["id"]: w["buttons"] for w in shown["waiting"]}
            assert set(ids) <= set(waiting) and dismiss in waiting[ids[0]], shown
            assert not set(waiting) & {line["id"] for line in shown["timeline"]}, shown

            if width > 800:
                # Handled: out of the block and the number, in the timeline as handled.
                await page.evaluate(TAP, [ids[0], dismiss])
                assert await page.evaluate(NUMBER, short) == number - 1
                after = await page.evaluate(OPEN, short)
                assert after["count"] == number - 1 and ids[0] not in {
                    w["id"] for w in after["waiting"]
                }, after
                assert {"id": ids[0], "handled": True} in after["timeline"], after
            await context.close()
        await browser.close()
