"""Standen voor lampen (E-490 – E-496, design 73): four standen per lamp, the brain in felt
percentages, each lamp its own brightness and colour, Proef on the lamp itself, and what the
screen and the words show."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.levels import (
    START,
    LightLevels,
    Standen,
    between,
    capability,
    from_json,
    stand_of,
)
from housemind.brain.proposals import Answer
from housemind.brain.when import when
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, HouseModel, State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import Decision, Layer, Outcome
from housemind.server import views
from housemind.world.calendar import DayType
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
WARM: dict[str, object] = {"kelvin": 2200}
RHYTHM: dict[str, object] = {"rhythm": True}
# A lamp that already looks full above about 21% (the administrator's own example).
STEEP = Standen(
    (4, 9, 16, 22), ({"kelvin": 2200}, {"rhythm": True}, {"rhythm": True}, {"kelvin": 4000})
)


def local(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=TZ)


# -- the felt scale (B-406) ---------------------------------------------------------------


def test_a_lamp_nobody_set_does_what_it_always_did() -> None:
    levels = LightLevels(HouseModel())
    for felt in (1, 3, 10, 15, 20, 35, 40, 60, 80, 100):
        assert levels.real("light.any", felt) == felt
        assert levels.felt("light.any", felt) == felt


def test_a_lamp_with_its_own_standen_goes_straight_between_them() -> None:
    levels = LightLevels(HouseModel(), {"light.table": STEEP})
    assert [levels.real("light.table", f) for f in (15, 35, 60, 100)] == [4, 9, 16, 22]
    assert levels.real("light.table", 25) in (6, 7)  # halfway Gedimd and Normaal
    assert levels.real("light.table", 3) == 1  # softer than Gedimd, never off
    assert levels.felt("light.table", 9) == 35.0
    assert levels.felt("light.table", 22) == 100.0
    assert 15 < levels.felt("light.table", 6) < 35


def test_standen_are_checked_before_they_are_kept() -> None:
    assert from_json({"pct": [4, 9, 16, 22]}) is not None
    assert from_json({"pct": [10, 9, 16, 22]}) is None  # each brighter than the one before
    assert from_json({"pct": [0, 9, 16, 22]}) is None
    assert from_json({"pct": [4, 9, 16, 101]}) is None
    assert from_json({"pct": [4, 9, 16]}) is None
    bad_colour = {"pct": list(START), "colour": [WARM, RHYTHM, RHYTHM, {"name": "beige"}]}
    assert from_json(bad_colour) is None  # only the fixed row of colours
    fitted = from_json(
        {"pct": list(START), "colour": [{"kelvin": 1500}, RHYTHM, RHYTHM, {"kelvin": 9000}]},
        (2200, 6500),
    )
    assert fitted is not None and fitted.colour[0] == {"kelvin": 2200}
    assert fitted.colour[3] == {"kelvin": 6500}  # within what the lamp can


def test_words_of_the_scale() -> None:
    assert stand_of(15) == 0 and stand_of(100) == 3 and stand_of(20) is None
    assert between(3) == (None, 0)
    assert between(47) == (1, 2)
    assert between(60) == (2, 2)
    assert capability(["onoff"]) == "onoff"
    assert capability(["brightness"]) == "dim"
    assert capability(["color_temp"]) == "white"
    assert capability(["color_temp", "xy"]) == "colour"


def test_a_group_and_the_same_type_give_their_standen() -> None:
    model = HouseModel()
    model.devices["d1"] = _device("d1", "Make", "Bulb 1")
    model.devices["d2"] = _device("d2", "Make", "Bulb 1")
    for lamp, device in (("light.a", "d1"), ("light.b", "d2"), ("light.c", None)):
        model.entities[lamp] = Entity(lamp, lamp, lamp, device_id=device)
    model.entities["light.group"] = Entity("light.group", "g", "Group")
    model.steered_by["light.c"] = "light.group"
    levels = LightLevels(
        model, {"light.a": STEEP, "light.group": replace(STEEP, pct=(5, 10, 20, 30))}
    )
    assert levels.of("light.a")[1] == "own"
    assert levels.of("light.b")[1:] == ("type", "light.a")  # a new lamp of the same type
    assert levels.of("light.c")[1:] == ("group", "light.group")
    assert levels.real("light.c", 15) == 5
    model.entities["light.d"] = Entity("light.d", "d", "Other")
    assert levels.of("light.d")[1] == "start"


def _device(device_id: str, make: str, model: str):  # type: ignore[no-untyped-def]
    from housemind.core.model import Device

    return Device(device_id, device_id, None, "demo", make, model)


# -- the brain in standen (E-494, E-495) ----------------------------------------------------


async def start(
    at: datetime, tmp_path: Path, plain: bool = False
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    if plain:
        # A lamp in the living room that only goes on and off (E-492).
        lamp = "light.living_plain"
        house.ha.model.entities[lamp] = Entity(
            lamp,
            "demo-plain",
            "Woonkamer staande lamp",
            device_id=lamp,
            area_id="living",
            capabilities={"supported_color_modes": ["onoff"]},
        )
        house.ha.states[lamp] = State(
            lamp, "off", {"supported_color_modes": ["onoff"]}, clock.now()
        )
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    for i in (1, 2):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (f"demo-user-{i}", f"Resident {i}", 4, json.dumps({}), clock.now().isoformat()),
        )
    await hm._refresh_people()
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


async def calm_evening(
    tmp_path: Path, plain: bool = False
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    hm, clock, ha = await start(local(19, 18, 0), tmp_path, plain)
    assert hm.learning is not None
    for day in (13, 14, 15):
        hm.rhythm.record(
            HouseMode.HOME, HouseMode.SLEEP, local(day, 22, 45), DayType.WORKDAY, DayType.WORKDAY
        )
    hm.learning.steps[("bedtime_dim", "living")] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.living_presence", "on")
    await ha.simulate("light.living_reading", "on", {"brightness": 255})
    await ha.simulate("light.living_ceiling", "on", {"brightness": 255, "color_temp_kelvin": 4000})
    return hm, clock, ha


async def test_a_calm_evening_puts_each_lamp_on_its_own_gedimd(tmp_path: Path) -> None:
    hm, clock, ha = await calm_evening(tmp_path)
    assert await hm.levels_save("light.living_reading", STEEP.to_json())
    await until(clock, hm, local(19, 22, 16))
    assert pct(ha, "light.living_reading") == 4  # its own Gedimd, not 15
    assert pct(ha, "light.living_ceiling") == 15  # the start values
    assert ha.states["light.living_ceiling"].attributes.get("color_temp_kelvin") == 2200  # warm
    line = await hm.db.fetchall(
        "SELECT data FROM decisions WHERE entity_id = 'light.living_reading'"
    )
    assert line, "the dim was recorded"
    await hm.stop()


async def test_a_lamp_that_only_goes_on_and_off_goes_out_when_another_stays(tmp_path: Path) -> None:
    hm, clock, ha = await calm_evening(tmp_path, plain=True)
    await ha.simulate("light.living_plain", "on")
    standen = {**Standen().to_json(), "dim": "off"}
    assert await hm.levels_save("light.living_plain", standen)
    await until(clock, hm, local(19, 22, 16))
    assert ha.states["light.living_plain"].state == "off"
    assert pct(ha, "light.living_reading") == 15
    await hm.stop()


async def test_left_as_it_is_by_default(tmp_path: Path) -> None:
    hm, clock, ha = await calm_evening(tmp_path, plain=True)
    await ha.simulate("light.living_plain", "on")
    await until(clock, hm, local(19, 22, 16))
    assert ha.states["light.living_plain"].state == "on"  # keep: as it is (E-492)
    await hm.stop()


async def test_the_rule_and_the_proposal_speak_in_standen(tmp_path: Path) -> None:
    hm, clock, _ha = await calm_evening(tmp_path)
    assert hm.learning is not None and hm.explainer is not None
    hm.learning.steps[("bedtime_dim", "living")] = Step.PROPOSE
    row = when(hm, "nl", "bedtime_dim", "living", Step.PROPOSE, 4)
    assert "op Gedimd mag zetten" in row["when"], row["when"]
    assert "%" not in row["when"]
    await until(clock, hm, local(19, 22, 16))
    asked = [p for p in await hm.proposals.open(clock.now()) if p.skill == "bedtime_dim"]
    assert asked and asked[0].facts.get("stand") == "dim"
    request = asked[0].request(Layer.PLANNER, clock.now(), None)
    decision = Decision(request, Outcome.PROPOSED, None, Step.PROPOSE, (), clock.now())
    assert "op Gedimd zetten" in hm.explainer.question("nl", decision)
    assert "set" in hm.explainer.question("en", decision) and "Dimmed" in hm.explainer.question(
        "en", decision
    )
    await hm.stop()


async def test_safety_lights_full_and_white(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 21, 0), tmp_path)
    smoke = next(
        (e for e, s in ha.states.items() if e.startswith("binary_sensor.") and "smoke" in e), None
    )
    assert smoke is not None
    await ha.simulate(smoke, "on")
    await clock.advance(1)
    await hm.tick()
    ceiling = ha.states["light.living_ceiling"]
    assert ceiling.state == "on" and pct(ha, "light.living_ceiling") == 100
    assert ceiling.attributes.get("color_temp_kelvin") == 4000  # white (E-495)
    assert pct(ha, "light.living_reading") == 100
    await hm.stop()


async def test_learning_a_hand_choice_on_the_felt_scale(tmp_path: Path) -> None:
    hm, _clock, _ha = await start(local(19, 20, 0), tmp_path)
    assert hm.learning is not None
    await hm.levels_save("light.kitchen", STEEP.to_json())
    assert hm.learning.felt("light.kitchen", 9) == 35.0  # its Normaal is 35 on the scale
    await hm.stop()


# -- Proef (E-491) ----------------------------------------------------------------------


async def test_proef_puts_the_lamp_on_its_stand_and_back_after_a_minute(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 20, 0), tmp_path)
    await hm.levels_save("light.living_ceiling", STEEP.to_json())
    assert ha.states["light.living_ceiling"].state == "off"
    said = await hm.levels_try("nl", ["light.living_ceiling"], 0, "demo-user-1")
    assert said is None
    assert pct(ha, "light.living_ceiling") == 4
    assert ha.states["light.living_ceiling"].attributes.get("color_temp_kelvin") == 2200
    assert hm.in_test("light.living_ceiling", clock.now())  # counts for nothing (E-479)
    # Another stand in the same minute: the way back stays the one from before the first.
    await clock.advance(30)
    await hm.levels_try("nl", ["light.living_ceiling"], 3, "demo-user-1")
    assert pct(ha, "light.living_ceiling") == 22
    await clock.advance(45)
    await hm.tick()
    assert ha.states["light.living_ceiling"].state == "on"  # the minute began again
    await clock.advance(30)
    await hm.tick()
    assert ha.states["light.living_ceiling"].state == "off"  # back as it was
    await hm.stop()


async def test_proef_shows_what_the_screen_shows_before_saving(tmp_path: Path) -> None:
    hm, _clock, ha = await start(local(19, 20, 0), tmp_path)
    draft = {"pct": [7, 20, 40, 80], "colour": [{"kelvin": 2700}, RHYTHM, RHYTHM, RHYTHM]}
    await hm.levels_try("nl", ["light.living_ceiling"], 0, "demo-user-1", draft)
    assert pct(ha, "light.living_ceiling") == 7
    assert "light.living_ceiling" not in hm.light_levels.own  # nothing saved
    await hm.levels_back(["light.living_ceiling"])
    assert ha.states["light.living_ceiling"].state == "off"
    await hm.stop()


async def test_a_room_tries_one_stand_on_all_its_lamps(tmp_path: Path) -> None:
    hm, _clock, ha = await start(local(19, 20, 0), tmp_path)
    await hm.levels_save("light.living_reading", STEEP.to_json())
    lamps = hm.stand_lamps("living")
    assert {"light.living_ceiling", "light.living_reading"} <= set(lamps)
    await hm.levels_try("nl", lamps, 1, "demo-user-1")
    assert pct(ha, "light.living_reading") == 9
    assert pct(ha, "light.living_ceiling") == 35
    await hm.levels_back(lamps)
    assert ha.states["light.living_reading"].state == "off"
    await hm.stop()


# -- what the screen shows (B-413) ----------------------------------------------------------


async def test_brein_apparaten_shows_the_standen(tmp_path: Path) -> None:
    hm, _clock, _ha = await start(local(19, 20, 0), tmp_path, plain=True)
    await hm.levels_save("light.living_reading", STEEP.to_json())
    await hm.levels_save("light.living_plain", {**Standen().to_json(), "dim": "off"})
    rows = {r["id"]: r for r in views.devices(hm, "nl")}
    reading = rows["light.living_reading"]["levels"]
    assert reading["can"] == "dim" and reading["pct"] == [4, 9, 16, 22]
    assert reading["source"] == "own" and reading["edit"] is True
    ceiling = rows["light.living_ceiling"]["levels"]
    assert ceiling["can"] == "white" and ceiling["source"] == "start"
    assert ceiling["pct"] == list(START)
    plain = rows["light.living_plain"]["levels"]
    assert plain["can"] == "onoff" and plain["dim"] == "off"
    assert "levels" not in rows.get("binary_sensor.living_presence", {})
    assert rows["light.living_reading"]["areaId"] == "living"
    await hm.stop()


async def test_saved_standen_survive_a_restart(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 20, 0), tmp_path)
    await hm.levels_save("light.kitchen", STEEP.to_json())
    await hm.stop()
    again = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path))
    await again.start(autorun=False)
    assert again.light_levels.own["light.kitchen"].pct == STEEP.pct
    await again.levels_reset("light.kitchen")
    assert "light.kitchen" not in again.light_levels.own
    await again.stop()


async def test_a_new_lamp_of_the_same_type_takes_the_standen(tmp_path: Path) -> None:
    clock = SimulatedClock(local(19, 20, 0).astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    for lamp in ("light.kitchen", "light.living_reading"):
        device = house.ha.model.devices[lamp]
        house.ha.model.devices[lamp] = replace(device, manufacturer="Make", model="Bulb")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    await hm.levels_save("light.kitchen", STEEP.to_json())
    standen, source, origin = hm.light_levels.of("light.living_reading")
    assert (source, origin) == ("type", "light.kitchen") and standen.pct == STEEP.pct
    # Saving is for this lamp only (E-493): a lamp of the same type with standen of its own
    # keeps them – no tick box that writes over all the others.
    own = Standen((10, 30, 50, 90), STEEP.colour)
    await hm.levels_save("light.living_reading", own.to_json())
    await hm.levels_save("light.kitchen", Standen((3, 8, 14, 20), STEEP.colour).to_json())
    assert hm.light_levels.of("light.living_reading")[0].pct == (10, 30, 50, 90)
    assert hm.light_levels.of("light.kitchen")[0].pct == (3, 8, 14, 20)
    await hm.stop()


# -- one card for the room (E-499) -------------------------------------------------------


async def test_the_card_asks_for_the_room_and_says_what_each_lamp_gets(tmp_path: Path) -> None:
    hm, clock, ha = await calm_evening(tmp_path, plain=True)
    assert hm.learning is not None
    await ha.simulate("light.living_plain", "on")
    await hm.levels_save("light.living_reading", STEEP.to_json())
    hm.learning.steps[("bedtime_dim", "living")] = Step.PROPOSE
    await until(clock, hm, local(19, 22, 16))
    cards = [c for c in await views.proposals(hm, "nl", 3) if c["areaId"] == "living"]
    assert len(cards) == 1  # one card for the room, not one per lamp
    card = cards[0]
    assert card["question"] == "Mag ik de woonkamer op Gedimd zetten?"
    assert card["reason"] == "Over 30 minuten gaan jullie meestal slapen, rond 22:45."
    lamps = {lamp["name"]: lamp for lamp in card["lamps"]}
    reading = next(v for k, v in lamps.items() if "leeslamp" in k)
    ceiling = next(v for k, v in lamps.items() if "plafondlamp" in k)
    plain = next(v for k, v in lamps.items() if "staande" in k)
    assert reading["pct"] == 4 and reading["text"] == "alleen helderheid"
    assert ceiling["pct"] == 15 and ceiling["kelvin"] == 2200
    assert plain["stays"] is True and plain["text"] == "blijft zoals hij is"
    # One yes for the card is a yes for every lamp in it.
    await hm.answer(card["id"], Answer.DO, "demo-user-1")
    assert pct(ha, "light.living_reading") == 4
    assert pct(ha, "light.living_ceiling") == 15
    assert ha.states["light.living_plain"].state == "on"
    await hm.stop()


# -- vandaag met deze lamp (E-500) -----------------------------------------------------------


async def test_the_lamps_day_says_what_happened_and_who_went_first(tmp_path: Path) -> None:
    hm, clock, ha = await calm_evening(tmp_path)
    await hm.levels_save("light.living_reading", STEEP.to_json())
    await until(clock, hm, local(19, 22, 16))  # the calm evening: Gedimd, its own 4%
    await clock.advance(60)
    await ha.simulate("light.living_reading", "on", {"brightness": 23})  # by hand, about 9%
    rows = {r["id"]: r for r in views.devices(hm, "nl")}
    today = rows["light.living_reading"]["levels"]["today"]
    events = today["events"]
    assert [e["who"] for e in events] == [
        "Jij, met de hand",
        "Rustige avond voor het slapen: Gedimd",
        "Jij, met de hand",
    ], events
    assert [e["what"] for e in events] == ["Aan", "Zachter", "Feller"]
    assert events[1]["pct"] == 4 and events[2]["pct"] == 9
    assert today["since"] is not None  # HouseMind started today: it says since when
    await hm.stop()
