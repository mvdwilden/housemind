"""The time machine (E-99): play weeks of house life in seconds on a simulated clock.

A *routine* produces the events of one day (people waking up, wall switches, leaving,
coming home). The time machine sorts them, moves the simulated clock to each moment and lets
the simulated Home Assistant emit the change – exactly as a real house would.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from housemind.core.clock import SimulatedClock
from housemind.core.model import Source
from housemind.demo.houses import DemoHouse
from housemind.ha.fake import FakeHomeAssistant


@dataclass(frozen=True, slots=True, order=True)
class ScenarioStep:
    at: datetime
    entity_id: str = field(compare=False)
    state: str = field(compare=False)
    attributes: dict[str, Any] = field(default_factory=dict, compare=False)
    by: Source = field(default=Source.PHYSICAL, compare=False)


Routine = Callable[[date, random.Random], list[ScenarioStep]]


@dataclass(slots=True)
class RunStats:
    days: int = 0
    steps: int = 0
    skipped: int = 0


class TimeMachine:
    def __init__(self, clock: SimulatedClock, ha: FakeHomeAssistant) -> None:
        self.clock = clock
        self.ha = ha

    async def play(self, steps: Iterable[ScenarioStep]) -> RunStats:
        stats = RunStats()
        for step in sorted(steps):
            delta = (step.at - self.clock.now()).total_seconds()
            if delta < 0:
                stats.skipped += 1
                continue
            await self.clock.advance(delta)
            if step.entity_id not in self.ha.states:
                stats.skipped += 1
                continue
            await self.ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)
            stats.steps += 1
        return stats

    async def run_days(self, days: int, routine: Routine, seed: int = 1) -> RunStats:
        rng = random.Random(seed)  # noqa: S311 - simulation, not security
        total = RunStats()
        start = self.clock.now().date()
        for offset in range(days):
            day = start + timedelta(days=offset)
            stats = await self.play(routine(day, rng))
            total.days += 1
            total.steps += stats.steps
            total.skipped += stats.skipped
        end = datetime.combine(
            start + timedelta(days=days), time(0, 0), tzinfo=self.clock.now().tzinfo
        )
        remaining = (end - self.clock.now()).total_seconds()
        if remaining > 0:
            await self.clock.advance(remaining)
        return total


def _dusk_hour(day: date) -> int:
    """When it gets dark, by season – the same rough guess the world view uses without sun."""
    return 17 if day.month in (11, 12, 1) else 18 if day.month in (2, 10) else 20


def _at(day: date, hh: int, mm: int, tz: Any, jitter_min: int, rng: random.Random) -> datetime:
    base = datetime.combine(day, time(hh, mm), tzinfo=tz)
    return base + timedelta(minutes=rng.randint(-jitter_min, jitter_min))


def standard_routine(house: DemoHouse, tz: Any) -> Routine:
    """A plausible weekday/weekend rhythm for any demo house, built from what it has."""
    entities = set(house.ha.states)

    def has(entity_id: str) -> bool:
        return entity_id in entities

    def routine(day: date, rng: random.Random) -> list[ScenarioStep]:
        weekend = day.weekday() >= 5
        steps: list[ScenarioStep] = []
        wake = _at(day, 8 if weekend else 6, 45, tz, 20, rng)

        def add(when: datetime, entity_id: str, state: str, by: Source = Source.PHYSICAL) -> None:
            if has(entity_id):
                steps.append(ScenarioStep(when, entity_id, state, by=by))

        # The phone's alarm: on workdays someone sets it for the time they get up (E-88).
        alarm = wake.astimezone(UTC).isoformat() if not weekend else "unavailable"
        add(datetime.combine(day, time(0, 1), tzinfo=tz), "sensor.phone_1_next_alarm", alarm)
        add(wake + timedelta(minutes=1), "sensor.phone_1_next_alarm", "unavailable")
        # A shower: the bathroom gets humid for a while.
        add(wake + timedelta(minutes=2), "sensor.bathroom_humidity", "84")
        add(wake + timedelta(minutes=30), "sensor.bathroom_humidity", "68")
        add(wake + timedelta(minutes=50), "sensor.bathroom_humidity", "57")
        # Whoever switches the bedroom lamp is there: its sensor sees them (E-463).
        add(wake, "binary_sensor.bedroom_motion", "on")
        add(wake, "light.bedroom", "on")
        add(wake + timedelta(minutes=10), "light.bedroom", "off")
        add(wake + timedelta(minutes=10), "binary_sensor.bedroom_motion", "off")
        add(wake + timedelta(minutes=11), "binary_sensor.hall_motion", "on")
        add(wake + timedelta(minutes=12), "light.hall", "on")
        add(wake + timedelta(minutes=13), "binary_sensor.hall_motion", "off")
        kitchen_presence = (
            "binary_sensor.kitchen_presence"
            if has("binary_sensor.kitchen_presence")
            else "binary_sensor.kitchen_motion"
        )
        add(wake + timedelta(minutes=15), kitchen_presence, "on")
        add(wake + timedelta(minutes=15), "light.kitchen", "on")
        add(wake + timedelta(minutes=16), "light.hall", "off")
        add(wake + timedelta(minutes=40), "light.kitchen", "off")
        add(wake + timedelta(minutes=41), kitchen_presence, "off")
        # Curtains open after getting up (not before 7:00 on workdays, 8:30 on days off).
        not_before = datetime.combine(day, time(8, 30) if weekend else time(7, 0), tzinfo=tz)
        add(
            max(wake + timedelta(minutes=15), not_before) + timedelta(minutes=rng.randint(0, 5)),
            "cover.living_curtain",
            "open",
        )
        home_again = None
        if not weekend:
            leave = wake + timedelta(minutes=50)
            add(leave, "binary_sensor.front_door", "on")
            add(leave + timedelta(minutes=1), "binary_sensor.front_door", "off")
            add(leave + timedelta(minutes=2), "person.resident_1", "not_home", Source.UNKNOWN)
            back = _at(day, 17, 45, tz, 30, rng)
            home_again = back
            add(back, "person.resident_1", "home", Source.UNKNOWN)
            add(back + timedelta(minutes=1), "binary_sensor.front_door", "on")
            add(back + timedelta(minutes=2), "binary_sensor.front_door", "off")
        # Curtains close soon after it gets dark, once someone is home. Whoever closes them is
        # in the living room, so the evening there starts then at the latest.
        dusk = datetime.combine(day, time(_dusk_hour(day), 0), tzinfo=tz)
        if home_again is not None:
            dusk = max(dusk, home_again + timedelta(minutes=5))
        close = dusk + timedelta(minutes=rng.randint(5, 15))
        evening = min(_at(day, 19, 30, tz, 20, rng), close - timedelta(minutes=2))
        add(evening, "binary_sensor.living_presence", "on")
        add(evening + timedelta(minutes=1), "light.living_ceiling", "on")
        add(close, "cover.living_curtain", "closed")
        bed = _at(day, 22, 45, tz, 25, rng)
        add(bed, "light.living_ceiling", "off")
        add(bed + timedelta(minutes=1), "binary_sensor.living_presence", "off")
        add(bed + timedelta(minutes=2), "binary_sensor.bedroom_motion", "on")
        add(bed + timedelta(minutes=2), "light.bedroom", "on")
        add(bed + timedelta(minutes=20), "light.bedroom", "off")
        add(bed + timedelta(minutes=20), "binary_sensor.bedroom_motion", "off")
        return steps

    return routine
