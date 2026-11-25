"""Every entity's function (E-427, E-428, E-432, design 51), from the user's side. A room shows,
per device, what each entity is: the room's thermostat, a control of the heat pump itself, a
status LED, a setting – or not known yet. Without AI, from what Home Assistant says and the
rules. Each test fails without the build; what it checks is its docstring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity
from housemind.ha.fake import FakeHomeAssistant
from housemind.parts.thinking import MODEL_SETTLE
from housemind.runtime import HouseMind
from housemind.server import views
from tests.test_house_knowledge_derived import _add, _start

ROOM_CLIMATE = "climate.heatpump_room"
OFFSET = "climate.heatpump_leaving_water_offset"
WATER = "sensor.heatpump_leaving_water_temperature"
POWER = "sensor.heatpump_power"
LED = "light.living_radar_led"
MOUNTING = "switch.living_radar_upside_down"
SENSITIVITY = "number.living_radar_sensitivity"
RADAR = "binary_sensor.living_radar"
BATTERY = "sensor.living_radar_battery"
RELAY = "switch.living_relay"
OUTSIDE = "sensor.garden_temperature"
HEAT = {"hvac_modes": ["heat", "off"], "min_temp": 7, "max_temp": 30}


async def _house(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    """A heat pump with the room's thermostat and a leaving-water control, a presence sensor
    with its LED and settings, a relay nobody says what it switches, and a sensor outside."""
    ha.model.devices["device.heatpump"] = Device(
        "device.heatpump", "Warmtepomp", "living", "heatpump", model="Air X"
    )
    ha.model.devices["device.radar"] = Device(
        "device.radar", "Radar woonkamer", "living", "esphome", model="Presence Pro"
    )
    pump = {"device_id": "device.heatpump"}
    radar = {"device_id": "device.radar"}
    for entity, state in (
        (Entity(ROOM_CLIMATE, "r1", "Room Temperature", capabilities=HEAT, **pump), "heat"),
        (Entity(OFFSET, "r2", "Leaving Water Offset", capabilities=HEAT, **pump), "heat"),
        (
            Entity(WATER, "r3", "Leaving Water Temperature", device_class="temperature", **pump),
            "29",
        ),
        (Entity(POWER, "r4", "Power", device_class="power", unit="W", **pump), "400"),
        (Entity(LED, "r5", "LED", **radar), "on"),
        (Entity(MOUNTING, "r6", "Upside Down Mounting", entity_category="config", **radar), "off"),
        (Entity(SENSITIVITY, "r7", "Sensitivity", **radar), "5"),
        (Entity(RADAR, "r8", "Aanwezigheid", device_class="occupancy", **radar), "off"),
        (Entity(BATTERY, "r9", "Battery", device_class="battery", unit="%", **radar), "80"),
        (Entity(RELAY, "r10", "Relais", area_id="living"), "off"),
        (Entity(OUTSIDE, "r11", "Temperatuur", area_id="garden", device_class="temperature"), "9"),
    ):
        await _add(ha, clock, entity, state)


def _items(hm: HouseMind) -> dict[str, dict[str, Any]]:
    room = next(r for r in views.rooms(hm, "nl", 3) if r["id"] == "living")
    return {i["id"]: i for i in room["devices"]}  # the groups name them by id


async def test_the_room_says_what_each_entity_is(tmp_path: Path) -> None:
    """The heat pump's room thermostat and its leaving-water control are told apart; the LED is
    a status LED, the mounting switch a setting, the relay not known yet. Nothing came from AI."""
    hm, _clock, _ha = await _start(tmp_path, setup=_house)
    items = _items(hm)
    assert items[ROOM_CLIMATE]["function"] == "thermostaat van de ruimte"
    assert items[OFFSET]["function"] == "bediening van het apparaat"
    assert items[LED]["function"] == "status-LED" and items[LED]["kind"] == "status_led"
    assert items[MOUNTING]["function"] == "instelling" and items[MOUNTING]["kind"] == "setting"
    assert items[RELAY]["function"] == "nog onbekend"
    assert not any(i["fromAi"] for i in items.values())
    await hm.stop()


async def test_every_entity_of_a_device_gets_its_function_from_the_rules(tmp_path: Path) -> None:
    """Also what the room does not list: the water temperature and the power of the heat pump
    are the device's own, a sensitivity is a setting, the battery a status, the radar sees
    presence, and the temperature in the garden is outside."""
    hm, _clock, _ha = await _start(tmp_path, setup=_house)
    assert hm.world is not None
    functions = hm.world.functions
    assert functions[WATER] == "water_temperature"
    assert functions[POWER] == "device_power"
    assert functions[SENSITIVITY] == "setting"
    assert functions[BATTERY] == "battery"
    assert functions[RADAR] == "presence"
    assert functions[OUTSIDE] == "outside_temperature"
    assert {hm.world.function_sources[e] for e in (WATER, POWER, RADAR)} == {"rules"}
    await hm.stop()


async def test_a_new_device_from_home_assistant_gets_its_functions(tmp_path: Path) -> None:
    """A presence sensor added later in Home Assistant: after the house is read again its
    entities have their function too."""
    hm, clock, ha = await _start(tmp_path)
    assert hm.world is not None
    await _house(ha, clock)
    await clock.advance(MODEL_SETTLE.total_seconds() + 1)
    await hm.tick()
    assert hm.world.functions[LED] == "status_light"
    assert hm.world.functions[MOUNTING] == "setting"
    await hm.stop()
