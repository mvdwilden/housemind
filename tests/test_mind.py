"""What the brain thought, per level (E-217), from the user's side: what each level sees on
its screen. Fails without the change: the screen showed nothing below level 4."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.server.views import Viewer, snapshot
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 10, 19, 19, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))


async def _house(tmp_path: Path) -> HouseMind:
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    assert hm.learning is not None and hm.world is not None
    hm.learning.steps[("light_on_entry", "living")] = Step.AUTONOMOUS  # it does it
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE  # it asks
    hm.learning.steps[("light_on_entry", "hall")] = Step.OBSERVE  # it only watches
    hm.world.modes.mode = HouseMode.HOME
    for sensor in ("binary_sensor.living_presence", "binary_sensor.kitchen_motion"):
        await ha.simulate(sensor, "on")
    await ha.simulate("binary_sensor.hall_motion", "on")
    for _ in range(3):  # thinking again is no news: each thought shows once
        await clock.advance(20)
        await hm.tick()
    return hm


async def _mind(hm: HouseMind, level: int) -> list[dict[str, object]]:
    viewer = Viewer("demo-user-1", "Resident 1", level, True, {"language": "nl"})
    return list((await snapshot(hm, viewer))["mind"])


async def test_what_the_brain_thought_per_level(tmp_path: Path) -> None:
    """Below level 4 the screen showed nothing of what the brain thought; at level 4 only a
    raw stream. Now: 1 one sentence, 2 a few, 3 also what it left with saw → thought → did,
    4 more with the details."""
    hm = await _house(tmp_path)

    one = await _mind(hm, 1)
    assert len(one) == 1 and one[0]["outcome"] == "executed"
    assert str(one[0]["text"]).startswith("Ik heb") and "omdat" in str(one[0]["text"])
    assert "saw" not in one[0]
    assert "plafondlamp" in str(one[0]["text"]) and "leeslamp" in str(one[0]["text"])  # one line

    two = await _mind(hm, 2)
    assert 2 <= len(two) <= 4
    # Also what it would have done while it only watches (20.14); not what the gate refused.
    assert {item["outcome"] for item in two} <= {"executed", "proposed", "observed"}
    assert any(str(item["text"]).startswith("Ik vroeg: Zal ik") for item in two)
    texts = [item["text"] for item in two]
    assert len(texts) == len(set(texts))  # thinking three times: each thought once

    three = await _mind(hm, 3)
    living = next(item for item in three if item["area"] == "Woonkamer")
    assert "iemand in Woonkamer" in living["saw"] and "donker" in living["saw"]  # type: ignore[operator]
    assert living["thought"] and living["did"]
    left = [item for item in three if item["outcome"] not in ("executed", "proposed")]
    assert left and str(left[0]["text"]).startswith("Ik wilde")  # what it left, and why
    assert len(left) <= 3

    four = await _mind(hm, 4)
    assert len(four) >= len(three) and all("details" in item for item in four)
    await hm.stop()
