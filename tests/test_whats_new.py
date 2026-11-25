"""QA 2, group 5 – what is new per level (E-150, M-36) and languages that no person checked
yet (B-38, M-39). Each test fails without its fix; what it checks is its docstring."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from aiohttp.test_utils import TestClient, TestServer

from housemind import __version__
from housemind.server import views
from housemind.server.web import create_app
from tests.test_safety_gaps import _house
from tests.test_start_over import _hello, _snapshot

ROOT = Path(__file__).resolve().parents[1]


async def _person(hm: object, user_id: str, level: int, prefs: dict[str, object]) -> None:
    await hm.db.execute(  # type: ignore[attr-defined]
        "INSERT OR REPLACE INTO people(user_id, display_name, level, preferences, consents,"
        " created) VALUES(?,?,?,?,?,?)",
        (user_id, f"Bewoner {level}", level, json.dumps(prefs), "{}", "2026-10-01"),
    )


async def test_what_is_new_shows_as_much_as_the_level_once(tmp_path: Path) -> None:
    """There was no "what is new" at all. Now, after an update: nothing on Carefree, one
    sentence on Informed, and a short list on Involved and Expert. Read is read; someone who
    starts with this version sees none. Expert used to get every change of the version too,
    in English: for 0.1.0 that was over two hundred lines. The full list stays in Home
    Assistant's app store (E-44)."""
    hm, _ha = await _house(tmp_path)
    for level in (1, 2, 3, 4):
        await _person(hm, f"user-{level}", level, {"onboarded": True})
    await _person(hm, "user-new", 2, {"onboarded": True, "seen_version": __version__})
    await hm._refresh_people()
    seen = {}
    for user in ("user-1", "user-2", "user-3", "user-4", "user-new"):
        viewer = await views.load_viewer(hm, user, None, True)
        seen[user] = views.whats_new(hm, viewer, "nl")
    assert seen["user-1"] is None and seen["user-new"] is None
    assert seen["user-2"] == {"version": __version__, "one": seen["user-2"]["one"]}
    news = yaml.safe_load((ROOT / "knowledge" / "news" / "nl.yaml").read_text(encoding="utf-8"))
    assert seen["user-2"]["one"] == news[__version__]["one"]
    assert len(seen["user-3"]["list"]) >= 3 and "all" not in seen["user-3"]
    assert seen["user-4"] == seen["user-3"]
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, {"X-Remote-User-Id": "user-3"})
        assert (await _snapshot(ws))["news"]["list"]
        await ws.send_json({"type": "set_pref", "key": "seen_version", "value": __version__})
        await _snapshot(ws, lambda snap: snap["news"] is None)
        await ws.close()
    await hm.stop()


async def test_german_and_french_carry_no_mark_any_more(tmp_path: Path) -> None:
    """German and French said "(nog niet nagekeken)" in the language lists, as B-38 promised
    until a native speaker checked them. The administrator's choice (01-11-2026): "Label
    weghalen". Now no language in the lists has a mark; a language marked as not reviewed in
    its file still gets one, in the viewer's own language."""
    hm, _ha = await _house(tmp_path)
    await _person(hm, "user-2", 2, {"onboarded": True})
    await hm._refresh_people()
    viewer = await views.load_viewer(hm, "user-2", None, True)
    names = (await views.snapshot(hm, viewer))["languageNames"]
    assert names == {"nl": "Nederlands", "en": "English", "de": "Deutsch", "fr": "Français"}
    hm.languages.languages["fr"]["format.reviewed"] = "no"
    names = (await views.snapshot(hm, viewer))["languageNames"]
    assert names["fr"] == "Français (nog niet nagekeken)" and names["de"] == "Deutsch"
    await hm.stop()


def test_every_version_says_what_is_new_in_four_languages() -> None:
    """Each version that goes into the house has its own number (E-440) and its What is new in
    Dutch, English, German and French: one sentence and a short list (E-150)."""
    for lang in ("nl", "en", "de", "fr"):
        news = yaml.safe_load((ROOT / "knowledge" / "news" / f"{lang}.yaml").read_text("utf-8"))
        entry = news.get(__version__)
        assert entry and entry["one"] and len(entry["list"]) >= 1, lang
    changelog = (ROOT / "ha_app_stable" / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {__version__} – " in changelog
