"""The core never waits for Home Assistant netjes (E-351, E-85): the quarter-hour look reads
Home Assistant – the integrations, the recorder's history, the system – and that can take long
in a big house. It runs beside the core, one at a time. Each test fails without it; what it
checks is its docstring."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
NOON = datetime(2026, 10, 25, 11, 0, tzinfo=UTC)


async def _start(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, Any]:
    clock = SimulatedClock(NOON)
    house = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    return hm, clock, house.ha


def _hang(ha: Any, read: str) -> tuple[asyncio.Event, list[str]]:
    """Home Assistant takes its time with this read, until the event is set."""
    answer = asyncio.Event()
    asked: list[str] = []
    real = getattr(ha, read)

    async def slow(*args: Any, **kwargs: Any) -> Any:
        asked.append(read)
        await answer.wait()
        return await real(*args, **kwargs)

    setattr(ha, read, slow)
    return answer, asked


async def _quarter(hm: HouseMind, clock: SimulatedClock, minutes: int) -> None:
    await clock.advance(minutes * 60)
    hm._last_review = None  # the quarter-hour steps are due
    await asyncio.wait_for(hm.tick(), 0.5)


@pytest.mark.parametrize("read", ["history", "config_entries", "system_health"])
async def test_a_slow_home_assistant_does_not_hold_up_the_round(tmp_path: Path, read: str) -> None:
    """The recorder's history (for 'Meestal uit tussen …'), the integrations or the system
    health take long: the round of the core is still done within half a second, and when Home
    Assistant answers, the look finishes and its findings are there."""
    hm, clock, ha = await _start(tmp_path)
    answer, asked = _hang(ha, read)
    await _quarter(hm, clock, 11)  # 10 minutes after the start: a look is due (B-238)
    await asyncio.sleep(0.05)
    assert asked, f"the look did not ask Home Assistant for {read}"
    assert hm.tidy_checked is None
    answer.set()
    await hm.settled()
    assert hm.tidy_checked is not None
    assert hm.tidy_findings
    await hm.stop()


async def test_one_look_at_a_time(tmp_path: Path) -> None:
    """Home Assistant's recorder still has not answered when the next quarter of an hour comes:
    no second look starts beside the first, so slow answers do not pile up."""
    hm, clock, ha = await _start(tmp_path)
    answer, asked = _hang(ha, "history")
    await _quarter(hm, clock, 11)
    await asyncio.sleep(0.05)
    hm._tidy_fresh = True  # a look would be due again
    await _quarter(hm, clock, 15)
    await asyncio.sleep(0.05)
    assert asked == ["history"]
    answer.set()
    await hm.settled()
    assert hm.tidy_checked is not None
    await hm.stop()


async def test_stopping_does_not_wait_for_a_slow_look(tmp_path: Path) -> None:
    """HouseMind stops (an update, a restart) while Home Assistant's recorder still has not
    answered the look: it stops at once."""
    hm, clock, ha = await _start(tmp_path)
    _hang(ha, "history")
    await _quarter(hm, clock, 11)
    await asyncio.sleep(0.05)
    await asyncio.wait_for(hm.stop(), 2)
