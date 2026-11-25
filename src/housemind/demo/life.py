"""A living demo house (E-48): the residents follow a daily rhythm in real time.

The same routine as the time machine, but played on the real clock, so someone trying the demo
sees HouseMind notice, propose and act during the day.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from datetime import timedelta
from zoneinfo import ZoneInfo

from housemind.core.clock import Clock
from housemind.demo.energy_sim import EnergySim
from housemind.demo.houses import DemoHouse
from housemind.demo.timemachine import standard_routine

_LOGGER = logging.getLogger(__name__)


def energy_sim(house: DemoHouse, time_zone: str) -> EnergySim | None:
    """The demo house's sun, use, battery, car and prices – only with energy settings."""
    return EnergySim(house.ha, ZoneInfo(time_zone)) if house.ha.energy else None


async def simulate_energy(
    house: DemoHouse, clock: Clock, time_zone: str, sim: EnergySim | None = None
) -> None:
    """The demo energy house: sun, use, battery, car and prices move every minute."""
    sim = sim or energy_sim(house, time_zone)
    if sim is None:
        return
    while True:
        try:
            await sim.step(clock.now())
        except Exception:
            _LOGGER.exception("Demo energy step failed")
        await clock.sleep(60)


async def live(
    house: DemoHouse,
    clock: Clock,
    time_zone: str,
    seed: int | None = None,
    held: Callable[[], bool] | None = None,
) -> None:
    """``held``: while it says yes, the residents stay as 'Speel het huis' left them (a long
    time away, E-321) – their rhythm goes on without them."""
    tz = ZoneInfo(time_zone)
    routine = standard_routine(house, tz)
    rng = random.Random(seed)  # noqa: S311 - demo behaviour, not security
    day = clock.now().astimezone(tz).date()
    while True:
        for step in sorted(routine(day, rng)):
            delay = (step.at - clock.now()).total_seconds()
            if delay < 0:
                continue  # earlier today: already past
            await clock.sleep(delay)
            if held is not None and held():
                continue
            if step.entity_id in house.ha.states:
                try:
                    await house.ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)
                except Exception:
                    _LOGGER.exception("Demo step failed")
        day += timedelta(days=1)
