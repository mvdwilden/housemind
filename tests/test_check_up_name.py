"""The page that looks after Home Assistant is called Check-up (E-457, B-372), in every
language. The administrator's wish (04-11-2026): "Kan je eerst een paar voorstellen doen om
'Home Assistant Netjes' te hernoemen? (…) En als ik er 1 kies, dan graag hernoemen (ook in
alle talen)". The choice: Check-up. What a person reads – the menu, the tab on a phone, the
window, Help, the manual, Vesta's words – says Check-up; the old names are nowhere any more,
only in the news of earlier versions. Fails without the change."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from housemind.i18n.catalog import load_languages

ROOT = Path(__file__).resolve().parents[1]
OLD = {
    "nl": "Home Assistant netjes",
    "en": "Tidy Home Assistant",
    "de": "Home Assistant aufgeräumt",
    "fr": "Home Assistant en ordre",
}
TITLE = {
    "nl": "Check-up van Home Assistant",
    "en": "Home Assistant check-up",
    "de": "Check-up von Home Assistant",
    "fr": "Check-up de Home Assistant",
}


@pytest.mark.parametrize("lang", sorted(OLD))
def test_it_is_called_check_up(lang: str) -> None:
    languages = load_languages([ROOT / "knowledge" / "languages"])

    def t(key: str) -> str:
        return languages.text(lang, key)

    assert t("tabs.tidy") == t("tabs_short.tidy") == "Check-up"
    assert t("ui.sheet.tidy") == t("ui.tidy.title") == TITLE[lang]
    for level, q in (("l3", "q28"), ("l4", "q24")):
        assert "Check-up" in t(f"help.{level}.{q}") + t(f"help.{level}.a{q[1:]}")
    assert "check-up" in t("talk.know.tidy")  # 'Check-up' is understood when you ask Vesta


@pytest.mark.parametrize("lang", sorted(OLD))
def test_the_old_name_is_gone(lang: str) -> None:
    texts = (ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text(encoding="utf-8")
    # Asked in lower case, Vesta still knows the old name; written, it is gone.
    said = [line for line in texts.splitlines() if OLD[lang] in line]
    assert said == [], said
    manual = (ROOT / "knowledge" / "manual" / f"{lang}.html").read_text(encoding="utf-8")
    assert OLD[lang] not in manual
    assert f"<h3>{TITLE[lang]}</h3>" in manual and 'id="beheer-check-up"' in manual
    assert len(re.findall(r'href="#beheer-check-up">[^<]*Check-up</a>', manual)) >= 4
