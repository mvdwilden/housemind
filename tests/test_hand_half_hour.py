"""A change by hand holds half an hour at most (E-584), as the administrator of the real house
asked: "zodra ik handmatig de verlichting aanpas ... laat HouseMind dit voor een lange periode
met rust, maar ik wil dat je het maximaal een half uur met rust laat en daarna weer overneemt."
Before, a lamp someone switched by hand or in the app was left alone for up to four hours while
someone was there. From the user's side: the lamp itself, and whether HouseMind may act on it.
What each test checks is its docstring."""

from __future__ import annotations

from pathlib import Path

from housemind.core.model import Source
from tests.test_people_win_new_moment import (
    LAMP,
    LIVING,
    ON,
    _house,
    _request,
    _someone_puts_it_out,
)
from tests.test_rhythm import local, until


async def test_a_lamp_put_out_by_hand_is_housemind_s_again_after_half_an_hour(
    tmp_path: Path,
) -> None:
    """Someone in the room puts the light out by hand: for half an hour that stands, also with
    them still there; after it, HouseMind's own rules count again – the next time it would put
    the light on, it may. (It does not put it on by itself while they sit there: that happens
    when someone comes in.)"""
    hm, clock, ha = await _house(tmp_path)
    assert hm.gate is not None
    await ha.simulate(LIVING, "on")
    await hm.tick()
    await _someone_puts_it_out(ha)
    await until(clock, hm, local(19, 20, 29))
    assert ha.states[LAMP].state == "off"
    assert hm.gate.evaluate(_request(clock, ON[0])).reason == "people_win"
    await until(clock, hm, local(19, 20, 31))
    assert hm.gate.evaluate(_request(clock, ON[0])).reason != "people_win"
    await hm.stop()


async def test_a_lamp_put_on_in_the_app_is_left_half_an_hour(tmp_path: Path) -> None:
    """Put on from the app with nobody in the room: it stays on for half an hour; then going out
    when nobody is there counts again."""
    hm, clock, ha = await _house(tmp_path)
    await ha.simulate(LAMP, "on", {"brightness": 200}, by=Source.PERSON)
    await until(clock, hm, local(19, 20, 25))
    assert ha.states[LAMP].state == "on"
    await until(clock, hm, local(19, 20, 40))
    assert ha.states[LAMP].state == "off"
    await hm.stop()
