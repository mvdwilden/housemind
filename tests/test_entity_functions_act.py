"""Every entity's function, part C: HouseMind acts on it everywhere (E-430, E-431, design
51.6). A setting or a status is never switched by HouseMind or on an AI's request; what a person
says an entity is holds for the climate, a room's temperature, the doors and windows and who is
where. Each test fails without the build; what it checks is its docstring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from housemind.ai.providers import AIResult
from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind
from tests.test_ai import FakeAI, enable
from tests.test_house_knowledge_derived import _add, _start

LOCK = "switch.oven_child_lock"
DOOR = "binary_sensor.front_door"
PRESENCE = "binary_sensor.living_presence"
CLIMATE = "climate.living"


class SettingAI(FakeAI):
    """An AI that calls the oven's child lock a setting, and, asked to, suggests switching it."""

    def __init__(self) -> None:
        super().__init__([])
        self.lock_label = ""

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        self.sent.append((system, user, model))
        data = json.loads(user.removeprefix("<data>\n").removesuffix("\n</data>"))
        reply: dict[str, Any] = {}
        if "entities" in data:
            reply["functions"] = {
                label: {"function": "setting", "certainty": 0.9, "why": "Een kinderslot."}
                for label, facts in data["entities"].items()
                if facts.get("name") == "Child Lock"
            }
        elif "question" in data:
            reply = {
                "answer": "Ik zet het kinderslot uit.",
                "about_house": True,
                "suggestions": [{"device": self.lock_label, "action": "switch.turn_off"}],
            }
        return AIResult(ok=True, text=json.dumps(reply), input_tokens=900, output_tokens=200)


async def _oven(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    ha.model.devices["device.oven"] = Device(
        "device.oven", "Oven", "kitchen", "appliances", "Bakeware", "Bake 1"
    )
    lock = Entity(LOCK, "r1", "Kinderslot", device_id="device.oven", original_name="Child Lock")
    await _add(ha, clock, lock, "on")


async def test_an_ai_never_switches_a_setting(tmp_path: Path) -> None:
    """The AI said the child lock is a setting. Asked to switch it off, it suggests so – and
    HouseMind says that is a setting of the device and leaves it as it is."""
    hm, _clock, ha = await _start(tmp_path, setup=_oven)
    fake = SettingAI()
    await enable(hm, fake)
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    assert hm.world is not None and hm.world.functions[LOCK] == "setting"
    fake.lock_label = hm.ai.labels.to_label[LOCK]
    answer = await hm.ask("Zet het kinderslot van de oven uit", "nl", "demo-user-1")
    assert answer["ok"] and not answer["proposals"]
    assert any("instelling" in why for why in answer["refused"])
    assert ha.states[LOCK].state == "on"
    await hm.stop()


async def _person_says(hm: HouseMind, entity_id: str, function: str) -> None:
    """An administrator said what it is (Anders…): kept as theirs."""
    now = hm.clock.now()
    value = {"function": function, "why": None}
    await hm.knowledge.suggest("entity", entity_id, "function", value, None, now, None, "person")
    assert await hm.answer_knowledge(f"entity:{entity_id}:function", True)


async def test_what_a_person_says_it_is_holds_everywhere(tmp_path: Path) -> None:
    """The thermostat is the heat pump's own control, the front door contact is a device's,
    and the living room's presence sensor senses no people: from then on the climate leaves
    the thermostat alone, the door is no opening of the house, and the living room stays empty
    when that sensor is on. The rules held before."""
    hm, _clock, ha = await _start(tmp_path)
    assert hm.skills is not None and hm.world is not None and hm.skills.room_climates is not None
    assert CLIMATE in hm.skills.room_climates and DOOR in hm.openings
    await _person_says(hm, CLIMATE, "device_control")
    await _person_says(hm, DOOR, "device_state")
    await _person_says(hm, PRESENCE, "device_state")
    assert CLIMATE not in hm.skills.room_climates
    assert DOOR not in hm.openings and DOOR not in (hm.skills.openings or set())
    await ha.simulate(PRESENCE, "on")
    await hm.settled()
    assert not hm.world.snapshot(hm.clock.now()).occupied("living")
    await hm.stop()


THERMOMETER = "sensor.living_thermometer"


async def _thermometer(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    sensor = Entity(THERMOMETER, "r9", "Temperatuur", area_id="living", device_class="temperature")
    await _add(ha, clock, sensor, "21")


async def test_a_sensor_a_person_says_is_the_device_s_own_no_longer_measures_the_room(
    tmp_path: Path,
) -> None:
    """A thermometer the rules count for the living room: an administrator says it measures the
    device itself. The room's temperature no longer uses it."""
    hm, _clock, _ha = await _start(tmp_path, setup=_thermometer)
    assert THERMOMETER in hm.temperatures.sources("living")
    await _person_says(hm, THERMOMETER, "device_temperature")
    assert THERMOMETER not in hm.temperatures.sources("living")
    await hm.stop()
