"""Next to Vesta's name in her window, in words, what she does now (E-269), asked for in the
real house: "Vesta · aan het denken", "Vesta · pauze". Every word is a text in every language,
and the window shows it next to her name. Each test fails without it; what it checks is its
docstring."""

from __future__ import annotations

import re
from pathlib import Path

from housemind.i18n.catalog import load_languages

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"


def _doing() -> list[str]:
    source = (FRONTEND / "lib" / "vesta.ts").read_text(encoding="utf-8")
    found = re.search(r"export const DOING = \[(.*?)\] as const", source, re.S)
    assert found, "vesta.ts lists what Vesta can be doing"
    return re.findall(r'"([a-z]+)"', found.group(1))


def test_every_word_for_what_vesta_does_is_a_text_in_every_language() -> None:
    """Each state, and listening and speaking, has short words in Dutch, English, German and
    French – in Dutch the ones asked for: "aan het denken", "pauze"."""
    words = _doing()
    assert {"thinking", "paused", "listening", "speaking", "emergency"} <= set(words)
    languages = load_languages([ROOT / "knowledge" / "languages"])
    missing = [
        f"{code}: ui.vesta.now.{word}"
        for code in ("nl", "en", "de", "fr")
        for word in words
        if not str(languages.languages[code].get(f"ui.vesta.now.{word}", "")).strip()
    ]
    assert not missing, missing
    assert languages.text("nl", "ui.vesta.now.thinking") == "aan het denken"
    assert languages.text("nl", "ui.vesta.now.paused") == "pauze"


def test_the_window_shows_the_words_next_to_her_name() -> None:
    """In her window the words stand right after her name, and her Orb says them to a screen
    reader too."""
    window = (FRONTEND / "components" / "VestaWindow.svelte").read_text(encoding="utf-8")
    assert '{name} <span class="doing">· {doingWords}</span>' in window
    assert "label=\"{name} · {doingWords}. {t('orb.tap_hint')}\"" in window
    assert "ui.vesta.now.${doing(shown, vesta.phase, speaking)}" in window
