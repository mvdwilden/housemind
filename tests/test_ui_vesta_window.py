"""Vesta's window with a question is one choice for the whole house, in House settings › Vesta
for an administrator on Expert (E-450, B-364), in a real browser. The administrator's wish
(02-11-2026): "de expert kan de Vesta popup uitzetten, toch? en dan geldt het voor iedereen".
It is no longer in My preferences; on Betrokken the administrator does not see it.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_vesta_window.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

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
ADMIN = "demo-user-1"  # the demo house's administrator

pytestmark = [
    pytest.mark.ui,
    pytest.mark.skipif(not DIST.exists(), reason="frontend not built"),
]

# Open a sheet by its round button and a group in it; then the text of that group, and a tick
# box whose words start with ``label`` (unticked when ``untick``).
SHEET = """async ([button, group, label, untick]) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  document.querySelector(`button[aria-label="${button}"]`).click();
  await wait(800);
  [...document.querySelectorAll(".sheet button.group")]
    .find((b) => b.querySelector("strong").textContent.trim() === group).click();
  await wait(500);
  const sheet = document.querySelector(".sheet");
  const box = [...sheet.querySelectorAll("label.check")]
    .find((l) => l.textContent.trim().startsWith(label));
  const found = Boolean(box);
  if (box && untick) { box.querySelector("input").click(); await wait(600); }
  const text = sheet.textContent;
  sheet.querySelector(".x")?.click();
  await wait(300);
  return {found, text};
}"""


@pytest.fixture
async def house(tmp_path: Path) -> AsyncIterator[tuple[str, HouseMind]]:
    clock = SimulatedClock(datetime(2026, 10, 27, 11, 0, tzinfo=UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    runner = web.AppRunner(create_app(hm, DIST, ingress_only=False, demo=True))
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield f"http://127.0.0.1:{runner.addresses[0][1]}/", hm
    await runner.cleanup()
    await hm.stop()


async def test_vesta_window_is_a_house_setting_for_the_expert_administrator(
    house: tuple[str, HouseMind],
) -> None:
    url, hm = house

    def t(key: str) -> str:
        return hm.languages.text("nl", key)

    name = hm._vesta_name or "Vesta"  # the group has her name
    label = hm.languages.text("nl", "ui.settings.vesta_window", name=name).split(",")[0]
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()

        async def page_for() -> Any:
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="nl-NL",
                extra_http_headers={"X-Remote-User-Id": ADMIN},
            )
            page = await context.new_page()
            await page.goto(url)
            await page.wait_for_selector(".orb", timeout=10000)
            return page

        page = await page_for()
        mine = await page.evaluate(
            SHEET, [t("ui.my_preferences"), t("ui.prefs.group.about_me"), label, False]
        )
        assert not mine["found"] and label not in mine["text"]  # not a choice of one's own
        house_wide = await page.evaluate(SHEET, [t("ui.house_settings"), name, label, True])
        assert house_wide["found"], house_wide["text"][:400]
        assert await hm.db.get_setting("vesta_window", True) is False  # off, for the house
        await page.context.close()

        # The administrator on Betrokken does not see the choice.
        await hm.db.execute("UPDATE people SET level = 3 WHERE user_id = ?", (ADMIN,))
        await hm._refresh_people()
        page = await page_for()
        involved = await page.evaluate(SHEET, [t("ui.house_settings"), name, label, False])
        assert not involved["found"]
        await page.context.close()
        await browser.close()
