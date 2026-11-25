"""One word for one thing (E-118, QA 2 L10): the texts people read use the words of
docs/glossary.md. Words people say (recognition lists) may keep others, so Vesta still
understands them."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SAID = (
    "talk.words.",
    "talk.know.",
    "talk.settings.",
    "daily_words.",
    "energy_words.",
    "area_roles.",
)

# Per language: the words that mix with a word of the glossary, and which word that is.
NOT = {
    "nl": {r"\bkamers?\b": "ruimte", r"\bdomein(en)?\b": "onderdeel", r"\bstap \{\}": "{n} van"},
    "en": {r"\bdomains?\b": "part", r"\bareas?\b": "room", r"\bstep \{\}": "{n} of"},
    "de": {
        r"\bBereichs? \{\}": "Raum {area}",
        r"Schritt \{\}": "{n} von",
    },
    "fr": {r"\bespaces?\b": "pièce", r"[Éé]tape \{\}": "{n} sur"},
}


def _texts(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _texts(value, f"{path}{key}.")
    elif isinstance(node, str):
        yield path.rstrip("."), node


def test_every_text_uses_the_words_of_the_glossary() -> None:
    wrong = []
    for lang, patterns in NOT.items():
        texts = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        for key, text in _texts(texts):
            if key.startswith(SAID):
                continue
            text = re.sub(r"\{[a-z_]+\}", "{}", text)  # a place to fill in is no word
            for pattern, word in patterns.items():
                if re.search(pattern, text, flags=re.IGNORECASE if lang in ("en", "fr") else 0):
                    wrong.append(f"{lang} {key}: not {pattern!r} but {word!r}")
    assert not wrong, "\n".join(wrong)


def test_the_glossary_names_every_language() -> None:
    table = (ROOT / "docs" / "glossary.md").read_text()
    for word in ("ruimte", "onderdeel", "Raum", "Bereich", "pièce", "domaine", "room", "part"):
        assert f"| {word} |" in table


def test_a_step_has_one_name_everywhere() -> None:
    """The filter called Propose "Suggest" in English (found by the translator of the
    manual): a step has the same name in every list."""
    for lang in NOT:
        texts = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        for step, name in texts["steps"].items():
            assert texts["ui"]["filter"]["step"][step] == name, (lang, step)


def test_an_export_names_no_brand_of_a_program() -> None:
    """E-325: Exporteren said "Opent in Excel" – a brand name, and HouseMind names none in
    what people read. Now it says "een rekenprogramma" in every language."""
    brands = re.compile(r"\b(Excel|Numbers|LibreOffice|Google Sheets)\b")
    for lang in NOT:
        texts = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        found = [key for key, text in _texts(texts) if brands.search(text)]
        assert not found, (lang, found)


# The kinds of entity a house commonly has in Home Assistant (seen in a real house of 1395).
COMMON_KINDS = [
    "sensor",
    "binary_sensor",
    "light",
    "switch",
    "event",
    "number",
    "select",
    "scene",
    "script",
    "button",
    "text",
    "input_boolean",
    "input_number",
    "input_select",
    "input_button",
    "input_text",
    "input_datetime",
    "media_player",
    "camera",
    "cover",
    "climate",
    "conversation",
    "notify",
    "tts",
    "stt",
    "assist_satellite",
    "ai_task",
    "remote",
    "weather",
    "image",
    "sun",
    "zone",
    "update",
    "device_tracker",
    "alarm_control_panel",
    "water_heater",
    "person",
    "vacuum",
    "lock",
    "fan",
    "valve",
    "siren",
]


def test_every_common_kind_of_entity_has_a_name_people_read() -> None:
    """E-325: the overview and the type column of Apparaten en entiteiten showed 'input_boolean',
    'event', 'number' or 'select' where the rest had a name. Now each common kind has one in
    every language."""
    for lang in NOT:
        texts = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        missing = [kind for kind in COMMON_KINDS if kind not in texts["ui"]["domain"]]
        assert not missing, (lang, missing)
