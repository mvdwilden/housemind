"""One yes for the calendar (20.19, E-21): without an administrator's yes HouseMind does not
read the calendar; a house that already runs asks the administrators once. Each test fails
without the build."""

from __future__ import annotations

from pathlib import Path

from housemind.core.model import Entity, State
from housemind.server import views
from tests.test_house_modes import _start


async def _with_calendar(tmp_path: Path):  # type: ignore[no-untyped-def]
    hm, ha = await _start(tmp_path)
    await hm.db.set_setting("setup", "done")
    await ha.add_entity(
        Entity("calendar.family", "r-calendar", "Gezin"),
        State(
            "calendar.family", "on", {"message": "Schoolvakantie", "all_day": True}, hm.clock.now()
        ),
    )
    ha.events["calendar.family"] = [{"start": "2026-10-19T09:30:00+02:00", "summary": "Tandarts"}]
    await hm._refresh_model()
    return hm, ha


async def test_without_a_yes_the_calendar_is_not_read(tmp_path: Path) -> None:
    hm, ha = await _with_calendar(tmp_path)
    assert hm._calendars() == []
    asked = len(ha.calls)
    await hm.clock.advance(16 * 60)  # type: ignore[attr-defined]
    await hm.tick()
    assert hm.days.get(hm.clock.now().date()) is None  # no school holiday read
    assert not [c for c in ha.calls[asked:] if "calendar" in str(c)]
    await hm.stop()


async def test_a_running_house_asks_the_administrators_once(tmp_path: Path) -> None:
    hm, _ = await _with_calendar(tmp_path)
    await hm.clock.advance(16 * 60)  # type: ignore[attr-defined]
    await hm.tick()
    items = await views.notifications(hm, "nl", True, "demo-user-1")
    asked = [i for i in items if str(i["text"]).startswith("Mag ik de agenda")]
    assert len(asked) == 1 and asked[0]["actions"] == ["yes", "no"]
    assert not [
        i
        for i in await views.notifications(hm, "nl", False, "demo-user-2")
        if str(i["text"]).startswith("Mag ik de agenda")
    ]  # administrators only
    await hm.answer_calendar(False)
    await hm.clock.advance(16 * 60)  # type: ignore[attr-defined]
    await hm.tick()
    open_now = [
        r
        for r in await hm.notifications()
        if r["topic"] == "calendar_consent" and r["resolved_at"] is None
    ]
    assert open_now == []  # no stays no: not asked again
    await hm.answer_calendar(True)
    assert hm._calendars() == ["calendar.family"]
    assert (await views.house_settings(hm, "nl"))["calendar"] is True
    await hm.stop()
