"""Which temperature is the room's (E-209), and a socket that gets too hot (E-210).

Found in a real house: rooms showed around 30 °C. Every temperature sensor counted – the
one inside each wall socket, a radio chip, a tablet's battery, an oven, and a presence
sensor that warms itself – while small air sensors measured 20–21 °C. They all call
themselves "Temperature". Each test fails without the fix."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Device, Entity, HouseModel, State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.world.climate import sort_temperatures

ROOT = Path(__file__).resolve().parents[1]
NOON = datetime(2026, 10, 19, 12, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
WORDS = ["chip", "batterij", "battery", "oven", "buiten", "outdoor", "tank", "water"]


def _house() -> tuple[HouseModel, dict[str, str]]:
    model = HouseModel()
    for area in ("living", "kitchen", "hall", "garden", "bedroom"):
        model.areas[area] = Area(area, area.title())
    devices = {
        "socket": "living",  # a wall socket: switch + its own temperature
        "air": "living",  # a small air sensor: temperature, humidity, battery
        "presence": "bedroom",  # a presence sensor that also measures (and warms itself)
        "radio": "hall",  # a Zigbee coordinator's chip
        "tablet": "kitchen",  # a wall tablet's battery
        "oven": "kitchen",  # an oven with a switch and its inside temperature
        "pump": "bedroom",  # a heat pump: climate + its own outdoor sensor
        "garden_air": "garden",
    }
    for device, area in devices.items():
        model.devices[device] = Device(device, device.title(), area)

    def add(
        eid: str,
        device: str | None,
        cls: str | None = None,
        category: str | None = None,
        name: str = "Temperature",
        area: str | None = None,
    ) -> None:
        model.entities[eid] = Entity(
            eid,
            f"r-{eid}",
            name,
            device_id=device,
            area_id=area,
            device_class=cls,
            entity_category=category,
        )

    add("switch.socket", "socket", name="Socket")
    add("sensor.socket_device_temperature", "socket", "temperature", "diagnostic")
    add("sensor.socket_power", "socket", "power")
    add("sensor.air_temperature", "air", "temperature")
    add("sensor.air_humidity", "air", "humidity")
    add("sensor.air_battery", "air", "battery", "diagnostic")
    add("binary_sensor.presence_occupancy", "presence", "occupancy")
    add("sensor.presence_temperature", "presence", "temperature")
    add("sensor.radio_chip_temperature", "radio", "temperature", name="Zigbee chip temperatuur")
    add("sensor.tablet_battery_temperature", "tablet", "temperature", name="Batterijtemperatuur")
    add("sensor.tablet_light", "tablet", "illuminance")
    add("switch.oven_power", "oven", name="Power")
    add("sensor.oven_cavity", "oven", "temperature", name="Huidige temperatuur in de oven")
    add("climate.pump", "pump", name="Heat pump")
    add("sensor.pump_outdoor", "pump", "temperature", name="Buiten temperatuur")
    add("sensor.garden_air_temperature", "garden_air", "temperature")
    return model, {
        "living": "living",
        "kitchen": "kitchen",
        "hall": "hall",
        "garden": "outside",
        "bedroom": "bedroom",
    }


def test_only_what_measures_a_room_counts_for_the_room() -> None:
    model, roles = _house()
    found = sort_temperatures(model, roles, WORDS)
    assert found.sources("living") == ["sensor.air_temperature"]  # not the socket's 31 °C
    assert found.devices == {"sensor.socket_device_temperature"}  # watched for heat only
    assert found.sources("hall") == []  # a chip is not the hall
    assert found.sources("kitchen") == []  # a battery and an oven are not the kitchen
    assert found.sources("garden") == []  # outside is the weather, not a room
    # The heat pump's thermostat measures the bedroom; the presence sensor only counts when
    # there is nothing better.
    assert found.sources("bedroom") == ["climate.pump"]
    del model.entities["climate.pump"]
    assert sort_temperatures(model, roles, WORDS).sources("bedroom") == [
        "sensor.presence_temperature"
    ]
    states = {
        "sensor.air_temperature": State("sensor.air_temperature", "20.9", {}, NOON),
        "sensor.presence_temperature": State("sensor.presence_temperature", "31.7", {}, NOON),
    }
    assert found.of_room("living", states) == 20.9
    states["sensor.air_temperature"] = State("sensor.air_temperature", "48", {}, NOON)
    assert found.of_room("living", states) is None  # a broken or misplaced sensor


async def _start(tmp_path: Path) -> tuple[HouseMind, FakeHomeAssistant]:
    clock = SimulatedClock(NOON.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    ha.model.devices["socket"] = Device("socket", "WCD Versterkers", "living")
    ha.model.devices["air"] = Device("air", "Temperatuursensor", "living")
    for entity, value in (
        (Entity("switch.wcd", "r-wcd", "WCD Versterkers", device_id="socket"), "on"),
        (
            Entity(
                "sensor.wcd_device_temperature",
                "r-wcdt",
                "Temperatuur",
                device_id="socket",
                device_class="temperature",
                entity_category="diagnostic",
            ),
            "31",
        ),
        (
            Entity(
                "sensor.air_temperature",
                "r-air",
                "Temperatuur",
                device_id="air",
                device_class="temperature",
            ),
            "20.9",
        ),
    ):
        ha.model.entities[entity.entity_id] = entity
        ha.states[entity.entity_id] = State(entity.entity_id, value, {}, clock.now())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 4, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, ha


async def test_the_screens_and_the_ai_show_the_room_not_the_socket(tmp_path: Path) -> None:
    hm, ha = await _start(tmp_path)
    living = next(r for r in views.rooms(hm, "nl", 3) if r["id"] == "living")
    thermostat = float(ha.states["climate.living"].attributes["current_temperature"])
    assert living["temperature"] == round((20.9 + thermostat) / 2, 1)  # never 31 °C
    inside = await hm.inside_temperature()
    assert inside is not None and inside < 25
    label = hm.ai.labels.area("living")
    assert label is not None
    assert hm.house_facts()["rooms"][label]["climate"]["temperature"] == living["temperature"]
    await hm.stop()


async def test_a_socket_that_gets_too_hot_is_reported(tmp_path: Path) -> None:
    hm, ha = await _start(tmp_path)

    async def hot() -> list[dict[str, object]]:
        return [n for n in await hm.notifications() if "device_hot" in str(n["topic"])]

    await ha.simulate("sensor.wcd_device_temperature", "45")
    assert not await hot()  # warm is normal
    await ha.simulate("sensor.wcd_device_temperature", "72")
    told = await hot()
    assert told and told[0]["kind"] == "important"
    text = hm.languages.text("nl", str(told[0]["text_key"]), **json.loads(str(told[0]["params"])))
    assert text.startswith("WCD Versterkers wordt heet: 72 °C")
    hm._refresh_orb()
    assert hm.status.orb == "attention"
    await ha.simulate("sensor.wcd_device_temperature", "88")
    told = await hot()
    assert len(told) == 1 and told[0]["kind"] == "emergency"  # also reaches people at night
    await ha.simulate("sensor.wcd_device_temperature", "55")
    assert not await hot()  # cooled down: the message goes away
    assert ha.states["switch.wcd"].state == "on"  # HouseMind never switches it off itself
    await hm.stop()
