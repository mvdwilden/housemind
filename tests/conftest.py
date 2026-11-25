from __future__ import annotations

from pathlib import Path

import pytest

from housemind.knowledge.catalog import Catalog
from housemind.knowledge.loader import load_catalog
from housemind.runtime import HouseMind

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "knowledge"


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    loaded, report = load_catalog([KNOWLEDGE / "catalog"])
    assert not report.warnings, report.warnings
    return loaded


@pytest.fixture(autouse=True)
def house_already_on(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """A new installation waits for the introduction (E-275); the tests of everything else
    start with a house that is already on. ``@pytest.mark.fresh_install`` tests the wait."""
    if request.node.get_closest_marker("fresh_install") is None:
        monkeypatch.setattr(HouseMind, "FRESH_INACTIVE", False)
