"""Each kind of group in the demo, in a real browser (E-458). The administrator's wish
(04-11-2026): "kan je in de demo, naast een groep Zigbee2MQTT, ook andere type/merken groepen
neerzetten? zoals Philips Hue, ZHA, etc? … in mijn huidige HA-installatie heb ik enkel
Zigbee2MQTT, maar ik wil in de demo dit wel kunnen zien". In Brein › Apparaten of the extended
demo house each kind stands with its own name, says whether it switches its lamps in one go,
and – when it only gathers – why.

Runs only on request, like tests/test_ui.py: ``uv run pytest -m ui tests/test_ui_group_kinds.py``
(needs a built frontend and ``uv run playwright install chromium`` once)."""

from __future__ import annotations

from typing import Any

import pytest

from housemind.runtime import HouseMind
from tests.test_ui_group_children import house  # noqa: F401  (the fixture)

playwright = pytest.importorskip("playwright.async_api")

pytestmark = [pytest.mark.ui]

# Open Brein › Apparaten, every room, and read each group's line.
READ = """async ([brain, devices]) => {
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const button = (text) => [...document.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === text);
  button(brain).click(); await wait(300);
  button(devices).click(); await wait(500);
  for (const room of document.querySelectorAll(".devices details.group")) {
    if (!room.open) room.querySelector("summary").click();
  }
  await wait(300);
  return [...document.querySelectorAll("details.grp > summary")]
    .map((s) => [s.querySelector(".gname").textContent, s.textContent]);
}"""


async def test_each_kind_of_group_shows_in_devices(house: tuple[str, HouseMind]) -> None:  # noqa: F811
    url, hm = house
    t = hm.languages.text
    words = [t("nl", "tabs.brain"), t("nl", "ui.brain.tab.devices")]
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="nl-NL",
            extra_http_headers={"X-Remote-User-Id": "user-4"},
        )
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector(".orb", timeout=10000)
        lines: list[list[str]] = await page.evaluate(READ, words)
        await browser.close()
    text = " | ".join(x[1] for x in lines)
    m = hm.model

    def line(group: str) -> str:
        found = [x[1] for x in lines if x[0] == m.entities[group].name]
        assert found, (group, text)
        return found[0]

    kinds: dict[str, Any] = {g: m.group_source[g] for g in m.groups}
    for kind, name in (
        ("zigbee2mqtt", "Zigbee-groep (Zigbee2MQTT)"),
        ("zha", "Zigbee-groep (ZHA)"),
        ("hue", "Hue-ruimte of -zone"),
        ("deconz", "deCONZ-groep"),
        ("ha", "HA-groep"),
    ):
        assert any(name in line(g) for g, k in kinds.items() if k == kind), (kind, text)
    for group in m.steering:
        assert "schakelt ze in één keer" in line(group), text
    over = next(g for g, k in kinds.items() if k == "ha" and g not in m.group_area)
    assert "verzamelt alleen – over meerdere ruimtes" in line(over), text
