"""One Bewaren for the whole Standen window (E-582), as the administrator of the real house
asked: "waarom worden de wachttijden wel meteen bewaard, terwijl je de stand instellingen wel
met Bewaren moet bewaren. Ik wil 1 knop met Bewaren voor alle instellingen op deze pagina."
From the user's side, over the websocket the window uses: what the screen sends at Bewaren, and
what is kept and said. What each test checks is its docstring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.server.web import create_app
from tests.helpers import next_message
from tests.test_ui_icons import ADMIN, SEEN

ROOT = Path(__file__).resolve().parents[1]
LAMP = "light.living_reading"
LEVELS = {
    "pct": [10, 30, 55, 90],
    "colour": [{"rhythm": True}, {"rhythm": True}, {"rhythm": True}, {"rhythm": True}],
    "dim": "keep",
    "rhythm": True,
}


@pytest.fixture
async def window(tmp_path: Path) -> AsyncIterator[tuple[HouseMind, Any]]:
    """The family home, an administrator on Expert, and the websocket of their screen."""
    clock = SimulatedClock(datetime(2026, 11, 2, 19, 30, tzinfo=UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        (ADMIN, ADMIN, 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": ADMIN})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await next_message(ws, "snapshot")
        await ws.send_json({"type": "levels", "do": "hold", "id": LAMP})
        yield hm, ws
        await ws.close()
    await hm.stop()


async def _save(ws: Any, **extra: Any) -> str:
    await ws.send_json({"type": "levels", "do": "save", "id": LAMP, "levels": LEVELS, **extra})
    return str((await next_message(ws, "saved"))["text"])


async def test_one_bewaren_keeps_the_standen_and_the_wait_together(
    window: tuple[HouseMind, Any],
) -> None:
    """The standen and four minutes before off, then Bewaren: both are kept, and the screen says
    everything in the window is."""
    hm, ws = window
    said = await _save(ws, wait=4)
    assert said == "Alles in dit venster is bewaard."
    assert hm.light_levels.own[LAMP].pct == (10, 30, 55, 90)
    assert hm.light_levels.off_wait(LAMP) == 4


async def test_without_a_changed_wait_the_wait_stays_as_it_was(
    window: tuple[HouseMind, Any],
) -> None:
    """Bewaren without touching the slider: the room keeps teaching the wait, as before."""
    hm, ws = window
    assert await hm.off_wait_save(LAMP, 7)
    await _save(ws)
    assert hm.light_levels.off_wait(LAMP) == 7
    await _save(ws, wait=None)  # Laat HouseMind het leren, then Bewaren
    assert hm.light_levels.off_wait(LAMP) is None


async def test_standen_that_cannot_be_kept_say_so_and_the_wait_is_kept(
    window: tuple[HouseMind, Any],
) -> None:
    """Standen that do not get brighter, with a new wait: the screen says the standen could not
    be kept and the wait was – never 'bewaard' for what was not."""
    hm, ws = window
    await ws.send_json(
        {
            "type": "levels",
            "do": "save",
            "id": LAMP,
            "levels": {**LEVELS, "pct": [50, 30, 55, 90]},
            "wait": 3,
        }
    )
    said = str((await next_message(ws, "saved"))["text"])
    assert said.startswith("De standen kon ik niet bewaren")
    assert said.endswith("De wachttijd is wel bewaard.")
    assert LAMP not in hm.light_levels.own
    assert hm.light_levels.off_wait(LAMP) == 3


async def test_a_wait_that_cannot_be_kept_says_so(window: tuple[HouseMind, Any]) -> None:
    """A wait outside 0 – 30 minutes: the standen are kept, and the screen says the wait is not."""
    hm, ws = window
    said = await _save(ws, wait=99)
    assert said == "De standen zijn bewaard, de wachttijd niet."
    assert hm.light_levels.own[LAMP].pct == (10, 30, 55, 90)
    assert hm.light_levels.off_wait(LAMP) is None


async def test_back_to_the_start_values_counts_only_at_bewaren(
    window: tuple[HouseMind, Any],
) -> None:
    """Terug naar startwaarden changes only the window; Bewaren then gives the lamp back to where
    its standen come from, so it no longer has its own."""
    hm, ws = window
    await _save(ws)
    assert LAMP in hm.light_levels.own
    start = {
        "pct": [15, 35, 60, 100],
        "colour": [{"kelvin": 2200}, {"rhythm": True}, {"rhythm": True}, {"rhythm": True}],
        "dim": "keep",
        "rhythm": True,
    }
    await ws.send_json({"type": "levels", "do": "save", "id": LAMP, "levels": start, "reset": True})
    assert (await next_message(ws, "saved"))["text"] == "Alles in dit venster is bewaard."
    assert LAMP not in hm.light_levels.own


async def test_the_window_says_where_its_values_and_colour_come_from(
    window: tuple[HouseMind, Any],
) -> None:
    """What the block at the top of the window says: the start values in one short line."""
    hm, _ws = window
    view = views._levels_view(hm, "nl", LAMP)
    assert view["source"] in ("start", "type", "group", "own")
    assert hm.languages.text("nl", "ui.stands.start_note") == (
        "Startwaarden: Gedimd 15% en warm · Normaal 35% · Helder 60% · Maximaal 100%."
    )
