"""R2 release test (E-144, design H12 test 2): the demo energy house over six weeks.

Two weeks of measuring (the baseline), then four weeks in which the energy plan acts (without
the home battery there are fewer moments to learn from, so it needs a week longer). One
resident follows the daily routine and answers proposals. S-01 and S-02 hold for the energy
domain. S-03: the saving is measurable. The home battery and the car are not steered (E-363);
the house holds its warmth (the simulation), and HouseMind also learns the heating meanwhile,
which changes what the house uses. So the same six weeks run twice – with and without what the
energy plan steers – and the plan must make them cheaper (E-380). Each proposal is answered by
its own die, so one question more or less in one run changes nothing else.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from housemind.brain.proposals import Answer
from housemind.core.clock import SimulatedClock
from housemind.demo.energy_sim import EnergySim
from housemind.demo.houses import load_demo_house
from housemind.demo.timemachine import standard_routine
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import Decision

ROOT = Path(__file__).resolve().parents[1]
MONDAY = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)  # 06:00 local
TZ = ZoneInfo("Europe/Amsterdam")
DAYS = 42
RAW_KEY = re.compile(r"\b(action|reason|explain|ask|energy|skills)\.[a-z_]+")


async def _six_weeks(
    tmp_path: Path, person_starts: bool = False
) -> tuple[HouseMind, list[Decision], SimulatedClock]:
    clock = SimulatedClock(MONDAY)
    house = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    executed: list[Decision] = []

    async def record(_id: int, decision: Decision) -> None:
        executed.append(decision)

    hm.on_action(record)
    await hm.set_house_setting("consent_learning", True)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-1", "Resident", 3, clock.now().isoformat()),
    )
    await hm._refresh_people()
    sim = EnergySim(house.ha, TZ, person_starts=person_starts)
    routine = standard_routine(house, TZ)
    days_rng = random.Random(11)  # the same days in both runs
    await sim.step(clock.now())
    for day in range(DAYS):
        pending = sorted(routine((MONDAY + timedelta(days=day)).date(), days_rng))
        until = MONDAY + timedelta(days=day + 1)
        while clock.now() < until:
            next_tick = clock.now() + timedelta(minutes=5)
            while pending and pending[0].at <= next_tick:
                step = pending.pop(0)
                if step.at > clock.now():
                    await clock.advance((step.at - clock.now()).total_seconds())
                await house.ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)
            await clock.advance((next_tick - clock.now()).total_seconds())
            await sim.step(clock.now())
            await hm.tick()
            for proposal in await hm.open_proposals():
                # One die per question and day: one energy question more or less does not
                # change how the heating is answered in the other run.
                key = f"{proposal.skill}|{proposal.area_id}|{proposal.entity_id}|{clock.now():%F}"
                answer = Answer.DO if random.Random(key).random() < 0.95 else Answer.NOT_NOW
                await hm.answer(proposal.id, answer, "demo-user-1")
    return hm, executed, clock


async def _cost_after_baseline(hm: HouseMind) -> float:
    assert hm.energy is not None and hm.energy.meter is not None
    return sum(float(d["cost"]) for d in await hm.energy.meter.days(400) if d["planning"])


async def test_r2_energy_house_saves_measurably(tmp_path: Path) -> None:
    hm, executed, clock = await _six_weeks(tmp_path / "plan")
    assert hm.energy is not None and hm.learning is not None and hm.explainer is not None

    # S-03: after two weeks of measuring, every day is compared with what it would have cost
    # without HouseMind, honestly, in money and kWh ...
    savings = await hm.energy.savings(clock.now())
    assert not savings["measuring"]
    assert savings["compared"] >= 14, savings
    assert len(savings["days"]) == 14 and all("saving" in d for d in savings["days"]), savings

    # S-01 for energy: it acts on its own by now, without people correcting it.
    energy_steps = {
        skill: step
        for (skill, _area), step in hm.learning.steps.items()
        if skill.startswith("energy_")
    }
    assert Step.AUTONOMOUS in energy_steps.values(), energy_steps
    corrections = await hm.corrections_per_domain(7)
    assert corrections.get("energy", 0) < 1, corrections

    # S-02: every energy action has a plain explanation, and nothing happens silently.
    acted = [d for d in executed if (d.request.skill or "").startswith("energy_")]
    assert acted, "the plan should have acted"
    for decision in acted:
        text = " ".join(hm.explainer.explain(decision, 3, "nl", clock.now()))
        assert text.startswith("Ik heb") and not RAW_KEY.search(text), text
    # Raw decisions are kept 30 days (E-18): compare within what is still kept.
    ha: Any = hm.ha
    first = await hm.db.fetchone("SELECT min(at) AS at FROM decisions")
    assert first is not None and first["at"]
    since = datetime.fromisoformat(first["at"])
    logged = await hm.db.fetchone(
        "SELECT count(*) AS n FROM decisions WHERE outcome IN ('executed', 'failed')"
    )
    calls = sum(1 for at in ha.call_times if at >= since)
    assert logged is not None and int(logged["n"]) == calls
    await hm.stop()


@pytest.mark.slow
async def test_r2_the_energy_plan_makes_the_same_weeks_cheaper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-03, fair (E-380): the same six weeks, with and without what the energy plan steers –
    without it a person starts the washing machine as soon as it is ready. The comparison with
    the baseline also holds what HouseMind learns about heating meanwhile, so this is the
    measure. Measured 26-10-2026: € 207,58 with the plan against € 208,32 without."""
    hm, _, _ = await _six_weeks(tmp_path / "plan")
    with_plan = await _cost_after_baseline(hm)
    await hm.stop()
    import housemind.energy.manager as manager

    plan: Callable[..., Any] = manager.make_plan

    def nothing_steered(slots: Any, start: Any, settings: Any, made: Any) -> Any:
        bare = replace(
            start,
            has_boiler=False,
            has_heat_pump=False,
            appliances_ready=(),
            has_export_limit=False,
        )
        return plan(slots, bare, settings, made)

    monkeypatch.setattr(manager, "make_plan", nothing_steered)
    plain, _, _ = await _six_weeks(tmp_path / "plain", person_starts=True)
    without_plan = await _cost_after_baseline(plain)
    await plain.stop()
    assert with_plan < without_plan, (with_plan, without_plan)
