"""Buttons and switches are the residents' own hand (E-448, design 58; wish from the real
house, 01-11-2026: on Autonomous HouseMind takes over – but a wall switch or a button in the
app that only passes on "lamp on" kept the lamp 'alleen kijken' forever). An automation that
only starts when a person presses or flips something, and only switches or dims lamps and
switches, is no other system: its lamps are HouseMind's to work, HouseMind never asks to switch
it off, and what it does counts as the person who pressed (people win). Its switch HouseMind
never flips itself. Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestServer

from housemind.brain.automations import Status
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, Source, State, StateChange
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.ha.websocket import WebSocketHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer, Outcome
from housemind.server import views
from tests.ha_ws_server import TOKEN, FakeWsServer
from tests.test_automations import EVENING, ROOT
from tests.test_history import _evenings

LAMP = "light.kitchen"
WALL = "switch.kitchen_wall"
WALL_SWITCH = "automation.kitchen_wall_switch"
WALL_CONFIG: dict[str, Any] = {  # as 'Keuken - Smart Switch Toggle' in the real house
    "id": "2001",
    "alias": "Keuken - schakelaar",
    "triggers": [
        {
            "trigger": "device",
            "domain": "switch",
            "type": "turned_on",
            "entity_id": f"reg-{WALL}",
            "id": "aan",
        },
        {
            "trigger": "device",
            "domain": "switch",
            "type": "turned_off",
            "entity_id": f"reg-{WALL}",
            "id": "uit",
        },
    ],
    "actions": [
        {
            "choose": [
                {
                    "conditions": [{"condition": "trigger", "id": ["aan"]}],
                    "sequence": [{"action": "light.turn_on", "target": {"entity_id": LAMP}}],
                },
                {
                    "conditions": [{"condition": "trigger", "id": ["uit"]}],
                    "sequence": [{"action": "light.turn_off", "target": {"entity_id": LAMP}}],
                },
            ]
        }
    ],
    "mode": "single",
}
APP_BUTTON = "automation.hall_app_button"
APP_CONFIG: dict[str, Any] = {  # as 'Action: Oprit verlichting'
    "id": "2002",
    "alias": "Action: Hal",
    "triggers": [
        {"trigger": "event", "event_type": "ios.action_fired", "event_data": {"actionID": "X"}}
    ],
    "actions": [{"action": "light.toggle", "target": {"entity_id": "light.hall"}}],
}
REMOTE = "automation.bedroom_remote"
REMOTE_CONFIG: dict[str, Any] = {  # as 'Slaapkamer … - Afstandbediening'
    "id": "2003",
    "alias": "Slaapkamer - afstandsbediening",
    "triggers": [
        {
            "trigger": "device",
            "domain": "mqtt",
            "device_id": "remote-1",
            "type": "action",
            "subtype": "on",
            "id": "on",
        },
        {
            "trigger": "device",
            "domain": "mqtt",
            "device_id": "remote-1",
            "type": "action",
            "subtype": "brightness_move_up",
            "id": "up",
        },
    ],
    "actions": [
        {
            "choose": [
                {
                    "conditions": [{"condition": "trigger", "id": ["on"]}],
                    "sequence": [
                        {"action": "light.turn_on", "target": {"entity_id": "light.bedroom"}}
                    ],
                },
                {
                    "conditions": [{"condition": "trigger", "id": ["up"]}],
                    "sequence": [
                        {
                            "device_id": "dev-bedroom",
                            "domain": "light",
                            "entity_id": "reg-light.bedroom",
                            "type": "brightness_increase",
                        }
                    ],
                },
            ]
        }
    ],
}
HELPER = "input_boolean.all_off"
ALL_OFF = "automation.all_lamps_off"
ALL_OFF_CONFIG: dict[str, Any] = {  # as 'Alle lichten in huis uit': a helper on the dashboard
    "id": "2004",
    "alias": "Alle lampen uit",
    "triggers": [{"trigger": "state", "entity_id": [HELPER], "to": "on"}],
    "actions": [
        {"action": "light.turn_off", "target": {"entity_id": "all"}},
        {"action": "input_boolean.turn_off", "target": {"entity_id": HELPER}},
    ],
}


async def _start(
    tmp_path: Path, automations: dict[str, dict[str, Any]]
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    """The family house in the dark, with a wall switch in the kitchen and a helper button."""
    clock = SimulatedClock(EVENING)
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    for entity_id, name in ((WALL, "Keuken schakelaar"), (HELPER, "Alles uit")):
        area = "kitchen" if entity_id == WALL else None
        await ha.add_entity(
            Entity(entity_id, f"reg-{entity_id}", name, area_id=area),
            State(entity_id, "off", {"friendly_name": name}, clock.now()),
        )
    for entity_id, config in automations.items():
        await ha.add_automation(entity_id, str(config["alias"]), config)
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    if hm._history_task is not None:
        await hm._history_task
    return hm, clock, ha


def _request(
    clock: SimulatedClock, entity_id: str = LAMP, action: str = "light.turn_on"
) -> ActionRequest:
    return ActionRequest(
        Layer.REFLEX,
        entity_id,
        action,
        {},
        reason="skill:light_on_entry",
        skill="light_on_entry",
        area_id="kitchen",
        created_at=clock.now(),
        facts={"context": "workday|19:30"},
    )


def _sources(ha: FakeHomeAssistant) -> list[StateChange]:
    seen: list[StateChange] = []

    async def listener(change: StateChange) -> None:
        seen.append(change)

    ha.on_state_change(listener)
    return seen


async def test_a_wall_switch_keeps_no_lamp_watch_only_and_counts_as_a_hand(tmp_path: Path) -> None:
    """With 'Keuken - schakelaar' on, the kitchen lamp is HouseMind's to work: on Autonomous a
    reflex switches it, and "Waarom niet?" does not name the automation. Flipping the wall
    switch still works the lamp – and counts as the hand on the wall: HouseMind keeps off the
    lamp for a while (people win)."""
    hm, clock, ha = await _start(tmp_path, {WALL_SWITCH: WALL_CONFIG})
    assert hm.gate is not None and hm.learning is not None
    assert LAMP not in hm.gate.state.other_system
    assert hm.automations.readings[WALL_SWITCH].hand is Source.PHYSICAL
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    decision = await hm.consider(_request(clock))
    assert decision is not None and decision.executed
    assert "Keuken - schakelaar" not in hm.why_not(LAMP, "nl")
    seen = _sources(ha)
    await ha.simulate(WALL, "on", by=Source.PHYSICAL)  # someone flips the switch on the wall
    await ha.simulate(WALL, "off", by=Source.PHYSICAL)
    assert ha.states[LAMP].state == "off"  # the switch still works the lamp
    lamp = [c for c in seen if c.entity_id == LAMP]
    assert lamp and all(c.source is Source.PHYSICAL for c in lamp)
    assert LAMP in hm.gate.state.people_win_until
    hm._last_request.clear()
    again = await hm.consider(_request(clock))
    assert again is not None and again.outcome is Outcome.DENIED and again.reason == "people_win"
    await hm.stop()


async def test_the_switch_in_the_app_is_a_person_too(tmp_path: Path) -> None:
    """The wall switch flipped in the Home Assistant app: the lamp it works counts as that
    person."""
    hm, _clock, ha = await _start(tmp_path, {WALL_SWITCH: WALL_CONFIG})
    seen = _sources(ha)
    await ha.simulate(WALL, "on", by=Source.PERSON)
    assert [c.source for c in seen if c.entity_id == LAMP] == [Source.PERSON]
    await hm.stop()


async def test_an_action_in_the_phone_app_is_a_person(tmp_path: Path) -> None:
    """'Action: Hal' (an action in the phone app that toggles the hall lamp): the hall lamp is
    not only watched, and what the button does counts as a person."""
    hm, _clock, ha = await _start(tmp_path, {APP_BUTTON: APP_CONFIG})
    assert hm.gate is not None
    assert "light.hall" not in hm.gate.state.other_system
    assert hm.automations.readings[APP_BUTTON].hand is Source.PERSON
    seen = _sources(ha)
    await ha.fire_event("ios.action_fired")
    assert ha.states["light.hall"].state == "on"
    assert [c.source for c in seen if c.entity_id == "light.hall"] == [Source.PERSON]
    assert "light.hall" in hm.gate.state.people_win_until
    await hm.stop()


async def test_a_remote_that_also_dims_and_a_helper_that_puts_itself_back(tmp_path: Path) -> None:
    """A remote with on and brighter, and 'Alle lampen uit' (a helper on the dashboard that the
    automation puts back off): both are a hand, so no lamp is only watched – also not with
    "all lamps off". Pressing them still works the lamps, as the hand that pressed."""
    hm, _clock, ha = await _start(tmp_path, {REMOTE: REMOTE_CONFIG, ALL_OFF: ALL_OFF_CONFIG})
    assert hm.gate is not None
    assert hm.automations.readings[REMOTE].hand is Source.PHYSICAL
    assert hm.automations.readings[ALL_OFF].hand is Source.PHYSICAL
    assert not {e for e in hm.gate.state.other_system if e.startswith("light.")}
    seen = _sources(ha)
    await ha.press("on")
    assert ha.states["light.bedroom"].state == "on"
    await ha.simulate(HELPER, "on", by=Source.PERSON)
    assert ha.states["light.bedroom"].state == "off" and ha.states[HELPER].state == "off"
    bedroom = [c.source for c in seen if c.entity_id == "light.bedroom"]
    assert bedroom == [Source.PHYSICAL, Source.PERSON]
    await hm.stop()


async def test_not_a_hand_stays_as_it_was(tmp_path: Path) -> None:
    """A button that also sends a message, a switch that also listens to the clock, and a sun
    automation next to a switch on the same lamp: those are no hand. Their lamps stay only
    watched, "Waarom niet?" names the other one, and what a remote-only automation with a
    message does counts as an automation – not as a wall switch."""
    message = {
        **APP_CONFIG,
        "id": "3001",
        "alias": "Knop met bericht",
        "triggers": [
            {
                "trigger": "device",
                "domain": "mqtt",
                "device_id": "r",
                "type": "action",
                "subtype": "on",
            }
        ],
        "actions": [
            *APP_CONFIG["actions"],
            {"action": "notify.notify", "data": {"message": "Hal aan"}},
        ],
    }
    clocked = {
        **WALL_CONFIG,
        "id": "3002",
        "alias": "Schakelaar of klok",
        "triggers": [*WALL_CONFIG["triggers"], {"trigger": "time", "at": "23:00:00"}],
    }
    sun = {
        "id": "3003",
        "alias": "Keukenlicht bij zonsondergang",
        "triggers": [{"trigger": "sun", "event": "sunset"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": LAMP}}],
    }
    hm, _clock, ha = await _start(
        tmp_path,
        {"automation.message": message, "automation.clocked": clocked, "automation.sun": sun},
    )
    assert hm.gate is not None
    assert all(r.hand is None for r in hm.automations.readings.values())
    assert {LAMP, "light.hall"} <= hm.gate.state.other_system
    hm2, _c, _h = await _start(tmp_path / "two", {WALL_SWITCH: WALL_CONFIG, "automation.sun": sun})
    assert hm2.gate is not None and LAMP in hm2.gate.state.other_system
    assert hm2.automations.controller_of(LAMP) == "Keukenlicht bij zonsondergang"
    seen = _sources(ha)
    await ha.press("on")  # a run without a parent, as Home Assistant gives a remote
    assert [c.source for c in seen if c.entity_id == "light.hall"] == [Source.AUTOMATION]
    await hm.stop()
    await hm2.stop()


async def test_never_asked_to_go_off_and_the_list_says_why(tmp_path: Path) -> None:
    """On Autonomous HouseMind never asks to switch the wall switch's automation off – no
    message, no question in the row, no button – and Bestaande automatiseringen says: "Blijft
    aan. Dit is een knop of schakelaar van jullie. Wat hij doet, telt als jullie eigen hand.\""""
    hm, _clock, ha = await _start(tmp_path, {WALL_SWITCH: WALL_CONFIG})
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await hm.tick()
    await hm._review_automations(hm.world.snapshot(hm.clock.now()))  # type: ignore[union-attr]
    assert hm.automations.status[WALL_SWITCH] is Status.SEEN
    assert hm.automation_question("light_on_entry", "kitchen") is None
    assert ha.states[WALL_SWITCH].state == "on"
    assert not (await hm.turn_off_automation(WALL_SWITCH, "demo-user-1"))["ok"]
    rows = {r["id"]: r for r in (await views.house_settings(hm, "nl"))["automations"]}
    row = rows[WALL_SWITCH]
    assert row["state"] == "hand" and not row["canTurnOff"]
    assert row["text"] == (
        "Blijft aan. Dit is een knop of schakelaar van jullie. Wat hij doet, telt als jullie "
        "eigen hand."
    )
    await hm.stop()


async def test_housemind_never_flips_the_switch_itself(tmp_path: Path) -> None:
    """The wall switch is the residents' own: HouseMind never switches it, and says why; a
    person's own "Do it" may. Also when HouseMind took it for a lamp (in the real house: a
    switch that goes with a lamp, B-358), the lighting of the room leaves it out – so no
    "niet gedaan" in the Logbook either."""
    hm, clock, ha = await _start(tmp_path, {WALL_SWITCH: WALL_CONFIG})
    assert hm.gate is not None and hm.skills is not None
    assert WALL in hm.gate.state.hand_switches
    decision = await hm.gate.submit(_request(clock, WALL, "switch.turn_on"))
    assert decision.outcome is Outcome.DENIED and decision.reason == "hand_switch"
    assert ("switch.turn_on", WALL, {}) not in ha.calls
    text = hm.languages.text("nl", "gate.reason.hand_switch")
    assert text == "Met deze schakelaar bedienen jullie zelf de lamp. Die laat ik voor jullie."
    yes = replace(_request(clock, WALL, "switch.turn_on"), layer=Layer.PERSON_ANSWER)
    assert (await hm.gate.submit(yes)).reason != "hand_switch"
    hm.gate.state.roles[WALL] = "light"  # taken for a lamp
    hm.skills.forget()
    lamps = [e.entity_id for e in hm.skills._entities("kitchen", "light")]
    assert LAMP in lamps and WALL not in lamps
    hm.skills.hand_switches = set()  # without the hand it would be one of the lamps
    hm.skills.forget()
    assert WALL in [e.entity_id for e in hm.skills._entities("kitchen", "light")]
    await hm.stop()


async def test_from_the_history_a_button_counts_as_a_person(tmp_path: Path) -> None:
    """Reading the history (E-257): what the logbook puts on the wall switch's automation
    teaches as much as a person – not at half, as an automation – and does not count as
    that automation acting."""
    from housemind.brain.learning import HISTORY

    clock = SimulatedClock(EVENING)
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    await ha.add_entity(
        Entity(WALL, f"reg-{WALL}", "Keuken schakelaar", area_id="kitchen"),
        State(WALL, "off", {}, clock.now()),
    )
    await ha.add_automation(WALL_SWITCH, "Keuken - schakelaar", WALL_CONFIG)
    _evenings(ha, 3, by=Source.PERSON, slots=((17, 0),))
    _evenings(ha, 3, by=Source.AUTOMATION, cause=WALL_SWITCH, slots=((17, 30),))
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    assert hm._history_task is not None and hm.learning is not None
    await hm._history_task
    hits: dict[str, float] = {}
    for (s, a, b, r), c in hm.learning.counts.items():
        if s == "light_on_entry" and a == "kitchen" and b.startswith(HISTORY) and r == "hit":
            slot = b.rsplit("|", 1)[1]
            hits[slot] = hits.get(slot, 0) + c.weight
    assert hits["19:00"] > 0 and abs(hits["19:30"] - hits["19:00"]) < 0.01
    assert WALL_SWITCH not in hm.history_automations
    await hm.stop()


async def test_the_websocket_follows_which_automation_acts() -> None:
    """Against Home Assistant itself: HouseMind follows automation_triggered. A lamp switched
    in the run of the wall switch's automation counts as the switch on the wall; the same run
    of any other automation counts as an automation, also one without a parent."""
    server = FakeWsServer()
    async with TestServer(server.app()) as test_server:
        url = str(test_server.make_url("/api/websocket"))
        client = WebSocketHomeAssistant(SimulatedClock(EVENING), TOKEN, url=url, timeout_s=5)
        await client.connect()
        assert server.subscriptions.get("automation_triggered")
        changes: list[StateChange] = []

        async def listener(change: StateChange) -> None:
            changes.append(change)

        client.on_state_change(listener)
        client.tracker.hands = {WALL_SWITCH: Source.PHYSICAL}
        old = server.states["light.kitchen"]

        async def run(automation: str, run_id: str, parent: str | None) -> None:
            await server.push_event(
                "automation_triggered",
                {"entity_id": automation, "name": automation},
                {"id": run_id, "parent_id": parent, "user_id": None},
            )
            context = {"id": run_id, "parent_id": parent, "user_id": None}
            await server.push_state_changed(
                "light.kitchen", old, {**old, "state": "on", "context": context}
            )

        wall_old = {
            "entity_id": WALL,
            "state": "off",
            "attributes": {},
            "last_changed": old["last_changed"],
        }
        wall_new = {
            **wall_old,
            "state": "on",
            "context": {"id": "C1", "parent_id": None, "user_id": None},
        }
        await server.push_state_changed(WALL, wall_old, wall_new)
        await run(WALL_SWITCH, "C2", "C1")
        await run("automation.sunset", "C3", None)
        await asyncio.sleep(0.2)
        lamp = [c.source for c in changes if c.entity_id == "light.kitchen"]
        assert lamp == [Source.PHYSICAL, Source.AUTOMATION]
        await client.close()
