"""Leren en stappen kort and Wat ik weet in Brein, in a real browser (E-523 – E-525, design 88).
An administrator on Expert in the family home, with a room AI called a bedroom and two things of
one device confirmed: Brein has the tab Wat ik weet with what waits on it, "van AI" in the AI's
violet; the four tiles show
their groups; a Klopt counts; a confirmed room gets another kind and goes back to Te bevestigen
with what HouseMind thought itself; the things of one device fold under it. Huisinstellingen ›
Leren en stappen keeps learning, the history and the parts, with the way to Brein; an earlier
day is at Opnieuw beginnen; the thresholds are only in the settings file (QA 3). On a phone
the line opens in a sheet, the buttons one under the other, and nothing scrolls sideways.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_knowledge.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Device, Entity, State
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_ui_icons import ADMIN, CLICK, DIST, SEEN

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
SHOTS = os.environ.get("HOUSEMIND_SHOTS")
ROOM = "area:music:role"
SETTING = {"function": "setting"}

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

TEXT = """(selector) => [...document.querySelectorAll(selector)]
  .filter((e) => e.offsetParent)
  .map((e) => e.textContent.replace(/\\s+/g, " ").trim())"""
SECTIONS = """() => [...document.querySelectorAll(".sheet section[data-section]")]
  .filter((s) => s.offsetParent).map((s) => s.dataset.section)"""
WIDTHS = """() => [...document.querySelectorAll("[role=dialog] .acts button")]
  .map((b) => Math.round(b.getBoundingClientRect().width))"""
WIDE = """() => document.documentElement.scrollWidth <= window.innerWidth"""
# "van AI" in the AI's violet, the same as in the network (E-524): its colour and --ai's.
VIOLET = """() => {
  const probe = document.createElement("span");
  probe.style.color = "var(--ai)";
  document.body.append(probe);
  const ai = getComputedStyle(probe).color;
  probe.remove();
  const chips = [...document.querySelectorAll(".from-ai")].filter((e) => e.offsetParent);
  return [chips.length, chips.every((c) => getComputedStyle(c).color === ai), ai];
}"""
K = "[data-section=knowledge]"
ACTS = f"{K} .pane .acts button"
# Choose in the select of "Iets anders…" the option with this text.
PICK = """async (name) => {
  const select = [...document.querySelectorAll(".detail select")].find((s) => s.offsetParent);
  const option = [...select.options].find((o) => o.textContent.trim() === name);
  select.value = option.value;
  select.dispatchEvent(new Event("change", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 1200));
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind, list[str]]]:
    clock = SimulatedClock(datetime(2026, 11, 14, 10, 0, tzinfo=UTC))
    demo = load_demo_house("family_home", clock, "nl")
    demo.ha.model.areas["music"] = Area("music", "Muziekkamer", None)
    demo.ha.model.devices["device.oven"] = Device("device.oven", "Oven", "kitchen", "ovens")
    for n, (entity_id, name) in enumerate(
        (
            ("switch.oven_child_lock", "Kinderslot"),
            ("switch.oven_fast_preheat", "Snel voorverwarmen"),
        )
    ):
        oven = Entity(entity_id, f"o{n}", name, device_id="device.oven", platform="ovens")
        await demo.ha.add_entity(oven, State(entity_id, "off", {}, clock.now()))
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    now = clock.now()
    # A room AI thinks is a bedroom, waiting for a Klopt.
    value = {"role": "bedroom", "why": "Een rustige ruimte."}
    await hm.knowledge.suggest("area", "music", "role", value, 0.8, now, None)
    # Two things of one device, confirmed: they fold under the device.
    things = ["switch.oven_child_lock", "switch.oven_fast_preheat"]
    for entity_id in things:
        await hm.knowledge.suggest("entity", entity_id, "function", SETTING, 0.9, now, None)
        assert await hm.answer_knowledge(f"entity:{entity_id}:function", True)
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm, ["Oven", *things]
    await runner.cleanup()
    await hm.stop()


async def _shot(page: Any, name: str) -> None:
    if SHOTS:
        await page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


async def test_what_i_know_in_brein_and_everything_can_go_back(
    house: tuple[str, HouseMind, list[str]],
) -> None:
    url, hm, (device, *_things) = house

    def t(key: str, **facts: Any) -> str:
        return hm.languages.text("nl", key, **facts)

    async def tiles(page: Any) -> list[str]:
        return list(await page.evaluate(TEXT, f"{K} .tiles .tile"))

    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1366, "height": 1000},
            locale="nl-NL",
            extra_http_headers={"X-Remote-User-Id": ADMIN},
        )
        page: Any = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector(".orb", timeout=10000)
        await page.evaluate(CLICK, ["nav button", t("tabs.brain")])

        # The tab, in sight after Ritme, with what waits on it.
        tabs = await page.evaluate(TEXT, "[role=tablist] [role=tab]")
        mine = next(x for x in tabs if x.startswith(t("ui.brain.tab.knowledge")))
        assert mine == f"{t('ui.brain.tab.knowledge')}1"
        assert (
            tabs.index(mine)
            == tabs.index(next(x for x in tabs if x.startswith(t("ui.brain.tab.rhythm")))) + 1
        )
        await page.evaluate(CLICK, ["[role=tab]", t("ui.brain.tab.knowledge")])
        await page.wait_for_selector(f"{K} .tiles .tile")
        assert await tiles(page) == [
            f"1{t('ui.knowledge.tile.open')}",
            f"2{t('ui.knowledge.tile.yes')}",
            f"0{t('ui.knowledge.tile.no')}",
            f"{len(await _scenes(hm))}{t('ui.knowledge.tile.scenes')}",
        ]
        # What waits shows first; beside it what HouseMind thinks and its three buttons.
        rows = await page.evaluate(TEXT, f"{K} .rows .row .what")
        assert rows == ["Muziekkamer"]
        pane = " ".join(await page.evaluate(TEXT, f"{K} .pane"))
        assert "Slaapkamer" in pane and t("ui.knowledge.source.ai") in pane and "80%" in pane
        buttons = await page.evaluate(TEXT, ACTS)
        assert buttons == [t("ui.knowledge.yes"), t("ui.knowledge.no"), t("ui.knowledge.act.other")]
        chips, violet, colour = await page.evaluate(VIOLET)
        assert chips >= 2 and violet and colour not in ("rgb(0, 0, 0)", ""), colour
        await _shot(page, "weet-1-te-bevestigen")
        await page.evaluate(CLICK, [ACTS, t("ui.knowledge.yes")])
        assert hm.area_roles["music"] == "bedroom"
        assert t("ui.knowledge.done.yes") in " ".join(await page.evaluate(TEXT, f"{K} .pane"))
        assert (await tiles(page))[:2] == [
            f"0{t('ui.knowledge.tile.open')}",
            f"3{t('ui.knowledge.tile.yes')}",
        ]

        # Bevestigd: the room can get another kind, with what HouseMind thought itself.
        await page.evaluate(CLICK, [f"{K} .tile", f"3{t('ui.knowledge.tile.yes')}"])
        await page.evaluate(CLICK, [f"{K} .row", "Muziekkamer"])
        buttons = await page.evaluate(TEXT, ACTS)
        assert buttons == [
            t("ui.knowledge.act.other"),
            t("ui.knowledge.act.no_after_all"),
            t("ui.knowledge.act.back"),
        ]
        await page.evaluate(CLICK, [ACTS, t("ui.knowledge.act.other")])
        await page.evaluate(PICK, t("ui.knowledge.room.office"))
        assert hm.area_roles["music"] == "office"
        pane = " ".join(await page.evaluate(TEXT, f"{K} .pane"))
        assert "Werkkamer" in pane and f"{t('ui.knowledge.k.was')}: Slaapkamer" in pane
        await _shot(page, "weet-2-iets-anders")
        # Back to Te bevestigen: AI's bedroom waits again; the room is what its name says.
        await page.evaluate(CLICK, [ACTS, t("ui.knowledge.act.back")])
        assert hm.area_roles["music"] == "other"
        assert (await tiles(page))[0] == f"1{t('ui.knowledge.tile.open')}"
        assert t("ui.knowledge.done.back") in " ".join(await page.evaluate(TEXT, f"{K} .pane"))

        # The two things of one device fold under it, with what is in it.
        await page.evaluate(CLICK, [f"{K} .tile", f"2{t('ui.knowledge.tile.yes')}"])
        folds = await page.evaluate(TEXT, f"{K} .fold > summary")
        assert any(
            f.startswith(device) and t("ui.knowledge.things", count=2) in f for f in folds
        ), folds

        # Huisinstellingen › Leren en stappen: what you set, and the way to Brein.
        await page.get_by_role("button", name=t("ui.house_settings"), exact=True).first.click()
        await page.wait_for_selector(".sheet button.group")
        await page.evaluate(CLICK, [".sheet button.group", t("ui.settings.group.learning")])
        sections = await page.evaluate(SECTIONS)
        assert sections == ["learning", "history", "max_step"]
        await _shot(page, "weet-3-venster")
        await page.evaluate(CLICK, [".sheet .to-brain button", t("ui.knowledge.title")])
        await page.wait_for_selector(f"{K} .tiles .tile")
        # Een eerdere dag terugzetten is the first of Opnieuw beginnen (Expert).
        await page.get_by_role("button", name=t("ui.house_settings"), exact=True).first.click()
        await page.wait_for_selector(".sheet button.group")
        await page.evaluate(CLICK, [".sheet button.group", t("ui.settings.group.restart")])
        sections = await page.evaluate(SECTIONS)
        assert sections[0] == "snapshots"
        await page.keyboard.press("Escape")

        # The thresholds are no screen any more, only in the settings file (QA 3).
        await page.evaluate(CLICK, ["[role=tab]", t("ui.brain.tab.readiness")])
        await page.wait_for_selector('[data-section="readiness"] > .tiles > .tile')
        await page.evaluate(
            CLICK, ['[data-section="readiness"] > .tiles > .tile', t("ui.domain.lighting")]
        )
        assert not await page.evaluate(TEXT, "[data-section=readiness] .chosen .limits summary")

        # On a phone: the line in a sheet, the buttons under each other, nothing sideways.
        phone = await context.new_page()
        await phone.set_viewport_size({"width": 390, "height": 844})
        await phone.goto(url)
        await phone.wait_for_selector(".orb", timeout=10000)
        await phone.evaluate(CLICK, ["nav button", t("tabs_short.brain")])
        await phone.evaluate(CLICK, ["[role=tab]", t("ui.brain.tab.knowledge")])
        await phone.wait_for_selector(f"{K} .tiles .tile")
        await phone.evaluate(CLICK, [f"{K} .row", "Muziekkamer"])
        await phone.wait_for_selector("[role=dialog] .acts button")
        widths = await phone.evaluate(WIDTHS)
        assert len(set(widths)) == 1 and widths[0] > 250
        assert await phone.evaluate(WIDE)
        await _shot(phone, "weet-4-telefoon")
        await browser.close()


async def _scenes(hm: HouseMind) -> list[str]:
    return [
        e.entity_id
        for e in hm.model.entities.values()
        if e.domain in ("scene", "script") and not e.hidden and not e.disabled
    ]
