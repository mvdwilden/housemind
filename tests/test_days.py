"""Days that differ (E-89): heard, read from the calendar, or seen – that day only."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.days import DayNotes, day_wish, day_words
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.i18n.catalog import load_languages
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.world.calendar import DayType

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")


def local(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=TZ)


async def start(
    at: datetime, tmp_path: Path
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 2, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, clock, house.ha


def test_what_people_say_about_a_day_in_every_language() -> None:
    words = day_words(load_languages([ROOT / "knowledge" / "languages"]))
    assert day_wish("Vandaag werk ik thuis", words) == ("home_work", 0)
    assert day_wish("Morgen ben ik ziek.", words) == ("sick", 1)
    assert day_wish("Het is toch een gewone dag", words) == (None, 0)
    assert day_wish("I'm working from home tomorrow", words) == ("home_work", 1)
    assert day_wish("Morgen habe ich frei", words) == ("day_off", 1)
    assert day_wish("morgen ben ik vrij", words) == ("day_off", 1)
    assert day_wish("Demain je suis en télétravail", words) == ("home_work", 1)
    assert day_wish("Hoe gaat het?", words) is None


async def test_said_today_is_different_and_normal_again(tmp_path: Path) -> None:
    hm, clock, _ = await start(local(19, 8), tmp_path)
    assert hm.world is not None
    reply = await hm.talk("Vandaag werk ik thuis", "nl", 2, "demo-user-1")
    assert "thuiswerkdag" in reply["text"] and "patroon" in reply["text"]
    assert hm.world.snapshot(clock.now()).day_type is DayType.DEVIANT
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    snap = await views.snapshot(hm, viewer)
    assert snap["dayNote"]["kind"] == "home_work"
    assert snap["dayNote"]["text"] == "Vandaag is een thuiswerkdag."
    # It survives a restart.
    await hm.stop()
    hm2 = HouseMind(clock, hm.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm2.start(autorun=False)
    assert hm2.world is not None
    assert hm2.world.snapshot(clock.now()).day_type is DayType.DEVIANT
    reply = await hm2.talk("Het is toch een gewone dag", "nl", 2, "demo-user-1")
    assert "gewone dag" in reply["text"]
    assert hm2.world.snapshot(clock.now()).day_type is DayType.WORKDAY
    await hm2.stop()


async def test_an_all_day_event_in_the_calendar(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 7), tmp_path)
    assert hm.world is not None
    await hm.set_house_setting("calendar", True)  # the administrator's yes (20.19)
    await ha.add_entity(
        Entity("calendar.family", "r-calendar", "Gezin"),
        State(
            "calendar.family",
            "on",
            {"message": "Schoolvakantie", "all_day": True},
            clock.now(),
        ),
    )
    hm.world.states["calendar.family"] = ha.states["calendar.family"]
    hm.model.entities["calendar.family"] = Entity("calendar.family", "r-calendar", "Gezin")
    await clock.advance(16 * 60)
    await hm.tick()
    assert hm.days.get(local(19, 7).date()) == ("school_holiday", "calendar")
    assert hm.world.snapshot(clock.now()).day_type is DayType.DEVIANT
    await hm.stop()


def test_a_workday_at_home_is_seen_from_behaviour() -> None:
    notes = DayNotes()
    for day in (13, 14, 15):  # someone left early on recent workdays
        notes.resident_left(local(day, 7, 45))
    assert not notes.looks_like_home_day(local(16, 10, 0), DayType.WORKDAY, 2)  # too early
    assert notes.looks_like_home_day(local(16, 10, 31), DayType.WORKDAY, 2)
    assert not notes.looks_like_home_day(local(16, 11, 0), DayType.WORKDAY, 2)  # once a day
    notes.resident_left(local(19, 8, 0))
    assert not notes.looks_like_home_day(local(19, 10, 31), DayType.WORKDAY, 2)
    assert not notes.looks_like_home_day(local(18, 10, 31), DayType.FREE, 2)  # a Sunday
