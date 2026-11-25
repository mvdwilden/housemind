"""Quicker code from the code review of 24-11-2026 (code QA 3, punten 7, 10, 12, 14, 15, 16,
18) – what a screen, a wall tablet and a person coming home notice: less to send, less to ask
Home Assistant, the same house. Each test fails without the change; what it checks is its
docstring."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity
from housemind.demo.houses import load_demo_house
from housemind.ha.websocket import WebSocketHomeAssistant
from housemind.i18n.catalog import Languages
from housemind.runtime import HouseMind, Paths
from housemind.server import views

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Amsterdam")


async def _house(tmp_path: Path) -> HouseMind:
    clock = SimulatedClock(datetime(2026, 10, 19, 19, 0, tzinfo=TZ).astimezone(UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path / "data"))
    await hm.start(autorun=False)
    return hm


async def test_a_screen_gets_one_list_of_texts_without_english_beside_it(tmp_path: Path) -> None:
    """The first picture of a screen carries its own language, complete, and no second list in
    English (275 KB less on every start of a screen and every wall tablet that reconnects)."""
    hm = await _house(tmp_path)
    viewer = views.Viewer(user_id="demo-user-1", name=None, level=4, is_admin=True)
    snap = await views.snapshot(hm, viewer)
    assert snap["fallback"] == {}
    assert snap["texts"]["ui.pause"] == "Pauze"
    assert set(hm.languages.languages["en"]) <= set(snap["texts"])
    await hm.stop()


def test_an_own_language_with_gaps_gets_them_filled_in_english() -> None:
    """A language file of one's own that misses texts still shows every text on a screen: the
    gaps in English, the rest in that language."""
    languages = Languages()
    languages.add("en", {"ui.pause": "Pause", "ui.save": "Save"})
    languages.add("xx", {"ui.pause": "Paus"})
    assert languages.complete("xx") == {"ui.pause": "Paus", "ui.save": "Save"}
    assert languages.text("xx", "ui.save") == "Save"


async def test_a_room_names_its_devices_once(tmp_path: Path) -> None:
    """A room's devices go to the screen once; its groups per device name them by id."""
    hm = await _house(tmp_path)
    room = next(r for r in views.rooms(hm, "nl", 4) if r["devices"])
    ids = {d["id"] for d in room["devices"]}
    assert room["groups"] and all(isinstance(i, str) for g in room["groups"] for i in g["items"])
    assert {i for g in room["groups"] for i in g["items"]} == ids
    await hm.stop()


def test_what_kind_an_entity_is_is_worked_out_once() -> None:
    """An entity knows its kind ("light", "sensor") from the start: not worked out again at
    each of the many times a round asks; a renamed copy knows its new kind."""
    lamp = Entity("light.hall", "r-1", "Hal")
    assert "domain" in {f.name for f in dataclasses.fields(Entity)}
    assert lamp.domain == "light"
    assert dataclasses.replace(lamp, entity_id="switch.hall").domain == "switch"
    assert lamp == Entity("light.hall", "r-1", "Hal")


async def test_home_assistants_actions_are_asked_once_a_minute() -> None:
    """Someone coming home makes HouseMind look which phones can get a message. It asks Home
    Assistant for its whole list of actions at most once a minute, and again after a restart
    of Home Assistant."""
    ha = WebSocketHomeAssistant(SimulatedClock(datetime(2026, 11, 25, tzinfo=UTC)), "token")
    asked: list[dict[str, Any]] = []

    async def command(message: dict[str, Any]) -> Any:
        asked.append(message)
        return {"notify": {"mobile_app_phone": {}}}

    ha._command = command  # type: ignore[method-assign]
    for _ in range(3):
        assert "mobile_app_phone" in (await ha.available_actions())["notify"]
    assert len(asked) == 1
    ha._actions = None  # what a new connection does
    await ha.available_actions()
    assert len(asked) == 2


async def test_reaction_times_are_kept_at_the_round_not_at_each_action(tmp_path: Path) -> None:
    """After an action HouseMind writes nothing extra to its database for the reaction time;
    it keeps the times at its next round, so they still survive a restart."""
    hm = await _house(tmp_path)
    hm.status.reactions.append(12.5)
    hm._reactions_unsaved = True
    assert await hm.db.get_setting("reactions") in (None, [])
    await hm.tick()
    assert await hm.db.get_setting("reactions") == [12.5]
    await hm.stop()


async def test_who_is_home_has_one_answer(tmp_path: Path) -> None:
    """The line under Vesta counts who is home with the world's own count – one way to know."""
    hm = await _house(tmp_path)
    assert not hasattr(hm, "people_home")
    assert hm.world is not None and isinstance(hm.world.residents_home(), int)
    await hm.stop()
