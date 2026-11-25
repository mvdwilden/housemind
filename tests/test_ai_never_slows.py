"""The core never waits for AI (E-414, design 50.1) – the administrator's limit when choosing
more AI: "as long as AI does not take the actual decisions (see the decision trace), because
then switching lights and every other action becomes far too slow; the core of HouseMind stays
in control and must be lightning fast." AI work runs in a queue of its own beside the core.
Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

import asyncio
import time as timer
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

from housemind.ai.providers import AIResult
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from tests.test_ai import FakeAI, enable
from tests.test_speed import START, _stopwatch

ROOT = Path(__file__).resolve().parents[1]
CORE = ("core", "brain", "world", "safety", "knowledge", "energy", "memory")


class HangingAI(FakeAI):
    """An AI that never answers – slower than any real one."""

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        self.sent.append((system, user, model))
        await asyncio.Event().wait()
        raise AssertionError("never")


async def _house(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, Any]:
    clock = SimulatedClock(START.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    return hm, clock, house.ha


async def test_a_hanging_ai_does_not_slow_the_light_in_the_hall(tmp_path: Path) -> None:
    """With AI on and an AI that never answers – a job reading the house and a question to
    Vesta both waiting on it – someone walks into the dark hall: the light command still goes
    out within half a second (E-85)."""
    hm, clock, ha = await _house(tmp_path)
    await enable(hm, HangingAI([]))
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await clock.advance((START + timedelta(hours=17, minutes=30) - clock.now()).total_seconds())
    await hm.tick()
    assert hm.ai_jobs.submit("roles", lambda: hm.ai.suggest_roles(["hall"]))
    asking = asyncio.create_task(hm.ask("Waarom is het licht aan?", "nl", "demo-user-1"))
    await asyncio.sleep(0.05)  # both are now waiting on the AI
    assert "roles" in hm.ai_jobs.busy and not asking.done()
    moments = _stopwatch(ha)
    started = timer.perf_counter()
    await asyncio.wait_for(ha.simulate("binary_sensor.hall_motion", "on"), 0.5)
    assert moments, [d.reason for d in list(hm.status.thoughts)[-3:]]
    assert moments[0] - started < 0.5
    asking.cancel()
    await asyncio.wait_for(hm.stop(), 2)  # stopping does not wait for the AI either


class WritingAI(HangingAI):
    """An AI that can look things up and writes its answer word by word – and never ends."""

    tools = True
    streams = True

    async def converse(self, system: str, turns: Any, model: str, *args: Any, **kw: Any) -> Any:
        self.sent.append((system, turns[0].text, model))
        on_text = kw.get("on_text")
        if on_text is None and len(args) >= 4:
            on_text = args[3]
        await on_text('{"answer": "Even ')
        while True:
            await on_text("kijken ")
            await asyncio.sleep(0.01)


async def test_an_answer_being_written_does_not_slow_the_light_in_the_hall(
    tmp_path: Path,
) -> None:
    """With Vesta's answer coming in word by word to a window (E-159) – an AI that keeps
    writing and never ends – someone walks into the dark hall: the light command still goes
    out within half a second (E-85)."""
    hm, clock, ha = await _house(tmp_path)
    await enable(hm, WritingAI([]))
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await clock.advance((START + timedelta(hours=17, minutes=30) - clock.now()).total_seconds())
    await hm.tick()
    shown: list[str] = []

    async def window(text: str) -> None:
        shown.append(text)

    talking = asyncio.create_task(
        hm.talk("Hoe voelt het huis vandaag aan?", "nl", 3, "demo-user-1", "", on_partial=window)
    )
    await asyncio.sleep(0.1)  # her answer is being written
    assert shown and not talking.done()
    moments = _stopwatch(ha)
    started = timer.perf_counter()
    await asyncio.wait_for(ha.simulate("binary_sensor.hall_motion", "on"), 0.5)
    assert moments, [d.reason for d in list(hm.status.thoughts)[-3:]]
    assert moments[0] - started < 0.5
    talking.cancel()
    await asyncio.wait_for(hm.stop(), 2)


async def test_a_job_that_hangs_is_dropped_and_the_next_one_runs(tmp_path: Path) -> None:
    """A job that takes longer than its limit is dropped, one that fails is noted, and the
    queue goes on with the next. The same kind of work is never queued twice."""
    hm, _clock, _ha = await _house(tmp_path)
    ran: list[str] = []

    async def hang() -> None:
        await asyncio.Event().wait()

    async def fail() -> None:
        raise RuntimeError("the AI said nonsense")

    async def work() -> None:
        ran.append("devices")

    assert hm.ai_jobs.submit("roles", hang, timeout=0.05)
    assert not hm.ai_jobs.submit("roles", hang, timeout=0.05)  # already waiting
    assert hm.ai_jobs.submit("automations", fail)
    assert hm.ai_jobs.submit("devices", work)
    await asyncio.wait_for(hm.ai_jobs.idle(), 2)
    assert ran == ["devices"]
    assert hm.ai_jobs.finished == [
        ("roles", "timeout"),
        ("automations", "failed"),
        ("devices", "done"),
    ]
    assert hm.ai_jobs.busy == []
    await hm.stop()


def test_the_core_does_not_call_the_ai() -> None:
    """The parts that decide – the brain, the world, the gate, the knowledge, energy and the
    memory – do not use the AI; the house's run loop and its reaction to a change do not
    either."""
    for part in CORE:
        for path in (ROOT / "src" / "housemind" / part).rglob("*.py"):
            text = path.read_text()
            assert "housemind.ai" not in text, path
    # The run loop and the reaction to every change: thinking, emergencies, changes in HA.
    for part in ("thinking", "emergencies", "ha_changes"):
        text = (ROOT / "src" / "housemind" / "parts" / f"{part}.py").read_text()
        assert "self.ai" not in text and "ai_jobs" not in text, part
