"""Temperaturen per ruimte (E-583), as the administrator of the real house asked: "Net als
Standen en 'Uit als er niemand meer is', zou ik dat ook voor 'Klimaat' willen voor apparaten
waarbij je de temperatuur kan instellen ... welke temperatuur als men thuis is, en welk
temperatuur als men weg is, en welk temperatuur als je gaat slapen", with sliders in half
degrees. From the user's side: what the thermostat gets in each moment of the house, and the
window in Brein › Apparaten with its one Bewaren. What each test checks is its docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.room_temps import RoomTemps, Temps
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Catalog
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.server.web import create_app
from housemind.world.house_mode import HouseMode
from tests.test_skills import EVENING, Rig, _by

ROOT = Path(__file__).resolve().parents[1]
LIVING = "climate.living"
NOON = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)


def _own(rig: Rig, heat: Temps | None = None, cool: Temps | None = None, after: float = 4) -> None:
    rig.engine.room_temps = {"living": RoomTemps(heat, cool, after)}


def _set(requests: list[Any]) -> tuple[float, str] | None:
    found = _by(requests, LIVING)
    return (found[0].data["temperature"], found[0].skill) if found else None


# -- what the thermostat gets --------------------------------------------------------------------


async def test_the_room_gets_your_own_temperature_for_home(catalog: Catalog) -> None:
    """Someone is home: the living room goes to the administrator's Thuis, not to what a living
    room usually gets."""
    rig = Rig(catalog, "family_home", EVENING)
    _own(rig, Temps(21.5, 17.0, 16.0))
    assert _set(rig.run_as(HouseMode.HOME, 1)) == (21.5, "heat_on_presence")


async def test_the_house_asleep_gets_your_sleep_temperature(catalog: Catalog) -> None:
    """The house sleeps: Slapen as set, and the reason says it is the administrator's own."""
    rig = Rig(catalog, "family_home", EVENING)
    _own(rig, Temps(21.5, 17.5, 16.0))
    found = _by(rig.run_as(HouseMode.SLEEP, 1), LIVING)
    assert found and found[0].data == {"temperature": 17.5}
    assert found[0].skill == "setback_sleep" and found[0].facts.get("variant") == "own"


async def test_away_waits_as_long_as_pas_na_says(catalog: Catalog) -> None:
    """'Pas na' 4 hours: three hours away nothing changes, five hours away it is Weg. On
    vacation Weg comes at once."""
    rig = Rig(catalog, "family_home", EVENING)
    _own(rig, Temps(21.0, 17.0, 16.0), after=4)
    assert _set(rig.run_as(HouseMode.AWAY, 3)) is None
    assert _set(rig.run_as(HouseMode.AWAY, 5)) == (16.0, "setback_away")
    assert _set(rig.run_as(HouseMode.VACATION, 0)) == (16.0, "setback_away")


async def test_pas_na_meteen_is_weg_as_soon_as_everyone_left(catalog: Catalog) -> None:
    """'Pas na' at once: Weg as soon as nobody is home."""
    rig = Rig(catalog, "family_home", EVENING)
    _own(rig, Temps(21.0, 17.0, 16.5), after=0)
    assert _set(rig.run_as(HouseMode.AWAY, 0)) == (16.5, "setback_away")


async def test_without_own_temperatures_nothing_changes(catalog: Catalog) -> None:
    """A room without its own: as before – a day away before lower, the usual values."""
    rig = Rig(catalog, "family_home", EVENING)
    assert _set(rig.run_as(HouseMode.AWAY, 5)) is None
    assert _set(rig.run_as(HouseMode.AWAY, 25)) == (17.5, "setback_away")


async def test_cooling_has_its_own_three(catalog: Catalog) -> None:
    """An airco set to cool: Thuis, Slapen and Weg of cooling – Weg warmer, so it cools less."""
    rig = Rig(catalog, "family_home", EVENING)
    await rig.set(LIVING, "cool", temperature=25.0, current_temperature=26.0)
    _own(rig, Temps(21.0, 17.0, 16.0), Temps(24.0, 25.0, 28.0), after=4)
    assert _set(rig.run_as(HouseMode.HOME, 1)) == (24.0, "cool_on_presence")
    await rig.set(LIVING, "cool", temperature=24.0, current_temperature=25.0)  # as it was set
    assert _set(rig.run_as(HouseMode.SLEEP, 1)) == (25.0, "setback_sleep")
    assert _set(rig.run_as(HouseMode.AWAY, 5)) == (28.0, "setback_away")


async def test_one_setpoint_lowers_only_while_it_heats(catalog: Catalog) -> None:
    """A heat pump that heats and cools with one temperature: at night Slapen while the room is
    colder than Thuis; in a warm room it stays at Thuis, so that Slapen never means more
    cooling."""
    rig = Rig(catalog, "family_home", EVENING)
    await rig.set(LIVING, "heat_cool", temperature=21.0, current_temperature=19.0)
    _own(rig, Temps(21.0, 17.0, 16.0))
    assert _set(rig.run_as(HouseMode.SLEEP, 1)) == (17.0, "setback_sleep")
    await rig.set(LIVING, "heat_cool", temperature=21.0, current_temperature=24.0)
    assert _set(rig.run_as(HouseMode.SLEEP, 1)) is None  # it stays at Thuis: 21


# -- the window ----------------------------------------------------------------------------------


async def _house(tmp_path: Path) -> tuple[HouseMind, Any]:
    clock = SimulatedClock(NOON)
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    known = await hm.db.fetchone("SELECT 1 FROM people WHERE user_id = 'demo-user-1'")
    if known is None:
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            ("demo-user-1", "Resident 1", 4, json.dumps({}), clock.now().isoformat()),
        )
    await hm._refresh_people()
    return hm, house.ha


async def test_the_list_shows_the_rooms_three_and_where_they_come_from(tmp_path: Path) -> None:
    """Brein › Apparaten: the thermostat shows Thuis, Slapen and Weg of its room, in half
    degrees, from the start values while nobody set them; 'Pas na' starts at a day."""
    hm, _ha = await _house(tmp_path)
    row = next(r for r in views.devices(hm, "nl") if r["id"] == LIVING)
    temps = row["temps"]
    assert not temps["own"]
    heat = temps["values"]["heat"]
    assert {m: heat[m]["from"] for m in heat} == {
        "home": "start",
        "sleep": "start",
        "away": "start_away",
    }
    assert all(heat[m]["value"] * 2 == int(heat[m]["value"] * 2) for m in heat)
    assert heat["sleep"]["value"] <= heat["home"]["value"]
    assert temps["after"] == 24
    await hm.stop()


async def test_one_bewaren_keeps_everything_also_after_a_restart(tmp_path: Path) -> None:
    """The window sends Thuis, Slapen, Weg and 'Pas na' with one Bewaren over the websocket:
    the administrator hears it was saved, the list shows the room's own, and a restart keeps
    it."""
    hm, ha = await _house(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "set_level", "level": 4})
        await ws.send_json(
            {
                "type": "temperatures",
                "do": "save",
                "area": "living",
                "temps": {"heat": {"home": 21.5, "sleep": 17.0, "away": 16.5}, "after_h": 4},
            }
        )
        said: list[str] = []
        while "Temperaturen bewaard." not in said:  # setting the level says "Bewaard." first
            message = await ws.receive_json(timeout=5)
            if message["type"] == "saved":
                said.append(message["text"])
        await ws.close()
    temps = views._temps_view(hm, "nl", "living", LIVING)
    assert temps is not None and temps["own"] and temps["after"] == 4
    assert temps["values"]["heat"]["home"] == {"value": 21.5, "from": "own"}
    await hm.stop()
    again = HouseMind(hm.clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert again.skills is not None
    assert again.skills.room_temps["living"] == RoomTemps(Temps(21.5, 17.0, 16.5), None, 4.0)
    await again.stop()


async def test_only_halves_in_order_within_what_it_can_are_kept(tmp_path: Path) -> None:
    """Not on a half degree, Slapen warmer than Thuis, beyond what the thermostat can, or a
    'Pas na' that is not one of the steps: nothing is kept."""
    hm, _ha = await _house(tmp_path)
    for heat, after in (
        ({"home": 21.3, "sleep": 17.0, "away": 16.0}, 4),
        ({"home": 20.0, "sleep": 21.0, "away": 16.0}, 4),
        ({"home": 35.0, "sleep": 17.0, "away": 16.0}, 4),
        ({"home": 21.0, "sleep": 17.0, "away": 16.0}, 3),
    ):
        assert not await hm.room_temps_save("living", {"heat": heat, "after_h": after})
    assert hm.skills is not None and "living" not in hm.skills.room_temps
    await hm.stop()


async def test_back_to_what_it_learns(tmp_path: Path) -> None:
    """'Terug naar wat ik leer': the room's own go, and the start values count again."""
    hm, _ha = await _house(tmp_path)
    assert await hm.room_temps_save(
        "living", {"heat": {"home": 21.0, "sleep": 17.0, "away": 16.0}, "after_h": 24}
    )
    assert await hm.room_temps_save("living", None)
    temps = views._temps_view(hm, "nl", "living", LIVING)
    assert temps is not None and not temps["own"]
    assert temps["values"]["heat"]["home"]["from"] != "own"
    await hm.stop()


async def test_a_thermostat_that_also_cools_asks_both(tmp_path: Path) -> None:
    """A thermostat that can heat and cool: the window has both, and Bewaren needs both."""
    hm, ha = await _house(tmp_path)
    await ha.simulate(
        LIVING,
        "heat",
        {"temperature": 20.5, "current_temperature": 20.1, "hvac_modes": ["off", "heat", "cool"]},
    )
    temps = views._temps_view(hm, "nl", "living", LIVING)
    assert temps is not None and set(temps["values"]) == {"heat", "cool"}
    assert temps["now"] == "heat"  # it heats now: the window opens on Verwarmen
    heat = {"home": 21.0, "sleep": 17.0, "away": 16.0}
    assert not await hm.room_temps_save("living", {"heat": heat, "after_h": 4})
    cool = {"home": 24.0, "sleep": 24.5, "away": 27.0}
    assert await hm.room_temps_save("living", {"heat": heat, "cool": cool, "after_h": 4})
    await hm.stop()


async def test_a_thermostat_that_cools_now_opens_on_cooling(tmp_path: Path) -> None:
    """The airco cools now: the window opens on Koelen, where you want to change something."""
    hm, ha = await _house(tmp_path)
    await ha.simulate(
        LIVING,
        "cool",
        {"temperature": 24.0, "current_temperature": 25.0, "hvac_modes": ["off", "heat", "cool"]},
    )
    temps = views._temps_view(hm, "nl", "living", LIVING)
    assert temps is not None and temps["now"] == "cool"
    await hm.stop()
