"""Small helpers many tests share (code QA 3, punt 9): they were written again in each file."""

from __future__ import annotations

from typing import Any

from housemind.core.clock import SimulatedClock
from housemind.runtime import HouseMind


async def next_message(ws: Any, kind: str) -> dict[str, Any]:
    """The next message of this kind on a screen's connection; others are skipped."""
    while True:
        message = await ws.receive_json(timeout=5)
        if message["type"] == kind:
            return message  # type: ignore[no-any-return]


async def ticks(hm: HouseMind, clock: SimulatedClock, minutes: int) -> None:
    """``minutes`` of rounds, one every 30 seconds."""
    for _ in range(minutes * 2):
        await clock.advance(30)
        await hm.tick()


async def wait_seconds(hm: HouseMind, clock: SimulatedClock, seconds: int) -> None:
    """``seconds`` of time, with a round every 10 seconds (at least one)."""
    for _ in range(max(1, seconds // 10)):
        await clock.advance(10)
        await hm.tick()
