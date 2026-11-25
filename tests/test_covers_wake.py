"""Curtains open in a bedroom when the people there are up (E-482 – E-484, design 71).

From the side of the people in the house: a bedroom's curtains stay closed while someone is in
bed, while it is still dark and during the night; they open a few minutes after the bed is
empty once it is light; a room nobody slept in opens with the rest of the house; a room where
HouseMind cannot see sleep opens only when everyone is out. Works with what a house has – a bed
sensor, a presence sensor in the room, or nothing – and an automation that opens bedroom
curtains now teaches the row.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.brain.pretend import apply, available, room_of
from housemind.brain.when import When
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source, State
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind
from housemind.world.house_mode import HouseMode
from tests.test_real_house_counts import _add, _house, _until

CURTAIN = "cover.bedroom_curtain"
BED = "binary_sensor.bedroom_bed"
ROOM = "binary_sensor.bedroom_presence"
KITCHEN = "binary_sensor.kitchen_motion"
NIGHT = datetime(2026, 10, 19, 21, 0)  # 23:00 local (UTC+2), Monday
DAWN = datetime(2026, 10, 20, 4, 40)  # 06:40 local, still dark in Amsterdam
LIGHT = datetime(2026, 10, 20, 7, 10)  # 09:10 local, the sun well up


def _bedroom(bed: bool = False, room: bool = False, plug: bool = False) -> Any:
    def prepare(ha: Any) -> None:
        old = ha.states[CURTAIN]
        ha.states[CURTAIN] = State(CURTAIN, "closed", dict(old.attributes), old.last_changed)
        if bed:  # a sensor under the bed
            _add(ha, BED, "Bed", "bedroom", "off", device_class="occupancy")
        if room:  # a presence sensor that also sees someone lying still
            _add(ha, ROOM, "Aanwezigheid slaapkamer", "bedroom", "off", device_class="presence")
        if plug:  # 'bed' in a name, but no presence: the plug of an electric blanket
            _add(
                ha,
                "binary_sensor.bed_warmer",
                "Bed warmer plug",
                "bedroom",
                "on",
                device_class="plug",
            )

    return prepare


async def _start(tmp_path: Path, **sensors: bool) -> tuple[HouseMind, SimulatedClock, Any]:
    hm, clock, ha = await _house(tmp_path, located=True, prepare=_bedroom(**sensors))
    await hm.set_admin_step("covers_open_wake@bedroom", "autonomous", "demo-user-1")
    return hm, clock, ha


async def _night(hm: HouseMind, clock: SimulatedClock, ha: Any, sensor: str | None) -> None:
    """Someone goes to bed at 23:00 and the house sleeps."""
    await _until(hm, clock, NIGHT.replace(tzinfo=clock.now().tzinfo), step=600)
    if sensor is not None:
        await ha.simulate(sensor, "on")
    assert hm.world is not None
    hm.world.modes.mode = HouseMode.SLEEP
    await hm.tick()


async def _wake_house(hm: HouseMind, clock: SimulatedClock, ha: Any, at: datetime) -> None:
    """Someone is up in the kitchen: the house wakes."""
    await clock.advance((at.replace(tzinfo=clock.now().tzinfo) - clock.now()).total_seconds())
    await ha.simulate(KITCHEN, "on")
    await hm.tick()
    await ha.simulate(KITCHEN, "off")
    await hm.tick()


def _state(ha: Any) -> str:
    return str(ha.states[CURTAIN].state)


async def test_the_curtains_open_after_the_bed_is_empty_once_it_is_light(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path, bed=True)
    assert hm.world is not None
    assert hm.world.sleep_watch.signals["bedroom"][0] == "bed"
    await _night(hm, clock, ha, BED)
    # A trip to the toilet at 03:00 is no getting up: the house sleeps, and it is dark.
    toilet = datetime(2026, 10, 20, 1, 0, tzinfo=clock.now().tzinfo)
    await clock.advance((toilet - clock.now()).total_seconds())
    await ha.simulate(BED, "off")
    await _until(hm, clock, clock.now() + timedelta(minutes=8))
    assert _state(ha) == "closed"
    await ha.simulate(BED, "on")
    await hm.tick()
    # Up at 06:40, still dark: the curtains wait for the light.
    await _wake_house(hm, clock, ha, DAWN)
    await ha.simulate(BED, "off")
    await _until(hm, clock, DAWN.replace(tzinfo=clock.now().tzinfo) + timedelta(minutes=30))
    assert _state(ha) == "closed"
    await _until(hm, clock, LIGHT.replace(tzinfo=clock.now().tzinfo))
    assert _state(ha) == "open"


async def test_someone_still_in_bed_keeps_them_closed_until_a_few_minutes_after(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await _start(tmp_path, bed=True)
    await _night(hm, clock, ha, BED)
    # Another person is up and the house is awake, but someone sleeps in on in here.
    await _wake_house(hm, clock, ha, DAWN)
    await _until(hm, clock, LIGHT.replace(tzinfo=clock.now().tzinfo))
    assert _state(ha) == "closed"
    await ha.simulate(BED, "off")
    await _until(hm, clock, clock.now() + timedelta(minutes=3))
    assert _state(ha) == "closed"  # out of bed only a moment ago
    await _until(hm, clock, clock.now() + timedelta(minutes=3))
    assert _state(ha) == "open"


async def test_a_presence_sensor_in_the_room_waits_until_the_room_is_empty(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path, room=True)
    assert hm.world is not None
    assert hm.world.sleep_watch.signals["bedroom"][0] == "room"
    await _night(hm, clock, ha, ROOM)
    await _wake_house(hm, clock, ha, DAWN)
    await _until(hm, clock, LIGHT.replace(tzinfo=clock.now().tzinfo))
    assert _state(ha) == "closed"  # getting dressed in the room: still there
    await ha.simulate(ROOM, "off")
    await _until(hm, clock, clock.now() + timedelta(minutes=6))
    assert _state(ha) == "open"


async def test_a_bedroom_nobody_slept_in_opens_with_the_rest_of_the_house(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path, bed=True)
    await _night(hm, clock, ha, None)  # nobody in this bed tonight
    await _wake_house(hm, clock, ha, DAWN)
    await _until(hm, clock, LIGHT.replace(tzinfo=clock.now().tzinfo))
    assert _state(ha) == "open"
    reasons = [
        " ".join(hm.explain(r.decision, 3, "nl"))
        for r in hm.recent.values()
        if r.decision.request.skill == "covers_open_wake"
    ]
    assert any("niemand sliep" in r for r in reasons), reasons


async def test_without_a_sign_of_sleep_only_everyone_out_opens_them(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path, plug=True)
    assert hm.world is not None
    assert hm.world.sleep_watch.signals["bedroom"][0] == "none"  # a plug is no sign
    await _night(hm, clock, ha, None)
    await _wake_house(hm, clock, ha, DAWN)
    await _until(hm, clock, LIGHT.replace(tzinfo=clock.now().tzinfo))
    assert _state(ha) == "closed"  # it cannot see whether someone sleeps: nothing
    for person in ("person.resident_1", "person.resident_2", "person.resident_3"):
        await ha.simulate(person, "not_home")
    hm.world.modes.mode = HouseMode.AWAY
    await _until(hm, clock, clock.now() + timedelta(minutes=2))
    assert _state(ha) == "open"


async def test_people_win_closed_again_stays_closed_today(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path, bed=True)
    await _night(hm, clock, ha, BED)
    await _wake_house(hm, clock, ha, DAWN)
    await ha.simulate(BED, "off")
    await _until(hm, clock, LIGHT.replace(tzinfo=clock.now().tzinfo))
    assert _state(ha) == "open"
    await ha.simulate(CURTAIN, "closed", by=Source.PERSON)
    await _until(hm, clock, clock.now() + timedelta(minutes=30))
    assert _state(ha) == "closed"


async def test_an_automation_opening_bedroom_curtains_teaches_the_row(tmp_path: Path) -> None:
    """As in the real house: open at sunrise when a helper says it is morning."""
    automation = {
        "alias": "Slaapkamer - Gordijnen open bij zonsopgang",
        "triggers": [
            {"trigger": "state", "entity_id": "input_select.modus", "to": "Ochtendroutine"},
            {"trigger": "sun", "event": "sunrise", "offset": 0},
        ],
        "conditions": [{"condition": "state", "entity_id": "sun.sun", "state": "above_horizon"}],
        "actions": [{"action": "cover.open_cover", "target": {"entity_id": [CURTAIN]}}],
    }
    hm, _clock, _ha = await _house(
        tmp_path, automations={"automation.bedroom_open": automation}, prepare=_bedroom(bed=True)
    )
    reading = hm.automations.readings["automation.bedroom_open"]
    assert ("covers_open_wake", "bedroom") in reading.learned
    assert ("covers_open_morning", "bedroom") not in reading.learned
    assert hm.steering("covers_open_wake", "bedroom")  # while it is on, HouseMind only watches


async def test_housemind_learns_how_long_after_getting_up(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path, bed=True)
    await hm.set_admin_step("covers_open_wake@bedroom", "observe", "demo-user-1")
    await _night(hm, clock, ha, BED)
    await _wake_house(hm, clock, ha, LIGHT)
    await ha.simulate(BED, "off")
    await _until(hm, clock, clock.now() + timedelta(minutes=12))
    await ha.simulate(CURTAIN, "open", by=Source.PERSON)
    await hm.tick()
    assert hm.learning is not None
    mean, weight, _at = hm.learning.values[("covers_open_wake", "bedroom", "*", "wait_minutes")]
    assert 11 <= mean <= 13 and weight >= 1


async def test_brein_says_what_housemind_sees_in_the_bedroom(tmp_path: Path) -> None:
    hm, _clock, _ha = await _start(tmp_path, bed=True)
    said = When(hm, "nl").row("covers_open_wake", "bedroom", Step.AUTONOMOUS, 4)
    assert "uit bed" in said["when"] and "licht" in said["when"], said
    assert "wie er in bed ligt: Bed" in said["whenDetail"], said
    other, _c, _h = await _start(tmp_path / "plain")
    said = When(other, "nl").row("covers_open_wake", "bedroom", Step.OBSERVE, 3)
    assert said["when"].startswith("Ik zou"), said
    assert "Ik zie hier niet of iemand slaapt" in said["when"], said


async def test_pretend_out_of_bed_in_a_bedroom_that_sees_sleep(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path, bed=True)
    assert hm.world is not None
    room = room_of(hm.model, "bedroom", lambda entity_id: None, "bed")
    assert "out_of_bed" in available(room, True)
    picture, _states = apply(
        hm.world.look(clock.now()), dict(ha.states), "bedroom", ["out_of_bed"], room, []
    )
    view = picture.sleep["bedroom"]
    assert not view.asleep and view.slept and view.empty_since is not None
    plain = room_of(hm.model, "bedroom", lambda entity_id: None)
    assert "out_of_bed" not in available(plain, True)
