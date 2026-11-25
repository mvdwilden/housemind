"""A proposal that made no sense, found in a real house: "Shall I turn on the kitchen island
light, because nobody has been in the kitchen for a while? I learned this from your
automation 'Presence – downstairs hall'." Three causes:

- a light group of the hall's lamps sat in the kitchen in HA, and HouseMind took the group's
  room instead of its lamps';
- an automation where presence in one room switches a lamp in another taught a skill for
  "the kitchen";
- the "dim first" courtesy step became a question of its own ("turn on" at 30 %).

And the Dutch sentence was broken. Each test fails without the fix."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from tests.test_automations import EVENING, _family, _read

ROOT = Path(__file__).resolve().parents[1]
NIGHT = datetime(2026, 10, 19, 20, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


def _hall_empties(target: str) -> dict[str, Any]:
    return {
        "id": "2001",
        "alias": "Presence downstairs hall",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.hall_motion", "to": "off"}],
        "actions": [{"action": "light.turn_off", "target": {"entity_id": target}}],
    }


def test_presence_in_one_room_switching_another_teaches_nothing() -> None:
    model, catalog, params = _family(SimulatedClock(EVENING))
    across = _read(_hall_empties("light.kitchen"), model, catalog, params)
    assert across.learned == ()  # not "light off when leaving" for the kitchen
    assert not across.whole
    same = _read(_hall_empties("light.hall"), model, catalog, params)
    assert same.learned == (("light_off_leave", "hall"),)


def test_a_group_is_read_with_its_lamps_in_their_own_room() -> None:
    model, catalog, params = _family(SimulatedClock(EVENING))
    model.entities["light.hall_group"] = Entity(
        "light.hall_group",
        "r-group",
        "Hall",
        area_id="kitchen",  # misplaced in HA
    )
    model.groups["light.hall_group"] = ("light.hall",)
    reading = _read(_hall_empties("light.hall_group"), model, catalog, params)
    # The automation controls the group and its lamp: HouseMind keeps off both.
    assert reading.controls == {"light.hall_group", "light.hall"}
    assert reading.learned == (("light_off_leave", "hall"),)


async def _start(tmp_path: Path) -> tuple[HouseMind, Any, SimulatedClock]:
    clock = SimulatedClock(NIGHT.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    ha.model.entities["light.hall_group"] = Entity(
        "light.hall_group", "r-group", "Hal beneden", area_id="kitchen"
    )
    ha.states["light.hall_group"] = State(
        "light.hall_group",
        "off",
        {"entity_id": ["light.hall"], "supported_color_modes": ["brightness"]},
        clock.now(),
    )
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, ha, clock


async def test_skills_never_control_a_group(tmp_path: Path) -> None:
    hm, _ha, _clock = await _start(tmp_path)
    assert hm.model.groups == {"light.hall_group": ("light.hall",)}
    assert hm.skills is not None
    kitchen = [e.entity_id for e in hm.skills._entities("kitchen", "light")]
    assert "light.hall_group" not in kitchen and "light.kitchen" in kitchen
    await hm.stop()


async def test_the_question_is_to_turn_off_in_a_whole_sentence(tmp_path: Path) -> None:
    hm, ha, clock = await _start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_off_leave", "living")] = Step.PROPOSE
    await ha.simulate("binary_sensor.living_presence", "on")
    await ha.simulate("light.living_reading", "on", {"brightness": 255})
    await ha.simulate("binary_sensor.living_presence", "off")
    for _ in range(50):  # 25 minutes: past the living room's hold time and the dim minute
        await clock.advance(30)
        await hm.tick()
    open_now = await hm.open_proposals()
    assert [p.action for p in open_now] == ["light.turn_off"]  # never "turn on" to dim
    asked = [n for n in await hm.notifications() if str(n["topic"]).startswith("proposal:")]
    # The question is kept in every language (one language per sentence, 20-10-2026).
    question = hm.languages.render("nl", json.loads(str(asked[0]["params"]))["question"])
    assert question.startswith("Zal ik ") and ", omdat er al een tijdje niemand in de " in question
    assert question.endswith(" is?")
    await hm.stop()


async def test_a_group_has_priority_over_its_lamps(tmp_path: Path) -> None:
    """The administrator's wish: lamps that mostly work as a group (a dining table's lamps in
    a Zigbee2MQTT group, each lamp also on its own) are steered through the group. The
    smallest group in one room steers; "all lights" only gathers. The lamps reporting after
    HouseMind switched the group are HouseMind's own doing, not a person."""
    clock = SimulatedClock(NIGHT.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    lamps = ["light.table_1", "light.table_2", "light.table_3"]
    for lamp in lamps:
        ha.model.entities[lamp] = Entity(lamp, f"r-{lamp}", lamp, area_id="kitchen")
        ha.states[lamp] = State(lamp, "off", {"supported_color_modes": ["brightness"]}, clock.now())
    ha.model.entities["light.table"] = Entity("light.table", "r-t", "Eettafel")
    ha.states["light.table"] = State(
        "light.table",
        "off",
        {"group_entities": lamps, "supported_color_modes": ["brightness"]},
        clock.now(),
    )
    ha.model.entities["light.all"] = Entity("light.all", "r-a", "Alle lichten")
    ha.states["light.all"] = State(
        "light.all", "off", {"group_entities": ["light.table", "light.kitchen"]}, clock.now()
    )
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    assert hm.skills is not None and hm.learning is not None
    kitchen = {e.entity_id for e in hm.skills._entities("kitchen", "light")}
    assert "light.table" in kitchen  # the group steers …
    assert not kitchen & set(lamps)  # … not its lamps one by one
    assert "light.all" not in kitchen  # "all lights" only gathers
    assert hm.model.area_of("light.table").id == "kitchen"  # its room is its lamps' room
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    assert ha.states["light.table"].state == "on"
    tracker = ha.tracker
    from housemind.core.model import Context, Source

    assert tracker.classify("light.table_1", Context(id="z2m-1")) is Source.HOUSEMIND
    await hm.stop()
