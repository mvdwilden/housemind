"""Existing Home Assistant automations (E-03, E-41, E-42, E-110, E-112, E-160).

HouseMind learns from them, keeps off their devices while they are on, asks an administrator
before it switches one off – never deletes – and switches them back on when it gives back.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.automations import Status, can_take_over, read_automation, trigger_kinds
from housemind.brain.learning import Result
from housemind.brain.skills import load_skill_params
from housemind.core.clock import SimulatedClock
from housemind.core.model import Context, Entity, HouseModel, Source, State
from housemind.demo.houses import load_demo_house
from housemind.ha.adapter import HATrigger, HomeAssistant
from housemind.ha.fake import FakeHomeAssistant
from housemind.ha.translate import parse_automation
from housemind.ha.websocket import WebSocketHomeAssistant
from housemind.knowledge.catalog import (
    AllowedAction,
    Catalog,
    DeviceKind,
    Initiative,
    Match,
    SafetyClass,
    Step,
)
from housemind.knowledge.loader import load_catalog
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, GateState, Layer, Outcome, SafetyGate
from housemind.safety.hard_limits import hard_limit_reason
from housemind.server import views
from housemind.server.web import create_app
from tests.ha_ws_server import TOKEN, FakeWsServer

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 10, 19, 17, 30, tzinfo=UTC)  # Monday 19:30 local, dark
HALL = "automation.hall_light"
HALL_CONFIG: dict[str, Any] = {
    "id": "1001",
    "alias": "Hall light at motion",
    "triggers": [{"trigger": "state", "entity_id": "binary_sensor.hall_motion", "to": "on"}],
    "actions": [
        {"action": "light.turn_on", "target": {"entity_id": "light.hall"}, "data": {}},
    ],
}
LIVING_CONFIG: dict[str, Any] = {
    "id": "1002",
    "alias": "Living light and a message",
    "trigger": [{"platform": "state", "entity_id": "binary_sensor.living_presence", "to": "on"}],
    "action": [
        {
            "service": "light.turn_on",
            "entity_id": "light.living_reading",
            "data": {"brightness_pct": 40},
        },
        {"service": "notify.mobile_app_phone_1", "data": {"message": "Welcome"}},
    ],
}


async def start(
    tmp_path: Path, automations: dict[str, dict[str, Any]] | None = None, consent: bool = True
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(EVENING)
    house = load_demo_house("family_home", clock, "nl")
    for entity_id, config in (
        automations if automations is not None else {HALL: HALL_CONFIG}
    ).items():
        await house.ha.add_automation(entity_id, str(config["alias"]), config)
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    if consent:
        await hm.set_house_setting("consent_learning", True)
    return hm, clock, house.ha


def hall_light_on(clock: SimulatedClock, layer: Layer = Layer.REFLEX) -> ActionRequest:
    return ActionRequest(
        layer,
        "light.hall",
        "light.turn_on",
        {},
        reason="skill:light_on_entry",
        skill="light_on_entry",
        area_id="hall",
        created_at=clock.now(),
        facts={"context": "workday|19:30"},
    )


async def admin_view(hm: HouseMind) -> views.Viewer:
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    await hm.db.execute("UPDATE people SET level = 3 WHERE user_id = 'demo-user-1'")
    viewer.level = 3
    viewer.preferences["language"] = "nl"
    return viewer


# -- the translation layer (E-151) -----------------------------------------------------------


def test_reading_an_automation_config_new_and_old_style() -> None:
    state = State(HALL, "on", {"friendly_name": "Hall light at motion", "id": "1001"}, EVENING)
    automation = parse_automation(
        state,
        {
            "triggers": [
                {
                    "trigger": "state",
                    "entity_id": ["binary_sensor.a", "binary_sensor.b"],
                    "to": "on",
                },
                {"trigger": "sun", "event": "sunset", "offset": "-00:30:00"},
                {"trigger": "time", "at": "07:15:00"},
                {
                    "trigger": "device",
                    "type": "motion",
                    "entity_id": "reg-x",
                    "domain": "binary_sensor",
                },
                {"trigger": "state", "entity_id": "binary_sensor.c", "enabled": False},
            ],
            "actions": [
                {"delay": "00:00:05"},
                {
                    "choose": [
                        {
                            "conditions": [],
                            "sequence": [
                                {
                                    "action": "light.turn_on",
                                    "target": {"area_id": "hall"},
                                    "data": {"brightness_pct": 30},
                                }
                            ],
                        }
                    ],
                    "default": [
                        {"service": "light.turn_off", "entity_id": "light.hall, light.porch"}
                    ],
                },
                {"if": [{"condition": "state"}], "then": [{"scene": "scene.evening"}]},
                {"type": "turn_on", "device_id": "dev-1", "entity_id": "reg-y", "domain": "light"},
                {"action": "light.turn_on", "target": {"entity_id": "light.x"}, "enabled": False},
            ],
        },
    )
    assert automation.enabled and automation.config_id == "1001" and automation.readable
    assert [t.platform for t in automation.triggers] == ["state", "sun", "time", "device"]
    assert automation.triggers[0].entity_ids == ("binary_sensor.a", "binary_sensor.b")
    assert automation.triggers[1].to == "sunset" and automation.triggers[3].to == "motion"
    assert [s.action for s in automation.steps] == [
        "light.turn_on",
        "light.turn_off",
        "light.turn_on",
    ]
    assert automation.steps[0].area_ids == ("hall",) and automation.steps[0].data == {
        "brightness_pct": 30
    }
    assert automation.steps[1].entity_ids == ("light.hall", "light.porch")
    assert automation.steps[2].device_ids == ("dev-1",)
    assert automation.other == 1  # the scene
    unread = parse_automation(State("automation.x", "off", {}, EVENING), None)
    assert not unread.readable and not unread.enabled and not unread.steps


async def test_websocket_client_reads_automations_and_switches_them() -> None:
    server = FakeWsServer()
    server.states["automation.yaml_only"] = {
        "entity_id": "automation.yaml_only",
        "state": "on",
        "attributes": {"friendly_name": "Old automation"},
        "last_changed": "2026-10-17T18:00:00+00:00",
    }
    async with TestServer(server.app()) as test_server:
        url = str(test_server.make_url("/api/websocket"))
        client = WebSocketHomeAssistant(SimulatedClock(EVENING), TOKEN, url=url, timeout_s=5)
        await client.connect()
        found = {a.entity_id: a for a in await client.automations()}
        kitchen = found["automation.kitchen_light"]
        assert kitchen.name == "Kitchen light at motion" and kitchen.config_id == "1700000000"
        assert kitchen.steps[0].action == "light.turn_on"
        assert kitchen.steps[0].entity_ids == ("light.kitchen",)
        assert not found["automation.yaml_only"].readable
        result = await client.call_action("automation.turn_off", "automation.kitchen_light")
        assert result.ok
        assert server.calls[-1]["domain"] == "automation"
        assert server.calls[-1]["service"] == "turn_off"
        assert server.states["automation.kitchen_light"]["state"] == "off"
        await client.close()


def test_ha_layer_can_read_automations_but_never_make_or_delete_them() -> None:
    """E-03: there is simply no way in the translation layer to make, change or delete one.
    It may only take away HouseMind's own link, for the factory settings (E-274) – without a
    name, so nothing else can be meant – and make or update Vesta's own Assist pipeline (E-280):
    the layer itself refuses any pipeline whose agent is not HouseMind's."""
    names = {n for n, _ in inspect.getmembers(HomeAssistant) if not n.startswith("_")}
    assert "automations" in names
    assert not {n for n in names if any(w in n for w in ("create", "delete", "remove", "save"))}
    unlink = inspect.signature(HomeAssistant.unlink_housemind).parameters
    assert list(unlink) == ["self", "pipeline"]
    assert list(inspect.signature(HomeAssistant.vesta_pipeline).parameters) == [
        "self",
        "pipeline",
        "settings",
    ]


# -- understanding what an automation does (E-41) ---------------------------------------------


def _family(clock: SimulatedClock) -> tuple[HouseModel, Catalog, dict[str, dict[str, Any]]]:
    house = load_demo_house("family_home", clock, "en")
    catalog, _ = load_catalog([ROOT / "knowledge" / "catalog"])
    params = load_skill_params([ROOT / "knowledge" / "skills"])
    return house.ha.model, catalog, params


def _read(config: dict[str, Any], model: HouseModel, catalog: Catalog, params: Any) -> Any:
    state = State("automation.test", "on", {"friendly_name": "Test"}, EVENING)
    roles = {"living": "living", "hall": "hall", "kitchen": "kitchen", "bedroom": "bedroom"}
    return read_automation(parse_automation(state, config), model, catalog, {}, params, roles)


def test_an_automation_teaches_skills_with_its_values() -> None:
    model, catalog, params = _family(SimulatedClock(EVENING))
    hall = _read(HALL_CONFIG, model, catalog, params)
    assert hall.learned == (("light_on_entry", "hall"),)
    assert hall.whole and hall.domains == {"lighting"} and hall.controls == {"light.hall"}
    living = _read(LIVING_CONFIG, model, catalog, params)
    assert living.learned == (("light_on_entry", "living"),)
    assert living.values == {("light_on_entry", "living"): {"brightness_pct": 40.0}}
    assert not living.whole  # the message is something HouseMind does not do
    curtains = _read(
        {
            "triggers": [{"trigger": "sun", "event": "sunset"}],
            "actions": [{"action": "cover.close_cover", "target": {"area_id": "living"}}],
        },
        model,
        catalog,
        params,
    )
    assert curtains.learned == (("covers_close_dark", "living"),) and curtains.whole
    away = _read(
        {
            "triggers": [{"trigger": "state", "entity_id": "person.resident_1", "to": "not_home"}],
            "actions": [
                {
                    "action": "climate.set_temperature",
                    "target": {"entity_id": "climate.living"},
                    "data": {"temperature": 16},
                }
            ],
        },
        model,
        catalog,
        params,
    )
    assert away.learned == (("setback_away", "living"),) and away.whole
    awning = _read(
        {
            "triggers": [{"trigger": "sun", "event": "sunrise"}],
            "actions": [
                {"action": "cover.open_cover", "target": {"entity_id": "cover.garden_awning"}}
            ],
        },
        model,
        catalog,
        params,
    )
    assert not awning.learned and not awning.whole  # no skill of HouseMind does this yet
    assert awning.controls == {"cover.garden_awning"}  # but HouseMind still keeps off it


def test_trigger_kinds_in_plain_words() -> None:
    model, _, _ = _family(SimulatedClock(EVENING))
    assert trigger_kinds(HATrigger("sun", to="sunrise"), model) == {"sun_up"}
    assert trigger_kinds(HATrigger("zone", ("person.resident_1",), to="leave"), model) == {
        "people_leave"
    }
    assert trigger_kinds(HATrigger("time", at="22:30:00"), model) == {"time_evening"}
    assert trigger_kinds(HATrigger("time", at="23:30"), model) == {"time_night"}
    sun = HATrigger("numeric_state", ("sun.sun",), attribute="elevation", below=-2)
    assert trigger_kinds(sun, model) == {"sun_down"}
    assert trigger_kinds(HATrigger("numeric_state", ("zone.home",), below=1), model) == {
        "people_leave"
    }
    window = HATrigger("state", ("binary_sensor.bedroom_window",), to="on")
    assert trigger_kinds(window, model) == {"opening_on"}
    motion = model.entities["binary_sensor.hall_motion"]
    device = HATrigger("device", (motion.registry_id,), to="no_motion")
    assert trigger_kinds(device, model) == {"presence_off"}


def test_can_take_over_needs_every_domain() -> None:
    model, catalog, params = _family(SimulatedClock(EVENING))
    both = _read(
        {
            "triggers": [
                {"trigger": "state", "entity_id": "binary_sensor.living_presence", "to": "on"}
            ],
            "actions": [
                {"action": "light.turn_on", "target": {"entity_id": "light.living_reading"}},
                {
                    "action": "climate.set_temperature",
                    "target": {"entity_id": "climate.living"},
                    "data": {"temperature": 20},
                },
            ],
        },
        model,
        catalog,
        params,
    )
    assert not both.whole  # no climate skill starts at presence in a room
    hall = _read(HALL_CONFIG, model, catalog, params)

    def skill_domain(skill: str) -> str | None:
        return params[skill]["domain"]

    assert not can_take_over(hall, lambda d: False, lambda s, a: False, skill_domain)
    # Lighting on Autonomous somewhere else is not enough: the hall itself must be (20.12).
    assert not can_take_over(hall, lambda d: d == "lighting", lambda s, a: False, skill_domain)
    assert can_take_over(hall, lambda d: False, lambda s, a: True, skill_domain)


# -- the safety gate (E-42, E-110, E-03) -------------------------------------------------------


def _automation_gate(catalog: Catalog) -> tuple[SafetyGate, FakeHomeAssistant, SimulatedClock]:
    clock = SimulatedClock(EVENING)
    entities = {
        HALL: Entity(HALL, "reg-auto", "Hall light at motion"),
        "light.hall": Entity(
            "light.hall", "reg-light", "Hall", capabilities={"supported_color_modes": ["onoff"]}
        ),
    }
    states = {e: State(e, "on" if e == HALL else "off", {}, EVENING) for e in entities}
    ha = FakeHomeAssistant(clock, HouseModel(entities=entities), states)
    state = GateState(skill_step=lambda skill, area: Step.AUTONOMOUS)
    return SafetyGate(clock, ha, catalog, ha.model, state), ha, clock


async def test_gate_never_switches_an_automation_by_itself(catalog: Catalog) -> None:
    gate, ha, _ = _automation_gate(catalog)
    for layer in (
        Layer.REFLEX,
        Layer.PLANNER,
        Layer.LEARNING,
        Layer.TRIAL,
        Layer.AI,
        Layer.HOUSE_RULE,
    ):
        decision = await gate.submit(ActionRequest(layer, HALL, "automation.turn_off"))
        assert decision.outcome is Outcome.PROPOSED, layer
    assert not ha.calls
    yes = await gate.submit(ActionRequest(Layer.PERSON_ANSWER, HALL, "automation.turn_off"))
    assert yes.executed and ha.states[HALL].state == "off"
    for action in ("automation.trigger", "automation.reload", "automation.toggle"):
        decision = await gate.submit(ActionRequest(Layer.PERSON_ANSWER, HALL, action))
        assert decision.outcome is Outcome.DENIED, action


async def test_switching_back_on_is_always_possible(catalog: Catalog) -> None:
    gate, ha, clock = _automation_gate(catalog)
    ha.states[HALL] = State(HALL, "off", {}, EVENING)
    gate.state.paused_until = clock.now() + timedelta(hours=1)
    gate.state.admin_max_step["automation"] = Step.OBSERVE  # as after "give everything back"
    assert (
        await gate.submit(ActionRequest(Layer.PERSON_ANSWER, HALL, "automation.turn_on"))
    ).reason == "paused"
    back = await gate.submit(ActionRequest(Layer.RESTORE, HALL, "automation.turn_on"))
    assert back.executed and ha.states[HALL].state == "on"
    gate.state.excluded.add(HALL)  # "leave alone" never blocks giving back
    ha.states[HALL] = State(HALL, "off", {}, EVENING)
    assert (await gate.submit(ActionRequest(Layer.RESTORE, HALL, "automation.turn_on"))).executed
    other = await gate.submit(ActionRequest(Layer.RESTORE, "light.hall", "light.turn_on"))
    assert other.reason == "restore_only_automations"
    off = await gate.submit(ActionRequest(Layer.RESTORE, HALL, "automation.turn_off"))
    assert off.reason == "restore_only_automations"


async def test_devices_of_another_system_are_only_watched(catalog: Catalog) -> None:
    gate, ha, clock = _automation_gate(catalog)
    gate.state.other_system.add("light.hall")
    decision = await gate.submit(hall_light_on(clock))
    assert decision.outcome is Outcome.OBSERVED and decision.reason == "other_system"
    assert not ha.calls
    answer = await gate.submit(hall_light_on(clock, Layer.PERSON_ANSWER))
    assert answer.outcome is Outcome.OBSERVED
    emergency = await gate.submit(hall_light_on(clock, Layer.EMERGENCY))
    assert emergency.executed  # an emergency always acts (E-80)


def test_hard_limit_no_own_automations_scripts_scenes_or_helpers() -> None:
    for action in (
        "automation.trigger",
        "automation.reload",
        "script.turn_on",
        "script.reload",
        "scene.create",
        "scene.turn_on",
        "blueprint.import",
        "input_boolean.turn_on",
        "input_number.set_value",
    ):
        assert hard_limit_reason(None, action) == "no_own_automations", action
    assert hard_limit_reason(None, "automation.turn_off") is None
    assert hard_limit_reason(None, "automation.turn_on") is None


async def test_no_knowledge_file_lets_housemind_switch_automations_itself() -> None:
    """A user's own catalogue entry cannot loosen E-42: the limit lives in code."""
    loose = Catalog()
    loose.add(
        DeviceKind(
            key="automation.mine",
            match=Match(domain="automation"),
            safety_class=SafetyClass.FREE,
            initiative=Initiative.HOUSE,
            max_step=Step.AUTONOMOUS,
            actions=(AllowedAction("automation.turn_off"), AllowedAction("automation.turn_on")),
        )
    )
    gate, ha, _ = _automation_gate(loose)
    for layer in (Layer.REFLEX, Layer.TRIAL, Layer.EMERGENCY):
        decision = await gate.submit(ActionRequest(layer, HALL, "automation.turn_off"))
        assert not decision.executed, layer
    assert not ha.calls


# -- the whole story in a demo house -----------------------------------------------------------


async def test_keeps_off_devices_of_an_automation_and_says_why(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path)
    assert hm.gate is not None and hm.learning is not None
    assert "light.hall" in hm.gate.state.other_system
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    decision = await hm.consider(hall_light_on(clock))
    assert decision is not None and decision.outcome is Outcome.OBSERVED
    assert ("light.turn_on", "light.hall", {}) not in ha.calls
    assert "Hall light at motion" in hm.why_not("light.hall", "nl")
    assert any(p.entity_id == "light.hall" for p in hm.learning.pending)  # it still learns
    # Someone switches the automation off in Home Assistant: HouseMind may act again.
    await ha.simulate(HALL, "off", by=Source.PERSON)
    assert "light.hall" not in hm.gate.state.other_system
    hm._last_request.clear()
    decision = await hm.consider(hall_light_on(clock))
    assert decision is not None and decision.executed
    await hm.stop()


async def test_what_an_automation_does_is_start_knowledge(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {"automation.living": LIVING_CONFIG})
    assert hm.learning is not None and hm.explainer is not None
    await hm.tick()  # the first review reads the automations again, now with consent
    assert hm.learning.value("light_on_entry", "living", "workday|19:30", "brightness_pct") == 40
    first = hm.learning.total("light_on_entry", "living", Result.HIT, clock.now())
    assert first >= 3 - 0.05
    await hm._sync_automations()  # once is enough: no double counting
    assert hm.learning.total("light_on_entry", "living", Result.HIT, clock.now()) == first
    request = ActionRequest(
        Layer.REFLEX,
        "light.living_reading",
        "light.turn_on",
        {"brightness_pct": 40},
        skill="light_on_entry",
        area_id="living",
        facts={"context": "workday|19:30"},
    )
    decision = (await hm.gate.submit(request)) if hm.gate else None
    assert decision is not None
    lines = hm.explain(decision, 3, "nl")
    assert any('automatisering "Living light and a message"' in line for line in lines), lines
    await hm.stop()
    # After a restart it still knows it gave this start knowledge already.
    again = HouseMind(clock, hm.ha, Paths([ROOT / "knowledge"], tmp_path))
    await again.start(autorun=False)
    assert again.automations.seeded == {"automation.living"}
    await again.stop()


async def test_no_learning_from_automations_without_consent(tmp_path: Path) -> None:
    hm, _, _ = await start(
        tmp_path, {"automation.living": LIVING_CONFIG, HALL: HALL_CONFIG}, consent=False
    )
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await hm.tick()
    assert not hm.automations.seeded
    assert not any(k[2] == "automation" for k in hm.learning.counts)
    assert hm.automations.status[HALL] is Status.SEEN  # without consent nothing is taken over
    assert (await hm.turn_off_automation(HALL, "demo-user-1"))["reason"] == "not_ready"
    assert not any(d["id"] == HALL for d in views.devices(hm, "en"))
    await hm.stop()


async def test_asks_yes_or_no_turns_off_and_gives_back(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None and hm.gate is not None
    await hm.tick()
    assert hm.automations.status[HALL] is Status.SEEN  # not ready yet: nothing asked
    # HouseMind now does the hall light itself (E-42, 20.12).
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await clock.advance(16 * 60)
    await hm.tick()
    assert hm.automations.status[HALL] is Status.OFFERED
    assert not any(c[0].startswith("automation.") for c in ha.calls)  # only asked
    proposed = await hm.db.fetchone(
        "SELECT outcome FROM decisions WHERE entity_id = ? ORDER BY id DESC", (HALL,)
    )
    assert proposed is not None and proposed["outcome"] == "proposed"
    admin = await admin_view(hm)
    notices = await views.notifications(hm, "nl", admin=True)
    offer = next(n for n in notices if "Hall light at motion" in n["text"])
    assert offer["kind"] == "learning" and offer["actions"] == ["yes", "no"]
    assert "wis niets" in offer["text"]
    assert not [n for n in await views.notifications(hm, "nl") if n["actions"] == ["yes", "no"]]
    settings = await views.house_settings(hm, "nl")
    row = next(a for a in settings["automations"] if a["id"] == HALL)
    assert row["state"] == "ready" and row["canTurnOff"]
    assert admin.is_admin

    result = await hm.answer_automation(HALL, True, "demo-user-1")
    assert result["ok"]
    assert ("automation.turn_off", HALL, {}) in ha.calls
    assert ha.states[HALL].state == "off" and HALL in ha.automation_configs  # never deleted
    assert hm.automations.status[HALL] is Status.TURNED_OFF
    assert "light.hall" not in hm.gate.state.other_system
    assert not any("Hall light at motion" in n["text"] for n in await hm.notifications())
    recent = list(hm.recent.values())[-1].decision
    assert hm.explain(recent, 2, "nl")[0].startswith(
        'Ik heb de automatisering "Hall light at motion"'
    )
    await clock.advance(16 * 60)
    await hm.tick()  # it stays off: HouseMind does not ask again
    assert hm.automations.status[HALL] is Status.TURNED_OFF

    # E-112: before removal it goes back on – also during a pause.
    await hm.pause(0)
    given = await hm.give_back(True, "demo-user-1")
    assert given == {"ok": True, "failed": [], "link": True}  # the link goes too (E-112)
    assert ("automation.turn_on", HALL, {}) in ha.calls
    assert ha.states[HALL].state == "on"
    assert hm.automations.status[HALL] is Status.SEEN
    assert "light.hall" in hm.gate.state.other_system
    assert hm.gate.state.admin_max_step["light"] is Step.OBSERVE
    await hm.pause(None)
    await clock.advance(16 * 60)
    await hm.tick()
    assert hm.automations.status[HALL] is Status.SEEN  # given back: nothing is asked
    await hm.stop()


async def test_no_means_no_and_switching_back_on_means_keep_it(tmp_path: Path) -> None:
    kitchen = {
        "id": "1003",
        "alias": "Kitchen light at motion",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.kitchen_motion", "to": "on"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.kitchen"}}],
    }
    hm, clock, ha = await start(tmp_path, {HALL: HALL_CONFIG, "automation.kitchen": kitchen})
    assert hm.learning is not None and hm.gate is not None
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await hm.tick()
    assert hm.automations.status[HALL] is Status.OFFERED
    # No: HouseMind does not ask again, and keeps off the hall light.
    await hm.answer_automation(HALL, False, "demo-user-1")
    assert hm.automations.status[HALL] is Status.DECLINED
    late = await hm.answer_automation(HALL, True, "demo-user-2")  # the first answer counts
    assert late == {"ok": False, "reason": "already_answered"}
    assert ha.states[HALL].state == "on"
    # Yes for the kitchen, then someone switches it back on in Home Assistant.
    assert (await hm.answer_automation("automation.kitchen", True, "demo-user-1"))["ok"]
    await ha.simulate("automation.kitchen", "on", by=Source.PERSON)
    assert hm.automations.status["automation.kitchen"] is Status.DECLINED
    assert {"light.hall", "light.kitchen"} <= hm.gate.state.other_system
    await clock.advance(16 * 60)
    await hm.tick()
    assert hm.automations.status[HALL] is Status.DECLINED
    calls = len(ha.calls)
    assert (await hm.give_back(True))["ok"]
    assert len(ha.calls) == calls  # nothing of HouseMind's left to switch back on
    await hm.stop()


async def test_undo_from_house_settings_and_an_admin_cap_holds_back(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    assert hm.learning is not None and hm.gate is not None
    for skill, area in hm.automations.readings[HALL].learned:  # all it does: Autonomous
        hm.learning.steps[(skill, area)] = Step.AUTONOMOUS
    await hm.set_house_setting("admin_max_step", {"light": "propose"})
    await hm.tick()
    assert hm.automations.status[HALL] is Status.SEEN  # the administrator holds lighting back
    reading = hm.automations.readings[HALL]
    assert not hm.ready_to_take_over(reading)
    await hm.set_house_setting("admin_max_step", {})
    assert hm.ready_to_take_over(reading)
    # Lifting the limit to Autonomous is the administrator's yes (E-42, 01-11-2026).
    assert ha.states[HALL].state == "off"
    assert hm.automations.status[HALL] is Status.TURNED_OFF
    assert (await hm.answer_automation(HALL, True, "demo-user-1"))["reason"] == "already_answered"
    back = await hm.restore_automation(HALL, "demo-user-1")
    assert back["ok"] and ha.states[HALL].state == "on"
    assert hm.automations.status[HALL] is Status.DECLINED
    assert (await hm.restore_automation(HALL, "demo-user-1"))["reason"] == "not_turned_off"
    rows = {a["id"]: a for a in (await views.house_settings(hm, "en"))["automations"]}
    assert rows[HALL]["state"] == "kept" and rows[HALL]["canTurnOff"]
    await hm.stop()


async def test_mixed_automation_stays_on(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path, {"automation.living": LIVING_CONFIG})
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await hm.tick()
    assert hm.automations.status["automation.living"] is Status.SEEN
    result = await hm.turn_off_automation("automation.living", "demo-user-1")
    assert result == {"ok": False, "reason": "not_whole"}
    rows = (await views.house_settings(hm, "en"))["automations"]
    assert rows[0]["state"] == "stays" and not rows[0]["canTurnOff"]
    assert rows[0]["teaches"] == ["light when entering – Woonkamer"]
    await hm.stop()


async def test_only_autonomous_in_its_rooms_is_enough_to_ask(tmp_path: Path) -> None:
    """Without proof (E-460) only the skill on Autonomous in the automation's own room makes
    HouseMind ask (20.12, E-42): another room is not enough."""
    hm, clock, _ = await start(tmp_path)
    assert hm.learning is not None
    now = clock.now()
    for day in range(8):
        hm.learning._add(
            "light_on_entry", "hall", "workday|19:30", Result.HIT, now + timedelta(days=day)
        )
    await clock.advance(8 * 86400)
    assert not hm.learning.proven("light_on_entry", "hall", clock.now())
    assert hm.domain_step("lighting") is not Step.AUTONOMOUS
    await hm.tick()
    assert hm.automations.status[HALL] is Status.SEEN  # it does not do it yet
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await clock.advance(16 * 60)
    await hm.tick()
    assert hm.automations.status[HALL] is Status.SEEN  # another room is not enough
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await clock.advance(16 * 60)
    await hm.tick()
    assert hm.automations.status[HALL] is Status.OFFERED
    await hm.stop()


async def test_a_renamed_automation_keeps_its_story(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await hm.tick()
    assert (await hm.answer_automation(HALL, True, "demo-user-1"))["ok"]
    await ha.rename_entity(HALL, "automation.hall_light_motion")
    await hm._sync_automations()
    assert hm.automations.status == {"automation.hall_light_motion": Status.TURNED_OFF}
    assert hm.automations.turned_off() == ["automation.hall_light_motion"]
    await hm.stop()


async def test_phone_buttons_answer_the_question(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    assert hm.learning is not None and hm.router is not None
    await admin_view(hm)  # an administrator with a phone
    await hm._refresh_people()
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await hm.tick()
    delivery = next(d for d in hm.router.deliveries if "Hall light at motion" in d.message)
    yes = next(code for code, label in delivery.actions if label == "Ja, zet uit")
    await ha.press_notification_action("HM|1|forged")  # a fake button does nothing
    assert hm.automations.status[HALL] is Status.OFFERED
    await ha.press_notification_action(yes)
    assert hm.automations.status[HALL] is Status.TURNED_OFF
    await hm.stop()


async def test_own_switch_is_recognised(tmp_path: Path) -> None:
    """HouseMind's own switch-off is not mistaken for a person keeping it (E-115)."""
    hm, _, ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await hm.tick()
    await hm.answer_automation(HALL, True, "demo-user-1")
    # Its own switch-off is not a person keeping the automation off or on (E-115).
    assert hm.automations.status[HALL] is Status.TURNED_OFF
    # An automation that runs a device is the automation, not a person: no "people win".
    assert hm.gate is not None
    await ha._emit("light.hall", "on", {}, Context("c-auto", parent_id="parent"))
    assert "light.hall" not in hm.gate.state.people_win_until
    await hm.stop()


async def test_only_administrators_switch_automations_over_the_websocket(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await hm.tick()
    offer = next(n for n in await hm.notifications() if str(n["topic"]).startswith("automation:"))

    async def said(ws: Any) -> str:
        while True:
            message = await ws.receive_json(timeout=5)
            if message["type"] == "say":
                return str(message["text"])

    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        resident = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-2"})
        await resident.send_json({"type": "automation", "entity": HALL, "turn": "off"})
        await resident.send_json({"type": "notification", "id": int(offer["id"]), "action": "yes"})
        await resident.send_json({"type": "orb_tap"})
        await said(resident)  # the messages before it were handled: nothing happened
        assert ha.states[HALL].state == "on"
        await resident.close()

        admin = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await admin.send_json({"type": "hello", "browserLanguage": "nl"})
        await admin.send_json({"type": "notification", "id": int(offer["id"]), "action": "yes"})
        await admin.send_json({"type": "orb_tap"})
        await said(admin)
        assert ha.states[HALL].state == "off"
        await admin.send_json({"type": "automation", "entity": HALL, "turn": "on"})
        assert "weer aan" in await said(admin)
        assert ha.states[HALL].state == "on"
        await admin.send_json({"type": "get", "what": "settings"})
        while (message := await admin.receive_json(timeout=5))["type"] != "data":
            pass
        rows = message["data"]["automations"]
        assert rows[0]["id"] == HALL and rows[0]["state"] == "kept"
        await admin.close()
    await hm.stop()
