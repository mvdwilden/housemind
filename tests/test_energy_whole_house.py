"""An energy device without a room in Home Assistant learns its step for the whole house
(E-343): it is never held on Observe only because it has no room. The inverter's export limit
stands for every device HouseMind still steers (E-363)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.core.clock import SimulatedClock
from housemind.core.model import WHOLE_HOUSE
from housemind.demo.houses import load_demo_house
from housemind.energy.plan import Plan, Step
from housemind.knowledge.catalog import Step as Autonomy
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
LIMIT = "number.solar_export_limit"


async def started(tmp_path: Path) -> tuple[HouseMind, Any]:
    clock = SimulatedClock(T0)
    house = load_demo_house("energy_home", clock, "nl")
    model = house.ha.model
    # The inverter without a room, as many inverters are in Home Assistant.
    inverter = model.entities[LIMIT].device_id or ""
    model.devices[inverter] = replace(model.devices[inverter], area_id=None)
    for entity_id, entity in list(model.entities.items()):
        if entity.device_id == inverter:
            model.entities[entity_id] = replace(entity, area_id=None)
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    assert hm.energy is not None and hm.model.area_of(LIMIT) is None
    hm.energy.since = clock.now() - timedelta(days=15)
    await hm.set_house_setting("consent_learning", True)
    return hm, house.ha


def limit_plan() -> Plan:
    step = Step(
        "export",
        "limit",
        T0 - timedelta(hours=1),
        T0 + timedelta(hours=3),
        "negative_price",
        value=0.0,
    )
    return Plan(T0, [], [step], [], [])


async def test_an_inverter_without_a_room_learns_for_the_whole_house(tmp_path: Path) -> None:
    hm, _ha = await started(tmp_path)
    assert hm.energy is not None and hm.world is not None and hm.learning is not None
    hm.energy.plan = limit_plan()
    requests = hm.energy.requests(hm.world.states, hm.clock.now(), hm.model.area_of)
    limit = [r for r in requests if r.entity_id == LIMIT]
    assert limit and limit[0].area_id == WHOLE_HOUSE
    # Observing, HouseMind learns – for the whole house, not for no room at all.
    await hm._energy_do(limit[0], hm.clock.now())
    assert ("energy_export", WHOLE_HOUSE) in hm.learning.first_seen
    assert hm.world.states[LIMIT].state == "100"  # still only watched
    # Once the whole house has earned the step, it acts.
    hm.learning.steps[("energy_export", WHOLE_HOUSE)] = Autonomy.AUTONOMOUS
    await hm.clock.advance(400)
    for request in hm.energy.requests(hm.world.states, hm.clock.now(), hm.model.area_of):
        await hm._energy_do(request, hm.clock.now())
    assert float(hm.world.states[LIMIT].state) == 0.0
    await hm.stop()


async def test_klaar_voor_autonomie_says_the_whole_house(tmp_path: Path) -> None:
    hm, _ha = await started(tmp_path)
    assert hm.learning is not None
    from housemind.brain.readiness import what

    hm.learning.first_seen[("energy_export", WHOLE_HOUSE)] = hm.clock.now()
    assert "_house" not in what(hm, "nl", "energy_export", WHOLE_HOUSE)
    assert hm.names is not None and hm.names.where(WHOLE_HOUSE, "nl") == ""
    await hm.stop()
