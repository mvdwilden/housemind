"""A screen that stops reading never holds up the house. Found while building the setting up
(E-277): every change waited until each open screen had taken its new picture, so a phone whose
connection hung could hold up even an emergency. Each test fails without it; what it checks is
its docstring."""

from __future__ import annotations

import asyncio
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind.core.model import Source
from housemind.server.web import create_app
from tests.test_automations import start


async def test_an_emergency_does_not_wait_for_a_screen_that_does_not_read(tmp_path: Path) -> None:
    """A screen is open but reads nothing; the house changes a hundred times and then smoke is
    seen: the reaction comes at once, and the screen still gets the newest picture."""
    hm, _, ha = await start(tmp_path)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await asyncio.sleep(0.2)
        for i in range(100):
            await asyncio.wait_for(hm._notify(), 2)
            if i % 10 == 0:
                await asyncio.sleep(0)
        await asyncio.wait_for(
            ha.simulate("binary_sensor.kitchen_smoke", "on", by=Source.PHYSICAL), 5
        )
        assert hm.status.emergency is not None
        assert ha.states["light.kitchen"].state == "on"
        orb = None
        while orb != "emergency":
            message = await ws.receive_json(timeout=5)
            if message["type"] == "snapshot":
                orb = message["snapshot"]["orb"]
        await ws.close()
    await hm.stop()
