"""The automation that steers a row, right at the step (E-317, wish from the real house,
21-10-2026): "if I set it to Autonomous, HouseMind and the automation both do it by
themselves". In 'Klaar voor autonomie?' each row says which automation steers it. Since
01-11-2026 ("zodra er iets op autonoom wordt gezet, dan dat het desbetreffende automatisering
uitgezet wordt, en deze weer aangezet wordt zodra het terug gezet wordt"): an administrator
setting Autonomous switches off what HouseMind can wholly take over, and a step back switches
it on again; only an automation that does more still asks, in the row. Vesta sets a step for
an administrator and says the same. Each test fails without it; what it checks is its
docstring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.automations import Status
from housemind.brain.readiness import readiness
from housemind.runtime import HouseMind
from housemind.server.web import create_app
from tests.test_automations import HALL, HALL_CONFIG, LIVING_CONFIG, start
from tests.test_start_over import _hello, _next, _snapshot

LIVING = "automation.living_light"
BOTH = "automation.hall_and_kitchen"
BOTH_CONFIG: dict[str, Any] = {
    "id": "1003",
    "alias": "Hall and kitchen light at motion",
    "triggers": [
        {"trigger": "state", "entity_id": "binary_sensor.hall_motion", "to": "on"},
        {"trigger": "state", "entity_id": "binary_sensor.kitchen_motion", "to": "on"},
    ],
    "actions": [
        {"action": "light.turn_on", "target": {"entity_id": "light.hall"}, "data": {}},
        {"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}, "data": {}},
    ],
}


async def _expert(hm: HouseMind) -> None:
    """The administrator on Expert, where a step per room is set (20.10)."""
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 4, json.dumps({}), hm.clock.now().isoformat()),
    )
    await hm._refresh_people()


async def _row(hm: HouseMind, skill: str, area: str) -> dict[str, Any]:
    for part in await readiness(hm, "nl", 4, True):
        for row in part.get("skills", []):
            if (row["skill"], row["area"]) == (skill, area):
                return dict(row)
    raise AssertionError(f"no row {skill} {area}")


async def _seen(hm: HouseMind) -> None:
    """HouseMind reads the automations, as it does every few minutes."""
    await hm._sync_automations()


async def test_the_row_says_which_automation_steers_it_and_autonomous_switches_it_off(
    tmp_path: Path,
) -> None:
    """The row of the hall says that 'Hall light at motion' steers it. Set to Autonomous by an
    administrator: that is the yes – the automation goes off in Home Assistant at once, with
    no question, and the row says HouseMind does it now."""
    hm, _clock, ha = await start(tmp_path, {HALL: HALL_CONFIG})
    await _expert(hm)
    await _seen(hm)
    row = await _row(hm, "light_on_entry", "hall")
    assert row["steer"]["text"].startswith("De automatisering ‘Hall light at motion’ stuurt dit")
    assert "question" not in row  # not on Autonomous: nothing to ask yet
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client)
        await _snapshot(ws)
        await ws.send_json(
            {"type": "admin_step", "key": "light_on_entry@hall", "step": "autonomous"}
        )
        await _next(ws, "saved")
        await ws.close()
    assert ha.states[HALL].state == "off" and hm.automations.status[HALL] is Status.TURNED_OFF
    row = await _row(hm, "light_on_entry", "hall")
    assert "question" not in row
    assert row["steer"]["text"] == "‘Hall light at motion’ heb ik uitgezet; ik doe dit nu zelf."
    await hm.stop()


async def test_back_from_autonomous_switches_it_on_again(tmp_path: Path) -> None:
    """Back to Voorstellen, or to Observeren: the automation goes on again at once, so nothing
    is left undone, and nothing is asked."""
    hm, _clock, ha = await start(tmp_path, {HALL: HALL_CONFIG})
    await _expert(hm)
    await _seen(hm)
    for lower in ("propose", "observe"):
        await hm.set_admin_step("light_on_entry@hall", "autonomous", "demo-user-1")
        assert ha.states[HALL].state == "off"
        await hm.set_admin_step("light_on_entry@hall", lower, "demo-user-1")
        assert ha.states[HALL].state == "on"
        assert hm.automations.status[HALL] is Status.SEEN
        assert "question" not in await _row(hm, "light_on_entry", "hall")
    await hm.stop()


async def test_other_rows_go_along_and_what_else_stops_is_named(tmp_path: Path) -> None:
    """An automation that also steers the kitchen: the question says the kitchen goes to
    Autonomous too, and a yes sets it. One that also sends a message: the question says that
    stops too, and the administrator may still say yes."""
    hm, _clock, ha = await start(tmp_path, {BOTH: BOTH_CONFIG, LIVING: LIVING_CONFIG})
    await _expert(hm)
    await _seen(hm)
    await hm.set_admin_step("light_on_entry@hall", "autonomous", "demo-user-1")
    question = (await _row(hm, "light_on_entry", "hall"))["question"]
    assert "Dan zet ik ook op Autonoom: licht bij binnenkomen in de keuken." in question["text"]
    assert (await hm.turn_off_automation(BOTH, "demo-user-1", along=True))["ok"]
    assert hm.autonomous_here("light_on_entry", "kitchen")
    assert ha.states[BOTH].state == "off"

    pair = next(p for p in hm.automations.readings[LIVING].learned)
    await hm.set_admin_step(f"{pair[0]}@{pair[1]}", "autonomous", "demo-user-1")
    question = (await _row(hm, *pair))["question"]
    assert "Dan stopt ook: één andere actie, zoals een bericht." in question["text"]
    assert (await hm.turn_off_automation(LIVING, "demo-user-1"))["reason"] == "not_whole"
    assert (await hm.turn_off_automation(LIVING, "demo-user-1", along=True))["ok"]
    assert ha.states[LIVING].state == "off"
    await hm.stop()


async def test_no_leaves_it_on_and_a_button_stays(tmp_path: Path) -> None:
    """An automation that also steers another room: set to Autonomous, the question is in the
    row, not once more as a message. 'Nee': the automation stays on, HouseMind keeps watching,
    and instead of the question the row keeps a button to do it later."""
    hm, clock, ha = await start(tmp_path, {BOTH: BOTH_CONFIG})
    await _expert(hm)
    await _seen(hm)
    await hm.set_admin_step("light_on_entry@hall", "autonomous", "demo-user-1")
    await clock.advance(16 * 60)
    await hm.tick()
    assert f"automation:{BOTH}" not in hm._raised
    assert ha.states[BOTH].state == "on"  # it also steers the kitchen: asked, not done
    assert (await hm.leave_automation(BOTH, "demo-user-1"))["ok"]
    row = await _row(hm, "light_on_entry", "hall")
    assert row["question"]["declined"] is True
    assert row["question"]["button"] == "‘Hall and kitchen light at motion’ uitzetten en zelf doen"
    assert ha.states[BOTH].state == "on" and "light.hall" in hm.automations.controlled()
    await hm.stop()


async def test_vesta_sets_a_step_for_an_administrator_and_asks_about_the_automation(
    tmp_path: Path,
) -> None:
    """Talking only to Vesta: "zet licht bij binnenkomen in de gang op autonoom". She reads it
    back and asks yes or no; after "ja" she sets it, and says the automation is off now."""
    hm, _clock, ha = await start(tmp_path, {HALL: HALL_CONFIG})
    await _expert(hm)
    await _seen(hm)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client)
        await _snapshot(ws)
        await ws.send_json(
            {"type": "talk", "text": "Zet licht bij binnenkomen in de gang op autonoom"}
        )
        said = await _next(ws, "say")
        assert said["text"] == "Zal ik licht bij binnenkomen in de gang op Autonoom zetten?"
        assert said["confirm"]
        await ws.send_json({"type": "confirm_rule", "token": said["confirm"], "yes": True})
        said = await _next(ws, "say")
        assert said["text"].startswith("Gedaan: licht bij binnenkomen in de gang staat nu zelf")
        assert said["text"].endswith("‘Hall light at motion’ staat uit. Ik doe dit nu zelf.")
        assert not said.get("confirm")  # nothing more to ask
        await ws.close()
    assert hm.autonomous_here("light_on_entry", "hall") and ha.states[HALL].state == "off"
    await hm.stop()


async def test_vesta_sets_no_step_for_someone_she_does_not_know(tmp_path: Path) -> None:
    """On a shared speaker (no user) or for a resident who is no administrator, she sets
    nothing and says where it can be done; Assist on the administrator's own phone works,
    with the same question."""
    hm, _clock, ha = await start(tmp_path, {HALL: HALL_CONFIG})
    await _expert(hm)
    await _seen(hm)
    wish = "zet licht bij binnenkomen in de gang op autonoom"
    shared = await hm.assist(wish, "nl", "speaker", None)
    assert shared["text"] == hm.languages.text("nl", "talk.step.admin_only")
    resident = await hm.assist(wish, "nl", "phone-2", "demo-user-2")
    assert resident["text"] == hm.languages.text("nl", "talk.step.admin_only")
    assert not hm.autonomous_here("light_on_entry", "hall")

    asked = await hm.assist(wish, "nl", "phone-1", "demo-user-1")
    assert asked["continue"] and asked["text"].startswith("Zal ik licht bij binnenkomen")
    second = await hm.assist("ja", "nl", "phone-1", "demo-user-1")
    assert not second["continue"] and "‘Hall light at motion’ staat uit" in second["text"]
    assert ha.states[HALL].state == "off"
    await hm.stop()
