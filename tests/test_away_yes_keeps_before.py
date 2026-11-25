"""After "Doe het" on the heat pump's "away", coming home puts back the setting it had before.
Found while fixing Vesta's reasons (01-11-2026): a "yes" carried out the proposal without the
setting from before, so HouseMind remembered "none" and put that back instead of, say, "eco".
The test fails without the change; what it checks is its docstring."""

from __future__ import annotations

from pathlib import Path

from housemind.brain.proposals import Answer
from housemind.core.model import Source
from housemind.knowledge.catalog import Step
from tests.test_house_modes import _everyone_leaves, _start, _ticks

PUMP = "climate.living"


async def test_a_yes_to_away_puts_back_eco_when_someone_comes_home(tmp_path: Path) -> None:
    """The heat pump stands on "eco". After a day away HouseMind asks to set it to "away";
    someone says "Doe het". When someone comes home, the pump goes back to "eco" – not to
    "none"."""
    hm, ha = await _start(tmp_path)
    assert hm.learning is not None and hm.gate is not None
    hm.activated, hm.gate.state.inactive = True, False  # past the introduction (E-275)
    hm.learning.steps[("setback_away", "living")] = Step.PROPOSE
    hm.learning.steps[("keep_temperature", "living")] = Step.AUTONOMOUS
    pump = {"preset_modes": ["away", "eco", "none"], "current_temperature": 20.0}
    await ha.simulate(PUMP, "heat_cool", {"temperature": 20.0, "preset_mode": "eco", **pump},
                      by=Source.PERSON)  # fmt: skip
    await _everyone_leaves(ha)
    await _ticks(hm, 24 * 60, every=30)
    asked = []
    for _ in range(120):  # asked once the day away is over; answered while it is open
        await _ticks(hm, 1, every=1)
        asked = [p for p in await hm.open_proposals() if p.entity_id == PUMP]
        if asked:
            break
    assert len(asked) == 1 and asked[0].data == {"preset_mode": "away"}
    assert (await hm.answer(asked[0].id, Answer.DO, "demo-user-1"))["ok"]
    assert ha.states[PUMP].attributes["preset_mode"] == "away"
    await ha.simulate("person.resident_1", "home")
    await _ticks(hm, 60)  # within the hour: the gate lets a device change only so often
    assert ha.states[PUMP].attributes["preset_mode"] == "eco"
    await hm.stop()
