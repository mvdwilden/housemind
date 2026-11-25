"""Light when coming in counts what really happened (found in the real house, B-116), from the
user's side: one situation each time someone comes into a dark place or it gets dark while
they are there – not one per lamp, and not every five minutes while they sit there. What
HouseMind learned about a room survives a restart. Each test fails without it; what it
checks is its docstring."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from housemind.brain.learning import SITUATIONS, Result
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source
from housemind.runtime import HouseMind, Paths
from housemind.world.house_mode import HouseMode
from tests.test_automations import ROOT
from tests.test_presence_at_start import LIVING, READING, _house, _wait

CEILING = "light.living_ceiling"


def _counts(hm: HouseMind, clock: SimulatedClock) -> tuple[float, float, float]:
    """Hits, misses and situations of light on when coming in, in the living room."""
    learning = hm.learning
    assert learning is not None
    situations = learning.counts.get(("light_on_entry", "living", "*", SITUATIONS))
    return (
        learning.total("light_on_entry", "living", Result.HIT, clock.now()),
        learning.total("light_on_entry", "living", Result.MISS, clock.now()),
        situations.weight if situations else 0.0,
    )


async def _home(tmp_path: Path, before: dict | None = None) -> tuple:
    hm, clock, ha = await _house(tmp_path, before or {})
    assert hm.world is not None
    hm.world.modes.mode = HouseMode.HOME
    return hm, clock, ha


async def test_one_lamp_on_when_coming_in_is_one_good_situation(tmp_path: Path) -> None:
    """Coming into the dark living room with two lamps and switching one on is one situation,
    and a good one – the other lamp that stays off is not a "not like that"."""
    hm, clock, ha = await _home(tmp_path)
    await ha.simulate(LIVING, "on")
    await _wait(hm, clock, 20)
    await ha.simulate(READING, "on")
    await _wait(hm, clock, 15 * 60)
    hits, misses, situations = _counts(hm, clock)
    assert (round(hits, 2), misses, situations) == (1.0, 0, 1)
    await hm.stop()


async def test_sitting_in_the_dark_is_one_situation_until_someone_comes_in_again(
    tmp_path: Path,
) -> None:
    """Found in the real house: someone sitting an evening in the dark counted as "not like
    that" every five minutes, per lamp – a thousand a day. Now it is one situation; leaving
    (for more than two minutes, E-268) and coming back is the next one."""
    hm, clock, ha = await _home(tmp_path)
    await ha.simulate(LIVING, "on")
    await _wait(hm, clock, 60 * 60)
    _hits, misses, situations = _counts(hm, clock)
    assert (round(misses, 2), situations) == (1.0, 1)
    await ha.simulate(LIVING, "off")
    await _wait(hm, clock, 3 * 60)
    await ha.simulate(LIVING, "on")
    await _wait(hm, clock, 30 * 60)
    assert _counts(hm, clock)[2] == 2
    await hm.stop()


async def test_coming_into_a_lit_room_is_no_situation(tmp_path: Path) -> None:
    """A lamp already on when someone comes in: people chose their light, there is nothing to
    learn about light when coming in, and HouseMind adds no lamps."""
    hm, clock, ha = await _home(tmp_path, {CEILING: ("on", {})})
    await ha.simulate(LIVING, "on")
    await _wait(hm, clock, 20 * 60)
    assert hm.learning is not None
    assert not [p for p in hm.learning.pending if p.skill == "light_on_entry"]
    assert _counts(hm, clock) == (0, 0, 0)
    await hm.stop()


async def test_days_and_usual_lamps_survive_a_restart(tmp_path: Path) -> None:
    """Found in the real house: after every start the days of experience began again ("sinds
    –" on the card), and HouseMind forgot which lamps people use in a room."""
    hm, clock, ha = await _home(tmp_path)
    assert hm.learning is not None
    since = clock.now() - timedelta(days=5)
    hm.learning.first_seen[("light_on_entry", "living")] = since
    await ha.simulate(READING, "on", by=Source.PERSON)
    await hm.stop()
    again = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert again.learning is not None
    assert again.learning.first_seen[("light_on_entry", "living")] == since
    assert again.learning.usual_lights("living") == {READING}
    await again.stop()
