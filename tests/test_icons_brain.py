"""Icons in Brein, the Logboek and Vesta's questions and notices (E-511, design 79, part B): a
house rule shows what it is about – a device, a room, a kind of device or the house; a question,
a decision in the trace and a thought show the device they are about, as it is; a notice the
device in its subject; the rooms of 'Wat ik per ruimte leerde' their icons. Each test fails
without the build."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from housemind.brain.rules import HouseRule
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer
from housemind.server import views

ROOT = Path(__file__).resolve().parents[1]


async def _start(tmp_path: Path, at: datetime | None = None) -> tuple[HouseMind, object]:
    clock = SimulatedClock(at or datetime(2026, 11, 3, 19, 0, tzinfo=UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path), set(demo.pet_areas))
    await hm.start(autorun=False)
    return hm, demo


async def test_a_rule_shows_what_it_is_about(tmp_path: Path) -> None:
    hm, _ = await _start(tmp_path)
    await hm.save_rule(HouseRule("area", "bedroom", "never", "open"), "demo-user-1")
    await hm.save_rule(HouseRule("house", "*", "limit", "heat", 21.0), "demo-user-1")
    await hm.save_rule(HouseRule("entity", "light.living_ceiling", "never", "on"), "demo-user-1")
    shown = {r["icon"] for r in views.rules(hm, "nl")}
    assert shown == {"bed", "home", "lamp"}
    await hm.stop()


async def test_a_question_and_a_decision_show_their_device_as_it_is(tmp_path: Path) -> None:
    hm, demo = await _start(tmp_path)
    assert hm.gate is not None and hm.learning is not None
    request = ActionRequest(
        Layer.REFLEX,
        "light.toilet",
        "light.turn_on",
        {},
        reason="skill:light_on_entry",
        skill="light_on_entry",
        area_id="toilet",
        created_at=hm.clock.now(),
        facts={"context": "workday|12:00"},
    )
    hm.learning.steps[("light_on_entry", "toilet")] = Step.PROPOSE
    assert await hm.proposals.add(hm.gate.evaluate(request)) is not None
    card = next(c for c in await views.proposals(hm, "nl", 3) if c["entity"] == "light.toilet")
    assert card["icon"] == "lamp" and "pct" not in card  # the lamp is off
    await demo.ha.simulate("light.toilet", "on", {"brightness": 153})  # type: ignore[attr-defined]
    assert views.icon_look(hm, "light.toilet") == {"icon": "lamp", "pct": 60}
    assert views.icon_look(hm, "house") == {}
    await hm.stop()


async def test_a_notice_shows_the_device_in_its_subject(tmp_path: Path) -> None:
    hm, _ = await _start(tmp_path)
    assert views._notice_icon(hm, "unusual:light.living_ceiling", {})["icon"] == "lamp"
    assert views._notice_icon(hm, "vacation_suggestion", {}) == {}
    found = views._notice_icon(hm, "safety", {"device": "binary_sensor.living_presence"})
    assert found["icon"] == "presence"
    await hm.stop()
