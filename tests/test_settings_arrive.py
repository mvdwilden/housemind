"""What an administrator chooses in Huisinstellingen is kept – from the screen's side. Found on
24-11-2026 when the administrator asked to make the hall lamps the signal lamps and to open the
curtains at a fire: the screen said 'Bewaard.', but nothing under Beveiliging reached HouseMind
(nor 'leren uit de geschiedenis'). Each test fails without the change; what it checks is its
docstring."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.server.web import HOUSE_KEYS, create_app
from tests.test_alarm_reaction import SIGNAL, _house
from tests.test_screens import _next

ROOT = Path(__file__).resolve().parent.parent
ADMIN = {"X-Remote-User-Id": "demo-user-1"}
SHUTTER = "cover.bedroom_shutter"


async def _ws(client: TestClient) -> Any:
    ws = await client.ws_connect("/api/ws", headers=ADMIN)
    await ws.send_json({"type": "hello", "browserLanguage": "nl"})
    await _next(ws, "snapshot")
    return ws


async def _choose(ws: Any, key: str, value: Any) -> str:
    await ws.send_json({"type": "house_setting", "key": key, "value": value})
    return str((await _next(ws, "saved"))["text"])


async def _settings(ws: Any) -> dict[str, Any]:
    await ws.send_json({"type": "get", "what": "settings"})
    while True:
        message = await _next(ws, "data")
        if message.get("what") == "settings":
            return message["data"]  # type: ignore[no-any-return]


async def test_what_is_chosen_under_beveiliging_is_kept(tmp_path: Path) -> None:
    """An administrator ticks a signal lamp, a shutter that goes up at a fire, the alarm on by
    itself when everyone left, and a sound; the screen says Bewaard. and shows the ticks again
    when it opens the settings anew – and HouseMind itself has them."""
    hm, _clock, ha = await _house(tmp_path)
    await ha.simulate(SHUTTER, "closed")
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        assert await _choose(ws, "alarm_signal_lamps", [SIGNAL]) == "Bewaard."
        assert await _choose(ws, "fire_open_covers", [SHUTTER]) == "Bewaard."
        assert await _choose(ws, "alarm_self", {"away": True}) == "Bewaard."
        sound = sorted(hm.security.words.optional_sounds)[0]
        assert await _choose(ws, "camera_sounds", [sound]) == "Bewaard."
        security = (await _settings(ws))["security"]
        await ws.close()
    assert security["signal"] == [SIGNAL]
    assert security["fireOpen"] == [SHUTTER]
    assert security["selfArm"]["away"] is True
    assert hm.security.signal == {SIGNAL} and hm.security.fire_covers == {SHUTTER}
    await hm.stop()


async def test_leren_uit_de_geschiedenis_off_is_kept(tmp_path: Path) -> None:
    """The administrator switches 'leren uit de geschiedenis' off; reopened, it is off."""
    hm, _clock, _ha = await _house(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        assert await _choose(ws, "history_learning", False) == "Bewaard."
        assert (await _settings(ws))["historyLearning"] is False
        await ws.close()
    await hm.stop()


async def test_what_was_not_kept_never_says_bewaard(tmp_path: Path) -> None:
    """A choice HouseMind cannot keep – an alarm that does not exist, a setting it does not
    know – says so, and never 'Bewaard.'."""
    hm, _clock, _ha = await _house(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        no = "Dat lukte niet. Er is niets bewaard."
        assert await _choose(ws, "alarm_entity", "alarm_control_panel.nothing") == no
        assert await _choose(ws, "something_else", True) == no
        await ws.close()
    await hm.stop()


def test_every_choice_on_the_screens_has_a_way_in() -> None:
    """Every house setting a screen sends reaches HouseMind: none is lost on the way."""
    sent: set[str] = set()
    for sheet in (ROOT / "frontend" / "src").rglob("*.svelte"):
        text = sheet.read_text()
        sent |= set(re.findall(r'\bsetting\("([a-z_]+)"', text))
        sent |= set(re.findall(r'type: "house_setting", key: "([a-z_]+)"', text))
    assert {"alarm_signal_lamps", "fire_open_covers", "history_learning"} <= sent
    handled_apart = {"max_step", "parts", "house_language"}
    assert sent - handled_apart - HOUSE_KEYS == set()
