"""Light at once, and out as this house does it (E-443 – E-445). The administrator's wishes:
"Als je een ruimte binnenkomt, dan wil je juist dat het licht binnen 2 milliseconden aangaat,
ipv 2 minuten" – and for light off: "ruimtes waar men continue doorlopen dat je het licht pas na
een tijdje uitzet, maar ruimtes zoals WC's, badkamers, kelders, kastruimtes juist wel sneller
uit … belangrijk is dat je nog steeds leert van het huis, en dat als uitgangspunt ook neemt."
Each test fails without the change; what it checks is its docstring."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.brain.automations import read_automation
from housemind.brain.readiness import readiness
from housemind.brain.skills import NoPreferences, light_off_hold, load_skill_params
from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Entity, HouseModel, Source, State, StateChange
from housemind.demo.houses import load_demo_house
from housemind.ha.adapter import HAAutomation, HAStep
from housemind.ha.translate import parse_trigger
from housemind.i18n.catalog import load_languages
from housemind.knowledge.area_roles import area_role, role_words
from housemind.knowledge.loader import load_catalog
from housemind.runtime import HouseMind, Paths
from housemind.world.occupancy import OccupancyModel
from tests.test_real_house_counts import _days, _local, _open_kitchen, _pending
from tests.test_real_house_counts import _house as _counted_house

ROOT = Path(__file__).resolve().parents[1]
AFTERNOON = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
PRESENCE = "binary_sensor.living_presence"
LUX = "sensor.living_light_level"
LAMP = "light.living_ceiling"
PARAMS = load_skill_params([ROOT / "knowledge" / "skills"])["light_off_leave"]


async def _living(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, Any]:
    """The extended demo house: a living room with a presence sensor, a light sensor and
    lamps."""
    clock = SimulatedClock(AFTERNOON)
    house = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    return hm, clock, house.ha


async def _let_run() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


def _row(parts: list[dict[str, Any]], skill: str, area: str) -> dict[str, Any]:
    rows = [s for p in parts for s in p.get("skills", [])]
    return next(r for r in rows if r["skill"] == skill and r["area"] == area)


# -- light on, at once (E-443) ---------------------------------------------------------------


async def test_the_card_says_at_once_not_within_two_minutes(tmp_path: Path) -> None:
    """'aan binnen 2 minuten na binnenkomen' read as a wait of two minutes. HouseMind acts at
    once; the two minutes are only how long after coming in it still does."""
    hm, clock, _ha = await _living(tmp_path)
    assert hm.learning is not None
    hm.learning.first_seen[("light_on_entry", "living")] = clock.now()
    detail = _row(await readiness(hm, "nl", 4, True), "light_on_entry", "living")["whenDetail"]
    assert "meteen bij binnenkomen (tot 2 minuten erna)" in detail
    assert "aan binnen" not in detail
    await hm.stop()


async def test_staying_in_a_walk_through_room_lights_it_after_ten_seconds(
    tmp_path: Path,
) -> None:
    """A room where people mostly walk through waited 30 seconds, and then for the next tick:
    up to a minute in the dark. Now it is 10 seconds, and HouseMind looks again exactly then –
    no tick in this test."""
    zone, lamp = "binary_sensor.k_dining", "light.dining"

    def prepare(ha: Any) -> None:  # dinner with the table lamp; otherwise walking through
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

    start = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)  # 20:00 in Amsterdam, dark
    hm, clock, ha = await _counted_house(tmp_path, start=start, located=True, prepare=prepare)
    assert hm.learning is not None and hm.learning.passing("dining", clock.now())
    await ha.simulate(zone, "on")
    await clock.advance(5)
    await _let_run()
    assert not _pending(hm, "light_on_entry", "dining")  # perhaps only walking through
    looking_again = hm._recheck_task
    assert looking_again is not None  # HouseMind will look again by itself
    await clock.advance(5)
    await asyncio.wait_for(looking_again, 5)
    assert _pending(hm, "light_on_entry", "dining")
    await hm.stop()


def _room(*sensors: tuple[str, str]) -> tuple[OccupancyModel, datetime]:
    model = HouseModel()
    model.areas["hall"] = Area("hall", "Hal")
    for entity_id, device_class in sensors:
        model.entities[entity_id] = Entity(
            entity_id, f"r-{entity_id}", entity_id, area_id="hall", device_class=device_class
        )
    return OccupancyModel(model), AFTERNOON


def _set(occ: OccupancyModel, entity_id: str, old: str | None, new: str, at: datetime) -> None:
    before = State(entity_id, old, {}, at) if old is not None else None
    occ.update(StateChange(entity_id, before, State(entity_id, new, {}, at), Source.PHYSICAL), at)


def test_new_motion_is_someone_coming_in_before_the_presence_sensor_sees_them() -> None:
    """In a room with a presence sensor, a quick motion sensor counted for little since the
    presence sensor became the judge: the light waited for the slower sensor. Motion that
    comes on after the presence sensor said nobody is someone coming in, at once; motion from
    before still counts for little, so the room still empties at once."""
    occ, t0 = _room(("binary_sensor.p", "occupancy"), ("binary_sensor.m", "motion"))
    _set(occ, "binary_sensor.p", None, "on", t0)
    _set(occ, "binary_sensor.m", None, "on", t0)
    _set(occ, "binary_sensor.p", "on", "off", t0 + timedelta(seconds=30))  # left
    assert not occ.evaluate(t0 + timedelta(seconds=31))["hall"].occupied  # old motion: no one
    _set(occ, "binary_sensor.m", "on", "off", t0 + timedelta(seconds=60))
    _set(occ, "binary_sensor.m", "off", "on", t0 + timedelta(seconds=90))  # coming in
    assert occ.evaluate(t0 + timedelta(seconds=90))["hall"].occupied


def test_someone_outside_the_zones_counts_after_one_second() -> None:
    """A sensor with zones waited three seconds for its zones before 'someone in range, in
    none of its zones' counted; now one."""
    from housemind.world import occupancy

    assert occupancy.ZONE_SETTLE_S == 1.0


async def test_getting_dark_while_someone_is_there_lights_at_once(tmp_path: Path) -> None:
    """A light sensor was looked at once a minute at most, so getting dark while someone sat
    there could take a minute. With someone there and no lamp on, the moment it is dark
    counts."""
    hm, clock, ha = await _living(tmp_path)
    await ha.simulate(LUX, "300")
    await ha.simulate(PRESENCE, "on")
    await _let_run()
    assert not _pending(hm, "light_on_entry", "living")  # light enough
    await clock.advance(20)
    await ha.simulate(LUX, "4")
    await _let_run()
    assert _pending(hm, "light_on_entry", "living")
    await hm.stop()


# -- light off, as this house does it (E-444, E-445) -----------------------------------------


def test_start_knowledge_short_where_you_are_briefly_longer_in_a_hall() -> None:
    """Before the house taught anything: toilet, bathroom and storage rooms soon, a hall where
    people walk back and forth later. Where only motion senses people, at least three
    minutes: someone sitting still is not seen."""
    nothing = NoPreferences()
    for role, minutes in (("toilet", 1), ("bathroom", 2), ("utility", 1), ("hall", 5)):
        assert light_off_hold(PARAMS, nothing, "r", role, False, True) == (minutes, False)
    assert light_off_hold(PARAMS, nothing, "r", "toilet", False, False) == (3, False)


def test_cellars_closets_and_storage_are_rooms_you_are_in_briefly() -> None:
    words = role_words(load_languages([ROOT / "knowledge" / "languages"]))
    for name in ("Kelder", "Inloopkast", "Berging", "Walk-in Closet", "Keller", "Dressing"):
        assert area_role(Area("x", name), words) == "utility", name


async def test_the_house_teaches_the_wait(tmp_path: Path) -> None:
    """The living room's automation puts the lamps out as soon as its sensor sees nobody. That
    is what this house does: after three times HouseMind waits one minute there, not the
    twenty minutes of its start knowledge – and the card says it learned it."""
    hm, clock, ha = await _living(tmp_path)
    for _ in range(3):
        await ha.simulate(PRESENCE, "on")
        await ha.simulate(LAMP, "on", by=Source.PHYSICAL)
        await clock.advance(30 * 60)  # an evening on the couch, sitting still
        await ha.simulate(PRESENCE, "off")
        await clock.advance(2)
        await ha.simulate(LAMP, "off", by=Source.AUTOMATION)
        await clock.advance(60 * 60)
    assert hm.learning is not None
    assert light_off_hold(PARAMS, hm.learning, "living", "living", False, True) == (1, True)
    leave = _row(await readiness(hm, "nl", 4, True), "light_off_leave", "living")
    assert "als er 1 minuut niemand meer is" in leave["when"]
    assert "wachttijd 1 min (geleerd)" in leave["whenDetail"]
    await hm.stop()


async def test_someone_still_there_makes_the_wait_longer(tmp_path: Path) -> None:
    """Coming back during the dimming, or switching the lamp on again just after HouseMind put
    it out, means the wait was too short there: it grows."""
    hm, clock, _ha = await _living(tmp_path)
    learning = hm.learning
    assert learning is not None
    for _ in range(3):
        learning.seed_hold("living", 1.0, clock.now())
    before, _ = light_off_hold(PARAMS, learning, "living", "living", False, True)
    learning.held_too_short("living", before, clock.now())
    after, _ = light_off_hold(PARAMS, learning, "living", "living", False, True)
    assert after > before
    await hm.stop()


def test_the_wait_starts_when_the_sensor_stops_seeing_someone() -> None:
    """After an hour sitting still in a room with a presence sensor, 'empty for' counted from
    the moment the sensor first saw them – an hour – so the lamps went out at once. It counts
    from when the sensor stopped seeing them."""
    occ, t0 = _room(("binary_sensor.p", "occupancy"))
    _set(occ, "binary_sensor.p", None, "on", t0)
    _set(occ, "binary_sensor.p", "on", "off", t0 + timedelta(hours=1))
    hall = occ.evaluate(t0 + timedelta(hours=1, seconds=5))["hall"]
    assert hall.last_activity == t0 + timedelta(hours=1)


def test_an_automation_that_waits_teaches_its_wait() -> None:
    """An automation that puts the lamps out four minutes after its sensor sees nobody: those
    four minutes are where HouseMind starts in that room."""
    model = load_demo_house("family_home", SimulatedClock(AFTERNOON), "nl").ha.model
    trigger = parse_trigger(
        {
            "trigger": "state",
            "entity_id": "binary_sensor.hall_motion",
            "to": "off",
            "for": "00:04:00",
        }
    )
    assert trigger is not None and trigger.for_s == 240
    automation = HAAutomation(
        "automation.hall_off",
        "Gang licht uit",
        True,
        triggers=(trigger,),
        steps=(HAStep("light.turn_off", ("light.hall",)),),
    )
    catalog, _ = load_catalog([ROOT / "knowledge" / "catalog"])
    params = load_skill_params([ROOT / "knowledge" / "skills"])
    reading = read_automation(automation, model, catalog, {}, params, {"hall": "hall"})
    assert reading.values[("light_off_leave", "hall")]["hold_minutes"] == 4.0
