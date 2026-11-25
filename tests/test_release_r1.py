"""R1 release test (E-144, design H12 test 2): S-01, S-02 and S-04 in every demo house.

Each demo house lives four weeks in the time machine. One resident follows the daily routine
and answers proposals the way someone with that routine would (mostly "Do it"). S-03 (saving
energy) belongs to R2 Energy and is tested there.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from housemind.brain.proposals import Answer
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import DEMO_HOUSES, load_demo_house
from housemind.demo.timemachine import ScenarioStep, standard_routine
from housemind.knowledge.catalog import Step
from housemind.knowledge.matcher import recognise
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import Decision, Layer
from housemind.server import views

ROOT = Path(__file__).resolve().parents[1]
MONDAY = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)  # 06:00 local
WEEKS = 4
CORE = {"lighting": "light", "covers": "cover", "climate": "climate"}
RAW_KEY = re.compile(r"\b(action|reason|explain|ask|gate|skills|steps)\.[a-z_]+")


@dataclass
class Run:
    hm: HouseMind
    clock: SimulatedClock
    executed: list[Decision] = field(default_factory=list)
    proposals_day1: int = 0
    answered: int = 0


async def _live(
    run: Run, steps: list[ScenarioStep], until: datetime, answers: random.Random
) -> None:
    hm, clock = run.hm, run.clock
    pending = sorted(steps)
    while clock.now() < until:
        next_tick = clock.now() + timedelta(minutes=5)
        while pending and pending[0].at <= next_tick:
            step = pending.pop(0)
            if step.at > clock.now():
                await clock.advance((step.at - clock.now()).total_seconds())
            await hm.ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)  # type: ignore[attr-defined]
        if next_tick > clock.now():
            await clock.advance((next_tick - clock.now()).total_seconds())
        await hm.tick()
        # The resident answers what HouseMind asks, like someone with this routine would.
        for proposal in await hm.open_proposals():
            answer = Answer.DO if answers.random() < 0.95 else Answer.NOT_NOW
            await hm.answer(proposal.id, answer, "demo-user-1")
            run.answered += 1


async def _four_weeks(key: str, tmp_path: Path) -> Run:
    clock = SimulatedClock(MONDAY)
    house = load_demo_house(key, clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    run = Run(hm, clock)

    async def record(_id: int, decision: Decision) -> None:
        run.executed.append(decision)

    hm.on_action(record)
    # The introduction: one resident, level Involved, says yes to learning (B-20).
    await hm.set_house_setting("consent_learning", True)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-1", "Resident", 3, clock.now().isoformat()),
    )
    await hm._refresh_people()
    routine = standard_routine(house, ZoneInfo("Europe/Amsterdam"))
    # The life of the house and the answers each have their own dice: how many questions
    # HouseMind asks never changes how the residents live.
    rng, answers = random.Random(7), random.Random(8)
    for day in range(WEEKS * 7):
        date = (MONDAY + timedelta(days=day)).date()
        await _live(run, routine(date, rng), MONDAY + timedelta(days=day + 1), answers)
        if day == 0:
            rows = await hm.db.fetchall("SELECT id FROM proposals")
            run.proposals_day1 = len(rows)
    return run


@pytest.mark.parametrize("key", DEMO_HOUSES)
async def test_s04_up_and_running_in_15_minutes(key: str, tmp_path: Path) -> None:
    clock = SimulatedClock(MONDAY)
    house = load_demo_house(key, clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    assert hm.gate is not None
    # The house is recognised without any manual work.
    core = [e for e in hm.model.entities.values() if e.domain in ("light", "cover", "climate")]
    assert core and all(recognise(hm.catalog, e, None).kind is not None for e in core)
    # First insights: the calm line, rooms and the day.
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident", 2, '{"emergency_push": true}', clock.now().isoformat()),
    )
    await hm._refresh_people()
    await clock.advance(10 * 60)
    await hm.tick()
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    snap = await views.snapshot(hm, viewer)
    assert snap["line"] and snap["rooms"] and snap["day"]
    # Safety monitoring is active: a leak is handled within the first quarter of an hour.
    leak = next(e for e in hm.model.entities.values() if e.device_class == "moisture")
    await house.ha.simulate(leak.entity_id, "on")
    assert hm.status.orb == "emergency"
    assert any(n["kind"] == "emergency" for n in await hm.notifications())
    assert clock.now() - MONDAY < timedelta(minutes=15)
    await hm.stop()


@pytest.mark.parametrize("key", DEMO_HOUSES)
async def test_r1_four_weeks_meet_s01_and_s02(key: str, tmp_path: Path) -> None:
    run = await _four_weeks(key, tmp_path)
    hm = run.hm
    assert hm.learning is not None and hm.explainer is not None

    # S-04: within 24 hours the first proposals from start knowledge (E-160).
    assert run.proposals_day1 > 0

    # S-01: after four weeks every core domain of the house is autonomous, with fewer than
    # one correction a week per domain – counted by HouseMind itself.
    domains_present = {
        d for d, ha in CORE.items() if any(e.domain == ha for e in hm.model.entities.values())
    }
    steps: dict[str, Step] = {}
    for (skill, area), _ in hm.learning.steps.items():
        domain = hm.skill_params.get(skill, {}).get("domain")
        if domain in CORE:
            steps[domain] = max(steps.get(domain, Step.OBSERVE), hm._skill_step(skill, area))
    report = {d: steps.get(d, Step.OBSERVE).name for d in domains_present}
    assert all(steps.get(d) is Step.AUTONOMOUS for d in domains_present), report
    corrections = await hm.corrections_per_domain(7)
    assert all(corrections.get(d, 0) < 1 for d in domains_present), corrections

    # S-02 (a): every action has a plain explanation in one tap, without AI.
    assert run.executed, "HouseMind should have acted during four weeks"
    for decision in run.executed:
        lines = hm.explainer.explain(decision, 2, "nl", hm.clock.now())
        assert lines and lines[0].startswith("Ik heb"), lines
        assert not RAW_KEY.search(" ".join(lines)), lines
    # S-02 (c): never acting silently – every call to Home Assistant is in the log.
    ha: Any = hm.ha
    logged = await hm.db.fetchone(
        "SELECT count(*) AS n FROM decisions WHERE outcome IN ('executed', 'failed')"
    )
    assert logged is not None and int(logged["n"]) == len(ha.calls)
    # S-02 (d): honest about doubt – level 4 shows how sure HouseMind is.
    learned = [
        d for d in run.executed if d.request.skill and d.request.layer is not Layer.EMERGENCY
    ]
    assert any(
        "% zeker" in " ".join(hm.explainer.explain(d, 4, "nl", hm.clock.now())) for d in learned
    )
    await hm.stop()
