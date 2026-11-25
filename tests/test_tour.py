"""Vesta from start to finish (E-249, E-253, B-97, B-98), from the user's side: the tour is
remembered per person, her voice tells the tour and the introduction, and an answer in the
introduction may be said aloud. Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from housemind.ha.adapter import Voice
from housemind.server.web import create_app
from tests.test_vesta import _house
from tests.test_vesta_asks import _until


async def _client(hm: Any, tmp_path: Path, client: TestClient) -> Any:
    ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
    await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": True})
    await _until(ws, "snapshot")
    return ws


async def test_the_tour_is_remembered_per_person_without_a_saved_message(tmp_path: Path) -> None:
    """Which levels someone saw the tour for is kept with them (every device the same), and the
    end of the tour does not pop up "Bewaard."."""
    hm, _ha, _clock = await _house(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _client(hm, tmp_path, client)
        await ws.send_json({"type": "set_pref", "key": "tour", "value": [2, 1, 9, "x"]})
        snapshot = (await _until(ws, "snapshot"))["snapshot"]["snapshot"]
        assert snapshot["prefs"]["tour"] == [1, 2]
        with pytest.raises(asyncio.TimeoutError):
            await _until(ws, "saved", wait=1)
        await ws.close()
    await hm.stop()


async def test_her_voice_tells_the_tour_and_the_introduction_and_nothing_else(
    tmp_path: Path,
) -> None:
    """A step of the tour or the introduction in her voice, with her name filled in; any other
    text is not spoken (the app cannot make her say something else)."""
    hm, ha, _clock = await _house(tmp_path)
    ha.voice = Voice("tts.cloud", "nl-NL", "Colette")
    hm.speech.voice = await hm.vesta_voice()
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _client(hm, tmp_path, client)
        await ws.send_json({"type": "vesta_read", "key": "ui.tour.welcome.text"})
        await _until(ws, "voice")
        assert ha.sounds[-1][0].startswith("Ik ben Vesta, de stem van dit huis.")
        await ws.send_json({"type": "vesta_read", "key": "ui.intro.vesta.level"})
        await _until(ws, "voice")
        spoken = len(ha.sounds)
        await ws.send_json({"type": "vesta_read", "key": "notify.learned"})
        with pytest.raises(asyncio.TimeoutError):
            await _until(ws, "voice", wait=1)
        assert len(ha.sounds) == spoken
        await ws.close()
    await hm.stop()


async def test_an_answer_in_the_introduction_may_be_said_aloud(tmp_path: Path) -> None:
    """ "Niveau twee", "Betrokken", "ja", "nee": the introduction takes it like the button;
    something else is not understood, and she says so."""
    hm, ha, _clock = await _house(tmp_path)
    ha.heard = ["Niveau twee", "Ik wil Betrokken", "Ja graag", "Nee", "Een banaan"]
    expected = [
        ("level", 2),
        ("level", 3),
        ("emergency", True),
        ("learning", False),
        ("pets", None),
    ]
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _client(hm, tmp_path, client)
        for step, value in expected:
            await ws.send_json({"type": "listen_start"})
            await ws.send_bytes(b"\x01\x00" * 16000)
            await ws.send_json({"type": "listen_end", "intro": step})
            heard = (await _until(ws, "intro_heard"))["intro_heard"]
            assert (heard["step"], heard["value"]) == (step, value)
        await ws.close()
    await hm.stop()
