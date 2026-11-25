"""The wall tablet (E-134, E-22, E-25): its room first, the calmest level at home, HA's language,
awake when someone is there, dark at night, no personal or house settings."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

from housemind.core.clock import SimulatedClock
from housemind.core.model import State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")


async def start(
    tmp_path: Path, at: datetime, levels: tuple[int, ...]
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "de")  # HA itself speaks German here
    house.ha.states["weather.home"] = State(
        "weather.home", "sunny", {"temperature": 12.3}, clock.now()
    )
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    for i, level in enumerate(levels, start=1):
        prefs = {"language": "nl", "theme": "dark"}
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, preferences, created) "
            "VALUES(?,?,?,?,?)",
            (f"demo-user-{i}", f"Resident {i}", level, json.dumps(prefs), clock.now().isoformat()),
        )
    await hm._refresh_people()
    return hm, clock, house.ha


async def _snapshot(ws: Any) -> dict[str, Any]:
    while True:
        message = await ws.receive_json(timeout=5)
        if message["type"] == "snapshot":
            return message["snapshot"]  # type: ignore[no-any-return]


async def test_the_wall_tablet_is_a_calm_shared_screen(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path, datetime(2026, 10, 19, 18, 0, tzinfo=TZ), (3, 2))
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "tablet": "living", "haLanguage": "nl"})
        snap = await _snapshot(ws)
        tablet = snap["tablet"]
        assert tablet["area"] == "living" and tablet["awake"] is False
        assert snap["level"] == 2  # the calmest of who is home, not the user's own 3 (E-22)
        assert snap["language"] == "de"  # HA's language, not a person's (E-25)
        assert snap["user"]["isAdmin"] is False and snap["prefs"]["theme"] == "auto"
        assert tablet["weather"] == "12 °C, sonnig"
        assert {s["name"] for s in tablet["scenes"]} >= {"Film", "Gemütlich"}
        # Someone comes into the room: it wakes up.
        await ha.simulate("binary_sensor.living_presence", "on")
        snap = await _snapshot(ws)
        assert snap["tablet"]["awake"] is True
        # A moment from the tablet goes through the gate like any request.
        await ws.send_json({"type": "scene", "area": "living", "id": "start:movie"})
        for _ in range(5):
            await _snapshot(ws)
            if ha.states["cover.living_curtain"].state == "closed":
                break
        assert ha.states["cover.living_curtain"].state == "closed"
        # A wrong room is ignored: a normal screen.
        await ws.send_json({"type": "hello", "tablet": "nowhere"})
        for _ in range(5):
            snap = await _snapshot(ws)
            if snap["tablet"] is None:
                break
        assert snap["tablet"] is None and snap["level"] == 3
        await ws.close()
    await hm.stop()


async def test_at_night_the_tablet_is_dark_and_carefree_at_home_means_only_the_orb(
    tmp_path: Path,
) -> None:
    hm, _, _ = await start(tmp_path, datetime(2026, 10, 19, 23, 30, tzinfo=TZ), (3, 1))
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "tablet": "kitchen"})
        snap = await _snapshot(ws)
        assert snap["tablet"]["night"] is True
        assert snap["level"] == 1 and "rooms" not in snap  # someone on Carefree is home
        await ws.close()
    await hm.stop()
