"""Round 4 of the device audit: climate, ventilation and the light sensor. Each test fails
without the fix; the finding it comes from is in its docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]


async def _start(
    tmp_path: Path, at: datetime, extra: list[tuple[Entity, str, dict[str, Any]]]
) -> tuple[HouseMind, Any, SimulatedClock]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    ha.model.devices["pool"] = Device("pool", "Zwembad warmtepomp", "garden")
    ha.model.devices["hygro"] = Device("hygro", "Vochtsensor", "bathroom")
    for entity, value, attrs in extra:
        ha.model.entities[entity.entity_id] = entity
        ha.states[entity.entity_id] = State(entity.entity_id, value, attrs, clock.now())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 4, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, ha, clock


def _requests(hm: HouseMind, entity_id: str) -> list[tuple[str, dict[str, Any]]]:
    assert hm.skills is not None and hm.world is not None
    snap = hm.world.snapshot(hm.clock.now())
    return [
        (r.action, r.data)
        for r in hm.skills.evaluate(snap, hm.world.states)
        if r.entity_id == entity_id
    ]


async def test_cooling_is_left_alone_and_a_pool_is_no_room(tmp_path: Path) -> None:
    """An airco got heating rules ("window open → 12 °C" = full cooling); a pool heat pump
    shown as climate got a room's setpoint and counted as the garden's temperature."""
    evening = datetime(2026, 7, 19, 19, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    pool = Entity(
        "climate.pool",
        "r-pool",
        "Zwembad",
        device_id="pool",
        capabilities={"min_temp": 10, "max_temp": 40, "hvac_modes": ["heat", "off"]},
    )
    hm, ha, _clock = await _start(tmp_path, evening, [(pool, "heat", {"temperature": 28})])
    assert "climate.pool" not in hm.temperatures.rooms_climate
    await ha.simulate("climate.living", "cool", {"temperature": 24})
    await ha.simulate("binary_sensor.living_window", "on")
    assert _requests(hm, "climate.living") == []  # never "12 °C" while cooling
    assert _requests(hm, "climate.pool") == []
    await hm.stop()


async def test_ventilation_finds_the_room_s_sensor_through_its_device(tmp_path: Path) -> None:
    """Ventilation only found sensors with their own room, not the ones that take their room
    from their device – the usual case – so it hardly ever ran."""
    noon = datetime(2026, 10, 19, 12, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    hygro = Entity(
        "sensor.hygro_humidity",
        "r-h",
        "Vocht",
        device_id="hygro",
        device_class="humidity",
        unit="%",
    )
    old = Entity(
        "sensor.bathroom_humidity", "r-old", "Oud", area_id="bathroom", device_class="humidity"
    )
    hm, ha, clock = await _start(tmp_path, noon, [(hygro, "40", {}), (old, "unavailable", {})])
    assert hm.learning is not None
    hm.learning.steps[("ventilate_humidity", "bathroom")] = Step.AUTONOMOUS
    await ha.simulate("sensor.hygro_humidity", "120")  # broken: not a reason
    assert ha.states["fan.bathroom"].state == "off"
    await clock.advance(61)  # a sensor makes HouseMind think at most once a minute
    await ha.simulate("sensor.hygro_humidity", "85")
    assert ha.states["fan.bathroom"].state == "on"
    await hm.stop()


def _considered(hm: HouseMind, entity_id: str, action: str) -> bool:
    return any(
        d.request.entity_id == entity_id and d.request.action == action for d in hm.status.thoughts
    )


async def test_covers_follow_the_outside_not_a_grey_room(tmp_path: Path) -> None:
    """Covers closed at 13:00 on a grey afternoon (the room's lux below 40), and then "once
    today" blocked the real close in the evening."""
    afternoon = datetime(2026, 10, 19, 13, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    lux = Entity(
        "sensor.living_lux", "r-lux", "Licht", area_id="living", device_class="illuminance"
    )
    hm, ha, _clock = await _start(tmp_path, afternoon, [(lux, "12", {})])
    await ha.simulate("sun.sun", "above_horizon", {"elevation": 25.0})
    await ha.simulate("sensor.living_lux", "11")
    assert not _considered(hm, "cover.living_curtain", "cover.close_cover")
    await ha.simulate("sun.sun", "below_horizon", {"elevation": -4.0})
    assert _considered(hm, "cover.living_curtain", "cover.close_cover")
    await hm.stop()
