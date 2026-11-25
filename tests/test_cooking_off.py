"""The hob or oven on while nobody is home (E-61, B-327): with a switch that really cuts its
power, HouseMind asks "Zal ik hem uitzetten?" – and only a person's yes switches it off. Never
on, never by itself, and never a child lock or a light."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer, Outcome
from housemind.server import views
from housemind.server.web import create_app
from tests.test_household import ROOT, TZ, leave_home, ticks
from tests.test_start_over import _hello, _next

HOB = "switch.kookplaat"
TOPIC = "house:cooking:dev.hob"


async def _start(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    """The family home with a hob on a smart plug, and an oven whose only switch is its child
    lock."""
    clock = SimulatedClock(datetime(2026, 10, 19, 11, 0, tzinfo=TZ).astimezone(UTC))
    ha = load_demo_house("family_home", clock, "nl").ha
    ha.model.devices["dev.hob"] = Device("dev.hob", "Kookplaat", "kitchen")
    ha.model.devices["dev.oven"] = Device("dev.oven", "Oven", "kitchen")
    for entity_id, name, device_id, device_class, state in (
        ("sensor.kookplaat_vermogen", "Vermogen", "dev.hob", "power", "0"),
        (HOB, "Kookplaat", "dev.hob", None, "on"),
        ("sensor.oven_vermogen", "Vermogen", "dev.oven", "power", "0"),
        ("switch.oven_kinderslot", "Kinderslot", "dev.oven", None, "on"),
    ):
        ha.model.entities[entity_id] = Entity(
            entity_id,
            f"r-{entity_id}",
            name,
            device_id=device_id,
            area_id="kitchen",
            device_class=device_class,
        )
        ha.states[entity_id] = State(entity_id, state, {}, clock.now())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, clock, ha


async def _asked(hm: HouseMind, topic: str = TOPIC) -> dict[str, Any] | None:
    """The card in the app, as a resident sees it."""
    row = next((n for n in await hm.notifications() if n["topic"] == topic), None)
    return None if row is None else views.notice_item(hm, "nl", row, False, "demo-user-1")


async def _answer(hm: HouseMind, tmp_path: Path, action: str) -> None:
    """Press a button on the notice in the app."""
    asked = await _asked(hm)
    assert asked is not None
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client)
        await _next(ws, "snapshot")
        await ws.send_json({"type": "notification", "id": asked["id"], "action": action})
        await _next(ws, "snapshot")
        await ws.close()


async def test_the_hob_on_a_smart_plug_goes_off_only_after_a_yes(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path)
    await leave_home(ha)
    await ha.simulate("sensor.kookplaat_vermogen", "1500")
    await ticks(hm, clock, 25)
    told = [n for n in ha.notifications if n[2].startswith("Kookplaat staat aan")]
    assert told and told[-1][2].endswith("Zal ik hem uitzetten?")
    asked = await _asked(hm)
    assert asked is not None and asked["actions"] == ["yes", "no"]
    assert asked["labels"] == {"yes": "Ja, zet uit", "no": "Nee, laat aan"}
    assert not [c for c in ha.calls if c[1] == HOB]  # it asks; it does not do it itself

    await _answer(hm, tmp_path, "yes")
    assert [c[0] for c in ha.calls if c[1] == HOB] == ["switch.turn_off"]
    assert await _asked(hm) is None
    await ha.simulate("sensor.kookplaat_vermogen", "0")
    await ticks(hm, clock, 10)
    assert await _asked(hm) is None
    assert not [c for c in ha.calls if c[1] == HOB and c[0] != "switch.turn_off"]  # never on
    await hm.stop()


async def test_leave_it_on_closes_the_question_while_it_stays_on(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path)
    await leave_home(ha)
    await ha.simulate("sensor.kookplaat_vermogen", "1500")
    await ticks(hm, clock, 25)
    await _answer(hm, tmp_path, "no")
    await ticks(hm, clock, 30)
    assert await _asked(hm) is None
    assert not [c for c in ha.calls if c[1] == HOB]
    # Off, and on again another time: then it asks again.
    await ha.simulate("sensor.kookplaat_vermogen", "0")
    await ticks(hm, clock, 2)
    await ha.simulate("sensor.kookplaat_vermogen", "1500")
    await ticks(hm, clock, 2)
    assert await _asked(hm) is not None
    await hm.stop()


async def test_a_child_lock_is_never_the_switch_only_the_notice(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path)
    await leave_home(ha)
    await ha.simulate("sensor.oven_vermogen", "2000")
    await ticks(hm, clock, 25)
    oven = await _asked(hm, "house:cooking:dev.oven")
    assert oven is not None and "yes" not in oven["actions"]
    assert [n for n in ha.notifications if n[2].startswith("Oven staat aan")][-1][2].endswith(
        "in Home Assistant."
    )
    assert not [c for c in ha.calls if c[1].startswith("switch.oven")]
    await hm.stop()


async def test_housemind_itself_never_switches_the_hob_on_or_off(tmp_path: Path) -> None:
    hm, clock, _ha = await _start(tmp_path)
    assert hm.gate is not None
    for action in ("switch.turn_on", "switch.turn_off"):
        for layer in (Layer.LEARNING, Layer.AI, Layer.HOUSE_RULE):
            decision = await hm.gate.submit(
                ActionRequest(layer, HOB, action, reason="test", created_at=clock.now())
            )
            assert decision.outcome is not Outcome.EXECUTED, (action, layer)
    on = await hm.gate.submit(
        ActionRequest(Layer.PERSON_ANSWER, HOB, "switch.turn_on", created_at=clock.now())
    )
    assert on.outcome is Outcome.DENIED  # a yes never switches it on either
    await hm.stop()
