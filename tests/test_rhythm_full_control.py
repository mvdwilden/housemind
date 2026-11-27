"""HouseMind has the lamps to itself (E-579), as the administrator of the real house asked: "Ik
wil echt zorgen dat HouseMind volledige controle heeft en niet dat allerlei anderen ook invloed
hebben op de lampen." Two ways the integration for the day's rhythm got in between: it kept a
room's lamps while only another light rule there was on Autonomous, and it kept changing a lamp
while its standen were being set. From the user's side: the switch of that integration, the
lamp, the Logboek and the Standen window. What each test checks is its docstring."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from housemind.core.model import Source
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from tests.test_day_rhythm import (
    GROUP,
    LAMP,
    RHYTHM,
    ROOT,
    SENSOR,
    SWITCH,
    ZONE,
    _house,
    _lamp,
    _rows,
)


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


# -- on Autonomous the room's light is HouseMind's, also its colour -----------------------------


async def test_a_room_whose_going_out_is_on_autonomous_gets_its_colour_from_housemind(
    tmp_path: Path,
) -> None:
    """Only 'light off when everyone left' is on Autonomous, 'light on when you come in' is still
    on Propose: the lamps of the room are HouseMind's all the same. The integration's switch goes
    off, without a question, and HouseMind does the group's day itself."""
    hm, _clock, ha = await _house(tmp_path)
    await hm.set_admin_step("light_on_entry@living", "propose", "demo-user-1")
    await hm.set_admin_step("light_off_leave@living", "autonomous", "demo-user-1")
    await hm.tick()
    assert ha.states[SWITCH].state == "off"
    assert hm.daylight.ours(RHYTHM)
    await hm.stop()


async def test_a_lamp_that_burns_then_follows_the_evening_without_a_question(
    tmp_path: Path,
) -> None:
    """With only going out on Autonomous, a lamp someone put on in the afternoon gets softer and
    warmer by itself towards the evening, as the group said – and nobody is asked anything for
    it, and the Logboek does not fill up."""
    hm, clock, ha = await _house(tmp_path, start=datetime(2026, 11, 7, 15, 30, tzinfo=ZONE))
    await hm.set_admin_step("light_on_entry@living", "propose", "demo-user-1")
    await hm.set_admin_step("light_off_leave@living", "autonomous", "demo-user-1")
    await hm.tick()
    await ha.simulate(SENSOR, "on")
    await ha.simulate(GROUP, "on", by=Source.PHYSICAL)  # a plain "on" from the wall
    await clock.advance(40)
    rows = await _rows(hm, GROUP)
    for _ in range(int(5 * 3600 / 300)):
        await clock.advance(300)
        await ha.simulate(SENSOR, "on")  # someone is there all evening
        await hm.tick()
    state, pct, warm = _lamp(ha, GROUP)
    assert state == "on" and pct == 5 and warm == 2500
    assert hm.learning is not None
    assert not [p for p in hm.learning.pending if p.area_id == "living"]
    assert await _rows(hm, GROUP) == rows
    await hm.stop()


async def test_without_a_light_rule_on_autonomous_the_group_keeps_its_lamps(
    tmp_path: Path,
) -> None:
    """Everything in the room on Propose: the integration keeps doing its work, HouseMind keeps
    its hands off."""
    hm, _clock, ha = await _house(tmp_path)
    await hm.set_admin_step("light_on_entry@living", "propose", "demo-user-1")
    await hm.set_admin_step("light_off_leave@living", "propose", "demo-user-1")
    await hm.tick()
    assert ha.states[SWITCH].state == "on"
    assert not hm.daylight.ours(RHYTHM)
    await hm.stop()


# -- while the standen are set, nothing else steers the lamp ------------------------------------


async def test_while_its_standen_are_set_the_group_rests_and_comes_back_after_klaar(
    tmp_path: Path,
) -> None:
    """The Standen window of a lamp the integration still steers: its switch goes off as long as
    the window is open, so only what the administrator tries is on the lamp. Klaar: it goes on
    again. Quiet, as the Proef: no line in the Logboek."""
    hm, _clock, ha = await _house(tmp_path)
    await hm.set_admin_step("light_on_entry@living", "propose", "demo-user-1")
    await hm.tick()
    assert ha.states[SWITCH].state == "on"
    rows = await _rows(hm)
    hm.levels_hold(LAMP)
    await _settle()
    assert ha.states[SWITCH].state == "off"
    hm.levels_hold(LAMP)  # the window says it is still open
    await _settle()
    await hm.tick()
    assert ha.states[SWITCH].state == "off"
    await hm.levels_release(LAMP)
    assert ha.states[SWITCH].state == "on"
    assert not hm.daylight.ours(RHYTHM)
    assert await _rows(hm) == rows
    await hm.stop()


async def test_a_window_that_went_away_lets_the_group_go_on_again(tmp_path: Path) -> None:
    """The phone with the window went away without Klaar: a minute after its last word the hold
    ends, and the group steers again."""
    hm, clock, ha = await _house(tmp_path)
    await hm.set_admin_step("light_on_entry@living", "propose", "demo-user-1")
    await hm.tick()
    hm.levels_hold(LAMP)
    await _settle()
    assert ha.states[SWITCH].state == "off"
    await clock.advance(timedelta(seconds=90).total_seconds())
    await hm.tick()
    assert ha.states[SWITCH].state == "on"
    await hm.stop()


async def test_someone_putting_it_on_while_it_rests_keeps_it_on(tmp_path: Path) -> None:
    """Put on again in Home Assistant while the window is open: that is theirs, HouseMind does
    not switch it off again for this window."""
    hm, _clock, ha = await _house(tmp_path)
    await hm.set_admin_step("light_on_entry@living", "propose", "demo-user-1")
    await hm.tick()
    hm.levels_hold(LAMP)
    await _settle()
    await ha.simulate(SWITCH, "on", by=Source.PERSON)
    hm.levels_hold(LAMP)
    await _settle()
    await hm.levels_release(LAMP)
    assert ha.states[SWITCH].state == "on"
    assert not hm.daylight.paused
    await hm.stop()


async def test_on_autonomous_by_the_time_klaar_it_stays_housemind_s(tmp_path: Path) -> None:
    """Set on Autonomous while the window was open: at Klaar the switch stays off, because
    HouseMind does the work from now on."""
    hm, _clock, ha = await _house(tmp_path)
    await hm.set_admin_step("light_on_entry@living", "propose", "demo-user-1")
    await hm.tick()
    hm.levels_hold(LAMP)
    await _settle()
    await hm.set_admin_step("light_on_entry@living", "autonomous", "demo-user-1")
    await hm.tick()
    await hm.levels_release(LAMP)
    assert ha.states[SWITCH].state == "off"
    assert hm.daylight.ours(RHYTHM)
    await hm.stop()


async def test_a_restart_while_it_rests_puts_it_on_again(tmp_path: Path) -> None:
    """HouseMind stopped while the window was open: after the start no window holds the lamp,
    so the group goes on again."""
    hm, clock, ha = await _house(tmp_path)
    await hm.set_admin_step("light_on_entry@living", "propose", "demo-user-1")
    await hm.tick()
    hm.levels_hold(LAMP)
    await _settle()
    assert ha.states[SWITCH].state == "off"
    await hm.stop()
    again = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert ha.states[SWITCH].state == "on"
    assert not again.daylight.paused
    await again.stop()


async def test_the_window_says_who_gives_the_lamp_its_day(tmp_path: Path) -> None:
    """The Standen window: on Propose the group's name and that it rests while the window is
    open; on Autonomous that HouseMind does that group's day itself."""
    hm, _clock, _ha = await _house(tmp_path)
    await hm.set_admin_step("light_on_entry@living", "propose", "demo-user-1")
    await hm.tick()
    view = views._levels_view(hm, "nl", LAMP)
    assert view["rhythm"] == {"by": "ha", "name": RHYTHM.title, "ours": False}
    await hm.set_admin_step("light_on_entry@living", "autonomous", "demo-user-1")
    await hm.tick()
    assert views._levels_view(hm, "nl", LAMP)["rhythm"]["ours"] is True
    await hm.stop()
