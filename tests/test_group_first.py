"""A group goes first, and left alone is left alone (E-458). The administrator's wish
(04-11-2026), with screenshots from the real house: "Ik wil dat HM voorkeur geeft aan groepen,
als het bv gaat om een groep lampen oid. Dan wil ik geen individuele lampen zien in het
logboek, 'Klaar voor autonomie?', beslisspoor, gedachtestroom etc. Maw, devices die met rust
worden gelaten, moeten met rust worden gelaten, en als er een groep aanwezig is, dan heeft deze
de voorkeur."

In the real house a camera saw someone on the driveway in the dark: "Wat ik bedacht" and the
decision trail named six spots one by one, each "niet gedaan", instead of their Zigbee2MQTT
group. And on "Klaar voor autonomie?" the lamps of the dining room were every lamp of its two
groups, and four switches "Charging protection" stood among the lamps of the living room.

His choice (04-11-2026): a lamp ticked "Met rust laten" is never addressed on its own, but a
group that steers it takes it along; a group ticked holds itself and all that is in it. Each
test fails without the change; what it checks is its docstring."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.brain import security
from housemind.brain.briefing import findings
from housemind.brain.cameras import Detection
from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from tests.test_automation_in_row import _row
from tests.test_groups import _house, _lamp, _z2m

ROOT = Path(__file__).resolve().parents[1]
SPOTS = ["light.spot_up", "light.spot_down", "light.door_up", "light.door_down"]
TABLE = ["light.table_1", "light.table_2", "light.table_3"]


def _garden(ha: Any, clock: SimulatedClock) -> None:
    """The real driveway: spots in one Zigbee2MQTT group."""
    for lamp in SPOTS:
        _lamp(ha, clock, lamp, "garden")
    _z2m(ha, clock, "light.spots", 7, SPOTS)


def _dining(ha: Any, clock: SimulatedClock) -> None:
    """The real dining room: a table's lamps in a Zigbee2MQTT group, next to a lamp alone."""
    for lamp in TABLE:
        _lamp(ha, clock, lamp, "kitchen")
    _z2m(ha, clock, "light.table", 9, TABLE)


async def _expert(hm: HouseMind) -> None:
    """The resident on Expert, where the lamps' line shows (whenDetail)."""
    await hm.db.execute("UPDATE people SET level = 4 WHERE user_id = ?", ("demo-user-1",))
    await hm._refresh_people()


def _names(hm: HouseMind, lamps: list[Any]) -> list[str]:
    return [e.entity_id for e in lamps]


async def test_a_person_on_camera_lights_the_group_not_each_spot(tmp_path: Path) -> None:
    """Someone on camera outside in the dark: one decision for the group of spots – one line
    in "Wat ik bedacht" and in the trail – never one per spot."""
    hm, _ha, clock = await _house(tmp_path, _garden)
    assert hm.world is not None and hm.learning is not None
    hm.learning.steps[(security.LIGHT_SKILL, "garden")] = Step.AUTONOMOUS
    seen = Detection("person", "binary_sensor.garden_person", None, "garden", clock.now())
    await security._outside_light(hm, seen, hm.world.snapshot(clock.now()))
    touched = {d.request.entity_id for d in hm.status.thoughts}
    assert "light.spots" in touched
    assert not touched & set(SPOTS)
    thought = " ".join(str(item["text"]) for item in views.mind(hm, "nl", 4))
    assert "light.spots" in thought and "light.spot_up" not in thought
    await hm.stop()


async def test_a_lamp_left_alone_in_a_group_goes_along_with_it(tmp_path: Path) -> None:
    """The administrator ticked lamps of a group to have the group used. The group still
    switches them all in one go, and the lamps' line on "Klaar voor autonomie?" names the
    group, not its lamps – before, the group stopped and every other lamp stood there alone."""
    hm, _ha, _clock = await _house(tmp_path, _dining)
    await _expert(hm)
    await hm.set_house_setting("excluded", ["light.table_1"])
    assert hm.skills is not None and hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    kitchen = _names(hm, hm.skills._entities("kitchen", "light"))
    assert "light.table" in kitchen and not set(kitchen) & set(TABLE)
    detail = (await _row(hm, "light_on_entry", "kitchen"))["whenDetail"]
    lamps = next(p for p in detail.split(" · ") if p.startswith("lampen: "))
    assert "light.table" in lamps and "light.table_" not in lamps
    await hm.stop()


async def test_left_alone_is_left_alone_everywhere(tmp_path: Path) -> None:
    """A lamp ticked on its own, in no group: no request, so no "niet gedaan" in the trail or
    the Logboek, and not in the lamps' line of "Klaar voor autonomie?". It stays only in
    Apparaten, where the tick can be taken away."""
    hm, ha, _clock = await _house(tmp_path, _dining)
    await _expert(hm)
    assert hm.learning is not None and hm.skills is not None
    await hm.set_house_setting("excluded", ["light.kitchen"])
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    touched = {d.request.entity_id for d in hm.status.thoughts}
    assert "light.table" in touched and "light.kitchen" not in touched
    assert "light.kitchen" not in _names(hm, hm.skills._entities("kitchen", "light"))
    detail = (await _row(hm, "light_on_entry", "kitchen"))["whenDetail"]
    lamps = next(p for p in detail.split(" · ") if p.startswith("lampen: "))
    kitchen = hm.model.entities["light.kitchen"].name
    assert kitchen not in lamps
    rows = {row["id"]: row for row in views.devices(hm, "nl")}
    assert rows["light.kitchen"]["step"] == "left_alone"  # Apparaten keeps it, to untick
    await hm.stop()


async def test_a_group_left_alone_holds_all_in_it(tmp_path: Path) -> None:
    """A group ticked: the group and every lamp in it stay still – none in the lamps' list,
    none switched on its own – and Apparaten says why the group only gathers."""
    hm, ha, _clock = await _house(tmp_path, _dining)
    assert hm.learning is not None and hm.skills is not None
    await hm.set_house_setting("excluded", ["light.table"])
    kitchen = _names(hm, hm.skills._entities("kitchen", "light"))
    assert not set(kitchen) & {"light.table", *TABLE}
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    touched = {d.request.entity_id for d in hm.status.thoughts}
    assert not touched & {"light.table", *TABLE}
    rows = {row["id"]: row for row in views.devices(hm, "nl")}
    assert rows["light.table"]["group"]["why"] == "met rust gelaten"
    await hm.stop()


async def test_the_bedtime_check_names_the_group_once(tmp_path: Path) -> None:
    """Lamps still on at bedtime are named by their group, once – not each lamp of the table,
    and not a lamp left alone."""
    hm, ha, clock = await _house(tmp_path, _dining)
    await hm.set_house_setting("excluded", ["light.kitchen"])
    for lamp in [*TABLE, "light.table", "light.kitchen"]:
        ha.states[lamp] = State(lamp, "on", {}, clock.now())
    found = findings(hm.model, ha.states, hm.area_roles, set())
    lamps = [name for kind, name in found if kind == "light"]
    assert lamps.count("light.table") == 1
    assert not set(lamps) & {*TABLE, hm.model.entities["light.kitchen"].name}
    await hm.stop()


async def test_a_setting_switch_is_no_lamp(tmp_path: Path) -> None:
    """A plug with its own switch and a second one, "Charging protection", that a guess took
    for a lamp: a switch with a name of its own next to the device's own switch is a setting,
    never in a list of lamps."""

    def build(ha: Any, clock: SimulatedClock) -> None:
        ha.model.devices["plug"] = Device("plug", "Plug", "kitchen")
        plug = (("switch.plug", None), ("switch.plug_protect", "Charging protection"))
        for entity_id, own in plug:
            ha.model.entities[entity_id] = Entity(
                entity_id, f"r-{entity_id}", own or "Plug", device_id="plug", original_name=own
            )
            ha.states[entity_id] = State(entity_id, "off", {}, clock.now())

    hm, _ha, _clock = await _house(tmp_path, build)
    assert hm.gate is not None and hm.skills is not None
    hm.gate.state.roles["switch.plug_protect"] = "light"
    hm.gate.state.derived.add("switch.plug_protect")  # a guess from behaviour
    hm._index_functions()
    assert "switch.plug_protect" not in _names(hm, hm.skills._entities("kitchen", "light"))
    await hm.stop()


async def test_the_demo_has_each_kind_of_group(tmp_path: Path) -> None:
    """The administrator's wish (04-11-2026): in the demo, next to a Zigbee2MQTT group, the
    other kinds too – a Hue room, a ZHA group, a deCONZ group and groups made in Home Assistant –
    as Home Assistant shows them. Each is known in Apparaten by its kind; those whose lamps HA
    lists in one room steer them, deCONZ (HA lists no lamps) and one over rooms only gather."""
    when = datetime(2026, 10, 19, 19, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))
    clock = SimulatedClock(when.astimezone(UTC))
    demo = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    m = hm.model
    kinds = {g: m.group_source[g] for g in m.groups}
    assert set(kinds.values()) == {"zigbee2mqtt", "zha", "hue", "deconz", "ha"}
    steering = {kinds[g] for g in m.steering}
    assert steering == {"zigbee2mqtt", "zha", "hue", "ha"}
    deconz = next(g for g, k in kinds.items() if k == "deconz")
    assert m.groups[deconz] == () and deconz not in m.steering
    rows = {row["id"]: row for row in views.devices(hm, "nl")}
    names = {rows[g]["group"]["sourceName"] for g in m.groups}
    assert {"Hue-ruimte of -zone", "Zigbee-groep (ZHA)", "deCONZ-groep"} <= names
    assert hm.skills is not None
    for group in m.steering:  # the room has the group, never its lamps one by one
        area = m.area_of(group)
        assert area is not None
        lamps = _names(hm, hm.skills._entities(area.id, "light"))
        assert group in lamps and not set(lamps) & set(m.groups[group])
    await hm.stop()
