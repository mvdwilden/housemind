"""The changelog in Home Assistant is simple and readable (the administrator's wish,
01-11-2026: "hou de ChangeLog eenvoudiger en leesbaarder, en ruim alles op tot versie 0.2.0"; and
27-11-2026: "Kan je de changelog bij de installatie beperken? Begin vanaf versie 0.4.0 en houd bij
wat je bijgewerkt hebt").
Per version it says what the app says after an update – the English What's new, one sentence and
a short list – made by tools/changelog.py. Before, it held requirement numbers, file names and
every detail since the first build: 113 KB."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ha_app_stable" / "CHANGELOG.md"


def _tool() -> Any:
    spec = importlib.util.spec_from_file_location("changelog", ROOT / "tools" / "changelog.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_changelog_is_what_is_new_in_plain_words() -> None:
    tool = _tool()
    text = TARGET.read_text(encoding="utf-8")
    assert text == tool.make(text), "run: python tools/changelog.py"
    versions = [version for version, _ in tool.HEADING.findall(text)]
    assert versions[-1] == "0.4.0" and len(versions) == len(set(versions))
    # What people read: no pictures, no comments (the support block is made by a tool).
    plain = re.sub(r"\(data:image/[^)]+\)|<!--.*?-->", "", text)
    assert not re.search(r"\b[EBMH]-\d{1,3}\b|\.py\b|\.svelte\b|\.yaml\b|`", plain)
    # Short per version, not in total: a total only keeps long technical texts out until the
    # versions alone add up to it (it was reached at 0.2.33, with ordinary short notes).
    assert len(plain.encode("utf-8")) / len(versions) < 800
