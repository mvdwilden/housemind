"""What holds a room, and what does not (E-461).

A signal – the colour of the alarm, the blink of the doorbell – does not light a room: it holds
nothing, teaches nothing and stays on, and HouseMind keeps off the lamp while it lasts. An
automation that teaches nothing but only switches lamps of rules goes along in their one question.
One that does more keeps its own lamp, not the room.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from housemind.brain.automations import (
    SIGNAL_LEFT_ON,
    SIGNAL_MARGIN,
    Status,
    trigger_kinds,
)
from housemind.brain.readiness import automation_state, steer_view
from housemind.brain.skills import SkillEngine
from housemind.core.clock import SimulatedClock
from housemind.core.model import Context, Entity, HouseModel, Source, State, StateChange
from housemind.ha.adapter import HATrigger
from housemind.ha.source import SourceTracker
from housemind.ha.translate import parse_automation
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind
from housemind.safety.gate import ActionRequest, Layer, Outcome
from tests.test_automations import EVENING, HALL, HALL_CONFIG, start
from tests.test_proof_one_yes import (
    CONTEXT,
    OFF,
    ON,
    _about,
    _people,
    _prove,
    _review,
    _row,
)

ALARM = "automation.alarm_disarmed"
ALARM_CONFIG: dict[str, Any] = {
    "id": "2001",
    "alias": "Alarm disarmed green",
    "triggers": [{"trigger": "state", "entity_id": "alarm_control_panel.home", "to": "disarmed"}],
    "actions": [
        {
            "action": "light.turn_on",
            "target": {"entity_id": "light.hall"},
            "data": {"rgb_color": [0, 255, 0], "effect": "Solid"},
        },
        {"delay": {"seconds": 4}},
        {"action": "light.turn_off", "target": {"entity_id": "light.hall"}, "data": {}},
    ],
}
ALARM_STAYS = "automation.alarm_triggered"
ALARM_STAYS_CONFIG: dict[str, Any] = {
    "id": "2002",
    "alias": "Alarm goes off red",
    "triggers": [{"trigger": "state", "entity_id": "alarm_control_panel.home", "to": "triggered"}],
    "actions": [
        {
            "action": "light.turn_on",
            "target": {"entity_id": "light.hall"},
            "data": {"rgb_color": [255, 0, 0], "effect": "Blink"},
        },
    ],
}
BLINK = "automation.fingerprint"
BLINK_CONFIG: dict[str, Any] = {
    "id": "2003",
    "alias": "Fingerprint",
    "triggers": [{"trigger": "webhook", "webhook_id": "abc"}],
    "actions": [
        {"action": "light.turn_on", "target": {"entity_id": "light.hall"}, "data": {}},
        {"delay": {"seconds": 2}},
        {"action": "light.turn_off", "target": {"entity_id": "light.hall"}, "data": {}},
    ],
}
TV_OFF = "automation.lights_out_with_the_tv"
TV_OFF_CONFIG: dict[str, Any] = {
    "id": "2004",
    "alias": "Lights out with the TV",
    "triggers": [{"trigger": "state", "entity_id": "media_player.tv", "to": "off"}],
    "actions": [
        {"action": "light.turn_off", "target": {"entity_id": "light.hall"}, "data": {}},
    ],
}
AMPLIFIER = "automation.amplifier"
AMPLIFIER_CONFIG: dict[str, Any] = {
    "id": "2005",
    "alias": "Amplifier follows",
    "triggers": [
        {"trigger": "numeric_state", "entity_id": "sensor.amp_power", "above": 35},
        {"trigger": "numeric_state", "entity_id": "sensor.amp_power", "below": 30},
    ],
    "actions": [
        {
            "choose": [
                {
                    "conditions": [],
                    "sequence": [
                        {"action": "switch.turn_on", "target": {"entity_id": "switch.speakers"}},
                        {"action": "light.turn_on", "target": {"entity_id": "light.hall"}},
                    ],
                },
                {
                    "conditions": [],
                    "sequence": [
                        {"action": "switch.turn_off", "target": {"entity_id": "switch.speakers"}},
                        {"action": "light.turn_off", "target": {"entity_id": "light.hall"}},
                    ],
                },
            ]
        }
    ],
}


def hall_off(clock: SimulatedClock) -> ActionRequest:
    return ActionRequest(
        Layer.REFLEX,
        "light.hall",
        "light.turn_off",
        {},
        skill=OFF[0],
        area_id=OFF[1],
        created_at=clock.now(),
    )


# -- what a signal is ------------------------------------------------------------------------


async def test_a_colour_or_a_blink_that_nothing_of_daily_life_starts_is_a_signal(
    tmp_path: Path,
) -> None:
    hm, _clock, _ha = await start(
        tmp_path,
        {ALARM: ALARM_CONFIG, ALARM_STAYS: ALARM_STAYS_CONFIG, BLINK: BLINK_CONFIG},
    )
    await hm._sync_automations()
    readings = hm.automations.readings
    assert readings[ALARM].signal and readings[ALARM_STAYS].signal and readings[BLINK].signal
    assert readings[ALARM].automation.ends and not readings[ALARM_STAYS].automation.ends
    assert hm.automations.signals() == {ALARM, ALARM_STAYS, BLINK}
    assert "light.hall" not in hm.automations.controlled()
    await hm.stop()


async def test_what_lights_a_room_is_no_signal(tmp_path: Path) -> None:
    """Presence with a colour is daily light; a lamp out with the TV shows nothing; a lamp that
    follows an amplifier, with no wait in one go, is no blink."""
    with_colour = {
        **HALL_CONFIG,
        "actions": [
            {
                "action": "light.turn_on",
                "target": {"entity_id": "light.hall"},
                "data": {"rgb_color": [255, 0, 0]},
            }
        ],
    }
    hm, _clock, _ha = await start(
        tmp_path,
        {HALL: with_colour, TV_OFF: TV_OFF_CONFIG, AMPLIFIER: AMPLIFIER_CONFIG},
    )
    await hm._sync_automations()
    readings = hm.automations.readings
    assert not readings[HALL].signal
    assert not readings[TV_OFF].signal
    assert not readings[AMPLIFIER].signal
    assert {"light.hall"} <= hm.automations.controlled()
    await hm.stop()


def test_a_way_through_that_leaves_the_lamp_on_is_no_end() -> None:
    """A status with a choice: one way puts it out, the other leaves it red."""
    state = State("automation.x", "on", {"friendly_name": "X"}, EVENING)
    config = {
        "triggers": [{"trigger": "state", "entity_id": "alarm_control_panel.home"}],
        "actions": [
            {
                "choose": [
                    {
                        "sequence": [
                            {
                                "action": "light.turn_on",
                                "target": {"entity_id": "light.a"},
                                "data": {"rgb_color": [255, 0, 0]},
                            }
                        ]
                    },
                    {
                        "sequence": [
                            {"action": "light.turn_off", "target": {"entity_id": "light.a"}}
                        ]
                    },
                ]
            }
        ],
    }
    assert not parse_automation(state, config).ends
    config["actions"] = [
        {"action": "scene.create", "data": {"scene_id": "before"}},
        {"action": "light.turn_on", "target": {"entity_id": "light.a"}, "data": {"effect": "x"}},
        {"delay": "00:00:08"},
        {"action": "scene.turn_on", "target": {"entity_id": "scene.before"}},
    ]
    read = parse_automation(state, config)
    assert read.ends and read.wait_s == 8.0


def test_home_assistants_own_trigger_words_are_daily_life() -> None:
    model = HouseModel()
    words = {
        "occupancy.detected": "presence_on",
        "occupancy.cleared": "presence_off",
        "motion.detected": "presence_on",
        "door.opened": "opening_on",
        "window.closed": "opening_off",
    }
    for platform, kind in words.items():
        assert trigger_kinds(HATrigger(platform), model) == {kind}
    assert trigger_kinds(HATrigger("webhook"), model) == set()
    state = State("automation.x", "on", {"friendly_name": "X"}, EVENING)
    read = parse_automation(
        state,
        {
            "triggers": [
                {
                    "trigger": "occupancy.cleared",
                    "target": {"entity_id": "binary_sensor.p"},
                    "options": {"for": {"minutes": 2}},
                }
            ],
            "actions": [],
        },
    )
    assert read.triggers[0].entity_ids == ("binary_sensor.p",)
    assert read.triggers[0].for_s == 120.0


# -- it holds nothing ------------------------------------------------------------------------


async def test_a_signal_holds_no_room_and_stays_on(tmp_path: Path) -> None:
    """The real house: the alarm's colours and the doorbell held rooms that were proven."""
    hm, clock, ha = await start(tmp_path, {ALARM: ALARM_CONFIG, ALARM_STAYS: ALARM_STAYS_CONFIG})
    await _people(hm)
    await hm._sync_automations()
    assert hm.rule_hold(*ON) == (False, [])
    _prove(hm, ON)
    await _review(hm)
    assert await _about(hm, "ready:") is not None  # its own question, no automation in it
    assert await _about(hm, "automation:") is None
    row = await _row(hm, *ON)
    assert row["step"] != "observe"
    assert "steer" not in row
    assert ha.states[ALARM].state == "on"
    assert hm.gate is not None
    assert hm.gate.evaluate(hall_off(clock)).reason != "other_system"
    await hm.stop()


async def test_a_signal_teaches_nothing(tmp_path: Path) -> None:
    hm, _clock, _ha = await start(tmp_path, {ALARM: ALARM_CONFIG})
    await hm._sync_automations()
    assert hm.learning is not None
    change = StateChange(
        "light.hall",
        State("light.hall", "off", {}, EVENING),
        State("light.hall", "on", {}, EVENING),
        Source.SIGNAL,
        ALARM,
    )
    assert hm.learning.observe(change, "hall", CONTEXT, EVENING) == []
    assert "light.hall" not in hm.learning.switched
    await hm.stop()


def test_a_run_of_a_signal_is_nobodys_doing() -> None:
    tracker = SourceTracker(SimulatedClock(EVENING))
    tracker.signals = {ALARM}
    tracker.automation_ran("run-1", ALARM, None)
    assert tracker.classify("light.hall", Context("run-1")) is Source.SIGNAL
    tracker.automation_ran("run-2", HALL, None)
    assert tracker.classify("light.hall", Context("run-2")) is Source.AUTOMATION


async def test_the_lamp_is_left_alone_while_the_signal_lasts(tmp_path: Path) -> None:
    hm, clock, _ha = await start(tmp_path, {ALARM: ALARM_CONFIG})
    await hm._sync_automations()
    assert hm.gate is not None
    assert hm.automations.rest_after(ALARM) == timedelta(seconds=4) + SIGNAL_MARGIN
    hm._people_win(StateChange("light.hall", None, None, Source.SIGNAL, ALARM))
    decision = hm.gate.evaluate(hall_off(clock))
    assert decision.outcome is Outcome.DENIED and decision.reason == "signal"
    person = replace(hall_off(clock), layer=Layer.PERSON_ANSWER)
    assert hm.gate.evaluate(person).reason != "signal"  # a person's own "Do it" goes
    await clock.advance(15)
    assert hm.gate.evaluate(hall_off(clock)).reason != "signal"
    await hm.stop()


async def test_a_signal_that_stays_keeps_the_lamp_until_it_changes(tmp_path: Path) -> None:
    """An alarm going off, a strip that shows the state: not put out because the room is empty.
    A signal that ends, or anyone else changing the lamp, gives it back."""
    hm, clock, _ha = await start(tmp_path, {ALARM: ALARM_CONFIG, ALARM_STAYS: ALARM_STAYS_CONFIG})
    await hm._sync_automations()
    assert hm.gate is not None
    assert hm.automations.rest_after(ALARM_STAYS) == SIGNAL_LEFT_ON
    hm._people_win(StateChange("light.hall", None, None, Source.SIGNAL, ALARM_STAYS))
    await clock.advance(3600)
    assert hm.gate.evaluate(hall_off(clock)).reason == "signal"
    hm._people_win(StateChange("light.hall", None, None, Source.SIGNAL, ALARM))  # disarmed
    await clock.advance(15)
    assert hm.gate.evaluate(hall_off(clock)).reason != "signal"
    hm._people_win(StateChange("light.hall", None, None, Source.SIGNAL, ALARM_STAYS))
    hm._people_win(StateChange("light.hall", None, None, Source.AUTOMATION, HALL))
    assert hm.gate.evaluate(hall_off(clock)).reason != "signal"
    await hm.stop()


async def test_a_group_with_a_lamp_in_a_signal_waits_too(tmp_path: Path) -> None:
    from tests.test_group_first import TABLE, _dining
    from tests.test_group_first import _house as group_house

    hm, _ha, clock = await group_house(tmp_path, _dining)
    assert hm.gate is not None
    hm.gate.state.signal_until[TABLE[0]] = clock.now() + timedelta(seconds=30)
    request = ActionRequest(
        Layer.REFLEX,
        "light.table",
        "light.turn_on",
        {},
        skill="light_on_entry",
        area_id="kitchen",
        created_at=clock.now(),
    )
    assert hm.gate.evaluate(request).reason == "signal"
    await hm.stop()


def test_a_lamp_in_a_signal_is_no_light_of_the_room() -> None:
    """Coming home: the strip flashes green at the alarm, and someone walks into the dark. That
    is still coming into the dark, not 'people just chose their light'."""
    clock = SimulatedClock(EVENING)
    model = HouseModel()
    model.entities["light.strip"] = Entity("light.strip", "r1", "Strip")
    engine = SkillEngine.__new__(SkillEngine)
    engine.model = model
    engine.in_signal = lambda entity_id, now: entity_id == "light.strip"
    assert engine._signalling("light.strip", clock.now())
    model.groups["light.all"] = ("light.strip", "light.other")
    assert engine._signalling("light.all", clock.now())
    assert not engine._signalling("light.other", clock.now())


# -- along with its lamps --------------------------------------------------------------------


async def test_lamps_only_goes_along_in_the_one_question(tmp_path: Path) -> None:
    """It teaches nothing, but all it does is what proven rules do with their lamps: named in the
    question and off with the yes. A step back brings it on again."""
    hm, clock, ha = await start(tmp_path, {HALL: HALL_CONFIG, TV_OFF: TV_OFF_CONFIG})
    await _people(hm)
    _prove(hm, ON)
    _prove(hm, OFF)
    await hm._sync_automations()
    reading = hm.automations.readings[TV_OFF]
    assert reading.lamps_only and not reading.learned and not reading.signal
    assert reading.domains == {"lighting"}
    assert hm.lamp_rules(reading) == [OFF]
    assert automation_state(hm, reading, False) == "along"
    await _review(hm)
    card = await _about(hm, "automation:")
    assert card is not None
    assert "Hall light at motion" in card["text"] and "Lights out with the TV" in card["text"]
    assert "gaan dan uit" in card["text"]
    row = await hm.db.fetchone("SELECT topic FROM notifications WHERE id = ?", (card["id"],))
    assert row is not None
    head = str(row["topic"]).split(":", 1)[1]
    assert (await hm.answer_automation(head, True, "demo-user-1"))["ok"]
    assert ha.states[HALL].state == "off"
    assert ha.states[TV_OFF].state == "off"
    assert hm.automations.status[TV_OFF] is Status.TURNED_OFF
    await _review(hm)
    assert ha.states[TV_OFF].state == "off"  # still HouseMind's: it stays off
    assert hm.learning is not None
    from housemind.brain.learning import Result

    for _ in range(2):
        hm.learning._add(*OFF, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    await _review(hm)
    assert ha.states[TV_OFF].state == "on"
    assert ha.states[HALL].state == "on"
    await hm.stop()


async def test_it_waits_for_every_rule_of_its_lamps(tmp_path: Path) -> None:
    hm, _clock, _ha = await start(tmp_path, {TV_OFF: TV_OFF_CONFIG})
    await _people(hm)
    _prove(hm, ON)
    await hm._sync_automations()
    await _review(hm)
    assert hm.learning is not None
    # The rule that puts lamps out is not proven: nothing to take over yet.
    hm.learning.steps[OFF] = Step.PROPOSE
    hm.learning.first_seen[OFF] = hm.clock.now()
    hm.forget_lamp_rules()
    await _review(hm)
    assert await _about(hm, "automation:") is None
    _prove(hm, OFF)
    await _review(hm)
    assert await _about(hm, "automation:") is not None
    await hm.stop()


async def test_a_message_in_it_means_it_does_not_go_along(tmp_path: Path) -> None:
    with_message = {
        **TV_OFF_CONFIG,
        "actions": [
            *TV_OFF_CONFIG["actions"],
            {"action": "notify.phone", "data": {"message": "Lights out"}},
        ],
    }
    hm, _clock, _ha = await start(tmp_path, {TV_OFF: with_message})
    _prove(hm, OFF)
    await hm._sync_automations()
    reading = hm.automations.readings[TV_OFF]
    assert not reading.lamps_only and hm.lamp_rules(reading) == []
    assert not hm.can_go(reading)
    await hm.stop()


# -- one that does more keeps its own lamp -----------------------------------------------------


async def test_what_cannot_go_keeps_its_lamp_not_the_room(tmp_path: Path) -> None:
    """The real house: a lamp that follows an amplifier held a whole living room. It stays on,
    its lamp stays with it, and the rest of the room goes on as usual."""
    other_lamp = {
        **AMPLIFIER_CONFIG,
        "actions": [
            {
                "choose": [
                    {
                        "sequence": [
                            {
                                "action": "switch.turn_on",
                                "target": {"entity_id": "switch.speakers"},
                            },
                            {
                                "action": "light.turn_on",
                                "target": {"entity_id": "light.living_reading"},
                            },
                        ]
                    }
                ]
            }
        ],
    }
    hm, _clock, _ha = await start(tmp_path, {AMPLIFIER: other_lamp})
    await _people(hm)
    await hm._sync_automations()
    assert hm.skills is not None and hm.learning is not None
    pair = ("light_on_entry", "living")
    assert len(hm.skills.units_of(*pair)) == 2
    assert hm.rule_hold(*pair) == (False, [])
    assert [r.entity_id for r in hm.lamps_kept(*pair)] == [AMPLIFIER]
    view = steer_view(hm, "nl", *pair, True)
    assert "Amplifier follows" in view["steer"]["text"]
    assert "rest" in view["steer"]["text"]
    assert hm.gate is not None
    assert "light.living_reading" in hm.gate.state.other_system  # its own lamp stays watched
    await hm.stop()


async def test_with_every_lamp_kept_the_rule_is_held(tmp_path: Path) -> None:
    hm, _clock, _ha = await start(tmp_path, {AMPLIFIER: AMPLIFIER_CONFIG})
    await _people(hm)
    await hm._sync_automations()
    assert hm.skills is not None
    assert [e.entity_id for e in hm.skills.units_of(*ON)] == ["light.hall"]
    held, holding = hm.rule_hold(*ON)
    assert held and [r.entity_id for r in holding] == [AMPLIFIER]
    assert hm.lamps_kept(*ON) == []
    _prove(hm, ON)
    await _review(hm)
    assert await _about(hm, "ready:") is None
    assert await _about(hm, "automation:") is None
    assert "niet overnemen" in (await _row(hm, *ON))["text"]
    await hm.stop()


# -- it says so ------------------------------------------------------------------------------


async def test_the_list_of_automations_says_what_each_is(tmp_path: Path) -> None:
    from housemind.server import views

    hm, _clock, _ha = await start(tmp_path, {ALARM: ALARM_CONFIG, TV_OFF: TV_OFF_CONFIG})
    _prove(hm, OFF)
    await hm._sync_automations()
    for lang in ("nl", "en", "de", "fr"):
        rows = {row["id"]: row for row in views.automations(hm, lang)}
        assert rows[ALARM]["state"] == "signal"
        assert rows[TV_OFF]["state"] == "along"
        assert rows[ALARM]["text"] and "ui.automations" not in rows[ALARM]["text"]
        assert rows[TV_OFF]["text"] and "ui.automations" not in rows[TV_OFF]["text"]
    await hm.stop()


async def test_the_reason_is_written_in_each_language(tmp_path: Path) -> None:
    hm: HouseMind
    hm, _clock, _ha = await start(tmp_path, {})
    for lang in ("nl", "en", "de", "fr"):
        assert "gate.reason" not in hm.languages.text(lang, "gate.reason.signal")
        said = hm.languages.text(lang, "ui.ready.steer.partly", names="‘X’")
        assert "‘X’" in said
    await hm.stop()
