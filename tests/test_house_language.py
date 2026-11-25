"""The language of the house can change later (E-320, wish from the real house, 21-10-2026):
Vesta's pipeline stood in English – the administrator's HA language when setting up – and
did not follow. Now Huisinstellingen › Vesta has "Taal van het huis"; after a yes her own
pipeline follows at once. Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind.server import views
from housemind.server.web import create_app
from tests.test_automations import start
from tests.test_setting_up import AGENT, _link
from tests.test_start_over import _hello, _next, _snapshot

RESIDENT = {"X-Remote-User-Id": "demo-user-2"}


async def test_the_administrator_changes_the_language_of_the_house_and_her_pipeline_follows(
    tmp_path: Path,
) -> None:
    """Set up in English, the administrator chooses Nederlands in Vesta's settings: her own
    pipeline, its speech recognition and voice go to Dutch, and she says so."""
    hm, clock, ha = await start(tmp_path)
    await _link(hm, ha, clock)
    await hm.set_house_language("en")
    from housemind.brain import setting_up

    assert await setting_up.make_pipeline(hm)
    mine = next(p for p in ha.pipelines if p["conversation_engine"] == AGENT)
    assert mine["language"] == "en"
    shown = await views.vesta_settings(hm, "nl")
    assert shown["houseLanguage"] == "en" and shown["languages"]["nl"] == "Nederlands"
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client)
        await _snapshot(ws)
        await ws.send_json({"type": "house_setting", "key": "house_language", "value": "nl"})
        said = await _next(ws, "saved")
        assert said["text"] == "De taal van het huis is nu Nederlands. Haar pijplijn is aangepast."
        await ws.close()
    mine = next(p for p in ha.pipelines if p["conversation_engine"] == AGENT)
    assert mine["language"] == "nl"
    assert mine["tts_language"] == "nl-NL" and mine["stt_language"] == "nl-NL"
    assert (await views.vesta_settings(hm, "nl"))["houseLanguage"] == "nl"
    await hm.stop()


async def test_only_an_administrator_and_only_a_known_language(tmp_path: Path) -> None:
    """A resident without administrator rights changes nothing, and neither does a language
    HouseMind has no language file for; an administrator's choice of a known one does."""
    hm, clock, ha = await start(tmp_path)
    await _link(hm, ha, clock)
    await hm.set_house_language("en")
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, RESIDENT)
        await _snapshot(ws)
        await ws.send_json({"type": "house_setting", "key": "house_language", "value": "nl"})
        await ws.send_json({"type": "get", "what": "settings"})
        await ws.close()
    assert hm._language() == "en"
    assert not await hm.set_house_setting("house_language", "xx")
    assert hm._language() == "en"
    assert await hm.set_house_setting("house_language", "de")  # an administrator can
    assert hm._language() == "de"
    await hm.stop()
