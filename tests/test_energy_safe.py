"""Energy, safe (design 29, part A, as kept by E-363): what HouseMind set itself and only that
goes back (E-328), giving back when it pauses, stops or still measures, and the watchdog's list
(E-329, E-343). The export limit stands for every device HouseMind still steers."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.brain.proposals import Answer
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source
from housemind.demo.houses import load_demo_house
from housemind.energy.plan import Plan, Step
from housemind.knowledge.catalog import Step as Autonomy
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
LIMIT = "number.solar_export_limit"
GAP = 360  # past the export limit's five minutes between two changes


async def started(tmp_path: Path, step: Autonomy = Autonomy.AUTONOMOUS) -> tuple[HouseMind, Any]:
    clock = SimulatedClock(T0)
    house = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    assert hm.energy is not None and hm.gate is not None
    hm.gate.state.device_step = lambda skill, area, entity: step
    hm.energy.since = clock.now() - timedelta(days=15)  # the baseline is over
    return hm, house.ha


def plan_of(*steps: Step) -> Plan:
    return Plan(T0, [], list(steps), [], [])


def limit() -> Step:
    return Step(
        "export",
        "limit",
        T0 - timedelta(hours=1),
        T0 + timedelta(hours=1),
        "negative_price",
        value=0.0,
    )


async def follow(hm: HouseMind) -> None:
    """One round of the energy plan, as the tick does it (without making a new plan)."""
    assert hm.energy is not None and hm.world is not None
    now = hm.clock.now()
    hm.energy.watch_own(hm.world.states, now)
    for request in hm.energy.requests(hm.world.states, now, hm.model.area_of):
        await hm._energy_do(request, now)


def shows(hm: HouseMind) -> float:
    assert hm.world is not None
    return float(hm.world.states[LIMIT].state)


async def test_only_what_housemind_set_goes_back(tmp_path: Path) -> None:
    hm, ha = await started(tmp_path)
    assert hm.energy is not None and hm.energy.own is not None and hm.world is not None
    hm.energy.plan = plan_of(limit())
    await follow(hm)
    assert shows(hm) == 0.0
    assert hm.energy.own.before(LIMIT) == "100"
    # The plan no longer needs it: back to exactly what it was.
    hm.energy.plan = plan_of()
    await hm.clock.advance(GAP)
    await follow(hm)
    assert shows(hm) == 100.0
    assert not hm.energy.own.owns(LIMIT)
    # A person's own choice stays: HouseMind never set it.
    await ha.simulate(LIMIT, "60", by=Source.PERSON)
    await follow(hm)
    assert shows(hm) == 60.0
    # HouseMind sets it, then somebody else changes it: HouseMind lets go and says so.
    hm.energy.plan = plan_of(limit())
    hm.gate.state.people_win_until.clear()
    await hm.clock.advance(GAP)
    await follow(hm)
    assert shows(hm) == 0.0
    await ha.simulate(LIMIT, "40", by=Source.PERSON)
    hm.energy.plan = plan_of()
    await follow(hm)
    assert shows(hm) == 40.0
    assert LIMIT in hm.energy.own.others
    from housemind.server.views import energy

    involved = await energy(hm, "nl", 3)
    assert involved is not None and "Iemand anders veranderde" in involved["sentence"]
    informed = await energy(hm, "nl", 2)
    assert informed is not None and "Iemand anders" not in informed["sentence"]
    await hm.stop()


async def test_a_yes_on_a_proposal_is_given_back_too(tmp_path: Path) -> None:
    """Energy on Proposing (E-343): carried out after a "Do it", and still given back."""
    hm, _ha = await started(tmp_path, Autonomy.PROPOSE)
    assert hm.energy is not None and hm.energy.own is not None and hm.world is not None
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("u1", "Resident", 3, hm.clock.now().isoformat()),
    )
    await hm._refresh_people()
    hm.energy.plan = plan_of(limit())
    await follow(hm)
    assert shows(hm) == 100.0
    proposals = await hm.open_proposals()
    assert proposals
    await hm.answer(proposals[0].id, Answer.DO, "u1")
    assert shows(hm) == 0.0
    assert hm.energy.own.owns(LIMIT)
    hm.energy.plan = plan_of()
    await hm.clock.advance(GAP)
    await follow(hm)
    assert shows(hm) == 100.0  # no new question for this
    await hm.stop()


async def test_pause_stop_and_the_baseline_give_back(tmp_path: Path) -> None:
    hm, ha = await started(tmp_path)
    assert hm.energy is not None and hm.energy.own is not None and hm.world is not None
    hm.energy.plan = plan_of(limit())
    await follow(hm)
    assert hm.energy.own.owns(LIMIT)
    # The watchdog in Home Assistant gets the list with every answer (B-204).
    state = await hm.bridge_state()
    assert state["fallback"] == [
        {"entity_id": LIMIT, "action": "number.set_value", "data": {"value": 100.0}}
    ]
    # A pause gives back what HouseMind set itself (E-343).
    await hm.pause(60)
    await hm._energy_tick(hm.clock.now())
    assert shows(hm) == 100.0
    await hm.pause(None)  # resume
    # Still measuring (the baseline): HouseMind steers nothing and gives back.
    hm.energy.plan = plan_of(limit())
    await hm.clock.advance(GAP)
    await follow(hm)
    assert hm.energy.own.owns(LIMIT)
    hm.energy.since = hm.clock.now()
    await hm.clock.advance(GAP)
    await follow(hm)
    assert not hm.energy.own.owns(LIMIT)
    # Stopping gives back as well (E-329).
    hm.energy.since = hm.clock.now() - timedelta(days=15)
    await hm.clock.advance(GAP)
    await follow(hm)
    assert hm.energy.own.owns(LIMIT)
    await hm.stop()
    assert ha.calls[-1] == ("number.set_value", LIMIT, {"value": 100.0})


async def test_the_watchdog_gave_back_while_housemind_was_silent(tmp_path: Path) -> None:
    hm, _ha = await started(tmp_path)
    assert hm.energy is not None and hm.energy.own is not None
    hm.energy.plan = plan_of(limit())
    await follow(hm)
    hm.watchdog_done()
    assert not hm.energy.own.owns(LIMIT) and (await hm.bridge_state())["fallback"] == []
    await hm.stop()


async def test_the_register_of_the_first_energy_build_is_taken_over(tmp_path: Path) -> None:
    hm, _ha = await started(tmp_path)
    await hm.db.set_setting("energy_own", {"export_before": 80.0, "charger_ours": True})
    await hm.stop()
    hm2, _ = await started(tmp_path)
    assert hm2.energy is not None and hm2.energy.own is not None
    assert hm2.energy.own.before(LIMIT) == "80.0"
    assert set(hm2.energy.own.items) == {LIMIT}  # the charger is no longer HouseMind's
    await hm2.stop()


# -- the watchdog in Home Assistant (B-204, K5) ------------------------------------------------


def _watchdog_module() -> Any:
    path = ROOT / "integration" / "custom_components" / "housemind" / "watchdog.py"
    spec = importlib.util.spec_from_file_location("hm_watchdog", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_the_watchdog_gives_back_after_five_silent_minutes() -> None:
    module = _watchdog_module()
    now = [T0]
    saved: list[dict[str, Any]] = []
    calls: list[tuple[str, str, dict[str, Any]]] = []
    notes: list[tuple[str, str]] = []

    async def save(data: dict[str, Any]) -> None:
        saved.append(data)

    async def call(domain: str, service: str, data: dict[str, Any]) -> None:
        calls.append((domain, service, data))

    async def notify(title: str, text: str) -> None:
        notes.append((title, text))

    dog = module.Watchdog(
        lambda: now[0], save, call, notify, lambda: "nl", lambda e: "Teruglevering"
    )
    await dog.contact(
        [
            {"entity_id": LIMIT, "action": "number.set_value", "data": {"value": 100.0}},
            {"entity_id": "lock.front", "action": "lock.unlock", "data": {}},  # never allowed
            {"entity_id": "switch.x", "action": "number.set_value", "data": {}},  # wrong domain
        ]
    )
    assert len(dog.items) == 1 and saved
    now[0] = T0 + timedelta(minutes=4)
    assert not await dog.check()
    now[0] = T0 + timedelta(minutes=5)
    assert await dog.check()
    assert calls == [("number", "set_value", {"value": 100.0, "entity_id": LIMIT})]
    assert notes and "Teruglevering" in notes[0][1] and notes[0][0] == "HouseMind reageerde niet"
    assert dog.items == [] and dog.done_at is not None
    assert not await dog.check()  # only once
    # A restart of Home Assistant keeps the list; HouseMind's next answer clears "done".
    again = module.Watchdog(lambda: now[0], save, call, notify, lambda: "en", str)
    again.load(saved[-1])
    assert again.done_at == dog.done_at
    await again.contact([])
    assert again.done_at is None
