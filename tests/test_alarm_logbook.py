"""The alarm in the Logboek (E-533) and the house back after it is switched off (E-534), from
the side of the people at home. Found in the real house on 20 November: the alarm was switched
on at 19:52, went off at 19:53 and was switched off 30 seconds later. The Logboek showed no
switching on or off, and under the emergency message 117 lines "Ik heb … aangedaan, omdat het
past bij wat ik in huis zag"; afterwards every lamp stayed on. Each test fails without the
change; what went wrong is in its docstring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from housemind.brain import security
from housemind.core.model import Source
from housemind.server.logbook import decision_from_row, logbook
from tests.test_cameras import EVENING, start
from tests.test_screens import viewer

ALARM = "alarm_control_panel.home"


async def _lines(hm: Any) -> list[dict[str, Any]]:
    return (await logbook(hm, await viewer(hm, 3), "demo-user-1", "nl"))["items"]


async def _seconds(hm: Any, clock: Any, seconds: int) -> None:
    for _ in range(seconds // 10):
        await clock.advance(10)
        await hm.tick()


async def test_the_alarm_is_a_few_lines_in_the_logboek(tmp_path: Path) -> None:
    """Switched on and off were not in the Logboek; going off was a hundred lines, each with
    the reason "it fits what I saw"."""
    hm, clock, ha = await start(tmp_path, at=EVENING)
    await ha.simulate(ALARM, "arming")
    await ha.simulate(ALARM, "armed_away")
    await _seconds(hm, clock, 30)
    await ha.simulate(ALARM, "pending")
    await ha.simulate(ALARM, "triggered")
    assert ha.states["light.kitchen"].state == "on"
    await _seconds(hm, clock, 30)
    await ha.simulate(ALARM, "disarmed")
    lines = await _lines(hm)
    alarm = [i["text"] for i in lines if i["type"] == "alarm"]
    assert alarm[2] == "Het alarm (Gang alarmsysteem) ging aan: afwezig.", alarm
    assert (
        alarm[1].startswith("Het alarm (Gang alarmsysteem) ging af. Ik deed ")
        and "lampen aan" in alarm[1]
    )
    assert "noodmelding" in alarm[1]
    assert alarm[0] == "Het alarm (Gang alarmsysteem) is uitgezet, 30 seconden nadat het afging."
    assert len(alarm) == 3  # not arming or pending
    done = [i for i in lines if i["type"] == "done"]
    assert not any("past bij wat ik in huis zag" in i["text"] for i in done)
    assert not any(i.get("did", "").endswith("aangedaan") and "Keuken" in i["text"] for i in done)
    # Why, after a restart too: the decision keeps its reason.
    row = await hm.db.fetchone(
        "SELECT * FROM decisions WHERE entity_id = 'light.kitchen' ORDER BY id DESC LIMIT 1"
    )
    assert hm.explainer is not None
    assert hm.explainer.reason_phrase("nl", decision_from_row(row)) == "het alarm afging"
    await hm.stop()


async def test_after_it_is_switched_off_the_house_goes_back(tmp_path: Path) -> None:
    """After the alarm was switched off, every lamp it put on stayed on, and the curtains it
    opened stayed open. A minute later they now go back to how they were – but not what someone
    changed in that minute, and not a lamp in a room where someone is now."""
    hm, clock, ha = await start(tmp_path, at=EVENING)
    await ha.simulate("light.kitchen", "on", {"brightness": 77}, by=Source.PERSON)  # 30%
    await ha.simulate("cover.living_curtain", "closed")
    assert await security.set_setting(hm, "alarm_open_covers", ["cover.living_curtain"])
    await ha.simulate(ALARM, "armed_away")
    await _seconds(hm, clock, 20 * 60)  # the house quiet: the kitchen lamp is nobody's now
    await ha.simulate(ALARM, "triggered")
    for lamp in ("light.kitchen", "light.hall", "light.living_ceiling"):
        assert ha.states[lamp].state == "on"
    assert ha.states["cover.living_curtain"].state == "open"
    await _seconds(hm, clock, 30)
    await ha.simulate(ALARM, "disarmed")
    # Someone looks around: in the living room, and puts the hall light on again by hand.
    await ha.simulate("binary_sensor.living_presence", "on")
    await ha.simulate("light.hall", "off", by=Source.PERSON)
    await ha.simulate("light.hall", "on", by=Source.PERSON)
    await _seconds(hm, clock, 30)
    assert ha.states["light.living_ceiling"].state == "on"  # not yet: a minute
    await _seconds(hm, clock, 40)
    assert ha.states["light.kitchen"].state == "on"
    assert abs(ha.states["light.kitchen"].attributes["brightness"] - 77) <= 1  # as it was
    assert ha.states["light.hall"].state == "on"  # someone's choice in that minute
    assert ha.states["light.living_ceiling"].state == "on"  # someone is there
    assert ha.states["cover.living_curtain"].state == "closed"
    off = [
        e
        for e, s in ha.states.items()
        if e.startswith("light.") and e not in hm.model.groups and s.state == "off"
    ]
    assert off  # the rest went back off
    lines = [i["text"] for i in await _lines(hm) if i["type"] == "alarm"]
    assert lines[0].startswith("Het alarm is uit: ik heb ") and "en de gordijnen" in lines[0]
    assert lines[0].endswith("teruggezet zoals het was."), lines[0]
    await hm.stop()


async def test_switched_off_without_going_off_changes_nothing(tmp_path: Path) -> None:
    """Switched on and off again as usual: one line each, and nothing is put back."""
    hm, clock, ha = await start(tmp_path, at=EVENING)
    await ha.simulate("light.kitchen", "on", by=Source.PERSON)
    await ha.simulate(ALARM, "armed_night")
    await _seconds(hm, clock, 60)
    await ha.simulate(ALARM, "disarmed")
    await _seconds(hm, clock, 120)
    assert ha.states["light.kitchen"].state == "on"
    lines = [i["text"] for i in await _lines(hm) if i["type"] == "alarm"]
    assert lines == [
        "Het alarm (Gang alarmsysteem) ging uit.",
        "Het alarm (Gang alarmsysteem) ging aan: nacht.",
    ]
    await hm.stop()


async def test_vesta_shows_the_alarm(tmp_path: Path) -> None:
    """Vesta had no colour for a house that is secured, and after an alarm that went off and
    was switched off she was calm at once, as if nothing had happened (E-539)."""
    hm, clock, ha = await start(tmp_path, at=EVENING)
    assert await hm.set_house_mode("home")  # people still up
    assert hm.orb_state(thinking=False) == "calm"
    await ha.simulate(ALARM, "armed_home")
    assert hm.orb_state(thinking=False) == "secured"
    assert await hm.set_house_mode("sleep")
    assert hm.vesta_state(False) == ("sleeping", "secured")  # asleep she stays asleep
    assert await hm.set_house_mode("home")
    await ha.simulate(ALARM, "triggered")
    assert hm.orb_state(thinking=False) == "emergency"
    await _seconds(hm, clock, 30)
    await ha.simulate(ALARM, "disarmed")
    state, reason = hm.vesta_state(False)
    assert state == "attention" and reason == f"alarm_was:{ALARM}"
    note = next(n for n in await hm.notifications() if n["topic"] == reason)
    said = hm.languages.text("nl", note["text_key"], **json.loads(note["params"]))
    assert "ging af om" in said and "30 seconden later uitgezet" in said, said
    await _seconds(hm, clock, 30 * 60)
    assert hm.vesta_state(False)[0] == "attention"  # until someone saw it
    await hm.close_notice(reason)
    # A resident (the Check-up's word about backups is only for administrators).
    assert hm.vesta_state(False, admin=False)[0] == "calm"
    await hm.stop()
