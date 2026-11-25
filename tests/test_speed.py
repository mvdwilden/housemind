"""Speed norms (E-157, E-158, H12.1): measured, not guessed.

Slow, so only on request: ``uv run pytest -m slow tests/test_speed.py -s``. To come close to a
Raspberry Pi 4 / HA Green, run it in a container with little processor and memory (B-40):
``tools/pi_like.sh tests/test_speed.py``.

- From a sensor to the command HouseMind gives: at most 100 ms (p95), for a reflex (light on
  when someone comes in) and for an emergency (main valve shut on a leak).
- After a restart, with a month of memory: emergency reactions ready within 30 s, everything
  (the first full round of thinking) within 2 minutes.
"""

from __future__ import annotations

import random
import statistics
import time as timer
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from housemind.brain.proposals import Answer
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source
from housemind.demo.houses import DemoHouse, load_demo_house
from housemind.demo.timemachine import standard_routine
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from tests.helpers import ticks as _ticks

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
START = datetime(2026, 11, 2, 0, 0, tzinfo=TZ)
REFLEX_MS = 100.0
READY_EMERGENCY_S = 30.0
READY_ALL_S = 120.0


def _p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20)[-1] if len(values) >= 20 else max(values)


def _stopwatch(ha: FakeHomeAssistant) -> list[float]:
    """Record the moment of every command HouseMind sends (real time, not house time)."""
    moments: list[float] = []
    original = ha.call_action

    async def timed(*args: Any, **kwargs: Any) -> Any:
        moments.append(timer.perf_counter())
        return await original(*args, **kwargs)

    ha.call_action = timed  # type: ignore[method-assign]
    return moments


async def _live(hm: HouseMind, clock: SimulatedClock, house: DemoHouse, days: int) -> None:
    ha = house.ha
    routine = standard_routine(house, TZ)
    rng = random.Random(3)
    for day in range(days):
        date = START + timedelta(days=day)
        pending = sorted(routine(date.date(), rng))
        until = (date + timedelta(days=1)).astimezone(UTC)
        while clock.now() < until:
            next_tick = clock.now() + timedelta(minutes=5)
            while pending and pending[0].at <= next_tick:
                step = pending.pop(0)
                if step.at > clock.now():
                    await clock.advance((step.at - clock.now()).total_seconds())
                await ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)
            if next_tick > clock.now():
                await clock.advance((next_tick - clock.now()).total_seconds())
            await hm.tick()
            for proposal in await hm.open_proposals():
                await hm.answer(proposal.id, Answer.DO, "demo-user-1")


@pytest.mark.slow
async def test_speed_norms(tmp_path: Path) -> None:
    clock = SimulatedClock(START.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-1", "Resident", 3, clock.now().isoformat()),
    )
    await hm._refresh_people()
    await _live(hm, clock, house, 30)  # a month of memory, as in a real house
    assert hm.learning is not None
    for skill in ("light_on_entry", "light_off_leave"):
        hm.learning.steps[(skill, "hall")] = Step.AUTONOMOUS

    # A reflex: someone walks into the dark hall in the evening (before bedtime).
    await clock.advance(
        (START + timedelta(days=30, hours=17, minutes=30) - clock.now()).total_seconds()
    )
    moments = _stopwatch(ha)
    reflex: list[float] = []
    for _ in range(30):
        await clock.advance(5)  # never two changes of one lamp within 2 s (the gate)
        before = len(moments)
        on_at = clock.now()
        started = timer.perf_counter()
        await ha.simulate("binary_sensor.hall_motion", "on")
        assert len(moments) > before, (
            clock.now().astimezone(TZ),
            [(d.request.skill, d.outcome, d.reason) for d in list(hm.status.thoughts)[-3:]],
        )
        reflex.append((moments[before] - started) * 1000)
        await ha.simulate("binary_sensor.hall_motion", "off")
        for _ in range(30):  # the hall light goes off again after its hold time
            await _ticks(hm, clock, 1)
            if ha.states["light.hall"].state == "off":
                break
        assert ha.states["light.hall"].state == "off"
        while clock.now() - on_at < timedelta(minutes=5, seconds=30):
            await _ticks(hm, clock, 1)  # the same request is not repeated within five minutes

    # An emergency: a leak, the main valve shuts.
    emergency: list[float] = []
    for _ in range(20):
        before = len(moments)
        started = timer.perf_counter()
        await ha.simulate("binary_sensor.utility_leak", "on")
        assert len(moments) > before, "no command on a leak"
        emergency.append((moments[before] - started) * 1000)
        await ha.simulate("binary_sensor.utility_leak", "off")
        await ha.simulate("valve.water_main", "open", by=Source.PERSON)
        await _ticks(hm, clock, 1)
    await hm.stop()

    # A restart with that month of memory.
    clock2 = SimulatedClock(clock.now())
    house2 = load_demo_house("family_home", clock2, "nl")
    started = timer.perf_counter()
    hm2 = HouseMind(clock2, house2.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm2.start(autorun=False)
    ready_emergency = timer.perf_counter() - started
    await clock2.advance(30)
    await hm2.tick()
    ready_all = timer.perf_counter() - started
    await hm2.stop()

    print(
        f"\nreflex  p50 {statistics.median(reflex):6.1f} ms  p95 {_p95(reflex):6.1f} ms  "
        f"max {max(reflex):6.1f} ms"
        f"\nemerg.  p50 {statistics.median(emergency):6.1f} ms  p95 {_p95(emergency):6.1f} ms  "
        f"max {max(emergency):6.1f} ms"
        f"\nrestart emergency ready {ready_emergency:5.2f} s, everything {ready_all:5.2f} s"
    )
    assert _p95(reflex) <= REFLEX_MS
    assert _p95(emergency) <= REFLEX_MS
    assert ready_emergency <= READY_EMERGENCY_S
    assert ready_all <= READY_ALL_S
