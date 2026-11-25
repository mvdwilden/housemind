"""One index of what is in each room (code QA 3, punten 5 en 13) – what someone with a large
house notices: a round no longer grows with the number of rooms times the number of devices.
In a house of 1756 entities a round went from about 15 to about 6 ms; a house twelve times as
big was 28 times as slow. And a device added later still counts in its room at once. The
first test fails without the change; the second keeps the index honest when the house changes.
What each checks is its docstring."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from housemind.core.model import Area, Entity, HouseModel, State
from housemind.knowledge.catalog import Step
from tests.test_round4_climate import _start
from tests.test_speed_big_house import _big_house


async def _asked_per_round(
    tmp_path: Path, copies: int, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, int]:
    hm, _ha, clock = await _big_house(tmp_path / str(copies), copies)
    for _ in range(2):
        await clock.advance(30)
        await hm.tick()
    asked: list[int] = []
    original = HouseModel.area_of

    def counted(self: HouseModel, entity_id: str) -> Area | None:
        asked.append(1)
        return original(self, entity_id)

    monkeypatch.setattr(HouseModel, "area_of", counted)
    await clock.advance(30)
    await hm.tick()
    monkeypatch.setattr(HouseModel, "area_of", original)
    entities = len(hm.model.entities)
    await hm.stop()
    return len(asked), entities


async def test_a_round_grows_with_the_house_not_with_rooms_times_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A house four times as big asks where its devices are about four times as often in a
    round – not sixteen times, as when every room went through every device of the house."""
    small, small_entities = await _asked_per_round(tmp_path, 1, monkeypatch)
    big, big_entities = await _asked_per_round(tmp_path, 4, monkeypatch)
    grew = big_entities / small_entities
    assert big <= small * grew * 1.5, (small, big, grew)


async def test_a_sensor_added_later_counts_in_its_room_at_once(tmp_path: Path) -> None:
    """A humidity sensor added to the bathroom later – a new device in Home Assistant – turns
    the fan on when it is humid: the room's list follows the house, it is not the one from the
    start."""
    noon = datetime(2026, 10, 19, 12, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
    hm, ha, clock = await _start(tmp_path, noon, [])
    assert hm.learning is not None
    hm.learning.steps[("ventilate_humidity", "bathroom")] = Step.AUTONOMOUS
    assert ha.states["fan.bathroom"].state == "off"
    hygro = Entity(
        "sensor.shower_humidity",
        "r-shower",
        "Vocht douche",
        area_id="bathroom",
        device_class="humidity",
        unit="%",
    )
    attrs: dict[str, Any] = {"unit_of_measurement": "%"}
    await ha.add_entity(hygro, State(hygro.entity_id, "50", attrs, clock.now()))
    await hm._refresh_model()  # what HouseMind does after Home Assistant's registry changed
    await clock.advance(61)  # a sensor makes HouseMind think at most once a minute
    await ha.simulate(hygro.entity_id, "85", attrs)
    assert ha.states["fan.bathroom"].state == "on"
    await hm.stop()


def test_one_answer_to_which_sensors_measure_a_rooms_air() -> None:
    """The fan, the stale-air heads-up and the shower ask the same question – which sensors
    measure this room's air – and get the same answer: a sensor in the room counts, one on an
    airco or a humidifier measures that device and does not (code QA 3, punt 5)."""
    from datetime import UTC
    from datetime import datetime as dt

    from housemind.brain.air import co2_by_room
    from housemind.core.model import Device
    from housemind.world.shower import shower_sensors

    model = HouseModel()
    model.areas["bath"] = Area("bath", "Badkamer")
    model.devices["airco"] = Device("airco", "Airco", "bath")
    model.devices["wall"] = Device("wall", "Sensor", "bath")
    model.entities["climate.airco"] = Entity("climate.airco", "r-a", "Airco", "airco")
    for device in ("airco", "wall"):
        for kind in ("humidity", "carbon_dioxide"):
            entity_id = f"sensor.{device}_{kind}"
            model.entities[entity_id] = Entity(
                entity_id, entity_id, kind, device, device_class=kind
            )
    now = dt(2026, 11, 25, tzinfo=UTC)
    states = {
        e: State(e, "1500" if "carbon" in e else "60", {}, now)
        for e in model.entities
        if e.startswith("sensor.")
    }
    assert model.air_sensors(("humidity",)) == {"sensor.wall_humidity": "bath"}
    assert shower_sensors(model, ["bath"]) == {"sensor.wall_humidity": "bath"}
    assert co2_by_room(model, states) == {"bath": 1500.0}
    states["sensor.airco_carbon_dioxide"] = State("sensor.airco_carbon_dioxide", "4000", {}, now)
    assert co2_by_room(model, states) == {"bath": 1500.0}  # the airco's own sensor: not the room
