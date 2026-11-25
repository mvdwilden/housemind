from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from housemind.core.clock import SimulatedClock
from housemind.core.model import Source, StateChange
from housemind.demo.houses import DEMO_HOUSES, load_demo_house
from housemind.demo.timemachine import ScenarioStep, TimeMachine, standard_routine
from housemind.knowledge.catalog import Catalog
from housemind.knowledge.matcher import MatchQuality, recognise
from housemind.safety.emergency import EmergencyReactions
from housemind.safety.gate import GateState, SafetyGate

MONDAY = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("key", DEMO_HOUSES)
@pytest.mark.parametrize("language", ["en", "nl", "de", "fr"])
def test_demo_houses_load_in_every_language(key: str, language: str, catalog: Catalog) -> None:
    house = load_demo_house(key, SimulatedClock(MONDAY), language)
    assert house.ha.model.entities
    assert all(area.name for area in house.ha.model.areas.values())
    # Every demo device is known to the catalogue (at least generically).
    for entity in house.ha.model.entities.values():
        if entity.domain == "person":
            continue
        assert recognise(catalog, entity).quality is not MatchQuality.UNKNOWN, entity.entity_id


def test_demo_names_follow_language() -> None:
    nl = load_demo_house("apartment", SimulatedClock(MONDAY), "nl")
    fr = load_demo_house("apartment", SimulatedClock(MONDAY), "fr")
    assert nl.ha.model.areas["living"].name == "Woonkamer"
    assert fr.ha.model.areas["living"].name == "Salon"
    assert nl.name == "Compact appartement"


async def test_time_machine_plays_a_week_fast() -> None:
    clock = SimulatedClock(MONDAY)
    house = load_demo_house("family_home", clock, "nl")
    seen: list[StateChange] = []

    async def listener(change: StateChange) -> None:
        seen.append(change)

    house.ha.on_state_change(listener)
    machine = TimeMachine(clock, house.ha)
    started = time.perf_counter()
    stats = await machine.run_days(7, standard_routine(house, UTC), seed=7)
    elapsed = time.perf_counter() - started
    assert stats.days == 7
    assert stats.steps > 100
    assert clock.now() == MONDAY + timedelta(days=7)
    assert elapsed < 10  # a week in seconds (E-99)
    assert all(c.source in (Source.PHYSICAL, Source.UNKNOWN) for c in seen)
    # Same seed, same week.
    clock2 = SimulatedClock(MONDAY)
    house2 = load_demo_house("family_home", clock2, "nl")
    stats2 = await TimeMachine(clock2, house2.ha).run_days(7, standard_routine(house2, UTC), seed=7)
    assert stats2.steps == stats.steps


async def test_smoke_in_demo_house_triggers_safe_reactions(catalog: Catalog) -> None:
    clock = SimulatedClock(MONDAY + timedelta(hours=20))
    house = load_demo_house("family_home", clock, "en")
    gate = SafetyGate(clock, house.ha, catalog, house.ha.model, GateState())
    reactions = EmergencyReactions(clock, catalog, house.ha.model)
    executed: list[str] = []

    async def on_change(change: StateChange) -> None:
        event = reactions.react(change)
        if event is None:
            return
        for request in event.requests:
            decision = await gate.submit(request)
            if decision.executed:
                executed.append(request.entity_id)

    house.ha.on_state_change(on_change)
    machine = TimeMachine(clock, house.ha)
    await machine.play(
        [ScenarioStep(clock.now() + timedelta(minutes=5), "binary_sensor.kitchen_smoke", "on")]
    )
    assert "light.kitchen" in executed and "light.hall" in executed
    assert house.ha.states["fan.bathroom"].state == "off"
    assert house.ha.states["lock.front_door"].state == "locked"
    assert house.ha.states["cover.garage_door"].state == "closed"
    assert house.ha.states["alarm_control_panel.home"].state == "disarmed"
