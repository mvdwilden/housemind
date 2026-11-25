"""The morning card and the wall tablet follow the house, not the clock (code QA 3, punt 6) –
what someone notices on a late Sunday or an early evening: the rest of HouseMind already
followed the house's own rhythm (E-455), but the morning card stood there from 5:00 to 11:00
and a wall tablet went dark from 23:00 to 6:00 whatever the house did. The first and the last
test fail without the change; the middle one keeps the clock for a house without a rhythm yet.
What each checks is its docstring."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from housemind.brain.rhythm import Part
from housemind.runtime import HouseMind
from housemind.server import views
from housemind.world.house_mode import HouseMode
from tests.test_briefing import local, start


def _part(monkeypatch: pytest.MonkeyPatch, hm: HouseMind, kind: str, how: str) -> None:
    """What the house's rhythm says now."""
    assert hm.world is not None

    def part_now(_world: Any, now: datetime) -> Part:
        return Part(kind, now, how, kind == "night")

    monkeypatch.setattr(type(hm.world), "part_now", part_now)


async def _card(hm: HouseMind) -> Any:
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    return await views.morning_card(hm, viewer, "nl")


async def test_a_late_morning_still_has_its_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Sunday: the house woke at ten, and at half past eleven it is still its morning – the
    card is still there. When its day begins, the card goes, also at a quarter to nine."""
    hm, _clock, _ha = await start(local(19, 11, 30), tmp_path)
    _part(monkeypatch, hm, "morning", "woke")
    assert await _card(hm) is not None
    _part(monkeypatch, hm, "day", "rhythm")
    assert await _card(hm) is None
    await hm.stop()


async def test_without_a_rhythm_the_clock_still_decides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A house that has no rhythm yet keeps the clock: the card from 5:00 until 11:00."""
    hm, _clock, _ha = await start(local(19, 7, 30), tmp_path)
    _part(monkeypatch, hm, "night", "clock")  # only the clock says something
    assert await _card(hm) is not None
    await hm.stop()


async def test_a_wall_tablet_is_dark_in_the_houses_night(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The house's night begins at half past ten: a wall tablet goes dark then, not only at
    eleven; and a house still up at half past eleven on a Friday keeps its tablet light."""
    hm, clock, _ha = await start(local(19, 22, 30), tmp_path)
    _part(monkeypatch, hm, "night", "rhythm")
    assert views.tablet_view(hm, "kitchen", "nl")["night"] is True  # type: ignore[index]
    await clock.advance(3600)
    assert hm.world is not None
    hm.world.modes.mode = HouseMode.HOME  # still up: the house does not sleep
    _part(monkeypatch, hm, "evening", "rhythm")
    assert views.tablet_view(hm, "kitchen", "nl")["night"] is False  # type: ignore[index]
    await hm.stop()
