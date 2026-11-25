from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.voice import Phrases
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.helpers import next_message as _next

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)
HEADERS = {"X-Remote-User-Id": "user-1", "X-Remote-User-Display-Name": "Resident"}


async def _hm(tmp_path: Path) -> HouseMind:
    clock = SimulatedClock(T0)
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start()
    return hm


async def test_websocket_flow(tmp_path: Path) -> None:
    hm = await _hm(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=HEADERS)
        await ws.send_json({"type": "hello", "browserLanguage": "nl-NL"})
        snap = (await _next(ws, "snapshot"))["snapshot"]
        assert snap["orb"] == "calm"
        assert snap["level"] == 1  # a new person begins Carefree unless chosen (E-287)
        assert snap["language"] == "nl"
        assert "3 mensen thuis" in snap["line"]
        assert "°C binnen" in snap["line"]
        assert snap["texts"]["orb.calm"] == "Alles rustig"

        await ws.send_json({"type": "set_level", "level": 2})
        assert (await _next(ws, "snapshot"))["snapshot"]["level"] == 2
        await ws.send_json({"type": "set_level", "level": 1})
        assert (await _next(ws, "snapshot"))["snapshot"]["level"] == 1

        await ws.send_json({"type": "pause", "minutes": 60})
        snap = (await _next(ws, "snapshot"))["snapshot"]
        assert snap["orb"] == "paused"
        assert snap["line"].startswith("Pauze tot")

        await ws.send_json({"type": "orb_tap"})
        said = await _next(ws, "say")
        paused = Phrases.load([ROOT / "knowledge"]).variants("nl", "paused", False)
        assert said["voice"] == "Vesta" and any(v.startswith(said["text"]) for v in paused)

        await ws.send_json({"type": "pause", "minutes": None})
        assert (await _next(ws, "snapshot"))["snapshot"]["orb"] == "calm"

        ha = hm.ha
        await ha.simulate("binary_sensor.kitchen_smoke", "on", by=Source.PHYSICAL)  # type: ignore[attr-defined]
        snap = (await _next(ws, "snapshot"))["snapshot"]
        assert snap["orb"] == "emergency"
        assert ha.states["light.kitchen"].state == "on"  # type: ignore[attr-defined]
        await ws.send_json({"type": "orb_tap"})
        assert "rook" in (await _next(ws, "say"))["text"].lower()

        await ha.simulate("binary_sensor.kitchen_smoke", "off", by=Source.PHYSICAL)  # type: ignore[attr-defined]
        orbs = [(await _next(ws, "snapshot"))["snapshot"]["orb"]]
        while orbs[-1] != "calm" and len(orbs) < 30:  # screens queued during the emergency
            orbs.append((await _next(ws, "snapshot"))["snapshot"]["orb"])
        assert orbs[-1] == "calm"
        await ws.close()
    rows = await hm.db.fetchall("SELECT outcome FROM decisions")
    assert rows and all(r["outcome"] == "executed" for r in rows)
    await hm.stop()


async def test_people_win_after_manual_switch(tmp_path: Path) -> None:
    hm = await _hm(tmp_path)
    await hm.ha.simulate("light.hall", "on", by=Source.PHYSICAL)  # type: ignore[attr-defined]
    assert hm.gate is not None
    assert "light.hall" in hm.gate.state.people_win_until
    await hm.stop()


async def test_only_ingress_may_connect(tmp_path: Path) -> None:
    hm = await _hm(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=True))) as client:
        response = await client.get("/api/health")
        assert response.status == 403
        assert "Content-Security-Policy" in response.headers
    await hm.stop()


async def test_pause_survives_restart(tmp_path: Path) -> None:
    hm = await _hm(tmp_path)
    await hm.pause(0)
    await hm.stop()
    hm2 = await _hm(tmp_path)
    assert hm2.status.orb == "paused"
    await hm2.stop()
