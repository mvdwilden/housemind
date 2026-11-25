"""Test het huis (E-474, E-475, E-518, design 70, 85) in a real browser, from the user's side: on
a wide screen in a browser a small round button at the top right opens the panel on the right, in
the place of Speel het huis (since 14-11-2026, the administrator's wish; before, it was in the
menu on the left); on a phone it stays the tab 'Testen'. Speel het na says what HouseMind would do
and how that goes for real, switching nothing, and Doe het echt does it in the house.

Runs with the screens: ``pytest -m ui tests/test_ui_test_house.py`` (a built frontend and
Playwright's Chromium). Fails without the panel: there was no 'Test het huis' in the menu; and
without the button at the top right: 'Test het huis' was in the menu on the left."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_ui import AXE, DIST
from tests.test_ui_live_devices import _page, _served

playwright = pytest.importorskip("playwright.async_api")

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not AXE.exists() or not DIST.exists(), reason="frontend not built"),
]


# The Home Assistant app on a computer says so in its user agent.
HA_APP = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Home Assistant/2026.10"
)


def _button(page: Any) -> Any:
    """The round button 'Test het huis' at the top right."""
    return page.locator("header .corner").get_by_role("button", name="Test het huis")


async def test_on_a_computer_a_round_button_at_the_top_right_on_a_phone_the_tab(
    tmp_path: Path,
) -> None:
    """On a computer, in a browser, Test het huis is no longer in the menu on the left but a
    small round button at the top right, after Help and the tour and before House settings (the
    administrator's wish, 14-11-2026). It opens the column on the right, is lit while it is
    open, and closes it again – as does Sluiten in the panel. On a phone nothing changed: the
    tab 'Testen' at the bottom opens it as a page, and there is no button at the top. Where
    the column cannot stand – a narrower window, the Home Assistant app – it stays in the
    menu."""
    async with _served(tmp_path, running=False) as (url, demo):
        t = demo.hm.languages.text
        async with playwright.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await _page(browser, url)
            assert t("nl", "tabs.test") == "Test het huis"
            assert (
                await page.locator("nav.side").get_by_role("button", name="Test het huis").count()
                == 0
            )
            assert (
                await page.locator("nav.side")
                .get_by_role("button", name=t("nl", "tabs.tidy"))
                .count()
                == 1
            )
            button = _button(page)
            assert await button.get_attribute("title") == "Test het huis"
            assert await button.get_attribute("aria-pressed") == "false"
            names = await page.locator("header .corner button.round").evaluate_all(
                "bs => bs.map(b => b.getAttribute('aria-label'))"
            )
            assert names.index("Test het huis") == names.index(t("nl", "ui.tour.start")) + 1, names
            assert names.index("Test het huis") == names.index(t("nl", "ui.house_settings")) - 1, (
                names
            )
            panel = page.locator("aside.play-column")
            await panel.get_by_role("heading", name="Speel het huis").wait_for(timeout=5000)
            await button.click()
            await panel.get_by_role("heading", name="Test het huis").wait_for(timeout=5000)
            assert await button.get_attribute("aria-pressed") == "true"
            lit = await button.evaluate("b => getComputedStyle(b).borderColor")
            # The same button closes it: the column is Speel het huis's again.
            await button.click()
            await panel.get_by_role("heading", name="Speel het huis").wait_for(timeout=5000)
            assert await button.get_attribute("aria-pressed") == "false"
            assert await button.evaluate("b => getComputedStyle(b).borderColor") != lit
            # Sluiten in the panel closes it too, and the button goes out.
            await button.click()
            await panel.get_by_role("button", name="Sluiten").click()
            await panel.get_by_role("heading", name="Speel het huis").wait_for(timeout=5000)
            assert await button.get_attribute("aria-pressed") == "false"
            await page.context.close()

            async def at(width: int, height: int, agent: str | None = None) -> Any:
                context = await browser.new_context(
                    viewport={"width": width, "height": height},
                    locale="nl-NL",
                    color_scheme="dark",
                    extra_http_headers={"X-Remote-User-Id": "user-4"},
                    **({"user_agent": agent} if agent else {}),
                )
                shown = await context.new_page()
                await shown.goto(url)
                await shown.wait_for_selector(".orb", timeout=10000)
                return shown

            # On a phone: the tab 'Testen' at the bottom, as before; no button at the top.
            phone = await at(390, 844)
            assert await _button(phone).count() == 0
            tab = phone.locator("nav.tabs").get_by_role("button", name=t("nl", "tabs_short.test"))
            await tab.click()
            await (
                phone.locator("main")
                .get_by_role("heading", name="Test het huis")
                .wait_for(timeout=5000)
            )
            assert await tab.get_attribute("aria-current") == "page"
            await phone.context.close()

            # A narrower window, and the Home Assistant app on a computer: in the menu, a page.
            narrow = await at(1000, 800)
            assert await _button(narrow).count() == 0
            item = narrow.locator("nav.side").get_by_role("button", name="Test het huis")
            await item.click()
            await (
                narrow.locator("main")
                .get_by_role("heading", name="Test het huis")
                .wait_for(timeout=5000)
            )
            # The window grows wide while the page is open: the column takes it over.
            await narrow.set_viewport_size({"width": 1440, "height": 900})
            column = narrow.locator("aside.play-column")
            await column.get_by_role("heading", name="Test het huis").wait_for(timeout=5000)
            assert await _button(narrow).get_attribute("aria-pressed") == "true"
            assert await item.count() == 0
            assert (
                await narrow.locator("main").get_by_role("heading", name="Test het huis").count()
                == 0
            )
            await narrow.context.close()
            app = await at(1440, 900, HA_APP)
            assert await _button(app).count() == 0
            assert (
                await app.locator("nav.side").get_by_role("button", name="Test het huis").count()
                == 1
            )
            await app.context.close()
            await browser.close()


async def test_the_button_opens_the_panel_and_pretend_switches_nothing(tmp_path: Path) -> None:
    """'Test het huis' at the top right opens the panel right of the house; 'Iemand komt binnen'
    and 'Donker' in a room with a lamp give a line with what HouseMind would do and how that
    goes for real, and the lamp stays as it was until Doe het echt."""
    async with _served(tmp_path, running=False) as (url, demo):
        hm = demo.hm
        room = next(
            a
            for a in sorted(hm.model.areas.values(), key=lambda a: a.name.lower())
            if "someone" in [s["key"] for s in hm.test_view("nl", a.id, [])["situations"]]
            and any(
                (x := hm.model.area_of(e)) is not None and x.id == a.id
                for e in hm.model.entities
                if e.startswith("light.")
            )
        )
        before = {e: s.state for e, s in demo.house.ha.states.items()}
        async with playwright.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await _page(browser, url)
            await _button(page).click()
            panel = page.locator("aside.play-column")
            await panel.get_by_role("heading", name="Test het huis").wait_for(timeout=5000)
            await panel.locator("select").select_option(room.id)
            await panel.get_by_role("button", name="Iemand komt binnen").click()
            await panel.get_by_role("button", name="Donker", exact=True).click()
            line = panel.locator(".result li").filter(has_text="aandoen").first
            await line.wait_for(timeout=5000)
            assert "Staat op " in await line.locator(".real").inner_text()
            await panel.get_by_role("button", name="Doe het echt").wait_for(timeout=5000)
            assert {e: s.state for e, s in demo.house.ha.states.items()} == before
            # Closing gives the column back to Speel het huis.
            await panel.get_by_role("button", name="Sluiten").click()
            await panel.get_by_role("heading", name="Speel het huis").wait_for(timeout=5000)
            await browser.close()


async def test_do_it_for_real_switches_and_put_back_returns_it(tmp_path: Path) -> None:
    """The administrator's wish: 'als ik simuleer dat iemand binnenkomt, dan moet ook
    daadwerkelijk de lamp aan.' Iemand komt binnen and Donker, then Doe het echt: the lamp goes
    on in the house, the panel says 'Test loopt' with when it goes back and 'Gedaan' per line,
    and Zet terug puts it back at once (E-518)."""
    async with _served(tmp_path, running=False) as (url, demo):
        hm = demo.hm
        found = None
        for area in sorted(hm.model.areas.values(), key=lambda a: a.name.lower()):
            view = hm.test_view("nl", area.id, ["someone", "dark"])
            line = next((x for x in view["lines"] if x["will"] and "aandoen" in x["text"]), None)
            if line is not None and "someone" in [s["key"] for s in view["situations"]]:
                lamp = next(
                    e
                    for e, entity in hm.model.entities.items()
                    if e.startswith("light.") and entity.name and entity.name in line["text"]
                )
                found = (area.id, lamp)
                break
        assert found is not None
        area_id, lamp = found
        assert demo.house.ha.states[lamp].state == "off"
        async with playwright.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await _page(browser, url)
            await _button(page).click()
            panel = page.locator("aside.play-column")
            await panel.locator("select").select_option(area_id)
            await panel.get_by_role("button", name="Iemand komt binnen").click()
            await panel.get_by_role("button", name="Donker", exact=True).click()
            await panel.get_by_role("button", name="Doe het echt").click()
            running = panel.locator(".played.running")
            await running.get_by_text("Test loopt").wait_for(timeout=5000)
            assert "Gedaan." in await running.inner_text()
            assert demo.house.ha.states[lamp].state == "on"
            await running.get_by_role("button", name="Zet terug").click()
            await panel.get_by_text("Test voorbij", exact=True).wait_for(timeout=5000)
            assert demo.house.ha.states[lamp].state == "off"
            assert "weer zoals het was" in await panel.locator(".played").inner_text()
            await browser.close()


async def test_a_quarter_shows_at_the_top_and_stop_ends_it(tmp_path: Path) -> None:
    """Loop zelf rond, the testkwartier, for the lighting: the top of the screen says 'Test
    loopt tot …' with Stop; Stop ends it, and the bar is gone (E-477, E-478)."""
    async with _served(tmp_path, running=False) as (url, demo):
        hm = demo.hm
        area_id = next(
            a.id
            for a in sorted(hm.model.areas.values(), key=lambda a: a.name.lower())
            if "lighting" in [p["key"] for p in hm.test_view("nl", a.id, [])["quarter"]["parts"]]
        )
        async with playwright.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await _page(browser, url)
            await _button(page).click()
            panel = page.locator("aside.play-column")
            await panel.locator("select").select_option(area_id)
            await panel.get_by_role("heading", name="Loop zelf rond").wait_for(timeout=5000)
            row = panel.locator(".parts li").filter(has_text="Verlichting")
            await row.get_by_role("button", name="Start").click()
            bar = page.locator("header .testing")
            await bar.get_by_text("Test loopt tot").wait_for(timeout=5000)
            assert hm.test_running("nl")
            await bar.get_by_role("button", name="Stop").click()
            await bar.wait_for(state="detached", timeout=5000)
            assert hm.test_running("nl") == []
            await browser.close()
