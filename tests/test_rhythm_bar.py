"""The day bar also while HouseMind is still learning (E-507, B-433). The administrator
(12-11-2026): "In Brein › Ritme mis ik het visuele overzicht" – the manual shows a bar per type
of day, the app only once getting up and going to bed were settled (three close nights), so in
most houses there was no bar for days or weeks. Now the bar is there from the first night, the
part that is not sure yet striped over it, and the card knows where today is. Each test fails
without the build."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.test_rhythm import local, start
from tests.test_rhythm_card import H, S, _card, _moments, _record, _workday


def _day(snap: dict[str, Any], key: str = "workday") -> dict[str, Any]:
    return next(d for d in snap["rhythm"]["days"] if d["key"] == key)


def _bar(day: dict[str, Any]) -> list[tuple[str, int, int]]:
    return [(s["kind"], s["from"], s["to"]) for s in day["bar"]]


async def test_two_nights_draw_the_day_with_the_unsure_part_striped(tmp_path: Path) -> None:
    """Up at 6:30 and 7:10, to bed at 23:00 and 23:40: not a habit yet, but the bar is there,
    from the middle of the nights, and from the earliest to the latest night is unsure. The
    words below stay honest: two of the three nights."""
    hm, _, _ = await start(local(24, 12), tmp_path)
    for day, wake, bed in ((19, (6, 30), (23, 0)), (20, (7, 10), (23, 40))):
        _record(hm, S, H, local(day, *wake))
        _record(hm, H, S, local(day, *bed))
    snap = await _card(hm, 3)
    day = _day(snap)
    assert _bar(day) == [("sleep", 0, 430), ("home", 430, 1420), ("sleep", 1420, 1440)]
    assert day["unsure"] == [{"from": 390, "to": 445}, {"from": 1380, "to": 1435}]
    assert day["learning"] is True
    moments = _moments(snap)
    assert moments["wake"]["learning"] == "2 van de 3 nachten"
    assert moments["bed"]["learning"] == "2 van de 3 nachten"
    await hm.stop()


async def test_a_bedtime_around_midnight_is_unsure_on_both_ends(tmp_path: Path) -> None:
    """To bed at 23:50 and at 0:20, up once at 7:00: the unsure part around bedtime runs past
    midnight, so it comes in two; one night is unsure a quarter of an hour either side."""
    hm, _, _ = await start(local(24, 12), tmp_path)
    _record(hm, S, H, local(20, 7, 0))
    _record(hm, H, S, local(20, 23, 50))
    _record(hm, H, S, local(22, 0, 20))
    day = _day(await _card(hm, 3))
    assert _bar(day) == [("home", 0, 20), ("sleep", 20, 420), ("home", 420, 1440)]
    assert day["unsure"] == [
        {"from": 0, "to": 35},
        {"from": 405, "to": 435},
        {"from": 1430, "to": 1440},
    ]
    await hm.stop()


async def test_a_known_day_is_not_striped_and_today_has_its_place(tmp_path: Path) -> None:
    """Four workdays make a habit: no stripes, not learning. The card says which type of day
    today is and the minute now (a Saturday at noon), but not on a day that is different."""
    hm, _, _ = await start(local(24, 12), tmp_path)
    for day in (19, 20, 21, 22):
        _workday(hm, day, (7, 45), (17, 30))
    snap = await _card(hm, 3)
    day = _day(snap)
    assert day["unsure"] == [] and day["learning"] is False
    assert _bar(day)[1] == ("morning", 410, 465)
    assert snap["rhythm"]["today"] == "free"
    assert snap["rhythm"]["minute"] == 12 * 60
    hm.days.notes["2026-10-24"] = ("home_day", "said")
    assert "today" not in (await _card(hm, 3))["rhythm"]
    await hm.stop()


async def test_only_waking_seen_is_no_bar_yet(tmp_path: Path) -> None:
    """HouseMind saw the house wake up, but never go to sleep: there is nothing to draw yet,
    so the bar stays empty (the card shows an empty track) and the words say why."""
    hm, _, _ = await start(local(24, 12), tmp_path)
    _record(hm, S, H, local(21, 6, 45))
    snap = await _card(hm, 3)
    day = _day(snap)
    assert day["bar"] == [] and day["unsure"] == [] and day["learning"] is False
    assert _moments(snap)["wake"]["learning"] == "1 van de 3 nachten"
    await hm.stop()
