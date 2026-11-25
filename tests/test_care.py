"""Care and wellbeing (domain 17, experimental, E-21): unusual quiet, told to one contact."""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.care import quiet_reason
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")


def local(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=TZ)


def test_what_counts_as_unusually_quiet() -> None:
    night = local(19, 23, 0)
    assert quiet_reason(local(20, 8, 0), night, night, time(7, 0)) is None  # not yet
    assert quiet_reason(local(20, 9, 5), night, night, time(7, 0)) == "morning"
    assert quiet_reason(local(20, 9, 5), local(20, 7, 10), night, time(7, 0)) is None
    assert quiet_reason(local(20, 9, 59), night, night, None) is None  # 10:00 without rhythm
    assert quiet_reason(local(20, 10, 1), night, night, None) == "morning"
    assert quiet_reason(local(20, 15, 30), local(20, 9, 0), night, time(7, 0)) == "day"
    assert quiet_reason(local(20, 13, 0), local(20, 9, 0), night, time(7, 0)) is None


async def start(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(local(19, 22, 0).astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    for i, level in ((1, 3), (2, 1)):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (f"demo-user-{i}", f"Resident {i}", level, json.dumps({}), clock.now().isoformat()),
        )
    await hm._refresh_people()
    return hm, clock, house.ha


async def until(hm: HouseMind, clock: SimulatedClock, moment: datetime) -> None:
    while clock.now() < moment.astimezone(UTC):
        await clock.advance(15 * 60)
        hm._last_review = None
        await hm.tick()


def care_messages(ha: FakeHomeAssistant) -> list[tuple[str, str]]:
    return [(n[0], n[2]) for n in ha.notifications if "Misschien even bellen" in n[2]]


async def test_nothing_without_an_explicit_yes(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path)
    await hm.set_house_setting(
        "care", {"person": "person.resident_3", "contact": "demo-user-2", "consent": False}
    )
    await hm.set_house_setting("experimental", ["care"])
    await until(hm, clock, local(20, 11, 0))
    assert care_messages(ha) == []
    await hm.stop()


async def test_a_quiet_morning_reaches_only_the_contact(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path)
    await hm.set_house_setting(
        "care", {"person": "person.resident_3", "contact": "demo-user-2", "consent": True}
    )
    await hm.set_house_setting("experimental", ["care"])
    # Resident 3 has an account: they say yes themselves, once they open the app (QA 2, M-8).
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-3", "Resident 3", 1, json.dumps({}), clock.now().isoformat()),
    )
    assert await hm.answer_care("demo-user-3", True)
    await until(hm, clock, local(20, 10, 15))
    sent = care_messages(ha)
    # Only the contact – even though they are on Carefree, they asked for exactly this.
    assert [phone for phone, _ in sent] == ["notify.mobile_app_phone_2"]
    assert "vanochtend nog geen beweging" in sent[0][1]
    # Nobody reacts for half an hour: it is still only for the contact, never passed on to
    # the other administrators (found by the manual).
    await until(hm, clock, local(20, 10, 45))
    assert [phone for phone, _ in care_messages(ha)] == ["notify.mobile_app_phone_2"]
    # Movement again: the message disappears by itself.
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    await until(hm, clock, local(20, 11, 15))
    assert not [n for n in await hm.notifications() if str(n["topic"]).startswith("care:")]
    assert clock.now() - local(19, 22).astimezone(UTC) > timedelta(hours=12)
    await hm.stop()
