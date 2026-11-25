"""The device catalogue in English for the public QA page, made from HouseMind's own knowledge
files (asked for by the administrator, 20-10-2026). It fails when a device kind has no English
description, so the catalogue never falls behind the knowledge files."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from housemind.knowledge.loader import load_catalog

ROOT = Path(__file__).resolve().parents[1]


def _tool() -> object:
    spec = importlib.util.spec_from_file_location(
        "catalogue", ROOT / "tools" / "build_device_catalogue.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_device_kind_is_in_the_catalogue_in_english() -> None:
    """Each kind from the knowledge files has its English description and a row, and the hard
    limits are listed."""
    tool = _tool()
    catalog, _ = load_catalog([ROOT / "knowledge" / "catalog"])
    missing = sorted(set(catalog.kinds) - set(tool.KINDS))  # type: ignore[attr-defined]
    assert not missing, missing
    text = tool.build()  # type: ignore[attr-defined]
    for description in tool.KINDS.values():  # type: ignore[attr-defined]
        assert f"| {description}" in text
    assert "## Hard limits" in text and "Never open a lock." in text
