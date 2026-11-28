"""The day's rhythm of the light (E-465 – E-470, design 68). The administrator's wish: "Mijn huis
is gewend om warmere kleuren te gebruiken in de avond en wat wittere, lichtere kleuren overdag …
ik wil dat je het gedrag van Adaptive Lighting overneemt, dus dat je ziet hoe Adaptive Lighting
is geconfigureerd." From the user's side: what a lamp does in a demo house, with the group's
settings as Home Assistant gives them. Each test fails without the change; what it checks is its
docstring."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from housemind.brain.daylight import brightness, kelvin, sun_day, sun_position
from housemind.brain.learning import Learning
from housemind.brain.readiness import readiness
from housemind.brain.skills import load_skill_params
from housemind.brain.tidy import find as tidy_find
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, Source, State
from housemind.demo.houses import load_demo_house
from housemind.ha.adapter import HALightRhythm
from housemind.ha.translate import parse_light_rhythm
from housemind.ha.websocket import WebSocketHomeAssistant
from housemind.memory.database import Database
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
AMSTERDAM = (52.37, 4.90)
ZONE = ZoneInfo("Europe/Amsterdam")
NOVEMBER = date(2026, 11, 7)  # sunrise about 7:40, noon about 12:25, sunset about 17:00
AFTERNOON = datetime(2026, 11, 7, 13, 0, tzinfo=ZONE)  # bright and quite white
EVENING = datetime(2026, 11, 7, 20, 0, tzinfo=ZONE)  # soft and warm
LAMP = "light.living_ceiling"
GROUP = "light.living_room"  # the group that steers it: what HouseMind addresses
SENSOR = "binary_sensor.living_presence"
LUX = "sensor.living_light_level"
SWITCH = "switch.adaptive_lighting_living"
RHYTHM = HALightRhythm(
    "al-living",
    "Woonkamer - Adaptive Lighting",
    (LAMP,),
    min_brightness=5,
    max_brightness=50,
    min_kelvin=2500,
    max_kelvin=3500,
    interval_s=300,
    transition_s=60,
    brightness_mode="linear",
)


async def _house(
    tmp_path: Path, start: datetime = EVENING, on: bool = True, rhythm: HALightRhythm = RHYTHM
) -> tuple[HouseMind, SimulatedClock, Any]:
    """The extended demo house, with one group of that integration for its living room lamp, and
    someone who may be home."""
    clock = SimulatedClock(start.astimezone(UTC))
    house = load_demo_house("extended", clock, "nl")
    ha = house.ha
    ha.config = replace(ha.config, latitude=AMSTERDAM[0], longitude=AMSTERDAM[1])
    for eid, word in ((SWITCH, "Woonkamer"), (f"{SWITCH}_sleep_mode", "Slaapstand")):
        await ha.add_entity(
            Entity(
                eid,
                f"r-{eid}",
                f"Adaptive Lighting: {word}",
                platform="adaptive_lighting",
                config_entry_id=rhythm.entry_id,
            ),
            State(
                eid, "on" if eid == SWITCH and on else "off", {"manual_control": []}, clock.now()
            ),
        )
    ha.light_rhythm_list = [rhythm]
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    return hm, clock, ha


async def _autonomous(hm: HouseMind) -> None:
    await hm.set_admin_step("light_on_entry@living", "autonomous", "demo-user-1")
    await hm.tick()


def _lamp(ha: Any, entity_id: str = LAMP) -> tuple[str, float | None, Any]:
    state = ha.states[entity_id]
    raw = state.attributes.get("brightness")
    return (
        state.state,
        round(raw * 100 / 255) if isinstance(raw, int | float) else None,
        state.attributes.get("color_temp_kelvin"),
    )


async def _rows(hm: HouseMind, entity_id: str = "%") -> int:
    found = await hm.db.fetchone(
        "SELECT COUNT(*) AS n FROM decisions WHERE entity_id LIKE ?", (entity_id,)
    )
    return int(found["n"])


# -- the curve (E-466) -----------------------------------------------------------------------


def test_the_day_is_the_suns_day() -> None:
    """Sunrise, noon and sunset as an almanac gives them for this place and day, to a few
    minutes."""
    day = sun_day(RHYTHM, *AMSTERDAM, NOVEMBER, ZONE)
    for found, hour, minute in ((day.sunrise, 7, 43), (day.noon, 12, 24), (day.sunset, 17, 3)):
        expected = datetime(2026, 11, 7, hour, minute, tzinfo=ZONE)
        assert abs(found - expected) < timedelta(minutes=5), (found, expected)


def test_bright_by_day_soft_by_night_white_at_noon_warm_at_night() -> None:
    """The way the settings say it: at the most from an hour after sunrise to an hour before
    sunset, at the least from a quarter past sunset to a quarter before sunrise, in a straight
    line between; the colour warmest all night, whitest at noon."""
    day = sun_day(RHYTHM, *AMSTERDAM, NOVEMBER, ZONE)

    def pct(at: datetime) -> float:
        return brightness(RHYTHM, *AMSTERDAM, at, ZONE)

    def warm(at: datetime) -> int:
        return kelvin(RHYTHM, sun_position(RHYTHM, *AMSTERDAM, at, ZONE))

    hour, quarter = timedelta(hours=1), timedelta(minutes=15)
    assert pct(day.noon) == 50
    assert pct(day.sunset - hour) == 50
    assert pct(day.sunset + quarter) == 5
    assert pct(EVENING) == 5
    assert abs(pct(day.sunset - hour + (hour + quarter) / 2) - 27.5) < 0.1  # halfway down
    assert pct(day.sunrise - quarter) == 5
    assert pct(day.sunrise + hour) == 50
    assert warm(EVENING) == warm(day.sunset) == warm(day.sunrise) == 2500
    assert warm(day.noon) == 3500
    assert 2500 < warm(day.sunrise + hour) < warm(day.noon - hour) < 3500
    assert warm(day.noon + hour) > warm(day.sunset - hour) > 2500


def test_the_settings_as_home_assistant_gives_them() -> None:
    """The fields of a group's form, with "None" for a time that is not set."""
    read = parse_light_rhythm(
        "e1",
        "Keuken - Adaptive Lighting",
        {
            "lights": ["light.keuken"],
            "interval": 300,
            "transition": 60,
            "min_brightness": 5,
            "max_brightness": 50,
            "min_color_temp": 2500,
            "max_color_temp": 3500,
            "sleep_brightness": 1,
            "sleep_color_temp": 1000,
            "initial_transition": 1,
            "sunrise_time": "None",
            "sunset_time": "None",
            "sunrise_offset": 0,
            "brightness_mode": "linear",
            "brightness_mode_time_dark": 900,
            "brightness_mode_time_light": 3600,
            "only_once": False,
        },
    )
    assert read.lights == ("light.keuken",)
    assert (read.min_brightness, read.max_brightness) == (5, 50)
    assert (read.min_kelvin, read.max_kelvin) == (2500, 3500)
    assert (read.interval_s, read.transition_s, read.initial_transition_s) == (300, 60, 1)
    assert read.sunrise_time is None and read.sunset_time is None
    assert read.brightness_mode == "linear"


async def test_the_form_of_a_group_is_read_and_closed_again() -> None:
    """Home Assistant shows a group's settings only in the form of its options: HouseMind opens
    it, reads the values, and closes it at once – nothing stays open, nothing changes."""
    seen: list[str] = []

    async def opened(request: web.Request) -> web.Response:
        assert (await request.json()) == {"handler": "e1"}
        seen.append("open")
        return web.json_response(
            {
                "type": "form",
                "flow_id": "f1",
                "data_schema": [
                    {"name": "lights", "default": ["light.keuken"]},
                    {"name": "min_brightness", "default": 5},
                    {"name": "max_color_temp", "description": {"suggested_value": 3500}},
                    {
                        "type": "expandable",
                        "name": "advanced",
                        "required": True,
                        "schema": [
                            {"name": "initial_transition", "default": 1},
                            {"name": "brightness_mode", "default": "linear"},
                            {"name": "brightness_mode_time_dark", "default": 900},
                        ],
                    },
                ],
            }
        )

    async def closed(_: web.Request) -> web.Response:
        seen.append("close")
        return web.json_response({"message": "Flow aborted"})

    app = web.Application()
    app.router.add_post("/api/config/config_entries/options/flow", opened)
    app.router.add_delete("/api/config/config_entries/options/flow/f1", closed)
    async with TestServer(app) as server, aiohttp.ClientSession() as session:
        url = str(server.make_url("/api/websocket"))
        ha = WebSocketHomeAssistant(SimulatedClock(EVENING), "token", url, session)
        options = await ha._options_of("e1")
    assert seen == ["open", "close"]
    assert options == {
        "lights": ["light.keuken"],
        "min_brightness": 5,
        "max_color_temp": 3500,
        "initial_transition": 1,
        "brightness_mode": "linear",  # in the folded part of the form, as in the real house
        "brightness_mode_time_dark": 900,
    }


def test_a_fixed_sunset_and_an_offset_move_its_day() -> None:
    """A group whose settings fix the evening: sunset where they say, with its offset."""
    fixed = replace(RHYTHM, sunset_time="21:00:00", sunset_offset_s=-1800)
    day = sun_day(fixed, *AMSTERDAM, NOVEMBER, ZONE)
    assert day.sunset == datetime(2026, 11, 7, 20, 30, tzinfo=ZONE)
    assert brightness(fixed, *AMSTERDAM, datetime(2026, 11, 7, 19, 0, tzinfo=ZONE), ZONE) > 5


# -- while the integration does it (E-468) ---------------------------------------------------


async def test_below_autonomous_it_only_says_on_and_keeps_its_hands_off(tmp_path: Path) -> None:
    """On Propose the integration stays on, and what HouseMind wants to do has no colour of its
    own: otherwise the integration takes the lamp for someone's choice."""
    hm, _clock, ha = await _house(tmp_path)
    await hm.set_admin_step("light_on_entry@living", "propose", "demo-user-1")
    await hm.tick()
    assert ha.states[SWITCH].state == "on"
    await ha.simulate(LUX, "2")
    await ha.simulate(SENSOR, "on")
    assert hm.learning is not None
    asked = [
        p for p in hm.learning.pending if p.skill == "light_on_entry" and p.area_id == "living"
    ]
    assert asked
    group = [p for p in asked if p.entity_id == GROUP]
    assert group and all(p.data == {} for p in group)
    await hm.stop()


async def test_a_group_someone_put_off_is_left_alone(tmp_path: Path) -> None:
    """Off in Home Assistant by someone else: not HouseMind's. It does not touch the switch, and
    does not work out that group's day for the lamp."""
    hm, _clock, ha = await _house(tmp_path, on=False)
    await _autonomous(hm)
    assert ha.states[SWITCH].state == "off"
    assert not hm.daylight.ours(RHYTHM)
    await ha.simulate(LUX, "2")
    await ha.simulate(SENSOR, "on")
    state, pct, _ = _lamp(ha)
    assert state == "on"
    assert pct != 5  # what it learned or knows for the evening, not the group's
    await hm.stop()


# -- taking over (E-468) -----------------------------------------------------------------------


async def test_on_autonomous_housemind_takes_over_without_asking(tmp_path: Path) -> None:
    """The choice for Autonomous is the yes: the switch goes off, no question, and it is in the
    Logboek once."""
    hm, _clock, ha = await _house(tmp_path)
    before = await _rows(hm)
    await _autonomous(hm)
    assert ha.states[SWITCH].state == "off"
    assert hm.daylight.ours(RHYTHM)
    assert await _rows(hm) == before + 1
    await hm.stop()


async def test_the_lamp_goes_on_as_the_group_says_at_once(tmp_path: Path) -> None:
    """Coming into a dark room in the evening: soft and warm as the group says, not what the
    start knowledge or an average would say – and in a transition as short as it says."""
    hm, _clock, ha = await _house(tmp_path)
    await _autonomous(hm)
    await ha.simulate(LUX, "2")
    await ha.simulate(SENSOR, "on")
    assert _lamp(ha) == ("on", 5, 2500)
    await hm.stop()


async def test_in_the_afternoon_it_is_brighter_and_whiter(tmp_path: Path) -> None:
    """The same lamp at one o'clock: as bright as the group goes, and whiter."""
    hm, _clock, ha = await _house(tmp_path, start=AFTERNOON)
    await _autonomous(hm)
    await ha.simulate(LUX, "2")
    await ha.simulate(SENSOR, "on")
    state, pct, warm = _lamp(ha)
    assert state == "on" and pct == 50
    assert 3400 < warm <= 3500
    await hm.stop()


async def test_a_lamp_that_burns_follows_the_evening_quietly(tmp_path: Path) -> None:
    """A lamp on through the afternoon into the evening gets softer and warmer by itself, and
    the Logboek does not fill up with it."""
    hm, clock, ha = await _house(tmp_path, start=datetime(2026, 11, 7, 15, 30, tzinfo=ZONE))
    await _autonomous(hm)
    await ha.simulate(LUX, "2")
    await ha.simulate(SENSOR, "on")
    state, first_pct, first_warm = _lamp(ha)
    assert state == "on" and first_pct == 50
    rows = await _rows(hm, GROUP)
    seen = []
    for _ in range(int(5 * 3600 / 60)):
        await clock.advance(60)
        await hm.tick()
        seen.append(_lamp(ha, GROUP)[1:])
    state, pct, warm = _lamp(ha, GROUP)
    assert state == "on" and pct == 5 and warm == 2500
    assert first_warm > 2500
    assert sorted({s[0] for s in seen}, reverse=True)[1:3]  # it stepped down, not at once
    assert await _rows(hm, GROUP) == rows  # not a line per round
    await hm.stop()


# -- the hand goes first (E-467) ---------------------------------------------------------------


async def _lit(tmp_path: Path, start: datetime = EVENING) -> tuple[HouseMind, SimulatedClock, Any]:
    hm, clock, ha = await _house(tmp_path, start=start)
    await _autonomous(hm)
    await ha.simulate(LUX, "2")
    await ha.simulate(SENSOR, "on")
    assert _lamp(ha)[0] == "on"
    await clock.advance(40)  # what its lamps report of HouseMind's own "on" has passed
    return hm, clock, ha


async def _rounds(hm: HouseMind, clock: SimulatedClock, minutes: int = 12) -> None:
    for _ in range(minutes):
        await clock.advance(60)
        await hm.tick()


async def test_a_choice_by_hand_stays_half_an_hour(tmp_path: Path) -> None:
    """Set brighter in the app: the rounds leave it for half an hour (E-584); after that the
    lamp is on the day's rhythm again."""
    hm, clock, ha = await _lit(tmp_path, start=datetime(2026, 11, 7, 16, 0, tzinfo=ZONE))
    await ha.simulate(LAMP, "on", {"brightness": 200, "color_temp_kelvin": 4000}, by=Source.PERSON)
    await _rounds(hm, clock, 29)
    assert _lamp(ha)[1:] == (78, 4000)
    assert GROUP not in hm.daylight.following
    await _rounds(hm, clock, 3)
    assert GROUP in hm.daylight.following
    assert _lamp(ha, GROUP)[1:] != (78, 4000)
    await hm.stop()


async def test_a_dimmer_on_the_wall_and_a_scene_are_choices_too(tmp_path: Path) -> None:
    """Also what does not come through the app: the lamp is no longer where HouseMind left it."""
    hm, clock, ha = await _lit(tmp_path)
    await ha.simulate(LAMP, "on", {"brightness": 128}, by=Source.PHYSICAL)
    await _rounds(hm, clock)
    assert _lamp(ha)[1] == 50
    assert GROUP not in hm.daylight.following
    await hm.stop()
    hm, clock, ha = await _lit(tmp_path / "scene")
    await ha.simulate(LAMP, "on", {"brightness": 128}, by=Source.AUTOMATION)
    await _rounds(hm, clock)
    assert _lamp(ha)[1] == 50
    assert GROUP not in hm.daylight.following
    await hm.stop()


async def test_a_plain_button_is_no_choice(tmp_path: Path) -> None:
    """A lamp that goes on with a plain "on" – a button, an automation – comes back where it last
    was, say at the afternoon's colour. Nobody chose that: it gets the day's at once."""
    hm, clock, ha = await _house(tmp_path, start=AFTERNOON)
    await _autonomous(hm)
    await ha.simulate(LAMP, "on", by=Source.AUTOMATION)
    await _settled(hm, clock)
    afternoon = _lamp(ha)
    assert afternoon[1] == 50 and afternoon[2] > 3400
    await ha.simulate(LAMP, "off", by=Source.PERSON)
    await clock.advance((EVENING - AFTERNOON).total_seconds())
    await ha.simulate(LAMP, "on", by=Source.AUTOMATION)
    assert _lamp(ha)[1:] == afternoon[1:]  # as lamps do
    await _settled(hm, clock)
    assert _lamp(ha)[1:] == (5, 2500)
    await hm.stop()


async def test_an_on_with_its_own_colour_is_a_choice(tmp_path: Path) -> None:
    """A scene that puts the lamp on at its own values: those stay."""
    hm, clock, ha = await _house(tmp_path)
    await _autonomous(hm)
    await ha.simulate(LAMP, "on", by=Source.AUTOMATION)
    await _settled(hm, clock)
    await ha.simulate(LAMP, "off", by=Source.PERSON)
    await ha.simulate(
        LAMP, "on", {"brightness": 200, "color_temp_kelvin": 3000}, by=Source.AUTOMATION
    )
    await _settled(hm, clock)
    await _rounds(hm, clock)
    assert _lamp(ha)[1:] == (78, 3000)
    await hm.stop()


async def _settled(hm: HouseMind, clock: SimulatedClock) -> None:
    """The plain "on" a moment later: the task that looks at it has started, and has looked."""
    await asyncio.sleep(0)
    task = hm._daylight_task
    await clock.advance(2)
    if task is not None:
        await asyncio.wait_for(task, 5)


async def test_choosing_often_moves_the_rhythm_there(tmp_path: Path) -> None:
    """Someone keeps putting the lamp at about 25% in the evening: after three times HouseMind
    starts there; one single choice changes nothing."""
    hm, clock, ha = await _lit(tmp_path)
    assert hm.learning is not None
    part = hm.world.snapshot(clock.now()).part or "evening"  # type: ignore[union-attr]
    for count in range(3):
        await ha.simulate(LAMP, "on", {"brightness": 64}, by=Source.PERSON)
        if count == 0:
            assert hm.learning.day_shift("living", part, "brightness_pct") is None
        await ha.simulate(LAMP, "off", by=Source.PERSON)
        await ha.simulate(SENSOR, "off")
        await clock.advance(600)
        await hm.tick()
        await ha.simulate(LAMP, "on", by=Source.PHYSICAL)
        await _settled(hm, clock)
    assert abs((hm.learning.day_shift("living", part, "brightness_pct") or 0) - 20) < 1
    await hm.stop()


# -- giving back (E-468) -----------------------------------------------------------------------


async def test_back_from_autonomous_it_goes_on_again_and_says_so(tmp_path: Path) -> None:
    """Back to Propose: the integration does it again at once, and the administrators hear it."""
    hm, _clock, ha = await _lit(tmp_path)
    assert ha.states[SWITCH].state == "off"
    await hm.set_admin_step("light_on_entry@living", "propose", "demo-user-1")
    await hm.tick()
    assert ha.states[SWITCH].state == "on"
    assert not hm.daylight.ours(RHYTHM)
    assert GROUP not in hm.daylight.following
    assert any(str(n["topic"]).startswith("given_back:") for n in await hm.notifications())
    await hm.stop()


async def test_someone_putting_it_back_on_keeps_it_on(tmp_path: Path) -> None:
    """Put on by someone in Home Assistant while HouseMind does it: that stays, HouseMind asks
    nothing and does not switch it off again."""
    hm, clock, ha = await _lit(tmp_path)
    await ha.simulate(SWITCH, "on", by=Source.PERSON)
    await _rounds(hm, clock, 3)
    assert ha.states[SWITCH].state == "on"
    assert not hm.daylight.ours(RHYTHM)
    await hm.stop()


async def test_removing_housemind_gives_it_back(tmp_path: Path) -> None:
    """Give back, as before removing or factory settings: it goes back on, and stays."""
    hm, clock, ha = await _lit(tmp_path)
    await hm.give_back(True, "demo-user-1", unlink=False)
    assert ha.states[SWITCH].state == "on"
    await _rounds(hm, clock, 2)
    assert ha.states[SWITCH].state == "on"
    await hm.stop()


async def test_after_a_restart_it_still_knows(tmp_path: Path) -> None:
    """What it took over stays taken over after an update, and a lamp that burns goes on with it."""
    hm, clock, ha = await _lit(tmp_path)
    await hm.stop()
    again = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert again.daylight.ours(RHYTHM)
    assert GROUP in again.daylight.following
    await again.stop()


# -- a house without that integration (E-471) --------------------------------------------------


async def _without(tmp_path: Path, start: datetime) -> tuple[HouseMind, SimulatedClock, Any]:
    """The extended demo house, in a Home Assistant that does not have that integration."""
    clock = SimulatedClock(start.astimezone(UTC))
    house = load_demo_house("extended", clock, "nl")
    ha = house.ha
    ha.config = replace(ha.config, latitude=AMSTERDAM[0], longitude=AMSTERDAM[1])
    ha.light_rhythm_list = []
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    await hm.set_admin_step("light_on_entry@living", "autonomous", "demo-user-1")
    await hm.tick()
    return hm, clock, ha


async def _come_in(ha: Any) -> None:
    await ha.simulate(LUX, "2")
    await ha.simulate(SENSOR, "on")


async def test_without_it_a_lamp_goes_on_as_the_day_is(tmp_path: Path) -> None:
    """No such integration, a lamp that can: bright and white in the afternoon, softer and warm
    in the evening, between the start knowledge for the day and the evening."""
    hm, _clock, ha = await _without(tmp_path, AFTERNOON)
    await _come_in(ha)
    state, pct, warm = _lamp(ha, GROUP)
    assert state == "on" and pct == 60  # Helder by day, Normaal in the evening (E-494)
    assert 3800 < warm <= 4000
    await hm.stop()
    hm, _clock, ha = await _without(tmp_path / "evening", EVENING)
    await _come_in(ha)
    assert _lamp(ha, GROUP) == ("on", 35, 2700)
    await hm.stop()


async def test_without_it_a_lamp_that_burns_follows_the_evening(tmp_path: Path) -> None:
    """The same as with the integration: softer and warmer by itself, without lines in the
    Logboek."""
    hm, clock, ha = await _without(tmp_path, datetime(2026, 11, 7, 15, 30, tzinfo=ZONE))
    await _come_in(ha)
    assert _lamp(ha, GROUP)[1] == 60
    rows = await _rows(hm, GROUP)
    await _rounds(hm, clock, 5 * 60)
    assert _lamp(ha, GROUP) == ("on", 35, 2700)
    assert await _rows(hm, GROUP) == rows
    await hm.stop()


async def test_without_it_a_house_that_keeps_one_colour_keeps_it(tmp_path: Path) -> None:
    """What the house does comes first: learned 3000 K evening and day, it stays 3000 K."""
    hm, _clock, ha = await _without(tmp_path, AFTERNOON)
    assert hm.learning is not None
    now = hm.clock.now()
    for slot in ("free|13:00", "free|20:00"):
        hm.learning.values[("light_on_entry", "living", slot, "color_temp_kelvin")] = (
            3000.0,
            5.0,
            now,
        )
    hm._daylight_own_kept.clear()
    await _come_in(ha)
    assert _lamp(ha, GROUP)[2] == 3000
    await hm.stop()


async def test_without_it_the_night_keeps_what_it_learned(tmp_path: Path) -> None:
    """At night the lamp is what it knows for the night, not the evening's end of the line; so
    says "on" at night: nothing of the line, the part of the day's own value."""
    hm, clock, _ha = await _without(tmp_path, EVENING)
    may = {"brightness_pct", "color_temp_kelvin", "transition"}
    assert hm.daylight.on_data(GROUP, may, clock.now(), part="evening")
    assert hm.daylight.on_data(GROUP, may, clock.now(), part="night") is None
    await hm.stop()


async def test_when_the_settings_cannot_be_read_it_makes_no_rhythm_of_its_own(
    tmp_path: Path,
) -> None:
    """A bad moment says nothing about the integration: it may well be there."""
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("extended", clock, "nl")

    async def broken() -> list[HALightRhythm]:
        raise ConnectionError("no")

    house.ha.light_rhythms = broken  # type: ignore[method-assign]
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    assert not hm.daylight.known
    assert hm.daylight.group_of(LAMP) is None
    await hm.stop()


async def test_with_it_a_lamp_outside_its_groups_stays_as_it_was(tmp_path: Path) -> None:
    """The owner left this lamp out of the groups: no rhythm of HouseMind's own for it."""
    hm, _clock, _ha = await _house(tmp_path)
    assert hm.daylight.group_of("light.kitchen") is None
    await hm.stop()


async def test_without_it_expert_sees_that_it_follows_the_day(tmp_path: Path) -> None:
    """The card keeps its levels, and on Expert says the line it follows."""
    hm, clock, _ha = await _without(tmp_path, EVENING)
    assert hm.learning is not None
    hm.learning.first_seen[("light_on_entry", "living")] = clock.now()
    parts = await readiness(hm, "nl", 4, True)
    rows = [s for p in parts for s in p.get("skills", [])]
    row = next(r for r in rows if r["skill"] == "light_on_entry" and r["area"] == "living")
    assert "loopt mee met de dag: van Normaal tot Helder, 2700–4000 K" in row["whenDetail"]  # E-494
    await hm.stop()


# -- the lamps without a rhythm (E-469) ------------------------------------------------------


async def test_an_evening_is_no_average_of_the_white_hours(tmp_path: Path) -> None:
    """Found in the real house: a half hour it knew little of got the whole day's average. Now
    this part of the day comes before the whole day, also from what the history taught."""
    db = Database(tmp_path / "t.db")
    await db.open()
    now = datetime(2026, 11, 6, 19, 30, tzinfo=UTC)
    learning = Learning(db, now)
    learning.values.update(
        {
            ("light_on_entry", "music", "*", "brightness_pct"): (100.0, 9.0, now),
            ("light_on_entry", "music", "*", "color_temp_kelvin"): (3774.0, 9.0, now),
            ("light_on_entry", "music", "workday|20:00", "brightness_pct"): (65.0, 3.0, now),
            ("light_on_entry", "music", "workday|20:00", "color_temp_kelvin"): (2700.0, 3.0, now),
        }
    )
    for key, value in (("brightness_pct", 65.0), ("color_temp_kelvin", 2700.0)):
        got = learning.light_value("light_on_entry", "music", "workday|19:00", "evening", key)
        assert got == value
    assert (
        learning.light_value("light_on_entry", "music", "workday|10:00", "day", "brightness_pct")
        == 100
    )
    assert (
        learning.light_value(
            "light_on_entry", "music", "workday|20:00", "evening", "brightness_pct"
        )
        == 65
    )
    await db.close()


# -- on the screen (E-470) ---------------------------------------------------------------------


async def test_the_card_says_what_really_happens(tmp_path: Path) -> None:
    """Klaar voor autonomie?: the day's rhythm in the sentence, and on Expert the group, its limits
    and who does it now – not a learned average that is not what happens."""
    hm, clock, _ha = await _house(tmp_path)
    assert hm.learning is not None
    hm.learning.first_seen[("light_on_entry", "living")] = clock.now()

    def row(parts: list[dict[str, Any]]) -> dict[str, Any]:
        rows = [s for p in parts for s in p.get("skills", [])]
        return next(r for r in rows if r["skill"] == "light_on_entry" and r["area"] == "living")

    card = row(await readiness(hm, "nl", 4, True))
    assert "dagritme" in card["when"]
    assert "tot 50%" in card["when"] and "5%" in card["when"]
    detail = card["whenDetail"]
    assert "‘Woonkamer - Adaptive Lighting’" in detail and "5–50%" in detail
    assert "2500–3500 K" in detail and "doet die groep zelf" in detail
    await _autonomous(hm)
    assert "doe ik nu zelf" in row(await readiness(hm, "nl", 4, True))["whenDetail"]
    await hm.stop()


async def test_check_up_says_a_group_names_a_lamp_that_is_not_there(tmp_path: Path) -> None:
    """Found in the real house: two groups named lamps that no longer exist, so they did nothing.
    Check-up says so, like an automation that points to something that is gone."""
    gone = replace(RHYTHM, title="Eettafel - Adaptive Lighting", lights=("light.gone_long_ago",))
    hm, clock, ha = await _house(tmp_path, rhythm=gone)
    assert hm.daylight.group_of(LAMP) is None
    assert hm.daylight.missing == {gone.entry_id: ("light.gone_long_ago",)}
    configured = hm._tidy_configured(hm.world.states)  # type: ignore[union-attr]
    named = [c for c in configured if c.kind == "day_rhythm"]
    assert [c.name for c in named] == ["Eettafel - Adaptive Lighting"]
    found = tidy_find(hm.model, ha.states, [], named, set(), clock.now(), {}, set())
    assert "missing_ref" in [f.kind for f in found.findings]
    await hm.stop()


def test_the_catalogue_allows_a_transition_as_soft_as_the_groups_set() -> None:
    """A group with a minute's transition: the gate may not refuse it."""
    params = load_skill_params([ROOT / "knowledge" / "skills"])
    assert params["light_on_entry"]["color_temp_kelvin"]  # the start knowledge stays
    from housemind.knowledge.loader import load_catalog

    catalog, _ = load_catalog([ROOT / "knowledge" / "catalog"])
    allowed = catalog.kinds["light.color_temperature"].allowed("light.turn_on")
    assert allowed is not None
    assert allowed.values["transition"].contains(60)  # type: ignore[union-attr, arg-type]
