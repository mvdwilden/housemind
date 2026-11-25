"""Vesta's voice speaks the language of what she says (B-124). Found in the real house: after the
factory settings she spoke English – the language Home Assistant shows – in the Dutch voice of
an Assist pipeline. Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind.core.model import State
from housemind.ha.adapter import Voice
from housemind.parts.vesta import voice_locale
from housemind.server.web import create_app
from tests.helpers import next_message as _next
from tests.test_vesta import _house

DUTCH = Voice("tts.cloud", "nl-NL", "Colette")


async def test_her_voice_in_the_app_follows_the_language_she_speaks(tmp_path: Path) -> None:
    """Someone who reads HouseMind in English hears her in an English voice of the same
    engine; someone who reads Dutch keeps the Dutch voice of the pipeline."""
    hm, ha, _ = await _house(tmp_path)
    ha.voice = DUTCH
    ha.tts_locales["tts.cloud"] = ["en-GB", "en-US", "nl-NL"]
    hm.speech.voice = await hm.voice_for(None, await hm.vesta_voice())
    assert hm.speech.voice == DUTCH  # the house speaks Dutch
    await hm.db.execute(
        "UPDATE people SET preferences = ? WHERE user_id = ?",
        (json.dumps({"language": "en"}), "demo-user-1"),
    )
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "orb_tap"})
        said = await _next(ws, "say")
        await _next(ws, "voice")
        assert ha.sounds[-1] == (said["text"], Voice("tts.cloud", "en-US", "calm"))
        await ws.close()
    assert await hm.vesta_audio("Hallo", "nl")
    assert ha.sounds[-1] == ("Hallo", DUTCH)
    await hm.stop()


async def test_an_engine_without_the_language_gives_way_to_one_with_it(tmp_path: Path) -> None:
    """The pipeline's engine does not speak French: her French comes from an engine in HA that
    does, and never in the wrong voice."""
    hm, ha, clock = await _house(tmp_path)
    ha.voice = DUTCH
    for engine in ("tts.cloud", "tts.local"):
        ha.states[engine] = State(engine, "unknown", {}, clock.now())
    ha.tts_locales.update({"tts.cloud": ["nl-NL"], "tts.local": ["fr-FR"]})
    voice = await hm.voice_for("fr", DUTCH)
    assert voice == Voice("tts.local", "fr-FR", "calm")
    await hm.stop()


def test_the_usual_form_of_a_language() -> None:
    """The house's country first, then the usual form, else the first the engine has."""
    assert voice_locale("en", ["en-AU", "en-GB", "en-US"], "GB") == "en-GB"
    assert voice_locale("en", ["en-AU", "en-GB", "en-US"], "NL") == "en-US"
    assert voice_locale("nl", ["nl-BE", "nl-NL"], "BE") == "nl-BE"
    assert voice_locale("de", ["de-AT"], "NL") == "de-AT"
    assert voice_locale("fr", ["nl-NL"], "NL") is None
