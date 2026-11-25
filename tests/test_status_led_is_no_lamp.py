"""A sensor's own LED is no lamp, and a room shows its entities per device (E-426). In the real
house a presence sensor's "LED" made a room "licht 35%", and it could become "a lamp on for
hours". Now a light on a device that senses, with a word for a device's own light in its name,
is a status light until an administrator says otherwise – everywhere. Each test fails without
the change; what it checks is its docstring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, Source
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind
from housemind.server import views
from tests.test_house_knowledge_derived import _add, _start

LED = "light.living_radar_led"
RADAR = "binary_sensor.living_radar"
MOUNTING = "switch.living_radar_upside_down"
LAMP = "light.living_radar_lamp"  # a ceiling lamp that senses too: its own name, no LED word


async def _radar(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    """A presence sensor in the living room with its LED on, one of its settings, and a
    ceiling lamp with a sensor of its own."""
    ha.model.devices["device.radar"] = Device(
        "device.radar", "Radar woonkamer", "living", "esphome", model="Presence Pro"
    )
    ha.model.devices["device.lamp"] = Device("device.lamp", "Plafondlamp", "living", "zigbee")
    await _add(
        ha,
        clock,
        Entity(RADAR, "reg-r", "Aanwezigheid", device_id="device.radar", device_class="occupancy"),
    )
    await _add(ha, clock, Entity(LED, "reg-l", "LED", device_id="device.radar"), "on")
    await _add(
        ha,
        clock,
        Entity(
            MOUNTING,
            "reg-m",
            "Upside Down Mounting",
            device_id="device.radar",
            entity_category="config",
        ),
    )
    await _add(ha, clock, Entity(LAMP, "reg-p", "Plafondlamp", device_id="device.lamp"))
    await _add(
        ha,
        clock,
        Entity(
            "binary_sensor.living_lamp_motion",
            "reg-pm",
            "Beweging",
            device_id="device.lamp",
            device_class="motion",
        ),
    )


def _living(hm: HouseMind) -> dict[str, Any]:
    return next(r for r in views.rooms(hm, "nl", 3) if r["id"] == "living")


async def test_a_sensor_s_led_is_no_light_in_the_room_and_no_lamp_to_switch(
    tmp_path: Path,
) -> None:
    """Before anyone answered: the LED does not make the room lit, the lighting leaves it
    alone, and the ceiling lamp that senses too is still a lamp."""
    hm, _clock, _ha = await _start(tmp_path, setup=_radar)
    assert hm.skills is not None
    room = _living(hm)
    lit = [d for d in room["devices"] if d["domain"] == "light" and d["on"] and not d["kind"]]
    assert room["lightsOn"] == len(lit)
    assert LED not in {d["id"] for d in lit}
    lamps = {e.entity_id for e in hm.skills._entities("living", "light")}
    assert LED not in lamps and LAMP in lamps
    await hm.stop()


async def test_the_room_shows_its_entities_per_device_its_settings_last(tmp_path: Path) -> None:
    """Under the sensor's name and type: its LED as a status light, its setting last; what
    has no device comes after the devices."""
    hm, _clock, _ha = await _start(tmp_path, setup=_radar)
    room = _living(hm)
    groups, rows = room["groups"], {d["id"]: d for d in room["devices"]}
    radar = next(g for g in groups if g["name"] == "Radar woonkamer")
    assert radar["model"] == "Presence Pro"
    assert [(i, rows[i]["kind"]) for i in radar["items"]] == [
        (LED, "status_led"),
        (MOUNTING, "setting"),
    ]
    assert next(g for g in groups if g["name"] == "Plafondlamp")["items"] == [LAMP]
    names = [g["name"] for g in groups]
    assert "" not in names or names[-1] == ""
    await hm.stop()


async def test_tapping_the_led_or_a_setting_says_what_it_is(tmp_path: Path) -> None:
    hm, _clock, _ha = await _start(tmp_path, setup=_radar)
    led = await hm.why_device(LED, "nl")
    assert led.startswith("Dit is het lampje van het apparaat zelf")
    setting = await hm.why_device(MOUNTING, "nl")
    assert setting.startswith("Dit is een instelling van het apparaat")
    await hm.stop()


async def test_a_led_on_for_hours_is_never_asked_about(tmp_path: Path) -> None:
    """Someone was in the living room and left; hours later the LED is still on. HouseMind
    asks nothing about it – it is no lamp left on."""
    hm, clock, ha = await _start(tmp_path, setup=_radar)
    await ha.simulate(RADAR, "on")
    await ha.simulate(RADAR, "off")
    for _ in range(12 * 5):
        await clock.advance(300)
        await hm.tick()
    hm._last_review = None
    await hm.tick()
    await hm.settled()
    asked = [n["topic"] for n in await hm.notifications()]
    assert f"unusual:light_on:{LED}" not in asked
    await hm.stop()


async def test_not_right_makes_it_a_lamp_again(tmp_path: Path) -> None:
    """An administrator says it is a real lamp after all: from then on it counts as one."""
    hm, _clock, ha = await _start(tmp_path, setup=_radar)
    assert hm.skills is not None
    assert await hm.answer_knowledge(f"entity:{LED}:status_led", False)
    assert LED in {e.entity_id for e in hm.skills._entities("living", "light")}
    item = next(d for d in _living(hm)["devices"] if d["id"] == LED)
    assert item["kind"] == "" and item["on"]
    await ha.simulate(LED, "off", by=Source.PHYSICAL)
    await hm.stop()
