"""Ordering and filtering (E-258, B-106), from the user's side: what the buttons above the lists
filter on – per automation the rooms, parts, cause and whether it starts a scene or script; the
scenes and scripts of Home Assistant only to look at; devices with their device, part and step.
Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from tests.test_automations import EVENING, ROOT


def _auto(alias: str, triggers: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict:
    return {"id": alias, "alias": alias, "triggers": triggers, "actions": actions}


KITCHEN = _auto(
    "Keukenlicht bij beweging",
    [{"trigger": "state", "entity_id": "binary_sensor.kitchen_motion", "to": "on"}],
    [{"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}],
)
EVENING_SCENE = _auto(
    "Avond in de woonkamer",
    [{"trigger": "sun", "event": "sunset"}],
    [
        {"action": "scene.turn_on", "target": {"entity_id": "scene.evening"}},
        {"scene": "scene.movie"},
    ],
)
OTHERS = {
    "automation.at_seven": _auto(
        "Om zeven uur",
        [{"trigger": "time", "at": "07:00:00"}],
        [{"action": "cover.open_cover", "target": {"entity_id": "cover.living_curtain"}}],
    ),
    "automation.home": _auto(
        "Thuiskomen",
        [
            {
                "trigger": "zone",
                "event": "enter",
                "entity_id": "person.resident_1",
                "zone": "zone.home",
            }
        ],
        [{"action": "notify.mobile_app_phone_1", "data": {"message": "Welkom"}}],
    ),
    "automation.button": _auto(
        "Knop in de gang",
        [{"trigger": "event", "event_type": "zha_event"}],
        [{"action": "script.goodnight"}],
    ),
}


async def _start(tmp_path: Path) -> tuple[HouseMind, Any]:
    clock = SimulatedClock(EVENING)
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    for entity_id, area, members in (
        (
            "scene.evening",
            "living",
            ["light.living_ceiling", "light.living_reading", "cover.living_curtain"],
        ),
        ("scene.movie", None, ["light.living_reading"]),
    ):
        entity = Entity(
            entity_id, f"reg-{entity_id}", entity_id.split(".")[1].title(), area_id=area
        )
        await ha.add_entity(
            entity, State(entity_id, "scening", {"entity_id": members}, clock.now())
        )
    await ha.add_script(
        "script.goodnight",
        "Welterusten",
        {"sequence": [{"action": "light.turn_off", "target": {"entity_id": "light.kitchen"}}]},
    )
    for entity_id, config in {
        "automation.kitchen": KITCHEN,
        "automation.evening": EVENING_SCENE,
        **OTHERS,
    }.items():
        await ha.add_automation(entity_id, str(config["alias"]), config)
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    return hm, ha


async def test_an_automation_says_where_what_and_why(tmp_path: Path) -> None:
    """The kitchen light at motion is in the Keuken, about Verlichting, started by motion; one at
    seven o'clock by time, coming home by coming home, a button by a button."""
    hm, _ha = await _start(tmp_path)
    rows = {r["name"]: r for r in (await views.house_settings(hm, "nl"))["automations"]}
    kitchen = rows["Keukenlicht bij beweging"]
    assert (kitchen["rooms"], kitchen["parts"], kitchen["causes"], kitchen["scene"]) == (
        ["Keuken"],
        ["lighting"],
        ["presence"],
        False,
    )
    assert rows["Om zeven uur"]["causes"] == ["time"] and rows["Om zeven uur"]["parts"] == [
        "covers"
    ]
    assert rows["Thuiskomen"]["causes"] == ["people"] and rows["Thuiskomen"]["parts"] == [
        "messages"
    ]
    assert rows["Knop in de gang"]["causes"] == ["button"]
    await hm.stop()


async def test_scenes_and_scripts_only_to_look_at(tmp_path: Path) -> None:
    """An automation that starts a scene, also in the short form, falls under "Start een scène of
    script"; the scene says where it acts and who starts it. A script is read, never run."""
    hm, ha = await _start(tmp_path)
    settings = await views.house_settings(hm, "nl")
    rows = {r["name"]: r for r in settings["automations"]}
    evening = rows["Avond in de woonkamer"]
    assert evening["scene"] and evening["causes"] == ["sun"] and evening["rooms"] == ["Woonkamer"]
    assert rows["Knop in de gang"]["scene"]  # a script called by its own name
    listed = {r["id"]: r for r in settings["scenesScripts"]}
    scene = listed["scene.evening"]
    assert scene["kind"] == "scene" and scene["rooms"] == ["Woonkamer"]
    assert scene["parts"] == ["lighting", "covers"]
    assert scene["text"] == "Gestart door: Avond in de woonkamer"
    assert listed["scene.movie"]["text"] == "Gestart door: Avond in de woonkamer"
    script = listed["script.goodnight"]
    assert (script["kind"], script["rooms"], script["parts"]) == (
        "script",
        ["Keuken"],
        ["lighting"],
    )
    assert script["text"] == "Gestart door: Knop in de gang"
    assert not [c for c in ha.calls if c[0].startswith(("script.", "scene."))]
    await hm.stop()


async def test_devices_by_room_device_and_step(tmp_path: Path) -> None:
    """Every device and entity says its device, part and step: a sensor only measures, a lamp
    goes as far as HouseMind goes with Verlichting, one left alone says so."""
    hm, _ha = await _start(tmp_path)
    rows = {r["id"]: r for r in views.devices(hm, "nl")}
    assert rows["binary_sensor.kitchen_motion"]["step"] == "measure"
    assert (
        rows["light.kitchen"]["part"] == "lighting" and rows["light.kitchen"]["step"] == "observe"
    )
    assert rows["light.kitchen"]["area"] == "Keuken"
    assert hm.learning is not None and hm.gate is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    hm.gate.state.excluded.add("light.living_reading")
    rows = {r["id"]: r for r in views.devices(hm, "nl")}
    assert rows["light.kitchen"]["step"] == "autonomous"
    assert rows["light.living_reading"]["step"] == "left_alone"
    assert all("device" in r and "typeKey" in r for r in rows.values())
    await hm.stop()
