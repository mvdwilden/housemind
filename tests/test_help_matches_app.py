"""Help says what the app does (found while bringing the manual up to date, 20-10): Help sent you
to Onderhoud en privacy for ‘Alles teruggeven’, while the button is under Opnieuw beginnen; it
sent you to Huisinstellingen → Beveiliging, while that is under Huis; it said the thumbs were
counted from Expert (levels count from 1: 👍/👎 show from Geïnformeerd, the counts from
Betrokken); and some texts said "Vesta" where the house may have given her
another name. Each test fails without its fix; what it checks is its docstring."""

from __future__ import annotations

from pathlib import Path

from housemind.i18n.catalog import load_languages

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = load_languages([ROOT / "knowledge" / "languages"]).languages
CODES = ("nl", "en", "de", "fr")


def test_no_text_calls_her_vesta() -> None:
    """Her name is the one the house gave her: every text says {name}, never "Vesta"."""
    named = [
        f"{code}: {key}"
        for code in CODES
        for key, text in LANGUAGES[code].items()
        if "Vesta" in str(text)
    ]
    assert not named, "\n".join(named)


def test_help_finds_give_everything_back_where_it_is() -> None:
    """‘Alles teruggeven’ is under Opnieuw beginnen › HouseMind verwijderen, in every language."""
    for code in CODES:
        texts = LANGUAGES[code]
        path = f"› {texts['ui.settings.group.restart']} › {texts['ui.settings.remove']}"
        assert path in texts["help.l4.a23"], code
        assert texts["ui.settings.remove_button"] in texts["help.l4.a23"], code


def test_help_finds_the_alarm_choice_where_it_is() -> None:
    """Which alarm it is, you choose under Huis › Beveiliging – in Help and in the message."""
    for code in CODES:
        texts = LANGUAGES[code]
        path = f"› {texts['ui.settings.group.house']} › {texts['ui.settings.security.title']}"
        assert path in texts["help.l2.a29"], code
        assert path in texts["notify.alarm.choose"], code


def test_help_says_from_which_level_the_thumbs_show() -> None:
    """👍 and 👎 from Geïnformeerd on (level 2), the counts per part from Betrokken on (level 3)
    – as the app does; Expert is not where they start."""
    for code in CODES:
        texts = LANGUAGES[code]
        answer = texts["help.l2.a26"]
        assert texts["levels.informed"] in answer, code
        assert texts["levels.involved"] in answer, code
        assert texts["levels.expert"] not in answer, code
