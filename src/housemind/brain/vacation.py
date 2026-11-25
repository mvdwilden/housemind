"""Looking lived-in while the house is on vacation (E-285, 20.16).

Every evening in the vacation mode HouseMind makes a plan, a little different each time: the
living room's lamps go on when it gets dark (plus 0 to 30 minutes), off around the usual
bedtime (else 23:00, minus 0 to 30 minutes), and then a bedroom's lamps burn for 10 to 30
minutes. Only lamps HouseMind may already switch itself are used; every step still passes
the safety gate. Curtains go as they usually do.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from housemind.world.sun import elevation

DARK_SUN = 3.0  # the sun under 3°: dark enough for light, as learning starts out (E-268)
BEDTIME = time(23, 0)


@dataclass(frozen=True, slots=True)
class Step:
    at: datetime
    entity_id: str
    action: str  # light.turn_on or light.turn_off


def dusk(day: date, tz: Any, place: tuple[float, float] | None) -> datetime:
    """When it gets dark that evening: the sun under 3°, else a rough guess by season."""
    if place is not None:
        moment = datetime.combine(day, time(15, 0), tzinfo=tz)
        end = datetime.combine(day, time(23, 0), tzinfo=tz)
        while moment < end:
            if elevation(place[0], place[1], moment) < DARK_SUN:
                return moment
            moment += timedelta(minutes=5)
        return end
    hour = 17 if day.month in (11, 12, 1) else 18 if day.month in (2, 10) else 20
    return datetime.combine(day, time(hour, 0), tzinfo=tz)


def evening_plan(
    dark: datetime,
    bedtime: datetime,
    living: list[str],
    bedroom: list[str],
    rng: random.Random,
) -> list[Step]:
    """Tonight's plan: living room lamps from dark to bedtime, then a bedroom's briefly."""
    on = dark + timedelta(minutes=rng.randint(0, 30))
    off = bedtime - timedelta(minutes=rng.randint(0, 30))
    if off <= on + timedelta(minutes=30):
        off = on + timedelta(minutes=30 + rng.randint(0, 30))
    steps = [Step(on, e, "light.turn_on") for e in living]
    steps += [Step(off, e, "light.turn_off") for e in living]
    if bedroom:
        upstairs = off + timedelta(minutes=rng.randint(0, 3))
        steps += [Step(upstairs, e, "light.turn_on") for e in bedroom]
        later = upstairs + timedelta(minutes=rng.randint(10, 30))
        steps += [Step(later, e, "light.turn_off") for e in bedroom]
    return sorted(steps, key=lambda s: (s.at, s.entity_id))
