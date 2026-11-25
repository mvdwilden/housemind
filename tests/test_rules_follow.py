"""Rules follow the devices (E-485 – E-488, design 72), from the user's side.

New curtains in a room show their rules in Brein at once, on Observe, 'Nog niet gezien'; a room
HouseMind does not know gets what fits anywhere and one question about its kind, with the list
and Geen van deze; a rule that needs a measurement waits for the sensor; a removed device takes
its rule along, what was learned waits 30 days for a device that comes back, and then it is
forgotten; a device that is only unavailable keeps its rule.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from housemind.brain.readiness import readiness
from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Entity, State
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from tests.knowledge_page import confirmed, to_confirm
from tests.test_real_house_counts import ROOT, _house

CURTAIN = "cover.music_curtain"
FAN = "fan.music_fan"
HUMID = "sensor.music_humidity"
TOPIC = "room_kind:music"


def _music_room(curtain: bool = False) -> Any:
    def prepare(ha: Any) -> None:
        ha.model.areas["music"] = Area("music", "Muziekkamer")  # no word HouseMind knows
        if curtain:
            ha.model.entities[CURTAIN] = _curtain()
            ha.states[CURTAIN] = State(CURTAIN, "closed", {}, ha._clock.now())

    return prepare


def _curtain() -> Entity:
    return Entity(
        CURTAIN, "r-curtain", "Gordijn muziekkamer", area_id="music", device_class="curtain"
    )


async def _start(tmp_path: Path, curtain: bool = False) -> tuple[HouseMind, SimulatedClock, Any]:
    hm, clock, ha = await _house(tmp_path, prepare=_music_room(curtain))
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, clock, ha


async def _rows(hm: HouseMind, domain: str = "covers") -> dict[tuple[str, str], dict[str, Any]]:
    for part in await readiness(hm, "nl", 4, True):
        if part["domain"] == domain:
            return {(r["skill"], r["area"]): r for r in part.get("skills", [])}
    return {}


async def _review(hm: HouseMind, clock: SimulatedClock, minutes: int = 16) -> None:
    await clock.advance(minutes * 60)
    hm._last_review = None
    await hm.tick()


async def _questions(hm: HouseMind) -> list[dict[str, Any]]:
    return [dict(n) for n in await hm.notifications() if str(n["topic"]).startswith("room_kind:")]


async def test_new_curtains_in_a_room_it_does_not_know_get_their_rules_at_once(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await _start(tmp_path)
    assert hm.area_roles["music"] == "other"
    assert not [k for k in await _rows(hm) if k[1] == "music"]
    await ha.add_entity(_curtain(), State(CURTAIN, "closed", {}, clock.now()))
    await hm._refresh_model()
    rows = await _rows(hm)
    music = {k for k in rows if k[1] == "music"}
    assert music == {("covers_close_dark", "music"), ("covers_open_morning", "music")}
    for key in music:
        assert rows[key]["step"] == "observe"
        assert rows[key]["text"].startswith("Nog niet gezien"), rows[key]["text"]
    # One question to the administrator, with Kiezen and Nu niet, and its line on the page.
    await _review(hm, clock)
    asked = await _questions(hm)
    assert [q["topic"] for q in asked] == [TOPIC]
    item = views.notice_item(hm, "nl", asked[0], True, "demo-user-1")
    assert item is not None and item["actions"] == ["choose", "no"]
    assert "Muziekkamer" in item["text"]
    assert views.notice_item(hm, "nl", asked[0], False, "demo-user-2") is None
    page = views.house_knowledge(hm, "nl")
    line = next(r for r in to_confirm(page) if r["id"] == "area:music:role")
    assert line["unknown"] and line["what"] == "Soort onbekend"
    await _review(hm, clock)
    assert len(await _questions(hm)) == 1  # once
    await hm.stop()


async def test_choosing_bedroom_makes_the_morning_rule_the_one_for_getting_up(
    tmp_path: Path,
) -> None:
    hm, clock, _ha = await _start(tmp_path, curtain=True)
    await _review(hm, clock)
    assert await _questions(hm)
    assert await hm.answer_knowledge("area:music:role", True, "bedroom")
    rows = {k for k in await _rows(hm) if k[1] == "music"}
    assert rows == {("covers_close_dark", "music"), ("covers_open_wake", "music")}
    assert not await _questions(hm)
    await hm.stop()


async def test_none_of_these_keeps_what_fits_anywhere_and_asks_no_more(tmp_path: Path) -> None:
    hm, clock, _ha = await _start(tmp_path, curtain=True)
    await _review(hm, clock)
    assert await hm.answer_knowledge("area:music:role", True, "other")
    assert hm.area_roles["music"] == "other"
    assert {k for k in await _rows(hm) if k[1] == "music"} == {
        ("covers_close_dark", "music"),
        ("covers_open_morning", "music"),
    }
    assert not await _questions(hm)
    await _review(hm, clock)
    assert not await _questions(hm)
    rows = confirmed(views.house_knowledge(hm, "nl"))
    assert any(r["id"] == "area:music:role" and r["what"] == "Geen van deze" for r in rows)
    await hm.stop()


async def test_not_now_is_not_asked_again_also_after_a_restart(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path, curtain=True)
    await _review(hm, clock)
    await hm.answer_room_kind(TOPIC)
    assert not await _questions(hm)
    await _review(hm, clock)
    assert not await _questions(hm)
    await hm.stop()
    again = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path))
    await again.start(autorun=False)
    await _review(again, clock)
    assert not await _questions(again)
    await again.stop()


async def test_a_rule_that_needs_a_measurement_waits_for_the_sensor(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path)
    fan = Entity(FAN, "r-fan", "Ventilator muziekkamer", area_id="music")
    await ha.add_entity(fan, State(FAN, "off", {}, clock.now()))
    await hm._refresh_model()
    assert ("ventilate_humidity", "music") not in await _rows(hm, "air")
    sensor = Entity(HUMID, "r-humid", "Vocht muziekkamer", area_id="music", device_class="humidity")
    await ha.add_entity(sensor, State(HUMID, "55", {"unit_of_measurement": "%"}, clock.now()))
    await hm._refresh_model()
    rows = await _rows(hm, "air")
    assert ("ventilate_humidity", "music") in rows
    assert ("ventilate_co2", "music") not in rows  # no CO₂ sensor there
    await hm.stop()


async def test_a_removed_curtain_takes_its_rule_along_and_what_was_learned_waits_30_days(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await _start(tmp_path, curtain=True)
    assert hm.learning is not None
    pair = ("covers_close_dark", "music")
    await hm.set_admin_step("covers_close_dark@music", "autonomous", "demo-user-1")
    hm.learning.values[(*pair, "*", "sun_elevation")] = (-2.0, 3.0, clock.now())
    assert (await _rows(hm))[pair]["step"] == "autonomous"
    # Gone: the rule leaves Brein.
    await ha.remove_entity(CURTAIN)
    await hm._refresh_model()
    await _review(hm, clock)
    assert pair not in await _rows(hm)
    gone = await hm.db.get_setting("rules_gone", {})
    assert "covers_close_dark@music" in gone
    # Back after ten days: it goes on where it was.
    await clock.advance(10 * 86400)
    await ha.add_entity(_curtain(), State(CURTAIN, "closed", {}, clock.now()))
    await hm._refresh_model()
    await _review(hm, clock)
    assert (await _rows(hm))[pair]["step"] == "autonomous"
    assert (*pair, "*", "sun_elevation") in hm.learning.values
    assert "covers_close_dark@music" not in await hm.db.get_setting("rules_gone", {})
    # Gone again, and 31 days later: forgotten, with the administrator's own step.
    await ha.remove_entity(CURTAIN)
    await hm._refresh_model()
    await _review(hm, clock)
    await clock.advance(31 * 86400)
    await _review(hm, clock)
    assert "covers_close_dark@music" not in hm.admin_steps
    assert (*pair, "*", "sun_elevation") not in hm.learning.values
    assert pair not in hm.learning.steps
    raw = await hm.db.get_setting("admin_step", {})
    assert "covers_close_dark@music" not in raw
    await ha.add_entity(_curtain(), State(CURTAIN, "closed", {}, clock.now()))
    await hm._refresh_model()
    row = (await _rows(hm))[pair]  # as a new rule: start knowledge asks the first time
    assert row["step"] == "propose" and row["text"].startswith("Nog niet gezien"), row["text"]
    await hm.stop()


async def test_a_curtain_that_is_only_unavailable_keeps_its_rule(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path, curtain=True)
    await ha.simulate(CURTAIN, "unavailable")
    await _review(hm, clock)
    assert ("covers_close_dark", "music") in await _rows(hm)
    assert "covers_close_dark@music" not in (await hm.db.get_setting("rules_gone", {}) or {})
    await hm.stop()


async def test_an_automation_for_curtains_in_such_a_room_teaches_it(tmp_path: Path) -> None:
    automation = {
        "alias": "Muziekkamer - gordijn dicht bij zonsondergang",
        "triggers": [{"trigger": "sun", "event": "sunset", "offset": 0}],
        "actions": [{"action": "cover.close_cover", "target": {"entity_id": [CURTAIN]}}],
    }
    hm, _clock, _ha = await _house(
        tmp_path,
        prepare=_music_room(curtain=True),
        automations={"automation.music_close": automation},
    )
    reading = hm.automations.readings["automation.music_close"]
    assert ("covers_close_dark", "music") in reading.learned
    await hm.stop()


async def test_a_question_only_for_who_sees_the_list(tmp_path: Path) -> None:
    """On Geïnformeerd the page with the kinds is not there: no question then, and Vesta's
    own Klopt question is not about 'soort onbekend' either."""
    hm, clock, _ha = await _start(tmp_path, curtain=True)
    await hm.db.execute("UPDATE people SET level = 2 WHERE user_id = 'demo-user-1'")
    await hm._refresh_people()
    await _review(hm, clock)
    assert not await _questions(hm)
    page = views.house_knowledge(hm, "nl")  # the line waits, for when there is a page
    assert any(r["id"] == "area:music:role" and r.get("unknown") for r in to_confirm(page))
    topics = [str(n["topic"]) for n in await hm.notifications()]
    assert "knowledge:area:music:role" not in topics
    params = [json.loads(str(n["params"] or "{}")) for n in await hm.notifications()]
    assert all("Muziekkamer" not in json.dumps(p) for p in params)
    await hm.stop()


async def test_a_lamp_housemind_leaves_alone_keeps_its_rule(tmp_path: Path) -> None:
    """Found in the real house: a bedroom whose light is the residents' own switch, worked with
    a button. HouseMind leaves that switch alone (E-448), so no new rule comes for it – but a
    rule an administrator set there is no rule whose device went: it stays, and nothing of it
    is forgotten."""
    lamp = "light.music_lamp"

    def prepare(ha: Any) -> None:
        _music_room()(ha)
        ha.model.entities[lamp] = Entity(lamp, "r-lamp", "Lamp muziekkamer", area_id="music")
        ha.states[lamp] = State(lamp, "off", {"supported_color_modes": ["onoff"]}, ha._clock.now())

    hm, clock, _ha = await _house(tmp_path, prepare=prepare)
    assert hm.skills is not None and hm.learning is not None
    pair = ("light_on_entry", "music")
    assert pair in await _rows(hm, "lighting")
    await hm.set_admin_step("light_on_entry@music", "autonomous", "demo-user-1")
    hm.learning.first_seen[pair] = clock.now()
    hm.skills.hand_switches = {lamp}  # now worked by the residents' own button
    hm.skills.forget()
    assert pair not in hm.rules_possible()
    row = (await _rows(hm, "lighting"))[pair]
    assert row["step"] == "autonomous"
    assert hm.world is not None
    await clock.advance(31 * 86400)
    await hm._rules_follow(hm.world.snapshot(clock.now()))
    assert "light_on_entry@music" in hm.admin_steps
    assert "light_on_entry@music" not in (await hm.db.get_setting("rules_gone", {}) or {})
    await hm.stop()
