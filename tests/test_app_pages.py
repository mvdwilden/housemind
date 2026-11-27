"""The App's Info and Documentation tabs in Home Assistant (E-527, E-302). The Info tab tells
someone who has not installed HouseMind yet what it does for them, with three screens of the
app; the Documentation tab explains it. Both are made from their sources by tools/app_docs.py,
with the pictures inside, and the privacy statement in the Documentation is the App's own. Each
test fails without it; what it checks is its docstring."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("app_docs", ROOT / "tools" / "app_docs.py")
assert SPEC and SPEC.loader
app_docs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app_docs)
INFO = ROOT / "ha_app_stable" / "README.md"
DOCS = ROOT / "ha_app_stable" / "DOCS.md"


def test_both_tabs_are_made_from_their_sources(tmp_path: Path) -> None:
    """What Home Assistant shows is exactly what the sources make, pictures inside (run
    tools/app_docs.py after changing docs/app/README.md or docs/app/DOCS.md)."""
    for source, target in app_docs.PAGES:
        made = tmp_path / target.name
        app_docs.build_page(source, made)
        assert target.read_text(encoding="utf-8") == made.read_text(encoding="utf-8"), (
            f"run tools/app_docs.py ({target.name})"
        )
        assert "](images/" not in target.read_text(encoding="utf-8")  # every picture inside


def test_the_info_tab_shows_what_it_does_for_you_and_how_to_start() -> None:
    """Three screens first, then a day with HouseMind, what it takes off your hands, that it asks
    first, Vesta, privacy and how to start – no beta any more, free to use and not to change
    (PolyForm Strict), with the lowest Home Assistant the App asks for, and nothing for
    developers."""
    info = INFO.read_text(encoding="utf-8")
    first = info.index("![")
    assert info[first:].startswith("![HouseMind on a phone")
    assert "data:image/jpeg;base64," in info[first : first + 200]
    for heading in (
        "## A day with HouseMind",
        "## What it takes off your hands",
        "## It asks before it acts",
        "## Meet Vesta",
        "## Private by design",
        "## Get started",
    ):
        assert heading in info, heading
    config = yaml.safe_load((ROOT / "ha_app_stable" / "config.yaml").read_text(encoding="utf-8"))
    lowest = ".".join(str(config["homeassistant"]).split(".")[:2])
    assert "beta" not in info.lower() and f"version {lowest} or newer" in info
    assert "Demo mode" in info and "PolyForm Strict 1.0.0" in info
    assert "Development" not in info and "uv run" not in info


def test_the_documentation_says_the_apps_own_privacy_statement() -> None:
    """The Documentation said less than the App about what goes to an AI; now every line of the
    App's statement is in it, with her default name."""
    docs = DOCS.read_text(encoding="utf-8")
    raw = yaml.safe_load((ROOT / "knowledge" / "languages" / "en.yaml").read_text("utf-8"))
    name = yaml.safe_load((ROOT / "knowledge" / "vesta" / "en.yaml").read_text("utf-8"))["name"]
    for key in ("intro", "stored", "out", "see", "rights", "contact"):
        for line in str(raw["privacy"][key]).strip().split("\n"):
            assert line.replace("{name}", name) in docs, line[:60]
    assert app_docs.PRIVACY not in docs and "{name}" not in docs


def test_the_documentation_names_what_came_lately() -> None:
    """Morning, evening and night, the heads-ups with the boiler that does not heat, the Check-up
    and the levels for a lamp."""
    docs = DOCS.read_text(encoding="utf-8")
    for words in ("## Morning, evening and night", "**Good night.**", "## Heads-ups", "boiler"):
        assert words in docs, words
    assert "**Check-up**" in docs and "**Dimmed**" in docs and "2026.4 or newer" in docs
