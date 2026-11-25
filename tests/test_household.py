"""Around the house: the oven while away (E-61), the fridge door, and the experimental domains
(E-60) – household, media (E-63), cleaning – off until an administrator turns them on."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.brain.readiness import readiness
from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from tests.helpers import ticks

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")


async def start(
    tmp_path: Path, at: datetime, extra: tuple[tuple[str, str, str, str | None], ...] = ()
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    for entity_id, name, area_id, device_class in extra:  # more devices: id, name, room, type
        ha.model.entities[entity_id] = Entity(
            entity_id, f"r-{entity_id}", name, area_id=area_id, device_class=device_class
        )
        ha.states[entity_id] = State(entity_id, "off", {}, clock.now())
    ha.model.devices["dev.oven"] = Device("dev.oven", "Oven", "kitchen")
    for entity_id, name, device_class, state in (
        ("sensor.oven_status", "Status", None, "inactive"),
        ("binary_sensor.oven_afstandsbediening", "Afstandsbediening", None, "on"),
        ("switch.oven_kinderslot", "Kinderslot", None, "on"),
    ):
        ha.model.entities[entity_id] = Entity(
            entity_id,
            f"r-{entity_id}",
            name,
            device_id="dev.oven",
            area_id="kitchen",
            device_class=device_class,
        )
        ha.states[entity_id] = State(entity_id, state, {}, clock.now())
    ha.model.entities["binary_sensor.fridge_door"] = Entity(
        "binary_sensor.fridge_door", "r-fd", "Koelkast deur", area_id="kitchen", device_class="door"
    )
    ha.states["binary_sensor.fridge_door"] = State(
        "binary_sensor.fridge_door", "off", {}, clock.now()
    )
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, clock, ha


async def leave_home(ha: FakeHomeAssistant) -> None:
    for i in (1, 2, 3):
        await ha.simulate(f"person.resident_{i}", "not_home")


async def test_the_oven_on_while_away_is_reported_but_not_its_child_lock(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    await leave_home(ha)
    await ticks(hm, clock, 25)
    assert not [n for n in ha.notifications if "niemand thuis is" in n[2] and "Oven" in n[2]]
    await ha.simulate("sensor.oven_status", "run")
    await ticks(hm, clock, 2)
    told = [n for n in ha.notifications if n[2].startswith("Oven staat aan")]
    assert told and not [c for c in ha.calls if c[1].startswith("switch.oven")]  # never acts
    await hm.stop()


async def test_a_fridge_door_left_open(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    await ha.simulate("binary_sensor.fridge_door", "on")
    await ticks(hm, clock, 2)
    assert not [n for n in ha.notifications if "Koelkast" in n[2]]
    await ticks(hm, clock, 2)
    assert [n for n in ha.notifications if n[2].startswith("De deur van Koelkast deur")]
    await ha.simulate("binary_sensor.fridge_door", "off")
    await ticks(hm, clock, 1)
    assert not [n for n in await hm.notifications() if str(n["topic"]).startswith("house:fridge")]
    await hm.stop()


async def test_a_ready_washing_machine_only_when_household_is_on(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    await ha.simulate("binary_sensor.washer_running", "on")
    await ticks(hm, clock, 15)
    await ha.simulate("binary_sensor.washer_running", "off")
    await ticks(hm, clock, 1)
    assert not [n for n in ha.notifications if "is klaar" in n[2]]
    assert await hm.set_house_setting("experimental", ["household", "rockets"])
    assert hm.experimental == {"household"}
    await ha.simulate("binary_sensor.washer_running", "on")
    await ticks(hm, clock, 15)
    await ha.simulate("binary_sensor.washer_running", "off")
    await ticks(hm, clock, 1)
    # Information: in the list (and said in an occupied room), not pushed to a phone.
    ready = [n for n in await hm.notifications() if n["text_key"] == "notify.house.appliance_done"]
    assert ready and ready[0]["kind"] == "info"
    assert not [n for n in ha.notifications if n[2].endswith("is klaar.")]
    await hm.stop()


async def test_the_robot_vacuum_only_after_a_yes_for_cleaning(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path, datetime(2026, 10, 19, 10, 0, tzinfo=TZ))
    assert hm.learning is not None
    hm.learning.steps[("vacuum_when_away", "living")] = Step.AUTONOMOUS
    await leave_home(ha)
    await ticks(hm, clock, 20)
    assert ha.states["vacuum.robot"].state == "docked"  # experimental: only watching
    await hm.set_house_setting("experimental", ["cleaning"])
    await ticks(hm, clock, 6)  # the same request is not repeated within five minutes
    assert ha.states["vacuum.robot"].state == "cleaning"
    await ha.simulate("vacuum.robot", "docked")
    await ticks(hm, clock, 30)
    assert ha.states["vacuum.robot"].state == "docked"  # once a day
    await hm.stop()


async def test_a_tv_nobody_watches_is_only_ever_a_proposal(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path, datetime(2026, 10, 19, 20, 0, tzinfo=TZ))
    assert hm.learning is not None
    await hm.set_house_setting("experimental", ["media"])
    hm.learning.steps[("media_off_empty", "living")] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.living_presence", "on")
    await ha.simulate("media_player.living_tv", "on")
    await ha.simulate("binary_sensor.living_presence", "off")
    await ticks(hm, clock, 40)
    assert ha.states["media_player.living_tv"].state == "on"  # never switched off by itself
    proposals = [p for p in await hm.open_proposals() if p.skill == "media_off_empty"]
    assert proposals and proposals[0].action == "media_player.turn_off"
    assert clock.now() - datetime(2026, 10, 19, 20, 0, tzinfo=TZ) >= timedelta(minutes=40)
    await hm.stop()


async def test_only_a_tv_is_switched_off_not_a_doorbell_or_a_speaker(tmp_path: Path) -> None:
    """E-63, from the real house: a doorbell, a camera, a speaker and an amplifier are media
    players too, and HouseMind asked whether it might switch off the doorbell. Now only a TV –
    Home Assistant's type, or no type and a TV word in its name. A room where it once saw
    something else keeps no line for it on 'Klaar voor autonomie?'."""
    extra = (
        ("media_player.living_speaker", "Speaker", "living", "speaker"),
        ("media_player.living_amplifier", "Versterker", "living", "receiver"),
        ("media_player.living_doorbell", "Deurbel", "living", None),
        ("media_player.hall_camera", "Camera", "hall", None),
        ("media_player.bedroom_cast", "Slaapkamer TV", "bedroom", None),
    )
    hm, clock, ha = await start(tmp_path, datetime(2026, 10, 19, 20, 0, tzinfo=TZ), extra)
    assert hm.learning is not None and hm.household is not None
    assert [e.entity_id for e in hm.household.tvs("living")] == ["media_player.living_tv"]
    assert [e.entity_id for e in hm.household.tvs("bedroom")] == ["media_player.bedroom_cast"]
    assert hm.household.tvs("hall") == []
    await hm.set_house_setting("experimental", ["media"])
    hm.learning.steps[("media_off_empty", "living")] = Step.AUTONOMOUS
    hm.learning.first_seen[("media_off_empty", "hall")] = clock.now()  # seen before the fix
    await ha.simulate("binary_sensor.living_presence", "on")
    for entity_id in ("media_player.living_tv", *(e[0] for e in extra)):
        await ha.simulate(entity_id, "on")
    await ha.simulate("binary_sensor.living_presence", "off")
    await ticks(hm, clock, 40)
    asked = {p.entity_id for p in await hm.open_proposals() if p.skill == "media_off_empty"}
    assert asked == {"media_player.living_tv"}
    media = next(p for p in await readiness(hm, "nl", 3, True) if p["domain"] == "media")
    rows = [s for s in media["skills"] if s["skill"] == "media_off_empty"]
    assert [r["area"] for r in rows] == ["living"]
    assert "tv" in rows[0]["when"] and "speaker" not in rows[0]["when"]
    assert "deurbel" not in rows[0]["when"] and "versterker" not in rows[0]["when"]
    await hm.stop()


async def test_a_mower_is_checked_at_every_start_whoever_asks(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """QA 2 (M-4, B-172): the checks of the robot mower (nobody outside, no rain, its hours)
    were only in the skill. A person's yes or a proposal of the AI started it anyway. Now the
    gate checks them at every start; a mower without a room never starts by HouseMind."""
    from housemind.safety.gate import ActionRequest, Layer, Outcome

    clock = SimulatedClock(datetime(2026, 10, 21, 11, 0, tzinfo=UTC))  # 13:00 in Amsterdam
    house = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    assert hm.gate is not None and hm.world is not None
    mower = hm.model.entities["lawn_mower.robot"]

    def ask(layer: Layer) -> ActionRequest:
        return ActionRequest(
            layer,
            "lawn_mower.robot",
            "lawn_mower.start_mowing",
            {},
            "test",
            skill="mower_when_dry",
            created_at=clock.now(),
        )

    assert hm._start_conditions(ask(Layer.PERSON_ANSWER), mower) is None  # dry, midday
    await house.ha.simulate("weather.home", "rainy")
    decision = await hm.gate.submit(ask(Layer.PERSON_ANSWER))
    assert decision.outcome is not Outcome.EXECUTED and decision.reason == "raining"
    await house.ha.simulate("weather.home", "cloudy")
    from housemind.world.world import WorldState

    monkeypatch.setattr(WorldState, "occupied", lambda self, area: area == "garden")
    assert hm._start_conditions(ask(Layer.AI), mower) == "someone_outside"
    monkeypatch.setattr(WorldState, "occupied", lambda self, area: False)
    loose = Entity("lawn_mower.robot", mower.registry_id, mower.name)  # no room
    hm.model.entities["lawn_mower.robot"] = loose
    assert hm._start_conditions(ask(Layer.PERSON_ANSWER), loose) == "no_area"
    hm.model.entities["lawn_mower.robot"] = mower
    await clock.advance(9 * 3600)  # 22:00
    assert hm._start_conditions(ask(Layer.PERSON_ANSWER), mower) == "outside_hours"
    await hm.stop()
