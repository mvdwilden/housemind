"""Pause 'Tot morgen' (E-83), found in the real house: paused with 'Tot morgen', the screen said
'tot 06:00', and after 06:00 it was still paused. A pause after midnight lasted until the next
day's morning, and a pause that ended did not reach the screens. Each test fails without the
fix; what it checks is its docstring."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server import views

ROOT = Path(__file__).resolve().parents[1]
AMS = ZoneInfo("Europe/Amsterdam")


async def _house(tmp_path: Path, at: datetime) -> HouseMind:
    clock = SimulatedClock(at)
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    return hm


async def test_tot_morgen_after_midnight_ends_this_morning(tmp_path: Path) -> None:
    """'Tot morgen' at half past midnight pauses until 06:00 the same morning – not until
    06:00 the day after – and the line says 'Pauze tot 06:00'."""
    hm = await _house(tmp_path, datetime(2026, 10, 25, 0, 30, tzinfo=AMS))
    await hm.pause(-1)
    until = hm.paused_until()
    assert until is not None and until.astimezone(AMS) == datetime(2026, 10, 25, 6, 0, tzinfo=AMS)
    assert "Pauze tot 06:00" in await views.status_line(hm, "nl", 3)
    await hm.stop()


async def test_tot_morgen_in_the_evening_says_morgen(tmp_path: Path) -> None:
    """In the evening 'Tot morgen' ends tomorrow at 06:00, and the line says so: 'Pauze tot
    morgen 06:00' – 'tot 06:00' alone read as this morning."""
    hm = await _house(tmp_path, datetime(2026, 10, 24, 22, 0, tzinfo=AMS))
    await hm.pause(-1)
    until = hm.paused_until()
    assert until is not None and until.astimezone(AMS) == datetime(2026, 10, 25, 6, 0, tzinfo=AMS)
    assert "Pauze tot morgen 06:00" in await views.status_line(hm, "nl", 3)
    await hm.stop()


async def test_a_pause_that_ends_reaches_the_screens(tmp_path: Path) -> None:
    """A part paused (Verlichting, via Vesta or Pauze) ends, and the open screens hear it in the
    next round – not only when something else happens to change, such as Vesta waking up. On a
    quiet afternoon the 'gepauzeerd' stayed on the screen."""
    hm = await _house(tmp_path, datetime(2026, 10, 24, 14, 0, tzinfo=AMS))
    told: list[int] = []

    async def screen() -> None:
        told.append(1)

    await hm.pause_domain("light", 60)
    for _ in range(4):  # let the house settle
        await hm.tick()
        await hm.clock.advance(30)
    hm.on_update(screen)
    told.clear()
    await hm.clock.advance(30)
    await hm.tick()
    assert not told  # a quiet round tells the screens nothing
    await hm.clock.advance(3600)
    await hm.tick()
    assert hm.paused_domains() == {} and views.pause_info(hm)["domains"] == {}
    assert told, "the screens were not told the pause ended"
    await hm.stop()
