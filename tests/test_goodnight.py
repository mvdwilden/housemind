"""Welterusten (E-517): when the house goes to sleep, what is still on or open goes off and
shut. The administrator's question (14-11-2026): "naar bed gaan, dat je juist de gordijnen
dichtdoet als dat al niet gedaan is. Alles van dat soort mogelijkheden." – and their "Ja, bouw
het" to the proposal. From the user's side: the demo house at night, Doe alsof and what stays as
it is. Each test fails without the change."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from housemind.core.model import Source
from housemind.knowledge.catalog import Step
from housemind.world.house_mode import HouseMode
from tests.test_house_modes import _ticks
from tests.test_house_sleeps import _quiet_night

SKILLS = [
    ("light_off_sleep", "toilet"),
    ("light_off_sleep", "kitchen"),
    ("covers_close_sleep", "living"),
    ("covers_close_sleep", "bedroom"),
]


async def _evening(tmp_path: Path) -> tuple[Any, Any]:
    """22:30, everyone home: the toilet lamp put on by hand earlier (a room without a sensor
    that sees people), the living room's curtain and the bedroom's still open."""
    hm, ha = await _quiet_night(tmp_path)
    for key in SKILLS:
        hm.learning.steps[key] = Step.AUTONOMOUS
    await ha.simulate("light.toilet", "on", by=Source.PHYSICAL)
    await ha.simulate("cover.living_curtain", "open", by=Source.PHYSICAL)
    return hm, ha


async def _asleep(hm: Any) -> None:
    for _ in range(60):  # a minute a tick: the house sleeps after twenty quiet minutes
        await hm.clock.advance(60)
        await hm.tick()
        if hm.world.modes.mode is HouseMode.SLEEP:
            break
    assert hm.world.modes.mode is HouseMode.SLEEP
    await _ticks(hm, 3)


async def test_at_bedtime_the_lamps_go_off_and_the_curtains_close(tmp_path: Path) -> None:
    """The house goes to sleep: the toilet lamp – put on by hand, in a room where no sensor can
    see anyone – goes off, and the curtains of the living room and the bedroom close."""
    hm, ha = await _evening(tmp_path)
    await _asleep(hm)
    assert ha.states["light.toilet"].state == "off"
    assert ha.states["cover.living_curtain"].state == "closed"
    assert ha.states["cover.bedroom_curtain"].state == "closed"
    await hm.stop()


async def test_once_a_night_and_never_a_bedroom_lamp(tmp_path: Path) -> None:
    """Who puts the toilet lamp on again later that night keeps it: Welterusten is once a
    night. A bedroom lamp is never Welterusten's – someone may be reading."""
    hm, ha = await _evening(tmp_path)
    await _asleep(hm)
    await ha.simulate("light.toilet", "on", by=Source.PHYSICAL)
    await hm.clock.advance(5 * 60)
    await _ticks(hm, 3)
    assert ha.states["light.toilet"].state == "on"
    view = hm.test_view("nl", "bedroom", ["sleep"])
    assert not any("slapen" in line["text"] and "uitdoen" in line["text"] for line in view["lines"])
    await hm.stop()


async def test_doe_alsof_het_huis_slaapt_says_it(tmp_path: Path) -> None:
    """Test het huis › Doe alsof › Het huis slaapt: for the toilet with its lamp just put on by
    hand, HouseMind says it would put it out because the house goes to sleep – everyone is in
    bed then – and switches nothing. Also later, when the house already slept."""
    hm, ha = await _quiet_night(tmp_path)
    await ha.simulate("light.toilet", "on", by=Source.PHYSICAL)
    await _ticks(hm, 15)
    view = hm.test_view("nl", "toilet", ["sleep"])
    texts = [line["text"] for line in view["lines"]]
    assert any("uitdoen" in t and "het huis gaat slapen" in t for t in texts), texts
    assert not any("niemand" in t for t in texts), texts  # no sensor: no 'nobody there'
    assert ha.states["light.toilet"].state == "on"
    await _ticks(hm, 60)  # an hour later the house sleeps, Welterusten only watched there
    later = [line["text"] for line in hm.test_view("nl", "toilet", ["sleep"])["lines"]]
    assert any("uitdoen" in t and "het huis gaat slapen" in t for t in later), later
    await hm.stop()


async def test_het_huis_slaapt_shows_each_lamp_once(tmp_path: Path) -> None:
    """In the house (14-11-2026) Doe alsof 'Het huis slaapt' showed the bathroom lamp twice:
    put out "because nobody has been there for a while" (does it) and "because the house goes
    to sleep" (asks first) – which one is it? Going to sleep is everyone just gone to bed: the
    living room's lamp gets one line, Welterusten's. 'Niemand' still is empty for a while."""
    hm, ha = await _quiet_night(tmp_path)
    await ha.simulate("light.living_ceiling", "on", by=Source.PHYSICAL)
    await _ticks(hm, 3)
    texts = [line["text"] for line in hm.test_view("nl", "living", ["sleep"])["lines"]]
    assert any("uitdoen" in t and "het huis gaat slapen" in t for t in texts), texts
    assert not any("niemand" in t for t in texts), texts
    nobody = [line["text"] for line in hm.test_view("nl", "living", ["nobody"])["lines"]]
    assert any("niemand" in t for t in nobody), nobody
    assert ha.states["light.living_ceiling"].state == "on"
    await hm.stop()


async def test_not_after_the_first_half_hour_of_sleep(tmp_path: Path) -> None:
    """Welterusten is the moment of going to sleep: a lamp found on at three in the night is
    no bedtime any more."""
    hm, ha = await _quiet_night(tmp_path)
    for key in SKILLS:
        hm.learning.steps[key] = Step.AUTONOMOUS
    await _asleep(hm)
    await hm.clock.advance(timedelta(hours=1).total_seconds())
    await ha.simulate("light.toilet", "on", by=Source.PHYSICAL)
    await _ticks(hm, 3)
    assert ha.states["light.toilet"].state == "on"
    await hm.stop()


async def test_straks_shows_welterusten_at_bedtime(tmp_path: Path) -> None:
    """Brein › Ritme › Straks: under 'Het huis gaat slapen' stands that the toilet lamp, on since
    the afternoon in a room without a sensor that sees people, goes out."""
    from housemind.brain.later import later
    from tests.test_later import _ahead, _moment, _texts

    hm = await _ahead(tmp_path)
    await hm.ha.simulate("light.toilet", "on", by=Source.PHYSICAL)  # type: ignore[attr-defined]
    await hm.tick()
    bed = _moment(later(hm, "nl"), "bed")
    assert "Toilet lamp uitdoen." in _texts(bed), "\n".join(_texts(bed))
    await hm.stop()
