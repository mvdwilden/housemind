"""Automations on the same device go off together and on together (E-538), from the side of
the administrator. The administrator's question (23-11-2026): one device is often steered by
several automations – when HouseMind switches one off, does it switch the others off too, and
on again when it gives them back? Each test fails without the change; what went wrong is in
its docstring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from housemind.brain.automations import Status
from housemind.knowledge.catalog import Step
from tests.test_automations import HALL, HALL_CONFIG, start

HALL_OFF = "automation.hall_light_off"
HALL_OFF_CONFIG: dict[str, Any] = {
    "id": "1003",
    "alias": "Hall light off without motion",
    "triggers": [
        {
            "trigger": "state",
            "entity_id": "binary_sensor.hall_motion",
            "to": "off",
            "for": {"minutes": 2},
        }
    ],
    "actions": [{"action": "light.turn_off", "target": {"entity_id": "light.hall"}}],
}
HALL_PARTY = "automation.hall_party"
HALL_PARTY_CONFIG: dict[str, Any] = {
    "id": "1004",
    "alias": "Hall light and a message at eight",
    "triggers": [{"trigger": "time", "at": "20:00:00"}],
    "actions": [
        {"action": "light.turn_on", "target": {"entity_id": "light.hall"}},
        {"action": "notify.mobile_app_phone_1", "data": {"message": "Party"}},
    ],
}


async def test_off_together_and_back_together(tmp_path: Path) -> None:
    """Switching off the hall's motion automation left 'light off without motion' on: the hall
    lamp stayed another system's, so HouseMind only watched it – and the light at motion,
    which the automation that went off had done, was done by nobody."""
    hm, _clock, ha = await start(tmp_path, {HALL: HALL_CONFIG, HALL_OFF: HALL_OFF_CONFIG})
    assert hm.learning is not None and hm.gate is not None
    for skill in ("light_on_entry", "light_off_leave"):
        hm.learning.steps[(skill, "hall")] = Step.AUTONOMOUS
    result = await hm.turn_off_automation(HALL, "demo-user-1")
    assert result["ok"], result
    assert ha.states[HALL].state == "off" and ha.states[HALL_OFF].state == "off"
    assert hm.automations.status[HALL_OFF] is Status.TURNED_OFF
    assert "light.hall" not in hm.gate.state.other_system  # HouseMind's now
    # Back on: an undo of the one brings the other back too.
    back = await hm.restore_automation(HALL, "demo-user-1")
    assert back["ok"]
    assert ha.states[HALL].state == "on" and ha.states[HALL_OFF].state == "on"
    assert "light.hall" in hm.gate.state.other_system
    await hm.stop()


async def test_one_that_cannot_go_keeps_them_all_on(tmp_path: Path) -> None:
    """An automation that does more than HouseMind can (a message too) on the same lamp:
    switching the other off left that lamp to it anyway – nothing gained, work left undone.
    Now none goes off, and the card says which one holds them."""
    hm, _clock, ha = await start(tmp_path, {HALL: HALL_CONFIG, HALL_PARTY: HALL_PARTY_CONFIG})
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    result = await hm.turn_off_automation(HALL, "demo-user-1")
    assert not result["ok"] and result["reason"] == "held_by"
    assert result["by"] == ["Hall light and a message at eight"]
    assert ha.states[HALL].state == "on" and ha.states[HALL_PARTY].state == "on"
    question = hm.automation_question("light_on_entry", "hall")
    assert question is not None and question["held"] == ["Hall light and a message at eight"]
    await hm.stop()
