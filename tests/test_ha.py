from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from aiohttp.test_utils import TestServer

from housemind.core.clock import SimulatedClock
from housemind.core.model import Context, Entity, HouseModel, Source, State, StateChange
from housemind.ha.fake import FakeHomeAssistant
from housemind.ha.source import SourceTracker
from housemind.ha.translate import build_model, parse_state
from housemind.ha.websocket import HAConnectionError, WebSocketHomeAssistant
from tests.ha_ws_server import TOKEN, FakeWsServer

T0 = datetime(2026, 10, 17, 18, 0, tzinfo=UTC)


def test_build_model_merges_registry_and_state_attributes() -> None:
    state = parse_state(
        {
            "entity_id": "light.kitchen",
            "state": "on",
            "attributes": {"supported_color_modes": ["brightness"], "device_class": None},
            "last_changed": "2026-10-17T18:00:00+00:00",
        }
    )
    assert state is not None
    model = build_model(
        [{"floor_id": "f", "name": "Ground"}],
        [{"area_id": "kitchen", "name": "Kitchen", "floor_id": "f"}],
        [{"id": "d1", "name": "Lamp", "area_id": "kitchen"}],
        [
            {"entity_id": "light.kitchen", "id": "r1", "device_id": "d1", "platform": "x"},
            {
                "entity_id": "sensor.hidden",
                "id": "r2",
                "hidden_by": "user",
                "entity_category": "diagnostic",
            },
        ],
        {"light.kitchen": state},
    )
    kitchen = model.entities["light.kitchen"]
    assert kitchen.registry_id == "r1"
    assert kitchen.capabilities["supported_color_modes"] == ["brightness"]
    assert model.area_of("light.kitchen") is not None
    assert model.area_of("light.kitchen").name == "Kitchen"  # type: ignore[union-attr]
    hidden = model.entities["sensor.hidden"]
    assert hidden.hidden and hidden.entity_category == "diagnostic"


def test_source_tracker_classifies_contexts() -> None:
    clock = SimulatedClock(T0)
    tracker = SourceTracker(clock)
    tracker.own_user_id = "me"
    tracker.record_own_context("mine")
    assert tracker.classify("light.a", Context("mine")) is Source.HOUSEMIND
    assert tracker.classify("light.a", Context("x", user_id="person")) is Source.PERSON
    assert tracker.classify("light.a", Context("y", parent_id="p")) is Source.AUTOMATION
    assert tracker.classify("light.a", Context("z")) is Source.PHYSICAL
    assert tracker.classify("light.a", None) is Source.UNKNOWN
    # Own user id only counts while the entity is expected.
    assert tracker.classify("light.a", Context("q", user_id="me")) is Source.PERSON
    tracker.expect("light.a")
    assert tracker.classify("light.a", Context("r", user_id="me")) is Source.HOUSEMIND
    tracker.other_system_entities.add("number.battery_mode")
    assert tracker.classify("number.battery_mode", Context("s")) is Source.OTHER_SYSTEM


def _fake() -> tuple[FakeHomeAssistant, SimulatedClock]:
    clock = SimulatedClock(T0)
    model = HouseModel(
        entities={"light.kitchen": Entity("light.kitchen", "reg-kitchen", "Kitchen light")}
    )
    states = {"light.kitchen": State("light.kitchen", "off", {}, T0)}
    return FakeHomeAssistant(clock, model, states), clock


async def test_fake_marks_own_actions_and_people() -> None:
    ha, _ = _fake()
    seen: list[StateChange] = []

    async def listener(change: StateChange) -> None:
        seen.append(change)

    ha.on_state_change(listener)
    result = await ha.call_action("light.turn_on", "light.kitchen", {"brightness_pct": 50})
    assert result.ok
    assert ha.states["light.kitchen"].attributes["brightness"] == 128
    await ha.simulate("light.kitchen", "off", by=Source.PHYSICAL)
    await ha.simulate("light.kitchen", "on", by=Source.PERSON)
    await ha.simulate("light.kitchen", "off", by=Source.AUTOMATION)
    assert [c.source for c in seen] == [
        Source.HOUSEMIND,
        Source.PHYSICAL,
        Source.PERSON,
        Source.AUTOMATION,
    ]


async def test_fake_failures_and_rename() -> None:
    ha, _ = _fake()
    ha.failing_entities.add("light.kitchen")
    assert not (await ha.call_action("light.turn_on", "light.kitchen")).ok
    assert not (await ha.call_action("light.blink", "light.kitchen")).ok
    await ha.rename_entity("light.kitchen", "light.keuken")
    assert ha.model.entities["light.keuken"].registry_id == "reg-kitchen"
    assert "light.kitchen" not in ha.states


async def test_websocket_client_end_to_end() -> None:
    server = FakeWsServer()
    async with TestServer(server.app()) as test_server:
        url = str(test_server.make_url("/api/websocket"))
        client = WebSocketHomeAssistant(SimulatedClock(T0), TOKEN, url=url, timeout_s=5)
        config = await client.connect()
        assert config.version == "2026.10.1"
        assert config.language == "nl"
        model = await client.load_model()
        assert model.entities["light.kitchen"].registry_id == "reg-1"
        assert model.area_of("light.kitchen") is not None
        changes: list[StateChange] = []

        async def listener(change: StateChange) -> None:
            changes.append(change)

        client.on_state_change(listener)
        result = await client.call_action("light.turn_on", "light.kitchen")
        assert result.ok and result.context_id == "srv-1"
        await asyncio.sleep(0.05)
        assert changes and changes[0].new is not None and changes[0].new.state == "on"
        # The event arrived before the result: recognised via own user id + expectation.
        assert changes[0].source is Source.HOUSEMIND
        assert (await client.available_actions())["light"] == {"turn_on", "turn_off"}
        await client.close()


async def test_a_long_announcement_counts_as_said() -> None:
    """Found in the house: a voice satellite answers an announcement only once it has been
    said. The morning briefing took 35 seconds, longer than HouseMind waited, so each time it
    was taken for 'not said' and said again the next round – five times. Still busy is being
    said, and the round goes on; a satellite that cannot say it answers at once (E-107)."""
    server = FakeWsServer()
    server.states["assist_satellite.living"] = {
        "entity_id": "assist_satellite.living",
        "state": "idle",
        "attributes": {},
        "last_changed": "2026-10-17T18:00:00+00:00",
        "context": {"id": "c2", "user_id": None, "parent_id": None},
    }
    server.speaking_s = 1.0
    async with TestServer(server.app()) as test_server:
        url = str(test_server.make_url("/api/websocket"))
        client = WebSocketHomeAssistant(SimulatedClock(T0), TOKEN, url=url, timeout_s=5)
        client.speak_wait_s = 0.2
        await client.connect()
        loop = asyncio.get_running_loop()
        started = loop.time()
        said = await client.speak("assist_satellite.living", "Goedemorgen. Het wordt droog.")
        assert said.ok, said.error
        assert loop.time() - started < 0.9  # did not wait for the whole sentence
        assert server.calls[-1]["service"] == "announce"
        await asyncio.sleep(1.0)  # the sentence is done
        server.speaking_s = 0.0
        gone = await client.speak("assist_satellite.gone", "Goedemorgen.")
        assert not gone.ok
        await client.close()


async def test_websocket_client_rejects_bad_token() -> None:
    server = FakeWsServer()
    async with TestServer(server.app()) as test_server:
        url = str(test_server.make_url("/api/websocket"))
        client = WebSocketHomeAssistant(SimulatedClock(T0), "wrong", url=url, timeout_s=5)
        with pytest.raises(HAConnectionError):
            await client.connect()
        await client.close()


async def test_after_a_restart_of_home_assistant_the_connection_comes_back() -> None:
    """H7.4: lost → told, trying again a little longer each time, back → told; HA that is
    still starting is waited for, and listening goes on as before."""
    server = FakeWsServer()
    async with TestServer(server.app()) as test_server:
        url = str(test_server.make_url("/api/websocket"))
        client = WebSocketHomeAssistant(SimulatedClock(T0), TOKEN, url=url, timeout_s=5)
        client.retry_delays = (0.05, 0.1)
        client.starting_poll_s = 0.01
        await client.connect()
        told: list[bool] = []
        back = asyncio.Event()

        async def connection(connected: bool) -> None:
            told.append(connected)
            if connected:
                back.set()

        client.on_connection(connection)
        changes: list[StateChange] = []

        async def listener(change: StateChange) -> None:
            changes.append(change)

        client.on_state_change(listener)
        await server.drop()
        await asyncio.sleep(0.3)  # HA is away for a while: a few tries fail
        assert told == [False] and not client.connected
        assert not (await client.call_action("light.turn_on", "light.kitchen")).ok
        server.version, server.starting, server.accepting = "2026.11.0", 2, True
        await asyncio.wait_for(back.wait(), 5)
        assert told == [False, True]
        assert (await client.ha_config()).version == "2026.11.0"
        assert len(server.subscriptions["state_changed"]) == 1  # listening again
        old = server.states["light.kitchen"]
        await server.push_state_changed("light.kitchen", old, {**old, "state": "on"})
        await asyncio.sleep(0.1)
        assert changes and changes[-1].new is not None and changes[-1].new.state == "on"
        await client.close()


async def test_a_listener_may_ask_home_assistant_without_blocking() -> None:
    """Found on the first real house: a state listener that asks HA something (who is admin,
    switch a lamp) must not wait for an answer that the blocked reader would bring."""
    server = FakeWsServer()
    async with TestServer(server.app()) as test_server:
        url = str(test_server.make_url("/api/websocket"))
        client = WebSocketHomeAssistant(SimulatedClock(T0), TOKEN, url=url, timeout_s=2)
        await client.connect()
        answers: list[int] = []

        async def listener(change: StateChange) -> None:
            if change.entity_id == "light.kitchen" and len(answers) < 1:
                answers.append(len(await client.users()))
                await client.call_action("light.turn_off", "light.kitchen")

        client.on_state_change(listener)
        started = asyncio.get_running_loop().time()
        await server.push_state_changed(
            "light.kitchen",
            server.states["light.kitchen"],
            {**server.states["light.kitchen"], "state": "on", "context": {"id": "x"}},
        )
        for _ in range(100):
            if server.calls:
                break
            await asyncio.sleep(0.01)
        assert answers == [1] and server.calls
        assert asyncio.get_running_loop().time() - started < 1.5
        # The user list is kept: asking again does not go to Home Assistant.
        await client.users()
        assert server.user_list_calls == 1
        await client.close()
