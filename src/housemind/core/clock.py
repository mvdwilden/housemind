"""Time source for the whole app.

Everything that needs "now" asks a :class:`Clock`. Production uses :class:`SystemClock`;
tests and the time machine (E-99) use :class:`SimulatedClock`, which only moves when told to.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
import itertools
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


class Clock(Protocol):
    """Source of the current time and of waiting."""

    def now(self) -> datetime:
        """Current time, timezone aware (UTC)."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Wait the given number of seconds on this clock."""
        ...


class SystemClock:
    """The real wall clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


@dataclass(order=True)
class _Sleeper:
    wake_at: datetime
    seq: int
    future: asyncio.Future[None] = field(compare=False)


class SimulatedClock:
    """A clock that only moves when :meth:`advance` is called.

    Sleepers wake in time order while the clock advances, so a week of house life can be
    replayed in seconds (the time machine).
    """

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 5, 6, 0, tzinfo=UTC)
        if self._now.tzinfo is None:
            raise ValueError("SimulatedClock needs a timezone-aware start time")
        self._sleepers: list[_Sleeper] = []
        self._seq = itertools.count()

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(
            self._sleepers,
            _Sleeper(self._now + timedelta(seconds=seconds), next(self._seq), future),
        )
        await future

    async def advance(self, seconds: float) -> None:
        """Move time forward, waking every sleeper whose time has come, in order."""
        if seconds < 0:
            raise ValueError("time cannot go backwards")
        target = self._now + timedelta(seconds=seconds)
        while self._sleepers and self._sleepers[0].wake_at <= target:
            sleeper = heapq.heappop(self._sleepers)
            self._now = max(self._now, sleeper.wake_at)
            if not sleeper.future.done():
                sleeper.future.set_result(None)
            # Let the woken task run before waking the next one.
            for _ in range(3):
                await asyncio.sleep(0)
        self._now = target
        await asyncio.sleep(0)


class DemoClock:
    """The demo house's own time (E-321, B-188): it runs like a clock (Play) or stands still
    (Stop), slower or faster than the real one (1/20 … 20 times), and can jump ahead. Only a demo
    house uses it; the real house keeps :class:`SystemClock`.

    Sleepers wake in time order; while it stands still, nobody wakes. A jump (:meth:`advance`)
    holds the clock and wakes every sleeper on its own moment, like the time machine, and waits
    until what it woke is done with that moment (it waits on the clock again) before the clock
    moves on – so the residents' rhythm and every round of the brain really play along. Time
    only goes forward.
    """

    SETTLE_S = 1.0  # at most this long (real time) for one woken task, then the jump goes on

    def __init__(
        self, start: datetime | None = None, running: bool = True, speed: float = 1.0
    ) -> None:
        self._base = start or datetime.now(UTC)
        self._real = time.monotonic()
        self.speed = speed if speed > 0 else 1.0
        self._frozen = False  # a jump goes on
        self._stopped = not running  # Stop: the time stands still until Play (B-188)
        self._sleepers: list[_Sleeper] = []
        self._seq = itertools.count()
        self._changed = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._owners: dict[int, asyncio.Task[Any]] = {}  # sleeper → the task that waits
        self._awake: set[asyncio.Task[Any]] = set()  # woken by a jump, not waiting again yet
        self._asked: dict[int, float] = {}  # sleeper → the seconds it asked for
        # During a long jump: the shortest wait for these tasks (by name), so a round every
        # 30 seconds becomes one every 5 minutes – a week then takes seconds (B-189).
        self.coarse: dict[str, float] = {}

    def now(self) -> datetime:
        if self._frozen or self._stopped:
            return self._base
        return self._base + timedelta(seconds=(time.monotonic() - self._real) * self.speed)

    def _rebase(self) -> None:
        self._base = self.now()
        self._real = time.monotonic()

    @property
    def running(self) -> bool:
        return not self._stopped

    def set_speed(self, speed: float) -> None:
        """1 is the real pace; 0.05 is twenty times slower, 20 twenty times faster."""
        self._rebase()
        self.speed = speed if speed > 0 else 1.0
        self._poke()

    def stop(self) -> None:
        """Stop: the time stands still; a jump can still move it ahead."""
        self._rebase()
        self._stopped = True
        self._poke()

    def play(self) -> None:
        """Play: the time runs again from where it stood."""
        self._real = time.monotonic()
        self._stopped = False
        self._poke()

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        seq = next(self._seq)
        task = asyncio.current_task()
        self._asked[seq] = seconds
        if task is not None:
            self._awake.discard(task)  # it waits again: done with its moment
            self._owners[seq] = task
            if self._frozen:
                seconds = max(seconds, self.coarse.get(task.get_name(), 0.0))
        heapq.heappush(
            self._sleepers, _Sleeper(self.now() + timedelta(seconds=seconds), seq, future)
        )
        self._poke()
        try:
            await future
        finally:
            self._owners.pop(seq, None)
            self._asked.pop(seq, None)

    def relax(self) -> None:
        """After a long jump: every round waits no longer than it asked, from now."""
        self.coarse = {}
        now = self.now()
        for sleeper in self._sleepers:
            asked = self._asked.get(sleeper.seq)
            if asked is not None:
                sleeper.wake_at = min(sleeper.wake_at, now + timedelta(seconds=asked))
        heapq.heapify(self._sleepers)
        self._poke()

    async def advance(self, seconds: float) -> None:
        """Jump ahead: every sleeper in between wakes on its own moment, in order."""
        if seconds < 0:
            raise ValueError("time cannot go backwards")
        self._rebase()
        self._frozen = True
        try:
            target = self._base + timedelta(seconds=seconds)
            while self._sleepers and self._sleepers[0].wake_at <= target:
                sleeper = heapq.heappop(self._sleepers)
                if sleeper.future.done():
                    continue  # it stopped waiting
                self._base = max(self._base, sleeper.wake_at)
                owner = self._owners.get(sleeper.seq)
                sleeper.future.set_result(None)
                if owner is not None and owner is not asyncio.current_task():
                    self._awake.add(owner)
                await self._settle()
            self._base = target
        finally:
            self._real = time.monotonic()
            self._frozen = False
            self._poke()
        await asyncio.sleep(0)

    async def _settle(self) -> None:
        """Until every task this jump woke waits on the clock again (or ends)."""
        loop = asyncio.get_running_loop()
        until = loop.time() + self.SETTLE_S
        await asyncio.sleep(0)
        while self._awake and loop.time() < until:
            self._awake = {t for t in self._awake if not t.done()}
            if self._awake:
                await asyncio.sleep(0.0005 if loop.time() > until - self.SETTLE_S / 2 else 0)
        self._awake.clear()

    def _poke(self) -> None:
        self._changed.set()
        if self._runner is None or self._runner.done():
            self._runner = asyncio.get_running_loop().create_task(self._run(), name="demo-clock")

    async def _run(self) -> None:
        """Wake the sleepers whose moment came; plan again after a jump or another speed."""
        while True:
            self._changed.clear()
            while self._sleepers and self._sleepers[0].future.done():
                heapq.heappop(self._sleepers)
            if self._frozen or self._stopped or not self._sleepers:
                await self._changed.wait()
                continue
            wait = (self._sleepers[0].wake_at - self.now()).total_seconds() / self.speed
            if wait > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._changed.wait(), wait)
                continue
            now = self.now()
            while self._sleepers and self._sleepers[0].wake_at <= now:
                sleeper = heapq.heappop(self._sleepers)
                if not sleeper.future.done():
                    sleeper.future.set_result(None)
            await asyncio.sleep(0)
