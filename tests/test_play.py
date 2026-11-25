"""'Speel het huis' (E-321, chapter 25): play a demo house and see what HouseMind does – only in
a demo house, never in the real one (B-187)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from housemind.core.clock import DemoClock
from housemind.demo.houses import SHOWN_HOUSES, DemoHouse, load_demo_house
from housemind.demo.life import energy_sim, live, simulate_energy
from housemind.demo.play import Busy, Locked, Play, load_clock
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.server.web import create_app
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
TZ = "Europe/Amsterdam"
# The demo house's clock starts here in the tests, not at the real time: what a story does
# depends on the time of day (found when the full check ran at 22:00 – the house was already
# dark, so it did not jump to the evening, and a house just started only watches at first).
AFTERNOON = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)  # a Monday, 15:00 in the house
LATE = datetime(2026, 10, 19, 20, 30, tzinfo=UTC)  # 22:30 in the house
HEADERS = {"X-Remote-User-Id": "user-1", "X-Remote-User-Display-Name": "Resident"}


@dataclass
class Demo:
    hm: HouseMind
    house: DemoHouse
    clock: DemoClock
    play: Play
    tasks: list[asyncio.Task[None]] = field(default_factory=list)


@contextlib.asynccontextmanager
async def _demo(
    key: str,
    tmp_path: Path,
    rhythm: bool = False,
    running: bool = False,
    start: datetime = AFTERNOON,
) -> AsyncIterator[Demo]:
    """A demo house as ``__main__`` starts it: its own clock, Play, and (on request) the
    residents' rhythm and the energy simulation. It stands still (Stop) unless asked, so
    things can be changed (B-188)."""
    clock = DemoClock(start, running=running)
    house = load_demo_house(key, clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start()
    await hm.set_house_setting("consent_learning", True)
    sim = energy_sim(house, TZ)
    play = Play(house, clock, hm, TZ, tmp_path / "play.json", sim)
    demo = Demo(hm, house, clock, play)
    if rhythm:
        demo.tasks.append(asyncio.create_task(live(house, clock, TZ, held=lambda: play.held)))
        demo.tasks.append(asyncio.create_task(simulate_energy(house, clock, TZ, sim)))
    try:
        yield demo
    finally:
        for task in demo.tasks:
            task.cancel()
        await hm.stop()


async def _story(demo: Demo, key: str) -> None:
    await demo.play.control({"do": "story", "key": key})
    await demo.play.wait()


# -- the clock ------------------------------------------------------------------------------


async def test_the_demo_clock_jumps_in_order_and_runs_faster() -> None:
    clock = DemoClock()
    start = clock.now()
    woke: list[int] = []

    async def sleeper(seconds: int) -> None:
        await clock.sleep(seconds)
        woke.append(seconds)

    tasks = [asyncio.create_task(sleeper(s)) for s in (20, 5, 10)]
    gone = asyncio.create_task(clock.sleep(8))
    await asyncio.sleep(0)
    gone.cancel()  # someone who stops waiting does not hold up the jump
    await clock.advance(30)
    await asyncio.gather(*tasks)
    assert woke == [5, 10, 20]
    assert clock.now() - start >= timedelta(seconds=30)
    # Faster: 100 seconds of the house in a moment of real time.
    clock.set_speed(1000)
    started = time.perf_counter()
    await asyncio.wait_for(clock.sleep(100), 2)
    assert time.perf_counter() - started < 1.5
    # Slower: twenty times slower, a second of the house takes twenty.
    clock.set_speed(0.05)
    before = clock.now()
    await asyncio.sleep(0.2)
    assert timedelta(0) < clock.now() - before < timedelta(seconds=0.1)


async def test_stop_holds_the_time_and_play_lets_it_run() -> None:
    clock = DemoClock()
    clock.set_speed(1000)
    woke: list[str] = []

    async def sleeper() -> None:
        await clock.sleep(60)
        woke.append("up")

    clock.stop()
    stood = clock.now()
    task = asyncio.create_task(sleeper())
    await asyncio.sleep(0.2)  # 200 seconds of the house at this speed – but it stands still
    assert clock.now() == stood and not woke and not clock.running
    await clock.advance(30)  # a jump moves it ahead, also while it stands still
    assert clock.now() == stood + timedelta(seconds=30) and not clock.running
    clock.play()
    await asyncio.wait_for(task, 2)
    assert woke == ["up"] and clock.running and clock.now() > stood + timedelta(seconds=60)


async def test_a_long_jump_plays_rounds_every_five_minutes_and_after_it_every_30_seconds() -> None:
    clock = DemoClock()
    rounds: list[float] = []

    async def brain() -> None:
        while True:
            await clock.sleep(30)
            rounds.append(clock.now().timestamp())

    task = asyncio.create_task(brain(), name="housemind-tick")
    await asyncio.sleep(0)
    clock.coarse = {"housemind-tick": 300}
    await clock.advance(3600)
    assert 10 <= len(rounds) <= 14  # every 5 minutes, not 120 times
    clock.relax()
    before = len(rounds)
    await clock.advance(60)
    assert len(rounds) - before >= 1  # back to every 30 seconds
    task.cancel()


async def test_after_a_restart_the_house_is_as_it_was_left(tmp_path: Path) -> None:
    """Zoals je het achterliet (the administrator's choice): its time, Stop or Play and the
    speed; its time never goes back."""
    store = tmp_path / "play.json"
    async with _demo("simple", tmp_path) as demo:
        await demo.play.control({"do": "jump", "to": "day"})
        await demo.play.wait()
        await demo.play.control({"do": "speed", "value": 20})
        left = demo.clock.now()
    again = load_clock(store)
    assert not again.running and again.speed == 20 and again.now() == left
    async with _demo("simple", tmp_path) as demo:
        await demo.play.control({"do": "play"})
        playing = demo.clock.now()
    again = load_clock(store)
    assert again.running and again.now() >= playing
    assert load_clock(tmp_path / "missing.json").running  # a new demo house lives by itself
    store.write_text('{"shift": 86400}')  # kept by the first version: how far ahead
    assert load_clock(store).now() - datetime.now(UTC) > timedelta(hours=23)


# -- only in a demo house (B-187) -----------------------------------------------------------


async def _next(ws: Any, kind: str, what: str | None = None) -> dict[str, Any]:
    while True:
        message = await ws.receive_json(timeout=10)
        if message["type"] == kind and (what is None or message.get("what") == what):
            return message  # type: ignore[no-any-return]


@pytest.mark.parametrize("demo_flag", [False, True])
async def test_the_real_house_refuses_every_play_message(tmp_path: Path, demo_flag: bool) -> None:
    """Without a demo house there is no bar, no panel, and a play message changes nothing –
    also when someone hands the server a Play by mistake for a house that is not a demo."""
    async with _demo("average", tmp_path, running=True) as demo:
        play = demo.play if not demo_flag else None
        app = create_app(demo.hm, tmp_path, ingress_only=False, demo=demo_flag, play=play)
        before = dict(demo.house.ha.states)
        async with TestClient(TestServer(app)) as client:
            ws = await client.ws_connect("/api/ws", headers=HEADERS)
            await ws.send_json({"type": "hello", "browserLanguage": "nl-NL"})
            snap = (await _next(ws, "snapshot"))["snapshot"]
            assert "demo" not in snap
            for message in (
                {"type": "play", "do": "everyone", "on": False},
                {"type": "play", "do": "emergency", "id": "binary_sensor.hall_smoke", "on": True},
                {"type": "play", "do": "jump", "to": "week"},
                {"type": "play", "do": "speed", "value": 20},
                {"type": "play", "do": "stop"},
            ):
                await ws.send_json(message)
            await ws.send_json({"type": "get", "what": "play"})
            await ws.send_json({"type": "get", "what": "readiness"})  # always answers
            while True:
                message = await ws.receive_json(timeout=10)
                assert message.get("what") != "play", "no panel in the real house"
                assert message["type"] != "snapshot" or "demo" not in message["snapshot"]
                if message.get("what") == "readiness":
                    break
            await ws.close()
        assert demo.house.ha.states["person.resident_1"].state == "home"
        assert demo.house.ha.states["binary_sensor.hall_smoke"].state == "off"
        assert {e: s.state for e, s in demo.house.ha.states.items()} == {
            e: s.state for e, s in before.items()
        }
        assert demo.clock.speed == 1 and demo.clock.running
        assert demo.play.busy is None


async def test_in_a_demo_house_the_bar_and_the_panel_work(tmp_path: Path) -> None:
    async with _demo("average", tmp_path, running=True) as demo:
        app = create_app(demo.hm, tmp_path, ingress_only=False, demo=True, play=demo.play)
        async with TestClient(TestServer(app)) as client:
            ws = await client.ws_connect("/api/ws", headers=HEADERS)
            await ws.send_json({"type": "hello", "browserLanguage": "nl-NL"})
            snap = (await _next(ws, "snapshot"))["snapshot"]
            assert snap["demo"]["speed"] == 1 and snap["demo"]["busy"] is None
            assert snap["demo"]["running"] is True
            assert snap["texts"]["play.open"] == "Speel het huis"
            await ws.send_json({"type": "get", "what": "play"})
            panel = (await _next(ws, "data", "play"))["data"]
            assert panel["house"] == demo.house.name
            assert "smoke" in panel["stories"]
            # While the house plays, a story or a jump waits for Stop (B-188); what you set
            # yourself goes along at once (E-473).
            await ws.send_json({"type": "play", "do": "story", "key": "smoke"})
            assert (await _next(ws, "saved"))["text"] == snap["texts"]["play.locked"]
            assert demo.play.busy is None
            await ws.send_json({"type": "play", "do": "stop"})
            # Every screen's picture says so at once, not only the panel's data.
            seen = {"snapshot": False, "data": False}
            while not all(seen.values()):
                message = await ws.receive_json(timeout=10)
                if message["type"] == "snapshot":
                    seen["snapshot"] = message["snapshot"]["demo"]["running"] is False
                elif message.get("what") == "play":
                    assert message["data"]["live"]["running"] is False
                    seen["data"] = True
            await ws.send_json({"type": "play", "do": "everyone", "on": False})
            panel = (await _next(ws, "data", "play"))["data"]
            assert all(not r["home"] for r in panel["residents"])
            assert demo.house.ha.states["person.resident_1"].state == "not_home"
            assert panel["last"]["kind"] == "control"
            await ws.send_json({"type": "play", "do": "speed", "value": 20})
            await _next(ws, "data", "play")
            assert demo.clock.speed == 20
            # A day ahead: what HouseMind does then is in Logboek, not a message per action.
            await ws.send_json({"type": "play", "do": "everyone", "on": True})
            await _next(ws, "data", "play")
            await ws.send_json({"type": "play", "do": "jump", "to": "day"})
            await _next(ws, "data", "play")
            await demo.play.wait()
            await ws.send_json({"type": "get", "what": "readiness"})
            while (message := await ws.receive_json(timeout=10)).get("what") != "readiness":
                assert message["type"] != "toast", message
            assert await demo.hm.db.fetchall("SELECT id FROM decisions WHERE outcome = 'executed'")
            await ws.close()


# -- every demo house -----------------------------------------------------------------------


@pytest.mark.parametrize("key", SHOWN_HOUSES)
async def test_every_story_plays_in_every_shown_demo_house(key: str, tmp_path: Path) -> None:
    async with _demo(key, tmp_path, rhythm=True) as demo:
        controls = demo.play.controls()
        stories = controls["stories"]
        assert {"home_dark", "leave", "away_long", "smoke", "leak"} <= set(stories)
        # Only what the house has (B-191).
        assert bool(controls["weather"]) == ("weather.home" in demo.house.ha.states)
        assert ("price_negative" in stories) == ("sensor.electricity_price" in demo.house.ha.states)
        assert ("burglar" in stories) == any(
            e.startswith("alarm_control_panel.") for e in demo.house.ha.states
        )
        viewer = await views.load_viewer(demo.hm, "local-user", None, True)
        for story in stories:
            before = demo.clock.now()
            await _story(demo, story)
            assert demo.play.busy is None
            assert demo.play.last is not None and demo.play.last["key"] == story
            assert demo.clock.now() > before
            panel = await demo.play.view(viewer, "local-user", "nl")
            assert panel["last"]["key"] == story
            await demo.play.control({"do": "safe"})
            await demo.play.control({"do": "everyone", "on": True})
        assert demo.hm.status.orb != "emergency"


async def test_coming_home_in_the_dark_asks_for_the_light(tmp_path: Path) -> None:
    """In the afternoon the story first goes to the evening; by then 'licht bij binnenkomen',
    start knowledge, may be asked: HouseMind asks for the lamp where someone comes in."""
    async with _demo("simple", tmp_path) as demo:
        await _story(demo, "home_dark")
        assert demo.play._dark()
        asked = [p for p in await demo.hm.open_proposals() if p.entity_id.startswith("light.")]
        assert asked, "light on entry is start knowledge: HouseMind asks"
        viewer = await views.load_viewer(demo.hm, "local-user", None, True)
        panel = await demo.play.view(viewer, "local-user", "nl")
        assert any("aandoen" in line["text"] for line in panel["happened"])


async def test_coming_home_late_in_a_house_just_started_watches_first(tmp_path: Path) -> None:
    """Late in the evening it is dark already: the story does not jump. A house that started a
    moment ago first only watches the lamp – the story says so ('de eerste keer kijkt het mee')."""
    async with _demo("simple", tmp_path, start=LATE) as demo:
        await _story(demo, "home_dark")
        assert demo.play._dark()
        lamps = [
            d
            for d in demo.hm.status.thoughts
            if d.request.skill == "light_on_entry" and d.request.entity_id.startswith("light.")
        ]
        assert lamps, "HouseMind turns to the lamp where someone comes in"
        viewer = await views.load_viewer(demo.hm, "local-user", None, True)
        panel = await demo.play.view(viewer, "local-user", "nl")
        assert any("aandoen" in line["text"] for line in panel["happened"])


async def test_leaving_guests_and_a_long_time_away(tmp_path: Path) -> None:
    async with _demo("average", tmp_path) as demo:
        await _story(demo, "leave")
        assert demo.hm.world is not None and demo.hm.world.modes.mode is HouseMode.AWAY
        await demo.play.control({"do": "everyone", "on": True})
        await _story(demo, "guests")
        assert demo.hm.world.modes.mode is HouseMode.GUESTS
        await demo.play.control({"do": "everyone", "on": True})
        await _story(demo, "away_long")
        assert demo.play.held  # the residents do not come home by themselves
        assert demo.hm.world.modes.mode is HouseMode.AWAY
        assert demo.hm.world.modes.suggest_vacation
        await demo.play.control({"do": "everyone", "on": True})
        assert not demo.play.held


@pytest.mark.parametrize("key", SHOWN_HOUSES)
async def test_someone_can_be_played_in_every_room(key: str, tmp_path: Path) -> None:
    """Every room of a demo house the App offers has a sensor, so the panel can say someone is
    there (the administrator's wish) – and HouseMind sees it like in a real house."""
    async with _demo(key, tmp_path) as demo:
        rooms = demo.play.controls()["rooms"]
        assert {r["area"] for r in rooms} == set(demo.house.ha.model.areas)
        for room in rooms:
            await demo.play.control({"do": "presence", "id": room["id"], "on": True})
            occupancy = demo.hm.world.snapshot(demo.clock.now()).occupancy  # type: ignore[union-attr]
            assert occupancy[room["area"]].occupied, room["name"]
            await demo.play.control({"do": "presence", "id": room["id"], "on": False})


async def test_a_new_sensor_lets_you_come_into_a_dark_room(tmp_path: Path) -> None:
    """The kitchen of the simple house had no sensor: now someone coming in, in the dark, turns
    HouseMind to its lamp."""
    async with _demo("simple", tmp_path, start=LATE) as demo:
        kitchen = {"do": "presence", "id": "binary_sensor.kitchen_motion", "on": True}
        await demo.play.control(kitchen)
        lamps = [d for d in demo.hm.status.thoughts if d.request.entity_id == "light.kitchen"]
        assert lamps and lamps[-1].request.skill == "light_on_entry"


@pytest.mark.parametrize("key", SHOWN_HOUSES)
async def test_waking_up_walks_through_the_right_rooms(key: str, tmp_path: Path) -> None:
    """With a sensor everywhere the story must not walk by the order of the names: bathroom,
    hall and kitchen by what they are for, never the attic or the utility room."""
    async with _demo(key, tmp_path) as demo:
        roles = {
            demo.hm.area_roles.get(demo.house.ha.model.entities[s.entity_id].area_id or "")
            for s in demo.play._walk("bathroom", "hall", "kitchen")
        }
        assert "hall" in roles and "kitchen" in roles
        utility = [s.entity_id for s in demo.play._walk("kitchen")]
        assert "binary_sensor.utility_motion" not in utility
        assert demo.play._living().area_id == "living"


async def test_an_emergency_left_from_before_a_restart_closes_at_the_start(
    tmp_path: Path,
) -> None:
    """After a restart the demo house's sensors are off again; an emergency HouseMind still held
    open from before closes by itself (the administrator's wish), one still on stays."""
    async with _demo("extended", tmp_path) as demo:
        await _story(demo, "smoke")
        await _story(demo, "burglar")
        assert demo.hm.status.orb == "emergency"
    async with _demo("extended", tmp_path) as demo:
        assert demo.hm.status.orb == "emergency"  # still open in its memory
        await demo.play.settle()
        assert demo.hm.status.orb != "emergency"
        assert not [n for n in await demo.hm.notifications() if n["kind"] == "emergency"]
    async with _demo("extended", tmp_path) as demo:
        await demo.play.control({"do": "emergency", "id": "binary_sensor.utility_leak", "on": True})
        await demo.play.settle()
        assert demo.hm.status.orb == "emergency"  # a sensor that is on stays an emergency


async def test_smoke_is_an_emergency_until_all_is_safe_again(tmp_path: Path) -> None:
    async with _demo("simple", tmp_path) as demo:
        await _story(demo, "smoke")
        assert demo.hm.status.orb == "emergency"
        assert all(
            demo.house.ha.states[e].state == "on"
            for e in demo.house.ha.states
            if e.startswith("light.")
        )
        await demo.play.control({"do": "safe"})
        await demo.play.control({"do": "presence", "id": "binary_sensor.hall_motion", "on": True})
        assert demo.house.ha.states["binary_sensor.hall_smoke"].state == "off"
        assert demo.hm.status.orb != "emergency"


async def test_the_free_controls_change_what_the_house_has(tmp_path: Path) -> None:
    async with _demo("extended", tmp_path, rhythm=True) as demo:
        ha = demo.house.ha
        await demo.play.control({"do": "switch", "id": "light.kitchen", "on": True, "by": "wall"})
        assert ha.states["light.kitchen"].state == "on"
        await demo.play.control(
            {"do": "switch", "id": "cover.living_curtain", "on": False, "by": "person"}
        )
        assert ha.states["cover.living_curtain"].state == "closed"
        await demo.play.control({"do": "opening", "id": "binary_sensor.bedroom_window", "on": True})
        assert ha.states["binary_sensor.bedroom_window"].state == "on"
        await demo.play.control({"do": "weather", "value": "rainy"})
        await demo.play.control({"do": "outdoor", "value": -5})
        assert ha.states["weather.home"].state == "rainy"
        assert ha.states["weather.home"].attributes["temperature"] == -5
        await demo.play.control({"do": "jump", "to": "morning"})
        await demo.play.wait()
        assert ha.states["weather.home"].state == "rainy"  # it stays until 'Gewoon'
        before = ha.states["climate.living"].attributes["current_temperature"]
        await demo.play.control({"do": "room_temp", "area": "living", "delta": 0.5})
        assert ha.states["climate.living"].attributes["current_temperature"] == before + 0.5
        await demo.play.control({"do": "price", "value": "negative"})
        assert float(ha.states["sensor.electricity_price"].state) < 0
        await demo.play.control({"do": "price", "value": None})
        assert float(ha.states["sensor.electricity_price"].state) > 0
        await demo.play.control({"do": "resident", "id": "person.resident_2", "on": False})
        assert ha.states["person.resident_2"].state == "not_home"
        await demo.play.control({"do": "emergency", "id": "alarm_control_panel.home", "on": True})
        assert ha.states["alarm_control_panel.home"].state == "triggered"
        await demo.play.control({"do": "safe"})
        assert ha.states["alarm_control_panel.home"].state == "disarmed"
        # Something the house does not have, or not this kind: nothing happens.
        await demo.play.control({"do": "switch", "id": "lock.front_door", "on": False})
        await demo.play.control({"do": "emergency", "id": "light.kitchen", "on": True})
        assert ha.states["lock.front_door"].state == "locked"


async def test_while_it_plays_you_play_along_and_stories_wait(tmp_path: Path) -> None:
    """The administrator's wish (11-11-2026): "als ik de demo laat lopen, dan kan ik niet op de
    knoppen drukken". While the house plays, what you set yourself happens at once and the
    time goes on; only a story or a jump waits for Stop (E-473, B-188)."""
    async with _demo("simple", tmp_path, running=True) as demo:
        for change in ({"do": "story", "key": "smoke"}, {"do": "jump", "to": "week"}):
            with pytest.raises(Locked):
                await demo.play.control(change)
        assert demo.play.busy is None
        await demo.play.control({"do": "speed", "value": 0.1})  # the speed may change
        assert demo.clock.speed == 0.1
        await demo.play.control({"do": "everyone", "on": False})
        assert demo.house.ha.states["person.resident_1"].state == "not_home"
        motion = "binary_sensor.bedroom_motion"
        await demo.play.control({"do": "presence", "id": motion, "on": True})
        assert demo.house.ha.states[motion].state == "on"
        assert demo.clock.running  # it goes on playing
        await demo.play.control({"do": "stop"})
        stood = demo.clock.now()
        await asyncio.sleep(0.1)
        assert demo.clock.now() == stood  # Stop: the time stands still
        await demo.play.control({"do": "play"})
        await asyncio.sleep(0.1)
        assert demo.clock.now() > stood


async def test_stop_halts_a_jump_where_it_got_to(tmp_path: Path) -> None:
    """Found in the real house: on a slow machine a week ahead took minutes and could not be
    stopped. Stop ends it where it got to; the time stands still there."""
    async with _demo("simple", tmp_path, rhythm=True) as demo:
        start = demo.clock.now()
        await demo.play.control({"do": "jump", "to": "week"})
        await asyncio.sleep(0.3)
        assert demo.play.busy is not None
        await demo.play.control({"do": "stop"})
        assert demo.play.busy is None and not demo.clock.running
        reached = demo.clock.now()
        assert start < reached < start + timedelta(days=7)
        assert not demo.play.forwarding and not demo.clock.coarse
        await asyncio.sleep(0.1)
        assert demo.clock.now() == reached
        await demo.play.control({"do": "everyone", "on": False})  # changes work again


async def test_one_thing_at_a_time(tmp_path: Path) -> None:
    async with _demo("simple", tmp_path) as demo:
        await demo.play.control({"do": "jump", "to": "day"})
        with pytest.raises(Busy):
            await demo.play.control({"do": "everyone", "on": False})
        await demo.play.wait()
        assert demo.play.busy is None


async def test_a_week_ahead_the_residents_answer_and_house_mind_learns(tmp_path: Path) -> None:
    async with _demo("simple", tmp_path, rhythm=True) as demo:
        viewer = await views.load_viewer(demo.hm, "local-user", None, True)
        before = (await demo.play.view(viewer, "local-user", "nl"))["steps"]
        start = demo.clock.now()
        await demo.play.control({"do": "jump", "to": "week"})
        await demo.play.wait()
        after = (await demo.play.view(viewer, "local-user", "nl"))["steps"]
        assert demo.clock.now() - start >= timedelta(days=7)
        assert after["propose"] > before["propose"]
        answered = await demo.hm.db.fetchall("SELECT id FROM proposals WHERE answer IS NOT NULL")
        assert answered  # the residents answered what HouseMind asked
