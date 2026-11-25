"""A small room with one door (E-504, E-505, design 76).

The administrator (12-11-2026), about a toilet with only a door sensor: shut after going in is
someone there; open and shut again is gone. "It must keep working for every Home Assistant, not
only mine": everything follows from the kind of room and its sensors, never a name.
"""

from __future__ import annotations

from pathlib import Path

from housemind.brain.house_knowledge import CONFIRMED, SUGGESTED
from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Entity, Source, State
from housemind.ha.fake import FakeHomeAssistant
from housemind.i18n.catalog import load_languages
from housemind.knowledge.area_roles import area_role, role_words
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind
from housemind.world.boxes import box_rooms
from tests.test_proof_one_yes import _row
from tests.test_rhythm import local, start, until

DOOR = "binary_sensor.toilet_door"
LAMP = "light.toilet"
PAIR = ("light_off_leave", "toilet")
KEY = "area:toilet:one_door"


async def _door(hm: HouseMind, ha: FakeHomeAssistant, entity_id: str = DOOR, **kw: str) -> None:
    entity = Entity(entity_id, f"reg-{entity_id}", "Toiletdeur", area_id="toilet", **kw)
    await ha.add_entity(entity, State(entity_id, "off", {}, hm.clock.now()))
    await hm._refresh_model()


async def _house(
    tmp_path: Path, yes: bool | None = True
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    hm, clock, ha = await start(local(19, 20, 0), tmp_path)
    await _door(hm, ha, device_class="door")
    if yes is not None:
        assert await hm.answer_knowledge(KEY, yes)
    assert hm.learning is not None
    hm.learning.steps[PAIR] = Step.AUTONOMOUS
    return hm, clock, ha


async def _move(ha: FakeHomeAssistant, clock: SimulatedClock, hm: HouseMind, state: str) -> None:
    await ha.simulate(DOOR, state)
    await clock.advance(3)
    await hm.tick()


async def test_a_toilet_with_only_its_door_is_asked_about_once(tmp_path: Path) -> None:
    hm, _clock, ha = await start(local(19, 20, 0), tmp_path)
    assert hm.area_roles["toilet"] == "toilet"
    assert hm.blind_here(*PAIR)  # no sensor yet: blind (E-462)
    await _door(hm, ha, device_class="door")
    found = hm.knowledge.get("area", "toilet", "one_door")
    assert found is not None and found.status == SUGGESTED and found.value["door"] == DOOR
    assert hm.still_open(found)
    # The question in plain words; until a Klopt it is still blind, and the card says how.
    text, params = hm._question(found)
    assert text == "notify.knowledge.one_door" and params["area"]
    assert hm.blind_here(*PAIR)
    row = await _row(hm, *PAIR)
    assert row["text"] == hm.languages.text("nl", "ui.ready.proof.no_sensor_box")
    # Klopt: the door counts, the rule is no longer blind.
    assert await hm.answer_knowledge(KEY, True)
    assert hm.knowledge.get("area", "toilet", "one_door").status == CONFIRMED  # type: ignore[union-attr]
    assert not hm.blind_here(*PAIR)
    assert hm.world is not None and hm.world.occupancy.boxes == {"toilet": DOOR}
    row = await _row(hm, *PAIR)
    assert row.get("byDoor") == hm.languages.text("nl", "ui.ready.proof.by_door")
    await hm.stop()


async def test_shut_after_going_in_keeps_the_light_on_and_open_and_shut_puts_it_out(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await _house(tmp_path)
    assert hm.world is not None
    await _move(ha, clock, hm, "on")  # someone opens the door …
    await ha.simulate(LAMP, "on", by=Source.PHYSICAL)
    await _move(ha, clock, hm, "off")  # … goes in and shuts it
    assert hm.world.occupancy.areas["toilet"].box == "in"
    await until(clock, hm, local(19, 20, 45))  # a long while, the door shut
    assert ha.states[LAMP].state == "on"
    snap = hm.world.snapshot(clock.now())
    assert snap.occupancy["toilet"].occupied and snap.occupancy["toilet"].sensed
    await _move(ha, clock, hm, "on")  # out …
    await _move(ha, clock, hm, "off")  # … and the door shut behind them
    assert hm.world.occupancy.areas["toilet"].box == "empty"
    await until(clock, hm, local(19, 21, 15))
    assert ha.states[LAMP].state == "off"
    await hm.stop()


async def test_an_open_door_is_not_sure_and_a_hand_in_a_shut_room_is_someone(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await _house(tmp_path)
    assert hm.world is not None
    await _move(ha, clock, hm, "on")
    await ha.simulate(LAMP, "on", by=Source.PHYSICAL)
    await _move(ha, clock, hm, "off")
    await _move(ha, clock, hm, "on")  # out, and the door stays open
    await until(clock, hm, local(19, 20, 40))
    assert ha.states[LAMP].state == "on"  # not sure: nothing
    assert not hm.world.snapshot(clock.now()).occupancy["toilet"].sensed
    await _move(ha, clock, hm, "off")  # shut now: out after all
    await until(clock, hm, local(19, 21, 10))
    assert ha.states[LAMP].state == "off"
    # Empty and shut, and someone puts the lamp on by hand: someone is in.
    await ha.simulate(LAMP, "on", by=Source.PHYSICAL)
    await clock.advance(3)
    await hm.tick()
    assert hm.world.occupancy.areas["toilet"].box == "in"
    await until(clock, hm, local(19, 21, 50))
    assert ha.states[LAMP].state == "on"
    await hm.stop()


async def test_klopt_niet_or_a_door_offline_is_as_without_a_sensor(tmp_path: Path) -> None:
    hm, clock, ha = await _house(tmp_path, yes=False)
    assert hm.world is not None and hm.world.occupancy.boxes == {}
    assert hm.blind_here(*PAIR)
    await hm.stop()
    hm, clock, ha = await _house(tmp_path / "b")
    await ha.simulate(DOOR, "unavailable")
    await clock.advance(3)
    await hm.tick()
    assert hm.blind_here(*PAIR)
    await hm.stop()


async def test_only_where_it_can_be_true(tmp_path: Path) -> None:
    """A second door, a motion sensor, a window or another kind of room: no question."""
    hm, _clock, ha = await start(local(19, 20, 0), tmp_path)
    assert hm.world is not None
    occ = hm.world.occupancy
    await _door(hm, ha, device_class="window")
    assert box_rooms(hm.model, occ, hm.openings, hm.area_roles) == {}
    await _door(hm, ha, DOOR, device_class="door")
    assert box_rooms(hm.model, occ, hm.openings, hm.area_roles) == {"toilet": DOOR}
    await _door(hm, ha, "binary_sensor.toilet_door_2", device_class="door")
    assert box_rooms(hm.model, occ, hm.openings, hm.area_roles) == {}
    found = hm.knowledge.get("area", "toilet", "one_door")
    assert found is not None and not hm.still_open(found)  # the question has gone
    # A living room with one door is no toilet or bathroom: not asked about – though an
    # administrator's Klopt would count there too (no kind needed once confirmed).
    living = "binary_sensor.living_door"
    entity = Entity(living, f"reg-{living}", "Deur", area_id="living", device_class="door")
    await ha.add_entity(entity, State(living, "off", {}, hm.clock.now()))
    await hm._refresh_model()
    assert "living" not in box_rooms(hm.model, occ, hm.openings, hm.area_roles)
    await hm.stop()


async def test_after_a_restart_shut_and_lit_is_someone(tmp_path: Path) -> None:
    hm, clock, ha = await _house(tmp_path)
    assert hm.world is not None
    await ha.simulate(LAMP, "on", by=Source.PHYSICAL)
    await clock.advance(3)
    hm.world.occupancy.set_boxes({}, hm.world.states, clock.now())  # as if new
    hm._sort_boxes()
    assert hm.world.occupancy.areas["toilet"].box == "in"
    await until(clock, hm, local(19, 20, 30))
    assert ha.states[LAMP].state == "on"
    await hm.stop()


def test_rooms_any_language() -> None:
    """The kind of room comes from words in every language file, not one house (E-105)."""
    root = Path(__file__).resolve().parents[1] / "knowledge" / "languages"
    words = role_words(load_languages([root]))
    for name in ("WC", "Toilet", "Gäste-WC", "Toilettes", "Badezimmer", "Salle de bain"):
        assert area_role(Area("x", name), words) in ("toilet", "bathroom"), name


async def test_an_ai_gets_only_the_kind_of_room_and_the_door_never_a_name(tmp_path: Path) -> None:
    """With AI, it may judge the question first – from neutral facts only (50.8)."""
    import json

    hm, _clock, ha = await start(local(19, 20, 0), tmp_path)
    await _door(hm, ha, device_class="door")
    found = hm.knowledge.get("area", "toilet", "one_door")
    assert found is not None
    facts = hm._doubt_facts(found)
    assert facts["question"] == "one_door_room" and facts["room"] == "toilet"
    sent = json.dumps(facts)
    assert "Toiletdeur" not in sent and DOOR not in sent
    await hm.stop()
