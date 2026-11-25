"""Afleiden uit gedrag (E-203, design 50.2, B-358): what a device is, from what it did.

Without AI and also when AI is off, from what Home Assistant's recorder kept – read only, it
stays in the house (E-15) –, as a suggestion with a certainty:

- **an appliance**: a plug or switch whose power sensor runs one to three hours at about
  2 kW, more than once, is probably a washing machine or dishwasher;
- **with a lamp**: a switch that (almost) always goes on and off together with a lamp
  belongs to that light – counting only real switching, never a restart (E-459);
- **light**: a sensor that goes dark at sunset, day after day, measures light.

Only what the catalogue knows comes out (a switch's role, the class of a sensor), with that
kind's own limits (E-58). The decisions stay with the core: a role nobody confirmed is watched
and learned, never proposed or done (50.8). These are plain functions over the history; the
slow job that reads it is ``parts/understanding.py``.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from housemind.world.sun import elevation

# An appliance (washing machine, dishwasher): below IDLE_W it is off; a pause in its program
# (soaking, drying) shorter than PAUSE is the same run.
IDLE_W = 10.0
PAUSE = timedelta(minutes=15)
SHORT = timedelta(minutes=10)  # a blip, not a run
RUN = (timedelta(hours=1), timedelta(hours=3))
PEAK_W = (1500.0, 3000.0)  # "about 2 kW": it heats water
RUNS = 2  # at least this many runs that fit, and at least half of all its runs
# A switch with a lamp: within TOGETHER of each other, at least TIMES changes, nearly all.
TOGETHER = timedelta(seconds=10)
TIMES = 6
NEARLY_ALL = 0.9
# A sensor that measures light: on most days much darker after sunset than before it.
SUNSET = -0.83  # degrees: the sun under the horizon, with the air's refraction
BEFORE = (timedelta(hours=2), timedelta(minutes=30))  # from 2 h to 30 min before sunset
AFTER = (timedelta(minutes=45), timedelta(hours=2))  # from 45 min to 2 h after it
LIGHT_MIN = 5.0  # some light before sunset at all
DARKER = 0.25  # after sunset at most a quarter of before
DAYS = 3
MOST_DAYS = 0.8
SAMPLE = timedelta(minutes=10)
SURE = 0.95  # never quite sure: a person says Klopt


@dataclass(slots=True)
class Runs:
    """The runs of a power sensor, fed in order, as its value held: from rising above IDLE_W
    until it stays below that for PAUSE. Kept between windows of the history, so a run over
    midnight is one run."""

    runs: list[tuple[timedelta, float]] = field(default_factory=list)  # (length, peak watts)
    _start: datetime | None = None
    _off: datetime | None = None  # since when below IDLE_W, during a run
    _peak: float = 0.0

    def feed(self, at: datetime, watts: float | None) -> None:
        if self._off is not None and at - self._off >= PAUSE:
            self._close()
        if watts is not None and watts > IDLE_W:
            if self._start is None:
                self._start, self._peak = at, 0.0
            self._off = None
            self._peak = max(self._peak, watts)
        elif self._start is not None and self._off is None:
            self._off = at

    def end(self, at: datetime) -> None:
        """The end of what was read: a run still going is counted the next time."""
        if self._off is not None and at - self._off >= PAUSE:
            self._close()

    def _close(self) -> None:
        if self._start is not None and self._off is not None and self._off - self._start >= SHORT:
            self.runs.append((self._off - self._start, self._peak))
        self._start = self._off = None


def appliance(runs: Sequence[tuple[timedelta, float]]) -> tuple[float, dict[str, float]] | None:
    """A washing machine or dishwasher, from its runs: (certainty, what it saw) or None."""
    fit = [(length, peak) for length, peak in runs if _fits(length, peak)]
    if len(fit) < RUNS or 2 * len(fit) < len(runs):
        return None
    hours = sum(length.total_seconds() for length, _ in fit) / len(fit) / 3600
    kw = sum(peak for _, peak in fit) / len(fit) / 1000
    certainty = min(0.9, 0.5 + 0.1 * len(fit)) * len(fit) / len(runs)
    return round(certainty, 2), {"runs": len(fit), "hours": round(hours, 1), "kw": round(kw, 1)}


def _fits(length: timedelta, peak: float) -> bool:
    return RUN[0] <= length <= RUN[1] and PEAK_W[0] <= peak <= PEAK_W[1]


def changes_of(rows: Iterable[tuple[datetime, str]], since: datetime) -> list[tuple[datetime, str]]:
    """The real changes after ``since``: the state at the start of a window is none."""
    out: list[tuple[datetime, str]] = []
    last: str | None = None
    for at, state in rows:
        if at > since and state != last and last is not None:
            out.append((at, state))
        last = state
    return out


def flips(rows: Iterable[tuple[datetime, str]], since: datetime) -> list[tuple[datetime, str]]:
    """A switch's real switching after ``since``: from on to off or from off to on (E-459).
    A restart of Home Assistant or of the radio – unavailable or unknown, then back to what it
    was – is no switching: at a restart every switch and lamp comes back at the same moment."""
    out: list[tuple[datetime, str]] = []
    last: str | None = None
    for at, state in rows:
        if at > since and state in ("on", "off") and last in ("on", "off") and state != last:
            out.append((at, state))
        last = state
    return out


def with_lamp(
    switch: Sequence[tuple[datetime, str]], lamps: Mapping[str, Sequence[tuple[datetime, str]]]
) -> tuple[str, float, int] | None:
    """The lamp a switch (almost) always changes together with: (lamp, share, changes).
    ``switch`` is its real switching (``flips``)."""
    changes = [(at, state) for at, state in switch if state in ("on", "off")]
    if len(changes) < TIMES:
        return None
    best: tuple[str, float, int] | None = None
    for lamp, rows in sorted(lamps.items()):
        together = sum(1 for at, state in changes if _together(at, state, rows))
        share = together / len(changes)
        if share >= NEARLY_ALL and (best is None or share > best[1]):
            best = (lamp, round(share, 2), len(changes))
    return best


def _together(at: datetime, state: str, rows: Sequence[tuple[datetime, str]]) -> bool:
    wanted = ("on",) if state == "on" else ("off", "unavailable")
    return any(abs(when - at) <= TOGETHER and lamp in wanted for when, lamp in rows)


def follows_sunset(
    values: Sequence[tuple[datetime, float]], place: tuple[float, float], end: datetime
) -> tuple[float, dict[str, int]] | None:
    """A sensor that is much darker after sunset than before, on most days: it measures
    light – (certainty, on how many of how many days) or None."""
    if not values:
        return None
    fit = days = 0
    for sunset in _sunsets(place, values[0][0], end):
        before = _mean(values, sunset - BEFORE[0], sunset - BEFORE[1])
        after = _mean(values, sunset + AFTER[0], sunset + AFTER[1])
        if before is None or after is None:
            continue
        days += 1
        if before >= LIGHT_MIN and after <= DARKER * before:
            fit += 1
    if days < DAYS or fit < MOST_DAYS * days:
        return None
    certainty = min(SURE, fit / days * min(1.0, fit / 4))
    return round(certainty, 2), {"days": fit, "of": days}


def _sunsets(place: tuple[float, float], start: datetime, end: datetime) -> list[datetime]:
    """When the sun went under, each day between start and end, to about five minutes."""
    found: list[datetime] = []
    step = timedelta(minutes=5)
    at, up = start, elevation(*place, start) > SUNSET
    while at < end:
        at += step
        now_up = elevation(*place, at) > SUNSET
        if up and not now_up:
            found.append(at)
        up = now_up
    return found


def _mean(values: Sequence[tuple[datetime, float]], start: datetime, end: datetime) -> float | None:
    """The average of a sensor's value between two moments, as it held (a step each change)."""
    times = [at for at, _ in values]
    seen: list[float] = []
    at = start
    while at <= end:
        index = bisect_right(times, at) - 1
        if index >= 0:
            seen.append(values[index][1])
        at += SAMPLE
    return sum(seen) / len(seen) if seen else None
