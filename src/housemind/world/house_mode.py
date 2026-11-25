"""House mode: home, away, sleep, vacation, guests (H3.1, E-52).

Rules (simple, explainable, tunable later by learning):
- away: no resident home (phones) and no human occupancy anywhere for 10 minutes;
- vacation: away for more than 24 hours (offered as a proposal, not forced);
- guests: occupancy while no resident is home for more than 3 minutes – could also be
  someone unexpected, so it is reported, and treated carefully (E-52);
- sleep: at night, residents home, no activity in living areas for 20 minutes and the
  bedroom lights off; wake-up on activity outside bedrooms after 05:00;
- a person may set the mode by hand; that holds until the next clear transition;
- the alarm (E-188, 16.6): armed away is a strong sign for away – at once, without the ten
  minutes – and armed night for sleep; while it is armed for the night the house stays
  asleep.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class HouseMode(StrEnum):
    HOME = "home"
    AWAY = "away"
    SLEEP = "sleep"
    VACATION = "vacation"
    GUESTS = "guests"


@dataclass(frozen=True, slots=True)
class ModeInputs:
    now_local: datetime
    residents_home: int
    occupied_areas: frozenset[str]
    living_activity: datetime | None  # last activity in non-bedroom areas
    bedroom_lights_on: bool
    alarm: str | None = None  # the state of "the alarm", if the house has one


@dataclass(slots=True)
class HouseModeMachine:
    mode: HouseMode = HouseMode.HOME
    since: datetime | None = None
    manual: bool = False
    _empty_since: datetime | None = None
    _strangers_since: datetime | None = None
    suggest_vacation: bool = False
    guests_unconfirmed: bool = False
    _manual_residents: int | None = None
    # Residents without a phone of their own in HA, such as children (E-293): someone inside
    # while every phone is away is someone at home, not an unexpected visitor.
    phoneless: bool = False
    # "Zijn jullie op vakantie?" was answered no: not asked again until someone is home (E-286).
    vacation_declined: bool = False

    def set_manual(self, mode: HouseMode, now: datetime) -> None:
        """A person chose the mode; it holds until who is home changes (or 12 hours).
        Vacation then lasts until a resident comes home (E-286)."""
        self.mode, self.since, self.manual = mode, now, True
        self.guests_unconfirmed = False
        self._manual_residents = None
        if mode is HouseMode.VACATION:
            self.suggest_vacation = False

    def _on_vacation(self, i: ModeInputs) -> bool:
        """Vacation lasts until a resident comes home (E-286) – not when the last ones leave
        after it was turned on. Someone inside while no resident is home is reported."""
        now = i.now_local
        before = self._manual_residents
        self._manual_residents = i.residents_home
        if before is not None and i.residents_home > before:
            self.manual, self._manual_residents = False, None
            self.guests_unconfirmed = False
            return self._switch(HouseMode.HOME, now)
        if i.residents_home == 0 and i.occupied_areas:
            self._strangers_since = self._strangers_since or now
            if now - self._strangers_since >= timedelta(minutes=3):
                self.guests_unconfirmed = True
        else:
            self._strangers_since = None
        return False

    def _switch(self, mode: HouseMode, now: datetime) -> bool:
        if mode is self.mode:
            return False
        self.mode, self.since, self.manual = mode, now, False
        return True

    def update(self, i: ModeInputs) -> bool:
        """Re-evaluate; returns True when the mode changed."""
        now = i.now_local
        if self.mode is HouseMode.VACATION:
            return self._on_vacation(i)
        if self.manual:
            if self._manual_residents is None:
                self._manual_residents = i.residents_home
            expired = self.since is not None and now - self.since.astimezone(
                now.tzinfo
            ) >= timedelta(hours=12)
            if i.residents_home == self._manual_residents and not expired:
                return False
            self.manual = False
        anyone_inside = bool(i.occupied_areas)
        residents = i.residents_home or (1 if self.phoneless and anyone_inside else 0)
        # Empty house
        if residents == 0 and not anyone_inside:
            self._strangers_since = None
            self._empty_since = self._empty_since or now
            if self.mode in (HouseMode.HOME, HouseMode.SLEEP, HouseMode.GUESTS):
                armed = i.alarm == "armed_away"  # a strong sign: no need to wait (16.6)
                if armed or now - self._empty_since >= timedelta(minutes=10):
                    return self._switch(HouseMode.AWAY, now)
            if (
                self.mode is HouseMode.AWAY
                and self.since
                and now - self.since >= timedelta(hours=24)
                and not self.vacation_declined
            ):
                self.suggest_vacation = True
            return False
        self._empty_since = None
        # Someone inside but no resident home
        if residents == 0 and anyone_inside:
            self._strangers_since = self._strangers_since or now
            if self.mode is not HouseMode.GUESTS and now - self._strangers_since >= timedelta(
                minutes=3
            ):
                self.guests_unconfirmed = not self.manual
                return self._switch(HouseMode.GUESTS, now)
            return False
        self._strangers_since = None
        self.suggest_vacation = False
        self.vacation_declined = False
        # Residents home
        if self.mode in (HouseMode.AWAY, HouseMode.VACATION, HouseMode.GUESTS):
            self.guests_unconfirmed = False
            return self._switch(HouseMode.HOME, now)
        hour = now.hour
        night = hour >= 21 or hour < 5
        quiet = i.living_activity is None or now - i.living_activity >= timedelta(minutes=20)
        if self.mode is HouseMode.HOME and i.alarm == "armed_night":
            return self._switch(HouseMode.SLEEP, now)  # armed for the night: asleep (16.6)
        if self.mode is HouseMode.HOME and night and quiet and not i.bedroom_lights_on:
            return self._switch(HouseMode.SLEEP, now)
        if (
            self.mode is HouseMode.SLEEP
            and i.alarm != "armed_night"
            and hour >= 5
            and i.living_activity is not None
            and now - i.living_activity < timedelta(minutes=2)
        ):
            return self._switch(HouseMode.HOME, now)
        return False
