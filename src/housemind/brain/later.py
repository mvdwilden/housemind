"""Straks: the next moments of the day and what HouseMind will then do.

In Brein › Ritme, under the rhythm bar. The moments come from what HouseMind already knows:
when it gets dark and light (the sun at the place of the house), and from the learned rhythm
when the house goes to sleep and gets up (or the phone's alarm). For each moment a picture of
the house at that time, as on a usual day – the evening spent in the living areas, the house
asleep, the bedrooms waking up – runs through a quiet copy of the skills and the gate's own
evaluation at that time, as Doe alsof does (design 70.2): nothing is switched, kept, learned or
said. What HouseMind would do in an earlier moment is how the house is in a later one, so the
evening light that goes on at dusk goes out at bedtime. What it wants now already is no 'later';
what only a person's coming or going sets off (coming in, a window, a sound) is no moment of
the day and stays out.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from housemind.brain.pretend import apply
from housemind.brain.rhythm import next_alarm
from housemind.brain.skills import quiet_copy
from housemind.core.model import State, daypart
from housemind.ha.fake import EFFECTS
from housemind.safety.emergency import ALARMS
from housemind.safety.gate import ActionRequest, Decision, Outcome
from housemind.world.calendar import day_type, time_bucket
from housemind.world.house_mode import HouseMode
from housemind.world.sun import elevation

if TYPE_CHECKING:
    from housemind.brain.skills import SkillEngine
    from housemind.runtime import HouseMind
    from housemind.safety.gate import SafetyGate
    from housemind.world.world import WorldState

HORIZON = timedelta(hours=24)  # from now until the same time tomorrow
STEP = timedelta(minutes=5)  # the sun is looked at every five minutes
DARK_SUN = 3.0  # degrees: below this it is dark, as the world itself says (calendar.is_dark)
EVENING_ON_PCT = 60  # a lamp in the living areas on a usual evening: Helder, felt
# Around a moment: the calm evening runs half an hour before bedtime, the wake light and the
# warm bathroom before getting up; the bedroom opens a few minutes after.
BEFORE_BED = timedelta(minutes=20)
AFTER_BED = timedelta(minutes=10)
BEFORE_WAKE = timedelta(minutes=10)
AFTER_WAKE = timedelta(minutes=15)
AFTER_SUN = timedelta(minutes=5)
# What only a person sets off – coming in, going out of a room, a window, a sound, the
# humidity of a shower – is no moment of the day: it is in Test het huis and the Logboek.
ON_EVENT = frozenset(
    {
        "light_on_entry",
        "night_light",
        "light_on_person_outside",
        "light_outside_arrival",
        "media_off_empty",
        "window_open_setback",
        "ventilate_humidity",
        "ventilate_co2",
        "water_leak_flow",
        "fridge_door",
        "vacuum_when_away",
        "mower_when_dry",
    }
)
# Devices HouseMind never touches in this house – left alone, a setting, a person's own switch –
# are no line: they would say "no" at every moment.
NEVER = frozenset(
    {
        "excluded_by_user",
        "device_setting",
        "hand_switch",
        "unknown_entity",
        "entity_disabled",
        "action_domain_mismatch",
        "unknown_device_kind",
        "action_not_allowed",
        "not_allowed",
    }
)
ORDER = {"dark": 0, "light": 0, "bed": 1, "wake": 2}


@dataclass(slots=True)
class Moment:
    """One moment of the day ahead: what it is, when (local), and how HouseMind knows."""

    key: str  # dark, light, bed or wake
    at: datetime
    how: str  # sun, usual, alarm or late (the house is still up after its usual bedtime)
    lines: list[dict[str, Any]] = field(default_factory=list)


class _At:
    """A clock that stands still at a moment: the gate looks at that moment."""

    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at

    async def sleep(self, seconds: float) -> None:  # pragma: no cover - never waits
        return None


def _dark_at(hm: HouseMind, local: datetime) -> bool:
    """Dark outside at that moment: the sun at the place of the house, else the season."""
    assert hm.world is not None
    if hm.world.location is not None:
        return elevation(*hm.world.location, local) < DARK_SUN
    return hm.world._fallback_dark(local)


def _sun_moments(hm: HouseMind, base: WorldState) -> list[Moment]:
    """When it gets dark and when it gets light next, within a day."""
    out: list[Moment] = []
    dark = base.outside_dark
    moment = base.local
    end = base.local + HORIZON
    while moment < end and len(out) < 2:
        moment += STEP
        now_dark = _dark_at(hm, moment)
        if now_dark == dark:
            continue
        dark = now_dark
        key = "dark" if dark else "light"
        if all(m.key != key for m in out):
            out.append(Moment(key, _to_minute(moment), "sun"))
    return out


def _to_minute(local: datetime) -> datetime:
    return local.replace(second=0, microsecond=0)


def _wake(hm: HouseMind, base: WorldState) -> tuple[Moment | None, bool]:
    """The next getting up: today's if it is still to come, else tomorrow's. With False: the
    rhythm does not know it yet."""
    assert hm.world is not None
    alarm = next_alarm(hm.model, hm.world.states, hm._alarm_sensors, base.now)
    country = hm.config.country if hm.config else None
    tz = base.local.tzinfo
    today = base.local.date()
    woke = base.woke.astimezone(tz).date() if base.woke is not None else None
    for day in (today, today + timedelta(days=1)):
        if day == today and woke == today:
            continue  # the house got up today already
        found = hm.rhythm.wake_for(day, day_type(day, country), alarm, tz)
        if found is not None and found[0] > base.local:
            return Moment("wake", _to_minute(found[0]), found[1]), True
    return None, False


def _bed(hm: HouseMind, base: WorldState) -> tuple[Moment | None, bool]:
    """Tonight's going to sleep; while the house is up after its usual bedtime: soon."""
    if base.mode is HouseMode.SLEEP:
        return None, True  # the house sleeps already
    bed = hm._bed_at(base)
    if bed is None:
        return None, False
    if bed <= base.local:
        return Moment("bed", _to_minute(base.local + STEP), "late"), True
    return Moment("bed", _to_minute(bed), "usual"), True


def moments(hm: HouseMind, base: WorldState) -> tuple[list[Moment], list[str]]:
    """The moments of the day ahead in their order, and what the rhythm does not know yet."""
    found = _sun_moments(hm, base)
    unknown: list[str] = []
    bed, known = _bed(hm, base)
    if bed is not None:
        found.append(bed)
    elif not known:
        unknown.append("bed")
    wake, known = _wake(hm, base)
    if wake is not None:
        found.append(wake)
    elif not known:
        unknown.append("wake")
    end = base.local + HORIZON
    found = [m for m in found if base.local < m.at <= end]
    found.sort(key=lambda m: (m.at, ORDER[m.key]))
    return found, unknown


# -- the house at a moment ---------------------------------------------------------------------


def _picture(
    hm: HouseMind,
    base: WorldState,
    states: dict[str, State],
    local: datetime,
    rooms: dict[str, list[str]],
    house: list[str],
) -> tuple[WorldState, dict[str, State]]:
    """The house at ``local``: each room in its situation (pretend), the house as a whole in
    its own (home, asleep), dark or light outside as the sun will be."""
    dark = _dark_at(hm, local)
    picture, out = base, dict(states)
    persons = hm._test_persons()
    for area_id in hm.model.areas:
        chosen = [*rooms.get(area_id, []), "dark" if dark else "light"]
        picture, out = apply(picture, out, area_id, chosen, hm._test_room(area_id), persons)
    if house:
        first = next(iter(hm.model.areas), None)
        if first is not None:
            picture, out = apply(picture, out, first, house, hm._test_room(first), persons)
    country = hm.config.country if hm.config else None
    part = "night" if picture.mode is HouseMode.SLEEP else daypart(local)
    return (
        replace(
            picture,
            local=local,
            bucket=time_bucket(local),
            day_type=day_type(local.date(), country),
            part=part,
            part_since=local,
            part_how="later",
            bedtime=picture.mode is HouseMode.SLEEP,
        ),
        out,
    )


def _roles(hm: HouseMind, skill: str) -> tuple[str, ...]:
    return tuple(hm.skill_params.get(skill, {}).get("in", ()))


def _rooms_with(hm: HouseMind, roles: tuple[str, ...]) -> list[str]:
    return [a for a in hm.model.areas if hm.area_roles.get(a, "other") in roles]


def _lamps_on(twin: SkillEngine, states: dict[str, State], areas: list[str]) -> dict[str, State]:
    """A usual evening: the lamps of the living areas are on (a lamp that is on stays as it is)."""
    out = dict(states)
    for area_id in areas:
        for entity in twin.units_of("bedtime_dim", area_id):
            state = out.get(entity.entity_id)
            if entity.domain != "light" or state is None or state.state == "on":
                continue
            attributes = {**state.attributes, "brightness": round(EVENING_ON_PCT * 255 / 100)}
            out[entity.entity_id] = replace(state, state="on", attributes=attributes)
    return out


Prepare = Callable[[dict[str, State]], dict[str, State]]


def _as_it_is(states: dict[str, State]) -> dict[str, State]:
    return states


Situation = tuple[dict[str, list[str]], list[str]]  # each room's situation, the house's
Picture = tuple[datetime, dict[str, list[str]], list[str], Prepare]


def _situations(
    hm: HouseMind, twin: SkillEngine, base: WorldState, moment: Moment, after: Situation
) -> tuple[list[Picture], Situation]:
    """The pictures of a moment – when, each room's situation, the house's, and what is on –
    and the situation the house is in after it. The sun comes up or goes down over the house
    as the moment before left it: asleep after bedtime, up after getting up."""
    if moment.key in ("dark", "light"):
        return [(moment.at + AFTER_SUN, after[0], after[1], _as_it_is)], after
    away = base.mode is HouseMode.VACATION  # on vacation nobody goes to bed or gets up here
    home = [] if away else ["home"]
    bedrooms = _rooms_with(hm, _roles(hm, "covers_open_wake"))
    sleeping = {a: ["in_bed"] for a in bedrooms if hm._test_room(a).sleep != "none"}
    if moment.key == "bed":
        living = _rooms_with(hm, _roles(hm, "bedtime_dim"))
        evening = {a: ["someone"] for a in living if hm._test_room(a).presence}
        empty = {
            a: ["nobody"] for a in hm.model.areas if a not in sleeping and hm._test_room(a).presence
        }

        def lamps(states: dict[str, State]) -> dict[str, State]:
            return states if away else _lamps_on(twin, states, living)

        asleep: Situation = ({**empty, **sleeping}, [*home, "sleep"])
        return [
            (moment.at - BEFORE_BED, {} if away else evening, home, lamps),
            (moment.at + AFTER_BED, *asleep, _as_it_is),
        ], asleep
    up: dict[str, list[str]] = {}
    for area_id in bedrooms:
        keys = ["out_of_bed"] if area_id in sleeping else []
        if hm._test_room(area_id).presence:
            keys.append("nobody")  # up and out of the bedroom for a few minutes
        up[area_id] = keys
    return [
        (moment.at - BEFORE_WAKE, sleeping, [*home, "sleep"], _as_it_is),
        (moment.at + AFTER_WAKE, up, home, _as_it_is),
    ], (up, home)


# -- what HouseMind would do then --------------------------------------------------------------


def _key(request: ActionRequest) -> str:
    data = json.dumps(request.data, sort_keys=True, default=str)
    return f"{request.entity_id}|{request.action}|{data}"


def _gate_at(hm: HouseMind, local: datetime, mode: HouseMode) -> SafetyGate:
    """The gate as it will judge at that moment: the clock, the house rules and the house mode
    of then; what holds now (a person's lamp, a pause) holds only as long as it lasts."""
    assert hm.gate is not None
    gate = copy.copy(hm.gate)
    gate._clock = _At(local)
    state = copy.copy(hm.gate.state)
    state.house_rule_veto = lambda request: hm.rules.veto(request, local, mode.value)
    gate.state = state
    return gate


def _done(states: dict[str, State], request: ActionRequest) -> dict[str, State]:
    """The house after HouseMind did it – for the next moment."""
    state = states.get(request.entity_id)
    effect = EFFECTS.get(request.action)
    if state is None or effect is None:
        return states
    value, attributes = effect(state, dict(request.data))
    return {**states, request.entity_id: replace(state, state=value, attributes=attributes)}


def _as_line(request: ActionRequest) -> ActionRequest:
    """The request as a line of its moment: judged at that moment, and a lamp that first dims
    as a warning on its way to off is the off."""
    request = replace(request, created_at=None)
    if request.facts.get("warning"):
        facts = {k: v for k, v in request.facts.items() if k != "warning"}
        domain = request.action.split(".", 1)[0]
        request = replace(request, action=f"{domain}.turn_off", data={}, facts=facts)
    return request


def _line(hm: HouseMind, lang: str, decision: Decision) -> dict[str, Any]:
    assert hm.explainer is not None
    t = hm.languages.text
    wanted = hm.explainer.wanted(lang, decision)
    text = t(lang, "later.line", action=wanted, Action=wanted[:1].upper() + wanted[1:])
    outcome = decision.outcome
    if outcome is Outcome.EXECUTED:
        chip = t(lang, "later.do")
    elif outcome is Outcome.PROPOSED:
        chip = t(lang, "later.ask")
    elif outcome is Outcome.OBSERVED:
        chip = t(lang, "later.watch")
    else:
        chip = t(lang, "later.not", why=hm.explainer.why_not(decision, lang).rstrip("."))
    return {**hm._test_icon(decision.request), "text": text, "chip": chip, "outcome": outcome.value}


def later(hm: HouseMind, lang: str) -> dict[str, Any]:
    """Straks: the moments of the day ahead and per moment what HouseMind would then do."""
    assert hm.world is not None and hm.skills is not None and hm.learning is not None
    lang = hm.languages.resolve(lang)
    t = hm.languages.text
    tz = ZoneInfo(hm.world.time_zone)
    now = hm.clock.now()
    base = hm.world.look(now)  # only looking: the house mode stays (design 70.1)
    out: dict[str, Any] = {
        "at": now.astimezone(tz).strftime("%H:%M"),
        "moments": [],
        "unknown": [],
        "blocked": None,
    }
    if hm.status.emergency in ALARMS:
        out["blocked"] = t(lang, "later.alarm")  # the emergency reactions rule
        return out
    found, unknown = moments(hm, base)
    out["unknown"] = [t(lang, f"later.unknown.{key}") for key in unknown]
    twin = quiet_copy(hm.skills, hm.learning)
    # Getting up as the house will: what it wants then, never fixing today's wake-up time.
    alarm = next_alarm(hm.model, hm.world.states, hm._alarm_sensors, now)
    country = hm.config.country if hm.config else None
    twin.wake_at = lambda world: hm.rhythm.wake_for(
        world.local.date(), day_type(world.local.date(), country), alarm, world.local.tzinfo
    )
    states = dict(hm.world.states)
    # Now first: what HouseMind does now is how the house goes on; what it may not do now,
    # and may not later for the same reason, is no line at every moment.
    refused: set[tuple[str, str]] = set()
    gate = _gate_at(hm, base.local, base.mode)
    for request in twin.evaluate(base, states):
        request = replace(request, created_at=None)
        decision = gate.evaluate(request)
        if decision.outcome is Outcome.DENIED:
            refused.add((_key(request), decision.reason or ""))
        else:
            states = _done(states, request)
    tomorrow = base.local.date() + timedelta(days=1)
    after: Situation = ({}, [])  # the house as it is now
    for moment in found:
        pictures, after = _situations(hm, twin, base, moment, after)
        shown: set[str] = set()
        for local, rooms, house, prepare in pictures:
            # The house as HouseMind leaves it at the moment before goes on from there.
            picture, states = _picture(hm, base, prepare(states), local, rooms, house)
            gate = _gate_at(hm, local, picture.mode)
            for request in twin.evaluate(picture, states):
                if request.skill in ON_EVENT:
                    continue
                request = _as_line(request)
                key = _key(request)
                if key in shown:
                    continue
                shown.add(key)
                decision = gate.evaluate(request)
                if decision.outcome is Outcome.DENIED:
                    why = decision.reason or ""
                    if (key, why) in refused or why.split(":")[0] in NEVER:
                        continue
                    refused.add((key, why))
                else:
                    states = _done(states, request)
                moment.lines.append(_line(hm, lang, decision))
        title = moment.key + ("_tomorrow" if moment.at.date() == tomorrow else "")
        out["moments"].append(
            {
                "key": moment.key,
                "at": moment.at.strftime("%H:%M"),
                "title": t(lang, f"later.moment.{title if moment.key == 'wake' else moment.key}"),
                "how": _how(hm, lang, moment),
                "lines": moment.lines,
                "nothing": None if moment.lines else t(lang, "later.nothing"),
            }
        )
    return out


def _how(hm: HouseMind, lang: str, moment: Moment) -> str:
    """How HouseMind knows the moment: as usual, the type of day of getting up, the alarm."""
    t = hm.languages.text
    if moment.key == "wake" and moment.how == "usual":
        country = hm.config.country if hm.config else None
        return t(lang, f"later.how.{day_type(moment.at.date(), country).value}")
    if moment.how in ("usual", "alarm", "late"):
        return t(lang, f"later.how.{moment.how}")
    return ""
