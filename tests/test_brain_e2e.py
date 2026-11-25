"""End to end: a demo house lives a week in the time machine; HouseMind learns."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.demo.timemachine import ScenarioStep, standard_routine
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
MONDAY = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)  # 06:00 local


async def live(
    hm: HouseMind,
    clock: SimulatedClock,
    steps: list[ScenarioStep],
    until: datetime,
    tick_s: int = 300,
) -> None:
    """Play scenario steps while ticking the brain every few minutes."""
    pending = sorted(steps)
    ha = hm.ha
    while clock.now() < until:
        next_tick = clock.now() + timedelta(seconds=tick_s)
        while pending and pending[0].at <= next_tick:
            step = pending.pop(0)
            if step.at > clock.now():
                await clock.advance((step.at - clock.now()).total_seconds())
            await ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)  # type: ignore[attr-defined]
        if next_tick > clock.now():
            await clock.advance((next_tick - clock.now()).total_seconds())
        await hm.tick()


async def test_a_week_of_life_teaches_the_brain(tmp_path: Path) -> None:
    clock = SimulatedClock(MONDAY)
    house = load_demo_house("apartment", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)  # the yes from the introduction
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES('u1', 'Resident', 2, ?)",
        (clock.now().isoformat(),),
    )
    routine = standard_routine(house, ZoneInfo("Europe/Amsterdam"))
    rng = random.Random(3)
    assert hm.learning is not None

    # Day 1: within 24 hours the first proposals come from start knowledge (S-04, E-160).
    day1 = routine(clock.now().date(), rng)
    await live(hm, clock, day1, MONDAY + timedelta(days=1))
    now = clock.now()
    steps_day1 = {pair: hm.learning.step_at(*pair, now) for pair in hm.learning.first_seen}
    assert any(step is Step.PROPOSE for step in steps_day1.values()), steps_day1

    # A full week: the brain keeps learning; nothing ever crosses a hard limit.
    for day in range(1, 8):
        await live(
            hm,
            clock,
            routine((MONDAY + timedelta(days=day)).date(), rng),
            MONDAY + timedelta(days=day + 1),
        )
    score, n, days = hm.learning.score("light_on_entry", "living", clock.now())
    assert n >= 7 and days >= 7
    assert score >= 0.5
    rows = await hm.db.fetchall("SELECT action, outcome FROM decisions")
    assert not any(r["action"] in ("lock.unlock", "lock.open") for r in rows)
    proposals = await hm.db.fetchall("SELECT * FROM proposals")
    assert proposals, "HouseMind should have proposed something during the week"
    await hm.stop()


async def test_carefree_household_gets_trials_not_questions(tmp_path: Path) -> None:
    clock = SimulatedClock(MONDAY)
    house = load_demo_house("apartment", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)  # the yes from the introduction
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES('u1', 'Resident', 1, ?)",
        (clock.now().isoformat(),),
    )
    routine = standard_routine(house, ZoneInfo("Europe/Amsterdam"))
    rng = random.Random(5)
    for day in range(9):
        await live(
            hm,
            clock,
            routine((MONDAY + timedelta(days=day)).date(), rng),
            MONDAY + timedelta(days=day + 1),
        )
    trials = await hm.db.fetchall("SELECT * FROM decisions WHERE source_layer = 'trial'")
    open_proposals = await hm.db.fetchall("SELECT * FROM proposals WHERE status = 'open'")
    assert trials, "Carefree households should get careful trials"
    # Not before a week of watching (QA 2, B-176): doing it without asking needs proof.
    assert min(datetime.fromisoformat(t["at"]) for t in trials) >= MONDAY + timedelta(days=7)
    assert all(p["entity_id"].startswith(("cover.garage", "lock", "alarm")) for p in open_proposals)
    await hm.stop()
