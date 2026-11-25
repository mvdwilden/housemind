from __future__ import annotations

from pathlib import Path

from housemind.brain.briefing import findings_line
from housemind.i18n.catalog import load_languages

KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"


def test_shipped_languages_are_complete() -> None:
    languages = load_languages(KNOWLEDGE / "languages")
    assert {"en", "nl"} <= set(languages.languages)
    assert languages.missing_keys() == {}


def test_text_resolves_language_and_plurals() -> None:
    languages = load_languages(KNOWLEDGE / "languages")
    assert languages.text("nl", "orb.calm") == "Alles rustig"
    assert languages.text("nl-BE", "orb.calm") == "Alles rustig"
    assert languages.text("nl", "ui.people_home", count=1) == "1 persoon thuis"
    assert languages.text("nl", "ui.people_home", count=2) == "2 mensen thuis"
    assert languages.text("en", "ui.people_home", count=3) == "3 people home"


def test_missing_text_falls_back_to_english(tmp_path: Path) -> None:
    (tmp_path / "en.yaml").write_text("a: Hello {name}\nb: Bye\n", encoding="utf-8")
    (tmp_path / "de.yaml").write_text("a: Hallo {name}\n", encoding="utf-8")
    languages = load_languages(tmp_path)
    assert languages.text("de", "a", name="Welt") == "Hallo Welt"
    assert languages.text("de", "b") == "Bye"
    assert languages.text("fr", "a") == "Hello {name}"
    assert languages.missing_keys() == {"de": {"b"}}


def test_an_own_language_file_cannot_take_words_that_protect(tmp_path: Path) -> None:
    """QA 2 (B-168): an own language file may add garage-door words, never shorten them; and it
    cannot add words that switch an emergency off (rain is no leak, a freezer is no frost)."""
    from housemind.brain.rhythm import daily_words
    from housemind.i18n.catalog import load_languages

    shipped = Path(__file__).resolve().parents[1] / "knowledge" / "languages"
    own = tmp_path / "languages"
    own.mkdir()
    (own / "nl.yaml").write_text(
        'daily_words:\n  garage_door: "schuurdeur"\n  not_leak: "lek, water"\n', encoding="utf-8"
    )
    (own / "xx.yaml").write_text('daily_words:\n  fridge: "rookmelder"\n', encoding="utf-8")
    plain = load_languages([shipped])
    languages = load_languages([shipped, own])
    garage = daily_words(languages, "garage_door")
    assert set(daily_words(plain, "garage_door")) <= set(garage) and "schuurdeur" in garage
    assert daily_words(languages, "not_leak") == daily_words(plain, "not_leak")
    assert daily_words(languages, "fridge") == daily_words(plain, "fridge")


def test_a_count_never_shows_a_text_key(tmp_path: Path) -> None:
    """A number passed to a text without one/other forms used to show the key itself: in a real
    house the bedtime check said '… and bedtime.item.more.other' (more than four things on), and
    a label question 'notify.label_system.one'. The plain text is used then."""
    (tmp_path / "en.yaml").write_text("a: '{count} more'\nb:\n  one: one b\n  other: '{count} b'\n")
    languages = load_languages(tmp_path)
    assert languages.text("en", "a", count=3) == "3 more"
    assert languages.text("en", "b", count=1) == "one b"
    assert languages.text("en", "b", count=2) == "2 b"
    shipped = load_languages(KNOWLEDGE / "languages")
    items = [("light", f"Lamp {n}") for n in range(1, 7)]
    two_more = {"nl": "nog 2 andere", "en": "2 more", "de": "2 weitere", "fr": "2 autres"}
    for lang, two in two_more.items():
        line = findings_line(shipped.text, lang, items, "bedtime.found")
        assert "bedtime." not in line and two in line, line
    one = findings_line(shipped.text, "nl", items[:5], "bedtime.found")
    assert "nog 1 andere" in one, one
