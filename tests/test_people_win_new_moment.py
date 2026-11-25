"""People go first, for as long as they are there (E-462).

The administrator's yes (06-11-2026) to: as long as someone is in the room, your choice stands.
Has the room been empty and someone comes in, that is a new moment. Otherwise, once HouseMind does
the light itself, one press on a switch means no light for four hours on whoever walks in later.

And what the first night on Autonomous showed: without a sensor that can see someone, a room is
never "empty" – the lamp went out on someone sitting there.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from housemind.core.clock import SimulatedClock
from housemind.core.model import Source
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind
from housemind.safety.gate import ActionRequest, Layer
from tests.test_proof_one_yes import _about, _people, _prove, _review, _row
from tests.test_rhythm import local, start, until

LIVING = "binary_sensor.living_presence"
LAMP = "light.living_ceiling"
ON = ("light_on_entry", "living")
OFF = ("light_off_leave", "living")


def _request(clock: SimulatedClock, skill: str, action: str = "light.turn_on") -> ActionRequest:
    return ActionRequest(
        Layer.REFLEX, LAMP, action, {}, skill=skill, area_id="living", created_at=clock.now()
    )


async def _house(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    hm, clock, ha = await start(local(19, 20, 0), tmp_path)
    assert hm.learning is not None
    hm.learning.steps[ON] = Step.AUTONOMOUS
    hm.learning.steps[OFF] = Step.AUTONOMOUS
    return hm, clock, ha


async def _someone_puts_it_out(ha: FakeHomeAssistant, by: Source = Source.PHYSICAL) -> None:
    await ha.simulate(LAMP, "on", {"brightness": 200}, by=by)
    await ha.simulate(LAMP, "off", by=by)


async def test_the_choice_stands_while_someone_is_there(tmp_path: Path) -> None:
    hm, clock, ha = await _house(tmp_path)
    assert hm.gate is not None
    await ha.simulate(LIVING, "on")
    await hm.tick()
    await _someone_puts_it_out(ha)
    await until(clock, hm, local(19, 21, 0))
    assert ha.states[LAMP].state == "off"
    assert LAMP not in hm.gate.state.come_in
    assert hm.gate.evaluate(_request(clock, ON[0])).reason == "people_win"
    await hm.stop()


async def test_coming_back_after_the_room_was_empty_is_a_new_moment(tmp_path: Path) -> None:
    """Puts the light out by hand, walks out, comes back half an hour later: light, not four
    hours of dark. The choice was for when they were there."""
    hm, clock, ha = await _house(tmp_path)
    assert hm.gate is not None
    await ha.simulate(LIVING, "on")
    await hm.tick()
    await _someone_puts_it_out(ha)
    await ha.simulate(LIVING, "off")
    await until(clock, hm, local(19, 20, 30))
    assert LAMP in hm.gate.state.come_in
    assert LAMP in hm.gate.state.people_win_until  # the four hours run on
    assert ha.states[LAMP].state == "off"  # nobody there: nothing happens yet
    await ha.simulate(LIVING, "on")
    await until(clock, hm, local(19, 20, 31))
    assert ha.states[LAMP].state == "on"
    await hm.stop()


async def test_walking_straight_back_in_is_still_the_same_moment(tmp_path: Path) -> None:
    """Put it out and back within two minutes: someone chose the dark just now."""
    hm, clock, ha = await _house(tmp_path)
    await ha.simulate(LIVING, "on")
    await hm.tick()
    await _someone_puts_it_out(ha)
    await ha.simulate(LIVING, "off")
    await clock.advance(30)
    await hm.tick()
    await ha.simulate(LIVING, "on")
    await until(clock, hm, local(19, 20, 2))
    assert ha.states[LAMP].state == "off"
    await hm.stop()


async def test_a_button_that_puts_all_out_leaves_no_dark_rooms_behind(tmp_path: Path) -> None:
    """A button or the phone, while nobody is in this room: nobody stays with that choice, so
    whoever comes in later gets light."""
    hm, clock, ha = await _house(tmp_path)
    assert hm.gate is not None
    await _someone_puts_it_out(ha, Source.PERSON)
    assert LAMP in hm.gate.state.people_win_until
    assert LAMP in hm.gate.state.come_in
    assert LAMP not in hm.gate.state.released
    await until(clock, hm, local(19, 21, 0))
    await ha.simulate(LIVING, "on")
    await until(clock, hm, local(19, 21, 1))
    assert ha.states[LAMP].state == "on"
    await hm.stop()


async def test_a_lamp_put_on_from_afar_stays_on(tmp_path: Path) -> None:
    """Someone put it on for a reason, also with nobody there: that is not undone, until someone
    has been there and left."""
    hm, clock, ha = await _house(tmp_path)
    assert hm.gate is not None
    await ha.simulate(LAMP, "on", {"brightness": 200}, by=Source.PERSON)
    await until(clock, hm, local(19, 21, 0))
    assert ha.states[LAMP].state == "on"
    assert hm.gate.evaluate(_request(clock, OFF[0], "light.turn_off")).reason == "people_win"
    await hm.stop()


async def test_a_new_hand_choice_starts_over(tmp_path: Path) -> None:
    hm, clock, ha = await _house(tmp_path)
    assert hm.gate is not None
    await ha.simulate(LIVING, "on")
    await hm.tick()
    await _someone_puts_it_out(ha)
    await ha.simulate(LIVING, "off")
    await until(clock, hm, local(19, 20, 30))
    assert LAMP in hm.gate.state.come_in
    await ha.simulate(LIVING, "on")
    await until(clock, hm, local(19, 20, 31))
    await ha.simulate(LAMP, "off", by=Source.PHYSICAL)  # no thanks, with someone there
    assert LAMP not in hm.gate.state.come_in
    assert LAMP not in hm.gate.state.released
    hm._last_request.clear()
    assert hm.gate.evaluate(_request(clock, ON[0])).reason == "people_win"
    await hm.stop()


async def test_with_only_a_motion_sensor_it_counts_when_the_room_is_empty(tmp_path: Path) -> None:
    """A switch says someone is there, until that fades: then they left."""
    hm, clock, ha = await start(local(19, 19, 0), tmp_path)
    assert hm.learning is not None and hm.gate is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    await ha.simulate("light.kitchen", "on", {"brightness": 200})
    await ha.simulate("light.kitchen", "off")
    assert "light.kitchen" not in hm.gate.state.come_in
    await ha.simulate("binary_sensor.kitchen_motion", "off")
    await until(clock, hm, local(19, 20, 30))
    assert "light.kitchen" in hm.gate.state.come_in
    await hm.stop()


async def test_only_coming_in_gets_through(tmp_path: Path) -> None:
    """Dimming, a scene or waking up are not someone coming in: those wait for the choice."""
    hm, clock, _ha = await _house(tmp_path)
    assert hm.gate is not None
    hm.learning.steps[("night_light", "living")] = Step.AUTONOMOUS  # type: ignore[union-attr]
    hm.gate.state.people_win_until[LAMP] = clock.now() + timedelta(hours=4)
    hm.gate.state.come_in.add(LAMP)
    assert hm.gate.evaluate(_request(clock, ON[0])).reason != "people_win"
    assert hm.gate.evaluate(_request(clock, "night_light")).reason != "people_win"
    assert hm.gate.evaluate(_request(clock, "wake_light")).reason == "people_win"
    leaving = _request(clock, OFF[0], "light.turn_off")
    assert hm.gate.evaluate(leaving).reason == "people_win"  # that is ``released``
    await hm.stop()


async def test_a_group_waits_for_every_lamp_in_it(tmp_path: Path) -> None:
    from tests.test_group_first import TABLE, _dining
    from tests.test_group_first import _house as group_house

    hm, _ha, clock = await group_house(tmp_path, _dining)
    assert hm.gate is not None
    for lamp in TABLE:
        hm.gate.state.people_win_until[lamp] = clock.now() + timedelta(hours=4)
    request = ActionRequest(
        Layer.REFLEX,
        "light.table",
        "light.turn_on",
        {},
        skill="light_on_entry",
        area_id="kitchen",
        created_at=clock.now(),
    )
    hm.gate.state.come_in.add(TABLE[0])
    assert hm.gate.evaluate(request).reason == "people_win"
    hm.gate.state.come_in.update(TABLE)
    assert hm.gate.evaluate(request).reason != "people_win"
    await hm.stop()


# -- no sensor, no "empty" --------------------------------------------------------------------


async def test_without_a_sensor_the_lamp_does_not_go_out_on_someone(tmp_path: Path) -> None:
    """The real thing: a room with only a door sensor. Someone went in and the door closed;
    four minutes later the lamp went out on them."""
    hm, clock, ha = await start(local(19, 20, 0), tmp_path)
    assert hm.learning is not None
    assert not hm.world.occupancy.sensed("toilet")  # type: ignore[union-attr]
    pair = ("light_off_leave", "toilet")
    hm.learning.steps[pair] = Step.AUTONOMOUS
    await ha.simulate("light.toilet", "on", {"brightness": 200})
    await until(clock, hm, local(19, 22, 0))
    assert ha.states["light.toilet"].state == "on"
    await hm.stop()


async def test_without_a_sensor_no_question_and_the_card_says_why(tmp_path: Path) -> None:
    hm, _clock, _ha = await start(local(19, 20, 0), tmp_path)
    assert hm.learning is not None
    await _people_already(hm)
    pair = ("light_off_leave", "toilet")
    _prove(hm, pair)
    await _review(hm)
    assert await _about(hm, f"ready:{pair[0]}@{pair[1]}") is None
    row = await _row(hm, *pair)
    assert row["step"] == "observe"
    assert row.get("watching") is True
    assert not row.get("proven")
    assert row["text"] == hm.languages.text("nl", "ui.ready.proof.no_sensor")
    # Coming in is another rule: that one sees someone come, so it may ask.
    assert not hm.blind_here("light_on_entry", "toilet")
    assert not hm.blind_here(*OFF)
    await hm.stop()


async def test_without_a_sensor_your_own_step_stays_and_the_card_says_so(tmp_path: Path) -> None:
    """The administrator set Autonomous where no sensor can see someone (12-11-2026): the choice
    stays saved, HouseMind only watches, and the card says both and what it waits for."""
    hm, clock, ha = await start(local(19, 20, 0), tmp_path)
    assert hm.learning is not None
    await _people_already(hm)
    pair = ("light_off_leave", "toilet")
    assert await hm.set_admin_step(f"{pair[0]}@{pair[1]}", "autonomous", "demo-user-1")
    row = await _row(hm, *pair)
    assert row.get("watching") is True
    assert row["own"]["step"] == "autonomous"
    step = hm.languages.text("nl", "steps.autonomous")
    assert row["text"] == hm.languages.text("nl", "ui.ready.proof.no_sensor_own", step=step)
    assert "sensor" in row["text"] and step in row["text"]
    # And it still does not put the light out on someone sitting there.
    await ha.simulate("light.toilet", "on", {"brightness": 200})
    await until(clock, hm, local(19, 20, 40))
    assert ha.states["light.toilet"].state == "on"
    await hm.stop()


async def _people_already(hm: HouseMind) -> Any:
    """The demo people from the start of the test house are there; a level 4 one to ask."""
    await hm.db.execute("DELETE FROM people WHERE user_id = 'demo-user-1'")
    await _people(hm)
