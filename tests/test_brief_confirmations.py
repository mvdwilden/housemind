"""Short confirmations in the header (E-174, wish from the real house, 21-10-2026): "Bewaard"
and "Gedaan" came as a notice with an Oké button. Now the server says which ones are short –
those are plain text in the header for 2 seconds – and a longer message, such as an error or
an explanation, stays a notice. Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind.server.web import BRIEF, create_app
from tests.test_automations import start
from tests.test_start_over import _hello, _next, _snapshot


async def test_saved_and_done_are_brief_and_an_explanation_is_not(tmp_path: Path) -> None:
    """After a saved setting the screen gets "Bewaard." marked brief; a wrong word for the
    factory settings is an explanation and stays a notice."""
    hm, _clock, _ha = await start(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client)
        await _snapshot(ws)
        await ws.send_json({"type": "set_pref", "key": "timing", "value": "longer"})
        saved = await _next(ws, "saved")
        assert saved["text"] == hm.languages.text("nl", "ui.saved") and saved["brief"] is True
        await ws.send_json({"type": "factory_reset", "word": "weg ermee"})
        told = await _next(ws, "saved")
        assert told["text"].startswith("Dat is niet het goede woord")
        assert told["brief"] is False
        await ws.close()
    await hm.stop()


def test_only_short_sentences_are_brief() -> None:
    """What goes in the header fits there: one short sentence in every language."""
    from housemind.i18n.catalog import load_languages

    root = Path(__file__).resolve().parents[1]
    languages = load_languages([root / "knowledge" / "languages"])
    for code in ("nl", "en", "de", "fr"):
        for key in BRIEF | {"ui.saved", "ui.done"}:
            text = languages.text(code, key)
            assert text != key and len(text) <= 40, (code, key, text)
