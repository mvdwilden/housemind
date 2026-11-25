"""Counting what really happens (E-268, B-118), from the user's side – found in the real house:
light when coming in was 2% right in an open kitchen and dining room, the curtains closing in
the evening 0%, and "goed na" of the automations about 13%. The house did nothing odd;
HouseMind measured wrongly. Each test fails without the change; what it checks is its
docstring."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.brain.learning import Result
from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Device, Entity, Source, State
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.world.house_mode import HouseMode
from tests.test_automations import EVENING, ROOT
from tests.test_presence_at_start import LIVING, READING, _wait

AMSTERDAM = {"latitude": 52.37, "longitude": 4.90}
KITCHEN_MOTION = "binary_sensor.kitchen_motion"
KITCHEN_LAMP = "light.kitchen"
CURTAIN = "cover.living_curtain"


async def _house(
    tmp_path: Path,
    start: datetime = EVENING,
    located: bool = False,
    prepare: Any = None,
    automations: dict[str, dict[str, Any]] | None = None,
    home: bool = True,
) -> tuple[HouseMind, SimulatedClock, Any]:
    """The family house; ``located``: Home Assistant knows where the house is (as in a real
    installation); ``prepare`` adds things and history before HouseMind starts."""
    clock = SimulatedClock(start)
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    if located:
        ha.config = replace(ha.config, **AMSTERDAM)
    for entity_id, config in (automations or {}).items():
        await ha.add_automation(entity_id, str(config["alias"]), config)
    if prepare is not None:
        prepare(ha)
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    if hm._history_task is not None:
        await hm._history_task
    if home:
        assert hm.world is not None
        hm.world.modes.mode = HouseMode.HOME
    return hm, clock, ha


def _add(ha: Any, entity_id: str, name: str, area: str, state: str, **extra: Any) -> None:
    device = extra.pop("device", entity_id)
    if device not in ha.model.devices:
        ha.model.devices[device] = Device(device, name, area)
    ha.model.entities[entity_id] = Entity(
        entity_id,
        f"r-{entity_id}",
        name,
        device_id=device,
        area_id=extra.pop("entity_area", None),
        device_class=extra.get("device_class"),
        unit=extra.get("unit_of_measurement"),
    )
    ha.states[entity_id] = State(entity_id, state, {"friendly_name": name, **extra}, EVENING)
    first = _days()[0] + timedelta(hours=16)  # 18:00, before the first evening
    ha.record(entity_id, state, first, {"friendly_name": name, **extra})  # in the recorder too


def _count(
    hm: HouseMind, clock: SimulatedClock, skill: str, area: str
) -> tuple[float, float, float]:
    """Right, "not like that" (with undone) and situations – all of it, live and history."""
    learning = hm.learning
    assert learning is not None
    hits = learning.total(skill, area, Result.HIT, clock.now())
    misses = learning.total(skill, area, Result.MISS, clock.now()) + learning.total(
        skill, area, Result.CORRECTION, clock.now()
    )
    _score, situations, _days = learning.score(skill, area, clock.now())
    return round(hits, 2), round(misses, 2), round(situations, 2)


def _pending(hm: HouseMind, skill: str, area: str) -> list[Any]:
    assert hm.learning is not None
    return [p for p in hm.learning.pending if p.skill == skill and p.area_id == area]


async def _until(hm: HouseMind, clock: SimulatedClock, moment: datetime, step: int = 60) -> None:
    while clock.now() + timedelta(seconds=step) <= moment:
        await clock.advance(step)
        await hm.tick()
    rest = (moment - clock.now()).total_seconds()
    if rest > 0:
        await clock.advance(rest)


def _days(n: int = 8) -> list[datetime]:
    """Midnight (UTC) of the n days before EVENING."""
    base = EVENING.replace(hour=0, minute=0, second=0)
    return [base - timedelta(days=d) for d in range(n, 0, -1)]


def _local(day: datetime, hour: int, minute: int = 0) -> datetime:
    """A local time in Amsterdam in October (UTC+2) on that day, in UTC."""
    return day.replace(hour=hour - 2, minute=minute)


# -- the sun ------------------------------------------------------------------------------


async def test_the_history_knows_where_the_sun_was(tmp_path: Path) -> None:
    """Home Assistant keeps no height of the sun in its history (only its name). Reading the
    history, HouseMind went by the clock – dark from 18:00 in October – so walking into the
    kitchen at 18:05, with the sun still well up and no lamp needed, counted as "not like
    that". HouseMind now works out where the sun was from the place of the house."""

    def prepare(ha: Any) -> None:
        for day in _days():
            ha.record("sun.sun", "above_horizon", _local(day, 7), {"friendly_name": "Zon"})
            at = _local(day, 18, 5)
            ha.record(KITCHEN_MOTION, "on", at)
            ha.record(KITCHEN_MOTION, "off", at + timedelta(minutes=1))
            at = _local(day, 20)
            ha.record(KITCHEN_MOTION, "on", at)
            ha.record(KITCHEN_LAMP, "on", at + timedelta(seconds=20), by=Source.PERSON)
            ha.record(KITCHEN_MOTION, "off", at + timedelta(minutes=1))
            ha.record(KITCHEN_LAMP, "off", at + timedelta(minutes=12), by=Source.PERSON)

    hm, clock, _ha = await _house(tmp_path, located=True, prepare=prepare)
    hits, misses, _n = _count(hm, clock, "light_on_entry", "kitchen")
    assert hits > 0  # in the dark it did count
    assert misses == 0
    await hm.stop()


# -- light when coming in -------------------------------------------------------------------


async def test_a_sensor_that_drops_out_for_seconds_is_no_new_coming_in(tmp_path: Path) -> None:
    """A presence sensor that loses someone for twenty seconds and finds them again: that is
    still the same person in the room, not someone coming in again – one situation, not one
    each time the sensor blinks."""
    hm, clock, ha = await _house(tmp_path)
    await ha.simulate(LIVING, "on")
    for _ in range(2):
        await _wait(hm, clock, 200)
        await ha.simulate(LIVING, "off")
        await _wait(hm, clock, 20)
        await ha.simulate(LIVING, "on")
    await _wait(hm, clock, 10 * 60)
    _hits, misses, situations = _count(hm, clock, "light_on_entry", "living")
    assert (misses, situations) == (1.0, 1.0)
    await hm.stop()


async def test_switching_the_light_on_before_the_sensor_sees_you_counts_as_right(
    tmp_path: Path,
) -> None:
    """Coming into the dark and switching the lamp on before the sensor notices you is exactly
    what "light when coming in" would do. It counted nowhere – only the times without light
    counted, as "not like that". Now it is one good situation."""
    hm, clock, ha = await _house(tmp_path)
    await _wait(hm, clock, 10)
    await ha.simulate(READING, "on", by=Source.PERSON)
    await _wait(hm, clock, 5)
    await ha.simulate(LIVING, "on")
    await _wait(hm, clock, 5 * 60)
    assert _count(hm, clock, "light_on_entry", "living") == (1.0, 0, 1.0)
    await hm.stop()


async def test_housemind_learns_when_it_is_dark_enough_in_a_room(tmp_path: Path) -> None:
    """The light sensor in the kitchen hardly measures daylight (0–5 lux all day), so the
    kitchen was "dark" all day and every visit by day counted as "not like that". HouseMind
    now learns per room when people switch the light on: after the history, coming into the
    kitchen at midday is no reason for light – in the evening it still is."""
    lux = "sensor.kitchen_light_level"

    def prepare(ha: Any) -> None:
        _add(
            ha,
            lux,
            "Lichtsensor",
            "kitchen",
            "0",
            device_class="illuminance",
            unit_of_measurement="lx",
        )
        for day in _days():
            for hour, value in ((3, "1"), (11, "5"), (13, "2"), (17, "0")):
                ha.record(lux, value, _local(day, hour))
            for hour, minute in ((10, 0), (12, 30), (15, 0)):
                at = _local(day, hour, minute)
                ha.record(KITCHEN_MOTION, "on", at)
                ha.record(KITCHEN_MOTION, "off", at + timedelta(minutes=1))
            at = _local(day, 19, 30)
            ha.record(KITCHEN_MOTION, "on", at)
            ha.record(KITCHEN_LAMP, "on", at + timedelta(seconds=20), by=Source.PERSON)
            ha.record(KITCHEN_MOTION, "off", at + timedelta(minutes=1))
            ha.record(KITCHEN_LAMP, "off", at + timedelta(minutes=30), by=Source.PERSON)

    noon = datetime(2026, 10, 19, 10, 30, tzinfo=UTC)  # 12:30 in Amsterdam
    hm, clock, ha = await _house(tmp_path, start=noon, located=True, prepare=prepare)
    await ha.simulate(KITCHEN_MOTION, "on")
    await _wait(hm, clock, 20)
    assert not _pending(hm, "light_on_entry", "kitchen")  # midday: no light expected
    await ha.simulate(KITCHEN_MOTION, "off")
    await _until(hm, clock, datetime(2026, 10, 19, 17, 30, tzinfo=UTC))  # 19:30
    await ha.simulate(KITCHEN_MOTION, "on")
    await _wait(hm, clock, 20)
    assert _pending(hm, "light_on_entry", "kitchen")  # evening: light expected
    await hm.stop()


# -- one open space -----------------------------------------------------------------------


def _open_kitchen(ha: Any) -> None:
    """A presence sensor in the kitchen with a zone for the dining room, as in an open kitchen,
    and a lamp above the dining table."""
    ha.model.areas["dining"] = Area("dining", "Eetkamer", "ground")
    for eid, name in (
        ("binary_sensor.k_range", "Aanwezigheidssensor"),
        ("binary_sensor.k_kitchen", "Zone: Keuken"),
        ("binary_sensor.k_dining", "Zone: Eetkamer"),
    ):
        _add(ha, eid, name, "kitchen", "off", device="k", device_class="occupancy")
    ha.model.devices["k"] = Device("k", "Sensor", "kitchen")
    _add(ha, "light.dining", "Eettafel", "dining", "off", supported_color_modes=["brightness"])


async def test_a_lamp_in_the_open_kitchen_lights_the_dining_room(tmp_path: Path) -> None:
    """The zone "Eetkamer" of the kitchen sensor also sees people at the kitchen island; the
    kitchen lamp is light enough there and the dining table lamp stays off. That counted as
    "not like that". Rooms that share one presence sensor are rooms of their own (E-530); while
    the light of the one is on, the other left dark teaches whether that light does there."""
    hm, clock, ha = await _house(tmp_path, prepare=_open_kitchen)
    await ha.simulate(KITCHEN_LAMP, "on", by=Source.PERSON)
    await _wait(hm, clock, 5 * 60)
    await ha.simulate("binary_sensor.k_dining", "on")
    await _wait(hm, clock, 10)
    assert hm.learning is not None and hm.learning.open_entries  # what people do now teaches
    await _wait(hm, clock, 5 * 60)
    assert _count(hm, clock, "light_on_entry", "dining")[1] == 0
    await hm.stop()


async def test_lighting_the_open_kitchen_when_coming_into_the_dining_room_is_no_miss(
    tmp_path: Path,
) -> None:
    """Coming into the dark dining part and switching on the kitchen lamp: people chose their
    light for the open space – no "not like that" for the dining table lamp."""
    hm, clock, ha = await _house(tmp_path, prepare=_open_kitchen)
    await ha.simulate("binary_sensor.k_dining", "on")
    await _wait(hm, clock, 20)
    assert _pending(hm, "light_on_entry", "dining")
    await ha.simulate(KITCHEN_LAMP, "on", by=Source.PERSON)
    await _wait(hm, clock, 5 * 60)
    assert _count(hm, clock, "light_on_entry", "dining")[1] == 0
    await hm.stop()


async def test_the_dining_lamp_after_the_kitchen_lamp_still_counts_as_right(
    tmp_path: Path,
) -> None:
    """Coming in for dinner, someone switches on the kitchen island lamp and a few seconds
    later the dining table lamp: that is light when coming into the dining room – the kitchen
    lamp going on first made it count for nothing."""
    hm, clock, ha = await _house(tmp_path, prepare=_open_kitchen)
    await ha.simulate("binary_sensor.k_dining", "on")
    await _wait(hm, clock, 20)
    await ha.simulate(KITCHEN_LAMP, "on", by=Source.PERSON)
    await _wait(hm, clock, 10)
    await ha.simulate("light.dining", "on", by=Source.PERSON)
    await _wait(hm, clock, 5 * 60)
    assert _count(hm, clock, "light_on_entry", "dining")[:2] == (1.0, 0)
    await hm.stop()


async def test_walking_through_a_room_where_people_mostly_pass_is_no_coming_in(
    tmp_path: Path,
) -> None:
    """The dining part of an open kitchen mostly sees people walking through for a few
    seconds, there and back a minute later, without light; at dinner they stay and switch
    the table lamp on. What counts is the time someone is really there. Walking through
    counted as "not like that", and it taught that the evening there is no reason for light.
    Now such a room waits 10 seconds in one go (it was 30, E-443): a short visit counts for
    nothing, someone who stays gets light expected, and switching it on yourself within those
    seconds counts as right."""
    zone, lamp = "binary_sensor.k_dining", "light.dining"

    def prepare(ha: Any) -> None:
        _open_kitchen(ha)
        for day in _days():
            at = _local(day, 20)
            ha.record(zone, "on", at)
            ha.record(lamp, "on", at + timedelta(seconds=10), by=Source.PERSON)
            ha.record(zone, "off", at + timedelta(minutes=40))
            ha.record(lamp, "off", at + timedelta(minutes=41), by=Source.PERSON)
            for hour, minute in ((21, 0), (21, 30)):  # there and back, a minute apart
                at = _local(day, hour, minute)
                ha.record(zone, "on", at)
                ha.record(zone, "off", at + timedelta(seconds=5))
                ha.record(zone, "on", at + timedelta(seconds=80))
                ha.record(zone, "off", at + timedelta(seconds=86))

    start = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)  # 20:00 in Amsterdam
    hm, clock, ha = await _house(tmp_path, start=start, located=True, prepare=prepare)
    _hits, misses, _n = _count(hm, clock, "light_on_entry", "dining")
    for _ in range(2):  # walking through, there and back: five seconds each, as in the house
        await ha.simulate(zone, "on")
        await clock.advance(5)
        await hm.tick()
        await ha.simulate(zone, "off")
        await _wait(hm, clock, 70)
    await _wait(hm, clock, 3 * 60)
    assert not _pending(hm, "light_on_entry", "dining")
    assert _count(hm, clock, "light_on_entry", "dining")[1] == misses
    await ha.simulate(zone, "on")  # staying
    await _wait(hm, clock, 40)
    assert _pending(hm, "light_on_entry", "dining")
    await ha.simulate(zone, "off")
    await _wait(hm, clock, 5 * 60)
    hits = _count(hm, clock, "light_on_entry", "dining")[0]
    await ha.simulate(zone, "on")  # staying, and switching on within those seconds
    await _wait(hm, clock, 10)
    await ha.simulate(lamp, "on", by=Source.PERSON)
    await _wait(hm, clock, 60)
    assert _count(hm, clock, "light_on_entry", "dining")[0] == hits + 1
    await hm.stop()


# -- the curtains -----------------------------------------------------------------------------


async def test_curtains_closed_at_sunset_by_an_automation_count_as_right(tmp_path: Path) -> None:
    """An automation closes the living room curtains at sunset (18:39 today). HouseMind expected
    it at "dark" – the clock's 18:00, or the sun at 3° – and waited 20 minutes, so it was
    always "not like that". A curtain that is still closing, or a short "unavailable" in
    between, also counted as "not like that". Now: half an hour, and only the end counts."""
    start = datetime(2026, 10, 19, 15, 30, tzinfo=UTC)  # 17:30 in Amsterdam
    hm, clock, ha = await _house(tmp_path, start=start, located=True)
    await _until(hm, clock, datetime(2026, 10, 19, 16, 25, tzinfo=UTC))
    await ha.simulate(CURTAIN, "unavailable")
    await _wait(hm, clock, 10)
    await ha.simulate(CURTAIN, "open")
    await _until(hm, clock, datetime(2026, 10, 19, 16, 39, 30, tzinfo=UTC))
    await ha.simulate(CURTAIN, "closing", by=Source.AUTOMATION)
    await _wait(hm, clock, 15)
    await ha.simulate(CURTAIN, "closed", by=Source.AUTOMATION)
    await _wait(hm, clock, 40 * 60)
    hits, misses, _n = _count(hm, clock, "covers_close_dark", "living")
    assert (hits, misses) == (0.5, 0)  # an automation counts half
    await hm.stop()


async def test_housemind_learns_when_the_curtains_close_in_a_room(tmp_path: Path) -> None:
    """Eight evenings of an automation closing the curtains at a quarter to seven: HouseMind
    learns at which height of the sun they close in that room and counts every evening as
    right. The curtain itself reports that it is closed, without the automation (as in the
    real house): it is still the automation's doing, and it is copied."""
    close = "automation.close"
    config = {
        "id": "cl",
        "alias": "Gordijnen dicht",
        "triggers": [{"trigger": "sun", "event": "sunset"}],
        "actions": [{"action": "cover.close_cover", "target": {"entity_id": CURTAIN}}],
    }

    def prepare(ha: Any) -> None:
        for day in _days():
            at = _local(day, 8, 30)
            ha.record(CURTAIN, "opening", at, by=Source.AUTOMATION, cause="automation.open")
            ha.record(CURTAIN, "open", at + timedelta(seconds=15))
            at = _local(day, 18, 45)
            ha.record(CURTAIN, "closing", at, by=Source.AUTOMATION, cause=close)
            ha.record(CURTAIN, "closed", at + timedelta(seconds=15))

    hm, clock, _ha = await _house(
        tmp_path, located=True, prepare=prepare, automations={close: config}
    )
    hits, misses, _n = _count(hm, clock, "covers_close_dark", "living")
    assert hits > 0 and misses == 0
    rows = {r["id"]: r for r in (await views.house_settings(hm, "nl"))["automations"]}
    assert rows[close]["history"] == (
        "Deed de afgelopen 8 dagen 8 keer iets; 0 keer zette iemand het terug. "
        "Ik doe hem nu 100% goed na."
    )
    assert hm.learning is not None
    learned = hm.learning.value("covers_close_dark", "living", "*", "sun_elevation")
    assert learned is not None and -2.0 <= learned <= 3.0
    await hm.stop()


# -- "goed na" of an automation ---------------------------------------------------------------


async def test_copying_an_automation_counts_each_time_once(tmp_path: Path) -> None:
    """An automation that switches the kitchen lights (a group and its two lamps) on when
    someone comes in and off when they leave did one thing each time, not three; and switching
    off an empty room before HouseMind's own wait is what "light off when leaving" does. It
    showed "13% goed na"; it is all of it."""
    group, members = "light.kitchen_all", ("light.kitchen_a", "light.kitchen_b")
    presence = "binary_sensor.kitchen_presence"
    cause = "automation.kitchen_light"
    config = {
        "id": "kl",
        "alias": "Keuken licht",
        "triggers": [{"trigger": "state", "entity_id": presence}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": group}}],
    }

    def prepare(ha: Any) -> None:
        _add(ha, presence, "Aanwezigheid", "kitchen", "off", device_class="presence")
        for lamp in members:
            _add(ha, lamp, "Lamp", "kitchen", "off", supported_color_modes=["brightness"])
        _add(ha, group, "Keuken", "kitchen", "off", group_entities=list(members))
        for day in _days():
            for hour in (19, 21):
                at = _local(day, hour)
                ha.record(presence, "on", at)
                for i, lamp in enumerate((group, *members)):
                    ha.record(
                        lamp,
                        "on",
                        at + timedelta(seconds=1 + i / 5),
                        by=Source.AUTOMATION,
                        cause=cause,
                    )
                gone = at + timedelta(minutes=5)
                ha.record(presence, "off", gone)
                for i, lamp in enumerate((group, *members)):
                    ha.record(
                        lamp,
                        "off",
                        gone + timedelta(seconds=2 + i / 5),
                        by=Source.AUTOMATION,
                        cause=cause,
                    )

    hm, _clock, _ha = await _house(tmp_path, prepare=prepare, automations={cause: config})
    rows = {r["id"]: r for r in (await views.house_settings(hm, "nl"))["automations"]}
    assert rows[cause]["history"] == (
        "Deed de afgelopen 8 dagen 32 keer iets; 0 keer zette iemand het terug. "
        "Ik doe hem nu 100% goed na."
    )
    await hm.stop()


# -- once more from the start -----------------------------------------------------------------
