from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Entity, HouseModel, State, StateChange
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Catalog, Step
from housemind.safety.emergency import Emergency, EmergencyReactions
from housemind.safety.gate import ActionRequest, GateState, Layer, Outcome, SafetyGate
from housemind.safety.hard_limits import hard_limit_reason

T0 = datetime(2026, 10, 17, 19, 0, tzinfo=UTC)

ENTITIES: list[tuple[str, dict[str, Any], str]] = [
    (
        "light.kitchen",
        {"capabilities": {"supported_color_modes": ["brightness"]}, "area_id": "kitchen"},
        "off",
    ),
    (
        "light.hall",
        {"capabilities": {"supported_color_modes": ["onoff"]}, "area_id": "hall"},
        "off",
    ),
    ("lock.front", {"area_id": "hall"}, "locked"),
    ("cover.garage", {"device_class": "garage"}, "closed"),
    ("cover.living", {"device_class": "curtain", "area_id": "living"}, "open"),
    ("alarm_control_panel.home", {}, "armed_away"),
    ("valve.water_main", {"device_class": "water"}, "open"),
    ("valve.gas_main", {"device_class": "gas"}, "open"),
    ("switch.plug", {}, "off"),
    ("switch.gate_relay", {}, "off"),
    ("fan.bathroom", {"area_id": "bath"}, "on"),
    ("climate.living", {"area_id": "living"}, "heat"),
    ("binary_sensor.smoke_kitchen", {"device_class": "smoke", "area_id": "kitchen"}, "off"),
    ("binary_sensor.leak_utility", {"device_class": "moisture"}, "off"),
    ("binary_sensor.gas_kitchen", {"device_class": "gas"}, "off"),
]


def build(
    catalog: Catalog, state: GateState | None = None
) -> tuple[SafetyGate, FakeHomeAssistant, SimulatedClock]:
    clock = SimulatedClock(T0)
    model = HouseModel(
        areas={a: Area(a, a.title()) for a in ("kitchen", "hall", "living", "bath")},
        entities={
            eid: Entity(eid, f"reg-{eid}", eid, **kw)  # type: ignore[arg-type]
            for eid, kw, _ in ENTITIES
        },
    )
    states = {eid: State(eid, s, {}, T0) for eid, _, s in ENTITIES}
    ha = FakeHomeAssistant(clock, model, states)
    gate_state = state or GateState(
        roles={"switch.gate_relay": "gate_opener"},
        skill_step=lambda skill, area: Step.AUTONOMOUS,
    )
    return SafetyGate(clock, ha, catalog, model, gate_state), ha, clock


def req(layer: Layer, entity_id: str, action: str, **data: Any) -> ActionRequest:
    return ActionRequest(layer, entity_id, action, data, skill="test_skill")


# -- hard limits -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entity", "action", "data", "role", "expected"),
    [
        (
            Entity("alarm_control_panel.a", "r", "a"),
            "alarm_control_panel.alarm_disarm",
            {},
            None,
            "alarm_disarm",
        ),
        (Entity("lock.a", "r", "a"), "lock.unlock", {}, None, "lock_open"),
        (Entity("lock.a", "r", "a"), "lock.open", {}, None, "lock_open"),
        (
            Entity("cover.g", "r", "g", device_class="garage"),
            "cover.open_cover",
            {},
            None,
            "garage_gate_door_open",
        ),
        (
            Entity("cover.g", "r", "g", device_class="gate"),
            "cover.set_cover_position",
            {"position": 10},
            None,
            "garage_gate_door_open",
        ),
        (Entity("cover.g", "r", "g", device_class="garage"), "cover.close_cover", {}, None, None),
        (
            Entity("switch.r", "r", "r"),
            "switch.turn_on",
            {},
            "gate_opener",
            "garage_gate_door_open",
        ),
        (
            Entity("switch.r", "r", "r"),
            "switch.turn_off",
            {},
            "garage_opener",
            "garage_gate_door_open",
        ),
        (Entity("cover.c", "r", "c", device_class="curtain"), "cover.open_cover", {}, None, None),
        (Entity("lock.a", "r", "a"), "lock.lock", {}, None, None),
    ],
)
def test_hard_limits(
    entity: Entity, action: str, data: dict[str, Any], role: str | None, expected: str | None
) -> None:
    assert hard_limit_reason(entity, action, data, role) == expected


def test_admin_can_protect_more() -> None:
    assert (
        hard_limit_reason(Entity("light.x", "r", "x"), "light.turn_on", {}, None, {"light.x"})
        == "protected_by_admin"
    )


# -- the gate ------------------------------------------------------------------------------


async def test_gate_executes_allowed_action(catalog: Catalog) -> None:
    gate, ha, _ = build(catalog)
    decision = await gate.submit(
        req(Layer.REFLEX, "light.kitchen", "light.turn_on", brightness_pct=60)
    )
    assert decision.outcome is Outcome.EXECUTED, decision
    assert ha.states["light.kitchen"].state == "on"
    assert [c.name for c in decision.checks] == [
        "1_valid",
        "2_allowed",
        "3_now",
        "4_step",
        "5_sensible",
        "6_done",
    ]


@pytest.mark.parametrize(
    ("entity", "action", "data", "reason"),
    [
        ("light.unknown", "light.turn_on", {}, "unknown_entity"),
        ("light.kitchen", "switch.turn_on", {}, "action_domain_mismatch"),
        (
            "light.kitchen",
            "light.turn_on",
            {"brightness_pct": 400},
            "value_out_of_range:brightness_pct",
        ),
        ("light.kitchen", "light.toggle", {}, "action_not_allowed"),
        ("lock.front", "lock.unlock", {}, "hard_limit:lock_open"),
        ("cover.garage", "cover.open_cover", {}, "hard_limit:garage_gate_door_open"),
        (
            "alarm_control_panel.home",
            "alarm_control_panel.alarm_disarm",
            {},
            "hard_limit:alarm_disarm",
        ),
        ("switch.gate_relay", "switch.turn_on", {}, "hard_limit:garage_gate_door_open"),
        ("valve.water_main", "valve.open_valve", {}, "hard_limit:valve_open"),  # QA 2, B-168
    ],
)
async def test_gate_denies_with_reason(
    catalog: Catalog, entity: str, action: str, data: dict[str, Any], reason: str
) -> None:
    gate, ha, _ = build(catalog)
    decision = await gate.submit(req(Layer.REFLEX, entity, action, **data))
    assert decision.outcome is Outcome.DENIED
    assert decision.reason == reason
    assert ha.calls == []


async def test_switch_without_role_is_only_observed(catalog: Catalog) -> None:
    gate, ha, _ = build(catalog)
    # switch.generic has no actions at all, so it is denied – never executed.
    decision = await gate.submit(req(Layer.REFLEX, "switch.plug", "switch.turn_on"))
    assert decision.outcome is Outcome.DENIED and ha.calls == []


async def test_garage_close_is_proposal_then_person_can_approve(catalog: Catalog) -> None:
    gate, ha, _ = build(catalog)
    assert (
        await gate.submit(req(Layer.REFLEX, "cover.garage", "cover.close_cover"))
    ).outcome is Outcome.PROPOSED
    approved = await gate.submit(req(Layer.PERSON_ANSWER, "cover.garage", "cover.close_cover"))
    assert approved.outcome is Outcome.EXECUTED
    assert ha.states["cover.garage"].state == "closed"


async def test_ai_never_acts_directly(catalog: Catalog) -> None:
    gate, ha, _ = build(catalog)
    decision = await gate.submit(req(Layer.AI, "light.kitchen", "light.turn_on"))
    assert decision.outcome is Outcome.PROPOSED and ha.calls == []


async def test_skill_step_limits_learning(catalog: Catalog) -> None:
    state = GateState(skill_step=lambda skill, area: Step.OBSERVE)
    gate, ha, _ = build(catalog, state)
    decision = await gate.submit(req(Layer.LEARNING, "light.kitchen", "light.turn_on"))
    assert decision.outcome is Outcome.OBSERVED and ha.calls == []


async def test_pause_and_people_win_block_but_not_emergency(catalog: Catalog) -> None:
    gate, _ha, clock = build(catalog)
    gate.state.paused_until = T0 + timedelta(hours=1)
    assert (
        await gate.submit(req(Layer.REFLEX, "light.kitchen", "light.turn_on"))
    ).reason == "paused"
    assert (await gate.submit(req(Layer.EMERGENCY, "light.kitchen", "light.turn_on"))).executed
    gate.state.paused_until = None
    gate.state.people_win_until["light.hall"] = T0 + timedelta(hours=2)
    assert (
        await gate.submit(req(Layer.REFLEX, "light.hall", "light.turn_on"))
    ).reason == "people_win"
    await clock.advance(3 * 3600)
    assert (await gate.submit(req(Layer.REFLEX, "light.hall", "light.turn_on"))).executed


async def test_other_system_and_house_rule(catalog: Catalog) -> None:
    gate, _, _ = build(catalog)
    gate.state.other_system.add("climate.living")
    assert (
        await gate.submit(
            req(Layer.PLANNER, "climate.living", "climate.set_temperature", temperature=20)
        )
    ).reason == "other_system"
    gate.state.house_rule_veto = lambda r: (
        "never_before_7" if r.entity_id == "cover.living" else None
    )
    assert (
        await gate.submit(req(Layer.REFLEX, "cover.living", "cover.close_cover"))
    ).reason == "house_rule:never_before_7"


async def test_rate_and_daily_limits(catalog: Catalog) -> None:
    gate, _, clock = build(catalog)
    first = await gate.submit(
        req(Layer.PLANNER, "climate.living", "climate.set_temperature", temperature=20)
    )
    assert first.executed
    again = await gate.submit(
        req(Layer.PLANNER, "climate.living", "climate.set_temperature", temperature=21)
    )
    assert again.reason == "rate_limit"
    for _ in range(9):
        await clock.advance(2700)
        assert (
            await gate.submit(
                req(Layer.PLANNER, "climate.living", "climate.set_temperature", temperature=20)
            )
        ).executed
    await clock.advance(2700)
    if clock.now().date() == T0.date():
        assert (
            await gate.submit(
                req(Layer.PLANNER, "climate.living", "climate.set_temperature", temperature=20)
            )
        ).reason == "daily_limit"


async def test_untested_ha_downgrades_careful_devices(catalog: Catalog) -> None:
    gate, _, _ = build(catalog)
    gate.state.untested_ha = True
    assert (
        await gate.submit(req(Layer.REFLEX, "cover.living", "cover.close_cover"))
    ).outcome is Outcome.PROPOSED
    assert (await gate.submit(req(Layer.REFLEX, "light.kitchen", "light.turn_on"))).executed


async def test_stale_request_is_refused(catalog: Catalog) -> None:
    gate, _, clock = build(catalog)
    request = ActionRequest(Layer.REFLEX, "light.kitchen", "light.turn_on", created_at=clock.now())
    await clock.advance(10)
    assert (await gate.submit(request)).reason == "stale_context"


async def test_failed_device_is_reported(catalog: Catalog) -> None:
    gate, ha, _ = build(catalog)
    ha.failing_entities.add("light.kitchen")
    decision = await gate.submit(req(Layer.REFLEX, "light.kitchen", "light.turn_on"))
    assert decision.outcome is Outcome.FAILED and decision.reason == "device_did_not_respond"


# -- property tests: whatever comes in, the hard limits hold ------------------------------

ACTIONS = st.sampled_from(
    [
        "lock.unlock",
        "lock.open",
        "lock.lock",
        "alarm_control_panel.alarm_disarm",
        "alarm_control_panel.alarm_arm_away",
        "cover.open_cover",
        "cover.close_cover",
        "cover.set_cover_position",
        "cover.toggle",
        "switch.turn_on",
        "switch.turn_off",
        "light.turn_on",
        "valve.open_valve",
        "valve.close_valve",
        "siren.turn_on",
    ]
)


@settings(
    max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None
)
@given(
    layer=st.sampled_from(list(Layer)),
    entity_id=st.sampled_from([e for e, _, _ in ENTITIES] + ["lock.ghost", "cover.ghost"]),
    action=ACTIONS,
    position=st.integers(-50, 200),
)
def test_hard_limits_hold_for_every_request(
    catalog: Catalog, layer: Layer, entity_id: str, action: str, position: int
) -> None:
    gate, _, _ = build(catalog)
    data = {"position": position} if action == "cover.set_cover_position" else {}
    decision = gate.evaluate(ActionRequest(layer, entity_id, action, data, skill="s"))
    if decision.outcome is Outcome.EXECUTED:
        entity = gate.model.entities[entity_id]
        assert hard_limit_reason(entity, action, data, gate.state.roles.get(entity_id)) is None
        assert action not in (
            "lock.unlock",
            "lock.open",
            "alarm_control_panel.alarm_disarm",
            "valve.open_valve",
        )
        assert not (
            entity_id in ("cover.garage", "switch.gate_relay") and action != "cover.close_cover"
        )
        assert layer is not Layer.AI


# -- emergency reactions ------------------------------------------------------------------


def _change(entity_id: str, old: str, new: str) -> StateChange:
    return StateChange(entity_id, State(entity_id, old, {}, T0), State(entity_id, new, {}, T0))


async def test_smoke_turns_on_lights_and_stops_ventilation(catalog: Catalog) -> None:
    gate, ha, clock = build(catalog)
    gate.state.paused_until = T0 + timedelta(hours=5)
    gate.state.people_win_until["light.kitchen"] = T0 + timedelta(hours=5)
    reactions = EmergencyReactions(clock, catalog, gate.model)
    event = reactions.react(_change("binary_sensor.smoke_kitchen", "off", "on"))
    assert event is not None and event.emergency is Emergency.SMOKE
    for request in event.requests:
        assert (await gate.submit(request)).executed
    assert ha.states["light.kitchen"].state == "on"
    assert ha.states["light.hall"].state == "on"
    assert ha.states["fan.bathroom"].state == "off"
    assert ha.states["lock.front"].state == "locked"  # never opened (B-21)
    # Same state again is not a new emergency.
    assert reactions.react(_change("binary_sensor.smoke_kitchen", "on", "on")) is None


async def test_gas_closes_valve_without_switching(catalog: Catalog) -> None:
    gate, _, clock = build(catalog)
    event = EmergencyReactions(clock, catalog, gate.model).react(
        _change("binary_sensor.gas_kitchen", "off", "on")
    )
    assert event is not None
    assert [(r.entity_id, r.action) for r in event.requests] == [
        ("valve.gas_main", "valve.close_valve")
    ]


async def test_leak_closes_water_valve(catalog: Catalog) -> None:
    gate, ha, clock = build(catalog)
    event = EmergencyReactions(clock, catalog, gate.model).react(
        _change("binary_sensor.leak_utility", "off", "on")
    )
    assert event is not None
    for request in event.requests:
        assert (await gate.submit(request)).executed
    assert ha.states["valve.water_main"].state == "closed"
