"""The part of the day from the house's own rhythm (E-455), from the user's side. The
administrator's wish (03-11-2026): "Waar ik op zoek ben is dat HM, voor alle mogelijke HA
installaties, begrijpt welke ritme gaande is" – night, morning, day and evening from what the
house does and what HouseMind learned, not from the clock; the clock only while a time is not
known yet. Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.rhythm import Rhythm
from housemind.knowledge.catalog import Catalog
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome
from housemind.server import views
from housemind.world.calendar import DayType
from housemind.world.house_mode import HouseMode
from tests.test_real_house_counts import _house
from tests.test_skills import Rig, _by

AMS = ZoneInfo("Europe/Amsterdam")
CEILING = "light.living_ceiling"
# Monday 19 October 2026 (summer time: UTC+2); Tuesday is a workday too.
MONDAY = datetime(2026, 10, 19, tzinfo=UTC)


def _minutes_after_noon(hour: int, minute: int = 0) -> int:
    return (hour * 60 + minute - 720) % 1440


async def _rig(catalog: Catalog, at: datetime, rhythm: Rhythm) -> Rig:
    """The extended house (a presence sensor in the living room, motion in the bedroom) in
    the dark, following this rhythm."""
    rig = Rig(catalog, "extended", at)
    rig.world.part_of = lambda *now: rhythm.part(*now)
    await rig.set("sensor.living_light_level", "2")
    return rig


def _local(hour: int, minute: int, day: datetime = MONDAY) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=AMS).astimezone(UTC)


async def test_still_up_after_eleven_is_still_the_evening(catalog: Catalog) -> None:
    """A house that usually goes to bed at half past twelve: at 23:15 the living room gets
    the evening's light, not the night's – the clock's night started at 23:00. The Logboek
    keeps why: the evening of this house."""
    rhythm = Rhythm(bed={"workday": [_minutes_after_noon(0, 30)] * 5})
    rig = await _rig(catalog, _local(23, 15), rhythm)
    await rig.set("binary_sensor.living_presence", "on")
    ceiling = _by(rig.run(), CEILING)
    assert ceiling and ceiling[0].data == {"brightness_pct": 35, "color_temp_kelvin": 2700}
    assert (ceiling[0].facts["part"], ceiling[0].facts["part_how"]) == ("evening", "rhythm")


async def test_nothing_learned_yet_keeps_the_clock(catalog: Catalog) -> None:
    """Without a learned bedtime 23:15 is the night, as before: soft, warm light."""
    rig = await _rig(catalog, _local(23, 15), Rhythm())
    await rig.set("binary_sensor.living_presence", "on")
    ceiling = _by(rig.run(), CEILING)
    assert ceiling and ceiling[0].data == {"brightness_pct": 15, "color_temp_kelvin": 2200}
    assert ceiling[0].facts["part_how"] == "clock"


async def test_the_bedrooms_stay_dark_from_the_usual_bedtime(catalog: Catalog) -> None:
    """Someone is still up at 22:45, but the house usually goes to bed at 22:30: coming into
    the bedroom turns on no lamp – somebody may be asleep there – while the living room still
    has its evening. Without a learned bedtime it was 'evening' until 23:00, bedroom too."""
    rhythm = Rhythm(bed={"workday": [_minutes_after_noon(22, 30)] * 5})
    rig = await _rig(catalog, _local(22, 45), rhythm)
    await rig.set("binary_sensor.living_presence", "on")
    await rig.set("binary_sensor.bedroom_motion", "on")
    requests = rig.run()
    assert not _by(requests, "light.bedroom")
    assert _by(requests, CEILING)[0].data == {"brightness_pct": 35, "color_temp_kelvin": 2700}
    clock = await _rig(catalog, _local(22, 45), Rhythm())
    await clock.set("binary_sensor.living_presence", "on")
    await clock.set("binary_sensor.bedroom_motion", "on")
    assert _by(clock.run(), "light.bedroom")


async def test_the_morning_starts_when_the_house_wakes_up(catalog: Catalog) -> None:
    """Up at 5:40 on a Saturday, while the house usually sleeps until 8:30 on free days: the
    morning starts when the house wakes up – morning light, not the clock's night."""
    saturday = datetime(2026, 10, 24, tzinfo=UTC)
    rhythm = Rhythm(wake={"free": [8 * 60 + 30] * 5})
    rig = await _rig(catalog, _local(5, 40, saturday), rhythm)
    rig.world.modes.set_manual(HouseMode.SLEEP, rig.clock.now())
    assert not _by(rig.run(), CEILING)  # asleep, nobody moving
    rig.world.modes.set_manual(HouseMode.HOME, rig.clock.now())
    await rig.set("binary_sensor.living_presence", "on")
    ceiling = _by(rig.run(), CEILING)
    assert ceiling and ceiling[0].data == {"brightness_pct": 60, "color_temp_kelvin": 3500}
    assert (ceiling[0].facts["part"], ceiling[0].facts["part_how"]) == ("morning", "woke")


def test_the_parts_of_a_learned_day() -> None:
    """Up at 6:40, out at 7:45, home at 17:30, to bed at 22:30: the morning lasts until people
    leave, the evening starts when they come home. With nobody home the night starts at the
    usual bedtime; with someone still up, two hours later."""
    rhythm = Rhythm(
        wake={"workday": [6 * 60 + 40] * 5},
        leave={"workday": [7 * 60 + 45] * 5},
        back={"workday": [17 * 60 + 30] * 5},
        bed={"workday": [_minutes_after_noon(22, 30)] * 5},
    )

    def part(hour: int, minute: int, mode: HouseMode = HouseMode.HOME) -> tuple[str, str, str]:
        local = datetime(2026, 10, 20, hour, minute, tzinfo=AMS)  # a Tuesday
        found = rhythm.part(local, mode, None, DayType.WORKDAY, DayType.WORKDAY, None)
        return found.kind, found.since.strftime("%H:%M"), found.how

    assert part(7, 30) == ("morning", "06:40", "rhythm")
    assert part(8, 0) == ("day", "07:45", "rhythm")
    assert part(17, 40) == ("evening", "17:30", "rhythm")
    assert part(22, 45, HouseMode.AWAY) == ("night", "22:30", "rhythm")
    assert part(23, 45) == ("evening", "17:30", "rhythm")
    assert part(0, 45) == ("night", "00:30", "late")
    asleep = rhythm.part(
        datetime(2026, 10, 20, 22, 10, tzinfo=AMS),
        HouseMode.SLEEP,
        datetime(2026, 10, 20, 22, 5, tzinfo=AMS),
        DayType.WORKDAY,
        DayType.WORKDAY,
        None,
    )
    assert (asleep.kind, asleep.since.strftime("%H:%M"), asleep.how) == ("night", "22:05", "sleep")


async def test_the_rhythm_page_and_the_logboek_say_it(tmp_path: Path) -> None:
    """Brein › Ritme says what part of the day it is, since when and why; a lamp's row in
    the Logboek says the part of the day its light was for – only when it came from the
    house's rhythm, not from the clock."""
    hm, clock, _ha = await _house(tmp_path, start=MONDAY.replace(hour=4, minute=30))  # 06:30
    assert hm.world is not None and hm.explainer is not None
    hm.world.modes.set_manual(HouseMode.SLEEP, clock.now())
    await hm.tick()  # HouseMind's regular look at the house
    assert views.rhythm(hm, "nl")["now"] == "Nu: nacht, sinds 06:30. Het huis slaapt."
    await clock.advance(timedelta(minutes=12).total_seconds())
    hm.world.modes.set_manual(HouseMode.HOME, clock.now())
    await hm.tick()
    assert views.rhythm(hm, "nl")["now"] == "Nu: ochtend, sinds 06:42. Toen werd het huis wakker."
    assert (
        views.rhythm(hm, "en")["now"]
        == "Now: morning, since 06:42. That is when the house woke up."
    )

    def seen(how: str) -> list[str]:
        request = ActionRequest(
            Layer.REFLEX,
            CEILING,
            "light.turn_on",
            skill="light_on_entry",
            area_id="living",
            facts={"mode": "home", "part": "morning", "part_how": how},
        )
        decision = Decision(request, Outcome.EXECUTED, None, None, (), clock.now())
        return hm.explainer.observations("nl", decision) if hm.explainer else []

    assert "ochtend in jullie ritme" in seen("woke")
    assert "ochtend in jullie ritme" not in seen("clock")
    await hm.stop()
