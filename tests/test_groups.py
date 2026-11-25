"""Groups have priority (E-312), the administrator's wish from the real house: a group in the
radio network itself (Zigbee2MQTT, ZHA, Hue, deCONZ) switches its lamps in one radio message,
much faster than HA's own groups or lamp by lamp. And Z-Wave, which has no groups in HA, sends
devices switched together in one multicast. Each test fails without the build."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.brain import groups
from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer, Outcome

ROOT = Path(__file__).resolve().parents[1]
NIGHT = datetime(2026, 10, 19, 20, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
DIMMABLE = {"supported_color_modes": ["brightness"]}


def _lamp(ha: Any, clock: SimulatedClock, entity_id: str, area: str, **extra: Any) -> None:
    ha.model.entities[entity_id] = Entity(
        entity_id, f"r-{entity_id}", entity_id, area_id=area, **extra
    )
    ha.states[entity_id] = State(entity_id, "off", dict(DIMMABLE), clock.now())


def _group(
    ha: Any,
    clock: SimulatedClock,
    entity_id: str,
    members: list[str],
    key: str = "group_entities",
    **extra: Any,
) -> None:
    ha.model.entities[entity_id] = Entity(entity_id, f"r-{entity_id}", entity_id, **extra)
    ha.states[entity_id] = State(entity_id, "off", {key: members, **DIMMABLE}, clock.now())


def _z2m(
    ha: Any, clock: SimulatedClock, entity_id: str, group_id: int, members: list[str] | None
) -> None:
    extra = {"platform": "mqtt", "unique_id": f"{group_id}_light_zigbee2mqtt"}
    if members is None:  # an older HA or Zigbee2MQTT: HA does not list the members
        ha.model.entities[entity_id] = Entity(entity_id, f"r-{entity_id}", entity_id, **extra)
        ha.states[entity_id] = State(entity_id, "off", dict(DIMMABLE), clock.now())
    else:
        _group(ha, clock, entity_id, members, **extra)


async def _house(tmp_path: Path, build: Any) -> tuple[HouseMind, Any, SimulatedClock]:
    clock = SimulatedClock(NIGHT.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    build(house.ha, clock)
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, house.ha, clock


BATH = ["light.wash_1", "light.wash_2", "light.shower_1", "light.shower_2", "light.wc_1"]


def _bathroom(ha: Any, clock: SimulatedClock) -> None:
    """The real house's bathroom: a Zigbee2MQTT group of all five lamps, two smaller ones,
    and – to compare – an HA group of the same five."""
    for lamp in BATH:
        _lamp(ha, clock, lamp, "kitchen")
    _z2m(ha, clock, "light.bath", 8, BATH)
    _z2m(ha, clock, "light.bath_wash", 13, BATH[:2])
    _z2m(ha, clock, "light.bath_shower", 11, BATH[2:4])
    _group(ha, clock, "light.bath_ha", BATH, key="entity_id", platform="group")


async def test_the_whole_room_goes_in_one_zigbee_message(tmp_path: Path) -> None:
    """HouseMind steered through the smallest group: the washbasin, the shower and the WC lamp
    one by one. Now the biggest radio group in one room steers – one message for the whole
    bathroom – over the smaller ones and over HA's own group of the same lamps."""
    hm, ha, _clock = await _house(tmp_path, _bathroom)
    m = hm.model
    assert m.group_source["light.bath"] == "zigbee2mqtt"
    assert m.group_source["light.bath_ha"] == "ha"
    assert all(m.steered_by[lamp] == "light.bath" for lamp in BATH)
    assert hm.skills is not None and hm.learning is not None
    kitchen = {e.entity_id for e in hm.skills._entities("kitchen", "light")}
    assert "light.bath" in kitchen and not kitchen & set(BATH)
    assert not kitchen & {"light.bath_wash", "light.bath_shower", "light.bath_ha"}
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    before = len(ha.calls)
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    bath_calls = [c for c in ha.calls[before:] if c[1].startswith("light.bath") or c[1] in BATH]
    assert bath_calls == [("light.turn_on", "light.bath", bath_calls[0][2])]  # one command
    await hm.stop()


async def test_ha_groups_keep_the_smallest_and_a_group_over_rooms_only_gathers(
    tmp_path: Path,
) -> None:
    """HA's own groups bring no speed: as before the smallest steers, "all lights" gathers;
    a radio group over several rooms never steers either."""

    def build(ha: Any, clock: SimulatedClock) -> None:
        for lamp in ("light.t1", "light.t2"):
            _lamp(ha, clock, lamp, "kitchen")
        _group(
            ha, clock, "light.table", ["light.t1", "light.t2"], key="entity_id", platform="group"
        )
        _group(
            ha,
            clock,
            "light.all",
            ["light.table", "light.kitchen"],
            key="entity_id",
            platform="group",
        )
        _z2m(ha, clock, "light.everywhere", 19, ["light.kitchen", "light.living_reading"])

    hm, _ha, _clock = await _house(tmp_path, build)
    m = hm.model
    assert m.steered_by["light.t1"] == "light.table"
    assert "light.all" not in m.steered_by.values()
    assert "light.everywhere" not in m.group_area  # two rooms
    assert "light.everywhere" not in m.steered_by.values()
    await hm.stop()


def test_each_kind_of_group_is_known() -> None:
    """How each shows in HA (HA, Zigbee2MQTT and ZHA docs and source)."""
    z2m = Entity("light.g", "r", "G", platform="mqtt", unique_id="8_light_zigbee2mqtt")
    z2m_device = Entity("light.d", "r", "D", platform="mqtt", unique_id="0x00_light_zigbee2mqtt")
    zha = Entity("light.z", "r", "Z", platform="zha", unique_id="light_zha_group_0x0002")
    helper = Entity("light.h", "r", "H", platform="group")
    made = Device("dev", "Group", manufacturer="Zigbee2MQTT", model="Group")
    by_device = Entity("light.x", "r", "X", device_id="dev", platform="mqtt", unique_id="x")
    assert groups.source_of(z2m, None, {}) == "zigbee2mqtt"
    assert groups.source_of(z2m_device, None, {}) == "ha"  # a device, not a group
    assert groups.source_of(by_device, made, {}) == "zigbee2mqtt"
    assert groups.source_of(zha, None, {}) == "zha"
    assert groups.source_of(None, None, {"is_hue_group": True}) == "hue"
    assert groups.source_of(None, None, {"is_deconz_group": True}) == "deconz"
    assert groups.source_of(helper, None, {}) == "ha"
    assert groups.members_of({"group_entities": ["a"], "entity_id": ["b"]}) == ["a"]
    assert groups.members_of({"entity_id": ["b"]}) == ["b"]
    assert groups.is_group({"is_deconz_group": True}) and not groups.is_group({})


async def test_zigbee2mqtt_members_come_from_its_own_list_when_ha_does_not_tell(
    tmp_path: Path,
) -> None:
    """An older HA or Zigbee2MQTT lists no members: HouseMind reads Zigbee2MQTT's own retained
    list of groups through HA's MQTT and finds the lamps by their device's ieee address."""

    def build(ha: Any, clock: SimulatedClock) -> None:
        for i, ieee in ((1, "0xaaaa"), (2, "0xbbbb")):
            ha.model.devices[f"dev{i}"] = Device(
                f"dev{i}", f"Lamp {i}", "kitchen", identifiers=(f"mqtt:zigbee2mqtt_{ieee}",)
            )
            _lamp(ha, clock, f"light.z{i}", "kitchen", device_id=f"dev{i}")
        _z2m(ha, clock, "light.zgroup", 21, None)
        ha.mqtt["zigbee2mqtt/bridge/groups"] = [
            {
                "id": 21,
                "friendly_name": "Spots",
                "members": [
                    {"ieee_address": "0xaaaa", "endpoint": 11},
                    {"ieee_address": "0xbbbb", "endpoint": 11},
                ],
            },
            {"id": 901, "friendly_name": "default_bind_group", "members": []},
        ]

    hm, _ha, _clock = await _house(tmp_path, build)
    m = hm.model
    assert m.groups["light.zgroup"] == ("light.z1", "light.z2")
    assert m.group_source["light.zgroup"] == "zigbee2mqtt"
    assert m.steered_by["light.z1"] == "light.zgroup"
    await hm.stop()


async def test_the_gate_answers_for_the_lamps_in_a_group(tmp_path: Path) -> None:
    """A lamp in the group switched by hand, watched by another system or under a house rule
    holds the whole group back – a group command would otherwise overrule that one lamp. Met
    rust laten (E-458, the administrator's choice of 04-11-2026): a lamp ticked by itself goes
    along with the group that steers it; a group ticked holds all that is in it."""
    hm, _ha, clock = await _house(tmp_path, _bathroom)
    assert hm.gate is not None and hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS

    def ask() -> Outcome:
        assert hm.gate is not None
        request = ActionRequest(
            Layer.REFLEX,
            "light.bath",
            "light.turn_on",
            {},
            skill="light_on_entry",
            area_id="kitchen",
            created_at=clock.now(),
        )
        return hm.gate.evaluate(request).outcome

    assert ask() is Outcome.EXECUTED
    hm.gate.state.people_win_until["light.wc_1"] = clock.now() + timedelta(hours=1)
    assert ask() is Outcome.DENIED
    hm.gate.state.people_win_until.clear()
    hm.gate.state.other_system.add("light.wash_2")
    assert ask() is Outcome.EXECUTED  # one lamp of a bigger group does not hold it (E-460) …
    lamps = hm.model.groups["light.bath"]
    hm.gate.state.other_system |= set(lamps[: (len(lamps) + 1) // 2])
    assert ask() is Outcome.OBSERVED  # … at least half of them does: only watch
    hm.gate.state.other_system.clear()
    hm.gate.state.other_system.add("light.bath")
    assert ask() is Outcome.OBSERVED  # and so does the group itself
    hm.gate.state.other_system.clear()
    await hm.set_house_setting("excluded", ["light.shower_1"])
    assert ask() is Outcome.EXECUTED  # the group takes the lamp along …
    assert hm.model.steered_by["light.shower_1"] == "light.bath"
    assert hm.model.unit("light.shower_1") == "light.bath"
    shower_1 = ActionRequest(
        Layer.REFLEX, "light.shower_1", "light.turn_on", {}, created_at=clock.now()
    )
    assert hm.gate.evaluate(shower_1).outcome is Outcome.DENIED  # … never on its own
    await hm.set_house_setting("excluded", ["light.bath_shower"])
    assert ask() is Outcome.DENIED  # a group ticked holds its lamps: no group with them steers
    m = hm.model
    assert {"light.shower_1", "light.shower_2"} <= m.held
    assert m.unit("light.shower_1") is None and m.unit("light.bath_shower") is None
    assert m.steered_by["light.wash_1"] == "light.bath_wash"  # the others go another way
    assert m.unit("light.wc_1") == "light.wc_1"
    await hm.stop()


def _zwave(ha: Any, clock: SimulatedClock) -> None:
    for i in (1, 2, 3):
        _lamp(ha, clock, f"light.zw{i}", "kitchen", platform="zwave_js")


async def test_z_wave_lamps_switched_together_go_in_one_multicast(tmp_path: Path) -> None:
    """Z-Wave has no groups in HA, but devices can be switched in one multicast: the lamps of
    one room going on together are one radio message. Each keeps its own decision."""
    hm, ha, _clock = await _house(tmp_path, _zwave)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    before = len(ha.calls)
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    assert ha.multicasts == [(True, 38, ["light.zw1", "light.zw2", "light.zw3"])]
    assert not [c for c in ha.calls[before:] if c[1].startswith("light.zw")]  # not one by one
    assert all(ha.states[f"light.zw{i}"].state == "on" for i in (1, 2, 3))
    done = [r for r in hm.recent.values() if r.decision.request.entity_id.startswith("light.zw")]
    assert len(done) == 3 and all(r.decision.executed for r in done)
    await hm.stop()


async def test_without_the_multicast_they_go_one_by_one(tmp_path: Path) -> None:
    hm, ha, _clock = await _house(tmp_path, _zwave)
    assert hm.learning is not None
    ha.refuse_multicast = True
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    before = len(ha.calls)
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    one_by_one = [c[1] for c in ha.calls[before:] if c[1].startswith("light.zw")]
    assert sorted(one_by_one) == ["light.zw1", "light.zw2", "light.zw3"]
    assert all(ha.states[f"light.zw{i}"].state == "on" for i in (1, 2, 3))
    await hm.stop()
