"""Uit na … zonder beweging, per lamp (E-575), standen gaan voor (E-576) en even dimmen in de
kleur van Gedimd (E-577): what a resident sees in the time machine of the demo house.

The administrator's own example: the living room lamp on at once with movement, and off the
moment nobody is there (0 minutes), or after as many minutes as they choose. A lamp whose
Gedimd is purple is purple whenever a rule asks for Gedimd."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.levels import nearest
from housemind.brain.when import when
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import NAMED_HS, FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.world.house_mode import HouseMode
from tests.test_day_rhythm import AFTERNOON, GROUP, _come_in, _lamp, _rounds, _without

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
READING = "light.living_reading"  # dims, no colour
CEILING = "light.living_ceiling"  # warm to white
COLOUR = "light.living_colour"  # colours too: added for these tests
PRESENCE = "binary_sensor.living_presence"
PURPLE = {
    "pct": [10, 30, 55, 100],
    "colour": [{"name": "mediumpurple"}, {"kelvin": 2700}, {"rhythm": True}, {"rhythm": True}],
    "dim": "keep",
    "rhythm": False,
}


def local(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=TZ)


async def start(
    at: datetime, tmp_path: Path, colour_in: tuple[str, ...] = ()
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    for area in colour_in:  # a lamp that shows colours, as many houses have
        lamp = f"light.{area}_colour"
        caps = {
            "supported_color_modes": ["color_temp", "hs"],
            "min_color_temp_kelvin": 2200,
            "max_color_temp_kelvin": 6500,
        }
        house.ha.model.entities[lamp] = Entity(
            lamp,
            f"demo-{area}-colour",
            "Kleurlamp",
            device_id=lamp,
            area_id=area,
            capabilities=caps,
        )
        house.ha.states[lamp] = State(lamp, "off", dict(caps), clock.now())
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    for i in (1, 2):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (f"demo-user-{i}", f"Resident {i}", 4, json.dumps({}), clock.now().isoformat()),
        )
    await hm._refresh_people()
    assert hm.learning is not None
    for skill in ("light_off_leave", "light_on_entry", "night_light"):
        for area in ("living", "bathroom"):
            hm.learning.steps[(skill, area)] = Step.AUTONOMOUS
    return hm, clock, house.ha


async def until(clock: SimulatedClock, hm: HouseMind, moment: datetime) -> None:
    target = moment.astimezone(UTC)
    while clock.now() < target:
        await clock.advance(min(30.0, (target - clock.now()).total_seconds()))
        await hm.tick()


def pct(ha: FakeHomeAssistant, entity_id: str) -> int | None:
    state = ha.states[entity_id]
    if state.state != "on":
        return None
    return round(state.attributes.get("brightness", 0) * 100 / 255)


async def evening_in_the_living_room(
    tmp_path: Path, colour: bool = False
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    """Someone in the living room on an October evening, the lamps on full."""
    hm, clock, ha = await start(local(19, 20, 0), tmp_path, ("living",) if colour else ())
    await ha.simulate(PRESENCE, "on")
    await ha.simulate(READING, "on", {"brightness": 255})
    await ha.simulate(CEILING, "on", {"brightness": 255, "color_temp_kelvin": 4000})
    await clock.advance(5)
    await hm.tick()
    return hm, clock, ha


async def nobody_there(hm: HouseMind, clock: SimulatedClock, ha: FakeHomeAssistant) -> datetime:
    await ha.simulate(PRESENCE, "off")
    await clock.advance(1)
    await hm.tick()
    return clock.now()


# -- a wait of its own, per lamp (E-575) ------------------------------------------------------


async def test_at_zero_minutes_the_lamp_goes_off_as_soon_as_nobody_is_there(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await evening_in_the_living_room(tmp_path)
    assert await hm.off_wait_save(READING, 0)
    empty = await nobody_there(hm, clock, ha)
    assert ha.states[READING].state == "off"  # at once, without dimming first
    assert pct(ha, CEILING) == 100  # the other lamp keeps the room's wait
    dimmed = await hm.db.fetchall(
        "SELECT data FROM decisions WHERE entity_id = ? AND action = 'light.turn_on' AND at >= ?",
        (READING, (empty - timedelta(seconds=1)).isoformat()),
    )
    assert not dimmed, "no dim warning at 0 minutes"
    await hm.stop()


async def test_four_minutes_goes_off_at_four_dimmed_in_the_last_part(tmp_path: Path) -> None:
    hm, clock, ha = await evening_in_the_living_room(tmp_path)
    assert await hm.off_wait_save(READING, 4)
    empty = await nobody_there(hm, clock, ha)
    await until(clock, hm, empty + timedelta(minutes=2, seconds=50))
    assert pct(ha, READING) == 100  # not yet
    await until(clock, hm, empty + timedelta(minutes=3, seconds=10))
    assert pct(ha, READING) == 15  # Gedimd: the last minute of its wait
    await until(clock, hm, empty + timedelta(minutes=4, seconds=1))
    assert ha.states[READING].state == "off"
    assert pct(ha, CEILING) == 100  # the room's own wait is longer
    await hm.stop()


async def test_another_lamp_keeps_the_rooms_learned_wait(tmp_path: Path) -> None:
    hm, clock, ha = await evening_in_the_living_room(tmp_path)
    assert hm.learning is not None
    hm.learning.seed_hold("living", 6.0, clock.now())  # an automation's wait, as start (E-444)
    assert await hm.off_wait_save(READING, 1)
    empty = await nobody_there(hm, clock, ha)
    await until(clock, hm, empty + timedelta(minutes=1, seconds=5))
    assert ha.states[READING].state == "off"
    assert pct(ha, CEILING) == 100
    await until(clock, hm, empty + timedelta(minutes=8))
    assert ha.states[CEILING].state == "off"  # with the room: 6 minutes, then its warning
    await hm.stop()


async def test_someone_back_during_its_warning_gets_the_light_back_and_teaches_nothing(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await evening_in_the_living_room(tmp_path)
    assert hm.learning is not None
    assert await hm.off_wait_save(READING, 2)
    before = hm.learning.value("light_off_leave", "living", "*", "hold_minutes")
    empty = await nobody_there(hm, clock, ha)
    await until(clock, hm, empty + timedelta(minutes=1, seconds=20))
    assert pct(ha, READING) == 15
    assert hm.skills is not None and hm.skills.dimming("living")
    await ha.simulate(PRESENCE, "on")
    await clock.advance(1)
    await hm.tick()
    assert pct(ha, READING) == 100  # back as it was
    assert not hm.skills.dimming("living")
    # A wait someone set is no wait the room taught: it learns nothing longer from it.
    assert hm.learning.value("light_off_leave", "living", "*", "hold_minutes") == before
    await hm.stop()


async def test_let_housemind_learn_it_gives_the_lamp_back_to_the_room(tmp_path: Path) -> None:
    hm, _clock, _ha = await evening_in_the_living_room(tmp_path)
    assert await hm.off_wait_save(READING, 4)
    assert not await hm.off_wait_save(READING, 31)  # 0 to 30 only
    assert not await hm.off_wait_save(READING, 2.5)  # whole minutes
    assert not await hm.off_wait_save("binary_sensor.living_presence", 3)  # lamps only
    row = {r["id"]: r for r in views.devices(hm, "nl")}[READING]["levels"]["offWait"]
    assert row["minutes"] == 4 and row["line"] == "Uit na 4 min zonder beweging · jouw keuze"
    assert row["sensed"] and not row["motionOnly"]  # a presence sensor watches the living room
    assert await hm.off_wait_save(READING, 0)
    row = {r["id"]: r for r in views.devices(hm, "nl")}[READING]["levels"]["offWait"]
    assert row["line"] == "Meteen uit als er niemand meer is · jouw keuze"
    assert await hm.off_wait_save(READING, None)
    assert hm.light_levels.off_wait(READING) is None
    row = {r["id"]: r for r in views.devices(hm, "nl")}[READING]["levels"]["offWait"]
    assert row["minutes"] is None
    assert row["line"] == "Uit na 20 min zonder beweging · startwaarde"  # a living room
    await hm.stop()


async def test_the_wait_survives_a_restart_and_brein_says_it(tmp_path: Path) -> None:
    hm, clock, _ha = await evening_in_the_living_room(tmp_path)
    assert await hm.off_wait_save(READING, 3)
    await hm.stop()
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    assert hm.light_levels.off_wait(READING) == 3
    row = when(hm, "nl", "light_off_leave", "living", Step.AUTONOMOUS, 4)
    assert "eigen wachttijd: Woonkamer leeslamp 3 minuten" in row.get("whenDetail", ""), row
    await hm.stop()


async def test_a_room_with_only_motion_says_someone_sitting_still_is_not_seen(
    tmp_path: Path,
) -> None:
    hm, _clock, _ha = await start(local(19, 20, 0), tmp_path)
    rows = {r["id"]: r for r in views.devices(hm, "nl")}
    kitchen = rows["light.kitchen"]["levels"]["offWait"]
    assert kitchen["motionOnly"] and kitchen["sensed"]
    assert rows["light.garden"]["levels"].get("offWait") is None  # outside: never by itself
    await hm.stop()


# -- standen gaan voor (E-576) -----------------------------------------------------------------


def test_the_nearest_stand_and_halfway_the_softer() -> None:
    assert [nearest(f) for f in (3, 15, 22, 25, 30, 47.5, 48, 80, 81, 100)] == [
        0,
        0,
        0,
        0,
        1,
        1,
        2,
        2,
        3,
        3,
    ]


async def test_what_the_house_learned_becomes_the_nearest_stand_with_its_colour(
    tmp_path: Path,
) -> None:
    """Learned 22% when someone comes in: on a lamp with standen that is Gedimd – purple."""
    hm, clock, ha = await start(local(19, 20, 0), tmp_path, ("living",))
    assert hm.learning is not None
    assert await hm.levels_save(COLOUR, PURPLE)
    hm.learning.values[("light_on_entry", "living", "*", "brightness_pct")] = (
        22.0,
        5.0,
        clock.now(),
    )
    await ha.simulate(PRESENCE, "on")
    await clock.advance(11)
    await hm.tick()
    colour = ha.states[COLOUR]
    assert colour.state == "on" and pct(ha, COLOUR) == 10  # its own Gedimd
    assert colour.attributes.get("hs_color") == NAMED_HS["mediumpurple"]
    # A lamp on the start values does what it learned, as before.
    assert pct(ha, READING) == 22
    await hm.stop()


async def test_the_night_light_takes_a_purple_gedimd(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 23, 30), tmp_path, ("bathroom",))
    lamp = "light.bathroom_colour"
    assert await hm.levels_save(lamp, PURPLE)
    assert await hm.set_house_mode(HouseMode.SLEEP.value)
    await ha.simulate("binary_sensor.bathroom_motion", "on")
    await clock.advance(11)
    await hm.tick()
    assert pct(ha, lamp) == 10
    assert ha.states[lamp].attributes.get("hs_color") == NAMED_HS["mediumpurple"]
    await hm.stop()


async def test_the_days_rhythm_only_picks_a_stand(tmp_path: Path) -> None:
    """HouseMind's own rhythm (no integration for it in Home Assistant): bright and white in the
    afternoon is Helder, the evening is Normaal with its own fixed colour – in steps."""
    hm, clock, ha = await _without(tmp_path, AFTERNOON)
    stands = {
        "pct": [5, 20, 50, 90],
        "colour": [{"kelvin": 2200}, {"kelvin": 3000}, {"rhythm": True}, {"rhythm": True}],
        "dim": "keep",
        "rhythm": True,
    }
    assert await hm.levels_save(GROUP, stands)
    await _come_in(ha)
    state, now_pct, warm = _lamp(ha, GROUP)
    assert state == "on" and now_pct == 50  # its Helder, not a percentage of its own
    assert warm is not None and 3800 < warm <= 4000  # Helder follows the day's colour
    await _rounds(hm, clock, 5 * 60)
    assert _lamp(ha, GROUP) == ("on", 20, 3000)  # Normaal in the evening, its fixed colour
    await hm.stop()


# -- even dimmen in de kleur van Gedimd (E-577) ------------------------------------------------


async def test_the_warning_dims_purple_and_coming_back_brings_the_colour_back(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await evening_in_the_living_room(tmp_path, colour=True)
    assert await hm.levels_save(COLOUR, PURPLE)
    await ha.simulate(COLOUR, "on", {"brightness": 255, "color_temp_kelvin": 4000})
    assert await hm.off_wait_save(COLOUR, 2)
    empty = await nobody_there(hm, clock, ha)
    await until(clock, hm, empty + timedelta(minutes=1, seconds=20))
    dimmed = ha.states[COLOUR]
    assert pct(ha, COLOUR) == 10 and dimmed.attributes.get("hs_color") == NAMED_HS["mediumpurple"]
    await ha.simulate(PRESENCE, "on")
    await clock.advance(1)
    await hm.tick()
    back = ha.states[COLOUR]
    assert pct(ha, COLOUR) == 100
    assert back.attributes.get("color_temp_kelvin") == 4000  # its colour again
    assert "hs_color" not in back.attributes
    await hm.stop()


async def test_a_lamp_on_the_start_values_dims_in_its_own_colour(tmp_path: Path) -> None:
    hm, clock, ha = await evening_in_the_living_room(tmp_path)
    assert await hm.off_wait_save(CEILING, 2)
    empty = await nobody_there(hm, clock, ha)
    await until(clock, hm, empty + timedelta(minutes=1, seconds=20))
    assert pct(ha, CEILING) == 15
    assert ha.states[CEILING].attributes.get("color_temp_kelvin") == 4000  # as it was (E-495)
    await hm.stop()
