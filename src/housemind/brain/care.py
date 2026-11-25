"""Care and wellbeing (domain 17, experimental): unusual quiet in the house, told to a chosen
contact – only after an explicit yes (E-21).

When the person it is about is home and nothing moves in the house – two hours past the usual
wake-up time in the morning (or 10:00 without a known rhythm), or six hours during the day –
the contact person gets one message. HouseMind does nothing else; the message disappears as
soon as there is movement again. Pets are no people (E-90): occupancy already accounts for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

MORNING_GRACE = timedelta(hours=2)
NO_RHYTHM_BY = time(10, 0)
DAY_QUIET = timedelta(hours=6)
DAY_FROM, DAY_UNTIL = time(8, 0), time(22, 0)


@dataclass(frozen=True, slots=True)
class CareSettings:
    person: str  # the HA person it is about
    contact: str  # the HA user who gets the message
    consent: bool  # an explicit yes (E-21)

    @classmethod
    def from_json(cls, raw: Any) -> CareSettings | None:
        if not isinstance(raw, dict):
            return None
        person, contact = raw.get("person"), raw.get("contact")
        if not isinstance(person, str) or not isinstance(contact, str):
            return None
        return cls(person, contact, raw.get("consent") is True)


def quiet_reason(
    local: datetime,
    last_activity: datetime | None,
    started: datetime,
    usual_wake: time | None,
) -> str | None:
    """ "morning" or "day" when the house has been unusually quiet, else None."""
    since = last_activity or started
    quiet_for = local - since.astimezone(local.tzinfo)
    morning_by = (
        (datetime.combine(local.date(), usual_wake) + MORNING_GRACE).time()
        if usual_wake is not None
        else NO_RHYTHM_BY
    )
    morning_start = datetime.combine(local.date(), time(4, 0), tzinfo=local.tzinfo)
    if local.time() >= morning_by and since < morning_start and local.time() < time(14, 0):
        return "morning"  # nothing moved yet this morning
    # During the day: six quiet hours after something did move this morning (a quiet morning
    # is the morning case above, not this one).
    if DAY_FROM <= local.time() < DAY_UNTIL and quiet_for >= DAY_QUIET and since >= morning_start:
        return "day"
    return None
