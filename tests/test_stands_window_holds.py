"""While you set a lamp's standen, HouseMind keeps off it (E-516, B-442, design 83).

The administrator's wish (14-11-2026): trying standen, it felt as if something else took the lamp
over. As long as the Standen window of a lamp is open, the one setting it decides: no rule, no
proposal, no house rule, no AI touches it, and a Proef stays on the lamp – not only for a minute.
Klaar or closing the window puts it back as it was, and HouseMind takes it over again. A screen
that goes away lets go by itself, a minute after it last said it was still open.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from housemind.knowledge.catalog import Step
from housemind.safety.gate import ActionRequest, Layer
from tests.test_light_levels import STEEP, local, pct, start

LAMP = "light.living_ceiling"
LIVING = "binary_sensor.living_presence"


def _rule(hm, layer: Layer = Layer.REFLEX, action: str = "light.turn_off") -> ActionRequest:  # type: ignore[no-untyped-def]
    return ActionRequest(
        layer,
        LAMP,
        action,
        {},
        skill="light_off_leave" if layer is not Layer.AI else None,
        area_id="living",
        created_at=hm.clock.now(),
    )


async def _open_window(hm, clock, seconds: float) -> None:  # type: ignore[no-untyped-def]
    """The window stays open: it says so every 20 seconds, as the screen does."""
    hm.levels_hold(LAMP)
    passed = 0.0
    while passed < seconds:
        await clock.advance(20)
        passed += 20
        hm.levels_hold(LAMP)
        await hm.tick()


async def test_a_rule_waits_while_the_window_is_open(tmp_path: Path) -> None:
    hm, clock, _ha = await start(local(19, 20, 0), tmp_path)
    assert hm.gate is not None
    hm.levels_hold(LAMP)
    for layer in (Layer.REFLEX, Layer.PLANNER, Layer.LEARNING, Layer.AI, Layer.HOUSE_RULE):
        assert hm.gate.evaluate(_rule(hm, layer)).reason == "setting_stands", layer
    # A person's own Doe het still goes; so would an emergency.
    assert hm.gate.evaluate(_rule(hm, Layer.PERSON_ANSWER)).reason != "setting_stands"
    assert hm.gate.evaluate(_rule(hm, Layer.EMERGENCY)).reason != "setting_stands"
    assert hm.in_test(LAMP, clock.now())  # what happens to it now teaches nothing
    # Closing the window: HouseMind's again.
    await hm.levels_release(LAMP)
    assert hm.gate.evaluate(_rule(hm)).reason != "setting_stands"
    assert not hm.in_test(LAMP, clock.now())
    await hm.stop()


async def test_the_why_not_says_who_decides_now(tmp_path: Path) -> None:
    hm, _clock, _ha = await start(local(19, 20, 0), tmp_path)
    assert hm.gate is not None and hm.explainer is not None
    hm.levels_hold(LAMP)
    decision = hm.gate.evaluate(_rule(hm))
    said = hm.explainer.why_not(decision, "nl")
    assert "standen van deze lamp" in said and "venster" in said
    await hm.stop()


async def test_a_proef_stays_on_the_lamp_until_klaar(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 20, 0), tmp_path)
    await hm.levels_save(LAMP, STEEP.to_json())
    assert ha.states[LAMP].state == "off"
    hm.levels_hold(LAMP)
    assert await hm.levels_try("nl", [LAMP], 2, "demo-user-1") is None
    assert pct(ha, LAMP) == 16
    # Five minutes of looking and thinking: not back after a minute.
    await _open_window(hm, clock, 300)
    assert pct(ha, LAMP) == 16
    # Klaar: back as it was.
    await hm.levels_back([LAMP])
    assert ha.states[LAMP].state == "off"
    await hm.levels_release(LAMP)
    await hm.stop()


async def test_closing_the_window_puts_it_back_and_hands_it_over(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 20, 0), tmp_path)
    assert hm.gate is not None
    hm.levels_hold(LAMP)
    await hm.levels_try("nl", [LAMP], 3, "demo-user-1")
    assert ha.states[LAMP].state == "on"
    await _open_window(hm, clock, 120)
    await hm.levels_release(LAMP)
    assert ha.states[LAMP].state == "off"
    assert hm.gate.evaluate(_rule(hm, action="light.turn_on")).reason != "setting_stands"
    await hm.stop()


async def test_a_screen_that_went_away_lets_go_by_itself(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 20, 0), tmp_path)
    assert hm.gate is not None
    hm.levels_hold(LAMP)
    await hm.levels_try("nl", [LAMP], 1, "demo-user-1")
    assert ha.states[LAMP].state == "on"
    # The phone went into a pocket: nothing more from the window.
    for _ in range(5):
        await clock.advance(15)
        await hm.tick()
    assert ha.states[LAMP].state == "off"  # back as it was, a minute later
    assert hm.gate.evaluate(_rule(hm)).reason != "setting_stands"
    await hm.stop()


async def test_someone_leaving_does_not_put_out_the_lamp_being_set(tmp_path: Path) -> None:
    """End to end: the lamp is on a stand, the room empties, the room's wait is over – the rule
    that puts the light out waits until the window closes."""
    hm, clock, ha = await start(local(19, 20, 0), tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "living")] = Step.AUTONOMOUS
    hm.learning.steps[("light_off_leave", "living")] = Step.AUTONOMOUS
    await ha.simulate(LIVING, "on")
    await hm.tick()
    hm.levels_hold(LAMP)
    await hm.levels_try("nl", [LAMP], 2, "demo-user-1")
    level = pct(ha, LAMP)
    assert level is not None
    await ha.simulate(LIVING, "off")
    await _open_window(hm, clock, timedelta(minutes=40).total_seconds())
    assert pct(ha, LAMP) == level  # still as you set it
    # Done: back as it was, and from now on the room's own rules count again.
    await hm.levels_release(LAMP)
    await clock.advance(30)
    await hm.tick()
    assert hm.gate is not None
    assert hm.gate.evaluate(_rule(hm)).reason != "setting_stands"
    await hm.stop()
