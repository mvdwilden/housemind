"""The Orb shows what the brain does (E-36, B-23) – found in a real house: it stayed aqua all
day, because "thinking" was never set and "learning" and "attention" almost never. Each test
fails without the fix: it looks at what the screens are told, not at the colour table."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.notify import Kind
from housemind.brain.proposals import Answer
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.world.house_mode import HouseMode
from tests.helpers import ticks

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 10, 19, 20, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


async def start(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant, list[str]]:
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    told: list[str] = []  # every Orb state the screens hear about

    async def listener() -> None:
        told.append(hm.status.orb)

    hm.on_update(listener)
    return hm, clock, house.ha, told


async def test_it_thinks_when_it_acts_then_rests(tmp_path: Path) -> None:
    hm, clock, ha, told = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    assert ha.states["light.kitchen"].state == "on"  # it acted …
    assert told[-1] == "thinking"  # … and every screen saw it think
    await clock.advance(5)
    hm._refresh_orb()
    assert hm.status.orb == "calm"
    await hm.stop()


async def test_an_open_question_is_learning_and_an_answer_teaches(tmp_path: Path) -> None:
    hm, clock, ha, told = await start(tmp_path)
    assert hm.learning is not None
    hm.learning_allowed = True  # the "yes" to learning (B-20): only then an answer teaches
    hm.learning.steps[("light_off_leave", "living")] = Step.PROPOSE
    await ha.simulate("binary_sensor.living_presence", "on")
    await ha.simulate("light.living_reading", "on")
    await ha.simulate("binary_sensor.living_presence", "off")
    await ticks(hm, clock, 25)
    open_now = await hm.open_proposals()
    assert len(open_now) == 1
    assert "thinking" in told  # it thought when it made the proposal
    assert hm.status.orb == "learning"  # amber while the question waits
    await hm.answer(open_now[0].id, Answer.DO, "demo-user-1")
    hm._refresh_orb()
    assert hm.status.orb == "learning"  # it learns from the answer …
    await ticks(hm, clock, 11)
    assert hm.status.orb == "calm"  # … for a while, then rests
    await hm.stop()


async def test_an_open_important_message_is_attention_also_while_sleeping(
    tmp_path: Path,
) -> None:
    """Since E-284 (20-10-2026) an open message comes before sleeping."""
    hm, _clock, _ha, _told = await start(tmp_path)
    assert hm.world is not None
    snapshot = hm.world.snapshot(hm.clock.now())
    await hm._raise(Kind.IMPORTANT, "test:window", "notify.learned", {"what": "x"}, snapshot)
    hm._refresh_orb()
    assert hm.status.orb == "attention"
    hm.world.modes.mode = HouseMode.SLEEP
    hm._refresh_orb()
    assert hm.status.orb == "attention"  # it waits for someone, also at night (E-284)
    hm.world.modes.mode = HouseMode.HOME
    await hm._resolve("test:window")
    hm._refresh_orb()
    assert hm.status.orb == "calm"
    await hm._raise(
        Kind.IMPORTANT,
        "test:one",
        "notify.learned",
        {"what": "x"},
        snapshot,
        concerns="user:demo-user-1",
    )
    hm._refresh_orb()
    assert hm.status.orb == "calm"  # a message for one person is not the house's state
    await hm.stop()


async def test_it_thinks_while_it_answers_a_question(tmp_path: Path) -> None:
    hm, clock, _ha, told = await start(tmp_path)
    await hm.talk("hoe gaat het", "nl", 2, "demo-user-1")
    assert told and told[0] == "thinking"  # at once, before the answer
    assert hm.status.orb == "thinking"  # and still a moment after it
    await clock.advance(5)
    hm._refresh_orb()
    assert hm.status.orb == "calm"
    await hm.stop()
