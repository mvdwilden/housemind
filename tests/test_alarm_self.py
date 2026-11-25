"""The alarm on by itself (E-540) and the alarm in Test het huis (E-541), from the side of the
people at home. The administrator's wish (23-11-2026): "bouw alarm zelf aanzetten op Autonoom
… generiek voor alle HA-integraties" and "kan je het ook zo maken dat het alarm kunnen testen
in Test het huis?". Each test fails without the change; what it checks is its docstring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from housemind.brain import security
from housemind.core.model import Source, State
from housemind.server import views
from housemind.server.logbook import decision_from_row
from housemind.world.house_mode import HouseMode
from tests.test_cameras import EVENING, everyone_away, start

ALARM = "alarm_control_panel.home"
PERSONS = tuple(f"person.resident_{i}" for i in (1, 2, 3, 4))


def _alarm_attrs(hm: Any, ha: Any, **attrs: Any) -> None:
    state = hm.world.states[ALARM]
    new = State(ALARM, state.state, {**state.attributes, **attrs}, state.last_changed)
    hm.world.states[ALARM] = new
    ha.states[ALARM] = new


def _pushes(ha: Any, tag: str) -> list[Any]:
    return [n for n in ha.notifications if str(n[3].get("tag", "")).startswith(tag)]


async def _ticks(hm: Any, clock: Any, minutes: int) -> None:
    for _ in range(minutes):
        await clock.advance(60)
        await hm.tick()


async def _home_again(hm: Any, clock: Any, ha: Any) -> None:
    """Someone comes home and switches the alarm off – by hand, as always."""
    await ha.simulate(ALARM, "disarmed", by=Source.PERSON)
    for person in PERSONS:
        await ha.simulate(person, "home")
    await _ticks(hm, clock, 30)
    assert hm.world.modes.mode is not HouseMode.AWAY


async def _topics(hm: Any, prefix: str) -> dict[int, str]:
    rows = await hm.db.fetchall(
        "SELECT id, topic FROM notifications WHERE topic LIKE ?", (prefix + "%",)
    )
    return {int(r["id"]): str(r["topic"]) for r in rows}


async def _offer(hm: Any, user: str = "demo-user-1", admin: bool = True) -> dict[str, Any] | None:
    ids = await _topics(hm, "alarm_self_offer:away")
    items = await views.notifications(hm, "nl", admin, user)
    return next((i for i in items if i["id"] in ids), None)


async def _until_armed(hm: Any, clock: Any, ha: Any, minutes: int = 15) -> None:
    for _ in range(minutes):
        if ha.states[ALARM].state.startswith("armed"):
            return
        await clock.advance(60)
        await hm.tick()


async def test_five_yes_one_question_then_it_switches_on_by_itself(tmp_path: Path) -> None:
    """Five times everyone left and five times the answer was "Ja, graag": HouseMind asks the
    administrator once whether it may do that itself from now on. After "Ja, voortaan zelf"
    the next time everyone leaves it switches the alarm on itself – no question – and says so
    at once; the Logboek says why. Switching it off stays a person's."""
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1})
    for time in range(5):
        assert await _offer(hm) is None, time  # not before the fifth yes
        await everyone_away(hm, clock, ha)
        asked = _pushes(ha, "alarm_arm:away")
        assert len(asked) == time + 1
        await ha.press_notification_action(asked[-1][3]["actions"][0]["action"])
        assert ha.states[ALARM].state == "armed_away"
        await _home_again(hm, clock, ha)
    offer = await _offer(hm)
    assert offer is not None and offer["actions"] == ["yes", "no"]
    assert offer["labels"]["yes"] == "Ja, voortaan zelf"
    assert "De laatste 5 keer zei je ja" in offer["text"]
    assert await _offer(hm, "demo-user-2", admin=False) is None  # an administrator's question
    assert await security.answer(hm, "alarm_self_offer:away", "yes", "demo-user-1")
    assert security.settings_view(hm, "nl")["selfArm"] == {"away": True, "night": False}
    await everyone_away(hm, clock, ha)
    await _until_armed(hm, clock, ha)  # no sensor saw anyone for ten minutes
    assert ha.states[ALARM].state == "armed_away"
    assert len(_pushes(ha, "alarm_arm:away")) == 5  # no sixth question
    told = _pushes(ha, "alarm_self:away")
    assert told and "Ik heb het alarm (Gang alarmsysteem) op afwezig gezet." in told[0][2]
    row = await hm.db.fetchone(
        f"SELECT * FROM decisions WHERE entity_id = '{ALARM}' ORDER BY id DESC LIMIT 1"
    )
    assert hm.explainer is not None
    why = hm.explainer.reason_phrase("nl", decision_from_row(row))
    assert why == "iedereen weg is, en jullie vroegen het alarm dan zelf aan te zetten"
    await hm.stop()


async def test_leaving_through_the_hall_it_goes_on_by_itself(tmp_path: Path) -> None:
    """With the standing yes, everyone leaves the usual way: through the hall, the phones out
    of the zone a minute later. The hall still sees someone for a while – Gasten for a moment
    – and once the house is empty and quiet HouseMind switches the alarm on itself, without a
    question. In a house where someone without a phone lives, it asks instead."""
    for phoneless in (False, True):
        hm, clock, ha = await start(tmp_path / str(phoneless), {1: 2, 2: 1})
        assert await security.set_setting(hm, "alarm_self", {"away": True})
        if phoneless:
            assert await hm.set_house_setting("phoneless_residents", True)
        await ha.simulate("binary_sensor.hall_motion", "on")
        await ha.simulate("binary_sensor.hall_motion", "off")
        for person in PERSONS:
            await ha.simulate(person, "not_home")
        await _ticks(hm, clock, 25)
        if phoneless:
            assert ha.states[ALARM].state == "disarmed"
        else:
            assert hm.world.modes.mode is HouseMode.AWAY
            assert ha.states[ALARM].state == "armed_away"
            assert not _pushes(ha, "alarm_arm:away") and _pushes(ha, "alarm_self:away")
        await hm.stop()


async def test_an_alarm_that_wants_a_code_stays_a_question(tmp_path: Path) -> None:
    """HouseMind keeps no codes: an alarm that wants one to switch on cannot be switched on by
    itself. House settings says so, and leaving gives the question that opens it in HA."""
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1})
    _alarm_attrs(hm, ha, code_arm_required=True, code_format="number")
    view = security.settings_view(hm, "nl")
    assert view["selfCan"] == {"away": False, "night": False}
    assert await security.set_setting(hm, "alarm_self", {"away": True})
    await everyone_away(hm, clock, ha)
    await _ticks(hm, clock, 12)
    assert ha.states[ALARM].state == "disarmed"
    asked = _pushes(ha, "alarm_arm:away")
    assert asked and asked[0][3]["url"] == f"entityId:{ALARM}"
    await hm.stop()


async def test_at_night_an_open_window_keeps_the_question(tmp_path: Path) -> None:
    """Night on by itself, but the house goes to sleep with a window open: HouseMind asks, so
    someone can close it first. Without anything open it switches it on itself."""
    hm, clock, ha = await start(tmp_path, at=EVENING)
    assert await security.set_setting(hm, "alarm_self", {"night": True})
    opening = next(iter(sorted(hm.openings)))
    await ha.simulate(opening, "on")
    await hm.tick()
    hm.world.modes.set_manual(HouseMode.SLEEP, clock.now())
    await hm.tick()
    assert ha.states[ALARM].state == "disarmed"
    assert [a[3]["tag"] for a in _pushes(ha, "alarm_arm:")] == ["alarm_arm:night"]
    await hm.stop()

    hm, clock, ha = await start(tmp_path / "closed", at=EVENING)
    assert await security.set_setting(hm, "alarm_self", {"night": True})
    await hm.tick()
    hm.world.modes.set_manual(HouseMode.SLEEP, clock.now())
    await hm.tick()
    assert ha.states[ALARM].state == "armed_night"
    assert not _pushes(ha, "alarm_arm:")
    # Said at once in the app – not pushed at night, nobody is woken for it.
    assert await _topics(hm, "alarm_self:night")
    await hm.stop()


async def test_switched_off_quickly_twice_and_it_asks_again(tmp_path: Path) -> None:
    """HouseMind switched it on itself, and twice in a week someone switched it off again
    within ten minutes: it was not the moment. HouseMind stops doing it itself, says so to
    the administrators, and asks again next time."""
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1})
    assert await security.set_setting(hm, "alarm_self", {"away": True})
    for _ in range(2):
        await everyone_away(hm, clock, ha)
        await _until_armed(hm, clock, ha)
        assert ha.states[ALARM].state == "armed_away"
        await _home_again(hm, clock, ha)  # at once: it was not the moment
    assert security.settings_view(hm, "nl")["selfArm"]["away"] is False
    back = await _topics(hm, "alarm_self_back:away")
    items = await views.notifications(hm, "nl", True, "demo-user-1")
    said = next(i["text"] for i in items if i["id"] in back)
    assert "twee keer zette iemand het binnen tien minuten weer uit" in said
    await everyone_away(hm, clock, ha)
    await _ticks(hm, clock, 12)
    assert ha.states[ALARM].state == "disarmed" and _pushes(ha, "alarm_arm:away")
    await hm.stop()


# -- Test het huis (E-541) -----------------------------------------------------------------


async def test_the_alarm_in_test_het_huis(tmp_path: Path) -> None:
    """Every room of a house with an alarm has 'Het alarm gaat af'. Speel het na: this room's
    lamps full on, and what happens elsewhere – lamps, Vesta, everyone's message – and that a
    test never touches the alarm itself. Doe het echt: this room's lamps go on for real and
    back after a minute; only the tester gets the message, as a test; the alarm stays off."""
    hm, clock, ha = await start(tmp_path, {1: 4}, at=EVENING)
    view = hm.test_view("nl", "living", [])
    assert "alarm" in [s["key"] for s in view["situations"]]
    view = hm.test_view("nl", "living", ["alarm"])
    texts = [line["text"] for line in view["lines"]]
    assert any("aandoen" in text for text in texts), texts
    assert any(text.startswith("Ook ") and "elders in huis" in text for text in texts)
    assert "Iedereen krijgt meteen een noodmelding, met het beeld van een camera." in texts
    assert texts[-1] == "Het alarm zelf zet ik in een test niet aan of uit."
    lamp = next(line for line in view["lines"] if "aandoen" in line["text"])
    assert lamp["real"] == "Als het alarm echt afgaat, doe ik dit meteen." and lamp["will"]
    assert hm.test_choose("living", ["smoke"], "alarm", True) == ["alarm"]
    lamps = [e for e in ha.states if e.startswith("light.living")]
    for lamp_id in lamps:
        await ha.simulate(lamp_id, "off")
    assert await hm.test_play("nl", "living", ["alarm"], "demo-user-1") is None
    assert any(ha.states[e].state == "on" for e in lamps)
    assert ha.states["light.kitchen"].state == "off"  # only this room
    assert ha.states[ALARM].state == "disarmed"  # never the alarm itself
    sent = _pushes(ha, "test_alarm:")
    assert [n[0] for n in sent] == ["notify.mobile_app_phone_1"]  # only the tester
    assert sent[0][2].startswith("Test: zo komt de melding als het alarm")
    play = hm.test_view("nl", "living", [])["plays"][0]
    assert "Jij kreeg de melding, als test. Verder niemand." in [
        line["text"] for line in play["lines"]
    ]
    await clock.advance(61)
    await hm.settled()
    assert all(ha.states[e].state == "off" for e in lamps)
    sent_ids = await _topics(hm, "test_alarm:")
    items = await views.notifications(hm, "nl", True, "demo-user-1")
    assert sent_ids and not any(i["id"] in sent_ids for i in items)  # gone with the minute
    await hm.stop()


async def test_test_het_huis_says_whether_it_would_switch_the_alarm_on(tmp_path: Path) -> None:
    """'Iedereen weg' or 'Het huis slaapt' in Test het huis: one line about the alarm – asking,
    or on by itself with the standing yes – and in a test it is never switched on."""
    hm, _clock, ha = await start(tmp_path, {1: 4}, at=EVENING)
    texts = [line["text"] for line in hm.test_view("nl", "living", ["away"])["lines"]]
    assert "Ik vraag of het alarm (Gang alarmsysteem) op afwezig mag." in texts, texts
    assert await security.set_setting(hm, "alarm_self", {"away": True, "night": True})
    texts = [line["text"] for line in hm.test_view("nl", "living", ["away"])["lines"]]
    assert any(t.startswith("Ik zet het alarm (Gang alarmsysteem) zelf op afwezig") for t in texts)
    texts = [line["text"] for line in hm.test_view("nl", "living", ["sleep"])["lines"]]
    assert "Ik zet het alarm (Gang alarmsysteem) zelf op nacht. Dat mag van jullie." in texts
    await hm.test_play("nl", "living", ["away"], "demo-user-1")
    assert ha.states[ALARM].state == "disarmed"
    await hm.stop()
