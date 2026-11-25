"""A boiler that does not heat (E-526, B-450), from the side of the people at home.

Found in the house (16-11-2026): the administrator asked whether HouseMind had turned the hot
water down, "because the water was not warm at all any more". It had not – the boiler stayed
at 52 °C – but the heat pump itself stopped heating on Sunday morning, after it heated at
04:46, and nobody knew until the shower was cold on Monday. Its tank went 52 → 44 → 39 → 37 →
36. Now HouseMind tells everyone once, when a boiler has not heated for more than a day and is
far below its own setting, and the message goes away by itself once it heats again."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
BOILER = "water_heater.boiler"
TOPIC = f"maintenance:boiler:{BOILER}"


async def start(
    at: datetime, tmp_path: Path
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, clock, house.ha


async def quarter(hm: HouseMind, clock: SimulatedClock, ha: FakeHomeAssistant, temp: float) -> None:
    """A quarter of an hour: the boiler's water is ``temp``, then HouseMind's review round."""
    await clock.advance(15 * 60)
    await ha.simulate(BOILER, ha.states[BOILER].state, {"current_temperature": temp})
    hm._last_review = None
    await hm.tick()


async def open_topics(hm: HouseMind) -> set[str]:
    return {str(n["topic"]) for n in await hm.notifications()}


async def texts(hm: HouseMind) -> list[str]:
    rows = await hm.db.fetchall(
        "SELECT text_key, params FROM notifications WHERE topic = ?", (TOPIC,)
    )
    return [hm.languages.text("nl", r["text_key"], **json.loads(r["params"])) for r in rows]


def weekend(hours: float, start: float = 52.0, end: float = 36.0) -> list[float]:
    """The tank as it went in the house: no heating, slowly colder, a shower now and then."""
    steps = int(hours * 4)
    return [round(start - (start - end) * i / steps) for i in range(1, steps + 1)]


async def test_a_boiler_that_stopped_heating_gets_one_important_word(tmp_path: Path) -> None:
    # Sunday 04:46 it heated to 52 °C, set to 52 °C – the last time.
    hm, clock, ha = await start(datetime(2026, 11, 15, 4, 46, tzinfo=TZ), tmp_path)
    await ha.simulate(BOILER, "heat_pump", {"temperature": 52, "current_temperature": 52})
    await quarter(hm, clock, ha, 52)
    temps = weekend(26)  # until Monday about 06:45
    for temp in temps[:-2]:
        await quarter(hm, clock, ha, temp)
        assert TOPIC not in await open_topics(hm)  # less than a day: nothing yet
    for temp in temps[-2:]:
        await quarter(hm, clock, ha, temp)
    assert TOPIC in await open_topics(hm)
    said = await texts(hm)
    assert len(said) == 1  # once, not every quarter
    assert "al meer dan een dag niet op" in said[0]
    assert "nog 36°" in said[0] and "op 52°" in said[0]
    assert "storing" in said[0]
    row = await hm.db.fetchone("SELECT kind FROM notifications WHERE topic = ?", (TOPIC,))
    assert row["kind"] == "important"  # straight away, not in the evening bundle
    # Monday 08:34 someone switches on the quick heating: 23 → 29 °C. The word goes by itself.
    await quarter(hm, clock, ha, 23)
    assert TOPIC in await open_topics(hm)
    await quarter(hm, clock, ha, 29)
    assert TOPIC not in await open_topics(hm)
    await hm.stop()


async def test_a_boiler_that_heats_every_night_never_gets_a_word(tmp_path: Path) -> None:
    # It heats once a day at a fixed time, at night, a little later now and then – and on the
    # days between it drops far below its setting after the showers.
    hm, clock, ha = await start(datetime(2026, 11, 10, 3, 0, tzinfo=TZ), tmp_path)
    await ha.simulate(BOILER, "heat_pump", {"temperature": 55, "current_temperature": 55})
    for day in range(4):
        cold = weekend(24 + (day % 2) * 0.75, start=55, end=38)
        for temp in cold:
            await quarter(hm, clock, ha, temp)
        for temp in (44, 50, 55):
            await quarter(hm, clock, ha, temp)
        assert TOPIC not in await open_topics(hm)
    assert await texts(hm) == []
    await hm.stop()


async def test_off_away_or_on_vacation_is_cold_on_purpose(tmp_path: Path) -> None:
    hm, clock, ha = await start(datetime(2026, 11, 15, 4, 46, tzinfo=TZ), tmp_path)
    # Switched off: two days cold, no word.
    await ha.simulate(BOILER, "off", {"temperature": 52, "current_temperature": 52})
    for temp in weekend(48, end=25):
        await quarter(hm, clock, ha, temp)
    assert TOPIC not in await open_topics(hm)
    # On again – it is on away in its own app: still no word.
    await ha.simulate(BOILER, "heat_pump", {"away_mode": "on"})
    for _ in range(30 * 4):
        await quarter(hm, clock, ha, 25)
    assert TOPIC not in await open_topics(hm)
    # Back from away, the house on vacation: still nothing.
    assert await hm.set_house_mode("vacation")
    await ha.simulate(BOILER, "heat_pump", {"away_mode": "off"})
    for _ in range(30 * 4):
        await quarter(hm, clock, ha, 25)
    assert TOPIC not in await open_topics(hm)
    # Home again and it still does not heat: the clock starts now, a day later the word.
    assert await hm.set_house_mode("home")
    for _ in range(25 * 4):
        await quarter(hm, clock, ha, 25)
    assert TOPIC not in await open_topics(hm)
    for _ in range(2 * 4):
        await quarter(hm, clock, ha, 25)
    assert TOPIC in await open_topics(hm)
    await hm.stop()


async def test_a_restart_does_not_start_the_day_again(tmp_path: Path) -> None:
    at = datetime(2026, 11, 15, 4, 46, tzinfo=TZ)
    hm, clock, ha = await start(at, tmp_path)
    await ha.simulate(BOILER, "heat_pump", {"temperature": 52, "current_temperature": 52})
    for temp in weekend(20, end=40):
        await quarter(hm, clock, ha, temp)
    await hm.stop()
    # HouseMind restarts (an update): what it knew of the boiler stays.
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    await hm._refresh_people()
    for temp in [39] * (7 * 4):
        await quarter(hm, clock, ha, temp)
    assert clock.now() - at.astimezone(UTC) < timedelta(hours=28)
    assert TOPIC in await open_topics(hm)
    await hm.stop()


async def test_close_to_its_setting_is_no_word(tmp_path: Path) -> None:
    # Nobody uses water for two days: it cools a few degrees and does not need to heat.
    hm, clock, ha = await start(datetime(2026, 11, 15, 4, 46, tzinfo=TZ), tmp_path)
    await ha.simulate(BOILER, "heat_pump", {"temperature": 52, "current_temperature": 52})
    for temp in weekend(48, end=44):
        await quarter(hm, clock, ha, temp)
    assert TOPIC not in await open_topics(hm)
    await hm.stop()
