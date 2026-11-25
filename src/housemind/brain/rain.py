"""Rain on its way, and something open (E-536, design 94).

When it rains, or will within the hour, while a window or an outside door of the house is open,
everyone gets one important word with what is open: "It will rain within the hour, and the
garden door is open." Raining now is any weather entity saying so; soon is the first hour of
an hourly forecast with rain, a chance of 70% or more, or more than a little. While the house
sleeps, what is open in a bedroom is left out: a window open on purpose at night. The word goes
by itself once all is closed or the rain is over, and comes back at most every three hours.
HouseMind only says it; it closes nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from housemind.core.model import State

RAIN = frozenset({"rainy", "pouring", "lightning-rainy", "snowy-rainy", "hail"})
CHANCE = 70.0  # % – or more
MM = 0.3  # more than a drizzle in the coming hour
WITHIN = timedelta(hours=1)
AGAIN_AFTER = timedelta(hours=3)


def raining_now(states: Mapping[str, State]) -> bool:
    return any(e.startswith("weather.") and s.state in RAIN for e, s in states.items())


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def rain_soon(forecast: Iterable[dict[str, Any]], now: datetime) -> bool:
    """Rain in an hourly forecast, in the hour that is starting or the next."""
    for hour in forecast:
        try:
            at = datetime.fromisoformat(str(hour.get("datetime")))
        except ValueError:
            continue
        if at.tzinfo is None or at + WITHIN <= now or at > now + WITHIN:
            continue
        chance = _number(hour.get("precipitation_probability"))
        mm = _number(hour.get("precipitation"))
        if hour.get("condition") in RAIN:
            return True
        if chance is not None and chance >= CHANCE:
            return True
        if mm is not None and mm >= MM and (chance is None or chance >= 50):
            return True
    return False
