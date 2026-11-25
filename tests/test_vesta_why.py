"""'Let op', en waarom (E-389, chapter 40), from the user's side: the line under Vesta's word
says why she has her colour – red, orange or amber – on every tab, for whom it concerns, and
goes back to the calm line once it is handled. Each test fails without it; what it checks is
in its docstring."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.notify import Kind
from housemind.server.web import create_app
from housemind.world.house_mode import HouseMode
from tests.test_vesta import _house, _raise
from tests.test_vesta_asks import _proposal

USER = {"X-Remote-User-Id": "demo-user-1"}


async def _snapshot(ws: Any) -> dict[str, Any]:
    while True:
        message = await ws.receive_json(timeout=5)
        if message["type"] == "snapshot":
            return dict(message["snapshot"])


async def _screen(client: TestClient, **hello: Any) -> tuple[Any, dict[str, Any]]:
    ws = await client.ws_connect("/api/ws", headers=USER)
    await ws.send_json({"type": "hello", "browserLanguage": "nl", **hello})
    return ws, await _snapshot(ws)


async def test_orange_says_what_waits_and_goes_back_after_later(tmp_path: Path) -> None:
    """ "Let op" stood above "20,8 °C binnen · 1 persoon thuis", and nothing on the screen
    said what waited. Now the line says it – the first sentence of that message, in plain
    names – and after Later the calm line comes back."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    hm.activated = True
    await _raise(
        hm, Kind.IMPORTANT, "battery:x", "notify.maintenance.battery", {"device": "Gang rookmelder"}
    )
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, snap = await _screen(client)
        assert snap["orb"] == "attention"
        why = snap["why"]
        assert why is not None and "rookmelder in de gang" in why["text"].lower(), why
        assert "Gang rookmelder" not in why["text"] and "binnen" not in why["text"]
        assert why["text"].count(".") <= 1  # one sentence, for one line
        notice = why["notification"]
        await ws.send_json({"type": "notification", "id": notice, "action": "later_1h"})
        while snap.get("why") is not None:
            snap = await _snapshot(ws)
        assert snap["orb"] != "attention" and snap["why"] is None
        await ws.close()
    await hm.stop()


async def test_amber_says_her_question(tmp_path: Path) -> None:
    """Amber ("Ik leer iets nieuws") said nothing about which question she had. Now the line
    is her question, without its reason, and points to it."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    hm.activated = True
    proposal = await _proposal(
        hm, "light_off_leave", "light.living_reading", "living", "light.turn_off"
    )
    assert hm.world is not None
    hm.world.modes.set_manual(HouseMode.HOME, hm.clock.now())  # awake: amber, not asleep
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, snap = await _screen(client)
        assert hm.orb_state(thinking=False, user_id="demo-user-1") == "learning"
        assert snap["why"]["text"] == "Zal ik de leeslamp in de woonkamer uitdoen?"
        assert snap["why"]["proposal"] == proposal
        await ws.close()
    await hm.stop()


async def test_only_for_whom_it_concerns(tmp_path: Path) -> None:
    """A message for administrators is not the reason a resident reads, and a wall tablet
    only says what concerns the whole house."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    hm.activated = True
    await _raise(hm, Kind.IMPORTANT, "a:x", "notify.learned", {"what": "x"}, concerns="admins")
    assert await hm.vesta_why("nl", 3, False, "demo-user-2") is None  # a resident
    assert (await hm.vesta_why("nl", 3, True, "demo-user-1")) is not None  # an administrator
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, snap = await _screen(client, tablet="living")
        assert snap["why"] is None
        await ws.close()
    await hm.stop()


async def test_red_says_what_happened(tmp_path: Path) -> None:
    """In an emergency the line says what happened, not the temperature."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    hm.activated = True
    await ha.simulate("binary_sensor.kitchen_smoke", "on")
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, snap = await _screen(client)
        assert snap["orb"] == "emergency"
        assert "rook" in snap["why"]["text"].lower(), snap["why"]
        await ws.close()
    await hm.stop()


async def test_the_calm_line_stays_for_what_it_already_says(tmp_path: Path) -> None:
    """A pause and no connection with Home Assistant are already in the line: no reason over
    them, and when all is calm there is none either."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    hm.activated = True
    assert await hm.vesta_why("nl", 3, True, "demo-user-1") is None  # calm
    await hm.pause(60)
    assert await hm.vesta_why("nl", 3, True, "demo-user-1") is None
    await hm.stop()


# -- in Home Assistant (B-300) -----------------------------------------------------------

SENSOR = Path(__file__).resolve().parents[1] / "integration/custom_components/housemind/sensor.py"


async def test_home_assistant_gets_the_reason_only_for_the_whole_house(tmp_path: Path) -> None:
    """HA's Toestand sensor said only "Attention". Now it says why as well – but only what
    concerns the whole house, since everyone in Home Assistant can read it: nothing for the
    administrators only, nothing of the calendar, a lock or an alarm."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    hm.activated = True
    await _raise(hm, Kind.IMPORTANT, "a:x", "notify.learned", {"what": "x"}, concerns="admins")
    state = await hm.bridge_state()
    assert state["orb"] == "attention" and state["reason"] is None  # for administrators only
    await _raise(hm, Kind.IMPORTANT, "waste:x", "notify.waste", {"what": "GFT"})
    assert (await hm.bridge_state())["reason"] is None  # from the calendar
    await _raise(
        hm, Kind.IMPORTANT, "battery:x", "notify.maintenance.battery", {"device": "Gang rookmelder"}
    )
    await hm._resolve("waste:x")  # the container is outside: the smoke detector is next
    reason = (await hm.bridge_state())["reason"]
    assert reason is not None and "rookmelder in de gang" in reason.lower(), reason
    tree = ast.parse(SENSOR.read_text(encoding="utf-8"))
    state_class = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HouseMindState"
    )
    assert "'reason'" in ast.unparse(state_class)  # the sensor passes it on as an attribute
    await hm.stop()
