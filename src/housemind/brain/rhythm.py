"""Sleeping and waking up (E-88): the rhythm of the house and the alarm of the phone.

HouseMind learns, per type of day, when the house goes to sleep and wakes up – from the house
mode, so from what really happens, not from a setting. A phone's next alarm (from the HA
companion app) wins over the learned time. Only a clear rhythm counts: with too few nights or
nights that differ a lot, there is no usual time and HouseMind does not wake anyone.

A bedtime belongs to the *next* day: on Sunday evening the house goes to bed for a workday.
Days that differ from normal (E-89) are not learned.

The same way it learns when the house empties in the morning and fills again (E-366): only a
day away of at least two hours counts, so a trip to the shop is no workday. From the four
times it draws the day (``day``): sleeping, the morning, away and at home. It also keeps the
last vacation and the last guests (E-367), and says per time how far it is (``status``, E-368).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from housemind.core.model import HouseModel, State
from housemind.knowledge.words import has_word as words_have
from housemind.world.calendar import DayType
from housemind.world.house_mode import HouseMode

KEEP = 21  # nights per day type
MIN_NIGHTS = 3
MAX_SPREAD_MIN = 45  # the middle half of the nights lies within this many minutes
UNSURE_AT_LEAST = 15  # a time still being learned is unsure at least this long either side
WAKE_EARLIEST, WAKE_LATEST = 3, 12  # a wake-up time lies between 03:00 and 12:00
LEAVE_EARLIEST, LEAVE_LATEST = 5, 13  # the house empties for the day between 05:00 and 13:00
BACK_EARLIEST, BACK_LATEST = 12, 23  # and fills again between 12:00 and 23:00
AWAY_AT_LEAST = timedelta(hours=2)  # shorter is a trip to the shop, not a day away
MORNING_AT_MOST = 240  # minutes from getting up to leaving that are still the morning
MORNING_HOME = timedelta(hours=3)  # the morning of a day people stay home
LATE = timedelta(hours=2)  # still up this long after the usual bedtime: night all the same
EVENING_EARLIEST, EVENING_LATEST = 17, 20  # coming home in this window starts the evening
# The clock, while a time of the rhythm is not known yet (as before E-455).
CLOCK_MORNING, CLOCK_DAY, CLOCK_EVENING, CLOCK_NIGHT = time(6), time(9), time(17), time(23)
ALARM_AHEAD = timedelta(hours=16)
# Android tells which app set the next alarm; calendar reminders are not an alarm.
ALARM_APPS = ("clock", "alarm", "sleep", "wecker", "reveil", "wekker")


def _minutes(moment: datetime, noon: bool) -> int:
    """Minutes since midnight, or since noon for bedtimes (so 23:30 and 00:30 stay close)."""
    minutes = moment.hour * 60 + moment.minute
    return (minutes - 720) % 1440 if noon else minutes


def _median(values: list[int]) -> int:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _spread(values: list[int]) -> int:
    ordered = sorted(values)
    quarter = len(ordered) // 4
    return ordered[-1 - quarter] - ordered[quarter]


def _clock(minutes: int, noon: bool) -> time:
    minutes = (minutes + 720) % 1440 if noon else minutes
    return time(minutes // 60, minutes % 60)


@dataclass(frozen=True, slots=True)
class Status:
    """How far one time is (E-368): how many days, the usual time when it is clear, how many
    days lie close to it, and – when the days still differ a lot – their middle half."""

    count: int
    usual: time | None
    close: int
    low: time | None = None
    high: time | None = None


@dataclass(frozen=True, slots=True)
class Part:
    """The part of the day in this house now (E-455): night, morning, day or evening, since
    when, and how HouseMind knows it – the house sleeps ("sleep"), it woke ("woke"), the learned
    rhythm ("rhythm"), long past the usual bedtime ("late") or the clock ("clock"). ``bedtime``:
    after the usual bedtime and before the morning – then no lamp goes on in a bedroom, also
    while someone else is still up."""

    kind: str
    since: datetime
    how: str
    bedtime: bool


@dataclass(slots=True)
class Rhythm:
    bed: dict[str, list[int]] = field(default_factory=dict)  # day type → minutes after noon
    wake: dict[str, list[int]] = field(default_factory=dict)  # day type → minutes after 0:00
    frozen: dict[str, str] = field(default_factory=dict)  # day → planned wake time (ISO)
    # One-off "yes" from the evening question: skill → day it holds for (ISO).
    granted: dict[str, str] = field(default_factory=dict)
    leave: dict[str, list[int]] = field(default_factory=dict)  # the house empties (minutes)
    back: dict[str, list[int]] = field(default_factory=dict)  # and fills again (minutes)
    away_from: str = ""  # when the house went away, until it is back (ISO)
    seen: dict[str, str] = field(default_factory=dict)  # "leave"/"back" → the day it counted
    periods: dict[str, list[str]] = field(default_factory=dict)  # the last vacation, guests

    # -- learning -----------------------------------------------------------------------

    def record(
        self, old: HouseMode, new: HouseMode, local: datetime, today: DayType, tomorrow: DayType
    ) -> bool:
        """A change of house mode; returns True when it taught something."""
        if old is HouseMode.HOME and new is HouseMode.SLEEP:
            evening_after_midnight = local.hour < WAKE_EARLIEST + 3
            next_day = today if evening_after_midnight else tomorrow
            if next_day is DayType.DEVIANT:
                return False
            self._add(self.bed, next_day.value, _minutes(local, noon=True))
            return True
        if old is HouseMode.SLEEP and new is HouseMode.HOME:
            if today is DayType.DEVIANT or not WAKE_EARLIEST <= local.hour < WAKE_LATEST:
                return False
            self._add(self.wake, today.value, _minutes(local, noon=False))
            return True
        if old is HouseMode.HOME and new is HouseMode.AWAY:
            self.away_from = local.isoformat()
            return False
        if old is HouseMode.AWAY and new is HouseMode.HOME:
            return self._back(local, today)
        return False

    def _back(self, local: datetime, today: DayType) -> bool:
        """Home again: a day away of at least two hours teaches when the house empties and
        fills; only the first leaving of the morning, and the last coming home of the day."""
        start = datetime.fromisoformat(self.away_from) if self.away_from else None
        self.away_from = ""
        if start is None or today is DayType.DEVIANT or start.date() != local.date():
            return False
        if local - start < AWAY_AT_LEAST:
            return False
        day = local.date().isoformat()
        taught = False
        if LEAVE_EARLIEST <= start.hour < LEAVE_LATEST and self.seen.get("leave") != day:
            self._add(self.leave, today.value, _minutes(start, noon=False))
            self.seen["leave"] = day
            taught = True
        if self.seen.get("leave") == day and BACK_EARLIEST <= local.hour < BACK_LATEST:
            if self.seen.get("back") == day and self.back.get(today.value):
                self.back[today.value][-1] = _minutes(local, noon=False)
            else:
                self._add(self.back, today.value, _minutes(local, noon=False))
            self.seen["back"] = day
            taught = True
        return taught

    def mark(self, old: HouseMode, new: HouseMode, local: datetime) -> bool:
        """The last vacation and the last guests, from when to when (E-367)."""
        changed = False
        for kind, mode in (("vacation", HouseMode.VACATION), ("guests", HouseMode.GUESTS)):
            if new is mode and old is not mode:
                self.periods[kind] = [local.isoformat(), ""]
                changed = True
            elif old is mode and new is not mode and kind in self.periods:
                self.periods[kind][1] = local.isoformat()
                changed = True
        return changed

    @staticmethod
    def _add(table: dict[str, list[int]], key: str, value: int) -> None:
        table.setdefault(key, []).append(value)
        del table[key][:-KEEP]

    def _table(self, kind: str) -> dict[str, list[int]]:
        return {"bed": self.bed, "wake": self.wake, "leave": self.leave, "back": self.back}[kind]

    def usual(self, kind: str, day: DayType) -> time | None:
        """The usual time of ``kind`` (bed, wake, leave, back) for this type of day – only when
        it is clear."""
        values = self._table(kind).get(day.value, [])
        if len(values) < MIN_NIGHTS or _spread(values) > MAX_SPREAD_MIN:
            return None
        return _clock(_median(values), noon=kind == "bed")

    def status(self, kind: str, day: DayType) -> Status:
        """How far HouseMind is with one time (E-368)."""
        values = self._table(kind).get(day.value, [])
        noon = kind == "bed"
        if not values:
            return Status(0, None, 0)
        middle = _median(values)
        close = sum(1 for v in values if abs(v - middle) <= MAX_SPREAD_MIN)
        usual = self.usual(kind, day)
        if usual is not None or len(values) < MIN_NIGHTS:
            return Status(len(values), usual, close)
        ordered = sorted(values)
        quarter = len(ordered) // 4
        low, high = _clock(ordered[quarter], noon), _clock(ordered[-1 - quarter], noon)
        return Status(len(values), None, close, low, high)

    def _guess(self, kind: str, day: DayType) -> time | None:
        """The usual time, or while it is still being learned the middle of the nights so far
        (only for getting up and going to bed, E-507)."""
        found = self.usual(kind, day)
        values = self._table(kind).get(day.value, [])
        if found is not None or kind not in ("wake", "bed") or not values:
            return found
        return _clock(_median(values), noon=kind == "bed")

    def unsure(self, day: DayType) -> list[tuple[int, int]]:
        """Where the day of this type is not sure yet (E-507), in minutes from 0:00: around a
        time to get up or go to bed that is still being learned, from the earliest to the latest
        night – the middle half once there are enough – and at least a quarter of an hour
        either side of the middle. A stretch past midnight comes in two."""
        spans: list[tuple[int, int]] = []
        for kind in ("wake", "bed"):
            values = self._table(kind).get(day.value, [])
            if not values or self.usual(kind, day) is not None:
                continue
            ordered = sorted(values)
            quarter = len(ordered) // 4 if len(ordered) >= MIN_NIGHTS else 0
            middle = _median(values)
            low = min(ordered[quarter], middle - UNSURE_AT_LEAST)
            high = max(ordered[-1 - quarter], middle + UNSURE_AT_LEAST)
            if kind == "wake":
                spans.append((max(low, 0), min(high, 1440)))
                continue
            low, high = low + 720, high + 720  # bedtimes count from noon
            if high <= 1440:
                spans.append((low, high))
            elif low >= 1440:
                spans.append((low - 1440, high - 1440))
            else:
                spans += [(low, 1440), (0, high - 1440)]
        return sorted(s for s in spans if s[1] > s[0])

    def day(self, day: DayType, learning: bool = False) -> list[tuple[str, int, int]]:
        """The day of this type in stretches (kind, from, to) in minutes from 0:00 – sleep,
        morning, away and home – from the usual times; empty while bed or wake is unknown.
        ``learning``: also while getting up or going to bed is still being learned, from the
        nights so far (E-507) – ``unsure`` says where."""
        kinds = ("wake", "leave", "back", "bed")
        times = {k: self._guess(k, day) if learning else self.usual(k, day) for k in kinds}
        wake, bed = times["wake"], times["bed"]
        if wake is None or bed is None:
            return []
        w = wake.hour * 60 + wake.minute
        b = bed.hour * 60 + bed.minute
        awake: list[tuple[str, int, int]] = []
        leave, back = times["leave"], times["back"]
        if leave is not None and back is not None:
            out, home = leave.hour * 60 + leave.minute, back.hour * 60 + back.minute
            if w < out < home and (b <= w or home < b):
                if out - w <= MORNING_AT_MOST:
                    awake += [("morning", w, out)]
                else:
                    awake += [("home", w, out)]
                awake += [("away", out, home), ("home", home, b)]
        if not awake:
            awake = [("home", w, b)]
        stretches: list[tuple[str, int, int]] = []
        for kind, start, end in awake:
            if end > start:
                stretches.append((kind, start, end))
            else:  # past midnight
                stretches += [(kind, start, 1440), (kind, 0, end)]
        stretches.sort(key=lambda s: s[1])
        out_: list[tuple[str, int, int]] = []
        at = 0
        for kind, start, end in stretches:
            if start > at:
                out_.append(("sleep", at, start))
            out_.append((kind, start, end))
            at = end
        if at < 1440:
            out_.append(("sleep", at, 1440))
        return out_

    def summary(self) -> dict[str, dict[str, str]]:
        """Usual times per day type, e.g. for the brain screen and the house report."""
        out: dict[str, dict[str, str]] = {}
        for day in (DayType.WORKDAY, DayType.FREE, DayType.HOLIDAY):
            for kind in ("bed", "wake"):
                found = self.usual(kind, day)
                if found is not None:
                    out.setdefault(day.value, {})[kind] = found.strftime("%H:%M")
        return out

    # -- planning -----------------------------------------------------------------------

    def wake_for(
        self, day: date, day_type: DayType, alarm: datetime | None, tz: Any
    ) -> tuple[datetime, str] | None:
        """When the house wakes up on ``day``: the phone's alarm, else the usual time."""
        if alarm is not None:
            local = alarm.astimezone(tz)
            if local.date() == day and WAKE_EARLIEST <= local.hour < WAKE_LATEST:
                return local, "alarm"
        if day_type is DayType.DEVIANT:
            return None  # a different day: no usual time (E-89)
        usual = self.usual("wake", day_type)
        if usual is None:
            return None
        return datetime.combine(day, usual, tzinfo=tz), "usual"

    def wake_today(
        self, local: datetime, day_type: DayType, alarm: datetime | None, lead: timedelta
    ) -> tuple[datetime, str] | None:
        """Today's wake-up time. Fixed once waking starts: after an alarm rings, the phone
        already shows tomorrow's alarm."""
        key = local.date().isoformat()
        if key in self.frozen:
            at, _, source = self.frozen[key].partition("|")
            return datetime.fromisoformat(at), source
        found = self.wake_for(local.date(), day_type, alarm, local.tzinfo)
        if found is not None and local >= found[0] - lead:
            self.frozen = {key: f"{found[0].isoformat()}|{found[1]}"}
        return found

    def bed_tonight(self, local: datetime, today: DayType, tomorrow: DayType) -> datetime | None:
        """This evening's usual bedtime (for a calm evening, E-88)."""
        after_midnight = local.hour < WAKE_EARLIEST + 3
        usual = self.usual("bed", today if after_midnight else tomorrow)
        if usual is None:
            return None
        day = local.date()
        if usual.hour < 12 and not after_midnight:
            day += timedelta(days=1)
        elif usual.hour >= 12 and after_midnight:
            day -= timedelta(days=1)
        return datetime.combine(day, usual, tzinfo=local.tzinfo)

    def part(
        self,
        local: datetime,
        mode: HouseMode,
        since: datetime | None,
        today: DayType,
        tomorrow: DayType,
        woke: datetime | None,
    ) -> Part:
        """The part of the day now (E-455), from what the house does and its learned rhythm:
        night while the house sleeps; the morning from when it woke up until people usually
        leave, or three hours; the evening from when they usually come home until the house
        sleeps – while someone is still up, until two hours after the usual bedtime. A time
        not learned yet is the clock's, as before. ``woke``: when the house woke up last."""
        tz = local.tzinfo
        day = local.date()
        home = mode in (HouseMode.HOME, HouseMode.GUESTS)

        def at(moment: time, on: date = day) -> datetime:
            return datetime.combine(on, moment, tzinfo=tz)

        def usual(kind: str) -> time | None:
            return self.usual(kind, today) if today is not DayType.DEVIANT else None

        if mode is HouseMode.SLEEP:
            return Part("night", since.astimezone(tz) if since else local, "sleep", True)
        # The morning: from when the house woke up today, else the usual time, else the clock.
        woken = woke.astimezone(tz) if woke is not None else None
        wake = usual("wake")
        if (
            woken is not None
            and woken.date() == day
            and WAKE_EARLIEST <= woken.hour < WAKE_LATEST
            and woken <= local
        ):
            start, how = woken, "woke"
        elif wake is not None:
            start, how = at(wake), "rhythm"
        else:
            start, how = at(CLOCK_MORNING), "clock"
        end = at(CLOCK_DAY) if how == "clock" else start + MORNING_HOME
        leave = usual("leave")
        if how != "clock" and leave is not None:
            if start < at(leave) <= start + timedelta(minutes=MORNING_AT_MOST):
                end = at(leave)
        end = min(end, at(time(12)))
        # The evening: from when people usually come home, else the clock.
        back = usual("back")
        if back is not None and EVENING_EARLIEST <= back.hour < EVENING_LATEST:
            evening, evening_how = at(back), "rhythm"
        else:
            evening, evening_how = at(CLOCK_EVENING), "clock"
        if local < start:  # before the morning: still last evening, or the night
            bed, learned = self._bedtime(day - timedelta(days=1), today, tz)
            evening -= timedelta(days=1)
        elif local < end:
            return Part("morning", start, how, False)
        elif local < evening:
            return Part("day", end, "clock" if how == "clock" else "rhythm", False)
        else:
            bed, learned = self._bedtime(day, tomorrow, tz)
        late = bed + LATE if home and learned else bed
        if local < late:  # the evening: from the rhythm when its start or its end is learned
            how = "rhythm" if learned or evening_how == "rhythm" else "clock"
            return Part("evening", evening, how, local >= bed)
        night_how = ("late" if home else "rhythm") if learned else "clock"
        return Part("night", late, night_how, True)

    def _bedtime(self, evening: date, next_day: DayType, tz: Any) -> tuple[datetime, bool]:
        """This evening's usual bedtime – it belongs to the next day – else the clock's; and
        whether it was learned."""
        usual = self.usual("bed", next_day) if next_day is not DayType.DEVIANT else None
        if usual is None:
            return datetime.combine(evening, CLOCK_NIGHT, tzinfo=tz), False
        on = evening + timedelta(days=1) if usual.hour < 12 else evening
        return datetime.combine(on, usual, tzinfo=tz), True

    def grant(self, skills: tuple[str, ...], day: date) -> None:
        for skill in skills:
            self.granted[skill] = day.isoformat()

    def is_granted(self, skill: str, day: date) -> bool:
        return self.granted.get(skill) == day.isoformat()

    # -- storage ------------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "bed": self.bed,
            "wake": self.wake,
            "frozen": self.frozen,
            "granted": self.granted,
            "leave": self.leave,
            "back": self.back,
            "away_from": self.away_from,
            "seen": self.seen,
            "periods": self.periods,
        }

    @classmethod
    def from_json(cls, raw: Any) -> Rhythm:
        if not isinstance(raw, dict):
            return cls()

        def table(value: Any) -> dict[str, list[int]]:
            if not isinstance(value, dict):
                return {}
            return {
                str(k): [int(x) for x in v if isinstance(x, int)][-KEEP:]
                for k, v in value.items()
                if isinstance(v, list)
            }

        def strings(value: Any) -> dict[str, str]:
            return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}

        periods = raw.get("periods")
        return cls(
            table(raw.get("bed")),
            table(raw.get("wake")),
            strings(raw.get("frozen")),
            strings(raw.get("granted")),
            table(raw.get("leave")),
            table(raw.get("back")),
            str(raw.get("away_from") or ""),
            strings(raw.get("seen")),
            {
                str(k): [str(x) for x in v][:2]
                for k, v in (periods.items() if isinstance(periods, dict) else [])
                if isinstance(v, list) and len(v) == 2
            },
        )


# -- the phone's alarm ------------------------------------------------------------------


def _has_word(text: str, words: list[str]) -> bool:
    return words_have(text, words, whole=True)  # one rule (code QA 3, punt 4)


def alarm_sensors(model: HouseModel, words: list[str]) -> list[str]:
    """The phones' "next alarm" sensors (a timestamp sensor of the HA companion app)."""
    found = []
    for entity in model.entities.values():
        if entity.domain != "sensor" or entity.device_class != "timestamp":
            continue
        if "next_alarm" in entity.entity_id or _has_word(entity.name or "", words):
            found.append(entity.entity_id)
    return sorted(found)


def next_alarm(
    model: HouseModel, states: dict[str, State], sensors: list[str], now: datetime
) -> datetime | None:
    """The earliest alarm in the coming hours of a phone that is at home."""
    earliest: datetime | None = None
    for entity_id in sensors:
        state = states.get(entity_id)
        if state is None or not state.available:
            continue
        package = str(state.attributes.get("Package") or state.attributes.get("package") or "")
        if package and not any(word in package.lower() for word in ALARM_APPS):
            continue
        if not _phone_home(model, states, entity_id):
            continue
        try:
            at = datetime.fromisoformat(state.state.replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None or not now - timedelta(minutes=30) <= at <= now + ALARM_AHEAD:
            continue
        if earliest is None or at < earliest:
            earliest = at
    return earliest


def _phone_home(model: HouseModel, states: dict[str, State], entity_id: str) -> bool:
    """An alarm counts when its phone is at home (or HouseMind cannot tell)."""
    entity = model.entities.get(entity_id)
    if entity is None or not entity.device_id:
        return True
    for other in model.on_device(entity.device_id):
        if other.domain == "device_tracker":
            state = states.get(other.entity_id)
            return state is None or not state.available or state.state == "home"
    return True


def daily_words(languages: Any, key: str) -> list[str]:
    """Words for ``daily_words.<key>`` from every language file (a new language needs no code),
    split once per key until a language file changes (code QA 3, punt 4)."""
    kept: dict[str, tuple[str, ...]] | None = getattr(languages, "daily", None)
    if kept is not None and key in kept:
        return list(kept[key])
    found: list[str] = []
    for texts in languages.languages.values():
        raw = texts.get(f"daily_words.{key}")
        if raw:
            found.extend(w.strip().lower() for w in str(raw).split(",") if w.strip())
    if kept is not None:
        kept[key] = tuple(found)
    return found
