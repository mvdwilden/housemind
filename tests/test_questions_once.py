"""A question is answered once (E-585), as the administrator of the real house asked: "Elke
vraag die wordt gesteld ... moet eenmalig beantwoord worden door een beheerder of door iemand
anders. Als die vraag al beantwoord is, wil ik niet dat die nog een keer zichtbaar is." From
the user's side: the phones it reached, a second answer on a screen, and the questions that
came back although they were answered. What each test checks is its docstring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain import security
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_alarm_self import ALARM, _home_again, _offer, _pushes, _topics
from tests.test_cameras import ROOT, everyone_away, start
from tests.test_safety_gaps import _house
from tests.test_start_over import _hello, _next, _snapshot

ADMIN = {"X-Remote-User-Id": "demo-user-1"}


def _cleared(ha: Any, topic: str) -> list[Any]:
    return [c for c in ha.cleared if c[1] == topic]


async def test_an_answer_takes_the_question_off_every_phone_and_a_late_tap_does_nothing(
    tmp_path: Path,
) -> None:
    """Everyone left: the question whether the alarm may go on reaches the phones. Someone
    says no on a screen: the question is taken off every phone it reached, and a tap on Ja on
    a phone that still showed it changes nothing – the alarm stays off."""
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1})
    await everyone_away(hm, clock, ha)
    asked = _pushes(ha, "alarm_arm:away")
    assert asked
    topic = str(asked[-1][3]["tag"])
    nid = next(i for i, t in (await _topics(hm, "alarm_arm:away")).items() if t == topic)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, ADMIN)
        await _snapshot(ws)
        await ws.send_json({"type": "notification", "id": nid, "action": "no"})
        await ws.send_json({"type": "set_pref", "key": "timing", "value": "normal"})
        await _next(ws, "saved")
        await ws.close()
    assert _cleared(ha, topic)
    await ha.press_notification_action(asked[-1][3]["actions"][0]["action"])  # Ja, late
    assert ha.states[ALARM].state == "disarmed"
    await hm.stop()


async def test_a_second_answer_on_a_screen_is_told_it_was_answered(tmp_path: Path) -> None:
    """Two screens showed the same question; on the second, Ja after the first said no: it
    says the question was answered already, and the alarm stays off."""
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1})
    await everyone_away(hm, clock, ha)
    topic = str(_pushes(ha, "alarm_arm:away")[-1][3]["tag"])
    nid = next(i for i, t in (await _topics(hm, "alarm_arm:away")).items() if t == topic)
    assert await security.answer(hm, topic, "no", "demo-user-1")
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, ADMIN)
        await _snapshot(ws)
        await ws.send_json({"type": "notification", "id": nid, "action": "yes"})
        said = await _next(ws, "saved")
        assert said["text"] == "Deze vraag is al beantwoord."
        await ws.close()
    assert ha.states[ALARM].state == "disarmed"
    await hm.stop()


async def test_no_to_switching_it_on_by_itself_is_not_asked_again(tmp_path: Path) -> None:
    """Five yes, the question whether HouseMind may do it itself, and the answer no: a month
    and five more yes later it is not asked again."""
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1})
    for _ in range(5):
        await everyone_away(hm, clock, ha)
        asked = _pushes(ha, "alarm_arm:away")
        await ha.press_notification_action(asked[-1][3]["actions"][0]["action"])
        await _home_again(hm, clock, ha)
    assert await _offer(hm) is not None
    assert await security.answer(hm, "alarm_self_offer:away", "no", "demo-user-1")
    await clock.advance(31 * 24 * 3600)
    for _ in range(5):
        await everyone_away(hm, clock, ha)
        asked = _pushes(ha, "alarm_arm:away")
        await ha.press_notification_action(asked[-1][3]["actions"][0]["action"])
        await _home_again(hm, clock, ha)
    assert await _offer(hm) is None  # not asked a second time
    await hm.stop()


async def test_the_question_for_this_time_is_not_asked_again_after_a_restart(
    tmp_path: Path,
) -> None:
    """Asked whether the alarm may go on now that everyone left; HouseMind restarts while they
    are still away: the same question does not come a second time."""
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1})
    await everyone_away(hm, clock, ha)
    before = len(_pushes(ha, "alarm_arm:away"))
    assert before == 1
    await hm.stop()
    again = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path))
    await again.start(autorun=False)
    for _ in range(5):
        await clock.advance(60)
        await again.tick()
    assert len(_pushes(ha, "alarm_arm:away")) == before
    await again.stop()


async def test_a_no_to_care_is_kept(tmp_path: Path) -> None:
    """The person care is about says no: they are not asked again when an administrator saves
    the house settings – only when the care itself changes."""
    hm, _ha = await _house(tmp_path)
    for i in (1, 2):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (f"demo-user-{i}", f"Resident {i}", 3, json.dumps({}), hm.clock.now().isoformat()),
        )
    await hm._refresh_people()
    await hm.set_house_setting("experimental", ["care"])
    care = {"person": "person.resident_2", "contact": "demo-user-1", "consent": True}
    await hm.set_house_setting("care", care)
    assert await hm.answer_care("demo-user-2", False)
    await hm.set_house_setting("care", care)  # saved again, the same care
    open_ = await hm.db.fetchone(
        "SELECT id FROM notifications WHERE topic = 'care_consent' AND resolved_at IS NULL"
    )
    assert open_ is None
    await hm.set_house_setting("care", {**care, "contact": "demo-user-2"})  # another contact
    open_ = await hm.db.fetchone(
        "SELECT id FROM notifications WHERE topic = 'care_consent' AND resolved_at IS NULL"
    )
    assert open_ is not None
    await hm.stop()
