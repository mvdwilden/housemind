"""Round 5, from the full retest: the bedtime message, the Orb per viewer, emergencies at
start-up, the untested HA version, garage doors. Each test fails without the fix."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.notify import Kind
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer, Outcome
from housemind.server.web import say
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 10, 19, 21, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


async def _start(tmp_path: Path, extra: list[tuple[Entity, str]] | None = None) -> tuple:
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    for entity, value in extra or []:
        ha.model.entities[entity.entity_id] = entity
        ha.states[entity.entity_id] = State(entity.entity_id, value, {}, clock.now())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, ha, clock


async def test_the_bedtime_message_goes_away(tmp_path: Path) -> None:
    """It never went away and kept the Orb orange for days."""
    hm, ha, clock = await _start(tmp_path)
    assert hm.world is not None
    snap = hm.world.snapshot(clock.now())
    await hm._raise(Kind.IMPORTANT, "bedtime:2026-10-19", "notify.learned", {"what": "x"}, snap)
    await ha.simulate("binary_sensor.bedroom_window", "on")
    await clock.advance(60)
    await hm.tick()
    assert "bedtime:2026-10-19" in hm._raised  # the window is still open
    await ha.simulate("binary_sensor.bedroom_window", "off")
    await clock.advance(60)
    await hm.tick()
    assert "bedtime:2026-10-19" not in hm._raised  # everything closed: gone
    await hm._raise(Kind.IMPORTANT, "bedtime:2026-10-20", "notify.learned", {"what": "x"}, snap)
    await ha.simulate("binary_sensor.bedroom_window", "on")
    await clock.advance(int(timedelta(hours=11).total_seconds()))
    await hm.tick()
    assert "bedtime:2026-10-20" not in hm._raised  # at the latest the next morning
    await hm.stop()


async def test_the_orb_per_viewer_and_what_a_tap_says(tmp_path: Path) -> None:
    """A message for administrators coloured everyone's Orb; a question kept it amber for
    days, also on level 1; a hot socket's emergency message left it calm; violet flashed at
    night; a tap on an orange Orb said "All is calm"."""
    hm, _ha, clock = await _start(tmp_path)
    assert hm.world is not None
    snap = hm.world.snapshot(clock.now())
    hm.world.modes.mode = HouseMode.HOME  # awake (the demo family sleeps at 21:00)
    await hm._raise(
        Kind.IMPORTANT, "admin:only", "notify.learned", {"what": "budget"}, snap, concerns="admins"
    )
    assert hm.orb_state(admin=True) == "attention"
    assert hm.orb_state(admin=False) == "calm"
    words = await say(hm, "nl", admin=True)
    assert words.startswith("Let op:") and "budget" in words
    await hm._resolve("admin:only")
    await hm._raise(Kind.LEARNING, "proposal:1", "notify.learned", {"what": "q"}, snap)
    assert hm.orb_state(level=2) == "learning"
    assert hm.orb_state(level=1) == "calm"
    await clock.advance(int(timedelta(hours=1, minutes=1).total_seconds()))
    assert hm.orb_state(level=2) == "calm"  # amber for an hour, not for days
    await hm._raise(Kind.EMERGENCY, "device_hot:x", "notify.learned", {"what": "hot"}, snap)
    hm.world.modes.mode = HouseMode.SLEEP
    assert hm.orb_state() == "emergency"  # also at night; red since E-284 (20-10-2026)
    await hm._resolve("device_hot:x")
    hm.status.thinking_until = clock.now() + timedelta(seconds=4)
    assert hm.orb_state() == "sleeping"  # no violet flash at night
    await hm.stop()


async def test_a_chosen_pause_shows_grey_also_while_a_message_waits(tmp_path: Path) -> None:
    """In the real house Vesta stayed orange ("Let op") after a pause, because a message was
    waiting. A pause someone chose now shows grey and a tap says so; danger still wins."""
    hm, _ha, clock = await _start(tmp_path)
    assert hm.world is not None
    snap = hm.world.snapshot(clock.now())
    hm.world.modes.mode = HouseMode.HOME
    await hm._raise(Kind.IMPORTANT, "battery:x", "notify.learned", {"what": "battery"}, snap)
    assert hm.orb_state() == "attention"
    await hm.pause(60)
    assert hm.orb_state() == "paused"
    assert (await say(hm, "nl")) == hm.languages.text("nl", "say.paused")
    await hm._raise(Kind.EMERGENCY, "device_hot:x", "notify.learned", {"what": "hot"}, snap)
    assert hm.orb_state() == "emergency"  # real danger shows, also during a pause (E-284)
    await hm.stop()


async def test_an_emergency_already_on_at_start_gets_its_reaction(tmp_path: Path) -> None:
    """A leak that began during a restart got no valve and no message."""
    valve = Entity("valve.main", "r-v", "Hoofdkraan", area_id="kitchen", device_class="water")
    leak = Entity(
        "binary_sensor.sink_leak", "r-l", "Lek", area_id="kitchen", device_class="moisture"
    )
    hm, ha, _clock = await _start(tmp_path, [(valve, "open"), (leak, "on")])
    assert ha.states["valve.main"].state == "closed"
    assert "emergency:binary_sensor.sink_leak" in hm._raised
    await hm.stop()


async def test_untested_ha_is_not_bypassed_by_a_trial(tmp_path: Path) -> None:
    """On level Carefree a trial still set the thermostat on an untested HA version."""
    hm, _ha, clock = await _start(tmp_path)
    assert hm.gate is not None and hm.learning is not None
    hm.learning.steps[("heat_on_presence", "living")] = Step.PROPOSE  # a trial would act
    hm.gate.state.untested_ha = True
    decision = await hm.gate.submit(
        ActionRequest(
            Layer.TRIAL,
            "climate.living",
            "climate.set_temperature",
            {"temperature": 19.5},
            skill="heat_on_presence",
            area_id="living",
            created_at=clock.now(),
        )
    )
    assert decision.outcome is not Outcome.EXECUTED
    await hm.stop()


async def test_a_garage_door_is_protected_whatever_ha_calls_it(tmp_path: Path) -> None:
    """A garage roller door with the class "shutter" could be opened."""
    door = Entity("cover.garage_roller", "r-g", "Roldeur", area_id="garage", device_class="shutter")
    hm, _ha, clock = await _start(tmp_path, [(door, "closed")])
    assert hm.gate is not None
    decision = await hm.gate.submit(
        ActionRequest(
            Layer.PERSON_ANSWER,
            "cover.garage_roller",
            "cover.open_cover",
            {},
            created_at=clock.now(),
        )
    )
    assert decision.outcome is not Outcome.EXECUTED and decision.reason.startswith("hard_limit")
    await hm.stop()
