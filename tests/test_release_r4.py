"""Release test R4 – broad (E-144, fase 3): two weeks in the time machine.

The family house lives its rhythm, with a shower every morning (water), one night with a
dripping tap, a door sensor whose battery runs low and is replaced, and the robot vacuum –
experimental, so it only runs after the administrator turns cleaning on. After two weeks:
- the dripping night is noticed and the main valve closed (after a yes), the message waits
  for the morning, and the house report counts the water;
- the battery got a word, which went away when it was replaced;
- the vacuum never ran before cleaning was on, and ran on days nobody was home after;
- corrections stay below one a day per domain (S-01) and no raw key ever shows (S-02).
"""

from __future__ import annotations

import json
import random
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.proposals import Answer
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, Source, State
from housemind.demo.houses import load_demo_house
from housemind.demo.timemachine import ScenarioStep, standard_routine
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
MONDAY = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)
DAYS = 14
LEAK_DAY = 9  # the night into Wednesday of week two
CLEANING_ON = 7
RAW_KEY = re.compile(r"\b(notify|report|skills|reason|ask|action|daily_words|weather)\.[a-z_]+")


def water_steps(day: datetime, litres: float, rng: random.Random) -> list[ScenarioStep]:
    """A shower in the morning: the meter goes up in three steps."""
    start = day + timedelta(hours=7, minutes=rng.randint(0, 20))
    return [
        ScenarioStep(start + timedelta(minutes=2 * i), "sensor.water_meter", f"+{litres / 3}")
        for i in range(3)
    ]


async def test_r4_two_weeks_broad(tmp_path: Path) -> None:
    clock = SimulatedClock(MONDAY.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    battery = Entity(
        "sensor.front_door_battery",
        "r-b",
        "Voordeur batterij",
        device_id="binary_sensor.front_door",
        device_class="battery",
        unit="%",
    )
    house.ha.model.entities[battery.entity_id] = battery
    house.ha.states[battery.entity_id] = State(
        battery.entity_id, "60", {"unit_of_measurement": "%"}, clock.now()
    )
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-1", "Resident", 3, clock.now().isoformat()),
    )
    await hm._refresh_people()
    routine = standard_routine(house, TZ)
    rng = random.Random(5)
    vacuum_starts: list[int] = []
    battery_topics: set[str] = set()
    texts: list[str] = []
    for day in range(DAYS):
        date = MONDAY + timedelta(days=day)
        steps = sorted(routine(date.date(), rng) + water_steps(date, 60, rng))
        if day == LEAK_DAY:
            night = date + timedelta(hours=1)
            steps += [
                ScenarioStep(night + timedelta(minutes=5 * i), "sensor.water_meter", "+0.5")
                for i in range(40)
            ]
            steps.append(
                ScenarioStep(date + timedelta(hours=7, minutes=40), "valve.water_main", "open")
            )
        if day == 3:
            steps.append(ScenarioStep(date + timedelta(hours=12), battery.entity_id, "9"))
        if day == 5:
            steps.append(ScenarioStep(date + timedelta(hours=12), battery.entity_id, "100"))
        steps.append(ScenarioStep(date + timedelta(hours=16), "vacuum.robot", "docked"))
        if date.weekday() < 5:  # on workdays the others are out too: the house is empty
            for resident in ("person.resident_2", "person.resident_3"):
                steps.append(
                    ScenarioStep(
                        date + timedelta(hours=8, minutes=30),
                        resident,
                        "not_home",
                        by=Source.UNKNOWN,
                    )
                )
                steps.append(
                    ScenarioStep(
                        date + timedelta(hours=15, minutes=30), resident, "home", by=Source.UNKNOWN
                    )
                )
        pending = sorted(steps)
        until = (date + timedelta(days=1)).astimezone(UTC)
        while clock.now() < until:
            next_tick = clock.now() + timedelta(minutes=5)
            while pending and pending[0].at <= next_tick:
                step = pending.pop(0)
                if step.at > clock.now():
                    await clock.advance((step.at - clock.now()).total_seconds())
                state = step.state
                if state.startswith("+"):  # the water meter goes up (m³)
                    current = float(house.ha.states["sensor.water_meter"].state)
                    state = f"{current + float(state[1:]) / 1000:.4f}"
                await house.ha.simulate(step.entity_id, state, step.attributes, by=step.by)
            if next_tick > clock.now():
                await clock.advance((next_tick - clock.now()).total_seconds())
            before = house.ha.states["vacuum.robot"].state
            await hm.tick()
            battery_topics |= {
                str(n["topic"]) for n in await hm.notifications() if "battery" in str(n["topic"])
            }
            for proposal in await hm.open_proposals():
                await hm.answer(proposal.id, Answer.DO, "demo-user-1")
            if before == "docked" and house.ha.states["vacuum.robot"].state == "cleaning":
                vacuum_starts.append(day)
        if day + 1 == CLEANING_ON:
            assert await hm.set_house_setting("experimental", ["cleaning"])

    # Water: the dripping night was noticed, the valve closed after a yes; the report counts.
    closed = await hm.db.fetchall(
        "SELECT at FROM decisions WHERE entity_id = 'valve.water_main' AND action = "
        "'valve.close_valve' AND outcome = 'executed'"
    )
    assert closed, "the main valve was never closed"
    at = datetime.fromisoformat(closed[0]["at"]).astimezone(TZ)
    assert at.date() == (MONDAY + timedelta(days=LEAK_DAY)).date() and at.time() < time(5, 0)
    # It was raised at once; at night it waits for the morning, and here someone answered and
    # the water stopped before then (closing it itself keeps a message: see test_water).
    raised = await hm.db.fetchall("SELECT topic FROM notifications WHERE topic = 'water_running'")
    assert raised, "the water message was never raised"
    data = await hm.week_data(clock.now())
    assert data["water_l"] and 300 <= data["water_l"] <= 600, data["water_l"]
    texts += hm.report_text(data, 3, "nl")

    # Maintenance: a word about the battery, gone after the new battery.
    assert "maintenance:battery:sensor.front_door_battery" in battery_topics
    assert not [n for n in await hm.notifications() if "battery" in str(n["topic"])]

    # Experimental: never before cleaning was on; on days nobody was home after.
    assert vacuum_starts and min(vacuum_starts) >= CLEANING_ON
    assert all((MONDAY + timedelta(days=d)).weekday() < 5 for d in vacuum_starts)

    # S-01 and S-02.
    per_domain = await hm.corrections_per_domain(7)
    assert all(n / 7 < 1 for n in per_domain.values()), per_domain
    for row in await hm.db.fetchall("SELECT text_key, params FROM notifications"):
        texts.append(hm.languages.text("nl", str(row["text_key"]), **json.loads(row["params"])))
    texts += [n[2] for n in house.ha.notifications]
    for decision in list(hm.status.decisions)[-80:]:
        texts += hm.explain(decision, 4, "nl")
    leftovers = [t for t in texts if RAW_KEY.search(t)]
    assert not leftovers, leftovers[:5]
    await hm.stop()
