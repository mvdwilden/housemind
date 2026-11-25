"""The rhythm of the house in pictures (E-366 – E-368, chapter 35): a day bar with getting up,
the morning, away from home, the evening at home and going to bed; out of the ordinary; and
honest about what is not known yet. Before, the card said only "Ik leer nog wanneer jullie gaan
slapen en opstaan." or two times per type of day. Each test fails without the build."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from housemind.runtime import HouseMind
from housemind.server import views
from housemind.world.calendar import DayType
from housemind.world.house_mode import HouseMode
from tests.test_rhythm import local, start

H, A, S = HouseMode.HOME, HouseMode.AWAY, HouseMode.SLEEP
WORK = DayType.WORKDAY


def _record(hm: HouseMind, old: HouseMode, new: HouseMode, at: datetime) -> None:
    hm.rhythm.record(old, new, at, WORK, WORK)


def _workday(hm: HouseMind, day: int, leave: tuple[int, int], back: tuple[int, int]) -> None:
    _record(hm, S, H, local(day, 6, 50))
    _record(hm, H, A, local(day, *leave))
    _record(hm, A, H, local(day, *back))
    _record(hm, H, S, local(day, 23, 10))


async def _card(hm: HouseMind, level: int) -> dict[str, Any]:
    viewer = await views.load_viewer(hm, "demo-user-1", None, True)
    viewer.level = level
    return await views.snapshot(hm, viewer)


def _moments(snap: dict[str, Any], day: str = "workday") -> dict[str, dict[str, Any]]:
    found = next(d for d in snap["rhythm"]["days"] if d["key"] == day)
    return {m["key"]: m for m in found["moments"]}


async def test_a_workday_in_five_moments_and_a_day_bar(tmp_path: Path) -> None:
    """Four workdays: up at 6:50, out at 7:45, back at 17:30, to bed at 23:10. The card shows
    the five moments in words and a bar of the whole day."""
    hm, _, _ = await start(local(24, 12), tmp_path)
    for day in (19, 20, 21, 22):
        _workday(hm, day, (7, 45), (17, 30))
    snap = await _card(hm, 3)
    moments = _moments(snap)
    assert [m["label"] for m in moments.values()] == [
        "Opstaan",
        "Ochtend",
        "Weg van huis",
        "Avond thuis",
        "Naar bed",
    ]
    assert moments["wake"]["value"] == "rond 06:50"
    assert moments["morning"]["value"] == "06:50 tot 07:45"
    assert moments["away"]["value"] == "07:45 tot 17:30"
    assert moments["evening"]["value"] == "17:30 tot 23:10"
    assert moments["bed"]["value"] == "rond 23:10"
    day = next(d for d in snap["rhythm"]["days"] if d["key"] == "workday")
    assert day["label"] == "Werkdagen"
    assert [(s["kind"], s["from"], s["to"]) for s in day["bar"]] == [
        ("sleep", 0, 410),
        ("morning", 410, 465),
        ("away", 465, 1050),
        ("home", 1050, 1390),
        ("sleep", 1390, 1440),
    ]
    assert "detail" not in moments["wake"]  # the counts are for Expert
    await hm.stop()


async def test_a_short_trip_is_no_day_away(tmp_path: Path) -> None:
    """Out for forty minutes in the morning is a trip to the shop, not a day away: the card
    shows one stretch at home from getting up to bed."""
    hm, _, _ = await start(local(24, 12), tmp_path)
    for day in (19, 20, 21, 22):
        _workday(hm, day, (9, 0), (9, 40))
    moments = _moments(await _card(hm, 3))
    assert list(moments) == ["wake", "home", "bed"]
    assert moments["home"] == {"key": "home", "label": "Thuis", "value": "06:50 tot 23:10"}
    await hm.stop()


async def test_what_is_not_known_yet_says_why(tmp_path: Path) -> None:
    """Two nights are not enough, and wake-up times that differ a lot are not a habit: each
    moment says so. A house that never slept says what that needs."""
    hm, _, _ = await start(local(24, 12), tmp_path)
    nothing = await _card(hm, 3)
    assert nothing["rhythm"]["note"].startswith("Ik zie nog niet wanneer het huis slaapt.")
    for day, (hh, mm) in zip((19, 20, 21, 22), ((6, 10), (9, 40), (7, 0), (8, 30)), strict=True):
        _record(hm, S, H, local(day, hh, mm))
    for day in (19, 20):
        _record(hm, H, S, local(day, 23, 10))
    moments = _moments(await _card(hm, 3))
    assert moments["bed"]["learning"] == "2 van de 3 nachten"
    assert moments["wake"]["learning"].startswith("loopt nog uiteen: ")
    await hm.stop()


async def test_out_of_the_ordinary_working_from_home_holidays_guests(tmp_path: Path) -> None:
    """Working from home (said or seen), the last holiday and the last guests stand under the
    bar – only what there is."""
    hm, _, _ = await start(local(24, 12), tmp_path)
    before = (await _card(hm, 3))["rhythm"]["other"]
    assert before == [{"key": "alarm", "label": "Wekker", "value": "ik volg 1 telefoon"}]
    hm.days.notes["2026-10-20"] = ("home_work", "said")
    hm.days.notes["2026-10-21"] = ("home_day", "seen")
    hm.days.notes["2026-10-22"] = ("sick", "said")
    hm.rhythm.mark(H, HouseMode.VACATION, local(12, 9))
    hm.rhythm.mark(HouseMode.VACATION, H, local(19, 18))
    hm.rhythm.mark(H, HouseMode.GUESTS, local(23, 14, 57))
    hm.rhythm.mark(HouseMode.GUESTS, H, local(23, 15, 33))
    other = {o["key"]: o for o in (await _card(hm, 3))["rhythm"]["other"]}
    assert other["home_work"]["label"] == "Thuiswerken"
    assert other["home_work"]["value"] == "2 werkdagen in de laatste 4 weken"
    assert other["vacation"]["from"].startswith("2026-10-12")
    assert other["vacation"]["to"].startswith("2026-10-19")
    assert other["guests"]["from"].startswith("2026-10-23T14:57")
    await hm.stop()


async def test_each_level_sees_its_part(tmp_path: Path) -> None:
    """Carefree sees nothing; Informed one sentence on Huis; Involved the card; Expert also how
    many days the time held."""
    hm, _, _ = await start(local(24, 12), tmp_path)
    for day in (19, 20, 21, 22):
        _workday(hm, day, (7, 45), (17, 30))
    carefree, informed = await _card(hm, 1), await _card(hm, 2)
    involved, expert = await _card(hm, 3), await _card(hm, 4)
    assert "rhythm" not in carefree and "rhythmLine" not in carefree
    assert "rhythm" not in informed
    assert informed["rhythmLine"] == (
        "Op werkdagen wordt het huis meestal rond 06:50 wakker en gaat het rond 23:10 slapen."
    )
    assert "rhythmLine" not in involved and _moments(involved)["wake"]["value"] == "rond 06:50"
    assert _moments(expert)["wake"]["detail"] == "4 van de 4 nachten"
    assert _moments(expert)["away"]["detail"] == "4 van de 4 dagen"
    await hm.stop()
