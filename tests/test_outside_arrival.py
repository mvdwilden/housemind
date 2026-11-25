"""The lamps at the entrance when someone comes home in the dark (E-447). Found while checking
the administrator's wish that HouseMind takes over on Autonomous (01-11-2026): an automation
"driveway light on when arriving after sunset, off when leaving" taught HouseMind nothing –
there was no skill for it – so the lamp stayed "only watching" forever. Each test fails
without the change; what it checks is its docstring."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.brain.automations import read_automation
from housemind.brain.learning import Result
from housemind.brain.proposals import Answer
from housemind.brain.skills import ARRIVAL, EVENING, SkillEngine, load_skill_params
from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Device, Entity, Source, State
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
DRIVE = "light.driveway"
GARDEN = "light.garden"
SEEN = "binary_sensor.driveway_person"
# 1 November in Amsterdam (CET): 23:30 UTC is half past midnight there – dark, and the evening
# light outside is long out.
NIGHT = datetime(2026, 11, 1, 23, 30, tzinfo=UTC)
# The automation in the real house, as Home Assistant gives it: a zone trigger for coming
# home and one for leaving, and a choose with the sun as condition.
ON_ARRIVAL: dict[str, Any] = {
    "id": "1724960401487",
    "alias": "Lichten oprit aan bij aankomst",
    "description": "",
    "triggers": [
        {
            "entity_id": "person.resident_1",
            "zone": "zone.home",
            "event": "enter",
            "id": "Aankomst",
            "trigger": "zone",
        },
        {
            "entity_id": "person.resident_1",
            "zone": "zone.home",
            "event": "leave",
            "id": "Vertrek",
            "trigger": "zone",
        },
    ],
    "conditions": [],
    "actions": [
        {
            "choose": [
                {
                    "conditions": [
                        {"condition": "trigger", "id": ["Aankomst"]},
                        {"condition": "sun", "after": "sunset", "before": "sunrise"},
                    ],
                    "sequence": [
                        {
                            "action": "light.turn_on",
                            "metadata": {},
                            "data": {"brightness_pct": 100},
                            "target": {"entity_id": DRIVE},
                        }
                    ],
                },
                {
                    "conditions": [{"condition": "trigger", "id": ["Vertrek"]}],
                    "sequence": [
                        {
                            "action": "light.turn_off",
                            "metadata": {},
                            "data": {},
                            "target": {"entity_id": DRIVE},
                        }
                    ],
                },
            ]
        }
    ],
    "mode": "single",
}


def _entity(ha: Any, entity_id: str, name: str, area: str, state: str, **extra: Any) -> None:
    capabilities = {"supported_color_modes": extra.pop("modes")} if "modes" in extra else {}
    ha.model.devices[entity_id] = Device(entity_id, name, area, "demo")
    ha.model.entities[entity_id] = Entity(
        entity_id,
        f"demo-{entity_id}",
        name,
        device_id=entity_id,
        area_id=area,
        device_class=extra.get("device_class"),
        platform="demo",
        capabilities=capabilities,
        original_name=name,
    )
    attrs = {"friendly_name": name, **extra, **capabilities}
    ha.states[entity_id] = State(entity_id, state, attrs, NIGHT)


def _driveway(ha: Any) -> None:
    """The family house with a driveway at the front: a lamp, and a sensor that sees people."""
    ha.model.areas["driveway"] = Area("driveway", "Oprit", "ground")
    _entity(ha, DRIVE, "Oprit lamp", "driveway", "off", modes=["color_temp"])
    _entity(ha, SEEN, "Oprit persoon", "driveway", "off", device_class="occupancy")


async def _away(
    tmp_path: Path,
    start: datetime = NIGHT,
    step: Step | None = Step.AUTONOMOUS,
    automations: dict[str, dict[str, Any]] | None = None,
    evening: Step | None = None,
) -> tuple[HouseMind, SimulatedClock, Any]:
    """Everyone out; the house went to "away". ``evening``: the step of the evening light at
    the driveway (E-446)."""
    hm, clock, ha = await _house(
        tmp_path, start=start, located=True, prepare=_driveway, automations=automations
    )
    assert hm.world is not None and hm.learning is not None and hm.gate is not None
    hm.activated, hm.gate.state.inactive = True, False  # past the introduction (E-275)
    if step is not None:
        hm.learning.steps[(ARRIVAL, "driveway")] = step
    if evening is not None:
        hm.learning.steps[(EVENING, "driveway")] = evening
    for i in (1, 2, 3):
        await ha.simulate(f"person.resident_{i}", "not_home")
    await _until(hm, clock, clock.now() + timedelta(minutes=11), step=60)
    assert hm.world.modes.mode is HouseMode.AWAY
    return hm, clock, ha


def _arrival(hm: HouseMind, area: str = "driveway") -> list[Decision]:
    return [
        r.decision
        for r in hm.recent.values()
        if r.decision.request.skill == ARRIVAL and r.decision.request.area_id == area
    ]


def _why(hm: HouseMind, decision: Decision) -> str:
    return " ".join(hm.explain(decision, 3, "nl"))


async def test_coming_home_in_the_dark_puts_the_entrance_light_on(tmp_path: Path) -> None:
    """Half past midnight, someone comes home: the driveway lamp goes on at once, bright – not
    the garden at the back. The logbook says why."""
    hm, _clock, ha = await _away(tmp_path)
    assert ha.states[DRIVE].state == "off"
    await ha.simulate("person.resident_1", "home")
    assert ha.states[DRIVE].state == "on"
    assert ha.states[GARDEN].state == "off" and not _arrival(hm, "garden")
    on = _arrival(hm)
    assert [d.request.action for d in on] == ["light.turn_on"]
    assert on[0].request.data.get("brightness_pct") == 100
    assert "iemand in het donker thuiskwam" in _why(hm, on[0])
    await hm.stop()


async def test_it_goes_out_after_five_minutes_and_later_while_someone_is_outside(
    tmp_path: Path,
) -> None:
    """Five minutes after coming home the lamp goes out – but not while someone is still seen
    on the driveway: then when they are gone. The logbook says why."""
    hm, clock, ha = await _away(tmp_path)
    await ha.simulate("person.resident_1", "home")
    home = clock.now()
    await _until(hm, clock, home + timedelta(minutes=4), step=30)
    assert ha.states[DRIVE].state == "on"
    await ha.simulate(SEEN, "on")  # unloading the car
    await _until(hm, clock, home + timedelta(minutes=8), step=30)
    assert ha.states[DRIVE].state == "on"
    await ha.simulate(SEEN, "off")
    await _until(hm, clock, home + timedelta(minutes=10), step=30)
    assert ha.states[DRIVE].state == "off"
    off = [d for d in _arrival(hm) if d.request.action == "light.turn_off"]
    assert len(off) == 1
    assert "iemand 5 min geleden thuiskwam en er niemand meer buiten is" in _why(hm, off[0])
    await hm.stop()


async def test_without_anyone_outside_it_is_out_on_its_minute(tmp_path: Path) -> None:
    """Nobody outside: out five minutes after coming home, on that minute – not only at the next
    round of the house. A sensor that keeps saying someone is there holds it half an hour at
    most."""
    hm, clock, ha = await _away(tmp_path)
    await ha.simulate("person.resident_1", "home")
    looking_again = hm._recheck_task
    assert looking_again is not None  # HouseMind will look again by itself
    await clock.advance(5 * 60 + 1)  # no round of the house in between
    await asyncio.wait_for(looking_again, 5)
    assert ha.states[DRIVE].state == "off"
    await ha.simulate("person.resident_1", "not_home")
    await _until(hm, clock, clock.now() + timedelta(minutes=1), step=30)
    await ha.simulate("person.resident_1", "home")  # home again, a sensor stuck on
    await ha.simulate(SEEN, "on")
    again = clock.now()
    assert ha.states[DRIVE].state == "on"
    await _until(hm, clock, again + timedelta(minutes=29), step=60)
    assert ha.states[DRIVE].state == "on"
    await _until(hm, clock, again + timedelta(minutes=31), step=60)
    assert ha.states[DRIVE].state == "off"
    await hm.stop()


async def test_in_daylight_nothing_happens(tmp_path: Path) -> None:
    """Coming home in the afternoon: the lamps outside stay as they are."""
    hm, clock, ha = await _away(tmp_path, start=datetime(2026, 11, 1, 11, 0, tzinfo=UTC))
    await ha.simulate("person.resident_1", "home")
    await _until(hm, clock, clock.now() + timedelta(minutes=2), step=30)
    assert ha.states[DRIVE].state == "off" and not _arrival(hm)
    await hm.stop()


async def test_the_evening_light_already_on_stays_and_stays_on_when_everyone_leaves(
    tmp_path: Path,
) -> None:
    """In the evening the driveway is lit already (the evening light, E-446). Coming home
    changes nothing: no lamp of its own, and it does not put the evening light out after five
    minutes. Everyone leaving again keeps the evening light on, so the house looks lived in."""
    evening = datetime(2026, 11, 1, 17, 30, tzinfo=UTC)  # 18:30 in Amsterdam
    hm, clock, ha = await _away(tmp_path, start=evening, evening=Step.AUTONOMOUS)
    assert ha.states[DRIVE].state == "on"  # the evening light
    await ha.simulate("person.resident_1", "home")
    await _until(hm, clock, clock.now() + timedelta(minutes=10), step=30)
    assert ha.states[DRIVE].state == "on" and not _arrival(hm)
    await ha.simulate("person.resident_1", "not_home")
    await _until(hm, clock, clock.now() + timedelta(minutes=12), step=60)
    assert hm.world is not None and hm.world.modes.mode is HouseMode.AWAY
    assert ha.states[DRIVE].state == "on" and not _arrival(hm)
    await hm.stop()


async def test_the_evening_light_does_not_put_out_the_lamp_of_coming_home(tmp_path: Path) -> None:
    """The others are up; an automation put the driveway lamp out at half past nine. At 23:27
    someone comes home, so the lamp goes on again. At the end of the evening (23:30) the evening
    light leaves that lamp alone: it goes out on the minutes of coming home."""
    nine = datetime(2026, 11, 1, 20, 0, tzinfo=UTC)  # 21:00 in Amsterdam
    hm, clock, ha = await _house(tmp_path, start=nine, located=True, prepare=_driveway)
    assert hm.world is not None and hm.learning is not None and hm.gate is not None
    hm.activated, hm.gate.state.inactive = True, False
    hm.learning.steps[(ARRIVAL, "driveway")] = Step.AUTONOMOUS
    hm.learning.steps[(EVENING, "driveway")] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.living_presence", "on")  # the others are up
    await ha.simulate("person.resident_1", "not_home")  # out for the evening
    hm.world.modes.set_manual(HouseMode.HOME, clock.now())
    await _until(hm, clock, clock.now() + timedelta(minutes=1), step=30)
    assert ha.states[DRIVE].state == "on"  # the evening light
    await _until(hm, clock, nine + timedelta(minutes=30), step=60)
    await ha.simulate(DRIVE, "off", by=Source.AUTOMATION)
    await _until(hm, clock, nine + timedelta(hours=2, minutes=20), step=300)
    await _until(hm, clock, nine + timedelta(hours=2, minutes=27), step=60)
    await ha.simulate("person.resident_1", "home")
    home = clock.now()
    assert ha.states[DRIVE].state == "on"
    await _until(hm, clock, home + timedelta(minutes=4), step=30)  # past the evening's end
    assert ha.states[DRIVE].state == "on"
    await _until(hm, clock, home + timedelta(minutes=6), step=30)
    assert ha.states[DRIVE].state == "off"
    assert [d.request.action for d in _arrival(hm)] == ["light.turn_on", "light.turn_off"]
    evening_off = [
        r.decision
        for r in hm.recent.values()
        if r.decision.request.skill == EVENING and r.decision.request.action == "light.turn_off"
    ]
    assert not evening_off
    await hm.stop()


async def test_a_camera_light_is_left_to_the_camera(tmp_path: Path) -> None:
    """A camera just lit the driveway for a person in the dark (E-183). Coming home then takes
    nothing over: that lamp stays the camera's, and coming home puts nothing out."""
    hm, clock, ha = await _away(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_person_outside", "driveway")] = Step.AUTONOMOUS
    request = ActionRequest(
        Layer.REFLEX,
        DRIVE,
        "light.turn_on",
        {},
        "camera_person",
        skill="light_on_person_outside",
        area_id="driveway",
        created_at=clock.now(),
    )
    decision = await hm.consider(request)
    assert decision is not None and decision.executed
    hm.security.lit[DRIVE] = clock.now()
    await ha.simulate("person.resident_1", "home")
    assert not _arrival(hm)
    assert hm.skills is not None and DRIVE not in hm.skills._arrival_lit
    await hm.stop()


async def test_everyone_leaving_puts_the_lamp_of_coming_home_out_at_once(tmp_path: Path) -> None:
    """Home and out again within a minute: the lamp HouseMind lit for coming home goes out
    when everyone has left, not five minutes later."""
    hm, clock, ha = await _away(tmp_path)
    await ha.simulate("person.resident_1", "home")
    assert ha.states[DRIVE].state == "on"
    await _until(hm, clock, clock.now() + timedelta(seconds=40), step=20)
    await ha.simulate("person.resident_1", "not_home")
    await _until(hm, clock, clock.now() + timedelta(seconds=40), step=20)
    assert ha.states[DRIVE].state == "off"
    off = [d for d in _arrival(hm) if d.request.action == "light.turn_off"]
    assert off and "iedereen weg is" in _why(hm, off[0])
    await hm.stop()


async def test_too_short_is_longer_next_time_and_the_card_says_it_is_learned(
    tmp_path: Path,
) -> None:
    """Out after five minutes, and someone switches it on again at once: too short. The next
    time – early in the morning, after the four hours a person's own switching wins (B-15) – it
    stays on half as long again, and the card says the time is learned."""
    hm, clock, ha = await _away(tmp_path)
    await _expert(hm)
    await ha.simulate("person.resident_1", "home")
    home = clock.now()
    await _until(hm, clock, home + timedelta(minutes=5, seconds=30), step=30)
    assert ha.states[DRIVE].state == "off"
    await ha.simulate(DRIVE, "on", by=Source.PERSON)
    await _until(hm, clock, clock.now() + timedelta(minutes=1), step=30)
    await ha.simulate(DRIVE, "off", by=Source.PERSON)
    row = await _row(hm, ARRIVAL, "driveway")
    assert "7,5 min (geleerd)" in row["whenDetail"]
    await ha.simulate("person.resident_1", "not_home")
    await _until(hm, clock, NIGHT + timedelta(hours=5), step=600)  # 05:30, still dark
    await ha.simulate("person.resident_1", "home")
    again = clock.now()
    assert ha.states[DRIVE].state == "on"
    await _until(hm, clock, again + timedelta(minutes=7), step=30)
    assert ha.states[DRIVE].state == "on"
    await _until(hm, clock, again + timedelta(minutes=8), step=30)
    assert ha.states[DRIVE].state == "off"
    await hm.stop()


async def test_the_automation_of_the_real_house_teaches_it_and_can_be_taken_over(
    tmp_path: Path,
) -> None:
    """'Lichten oprit aan bij aankomst' – on when coming home after sunset, off when leaving –
    taught nothing, so HouseMind left the driveway to it forever. Now it teaches the light at
    the driveway (100%), it is a whole HouseMind can do, and on Autonomous HouseMind switches
    it off and puts the driveway light on itself."""
    clock = SimulatedClock(NIGHT)
    house = load_demo_house("family_home", clock, "nl")
    _driveway(house.ha)
    model = house.ha.model
    state = State("automation.oprit", "on", {"friendly_name": ON_ARRIVAL["alias"]}, NIGHT)
    automation = parse_automation(state, ON_ARRIVAL)
    catalog, _ = load_catalog([ROOT / "knowledge" / "catalog"])
    params = load_skill_params([ROOT / "knowledge" / "skills"])
    roles = {"driveway": "outside", "garden": "outside"}
    reading = read_automation(automation, model, catalog, {}, params, roles)
    assert reading.learned == ((ARRIVAL, "driveway"),) and reading.whole
    assert reading.values[(ARRIVAL, "driveway")]["brightness_pct"] == 100

    automations = {"automation.oprit": ON_ARRIVAL}
    hm, clock, ha = await _away(tmp_path, step=None, automations=automations)
    await _expert(hm)
    await hm._sync_automations()
    row = await _row(hm, ARRIVAL, "driveway")
    assert "‘Lichten oprit aan bij aankomst’" in row["steer"]["text"]
    await hm.set_admin_step(f"{ARRIVAL}@driveway", "autonomous", "demo-user-1")
    assert ha.states["automation.oprit"].state == "off"  # the step is the yes (E-42)
    await ha.simulate("person.resident_1", "home")
    assert ha.states[DRIVE].state == "on"  # now HouseMind's own
    assert _arrival(hm)[0].request.data.get("brightness_pct") == 100
    await hm.stop()


async def test_after_a_yes_it_goes_out_without_asking_again(tmp_path: Path) -> None:
    """On Propose HouseMind asks when someone comes home ("Zal ik de lamp op de oprit
    aandoen?"). After "Doe het" it goes out five minutes later by itself – found in the demo
    house: it asked a second time whether it could put the lamp out."""
    hm, clock, ha = await _away(tmp_path, step=Step.PROPOSE)
    await ha.simulate("person.resident_1", "home")
    assert ha.states[DRIVE].state == "off"
    asked = [p for p in await hm.open_proposals() if p.entity_id == DRIVE]
    assert len(asked) == 1 and asked[0].action == "light.turn_on"
    assert (await hm.answer(asked[0].id, Answer.DO, "demo-user-1"))["ok"]
    assert ha.states[DRIVE].state == "on"
    await _until(hm, clock, clock.now() + timedelta(minutes=6), step=30)
    assert ha.states[DRIVE].state == "off"
    assert not [p for p in await hm.open_proposals() if p.entity_id == DRIVE]
    await hm.stop()


async def test_the_card_says_it_in_plain_words(tmp_path: Path) -> None:
    """'Klaar voor autonomie?' says what HouseMind would do at the driveway, in words a
    resident uses, and on Expert where and how long."""
    hm, _clock, ha = await _away(tmp_path, step=Step.OBSERVE)
    await _expert(hm)
    await ha.simulate("person.resident_1", "home")
    row = await _row(hm, ARRIVAL, "driveway")
    assert row["what"] == "buitenlicht bij thuiskomen op de oprit"
    assert row["when"].startswith(
        "Ik zou de lamp op de oprit aandoen als iemand in het donker thuiskomt, en 5 minuten"
        " later weer uitdoen, of later als er dan nog iemand buiten is"
    )
    assert "aan voor 5 min, langer als er buiten iemand is, hooguit 30 min" in row["whenDetail"]
    assert "alleen bij de ingang" in row["whenDetail"]
    await hm.stop()


async def test_nobody_switching_it_on_is_no_not_like_that(tmp_path: Path) -> None:
    """Only watching: someone comes home and nobody puts the driveway lamp on – the switch is
    inside. That is no 'not like that', or the skill would never get anywhere."""
    hm, clock, ha = await _away(tmp_path, step=Step.OBSERVE)
    assert hm.learning is not None
    await ha.simulate("person.resident_1", "home")
    await _until(hm, clock, clock.now() + timedelta(minutes=5), step=30)
    assert ha.states[DRIVE].state == "off"
    assert hm.learning.total(ARRIVAL, "driveway", Result.MISS, clock.now()) == 0
    _score, situations, _days = hm.learning.score(ARRIVAL, "driveway", clock.now())
    assert situations >= 1
    await hm.stop()


def test_the_entrance_is_known_by_its_name_in_every_language() -> None:
    """A driveway, a front door, a carport – in Dutch, English, German and French – is the
    entrance; a garden or terrace at the back is not."""
    clock = SimulatedClock(NIGHT)
    model = load_demo_house("family_home", clock, "nl").ha.model
    words = ["oprit", "voordeur", "carport", "driveway", "front door", "einfahrt", "allée"]
    names = {
        "a": "Oprit",
        "b": "Voordeur pad",
        "c": "Front door",
        "d": "Einfahrt",
        "e": "Allée",
        "f": "Achtertuin",
        "g": "Terras",
        "h": "Garden",
    }
    for key, name in names.items():
        model.areas[key] = Area(key, name)
    catalog, _ = load_catalog([ROOT / "knowledge" / "catalog"])
    engine = SkillEngine(catalog, model, {}, {}, entrance_words=tuple(words))
    assert {k for k in names if engine.arrival_place(k)} == {"a", "b", "c", "d", "e"}
