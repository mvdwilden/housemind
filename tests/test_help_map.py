"""Help matches the app (E-119, release check 5): every help topic is linked to what it
explains, and names the app's own words for buttons and places, in every language."""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import yaml

from housemind.i18n.catalog import load_languages

ROOT = Path(__file__).resolve().parents[1]
MAP = yaml.safe_load((ROOT / "docs" / "help-map.yaml").read_text())
LEVELS = ("l1", "l2", "l3", "l4")


def _resolve(name: str) -> bool:
    if "/" in name:
        return (ROOT / name).is_file()
    parts = name.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            found: Any = importlib.import_module(".".join(parts[:cut]))
        except ModuleNotFoundError:
            continue
        for attr in parts[cut:]:
            if not hasattr(found, attr):
                return False
            found = getattr(found, attr)
        return True
    return False


def _plain(text: str) -> str:
    return re.sub(r"[\s ]+", " ", text).strip().casefold()


def test_every_help_topic_is_linked_and_what_it_explains_exists() -> None:
    help_nl = yaml.safe_load((ROOT / "knowledge" / "languages" / "nl.yaml").read_text())["help"]
    for level in LEVELS:
        topics = {k for k in help_nl[level] if k.startswith("q")}
        assert topics == set(MAP[level]), (level, topics ^ set(MAP[level]))
    missing = [
        f"{level}.{q}: {name}"
        for level in LEVELS
        for q, entry in MAP[level].items()
        for name in entry["about"]
        if not _resolve(name)
    ]
    assert not missing, missing


def test_help_uses_the_app_s_own_words_in_every_language() -> None:
    languages = load_languages([ROOT / "knowledge" / "languages"])
    wrong = []
    for code in sorted(languages.languages):
        for level in LEVELS:
            for q, entry in MAP[level].items():
                answer = _plain(
                    languages.text(code, f"help.{level}.{q}")
                    + " "
                    + languages.text(code, f"help.{level}.a{q[1:]}")
                )
                for key in entry.get("labels", []):
                    label = _plain(languages.text(code, key))
                    if label not in answer:
                        wrong.append(f"{code} {level}.{q}: “{label}” not in the answer")
    assert not wrong, "\n".join(wrong)


def test_the_privacy_statement_says_what_the_code_does() -> None:
    """E-165: the keeping times in the statement are the ones the code uses, and the buttons it
    names are the app's own words, in every language."""
    import inspect

    from housemind.brain.notify import INFO_KEEP
    from housemind.memory.database import KEEP_LONGER, Database

    keep = dict((table, days) for table, _, days in KEEP_LONGER)
    raw_days = inspect.signature(Database.cleanup).parameters["keep_days"].default
    assert (raw_days, keep["notifications"], keep["proposals"]) == (30, 30, 90)
    assert keep["reports"] == keep["energy_days"] == 731 and INFO_KEEP.days == 7
    nl = load_languages([ROOT / "knowledge" / "languages"])
    stored = nl.text("nl", "privacy.stored")
    for words in ("30 dagen", "90 dagen", "2 jaar", "na een week"):
        assert words in stored, words
    for code in sorted(nl.languages):
        rights = _plain(nl.text(code, "privacy.rights"))
        for key in ("ui.why", "ui.undo"):
            assert _plain(nl.text(code, key)) in rights, (code, key)


def test_the_energy_goal_hint_names_the_choices_as_the_menu_does() -> None:
    languages = load_languages([ROOT / "knowledge" / "languages"])
    for code in sorted(languages.languages):
        hint = _plain(languages.text(code, "ui.energy.goal_hint"))
        for goal in ("own_power", "co2", "mix"):
            assert _plain(languages.text(code, f"ui.energy.goals.{goal}")) in hint, (code, goal)
