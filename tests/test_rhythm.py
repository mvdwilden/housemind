"""Sleeping and waking up (E-88): the learned rhythm, the phone's alarm, waking slowly with
light and warmth, a calm evening, and the question the evening before."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.proposals import Answer
from housemind.brain.rhythm import Rhythm, alarm_sensors, next_alarm
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source
from housemind.demo.houses import load_demo_house
from housemind.demo.timemachine import standard_routine
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.world.calendar import DayType
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")


def local(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=TZ)


async def start(
    at: datetime, tmp_path: Path, level: int = 3
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    for i, lvl in ((1, level), (2, level), (3, 1)):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (f"demo-user-{i}", f"Resident {i}", lvl, json.dumps({}), clock.now().isoformat()),
        )
    await hm._refresh_people()
    return hm, clock, house.ha


async def until(clock: SimulatedClock, hm: HouseMind, moment: datetime) -> None:
    """Let time pass to ``moment``, with a tick every 30 seconds like the real loop."""
    target = moment.astimezone(UTC)
    while clock.now() < target:
        await clock.advance(min(30.0, (target - clock.now()).total_seconds()))
        await hm.tick()


def brightness(ha: FakeHomeAssistant, entity_id: str) -> int | None:
    state = ha.states[entity_id]
    if state.state != "on":
        return None
    return round(state.attributes.get("brightness", 0) * 100 / 255)


def test_the_rhythm_is_learned_per_type_of_day_and_only_when_clear() -> None:
    r = Rhythm()
    for day in (19, 20, 21):  # Monday to Wednesday
        r.record(
            HouseMode.HOME, HouseMode.SLEEP, local(day - 1, 23, 5), DayType.FREE, DayType.WORKDAY
        )
        r.record(
            HouseMode.SLEEP, HouseMode.HOME, local(day, 6, 50), DayType.WORKDAY, DayType.WORKDAY
        )
    assert r.usual("bed", DayType.WORKDAY) == time(23, 5)  # Sunday evening belongs to Monday
    assert r.usual("wake", DayType.WORKDAY) == time(6, 50)
    # After midnight it still counts as the same night.
    r2 = Rhythm()
    for day in (19, 20, 21):
        r2.record(
            HouseMode.HOME, HouseMode.SLEEP, local(day, 0, 20), DayType.WORKDAY, DayType.WORKDAY
        )
    assert r2.usual("bed", DayType.WORKDAY) == time(0, 20)
    # A day that differs (E-89) is not learned; nights all over the place give no usual time.
    assert not r.record(
        HouseMode.SLEEP, HouseMode.HOME, local(22, 9, 0), DayType.DEVIANT, DayType.WORKDAY
    )
    for hh in (7, 9, 11):
        r.record(HouseMode.SLEEP, HouseMode.HOME, local(24, hh), DayType.FREE, DayType.FREE)
    assert r.usual("wake", DayType.FREE) is None
    assert Rhythm.from_json(r.to_json()).usual("wake", DayType.WORKDAY) == time(6, 50)


async def test_the_phones_alarm_wins_and_only_a_real_alarm_counts(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 1), tmp_path)
    sensors = alarm_sensors(hm.model, ["volgende wekker"])
    assert sensors == ["sensor.phone_1_next_alarm"]
    at = local(19, 6, 40).astimezone(UTC).isoformat()
    await ha.simulate("sensor.phone_1_next_alarm", at, {"Package": "com.android.deskclock"})
    found = next_alarm(hm.model, ha.states, sensors, clock.now())
    assert found is not None and found.astimezone(TZ).strftime("%H:%M") == "06:40"
    # A calendar reminder is no alarm.
    await ha.simulate("sensor.phone_1_next_alarm", at, {"Package": "com.android.calendar"})
    assert next_alarm(hm.model, ha.states, sensors, clock.now()) is None
    # Once waking starts the time is fixed, even when the phone already shows tomorrow.
    r = Rhythm()
    alarm = local(19, 6, 40)
    assert r.wake_today(local(19, 6, 15), DayType.WORKDAY, alarm, timedelta(minutes=30)) == (
        alarm,
        "alarm",
    )
    assert r.wake_today(local(19, 6, 45), DayType.WORKDAY, None, timedelta(minutes=30)) == (
        alarm,
        "alarm",
    )
    await hm.stop()


async def test_waking_up_slowly_with_light_and_people_win(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 5, 0), tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("wake_light", "bedroom")] = Step.AUTONOMOUS
    await ha.simulate("sensor.phone_1_next_alarm", local(19, 6, 40).astimezone(UTC).isoformat())
    await until(clock, hm, local(19, 6, 19))
    assert brightness(ha, "light.bedroom") is None  # not yet
    seen = []
    for minute in (21, 28, 35, 41):
        await until(clock, hm, local(19, 6, minute))
        seen.append(brightness(ha, "light.bedroom"))
    assert seen == [3, 15, 35, 60]
    why = hm.explain(hm.status.decisions[-1], 2, "nl")
    assert "wekker om 06:40" in " ".join(why)
    await until(clock, hm, local(19, 7, 5))
    assert brightness(ha, "light.bedroom") == 60  # not turned off by "nobody in the room"

    # The next day someone switches it off while waking: it stays off (E-33).
    await ha.simulate("light.bedroom", "off")
    await ha.simulate("sensor.phone_1_next_alarm", local(20, 6, 40).astimezone(UTC).isoformat())
    await until(clock, hm, local(20, 6, 22))
    assert brightness(ha, "light.bedroom") == 3
    await ha.simulate("light.bedroom", "off", by=Source.PHYSICAL)
    await until(clock, hm, local(20, 6, 45))
    assert ha.states["light.bedroom"].state == "off"
    await hm.stop()


async def test_the_evening_before_houseMind_asks_and_a_yes_holds_for_that_morning(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await start(local(18, 18, 0), tmp_path)  # Sunday evening
    await ha.simulate("sensor.phone_1_next_alarm", local(19, 6, 40).astimezone(UTC).isoformat())
    await until(clock, hm, local(18, 21, 0))  # start knowledge may be proposed after 2 hours
    proposals = await hm.proposals.open(clock.now())
    wake = [p for p in proposals if p.skill == "wake_light"]
    assert len(wake) == 1 and wake[0].facts["planned_for"].startswith("2026-10-19T06:40")
    asked = [n for n in ha.notifications if "wakker maken" in n[2]]
    assert asked and "morgen om 06:40" in asked[0][2]
    assert "licht en warmte" in asked[0][2]  # the yes holds for both (the bedroom can warm up)
    # The question stays open for the night (not only a quarter of an hour).
    await until(clock, hm, local(18, 23, 0))
    assert wake[0].id in [p.id for p in await hm.proposals.open(clock.now())]
    result = await hm.answer(wake[0].id, Answer.DO, "demo-user-1")
    assert result.get("planned") and ha.states["light.bedroom"].state == "off"  # not now
    await until(clock, hm, local(19, 6, 22))
    assert brightness(ha, "light.bedroom") == 3
    # The bedroom gets warm for getting up (wake_warmth shares the "yes").
    assert ha.states["climate.bedroom"].attributes["temperature"] == 17.5
    await hm.stop()


async def test_not_now_means_no_waking_and_no_question_at_six(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(18, 18, 0), tmp_path)
    await ha.simulate("sensor.phone_1_next_alarm", local(19, 6, 40).astimezone(UTC).isoformat())
    await until(clock, hm, local(18, 21, 0))
    wake = [p for p in await hm.proposals.open(clock.now()) if p.skill == "wake_light"]
    await hm.answer(wake[0].id, Answer.NOT_NOW, "demo-user-1")
    await until(clock, hm, local(19, 6, 45))
    assert ha.states["light.bedroom"].state == "off"
    assert not [p for p in await hm.proposals.open(clock.now()) if p.skill == "wake_light"]
    await hm.stop()


async def test_a_calm_evening_before_the_usual_bedtime(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 18, 0), tmp_path)
    assert hm.learning is not None
    for day in (13, 14, 15):  # Tuesday–Thursday nights before workdays
        hm.rhythm.record(
            HouseMode.HOME, HouseMode.SLEEP, local(day, 22, 45), DayType.WORKDAY, DayType.WORKDAY
        )
    hm.learning.steps[("bedtime_dim", "living")] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.living_presence", "on")
    await ha.simulate("light.living_reading", "on", {"brightness": 255})  # people win 4 hours
    await until(clock, hm, local(19, 22, 10))
    assert brightness(ha, "light.living_reading") == 100  # not yet
    await until(clock, hm, local(19, 22, 16))
    assert brightness(ha, "light.living_reading") == 15  # Gedimd (E-494)
    await hm.stop()


async def test_the_time_machine_house_learns_its_rhythm(tmp_path: Path) -> None:
    clock = SimulatedClock(local(19, 0, 0).astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    hm.learning_allowed = True  # the rhythm is learned only after the "yes" (B-20)
    routine = standard_routine(house, TZ)
    rng = random.Random(3)
    for offset in range(5):  # Monday to Friday
        pending = sorted(routine((local(19, 0) + timedelta(days=offset)).date(), rng))
        end = local(20 + offset, 0).astimezone(UTC)
        while clock.now() < end:
            next_tick = clock.now() + timedelta(minutes=5)
            while pending and pending[0].at <= next_tick:
                step = pending.pop(0)
                if step.at > clock.now():
                    await clock.advance((step.at - clock.now()).total_seconds())
                await house.ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)
            await clock.advance((next_tick - clock.now()).total_seconds())
            await hm.tick()
    wake = hm.rhythm.usual("wake", DayType.WORKDAY)
    bed = hm.rhythm.usual("bed", DayType.WORKDAY)
    assert wake is not None and time(6, 20) <= wake <= time(7, 30)
    assert bed is not None and time(22, 30) <= bed <= time(23, 59)
    await hm.stop()


async def test_people_win_until_they_leave_the_room(tmp_path: Path) -> None:
    """E-33: someone switches the kitchen light on and leaves – it may go off again then."""
    hm, clock, ha = await start(local(19, 19, 0), tmp_path)
    assert hm.learning is not None and hm.gate is not None
    hm.learning.steps[("light_off_leave", "kitchen")] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    await ha.simulate("light.kitchen", "on", {"brightness": 200})
    assert "light.kitchen" in hm.gate.state.people_win_until
    await ha.simulate("binary_sensor.kitchen_motion", "off")
    await until(clock, hm, local(19, 20, 30))
    assert "light.kitchen" in hm.gate.state.released
    assert ha.states["light.kitchen"].state == "off"
    await hm.stop()
