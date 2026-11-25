# ruff: noqa: E501 – the checks in the page are small JavaScript functions
"""QA 2, group 3 – use and accessibility, in a real browser (H-14, M-19 – M-23, M-25, L9).

Runs with the screens: ``pytest -m ui tests/test_ui_access.py`` (a built frontend and
Playwright's Chromium). Each test fails without its fix; what it checks is its docstring.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_ui import AXE, DIST, SEEN, TZ, house  # noqa: F401 – the fixture

playwright = pytest.importorskip("playwright.async_api")

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not AXE.exists() or not DIST.exists(), reason="frontend not built"),
]
ROOT = Path(__file__).resolve().parents[1]
ADMIN = "demo-user-1"  # an administrator in Home Assistant

# Contrast of two CSS colours (WCAG), with the brightness filters on the way up counted in.
CONTRAST = """
window.__lum = (c) => { const v = c.match(/[\\d.]+/g).slice(0, 3).map(Number).map(x => {
  x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]; };
window.__ratio = (a, b) => { const [x, y] = [__lum(a), __lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05); };
window.__bg = (el) => { for (let e = el; e; e = e.parentElement) {
  const c = getComputedStyle(e).backgroundColor;
  if (c && !/rgba\\(.*,\\s*0\\)$/.test(c) && c !== 'transparent') return c; }
  return 'rgb(255, 255, 255)'; };
window.__dim = (el, c) => { let f = 1; for (let e = el; e; e = e.parentElement) {
  const m = getComputedStyle(e).filter.match(/brightness\\(([\\d.]+)\\)/); if (m) f *= Number(m[1]); }
  const v = c.match(/[\\d.]+/g).slice(0, 3).map(Number).map(x => Math.round(x * f));
  return `rgb(${v[0]}, ${v[1]}, ${v[2]})`; };
"""


async def _person(hm: HouseMind, user_id: str, level: int) -> None:
    await hm.db.execute(
        "INSERT OR REPLACE INTO people(user_id, display_name, level, preferences, created) "
        "VALUES(?,?,?,?,?)",
        (user_id, "Beheerder", level, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()


async def _page(
    browser: Any, url: str, user: str, size: tuple[int, int], scheme: str = "dark", **store: Any
) -> Any:
    context = await browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        color_scheme="light" if scheme == "light" else "dark",
        locale="nl-NL",
        bypass_csp=True,  # only to add the contrast measure; the page itself is unchanged
        extra_http_headers={"X-Remote-User-Id": user},
        permissions=["microphone"],
    )
    page = await context.new_page()
    for key, value in store.items():
        await page.add_init_script(f"localStorage.setItem({json.dumps(key)}, {json.dumps(value)})")
    await page.goto(url)
    await page.wait_for_selector(".orb", timeout=10000)
    await page.add_script_tag(content=CONTRAST)
    return page


async def _browser(p: Any) -> Any:
    # A pretend microphone, so Vesta can listen in the test.
    return await p.chromium.launch(
        args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
    )


async def test_a_window_holds_the_keyboard_and_gives_it_back(
    house: tuple[str, HouseMind],  # noqa: F811
) -> None:
    """H-14: Tab left every sheet and focus did not come back after closing; Vesta's window
    got no focus. Now the focus goes in, Tab stays inside, and Escape gives it back."""
    url, hm = house
    t = hm.languages.text
    async with playwright.async_playwright() as p:
        browser = await _browser(p)
        page = await _page(browser, url, "user-2", (1280, 900))
        opener = page.get_by_role("button", name=t("nl", "ui.my_preferences"), exact=True).first
        await opener.focus()
        await page.keyboard.press("Enter")
        await page.wait_for_selector("[role=dialog]")
        for _ in range(40):
            await page.keyboard.press("Tab")
            assert await page.evaluate("() => !!document.activeElement.closest('[role=dialog]')"), (
                "Tab left the window"
            )
        for _ in range(5):
            await page.keyboard.press("Shift+Tab")
            assert await page.evaluate("() => !!document.activeElement.closest('[role=dialog]')")
        await page.keyboard.press("Escape")
        await page.wait_for_selector("[role=dialog]", state="detached")
        assert await opener.evaluate("(el) => el === document.activeElement")
        # Vesta's window takes the focus too.
        await page.locator("button.orb").first.click()
        await page.wait_for_selector(".window[role=dialog]")
        assert await page.evaluate("() => !!document.activeElement.closest('.window')")
        await browser.close()


async def test_the_manual_is_its_own_page_in_help(house: tuple[str, HouseMind]) -> None:  # noqa: F811
    """E-324: the formatted manual, the same page as online, opens in Help with numbered
    chapters and its own menu, its scripts allowed by the CSP (no bypass here) – at once, the
    Help is the manual (E-506). Groter gives the whole screen, Kleiner the window; Lees meer
    under Vesta's answer goes to its part; Escape inside the manual closes the window. On a
    phone no Groter and no new window, and nothing runs out of the window (M-19)."""
    url, hm = house
    t = hm.languages.text
    async with playwright.async_playwright() as p:
        browser = await _browser(p)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 860},
            locale="nl-NL",
            extra_http_headers={"X-Remote-User-Id": "user-2"},
        )
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector(".orb", timeout=10000)
        await page.get_by_role("button", name=t("nl", "ui.help"), exact=True).first.click()
        frame = await (await page.wait_for_selector("[role=dialog] iframe")).content_frame()
        await frame.wait_for_selector("section#wat h2")
        number = await frame.evaluate(
            "() => getComputedStyle(document.querySelector('section#wat h2'), '::before').content"
        )
        assert "counter(hoofdstuk)" in number, number  # each chapter counts itself
        assert (
            await frame.evaluate(
                "() => getComputedStyle(document.querySelector('section#wat')).counterIncrement"
            )
            == "hoofdstuk 1"
        )
        # Its menu script ran: it folds the chapters in.
        assert (
            await frame.evaluate(
                "() => document.querySelector('nav.inhoud .klap').getAttribute('aria-expanded')"
            )
            == "false"
        )
        theme = "() => document.documentElement.getAttribute('data-theme')"
        assert await frame.evaluate(theme) == await page.evaluate(theme)
        sheet = page.locator("[role=dialog]")
        await page.get_by_role("button", name=t("nl", "ui.manual.larger")).click()
        whole = await sheet.bounding_box()
        assert whole and whole["width"] >= 1279 and whole["height"] >= 859, whole
        await page.get_by_role("button", name=t("nl", "ui.manual.smaller")).click()
        window = await sheet.bounding_box()
        assert window and window["width"] <= 762, window
        assert await page.get_by_role("button", name=t("nl", "ui.manual.apart")).count() == 1
        await frame.locator("section#wat p").first.click()
        await page.keyboard.press("Escape")
        await page.wait_for_selector("[role=dialog]", state="detached")
        # Lees meer under Vesta's answer, asked in the manual's own box: the manual at that part.
        await page.get_by_role("button", name=t("nl", "ui.help"), exact=True).first.click()
        await page.locator("#help-ask").fill("Hoe praat ik terug met Vesta?")
        await page.locator("#help-ask").press("Enter")
        await page.get_by_role("button", name=t("nl", "ui.vesta.more")).first.click()
        await page.wait_for_function(
            "() => (document.querySelector('[role=dialog] iframe')?.getAttribute('src') || '').includes('#')"
        )
        element = await page.wait_for_selector("[role=dialog] iframe")
        part = (await element.get_attribute("src") or "").partition("#")[2]
        assert part
        frame = await element.content_frame()
        await frame.wait_for_selector(f"[id='{part}']")
        await frame.wait_for_function(
            f"() => Math.abs(document.getElementById({json.dumps(part)}).getBoundingClientRect().top) < 40"
        )
        await context.close()
        for width in (390, 1000):
            page = await _page(browser, url, "user-2", (width, 800))
            await page.get_by_role("button", name=t("nl", "ui.help"), exact=True).first.click()
            await page.wait_for_selector("[role=dialog] iframe")
            larger = page.get_by_role("button", name=t("nl", "ui.manual.larger"))
            apart = page.get_by_role("button", name=t("nl", "ui.manual.apart"))
            assert await larger.count() == (1 if width >= 900 else 0), width
            assert await apart.count() == (0 if width < 640 else 1), width
            sizes = await page.evaluate(
                """() => { const body = document.querySelector('[role=dialog] .body');
                const frame = body.querySelector('iframe').getBoundingClientRect();
                return [body.scrollWidth, body.clientWidth, frame.right, body.getBoundingClientRect().right]; }"""
            )
            assert sizes[0] <= sizes[1] + 1 and sizes[2] <= sizes[3] + 1, (width, sizes)
            await page.context.close()
        await browser.close()


async def test_help_is_the_manual_with_her_box_and_the_tour_has_its_own_button(
    house: tuple[str, HouseMind],  # noqa: F811
) -> None:
    """E-506, the administrator's wish of 12-11-2026: Help opened a list of short questions per
    level, with the tour and the manual as two more buttons. Now the Help button opens the
    manual at once, with "Vraag het Vesta" above it and no level tabs or questions; a button of
    its own beside Help starts the tour, and the tour lights up both buttons together."""
    url, hm = house
    t = hm.languages.text
    async with playwright.async_playwright() as p:
        browser = await _browser(p)
        page = await _page(browser, url, "user-2", (1280, 860))
        await page.get_by_role("button", name=t("nl", "ui.help"), exact=True).first.click()
        sheet = page.get_by_role("dialog")
        await sheet.locator("iframe").wait_for()
        assert await sheet.get_by_role("heading", name=t("nl", "ui.manual.title")).count() == 1
        assert await sheet.locator("#help-ask").is_visible()
        levels = [
            t("nl", f"levels.{key}") for key in ("carefree", "informed", "involved", "expert")
        ]
        for name in levels:
            assert await sheet.get_by_role("button", name=name, exact=True).count() == 0, name
        assert await sheet.locator("dl, dd").count() == 0
        assert await sheet.get_by_role("button", name=t("nl", "ui.tour.start")).count() == 0
        await page.keyboard.press("Escape")
        await page.wait_for_selector("[role=dialog]", state="detached")
        await page.get_by_role("button", name=t("nl", "ui.tour.start"), exact=True).first.click()
        tip = page.locator(".tip[role=dialog]")
        await tip.wait_for()
        title = t("nl", "ui.tour.help.title")
        for _ in range(12):
            if await page.locator("#tour-title").inner_text() == title:
                break
            await tip.get_by_role("button", name=t("nl", "ui.tour.next")).click()
        assert await page.locator("#tour-title").inner_text() == title
        await page.wait_for_timeout(500)  # after the scroll
        spot = await page.locator(".tour .spot").bounding_box()
        both = await page.locator("header .pair").bounding_box()
        assert spot and both and spot["width"] >= both["width"] + 15, (spot, both)
        assert both["width"] >= 2 * 40, both  # Help and the tour, side by side
        await page.context.close()
        await browser.close()


async def test_vestas_buttons_stay_in_her_window_on_a_phone(
    house: tuple[str, HouseMind],  # noqa: F811
) -> None:
    """M-20: Klaar, Nog even and Stop stood in one row of about 350 px and did not wrap on a
    phone. Now they wrap and stay inside her window."""
    url, hm = house
    async with playwright.async_playwright() as p:
        browser = await _browser(p)
        page = await _page(browser, url, "user-2", (320, 700))
        await page.locator("button.orb").first.click()
        await page.wait_for_selector(".window[role=dialog]")
        stop = page.locator(
            ".window .buttons button", has_text=hm.languages.text("nl", "ui.vesta.stop")
        )
        await stop.wait_for(timeout=5000)
        out = await page.evaluate(
            """() => { const w = document.querySelector('.window').getBoundingClientRect();
            return [...document.querySelectorAll('.window .buttons button')]
              .map(b => b.getBoundingClientRect())
              .filter(r => r.left < w.left - 0.5 || r.right > w.right + 0.5).length; }"""
        )
        assert out == 0
        await browser.close()


async def test_the_network_is_readable_on_a_phone(house: tuple[str, HouseMind]) -> None:  # noqa: F811
    """M-21: on a phone the room names in the network were about 6 px and a room a 14 px
    target. Now the names stay at least 12 px and a room is a 44 px target."""
    url, hm = house
    t = hm.languages.text
    async with playwright.async_playwright() as p:
        browser = await _browser(p)
        page = await _page(browser, url, "user-4", (390, 844))
        await page.get_by_role("button", name=t("nl", "ui.view.network")).first.click()
        await page.wait_for_selector(".net .room")
        # The rooms are named blocks since 'Het huis in beweging' (E-439): letters and target.
        text, target = await page.evaluate(
            """() => { const rooms = [...document.querySelectorAll('.net .room')];
            const text = Math.min(...rooms.map(r => parseFloat(getComputedStyle(r).fontSize)));
            const target = Math.min(...rooms.map(r => Math.min(r.getBoundingClientRect().width, r.getBoundingClientRect().height)));
            return [text, target]; }"""
        )
        assert text >= 12 and target >= 44, (text, target)
        await browser.close()


async def test_large_text_makes_letters_bigger_and_keeps_the_bars(
    house: tuple[str, HouseMind],  # noqa: F811
) -> None:
    """M-22: text size Large zoomed the whole page, so the bars could fall off the screen.
    Now only the letters grow, by an eighth, and the page still fits the phone."""
    url, _hm = house
    async with playwright.async_playwright() as p:
        browser = await _browser(p)
        sizes = []
        for choice in ("normal", "large"):
            device = json.dumps({"textSize": choice})
            page = await _page(browser, url, "user-2", (390, 844), **{"housemind.device": device})
            sizes.append(
                await page.evaluate(
                    """() => { const nav = document.querySelector('nav');
                    const r = nav ? nav.getBoundingClientRect() : {bottom: 0, right: 0};
                    return {font: parseFloat(getComputedStyle(document.body).fontSize),
                      zoom: getComputedStyle(document.body).zoom, bottom: r.bottom,
                      wide: document.documentElement.scrollWidth}; }"""
                )
            )
            await page.context.close()
        normal, large = sizes
        assert large["font"] == pytest.approx(normal["font"] * 1.125)
        assert large["zoom"] in ("1", "normal") and large["bottom"] <= 844
        assert large["wide"] <= 390
        await browser.close()


async def test_fields_dots_and_her_shape_stand_out(house: tuple[str, HouseMind]) -> None:  # noqa: F811
    """M-23: field borders, Vesta's shape on a light background and the level 1 dots were
    under 3:1 or told apart by colour alone. Now every field edge and her ring reach 3:1,
    and an empty room is a ring while a room with someone is a full dot."""
    url, hm = house
    await _person(hm, ADMIN, 4)
    t = hm.languages.text
    async with playwright.async_playwright() as p:
        browser = await _browser(p)
        for scheme in ("light", "dark"):
            page = await _page(browser, url, ADMIN, (1280, 900), scheme)
            await page.get_by_role(
                "button", name=t("nl", "ui.house_settings"), exact=True
            ).first.click()
            await page.wait_for_selector("[role=dialog] select")
            weak = await page.evaluate(
                """() => [...document.querySelectorAll('[role=dialog] input:not([type=checkbox]):not([type=radio]):not([type=range]), [role=dialog] select, [role=dialog] textarea')]
                .filter(f => f.getClientRects().length)
                .map(f => { const s = getComputedStyle(f);
                  const edge = parseFloat(s.borderTopWidth) ? f : f.parentElement;
                  const e = getComputedStyle(edge);
                  return [f.outerHTML.slice(0, 60), __ratio(e.borderTopColor, __bg(edge.parentElement))]; })
                .filter(([, r]) => r < 3)"""
            )
            assert not weak, (scheme, weak[:3])
            if scheme == "light":
                ring = await page.evaluate(
                    "() => getComputedStyle(document.querySelector('.core')).boxShadow"
                )
                assert "rgba(0, 0, 0, 0) 0px 0px 0px 1.5px" not in ring and "1.5px" in ring
            await page.context.close()
        page = await _page(browser, url, "user-1", (390, 844), "light")
        dots = await page.evaluate(
            """() => [...document.querySelectorAll('.dots span')].map(d => { const s = getComputedStyle(d);
            return [d.classList.contains('on'), s.backgroundColor, parseFloat(s.borderTopWidth)]; })"""
        )
        assert dots and all(width >= 1 for _, _, width in dots)
        assert all(bg in ("rgba(0, 0, 0, 0)", "transparent") for on, bg, _ in dots if not on)
        await browser.close()


@pytest.fixture
async def night(tmp_path: Path) -> AsyncIterator[str]:
    clock = SimulatedClock(datetime(2026, 10, 19, 23, 30, tzinfo=TZ).astimezone(UTC))
    demo = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await _person(hm, "user-2", 2)
    await hm.tick()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/"
    await runner.cleanup()
    await hm.stop()


async def test_the_wall_tablet_is_readable_at_night(night: str) -> None:
    """M-25: at night the wall tablet's muted text was 3.4 – 3.9:1, the whole screen dimmed.
    Now its text reaches 4.5:1 while the screen still gives little light."""
    async with playwright.async_playwright() as p:
        browser = await _browser(p)
        page = await _page(browser, night, "user-2", (1180, 820), **{"housemind.tablet": "kitchen"})
        await page.wait_for_selector(".tablet.night")
        await page.mouse.click(600, 400)  # a touch wakes it
        await page.wait_for_selector(".tablet.night:not(.asleep) .room")
        low = await page.evaluate(
            """() => [...document.querySelectorAll('.tablet .room, .tablet .weather, .tablet .exit')]
            .map(el => { const s = getComputedStyle(el);
              return [el.className, __ratio(__dim(el, s.color), __dim(el, __bg(el)))]; })
            .filter(([, r]) => r < 4.5)"""
        )
        assert not low, low
        await browser.close()


async def test_a_rule_and_a_moment_go_only_after_a_yes(house: tuple[str, HouseMind]) -> None:  # noqa: F811
    """L9: removing a house rule or a moment had no confirmation. Now the button asks first;
    Nee keeps it and gives the focus back."""
    url, hm = house
    t = hm.languages.text
    understood = hm.understand_rule("nooit de gordijnen in de woonkamer open voor 7:00", "nl")
    assert understood["ok"], understood
    await hm.save_rule(understood["rule"], ADMIN)
    async with playwright.async_playwright() as p:
        browser = await _browser(p)
        page = await _page(browser, url, "user-3", (1280, 900))
        await page.get_by_role("button", name=t("nl", "tabs.brain")).first.click()
        # Brein in tabs (E-398): the house rules are a tab of their own.
        await page.get_by_role("tab", name=t("nl", "ui.brain.tab.rules")).click()
        remove = page.get_by_role("button", name=t("nl", "ui.rules.remove")).first
        await remove.wait_for(timeout=5000)
        count = await page.locator(".rules li").count()
        await remove.click()
        await page.get_by_text(t("nl", "ui.rules.remove_confirm")).wait_for()
        await page.get_by_role("button", name=t("nl", "ui.no"), exact=True).click()
        assert await page.locator(".rules li").count() == count
        assert await page.evaluate("() => document.activeElement?.className.includes('remove')")
        await remove.click()
        await page.get_by_role("button", name=t("nl", "ui.yes"), exact=True).click()
        await page.wait_for_function(
            f"() => document.querySelectorAll('.rules li').length === {count - 1}"
        )
        await browser.close()


async def _export(page: Any, section: Any, t: Any) -> str:
    """Exporteren › CSV, and what the downloaded file says."""
    import asyncio

    async with page.expect_download() as info:
        await section.get_by_role("button", name=t("nl", "ui.list.export")).click()
        await section.get_by_role("menuitem").first.click()
    path = Path(await (await info.value).path())
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


async def _devices(page: Any, t: Any) -> Any:
    """Brein, its tab Apparaten (E-398), scrolled to the list."""
    await page.locator("nav button:visible", has_text=t("nl", "tabs.brain")).first.click()
    await page.get_by_role("tab", name=t("nl", "ui.brain.tab.devices")).click()
    section = page.locator("section.devices")
    await section.locator(".finder").scroll_into_view_if_needed()
    return section


async def test_a_long_list_counts_pages_exports_and_says_what_is_not_there(
    house: tuple[str, HouseMind],  # noqa: F811
) -> None:
    """E-325: a long list had no count, no pages, no export and no overview. Now it says
    '1–20 van …' and Volgende shows the next rows; rows per page stays after a reload; with
    Groeperen there are no pages; the export holds the chosen columns. Above it only what asks
    something of you (QA 3, schermen 10): "1 niet beschikbaar", and a tap sets that filter. On
    a phone there is no Exporteren and nothing runs out of the screen."""
    url, hm = house
    t = hm.languages.text
    assert hm.model is not None
    gone = sorted(e for e in hm.model.entities if e.startswith("light."))[0]
    await hm.ha.simulate(gone, "unavailable")
    async with playwright.async_playwright() as p:
        browser = await _browser(p)
        page = await _page(browser, url, "user-4", (1280, 900))
        devices = await _devices(page, t)
        await devices.locator(".finder select").first.select_option("none")
        count = devices.locator(".tools .count")
        total = len(hm.model.entities) if hm.model else 0
        await count.wait_for()
        assert (await count.inner_text()).startswith("1–20 van "), await count.inner_text()
        names = await devices.locator(":scope > ul li strong").all_inner_texts()
        assert names == sorted(names, key=str.casefold) or len(names) < 2, names[:5]
        await devices.get_by_role("button", name=t("nl", "ui.list.next")).click()
        assert (await count.inner_text()).startswith("21–"), await count.inner_text()
        await devices.locator(".pager select").select_option("50")
        await page.reload()
        devices = await _devices(page, t)
        assert (await devices.locator(".pager select").input_value()) == "50"
        # The export: name, the chosen columns and the id; one column less after Kolommen.
        text = await _export(page, devices, t)
        head = text.lstrip("\ufeff").splitlines()[0].split(";")
        assert head[0] == t("nl", "ui.list.name") and head[-1] == t("nl", "ui.list.id"), head
        room = t("nl", "ui.filter.title.room")
        assert room in head and t("nl", "ui.list.status") not in head  # no raw state by default
        await devices.get_by_role("button", name=t("nl", "ui.list.columns")).click()
        await devices.get_by_role("checkbox", name=room).uncheck()
        text = await _export(page, devices, t)
        assert room not in text.splitlines()[0]
        # "1 niet beschikbaar" sets that filter; the count says of how many.
        assert await devices.locator("details.overview").count() == 0
        await devices.get_by_role("button", name="1 niet beschikbaar").click()
        await page.wait_for_function(
            "() => document.querySelector('section.devices .tools .count').textContent.includes('(')"
        )
        # Grouped: the groups, no pages.
        await devices.locator(".finder select").first.select_option("room")
        assert await devices.locator(".pager").count() == 0
        await page.context.close()
        page = await _page(browser, url, "user-4", (390, 800))
        devices = await _devices(page, t)
        assert await devices.get_by_role("button", name=t("nl", "ui.list.export")).count() == 0
        assert await devices.get_by_role("button", name=t("nl", "ui.list.columns")).count() == 1
        sizes = await page.evaluate(
            "() => { const s = document.querySelector('section.devices'); return [s.scrollWidth, s.clientWidth]; }"
        )
        assert sizes[0] <= sizes[1] + 1, sizes
        assert total > 20
        await browser.close()
