"""'Waarom niet?' under a device says what HouseMind last did itself. Found in the real house
(27-10-2026): HouseMind switched the upstairs hall lights on and off, and 'Waarom niet?' under
those lamps said "Ik heb hier nog niets mee gedaan" – it only looked at what it left, and not
at the group the lamps are in. Each test fails without the fix."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.core.model import Source
from housemind.knowledge.catalog import Step
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome
from housemind.server.web import create_app
from tests.test_automations import HALL, admin_view, hall_light_on, start
from tests.test_vesta import _next

ADMIN = {"X-Remote-User-Id": "demo-user-1"}
NOTHING = "nog niets mee gedaan"


async def _why_not(hm: Any, tmp_path: Path, entity_id: str) -> str:
    """'Waarom niet?' tapped under a device on the page with devices."""
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ADMIN)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "why_not", "entity": entity_id})
        while True:
            message = await _next(ws, "data")
            if message.get("what") == "why":
                await ws.close()
                return str(message["data"]["text"])


async def test_after_it_switched_the_lamp_it_says_so(tmp_path: Path) -> None:
    """The automation in the hall is switched off; HouseMind turns the lamp on itself. Then
    'Waarom niet?' says what it did and when, not that it did nothing."""
    hm, clock, ha = await start(tmp_path)
    await admin_view(hm)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await hm.consider(hall_light_on(clock))  # the automation steers: only watching
    assert "Hall light at motion" in await _why_not(hm, tmp_path, "light.hall")
    await ha.simulate(HALL, "off", by=Source.PERSON)
    hm._last_request.clear()
    decision = await hm.consider(hall_light_on(clock))
    assert decision is not None and decision.executed
    said = await _why_not(hm, tmp_path, "light.hall")
    assert NOTHING not in said
    assert "Dat was om 19:30." in said
    await hm.stop()


async def test_a_lamp_in_a_group_it_switched(tmp_path: Path) -> None:
    """HouseMind switched the group the lamp is in; 'Waarom niet?' under the lamp says so."""
    hm, clock, _ha = await start(tmp_path, {})
    await admin_view(hm)
    hm.model.groups["light.hall_all"] = ("light.hall",)
    request = ActionRequest(
        Layer.REFLEX,
        "light.hall_all",
        "light.turn_on",
        {},
        reason="skill:light_on_entry",
        skill="light_on_entry",
        area_id="hall",
        created_at=clock.now(),
    )
    await hm._record(Decision(request, Outcome.EXECUTED, None, Step.AUTONOMOUS, (), clock.now()))
    said = await _why_not(hm, tmp_path, "light.hall")
    assert NOTHING not in said and "Dat was om 19:30." in said
    await hm.stop()
