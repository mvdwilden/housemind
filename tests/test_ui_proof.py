"""Proof and one yes, in a real browser (E-460, B-375). The administrator's question (06-11-2026):
"hoe zou HouseMind sneller over kunnen gaan naar voorstellen en dan naar autonoom, maar dan ook
echt naar autonoom?" So on screen: Klaar voor autonomie? shows how often it went well, and says
when HouseMind only watches; Wacht op jou has one question with Ja, voortaan zelf, Nog niet and
Nooit; a yes takes the automation over, and the row says HouseMind does it now.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_proof.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from housemind import __version__
from housemind.brain.learning import Result
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_automations import HALL, HALL_CONFIG

playwright = pytest.importorskip("playwright.async_api")

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
SEEN = f'{{"onboarded": true, "tour": [1, 2, 3, 4], "seen_version": "{__version__}"}}'
ADMIN = "demo-user-1"
SHOTS = os.environ.get("HOUSEMIND_SHOTS")  # a folder for screenshots, to look at by hand
CONTEXT = "workday|19:30"

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

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

WAITING = """() => [...document.querySelectorAll("main section.waiting li[data-notice]")]
  .map((li) => ({
    id: Number(li.dataset.notice),
    text: li.querySelector(".text").textContent.trim(),
    buttons: [...li.querySelectorAll(".actions button")].map((b) => b.textContent.trim()),
  }))"""

TAP = """async ([id, label]) => {
  const li = document.querySelector(`section.waiting li[data-notice="${id}"]`);
  [...li.querySelectorAll(".actions button")].find((b) => b.textContent.trim() === label).click();
  await new Promise((r) => setTimeout(r, 1500));
}"""

# The rows of the list: what it is, its step, its bar and what it says.
# Each rule is one row (B-421); everything about it is in the detail beside the list (B-422).
# ``text`` is the row and its detail together, ``bar`` the number in its ring.
ROWS = """async () => {
  const ready = document.querySelector('[data-section="readiness"]');
  const out = [];
  for (const li of ready.querySelectorAll("section.list li.skill")) {
    const row = li.querySelector("button.row");
    let detail = "";
    if (row) {
      row.click();
      // The detail is this row's once the row is the chosen one (aria-current), then one frame.
      for (let i = 0; i < 50 && row.getAttribute("aria-current") !== "true"; i++) {
        await new Promise((r) => setTimeout(r, 20));
      }
      await new Promise((r) => requestAnimationFrame(() => r(null)));
      const pane = ready.querySelector("aside.pane") ?? document.querySelector("[role=dialog]");
      detail = pane ? pane.textContent : "";
    }
    out.push({
      text: (li.textContent + " " + detail).replace(/\\s+/g, " "),
      bar: li.querySelector(".ring-text")?.textContent.trim().replace("–", "") || null,
      pill: li.querySelector(".pill, .step-pill, [class*=pill]")?.textContent.trim() ?? null,
    });
  }
  return out;
}"""


def _prove(hm: HouseMind, pair: tuple[str, str], hits: int = 14, days: float = 9.0) -> None:
    assert hm.learning is not None
    now = hm.clock.now()
    for _ in range(hits):
        hm.learning._add(*pair, CONTEXT, Result.HIT, now)
    hm.learning.first_seen[pair] = now - timedelta(days=days)
    hm.learning.steps[pair] = Step.PROPOSE


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind, FakeHomeAssistant]]:
    clock = SimulatedClock(datetime(2026, 11, 2, 15, 0, tzinfo=UTC))
    demo = load_demo_house("family_home", clock, "nl")
    await demo.ha.add_automation(HALL, str(HALL_CONFIG["alias"]), HALL_CONFIG)
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    await hm.db.set_setting("vesta_window", False)  # her window would lie over the page
    await hm._sync_automations()
    _prove(hm, ("light_on_entry", "hall"))
    _prove(hm, ("light_on_entry", "kitchen"))
    assert hm.learning is not None
    hm.learning.steps[("light_off_leave", "kitchen")] = Step.PROPOSE
    await clock.advance(16 * 60)
    await hm.tick()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm, demo.ha
    await runner.cleanup()
    await hm.stop()


async def test_one_question_one_yes_and_the_card_tells_the_truth(
    house: tuple[str, HouseMind, FakeHomeAssistant],
) -> None:
    url, hm, ha = house

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="nl-NL",
            extra_http_headers={"X-Remote-User-Id": ADMIN},
        )
        page: Any = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector(".orb", timeout=10000)

        # The card: the hall is held by the automation and says so; the bar is how often it
        # went well, not the share of yes, which would be 0.
        await page.evaluate(TAB, t("tabs.brain"))
        await page.evaluate(SUBTAB, t("ui.ready.title"))
        rows = await page.evaluate(ROWS)
        hall = next(r for r in rows if "Gang" in r["text"] and "binnenkomen" in r["text"])
        assert hall["bar"] == "100", hall
        assert t("ui.ready.watching") in hall["text"], hall
        assert "Hall light at motion" in hall["text"], hall
        kitchen = next(r for r in rows if "Keuken" in r["text"] and "binnenkomen" in r["text"])
        assert kitchen["bar"] == "100", kitchen
        assert t("steps.propose") in kitchen["text"], kitchen
        if SHOTS:
            await page.locator('[data-section="readiness"] section.list').screenshot(
                path=f"{SHOTS}/proof-card.png"
            )

        # Wacht op jou: one question each, with the three answers.
        await page.evaluate(TAB, t("tabs.logbook"))
        waiting = await page.evaluate(WAITING)
        answers = [
            t("notify.answer.from_now_yes"),
            t("notify.answer.later"),
            t("notify.answer.not_ever"),
        ]
        automation = next(w for w in waiting if "Hall light at motion" in w["text"])
        plain = next(w for w in waiting if "in de Keuken" in w["text"])
        assert automation["buttons"] == answers, automation
        assert plain["buttons"] == answers, plain
        if SHOTS:
            await page.screenshot(path=f"{SHOTS}/proof-waiting.png", full_page=False)

        # A yes: the automation goes off in Home Assistant, the question goes, the row says it.
        await page.evaluate(TAP, [automation["id"], answers[0]])
        assert ha.states[HALL].state == "off"
        await page.evaluate(TAP, [plain["id"], answers[0]])
        left = await page.evaluate(WAITING)
        assert not {automation["id"], plain["id"]} & {w["id"] for w in left}, left
        await page.evaluate(TAB, t("tabs.brain"))
        await page.evaluate(SUBTAB, t("ui.ready.title"))
        rows = await page.evaluate(ROWS)
        hall = next(r for r in rows if "Gang" in r["text"] and "binnenkomen" in r["text"])
        assert t("steps.autonomous") in hall["text"], hall
        if SHOTS:
            await page.screenshot(path=f"{SHOTS}/proof-after.png", full_page=True)
        await context.close()
        await browser.close()
