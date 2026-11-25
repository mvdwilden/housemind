"""Tiles instead of big blocks above a list (E-454, B-368), in a real browser. The
administrator's wish (03-11-2026): "ik vind de pagina ‘Klaar voor autonomie?’ te groot; ... als
ik op het pijltje klik (bv: 32 ruimtes), dan filtert de vaardigheden ergens onderaan de pagina
... het blok ‘Klaar voor autonomie?’ is zo groot dat je het niet meteen ziet." – then: "Kies
voor ontwerp B, en kijk ook even naar andere pagina's waarbij zo'n soort filter ook bestaat".
So: the parts are tiles in one row, only the part you tap has its text and buttons in one card
under them, and the list is right below, in sight; the overview per kind above a long list
(Logboek and the others) is tiles too. Then, with a screenshot of the real house on a wide
screen: the rings lay over the steps, and the row was not full – the tiles fill the row, and
the ring is at the bottom right, beside the rooms, never over a step; the name has the whole
width, so a long word ("Raambekleding") is never broken in two.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_tiles.py``
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

# Open a tab of Brein by its word.
SUBTAB = """async (name) => {
  [...document.querySelectorAll("main button")]
    .find((b) => b.textContent.trim().startsWith(name)).click();
  await new Promise((r) => setTimeout(r, 1500));
}"""

# Open a tab of the menu by its word.
TAB = """async (name) => {
  [...document.querySelectorAll("nav button")]
    .find((b) => b.offsetParent && b.textContent.includes(name)).click();
  await new Promise((r) => setTimeout(r, 1200));
}"""

# The parts of Klaar voor autonomie?: the tiles, the card and where the list starts.
READY = """() => {
  const ready = document.querySelector('[data-section="readiness"]');
  const tiles = [...ready.querySelectorAll(":scope > .tiles > .tile")];
  const box = (el) => el.getBoundingClientRect();
  const over = (a, b) =>
    a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  const row = box(ready.querySelector(":scope > .tiles"));
  const card = ready.querySelector("article.chosen");
  const list = ready.querySelector("section.list");
  return {
    tiles: tiles.map((t) => ({
      top: Math.round(t.getBoundingClientRect().top),
      pressed: t.getAttribute("aria-pressed"),
      name: t.querySelector(".name").textContent.trim(),
      // A ring that lies over a step, or sticks out of its tile.
      covers: [...t.querySelectorAll(".score")].some((ring) =>
        [...t.querySelectorAll(".counts > *, .name, .sub")].some((el) => over(box(ring), box(el)))
        || box(ring).right > box(t).right + 0.5
        || box(ring).top < box(t.querySelector(".name")).bottom),
      // The lines the name takes: one word is one line.
      lines: (() => {
        const range = document.createRange();
        range.selectNodeContents(t.querySelector(".name"));
        return new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size;
      })(),
    })),
    // How much of the row is left empty to the right of the last tile.
    gap: Math.round(row.right - Math.max(...tiles.map((t) => box(t).right))),
    card: card ? card.querySelector("h3").textContent.trim() : null,
    texts: ready.querySelectorAll(":scope > .tiles p.text, :scope > article p.text").length,
    listTop: Math.round(list.getBoundingClientRect().top),
    blockTop: Math.round(ready.getBoundingClientRect().top),
    screen: innerHeight,
    page: [document.documentElement.scrollWidth, innerWidth],
    count: list.querySelector(".tools .count")?.textContent ?? "",
  };
}"""

# Tap a tile of Klaar voor autonomie? by its number in the row.
PICK = """async (i) => {
  document.querySelectorAll('[data-section="readiness"] > .tiles > .tile')[i].click();
  await new Promise((r) => setTimeout(r, 600));
}"""

# The overview above a long list on this page: its tiles, and a tap on the first.
OVERVIEW = """async () => {
  const box = document.querySelector("main details.overview[open]");
  const tiles = () => [...box.querySelectorAll(".tiles li")];
  const pressed = (li) => li.querySelector("button")?.getAttribute("aria-pressed") ?? null;
  const before = tiles().map(pressed);
  const rows = new Set(tiles().map((li) => Math.round(li.getBoundingClientRect().top))).size;
  const table = box.querySelectorAll("table").length;
  box.querySelector(".tiles button[aria-pressed]").click();
  await new Promise((r) => setTimeout(r, 600));
  return {before, rows, table, after: pressed(tiles()[0]),
          tiles: tiles().length,
          count: document.querySelector("main .tools .count")?.textContent ?? ""};
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind]]:
    clock = SimulatedClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm
    await runner.cleanup()
    await hm.stop()


async def test_the_parts_are_tiles_and_the_list_stays_in_sight(
    house: tuple[str, HouseMind],
) -> None:
    url, hm = house

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        # A computer, a wide screen as in the real house, and a phone.
        for width, height in ((1280, 900), (1800, 1000), (390, 844)):
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                locale="nl-NL",
                extra_http_headers={"X-Remote-User-Id": ADMIN},
            )
            page: Any = await context.new_page()
            await page.goto(url)
            await page.wait_for_selector(".orb", timeout=10000)
            brain = t("tabs.brain") if width > 800 else t("tabs_short.brain")
            await page.evaluate(TAB, brain)
            await page.evaluate(SUBTAB, t("ui.brain.tab.readiness"))  # Brein keeps its last tab
            await page.wait_for_selector('[data-section="readiness"] > .tiles > .tile')
            ready = await page.evaluate(READY)
            tiles = ready["tiles"]
            assert len(tiles) >= 3, ready
            rows = len({tile["top"] for tile in tiles})
            # On a computer the parts in one row; on a phone two by two.
            assert rows == (1 if width > 800 else (len(tiles) + 1) // 2), ready
            # The tiles fill the row, and no ring lies over a step or the name.
            assert ready["gap"] <= 1, ready
            assert not any(tile["covers"] for tile in tiles), ready
            assert all(tile["lines"] == 1 for tile in tiles if " " not in tile["name"]), ready
            # Nothing chosen: no card, no long texts, every tile not pressed.
            assert ready["card"] is None and ready["texts"] == 0, ready
            assert {tile["pressed"] for tile in tiles} == {"false"}, ready
            assert ready["page"][0] <= ready["page"][1], ready  # no sideways scrolling
            if SHOTS:
                await page.screenshot(path=f"{SHOTS}/ready-{width}.png", full_page=False)

            # A tap: that tile pressed, its card under the tiles, the list filtered – and on a
            # computer the list starts on the screen, without scrolling.
            await page.evaluate(PICK, 0)
            chosen = await page.evaluate(READY)
            assert chosen["tiles"][0]["pressed"] == "true", chosen
            assert chosen["card"] == tiles[0]["name"] and chosen["texts"] == 1, chosen
            assert "(" in chosen["count"], chosen  # "32 (44 in totaal)": filtered
            if width > 800:
                assert chosen["listTop"] < chosen["screen"], chosen
            if SHOTS:
                await page.screenshot(path=f"{SHOTS}/ready-{width}-picked.png", full_page=False)
            # Again: all, and no card.
            await page.evaluate(PICK, 0)
            again = await page.evaluate(READY)
            assert again["card"] is None and "(" not in again["count"], again

            # The overview above a long list – here Apparaten in Brein – is tiles too, side
            # by side; a tap filters.
            if width > 800:
                await page.evaluate(SUBTAB, t("ui.brain.tab.devices"))
                await page.wait_for_selector("main details.overview .tiles li")
                seen = await page.evaluate(OVERVIEW)
                assert seen["table"] == 0 and seen["rows"] < seen["tiles"], seen
                assert set(seen["before"]) - {None} == {"false"} and seen["after"] == "true", seen
                assert "(" in seen["count"], seen
                if SHOTS:
                    await page.screenshot(path=f"{SHOTS}/devices-{width}.png", full_page=False)
            await context.close()
        await browser.close()


# The buttons under the filter bar of the list that is open on the page.
TOOLS = """() => [...document.querySelectorAll("main .tools button")]
  .map((b) => b.textContent.trim())"""

# Open a kind of Klaar voor autonomie? – Vaardigheden or Automatiseringen – by its word.
KIND = """async (name) => {
  [...document.querySelectorAll('[data-section="readiness"] .kinds button')]
    .find((b) => b.textContent.trim().startsWith(name)).click();
  await new Promise((r) => setTimeout(r, 600));
}"""


async def test_klaar_voor_autonomie_has_no_columns_and_no_export(
    house: tuple[str, HouseMind],
) -> None:
    """The administrator's wish (12-11-2026), with a screenshot of Klaar voor autonomie? and a
    red box round Kolommen and Exporteren: "Deze zijn overbodig en mogen verwijderd worden" –
    only there (E-501). The count stays; Apparaten still has both buttons."""
    url, hm = house

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1800, "height": 1000},
            locale="nl-NL",
            extra_http_headers={"X-Remote-User-Id": ADMIN},
        )
        page: Any = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector(".orb", timeout=10000)
        await page.evaluate(TAB, t("tabs.brain"))
        await page.evaluate(SUBTAB, t("ui.brain.tab.readiness"))
        await page.wait_for_selector('[data-section="readiness"] section.list .tools .count')
        for kind in ("ui.ready.tab.skills", "ui.ready.tab.automations"):
            if await page.locator('[data-section="readiness"] .kinds button').count():
                await page.evaluate(KIND, t(kind))
            assert await page.evaluate(TOOLS) == [], kind
            count = page.locator('[data-section="readiness"] section.list .tools .count')
            assert (await count.text_content() or "").strip(), kind  # the number stays
        await page.evaluate(SUBTAB, t("ui.brain.tab.devices"))
        await page.wait_for_selector("main section.devices .tools button")
        names = await page.evaluate(TOOLS)
        assert any(t("ui.list.columns") in n for n in names), names
        assert any(t("ui.list.export") in n for n in names), names
        await context.close()
        await browser.close()
