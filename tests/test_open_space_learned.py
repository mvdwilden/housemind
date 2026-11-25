"""Zones are rooms of their own; whether two rooms share their light is learned (E-530), from
the user's side. Found in the real house on 23 November: walking through the dining part of an
open kitchen, HouseMind put the dining light on – and twenty seconds later, in the kitchen, the
kitchen light stayed off, because a lamp in one room of an open space was light in the other.
Each test fails without the change; what went wrong is in its docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Device, Entity, Source, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.server.views import readiness
from housemind.world.house_mode import HouseMode
from tests.helpers import wait_seconds as _wait

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 11, 23, 17, 15, tzinfo=ZoneInfo("Europe/Amsterdam"))
RANGE, KITCHEN, DINING = (
    "binary_sensor.k_range",
    "binary_sensor.k_kitchen",
    "binary_sensor.k_dining",
)
KITCHEN_LAMP, DINING_LAMP = "light.kitchen", "light.dining"


async def _house(tmp_path: Path) -> tuple[HouseMind, Any, SimulatedClock]:
    """An open kitchen: one presence sensor in the kitchen, with a zone for the kitchen and one
    for the dining room, and a lamp in each room."""
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    m = ha.model
    m.areas["dining"] = Area("dining", "Eetkamer", "ground")
    m.devices["k"] = Device("k", "Sensor", "kitchen")
    for eid, name in (
        (RANGE, "Aanwezigheidssensor"),
        (KITCHEN, "Zone: Keuken"),
        (DINING, "Zone: Eetkamer"),
    ):
        m.entities[eid] = Entity(eid, f"r-{eid}", name, device_id="k", device_class="occupancy")
        ha.states[eid] = State(eid, "off", {}, clock.now())
    m.entities[DINING_LAMP] = Entity(DINING_LAMP, "r-dining", "Eetkamer lamp", area_id="dining")
    ha.states[DINING_LAMP] = State(DINING_LAMP, "off", {}, clock.now())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 4, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    await hm.set_house_setting("consent_learning", True)
    assert hm.learning is not None and hm.world is not None
    for area in ("kitchen", "dining"):
        for skill in ("light_on_entry", "light_off_leave"):
            hm.learning.steps[(skill, area)] = Step.AUTONOMOUS
    hm.world.modes.mode = HouseMode.HOME
    return hm, ha, clock


async def _dining_lit(hm: HouseMind, ha: Any, clock: SimulatedClock) -> None:
    """Someone walks through the dining part: HouseMind puts its light on."""
    await ha.simulate(RANGE, "on")
    await ha.simulate(DINING, "on")
    await _wait(hm, clock, 10)
    assert ha.states[DINING_LAMP].state == "on"
    assert ha.states[KITCHEN_LAMP].state == "off"


async def _into_kitchen(hm: HouseMind, ha: Any, clock: SimulatedClock) -> None:
    await _wait(hm, clock, 20)
    await ha.simulate(DINING, "off")
    await ha.simulate(KITCHEN, "on")
    await _wait(hm, clock, 10)


async def _out_of_kitchen(hm: HouseMind, ha: Any, clock: SimulatedClock) -> None:
    """Back to the dining part, long enough for the next time to be a new coming in – and for
    HouseMind to ask the same of a lamp again (five minutes)."""
    await ha.simulate(KITCHEN, "off")
    await ha.simulate(DINING, "on")
    await _wait(hm, clock, 6 * 60)


async def test_the_kitchen_gets_its_own_light_after_the_dining_room(tmp_path: Path) -> None:
    """The real house: through the dining part into the kitchen in the dark – the dining light
    went on, the kitchen light stayed off."""
    hm, ha, clock = await _house(tmp_path)
    await _dining_lit(hm, ha, clock)
    await _into_kitchen(hm, ha, clock)
    assert ha.states[KITCHEN_LAMP].state == "on"
    assert ha.states[DINING_LAMP].state == "on"  # the dining room keeps its own light
    await hm.stop()


async def test_in_range_in_no_zone_is_not_coming_into_the_kitchen(tmp_path: Path) -> None:
    """At the dining table the zone lets go for a while; the sensor still sees someone in
    range. With every zone a room of its own, that switched the kitchen light on. Someone in
    range in none of the zones is not in the kitchen, which has its own zone: its light stays
    off, while nothing in the open space goes off either."""
    hm, ha, clock = await _house(tmp_path)
    await _dining_lit(hm, ha, clock)
    await ha.simulate(DINING, "off")  # the zone lets go, the person is still in range
    await _wait(hm, clock, 60)
    assert hm.world is not None and hm.world.snapshot(clock.now()).occupied("kitchen")
    assert ha.states[KITCHEN_LAMP].state == "off"
    assert ha.states[DINING_LAMP].state == "on"
    await hm.stop()


async def test_putting_out_the_kitchen_light_teaches_one_open_space(tmp_path: Path) -> None:
    """Where people keep putting out the kitchen light that HouseMind gave them while the
    dining light is on, that light does for the kitchen: the next time it stays off. It is
    learned per house – no setting – and it is no "not like that" that sends the kitchen's
    light back to Voorstellen."""
    hm, ha, clock = await _house(tmp_path)
    assert hm.learning is not None
    await _dining_lit(hm, ha, clock)
    for _ in range(4):
        await _into_kitchen(hm, ha, clock)
        assert ha.states[KITCHEN_LAMP].state == "on"
        await ha.simulate(KITCHEN_LAMP, "off", by=Source.PERSON)
        await _wait(hm, clock, 20)
        await _out_of_kitchen(hm, ha, clock)
    assert hm.learning.shares_light("kitchen", "dining", clock.now())
    assert hm.learning.steps[("light_on_entry", "kitchen")] is Step.AUTONOMOUS
    await _into_kitchen(hm, ha, clock)
    assert ha.states[KITCHEN_LAMP].state == "off"  # the dining light does here
    rows = [s for p in await readiness(hm, "nl", 4, True) for s in p.get("skills", [])]
    card = next(r for r in rows if r["skill"] == "light_on_entry" and r["area"] == "kitchen")
    assert "het licht in Eetkamer is hier genoeg" in card["whenDetail"], card["whenDetail"]
    await hm.stop()


async def test_switching_the_kitchen_light_on_yourself_makes_it_a_room_again(
    tmp_path: Path,
) -> None:
    """Once learned as one open space, people who then switch the kitchen light on themselves
    show it is a room of its own again; HouseMind follows them."""
    hm, ha, clock = await _house(tmp_path)
    learning = hm.learning
    assert learning is not None
    there = learning.someone_there
    learning.someone_there = lambda area_id: True  # four times staying with the dining light
    for _ in range(4):
        learning.open_entry("kitchen", [KITCHEN_LAMP], ["dining"], clock.now())
        learning.expire(clock.now() + timedelta(minutes=5))
    learning.someone_there = there
    assert learning.shares_light("kitchen", "dining", clock.now())
    await _dining_lit(hm, ha, clock)
    for _ in range(2):
        await _into_kitchen(hm, ha, clock)
        assert ha.states[KITCHEN_LAMP].state == "off"
        await ha.simulate(KITCHEN_LAMP, "on", by=Source.PERSON)
        await _wait(hm, clock, 20)
        await ha.simulate(KITCHEN_LAMP, "off", by=Source.PERSON)
        await _out_of_kitchen(hm, ha, clock)
    assert not learning.shares_light("kitchen", "dining", clock.now())
    await _into_kitchen(hm, ha, clock)
    assert ha.states[KITCHEN_LAMP].state == "on"
    rows = [s for p in await readiness(hm, "nl", 4, True) for s in p.get("skills", [])]
    card = next(r for r in rows if r["skill"] == "light_on_entry" and r["area"] == "kitchen")
    assert "eigen licht, ook als het in Eetkamer brandt" in card["whenDetail"], card["whenDetail"]
    await hm.stop()


async def test_test_the_house_knows_the_rooms_of_the_zones(tmp_path: Path) -> None:
    """In the real house Test het huis took a room's sensors from the room Home Assistant gives
    them: the dining room, whose zone is on the kitchen sensor, had no Iemand or Niemand, and
    Iemand in the kitchen switched the dining room's zone on as well."""
    hm, _ha, _clock = await _house(tmp_path)
    assert hm._test_room("dining").presence == (DINING,)
    assert DINING not in hm._test_room("kitchen").presence
    keys = [s["key"] for s in hm.test_view("nl", "dining", [])["situations"]]
    assert "someone" in keys and "nobody" in keys
    lines = [line["text"] for line in hm.test_view("nl", "dining", ["someone", "dark"])["lines"]]
    assert any("Eetkamer lamp" in text for text in lines), lines
    await hm.stop()
