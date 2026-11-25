"""Recognising a shower (E-531) and a word for stale air (E-532), from the side of the people
at home. The administrator's wish after the proposal for the air sensors (22-11-2026): start
with these two – they help in almost every house. Each test fails without the change; what
went wrong is in its docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.world.house_mode import HouseMode
from housemind.world.shower import ShowerWatch
from tests.helpers import wait_seconds as _wait

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
MOTION, HUMIDITY = "binary_sensor.bathroom_motion", "sensor.bathroom_humidity"
LAMP, FAN = "light.bathroom", "fan.bathroom"
CO2, BEDROOM_MOTION = "sensor.bedroom_co2", "binary_sensor.bedroom_motion"
TOPIC = "maintenance:air:bedroom"


async def _start(tmp_path: Path, at: datetime) -> tuple[HouseMind, Any, SimulatedClock]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    for eid, name, cls, state in (
        (CO2, "CO2", "carbon_dioxide", "600"),
        (BEDROOM_MOTION, "Beweging", "motion", "off"),
    ):
        ha.model.entities[eid] = Entity(eid, f"r-{eid}", name, area_id="bedroom", device_class=cls)
        ha.states[eid] = State(eid, state, {}, clock.now())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    assert hm.learning is not None and hm.world is not None
    for skill in ("light_on_entry", "light_off_leave", "ventilate_humidity"):
        hm.learning.steps[(skill, "bathroom")] = Step.AUTONOMOUS
    hm.world.modes.mode = HouseMode.HOME
    return hm, ha, clock


async def _humidity(hm: HouseMind, ha: Any, clock: SimulatedClock, values: list[int]) -> None:
    """A reading a minute, as a humidity sensor sends them."""
    for value in values:
        await ha.simulate(HUMIDITY, str(value))
        await _wait(hm, clock, 60)


async def test_the_light_stays_on_while_someone_showers(tmp_path: Path) -> None:
    """Behind the shower curtain the motion sensor loses the person: the bathroom looked empty
    and its light went out in the middle of the shower; the fan waited until 70%."""
    hm, ha, clock = await _start(tmp_path, datetime(2026, 11, 23, 21, 0, tzinfo=TZ))
    assert hm.world is not None and hm.world.area_roles.get("bathroom") == "bathroom"
    await ha.simulate(MOTION, "on")
    await _wait(hm, clock, 20)
    assert ha.states[LAMP].state == "on"
    await ha.simulate(MOTION, "off")  # behind the curtain
    await _humidity(hm, ha, clock, [56, 58, 61, 64])
    snap = hm.world.snapshot(clock.now())
    assert "bathroom" in snap.showers and snap.occupied("bathroom")
    assert ha.states[FAN].state == "on"  # at once, at 64%
    await _humidity(hm, ha, clock, [70, 76, 80, 83, 85, 86, 86, 86, 86, 86, 86, 86])
    assert ha.states[LAMP].state == "on"  # sixteen minutes without motion
    await _humidity(hm, ha, clock, [84, 82])  # the water is off: out of the shower
    assert "bathroom" not in hm.world.snapshot(clock.now()).showers
    await _wait(hm, clock, 10 * 60)
    assert ha.states[LAMP].state == "off"  # nobody there: it goes off as usual
    await hm.stop()


async def test_humidity_without_anyone_there_is_no_shower(tmp_path: Path) -> None:
    """Steam from a bath that runs, or rain through an open window, with nobody in the room
    for an hour: no shower, no one there."""
    hm, ha, clock = await _start(tmp_path, datetime(2026, 11, 23, 21, 0, tzinfo=TZ))
    await _wait(hm, clock, 60 * 60)
    await _humidity(hm, ha, clock, [56, 60, 64, 68])
    assert hm.world is not None
    snap = hm.world.snapshot(clock.now())
    assert "bathroom" not in snap.showers and not snap.occupied("bathroom")
    await hm.stop()


async def _quarter(hm: HouseMind, clock: SimulatedClock, ha: Any, ppm: int) -> None:
    """A quarter of an hour: the CO₂ is ``ppm``, then HouseMind's review round."""
    await clock.advance(15 * 60)
    await ha.simulate(CO2, str(ppm))
    hm._last_review = None
    await hm.tick()


async def _open(hm: HouseMind) -> set[str]:
    return {str(n["topic"]) for n in await hm.notifications()}


async def _said(hm: HouseMind) -> list[str]:
    rows = await hm.db.fetchall(
        "SELECT text_key, params FROM notifications WHERE topic = ?", (TOPIC,)
    )
    return [hm.languages.text("nl", r["text_key"], **json.loads(r["params"])) for r in rows]


async def test_stale_air_where_someone_is_gets_one_word(tmp_path: Path) -> None:
    """CO₂ far above what is healthy in a room where someone sits said nothing: a CO₂ sensor
    did something only with a fan in the same room, and this house has none."""
    hm, ha, clock = await _start(tmp_path, datetime(2026, 11, 23, 19, 0, tzinfo=TZ))
    await ha.simulate(BEDROOM_MOTION, "on")
    await _quarter(hm, clock, ha, 1300)
    assert TOPIC not in await _open(hm)  # not a quarter of an hour yet
    await _quarter(hm, clock, ha, 1350)
    assert TOPIC in await _open(hm)
    said = await _said(hm)
    assert len(said) == 1
    assert "bedompt" in said[0] and "1.350 ppm" in said[0] and "raam" in said[0], said
    for ppm in (1400, 1250):
        await _quarter(hm, clock, ha, ppm)
    assert len(await _said(hm)) == 1  # once a day per room
    await _quarter(hm, clock, ha, 900)  # a window opened: fresh again
    assert TOPIC not in await _open(hm)
    await _quarter(hm, clock, ha, 1300)
    await _quarter(hm, clock, ha, 1300)
    assert len(await _said(hm)) == 1  # still today
    await hm.stop()


async def test_stale_air_in_an_empty_room_is_no_word(tmp_path: Path) -> None:
    hm, ha, clock = await _start(tmp_path, datetime(2026, 11, 23, 19, 0, tzinfo=TZ))
    for ppm in (1300, 1300, 1300):
        await _quarter(hm, clock, ha, ppm)
    assert TOPIC not in await _open(hm)
    await hm.stop()


async def test_a_stuffy_night_is_told_in_the_morning(tmp_path: Path) -> None:
    """At night no message wakes anyone; in the morning nobody heard that the bedroom's air
    had been stale all night."""
    hm, ha, clock = await _start(tmp_path, datetime(2026, 11, 23, 23, 0, tzinfo=TZ))
    assert hm.world is not None
    assert await hm.set_house_mode("sleep")
    for ppm in (1100, 1300, 1500, 1600, 1450):
        await _quarter(hm, clock, ha, ppm)
    assert TOPIC not in await _open(hm)  # nothing at night
    assert await hm.set_house_mode("home")
    await _quarter(hm, clock, ha, 1000)
    snap = hm.world.look(clock.now())
    text = await hm.briefing("nl", set(), snap)
    assert text is not None and "Vannacht liep het CO₂ in de slaapkamer op tot 1.600 ppm" in text
    await hm.stop()


def _watch(readings: list[tuple[str, float]], day: str) -> list[tuple[datetime, bool]]:
    """The real readings of the bathroom's humidity sensor, as HouseMind sees them – with
    someone in the bathroom – and whether it says "shower" every half minute."""
    watch = ShowerWatch()
    watch.index({HUMIDITY: "bathroom"})
    at = [(datetime.fromisoformat(f"{day}T{t}").replace(tzinfo=TZ), v) for t, v in readings]
    out: list[tuple[datetime, bool]] = []
    now, i = at[0][0], 0
    while now <= at[-1][0]:
        while i < len(at) and at[i][0] <= now:
            watch.see(HUMIDITY, str(at[i][1]), at[i][0], True)
            i += 1
        out.append((now, watch.showering("bathroom", now)))
        now += timedelta(seconds=30)
    return out


def test_a_real_shower_is_one_shower() -> None:
    """In the real house on 21 November the humidity climbed from 51 to 62 in seven minutes,
    jumping down by a point or more on the way: one shower, from the first climb until the
    water was off – not a shower that stops and starts again."""
    real = [
        ("08:51:36", 51.3), ("08:53:07", 53.0), ("08:53:17", 54.1), ("08:53:27", 55.8),
        ("08:53:37", 57.0), ("08:54:12", 55.7), ("08:54:23", 57.6), ("08:54:27", 59.1),
        ("08:54:43", 57.8), ("08:54:58", 56.7), ("08:55:13", 55.7), ("08:55:48", 57.5),
        ("08:56:24", 58.5), ("08:58:46", 59.3), ("08:58:50", 60.7), ("08:58:56", 62.1),
        ("08:59:43", 61.0), ("08:59:51", 59.9), ("09:00:11", 58.7), ("09:00:42", 57.7),
        ("09:01:53", 59.0), ("09:02:48", 58.0), ("09:03:54", 57.0), ("09:11:19", 58.3),
    ]  # fmt: skip
    seen = _watch(real, "2026-11-21")
    on = [t for t, showering in seen if showering]
    assert on and on[0].strftime("%H:%M") == "08:53"
    assert on[-1].strftime("%H:%M") in ("09:03", "09:04")
    assert all(showering for t, showering in seen if on[0] <= t <= on[-1])  # no gap


def test_a_short_shower_counts_and_washing_hands_does_not() -> None:
    """On 23 November a short morning shower took the humidity from 51 to 55; washing at the
    sink took it from 49 to 51 – that is no shower."""
    short = [("06:01:20", 51.4), ("08:15:45", 52.4), ("08:17:51", 53.4), ("08:19:17", 54.4),
             ("08:19:58", 55.4), ("08:32:46", 54.4)]  # fmt: skip
    assert any(showering for _, showering in _watch(short, "2026-11-23"))
    wash = [("10:00:10", 49.4), ("10:30:25", 50.6), ("10:30:40", 51.6), ("10:31:11", 50.5),
            ("11:24:16", 51.5)]  # fmt: skip
    assert not any(showering for _, showering in _watch(wash, "2026-11-23"))
