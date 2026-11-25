"""The rhythm bar in a real browser (E-507, B-433). The administrator (12-11-2026): "In Brein ›
Ritme mis ik het visuele overzicht" – the manual shows per type of day a bar of 24 hours, the
app showed only words until getting up and going to bed were settled. Now, after two nights:
Werkdagen has a bar of parts as in the manual, the unsure stretches striped over it, 'leert
nog' beside it, the line 'nu' at the time of the house, 0:00 to 24:00 under it and a legend with
an icon and a word; a type of day with too few nights has an empty track that says so. On a
phone the name stands above the bar and nothing scrolls sideways.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_rhythm_bar.py``
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
from housemind.world.calendar import DayType
from housemind.world.house_mode import HouseMode
from tests.test_rhythm import TZ

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

# Open a tab of the menu, then a tab of Brein, by its word.
TAB = """async (name) => {
  [...document.querySelectorAll("nav button")]
    .find((b) => b.offsetParent && b.textContent.includes(name)).click();
  await new Promise((r) => setTimeout(r, 1200));
}"""
SUBTAB = """async (name) => {
  [...document.querySelectorAll("main button")]
    .find((b) => b.textContent.trim().startsWith(name)).click();
  await new Promise((r) => setTimeout(r, 1500));
}"""

# What the card shows: per bar its name, words, parts, stripes and 'nu'; the legend; the page.
CARD = """() => {
  const card = document.querySelector("section.rhythm");
  const box = (el) => el.getBoundingClientRect();
  const bars = [...card.querySelectorAll(".bars > .track")].map((track) => {
    const name = track.previousElementSibling;
    return {
      name: name.querySelector("b").textContent,
      small: name.querySelector("small")?.textContent ?? null,
      label: track.getAttribute("aria-label"),
      empty: track.classList.contains("empty"),
      text: track.textContent.trim(),
      parts: [...track.querySelectorAll(".part")].map((p) => ({
        kind: [...p.classList].find((c) => c !== "part"),
        width: box(p).width,
        icon: !!p.querySelector("svg"),
        colour: getComputedStyle(p).backgroundColor,
      })),
      unsure: track.querySelectorAll(".unsure").length,
      now: track.querySelector(".now-line") ? {
        left: (box(track.querySelector(".now-line")).left - box(track).left) / box(track).width,
        word: track.querySelector(".now-line span").textContent,
      } : null,
      above: box(name).bottom <= box(track).top + 1,
      height: box(track).height,
    };
  });
  return {
    bars,
    axis: [...card.querySelectorAll(".axis span")].map((s) => s.textContent),
    legend: [...card.querySelectorAll(".legend li")].map((li) => ({
      word: li.textContent.trim(),
      icon: !!li.querySelector("svg"),
    })),
    page: [document.documentElement.scrollWidth, window.innerWidth],
  };
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind]]:
    # Tuesday 3 November, 11:00 in the house: a workday.
    clock = SimulatedClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 3, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    # Two workday nights: up at 6:30 and 7:10, to bed at 23:00 and 23:40 – not a habit yet.
    # The demo house brings one weekend night of its own: too few for a bar.
    hm.rhythm.wake.pop("workday", None)
    hm.rhythm.bed.pop("workday", None)
    work = DayType.WORKDAY
    for day, wake, bed in ((29, (6, 30), (23, 0)), (30, (7, 10), (23, 40))):
        up = datetime(2026, 10, day, *wake, tzinfo=TZ)
        down = datetime(2026, 10, day, *bed, tzinfo=TZ)
        hm.rhythm.record(HouseMode.SLEEP, HouseMode.HOME, up, work, work)
        hm.rhythm.record(HouseMode.HOME, HouseMode.SLEEP, down, work, work)
    hm.rhythm.wake["free"] = [9 * 60]
    hm.rhythm.bed.pop("free", None)
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm
    await runner.cleanup()
    await hm.stop()


async def test_the_bar_while_learning_as_in_the_manual(house: tuple[str, HouseMind]) -> None:
    url, hm = house

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        # Light on a computer and a phone, dark on a computer: the colours of the manual.
        for width, height, scheme in (
            (1280, 900, "light"),
            (390, 844, "light"),
            (1280, 900, "dark"),
        ):
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                color_scheme=scheme,
                locale="nl-NL",
                extra_http_headers={"X-Remote-User-Id": ADMIN},
            )
            page: Any = await context.new_page()
            await page.goto(url)
            await page.wait_for_selector(".orb", timeout=10000)
            brain = t("tabs.brain") if width > 800 else t("tabs_short.brain")
            await page.evaluate(TAB, brain)
            await page.evaluate(SUBTAB, t("ui.brain.tab.rhythm"))
            await page.wait_for_selector("section.rhythm .bars .track")
            card = await page.evaluate(CARD)
            work, free = card["bars"]

            # Werkdagen: a bar from the two nights, as in the manual, with what is unsure.
            assert work["name"] == "Werkdagen" and work["small"] == "leert nog", card
            assert [part["kind"] for part in work["parts"]] == ["sleep", "home", "sleep"], card
            assert work["unsure"] == 2, card  # around getting up and around going to bed
            assert work["parts"][0]["icon"] and work["parts"][1]["icon"], card  # moon, house
            assert not work["parts"][2]["icon"], card  # 23:40–24:00 has no room for one
            sleep = "rgb(63, 79, 176)" if scheme == "light" else "rgb(133, 146, 224)"
            assert work["parts"][0]["colour"] == sleep, card
            assert len({part["colour"] for part in work["parts"]}) == 2, card
            assert work["label"].startswith("Werkdagen: slapen 00:00–07:10, thuis 07:10–23:40")
            assert work["label"].endswith("; leert nog"), card
            assert work["height"] == 22, card
            # 'nu' at 11:00 in the house: 11/24 of the day, only on today's bar.
            assert work["now"] is not None and work["now"]["word"] == "nu", card
            assert abs(work["now"]["left"] - 11 / 24) < 0.01, card
            # Weekend: one morning seen, no bedtime – an empty track that says why.
            assert free["empty"] and free["text"] == "nog niet genoeg nachten", card
            assert free["parts"] == [] and free["now"] is None, card

            assert card["axis"] == ["0:00", "6:00", "12:00", "18:00", "24:00"], card
            words = [item["word"] for item in card["legend"]]
            assert words == ["Slapen", "Ochtend", "Weg", "Thuis", "Leert nog"], card
            assert all(item["icon"] for item in card["legend"][:4]), card

            # On a phone the name stands above the bar; nothing scrolls sideways.
            assert work["above"] == (width < 560), card
            assert card["page"][0] <= card["page"][1], card
            if SHOTS:
                rhythm = page.locator("section.rhythm")
                await rhythm.screenshot(path=f"{SHOTS}/rhythm-{width}-{scheme}.png")
            await context.close()
        await browser.close()
