"""QA 2, L13: four refusals of the safety gate had no test – a device left alone, one turned
off in Home Assistant, a kind HouseMind does not know, and too many changes in a day. Each
from what happens in the house, with the sentence Vesta gives as the reason."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer, Outcome

ROOT = Path(__file__).resolve().parents[1]
MORNING = datetime(2026, 10, 20, 6, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


async def _house(tmp_path: Path) -> tuple[HouseMind, SimulatedClock]:
    clock = SimulatedClock(MORNING.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    ha.model.entities["remote.mystery"] = Entity("remote.mystery", "r-m", "Iets", area_id="hall")
    ha.states["remote.mystery"] = State("remote.mystery", "off", {}, clock.now())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    return hm, clock


def _ask(
    hm: HouseMind, entity_id: str, action: str, skill: str = "", **data: object
) -> ActionRequest:
    found = hm.model.area_of(entity_id)
    area = found.id if found else ""
    return ActionRequest(
        Layer.REFLEX,
        entity_id,
        action,
        dict(data),
        skill=skill or None,
        area_id=area,
        created_at=hm.clock.now(),
    )


async def test_a_device_left_alone_is_never_switched(tmp_path: Path) -> None:
    hm, _clock = await _house(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await hm.set_house_setting("excluded", ["light.kitchen"])
    decision = await hm.consider(_ask(hm, "light.kitchen", "light.turn_on", "light_on_entry"))
    assert decision is not None and decision.reason == "excluded_by_user"
    assert hm.why_not("light.kitchen", "nl") == "Je hebt gevraagd dit apparaat met rust te laten."
    await hm.stop()


async def test_a_device_turned_off_in_home_assistant_is_not_used_and_vesta_says_so(
    tmp_path: Path,
) -> None:
    """She said "Ik heb hier nog niets mee gedaan" for a device that is turned off in HA."""
    hm, _clock = await _house(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    hm.model.entities["light.kitchen"] = replace(hm.model.entities["light.kitchen"], disabled=True)
    decision = await hm.consider(_ask(hm, "light.kitchen", "light.turn_on", "light_on_entry"))
    assert decision is not None and decision.reason == "entity_disabled"
    assert "uitgeschakeld in Home Assistant" in hm.why_not("light.kitchen", "nl")
    await hm.stop()


async def test_a_kind_she_does_not_know_is_only_watched(tmp_path: Path) -> None:
    hm, _clock = await _house(tmp_path)
    decision = await hm.consider(_ask(hm, "remote.mystery", "remote.turn_on"))
    assert decision is not None and decision.reason == "unknown_device_kind"
    assert hm.why_not("remote.mystery", "nl") == (
        "Dit soort apparaat ken ik nog niet; ik kijk alleen mee."
    )
    await hm.stop()


async def test_a_thermostat_changes_at_most_its_number_a_day(tmp_path: Path) -> None:
    hm, clock = await _house(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("keep_temperature", "bedroom")] = Step.AUTONOMOUS
    outcomes = []
    for i in range(11):
        decision = await hm.consider(
            _ask(
                hm,
                "climate.bedroom",
                "climate.set_temperature",
                "keep_temperature",
                temperature=17 + (i % 2),
            )
        )
        outcomes.append(
            decision.reason if decision and decision.outcome is not Outcome.EXECUTED else "ok"
        )
        await clock.advance(timedelta(minutes=46).total_seconds())
    assert outcomes[:10] == ["ok"] * 10, outcomes
    assert outcomes[10] == "daily_limit"
    assert (
        hm.why_not("climate.bedroom", "nl") == "Dit apparaat is vandaag al vaak genoeg veranderd."
    )
    await hm.stop()
