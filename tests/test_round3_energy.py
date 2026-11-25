"""Round 3 of the device audit: energy, the charger and words. Each test fails without the
fix; the finding it comes from is in its docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.energy.plan import Step
from housemind.energy.setup import _has_word
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer, Outcome

ROOT = Path(__file__).resolve().parents[1]
NOON = datetime(2026, 10, 19, 12, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


def test_short_words_match_whole_words_only() -> None:
    """ "car" matched "carbon monoxide", "ev" matched "Eve", "net" matched "netwerkkast" and
    "zon" matched "zonwering"."""
    assert not _has_word("CO-melder carbon monoxide battery", ["car"])
    assert not _has_word("Eve Thermo batterij", ["ev"])
    assert not _has_word("Netwerkkast vermogen", ["net"])
    assert not _has_word("Zonwering woonkamer", ["zon"])
    assert _has_word("Auto batterij", ["auto"]) and _has_word("Zon opbrengst", ["zon"])
    assert _has_word("Laadpaalstroom", ["laadpaal"])  # longer words: also in compounds


class _Plan:
    def __init__(self, steps: dict[str, Step]) -> None:
        self.by_device = steps
        self.steps = list(steps.values())
        self.slots: list[Any] = []

    def now(self, _now: datetime) -> dict[str, Step]:
        return self.by_device


async def _start(tmp_path: Path) -> tuple[HouseMind, Any, SimulatedClock]:
    clock = SimulatedClock(NOON.astimezone(UTC))
    house = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 4, json.dumps({}), clock.now().isoformat()),
    )
    assert hm.energy is not None
    hm.energy.since = clock.now() - timedelta(days=30)  # past the measuring weeks
    return hm, house.ha, clock


def _actions(requests: list[ActionRequest], entity: str) -> list[tuple[str, dict[str, Any]]]:
    return [(r.action, r.data) for r in requests if r.entity_id == entity]


def _done(hm: Any, reqs: list[Any], ha: Any, now: datetime) -> None:
    for request in reqs:
        hm.energy.done(request, ha.states.get(request.entity_id), now)


async def test_the_export_limit_goes_back_to_its_own_value(tmp_path: Path) -> None:
    """After a negative price the export limit went to its maximum, undoing a lower limit
    a person or the law wanted."""
    hm, ha, clock = await _start(tmp_path)
    assert hm.energy is not None
    now = clock.now()
    limit = "number.solar_export_limit"
    ha.states[limit] = State(limit, "60", {"min": 0, "max": 100}, now)
    hm.energy.plan = _Plan({})  # type: ignore[assignment]
    assert _actions(hm.energy.requests(dict(ha.states), now, hm.model.area_of), limit) == []
    step = Step("export", "limit", now, now + timedelta(hours=1), "negative_price")
    hm.energy.plan = _Plan({"export": step})  # type: ignore[assignment]
    reqs = hm.energy.requests(dict(ha.states), now, hm.model.area_of)
    assert _actions(reqs, limit) == [("number.set_value", {"value": 0.0})]
    _done(hm, reqs, ha, now)
    ha.states[limit] = State(limit, "0", {"min": 0, "max": 100}, now)
    hm.energy.plan = _Plan({})  # type: ignore[assignment]
    reqs = hm.energy.requests(dict(ha.states), now, hm.model.area_of)
    assert _actions(reqs, limit) == [("number.set_value", {"value": 60.0})]  # not 100
    await hm.stop()


async def test_a_device_setting_is_never_touched(tmp_path: Path) -> None:
    """The gate allowed actions on a device's own settings (an installation's max current,
    an indicator LED)."""
    hm, ha, clock = await _start(tmp_path)
    ha.model.entities["light.led_ring"] = Entity(
        "light.led_ring", "r-led", "LED-ring", area_id="garage", entity_category="config"
    )
    ha.states["light.led_ring"] = State("light.led_ring", "on", {}, clock.now())
    await hm._refresh_model()
    assert hm.gate is not None
    decision = await hm.gate.submit(
        ActionRequest(
            Layer.PERSON_ANSWER, "light.led_ring", "light.turn_off", {}, created_at=clock.now()
        )
    )
    assert decision.outcome is not Outcome.EXECUTED and decision.reason == "not_allowed"
    await hm.stop()
