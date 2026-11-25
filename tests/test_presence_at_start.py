"""Who is where from the very start, and learning that counts only what happened (found in the
real house), from the user's side. Each test fails without it; what it checks is its
docstring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from housemind.brain.learning import Result
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source, State
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from tests.helpers import wait_seconds as _wait
from tests.test_automations import EVENING, ROOT

LIVING = "binary_sensor.living_presence"
READING = "light.living_reading"


async def _house(
    tmp_path: Path, before: dict[str, tuple[str, dict[str, Any]]]
) -> tuple[HouseMind, SimulatedClock, Any]:
    """The family house with some things already on before HouseMind starts."""
    clock = SimulatedClock(EVENING)
    house = load_demo_house("family_home", clock, "nl")
    for entity_id, (state, attributes) in before.items():
        old = house.ha.states[entity_id]
        house.ha.states[entity_id] = State(
            entity_id, state, {**old.attributes, **attributes}, clock.now()
        )
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    return hm, clock, house.ha


def _misses(hm: HouseMind, clock: SimulatedClock) -> float:
    assert hm.learning is not None
    return hm.learning.total("light_off_leave", "living", Result.MISS, clock.now())


async def test_someone_already_in_a_room_at_the_start_is_there(tmp_path: Path) -> None:
    """Found in the real house: after a start (an update, a restart) a room where someone sat
    counted as empty until a sensor changed – and the lamp on there counted against "light off
    when leaving". Now what the sensors see at the start counts at once."""
    hm, clock, _ha = await _house(
        tmp_path, {LIVING: ("on", {}), "light.living_ceiling": ("on", {})}
    )
    assert hm.world is not None and hm.learning is not None
    assert hm.world.snapshot(clock.now()).occupancy["living"].occupied
    await _wait(hm, clock, 40 * 60)
    assert not [p for p in hm.learning.pending if p.skill == "light_off_leave"]
    assert _misses(hm, clock) == 0
    await hm.stop()


async def test_what_changed_while_housemind_did_not_listen_counts(tmp_path: Path) -> None:
    """Home Assistant away for a while and back: what the sensors see now counts – someone who
    left meanwhile is gone, someone who came in is there."""
    hm, clock, ha = await _house(tmp_path, {LIVING: ("on", {})})
    assert hm.world is not None
    await ha.drop()
    ha.states[LIVING] = State(LIVING, "off", {}, clock.now())
    ha.states["binary_sensor.hall_motion"] = State(
        "binary_sensor.hall_motion", "on", {}, clock.now()
    )
    await ha.come_back()
    await _wait(hm, clock, 10)
    occupancy = hm.world.snapshot(clock.now()).occupancy
    assert not occupancy["living"].occupied
    assert occupancy["hall"].occupied
    await hm.stop()


async def test_coming_back_when_nothing_was_dimmed_is_nothing_to_learn(tmp_path: Path) -> None:
    """On Observe the lamp is not dimmed as a warning. Someone coming back then is no reason
    to "turn it back on" – that counted as "not like that" for a lamp that simply stayed on."""
    hm, clock, ha = await _house(tmp_path, {READING: ("on", {"brightness": 200})})
    assert hm.skills is not None and hm.learning is not None
    await _wait(hm, clock, 10)
    assert "living" in hm.skills._dimmed  # the moment HouseMind would warn
    assert ha.states[READING].attributes["brightness"] == 200  # Observe: it did not
    await ha.simulate(LIVING, "on")
    await _wait(hm, clock, 10 * 60)
    assert not [
        p for p in hm.learning.pending if p.action.endswith("turn_on") and p.entity_id == READING
    ]
    assert _misses(hm, clock) == 0
    await hm.stop()


async def test_an_automation_quicker_than_housemind_teaches_it(tmp_path: Path) -> None:
    """An automation in Home Assistant that switches the lamp off once the room is empty, before
    HouseMind's own wait is over, did what "light off when leaving" would do: it counts as a good
    experience (half, like everything an automation does). Switching a lamp off while someone
    is still there is no "leaving"."""
    hm, clock, ha = await _house(tmp_path, {LIVING: ("on", {}), READING: ("on", {})})
    assert hm.learning is not None
    await ha.simulate(READING, "off", by=Source.PERSON)  # still there: nothing for this skill
    assert hm.learning.total("light_off_leave", "living", Result.HIT, clock.now()) == 0
    await ha.simulate(READING, "on", by=Source.PERSON)
    await ha.simulate(LIVING, "off")
    await _wait(hm, clock, 120)
    await ha.simulate(READING, "off", by=Source.AUTOMATION)
    assert hm.learning.total("light_off_leave", "living", Result.HIT, clock.now()) > 0.45
    assert _misses(hm, clock) == 0
    await hm.stop()
