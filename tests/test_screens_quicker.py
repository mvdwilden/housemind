"""Quicker screens (code QA 3, punt 11) – what someone with a few screens open in a large house
notices: a sensor that changes no longer makes every screen work out "Klaar voor autonomie?"
again, and a busy house gives a screen at most five pictures a second, the newest last. In a
house of 1756 entities three screens on Expert went from about 150 to about 40 ms after a
sensor. Each test fails without the change; what it checks is its docstring."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from housemind.brain import readiness as ready
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server import views

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Amsterdam")


async def _house(tmp_path: Path) -> HouseMind:
    clock = SimulatedClock(datetime(2026, 11, 25, 19, 0, tzinfo=TZ).astimezone(UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path / "data"))
    await hm.start(autorun=False)
    await clock.advance(30)
    await hm.tick()
    return hm


async def test_the_card_is_made_once_and_again_when_something_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two screens on Expert and a sensor that changes: "Klaar voor autonomie?" is made once
    for both. A setting that changes it, or a decision, makes it again – at once, not a round
    later."""
    hm = await _house(tmp_path)
    made: list[int] = []
    original = ready.Readiness.parts

    async def counted(self: ready.Readiness) -> list[dict[str, Any]]:
        made.append(1)
        return await original(self)

    monkeypatch.setattr(ready.Readiness, "parts", counted)
    phone = views.Viewer("demo-user-1", "Resident 1", 4, True, {"language": "nl"})
    tablet = views.Viewer("demo-user-1", "Resident 1", 4, True, {"language": "nl"})
    first = (await views.snapshot(hm, phone))["readiness"]
    await hm.ha.simulate("sensor.bathroom_humidity", "71")  # a sensor: nothing learned
    assert (await views.snapshot(hm, tablet))["readiness"] == first
    assert (await views.snapshot(hm, phone))["readiness"] == first
    assert len(made) == 1
    await hm.db.set_setting("given_back", True)  # something set
    await views.snapshot(hm, phone)
    assert len(made) == 2
    await hm._notify()  # an answer, a step, a rule: what HouseMind knows changed
    await views.snapshot(hm, tablet)
    assert len(made) == 3
    await hm.stop()


async def test_a_busy_house_gives_a_screen_five_pictures_a_second(tmp_path: Path) -> None:
    """Twenty changes in a few milliseconds: the screen gets at most a few pictures in half a
    second, not twenty."""
    from aiohttp.test_utils import TestClient, TestServer

    from housemind.server.web import create_app

    hm = await _house(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "haLanguage": "nl", "partial": True})
        while (await ws.receive_json(timeout=5))["type"] != "snapshot":
            pass
        await asyncio.sleep(0.3)  # what the hello brought along has gone
        while True:
            try:
                await ws.receive_json(timeout=0.05)
            except TimeoutError:
                break
        for _ in range(20):
            await hm._notify(knows=False)
            await asyncio.sleep(0.005)
        pictures = 0
        loop = asyncio.get_running_loop()
        until = loop.time() + 0.5
        while (left := until - loop.time()) > 0:
            try:
                message = await ws.receive_json(timeout=left)
            except TimeoutError:
                break
            pictures += message["type"] == "snapshot"
        assert 1 <= pictures <= 4, pictures
        await ws.close()
    await hm.stop()
