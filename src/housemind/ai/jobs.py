"""The slow loop (E-414, design 50.1): AI work in a queue of its own, beside the core.

The core never waits for it. Sensor, brain, gate and action run without AI; what the core uses
of AI is data that is already there (the house knowledge). Reading the house, explaining and
proposing go through this queue, one job at a time, each with a time limit: a job that fails
or hangs is dropped and the next one runs, and stopping HouseMind never waits for an AI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

_LOGGER = logging.getLogger(__name__)

TIMEOUT = 120.0  # seconds a job may take, its AI questions included
KEEP = 20  # how many finished jobs Expert can see


@dataclass(frozen=True)
class Job:
    kind: str  # one of each kind waits at a time: "roles", "devices", "automations" …
    run: Callable[[], Awaitable[object]]
    timeout: float = TIMEOUT


class AIJobs:
    """One worker, beside the core; started with HouseMind, stopped without waiting."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._kinds: set[str] = set()  # waiting or running
        self.finished: list[tuple[str, str]] = []  # (kind, "done" | "timeout" | "failed")

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._work(), name="housemind-ai-jobs")

    def submit(
        self, kind: str, run: Callable[[], Awaitable[object]], timeout: float = TIMEOUT
    ) -> bool:
        """Queue a job and return at once; False when one of this kind is already waiting or
        running, so that the same work never piles up."""
        if kind in self._kinds:
            return False
        self._kinds.add(kind)
        self._queue.put_nowait(Job(kind, run, timeout))
        return True

    @property
    def busy(self) -> list[str]:
        return sorted(self._kinds)

    async def _work(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await asyncio.wait_for(job.run(), job.timeout)
                outcome = "done"
            except TimeoutError:
                _LOGGER.warning("AI job %s took longer than %.0f s; dropped", job.kind, job.timeout)
                outcome = "timeout"
            except Exception:  # an AI job never takes the house down
                _LOGGER.exception("AI job %s failed", job.kind)
                outcome = "failed"
            finally:
                self._kinds.discard(job.kind)
                self._queue.task_done()
            self.finished = [*self.finished[-(KEEP - 1) :], (job.kind, outcome)]

    async def idle(self) -> None:
        """Until every queued job is done (for tests and the time machine)."""
        await self._queue.join()

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._worker
        self._worker = None
