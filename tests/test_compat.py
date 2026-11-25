"""Home Assistant restarting, updating and changing (H7.4, H7.5): HouseMind waits, looks
again, checks itself (E-152) and is careful on a version newer than tested (E-142, E-155)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.knowledge.compat import Compatibility, load_compatibility, month_of
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import Outcome
from housemind.server import views
from tests.helpers import ticks

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
EVENING = datetime(2026, 10, 19, 20, 0, tzinfo=TZ)


async def start(
    tmp_path: Path, version: str = "2026.10.0"
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    house.ha.config = replace(house.ha.config, version=version)
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    for user, level in (("demo-user-1", 1), ("demo-user-2", 3)):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (user, user, level, json.dumps({}), clock.now().isoformat()),
        )
    await hm._refresh_people()
    return hm, clock, house.ha


def test_versions_are_compared_by_year_and_month() -> None:
    assert month_of("2026.10.2") == (2026, 10)
    assert month_of("2026.11.0b3") == (2026, 11)
    assert month_of("2027.1.0.dev20261201") == (2027, 1)
    assert month_of("dev") is None
    compat = load_compatibility([ROOT / "knowledge"])
    assert compat.status("2026.10.2") == "tested"
    assert compat.status("2026.6.1") == "tested"  # between the oldest and the newest tested
    assert compat.status("2099.1.0") == "newer"
    assert compat.status("2025.12.0") == "older"
    assert Compatibility().status("2026.10.0") == "unknown"


async def test_while_home_assistant_restarts_house_mind_waits(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path)
    await ha.drop()
    assert hm.status.ha_lost is not None and hm.status.orb == "attention"
    carefree = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    involved = await views.load_viewer(hm, "demo-user-2", None, trusted_local=False)
    calm = await views.snapshot(hm, carefree)
    told = await views.snapshot(hm, involved)
    assert calm["orb"] == "calm" and "Home Assistant" not in calm["line"]  # level 1: calm
    assert told["orb"] == "attention" and told["line"].startswith("Even geen verbinding")
    before = len(ha.calls)
    await ticks(hm, clock, 30)  # dark, people home: still nothing new while HA is away
    assert len(ha.calls) == before
    await ha.come_back()
    assert hm.status.ha_lost is None and hm.status.orb != "attention"
    assert hm.self_check["broken_actions"] == []
    await hm.stop()


async def test_a_new_device_in_home_assistant_is_seen_without_a_restart(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path)
    lamp = Entity("light.study_lamp", "r-study", "Bureaulamp", area_id="living")
    await ha.add_entity(lamp, State(lamp.entity_id, "off", {}, clock.now()))
    await clock.advance(10)
    await hm.tick()
    assert lamp.entity_id not in hm.model.entities  # waits until the burst is over
    await ticks(hm, clock, 1)
    assert hm.model.entities[lamp.entity_id].name == "Bureaulamp"
    assert hm.world is not None and lamp.entity_id in hm.world.states
    await hm.stop()


async def test_an_action_home_assistant_no_longer_offers_is_only_watched(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    assert hm.learning is not None and hm.gate is not None
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    ha.removed_actions.add("light.turn_on")  # e.g. an integration that did not load
    await ha.drop()
    await ha.come_back()
    assert "light.turn_on" in hm.gate.state.broken_actions
    told = [n for n in await hm.notifications() if n["topic"] == "selfcheck:actions"]
    assert told and told[0]["kind"] == "important"
    await ha.simulate("binary_sensor.hall_motion", "on")
    entry = [d for d in hm.status.thoughts if d.request.skill == "light_on_entry"]
    assert entry and entry[-1].outcome is Outcome.OBSERVED
    # Back to normal after the next start of HA: the word goes away by itself.
    ha.removed_actions.clear()
    await ha.drop()
    await ha.come_back()
    assert not hm.gate.state.broken_actions
    assert not [n for n in await hm.notifications() if n["topic"] == "selfcheck:actions"]
    await hm.stop()


async def test_a_device_named_in_a_house_rule_that_disappears_gets_a_word(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await start(tmp_path)
    await hm.db.execute(
        "INSERT INTO house_rules(scope_type, scope_id, effect, created) VALUES(?,?,?,?)",
        ("entity", "light.hall", "never", clock.now().isoformat()),
    )
    await hm.rules.load(hm.db)
    name = ha.model.entities["light.hall"].name
    await hm._self_check()  # HouseMind remembers the names of what it follows
    await ha.remove_entity("light.hall")
    await ticks(hm, clock, 1)
    told = [n for n in await hm.notifications() if n["topic"] == "selfcheck:missing"]
    text = hm.languages.text("nl", str(told[0]["text_key"]), **json.loads(str(told[0]["params"])))
    # Its name, not a code, and one device is one "apparaat" (found by the manual).
    assert f": {name}." in text and "1 apparaat " in text and "light.hall" not in text
    await hm.stop()


async def test_a_newer_home_assistant_is_careful_for_one_good_day(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path)
    assert hm.gate is not None and not hm.gate.state.untested_ha
    await ha.drop()
    await ha.come_back(version="2099.1.0")
    assert hm.gate.state.untested_ha
    assert [n for n in await hm.notifications() if n["topic"] == "ha:untested"]
    await clock.advance(timedelta(hours=24, minutes=10).total_seconds())
    await ticks(hm, clock, 16)  # the next review after a day without failures
    assert not hm.gate.state.untested_ha
    assert not [n for n in await hm.notifications() if n["topic"] == "ha:untested"]
    await hm.stop()


async def test_an_older_home_assistant_than_supported_gets_a_clear_word(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path, version="2025.12.4")
    told = [n for n in await hm.notifications() if n["topic"] == "ha:too_old"]
    assert told
    text = hm.languages.text("nl", str(told[0]["text_key"]), **json.loads(str(told[0]["params"])))
    assert "2025.12.4" in text and "2026.4" in text
    await hm.stop()
