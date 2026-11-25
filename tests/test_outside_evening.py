"""The lamps outside in the evening (E-446). The administrator's wish (01-11-2026): "Het
belangrijkste is dat HM de controle overneemt bij autonoom; dus je mag leren van de
automatiseringen, en als ze uit staan, mag je best de beste instelling kiezen. Als voorbeeld
mogen de buitenverlichting best aan na zonsondergang en als er niemand thuis; dit vanwege de
veiligheid (om inbrekers op afstand te houden)." Each test fails without the change; what it
checks is its docstring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.brain.automations import read_automation
from housemind.brain.learning import Result
from housemind.brain.skills import EVENING, load_skill_params
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source, State
from housemind.demo.houses import load_demo_house
from housemind.ha.translate import parse_automation
from housemind.knowledge.catalog import Step
from housemind.knowledge.loader import load_catalog
from housemind.runtime import HouseMind
from housemind.safety.gate import ActionRequest, Decision, Layer
from housemind.world.house_mode import HouseMode
from tests.test_automation_in_row import _expert, _row
from tests.test_real_house_counts import _house, _until

ROOT = Path(__file__).resolve().parents[1]
LAMP = "light.garden"
# 1 November in Amsterdam: the sun sets at about 16:58 (CET); 15:00 UTC is 16:00 there.
AFTERNOON = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
# An automation as people write them: on at sunset, out at a quarter to midnight or when the
# house goes to sleep – only when someone is home.
GARDEN_LIGHT: dict[str, Any] = {
    "id": "2001",
    "alias": "Garden light in the evening",
    "triggers": [
        {"trigger": "sun", "event": "sunset", "offset": 0, "id": "dusk"},
        {"trigger": "time", "at": "23:45:00", "id": "night"},
    ],
    "conditions": [],
    "actions": [
        {
            "choose": [
                {
                    "conditions": [
                        {"condition": "trigger", "id": ["dusk"]},
                        {"condition": "state", "entity_id": "person.resident_1", "state": "home"},
                    ],
                    "sequence": [
                        {
                            "action": "light.turn_on",
                            "data": {"brightness_pct": 100, "color_temp_kelvin": 3100},
                            "target": {"entity_id": [LAMP]},
                        }
                    ],
                },
                {
                    "conditions": [{"condition": "trigger", "id": ["night"]}],
                    "sequence": [{"action": "light.turn_off", "target": {"entity_id": LAMP}}],
                },
            ]
        }
    ],
    "mode": "single",
}


async def _garden(
    tmp_path: Path, mode: HouseMode, step: Step = Step.AUTONOMOUS, start: datetime = AFTERNOON
) -> tuple[HouseMind, SimulatedClock, Any]:
    """The family house in Amsterdam, with its garden lamp; the house in this mode."""
    hm, clock, ha = await _house(tmp_path, start=start, located=True)
    assert hm.world is not None and hm.learning is not None and hm.gate is not None
    hm.activated, hm.gate.state.inactive = True, False  # past the introduction (E-275)
    hm.world.modes.set_manual(mode, clock.now())
    hm.learning.steps[(EVENING, "garden")] = step
    return hm, clock, ha


def _evening_decisions(hm: HouseMind) -> list[Decision]:
    return [r.decision for r in hm.recent.values() if r.decision.request.skill == EVENING]


def _why(hm: HouseMind, decision: Decision) -> str:
    return " ".join(hm.explain(decision, 3, "nl"))


async def test_the_lamps_outside_go_on_at_dusk_also_when_nobody_is_home(tmp_path: Path) -> None:
    """Nobody home, the sun going down: the garden lamp goes on, so the house looks lived in.
    The logbook says why – also now that nobody is home."""
    hm, clock, ha = await _garden(tmp_path, HouseMode.AWAY)
    assert ha.states[LAMP].state == "off"
    await _until(hm, clock, AFTERNOON + timedelta(hours=1), step=30)  # 17:00 in Amsterdam
    assert ha.states[LAMP].state == "on"
    on = _evening_decisions(hm)
    assert on and on[0].request.action == "light.turn_on"
    assert "ook nu er niemand thuis is" in _why(hm, on[0])
    await hm.stop()


async def test_with_nobody_home_they_go_out_late_a_little_different_each_evening(
    tmp_path: Path,
) -> None:
    """Nobody home: out at the end of the evening (23:30 until the house teaches otherwise), up
    to a quarter of an hour earlier or later – not every evening at the same minute, as a lamp
    on a timer would. The logbook says why."""
    hm, clock, ha = await _garden(tmp_path, HouseMode.AWAY)
    assert hm.skills is not None and hm.world is not None
    await _until(hm, clock, AFTERNOON + timedelta(hours=1), step=30)
    assert ha.states[LAMP].state == "on"
    await _until(hm, clock, AFTERNOON + timedelta(hours=10), step=60)  # 01:00
    assert ha.states[LAMP].state == "off"
    out = [d for d in _evening_decisions(hm) if d.request.action == "light.turn_off"]
    assert len(out) == 1
    off = out[0].at.astimezone(hm.world.snapshot(clock.now()).local.tzinfo)
    assert abs(off - off.replace(hour=23, minute=30)) <= timedelta(minutes=16)
    assert f"er niemand thuis is en het {off:%H:%M} is" in _why(hm, out[0])
    offs = {off.time()}
    for day in (1, 2):
        while clock.now() < AFTERNOON + timedelta(days=day, hours=6):  # 22:00
            await clock.advance(3600)
        offs.add(hm.skills.evening_off(hm.world.snapshot(clock.now()), "garden")[0].time())
    assert len(offs) > 1
    await hm.stop()


async def test_at_home_they_go_out_when_the_house_goes_to_sleep(tmp_path: Path) -> None:
    """Someone home: on at dusk, and out the moment the house goes to sleep – before the
    latest time."""
    hm, clock, ha = await _garden(tmp_path, HouseMode.HOME)
    assert hm.world is not None
    await _until(hm, clock, AFTERNOON + timedelta(hours=1), step=30)
    assert ha.states[LAMP].state == "on"
    await _until(hm, clock, AFTERNOON + timedelta(hours=6, minutes=30), step=300)  # 22:30
    assert ha.states[LAMP].state == "on"
    hm.world.modes.set_manual(HouseMode.SLEEP, clock.now())
    await _until(hm, clock, clock.now() + timedelta(minutes=1), step=30)
    assert ha.states[LAMP].state == "off"
    out = [d for d in _evening_decisions(hm) if d.request.action == "light.turn_off"]
    assert "het huis gaat slapen" in _why(hm, out[-1])
    await hm.stop()


async def test_an_automation_that_does_this_teaches_its_light_and_time_and_can_be_taken_over(
    tmp_path: Path,
) -> None:
    """An automation with the garden lamp on at sunset and out at 23:45 taught nothing: there
    was no skill for it, so HouseMind left the lamp to it forever. Now it teaches the evening
    light (its brightness and its time) and, set to Autonomous, HouseMind switches it off."""
    clock = SimulatedClock(AFTERNOON)
    model = load_demo_house("family_home", clock, "nl").ha.model
    state = State("automation.garden_light", "on", {"friendly_name": "Garden light"}, AFTERNOON)
    automation = parse_automation(state, GARDEN_LIGHT)
    catalog, _ = load_catalog([ROOT / "knowledge" / "catalog"])
    params = load_skill_params([ROOT / "knowledge" / "skills"])
    reading = read_automation(automation, model, catalog, {}, params, {"garden": "outside"})
    assert reading.whole and (EVENING, "garden") in reading.learned
    taught = reading.values[(EVENING, "garden")]
    assert taught["brightness_pct"] == 100 and taught["off_minutes"] == 11 * 60 + 45

    automations = {"automation.garden_light": GARDEN_LIGHT}
    hm, clock, ha = await _house(tmp_path, start=AFTERNOON, located=True, automations=automations)
    await _expert(hm)
    await hm._sync_automations()
    row = await _row(hm, EVENING, "garden")
    assert "‘Garden light in the evening’" in row["steer"]["text"]
    assert "uiterlijk om 23:45" in row["when"]
    await hm.set_admin_step(f"{EVENING}@garden", "autonomous", "demo-user-1")
    assert ha.states["automation.garden_light"].state == "off"  # the step is the yes (E-42)
    await hm.stop()


async def test_the_card_says_it_in_plain_words(tmp_path: Path) -> None:
    """'Klaar voor autonomie?' says what HouseMind does, in words a resident uses: on when it
    gets dark, also when nobody is home, and out when the house goes to sleep."""
    hm, clock, _ha = await _garden(tmp_path, HouseMode.HOME, step=Step.OBSERVE)
    await _expert(hm)
    await _until(hm, clock, AFTERNOON + timedelta(hours=1), step=30)
    row = await _row(hm, EVENING, "garden")
    assert row["what"] == "buitenlicht in de avond in de tuin"
    assert row["when"].startswith("Ik zou de lamp in de tuin aandoen als het donker wordt, ook")
    assert "uitdoen als het huis gaat slapen, uiterlijk om 23:30" in row["when"]
    assert "elke avond tot 15 min eerder of later" in row["whenDetail"]
    await hm.stop()


async def test_a_camera_light_is_not_taken_for_the_evening_light(tmp_path: Path) -> None:
    """Someone on camera just as it gets dark: the camera's lamp goes out again after a few
    minutes by itself. HouseMind waits for that and then puts the evening light on – it does
    not take the camera's lamp for the evening already lit, which left the garden dark all
    evening."""
    hm, clock, ha = await _garden(tmp_path, HouseMode.AWAY)
    assert hm.world is not None and hm.learning is not None
    hm.learning.steps[("light_on_person_outside", "garden")] = Step.AUTONOMOUS
    while not hm.world.snapshot(clock.now() + timedelta(seconds=30)).outside_dark:
        await _until(hm, clock, clock.now() + timedelta(seconds=30), step=30)
    # A person on camera: what brain/security.py does (_outside_light).
    request = ActionRequest(
        Layer.REFLEX,
        LAMP,
        "light.turn_on",
        {},
        "camera_person",
        skill="light_on_person_outside",
        area_id="garden",
        created_at=clock.now(),
    )
    decision = await hm.consider(request)
    assert decision is not None and decision.executed
    hm.security.lit[LAMP] = clock.now()
    await _until(hm, clock, clock.now() + timedelta(minutes=2), step=30)
    assert ha.states[LAMP].state == "on" and not _evening_decisions(hm)
    await _until(hm, clock, clock.now() + timedelta(minutes=6), step=30)
    assert LAMP not in hm.security.lit  # the camera's minutes are over
    assert ha.states[LAMP].state == "on"
    assert [d.request.action for d in _evening_decisions(hm)] == ["light.turn_on"]
    await hm.stop()


async def test_nobody_home_and_nobody_doing_it_is_no_not_like_that(tmp_path: Path) -> None:
    """Only watching, with nobody home: nobody puts the lamp on, because nobody can. That is no
    'not like that' – otherwise the evening light would learn it is unwanted on every evening
    away."""
    hm, clock, ha = await _garden(tmp_path, HouseMode.AWAY, step=Step.OBSERVE)
    assert hm.learning is not None
    await _until(hm, clock, AFTERNOON + timedelta(hours=2), step=30)
    assert ha.states[LAMP].state == "off"
    assert hm.learning.total(EVENING, "garden", Result.MISS, clock.now()) == 0
    await hm.stop()


async def test_the_house_teaches_when_the_evening_light_ends(tmp_path: Path) -> None:
    """Without an automation: someone puts the garden lamp out at about ten past eleven, four
    evenings. Then that is the latest time, and the card says it is learned."""
    hm, clock, ha = await _garden(tmp_path, HouseMode.HOME, step=Step.OBSERVE)
    await _expert(hm)
    assert hm.world is not None
    for day in range(4):
        await _until(hm, clock, AFTERNOON + timedelta(days=day, hours=7), step=600)  # 23:00
        hm.world.modes.set_manual(HouseMode.HOME, clock.now())  # still up
        await ha.simulate(LAMP, "on", by=Source.PERSON)
        await _until(hm, clock, AFTERNOON + timedelta(days=day, hours=7, minutes=10), step=60)
        await ha.simulate(LAMP, "off", by=Source.PERSON)
    row = await _row(hm, EVENING, "garden")
    assert "uiterlijk om 23:10" in row["when"]
    assert "uiterlijk om 23:10, geleerd" in row["whenDetail"]
    await hm.stop()
