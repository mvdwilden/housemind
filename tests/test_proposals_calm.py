"""Proposals that stay calm in a big house (found on the first evening of a real house's trial:
a hundred open questions within ten minutes). Garden lights are not turned off "because nobody
is there"; one question per skill and room, whose answer counts for all its lamps; and no
asking again right after no answer."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.proposals import Answer
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from tests.helpers import ticks

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 10, 19, 20, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


async def start(
    tmp_path: Path, at: datetime = EVENING
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    assert hm.learning is not None
    for area in ("living", "garden"):
        hm.learning.steps[("light_off_leave", area)] = Step.PROPOSE
    return hm, clock, house.ha


def asked(notices: list[dict[str, object]]) -> list[dict[str, object]]:
    return [n for n in notices if str(n["topic"]).startswith("proposal:")]


async def test_one_question_for_the_lamps_of_a_room_and_none_for_the_garden(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await start(tmp_path)
    await ha.simulate("binary_sensor.living_presence", "on")
    for lamp in ("light.living_ceiling", "light.living_reading", "light.garden"):
        await ha.simulate(lamp, "on")
    await ha.simulate("binary_sensor.living_presence", "off")
    await ticks(hm, clock, 25)  # past the living room's hold time
    open_now = await hm.open_proposals()
    assert {p.entity_id for p in open_now} == {"light.living_ceiling", "light.living_reading"}
    assert len(asked(await hm.notifications())) == 1  # one question, not one per lamp
    await hm.answer(open_now[0].id, Answer.DO, "demo-user-1")
    assert ha.states["light.living_ceiling"].state == "off"
    assert ha.states["light.living_reading"].state == "off"
    assert ha.states["light.garden"].state == "on"  # the garden light stays as it is
    assert not await hm.open_proposals() and not asked(await hm.notifications())
    await hm.stop()


async def test_no_answer_means_not_asked_again_for_a_while(tmp_path: Path) -> None:
    # In the afternoon: since E-290 (20-10-2026) there are no questions in quiet hours, and
    # 20:00 plus five hours would be one o'clock at night.
    hm, clock, ha = await start(tmp_path, EVENING.replace(hour=14))
    await ha.simulate("binary_sensor.living_presence", "on")
    await ha.simulate("light.living_reading", "on")
    await ha.simulate("binary_sensor.living_presence", "off")
    await ticks(hm, clock, 25)
    first = await hm.open_proposals()
    assert len(first) == 1
    await ticks(hm, clock, 90)  # nobody answers: it expires, and is not asked again
    rows = await hm.db.fetchall("SELECT id FROM proposals WHERE entity_id = 'light.living_reading'")
    assert len(rows) == 1 and not await hm.open_proposals()
    assert not asked(await hm.notifications())
    await clock.advance(timedelta(hours=3).total_seconds())
    await ticks(hm, clock, 2)  # later the same situation may be asked about again
    again = [p for p in await hm.open_proposals() if p.entity_id == "light.living_reading"]
    assert len(again) == 1
    await hm.stop()
