"""What a switch drives, in Wat ik weet (E-580), as the administrator of the real house asked:
"mijn badkamer-smart switch ... is een schakelaar voor een lamp, maar ik heb geen keuze om te
zeggen dat het een ander apparaat is." A lamp from a label someone said yes to never showed in
Wat ik weet, so it could not be changed. From the user's side: the page, the websocket the
screen uses, and what the rules then treat as a lamp. What each test checks is its docstring."""

from __future__ import annotations

from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.knowledge.matcher import recognise
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.server.web import create_app
from tests.test_house_knowledge import ADMIN, _next, _read
from tests.test_house_knowledge import _house as _ai_house
from tests.test_labels import ROOT, _house

PLUG = "switch.reading_plug"  # its device has the label Lights
KEY = f"entity:{PLUG}:role"


def _row(hm: HouseMind) -> dict[str, Any] | None:
    page = views.house_knowledge(hm, "nl")
    return next((r for r in page["rows"] if r["id"] == KEY), None)


def _a_lamp(hm: HouseMind) -> bool:
    assert hm.gate is not None
    kind = recognise(hm.catalog, hm.model.entities[PLUG], hm.gate.state.roles.get(PLUG)).kind
    return kind is not None and kind.match.role == "light"


async def _said_lamp(tmp_path: Any) -> HouseMind:
    hm, _ha = await _house(tmp_path)
    await hm.db.execute(
        "INSERT OR REPLACE INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-1", "Emma", 3, hm.clock.now().isoformat()),
    )  # an administrator on Betrokken: Wat ik weet is theirs
    await hm.answer_label(f"label_lamp:{PLUG}", True)
    assert _a_lamp(hm)
    return hm


async def test_a_lamp_from_a_label_shows_in_wat_ik_weet_as_your_own_answer(tmp_path: Any) -> None:
    """A yes to 'Is het een lamp?' for a switch with the label Lights: Wat ik weet shows it, as
    the administrator's own answer, with 'Iets anders…' to choose what it drives instead – a
    lamp, the start button of a device, the power switch of a cooking device, or another
    device."""
    hm = await _said_lamp(tmp_path)
    row = _row(hm)
    assert row is not None
    assert (row["state"], row["source"], row["what"]) == ("yes", "person", "een lamp")
    page = views.house_knowledge(hm, "nl")
    assert [c["id"] for c in page["switchRoles"]] == [
        "light",
        "appliance_start",
        "cooking_off",
        "other",
    ]
    assert page["switchRoles"][-1]["name"] == "een ander apparaat"
    await hm.stop()


async def test_another_device_is_no_lamp_also_after_a_restart(tmp_path: Any) -> None:
    """'Iets anders…' → 'een ander apparaat', over the websocket as the screen sends it: from
    then on it is an ordinary switch, the room's light rules leave it out, the label does not
    make it a lamp again – also not after a restart – and the page says so."""
    hm = await _said_lamp(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ADMIN)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await _next(ws, "snapshot")
        await ws.send_json({"type": "knowledge", "do": "role", "id": KEY, "role": "other"})
        await _next(ws, "snapshot")
        await ws.close()
    assert not _a_lamp(hm)
    area = hm.model.area_of(PLUG)
    assert area is not None and hm.skills is not None
    assert PLUG not in [e.entity_id for e in hm.skills.units_of("light_on_entry", area.id)]
    row = _row(hm)
    assert row is not None and row["what"] == "een ander apparaat"
    assert row["note"] == "Geen lamp: ik behandel het als een gewone schakelaar."
    await hm._apply_label_roles()  # what a rebuild of the house does
    assert not _a_lamp(hm)
    await hm.stop()
    again = HouseMind(hm.clock, hm.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert again.gate is not None and again.gate.state.roles.get(PLUG) is None
    row = _row(again)
    assert row is not None and row["what"] == "een ander apparaat"
    await again.stop()


async def test_back_from_another_device_is_the_label_s_lamp_again(tmp_path: Any) -> None:
    """Terug naar te bevestigen after 'een ander apparaat': back to what it was before that
    choice, the lamp from the label the administrator said yes to."""
    hm = await _said_lamp(tmp_path)
    assert await hm.answer_knowledge(KEY, True, "other")
    assert not _a_lamp(hm)
    assert await hm.reopen_knowledge(KEY)
    assert _a_lamp(hm)
    row = _row(hm)
    assert row is not None and row["what"] == "een lamp"
    await hm.stop()


async def test_back_from_the_label_s_yes_asks_the_label_s_question_again(tmp_path: Any) -> None:
    """Terug naar te bevestigen on the lamp itself: the yes is taken back, it is no lamp until
    an answer, and 'Is het een lamp?' may be asked again."""
    hm = await _said_lamp(tmp_path)
    assert await hm.reopen_knowledge(KEY)
    assert not _a_lamp(hm)
    assert _row(hm) is None
    assert hm.world is not None
    await hm._ask_labels(hm.world.snapshot(hm.clock.now()))
    assert f"label_lamp:{PLUG}" in hm._raised
    await hm.stop()


async def test_a_lamp_said_yes_to_before_this_version_shows_too(tmp_path: Any) -> None:
    """A house that said yes to a label's lamp before this version: after the update the switch
    is in Wat ik weet as well, as the administrator's answer, and still a lamp."""
    hm, _ha = await _house(tmp_path)
    await hm.db.set_setting("label_roles", {PLUG: "light"})
    await hm.db.set_setting("label_answers", {f"label_lamp:{PLUG}": True})
    await hm.stop()
    again = HouseMind(hm.clock, hm.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert _a_lamp(again)
    row = _row(again)
    assert row is not None and (row["state"], row["source"]) == ("yes", "person")
    await again.stop()


async def test_only_a_switch_gets_a_choice_of_what_it_drives(tmp_path: Any) -> None:
    """'Iets anders…' with a switch's choice for something that is no switch, or a choice that
    is not on the list: nothing changes."""
    hm = await _said_lamp(tmp_path)
    assert not await hm.answer_knowledge(KEY, True, "heater")
    light = next(e for e in hm.model.entities if e.startswith("light."))
    assert not await hm.answer_knowledge(f"entity:{light}:role", True, "other")
    assert _a_lamp(hm)
    await hm.stop()


async def test_ai_hears_that_a_smart_switch_in_a_room_most_often_powers_its_lamp(
    tmp_path: Any,
) -> None:
    """What AI is asked about a switch says what a house mostly has: a smart switch or relay in a
    room powers that room's lamp, sometimes another device – so its guess starts there."""
    hm, _clock, _ha = await _ai_house(tmp_path)
    fake = await _read(hm)
    asked = [system for system, sent, _ in fake.sent if '"switches"' in sent]
    assert asked
    assert "most often powers the lamp of that room" in asked[0]
    assert "a fan, a heater or a pump" in asked[0]
    await hm.stop()
