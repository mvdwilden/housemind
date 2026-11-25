"""Brein › Apparaten stays live (E-472), from the user's side over the screen's connection.

The administrator's wish (11-11-2026): "Ik zie ook geen wijzigingen als ik in de brein kijk. Bv
ik zet een bewegingssensor aan, en dan zie ik geen verandering in apparaten lijst". The list was
fetched once, when its tab opened. Now a screen that has the list gets only what changed – a
device's new state – by itself, in a demo house and in a real one. Each test fails without it:
no ``device_states`` ever came."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.server.web import create_app
from tests.test_play import _demo, _next
from tests.test_ui import SEEN

EXPERT = {"X-Remote-User-Id": "user-4", "X-Remote-User-Display-Name": "Bewoner 4"}


async def _expert(hm: Any) -> None:
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, preferences, created) VALUES(?,?,?,?,?)",
        ("user-4", "Bewoner 4", 4, SEEN, "2026-10-01"),
    )
    await hm._refresh_people()


async def _list(ws: Any) -> dict[str, str]:
    await ws.send_json({"type": "hello", "browserLanguage": "nl-NL", "partial": True})
    await _next(ws, "snapshot")
    await ws.send_json({"type": "get", "what": "devices"})
    rows = (await _next(ws, "data", "devices"))["data"]
    return {row["id"]: row["state"] for row in rows}


async def test_the_list_follows_what_you_play(tmp_path: Path) -> None:
    """In a demo house: 'Beweging' in the panel shows in the list at once, as the lamp does."""
    async with _demo("simple", tmp_path) as demo:
        await _expert(demo.hm)
        app = create_app(demo.hm, tmp_path, ingress_only=False, demo=True, play=demo.play)
        async with TestClient(TestServer(app)) as client:
            ws = await client.ws_connect("/api/ws", headers=EXPERT)
            states = await _list(ws)
            assert states["binary_sensor.bedroom_motion"] == "off"
            await ws.send_json(
                {"type": "play", "do": "presence", "id": "binary_sensor.bedroom_motion", "on": True}
            )
            changed = (await _next(ws, "data", "device_states"))["data"]
            assert changed["binary_sensor.bedroom_motion"] == "on"
            assert all(states.get(k) != v for k, v in changed.items())  # only what changed
            await ws.send_json(
                {"type": "play", "do": "switch", "id": "light.hall", "on": True, "by": "person"}
            )
            changed = (await _next(ws, "data", "device_states"))["data"]
            assert changed["light.hall"] == "on"


async def test_the_list_follows_the_real_house(tmp_path: Path) -> None:
    """Without a demo house – as in a real Home Assistant – a sensor that changes reaches the
    open list by itself, without opening the tab again."""
    async with _demo("simple", tmp_path) as demo:
        await _expert(demo.hm)
        app = create_app(demo.hm, tmp_path, ingress_only=False)
        async with TestClient(TestServer(app)) as client:
            ws = await client.ws_connect("/api/ws", headers=EXPERT)
            states = await _list(ws)
            assert states["binary_sensor.kitchen_motion"] == "off"
            await demo.house.ha.simulate("binary_sensor.kitchen_motion", "on")
            changed = (await _next(ws, "data", "device_states"))["data"]
            assert changed["binary_sensor.kitchen_motion"] == "on"
            await demo.house.ha.simulate("binary_sensor.kitchen_motion", "off")
            changed = (await _next(ws, "data", "device_states"))["data"]
            assert changed["binary_sensor.kitchen_motion"] == "off"
