"""Over HouseMind says what is good to know (E-413), and only what is true – the
administrator's wish: "GDPR, copyright and open source, the version, support for people with
low vision and the footnotes that matter; only what is really so, nothing that is not true".
Each test holds a line of the block against the app itself, so that a change that makes it
untrue fails here. The licences of the parts made by others were not in the App at all; now
they are."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from housemind import __version__
from housemind.knowledge.compat import load_compatibility
from housemind.server import views
from tests.test_screens import ROOT, start, viewer

LANGS = ("nl", "en", "de", "fr")


def _facts(lang: str) -> dict[str, str]:
    data = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
    return dict(data["ui"]["about_facts"])


async def test_the_block_shows_the_version_and_the_lowest_home_assistant(tmp_path: Path) -> None:
    """The version is this App's, without 'beta' as on its info page; 'works with Home Assistant
    2026.4 or newer' is the lowest version the App asks Home Assistant for."""
    hm, _clock, _ha = await start(tmp_path)
    snap = await views.snapshot(hm, await viewer(hm, 2))
    config = yaml.safe_load((ROOT / "ha_app_stable" / "config.yaml").read_text())
    assert snap["version"] == __version__ == config["version"]
    lowest = ".".join(str(config["homeassistant"]).split(".")[:2])
    assert snap["haOldest"] == lowest == load_compatibility([ROOT / "knowledge"]).oldest_text()
    assert "beta" not in (ROOT / "ha_app_stable" / "README.md").read_text().lower()
    assert "bèta" not in _facts("nl")["version"] and "beta" not in _facts("en")["version"]
    await hm.stop()


async def test_what_it_says_about_privacy_and_the_internet_is_so(tmp_path: Path) -> None:
    """A fresh house: AI is off, learning and the calendar wait for
    a yes – as the block says. My data has what it names: see, save as a file, Forget me."""
    hm, _clock, _ha = await start(tmp_path)
    assert hm.ai.status() in ("off", "not_configured")
    assert await hm.db.get_setting("calendar_consent", None) in (None, False)
    for lang in LANGS:  # with the words of the buttons in My data
        words = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        my_data = words["ui"]["my_data"]
        for key in ("title", "forget"):
            assert my_data[key] in _facts(lang)["privacy"], (lang, key)
    await hm.stop()


async def test_what_it_says_for_people_with_low_vision_is_there(tmp_path: Path) -> None:
    """Larger text, High contrast and a screen reader: the scheme exists and passes its 7:1
    check, the text size has Groot, and Vesta can speak aloud on a device."""
    hm, _clock, _ha = await start(tmp_path)
    snap = await views.snapshot(hm, await viewer(hm, 1))
    contrast = next(s for s in snap["themes"] if s["id"] == "contrast")
    assert contrast["name"] == "Hoog contrast"
    for lang in LANGS:
        assert "WCAG 2.2" in _facts(lang)["access"]
    prefs = (ROOT / "frontend" / "src" / "sheets" / "Preferences.svelte").read_text()
    assert "textSize" in prefs and "setVoiceWanted" in prefs
    await hm.stop()


def test_the_languages_and_the_licences_are_the_apps_own() -> None:
    """The licence files of the parts made by others travel with the App, with the texts of
    what is in its screens: Svelte and the three fonts. No person's name in the block."""
    third = (ROOT / "THIRD_PARTY_LICENSES.md").read_text()
    package = json.loads((ROOT / "frontend" / "package.json").read_text())
    for dependency in package["dependencies"]:
        font = dependency.split("/")[-1].replace("-", " ").lower()
        assert font in third.lower(), dependency
    assert "Svelte Contributors" in third and "SIL OPEN FONT LICENSE" in third.upper()
    for place in (ROOT / "Dockerfile", ROOT / "tools" / "make_local_app.py"):
        assert "THIRD_PARTY_LICENSES.md" in place.read_text()
    assert '"THIRD_PARTY_LICENSES.md"' in (ROOT / "tools" / "make_local_app.py").read_text()
    notice = (ROOT / "NOTICE").read_text()
    holder = re.search(r"Copyright \d{4} (.+)", notice)
    assert holder is not None
    for lang in LANGS:
        facts = _facts(lang)
        assert "2026" in facts["open"] and "PolyForm Strict 1.0.0" in facts["open"]
        assert holder.group(1) not in json.dumps(facts, ensure_ascii=False)
