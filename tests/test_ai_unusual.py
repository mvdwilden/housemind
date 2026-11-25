"""Something unusual as a question (E-416, design 50.3): the core itself sees that a lamp has
been on for hours in a room where nobody has been, and Vesta asks "Zal ik hem uitzetten?".
Only a person's yes switches it off; the core goes on as it was. With AI, the AI thinks of a
possible reason and words the question – with the hours of the core. Each test fails without
the change; what it checks is its docstring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.ai.providers import AIResult
from housemind.core.model import Source
from housemind.knowledge.catalog import Step
from housemind.server import views
from housemind.server.web import create_app
from tests.test_ai import FakeAI, enable, start
from tests.test_start_over import _hello, _next

LAMP = "light.kitchen"


async def _hours(hm: Any, clock: Any, hours: float) -> None:
    """Time passes as in the house: a round every few minutes."""
    for _ in range(int(hours * 12)):
        await clock.advance(300)
        await hm.tick()
    hm._last_review = None
    await hm.tick()
    await hm.ai_jobs.idle()


async def _house(tmp_path: Path) -> Any:
    """A house that has not learned yet to switch off the kitchen light when the room is
    empty: nothing it would propose about that lamp itself."""
    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_off_leave", "kitchen")] = Step.OBSERVE
    return hm, clock, ha


async def _lamp_on_for(hm: Any, clock: Any, ha: Any, hours: float) -> None:
    """Someone cooked, switched the kitchen light on and left; nobody came back."""
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    await ha.simulate(LAMP, "on", by=Source.PHYSICAL)
    await ha.simulate("binary_sensor.kitchen_motion", "off")
    await _hours(hm, clock, hours)


async def _asked(hm: Any) -> dict[str, Any] | None:
    row = next((n for n in await hm.notifications() if n["topic"].startswith("unusual:")), None)
    return None if row is None else views.notice_item(hm, "nl", row, False, "demo-user-1")


async def _answer(hm: Any, tmp_path: Path, action: str) -> None:
    asked = await _asked(hm)
    assert asked is not None
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client)
        await _next(ws, "snapshot")
        await ws.send_json({"type": "notification", "id": asked["id"], "action": action})
        await _next(ws, "snapshot")
        await ws.close()


async def test_a_lamp_on_for_hours_in_an_empty_room_becomes_a_question(tmp_path: Path) -> None:
    """The kitchen light has been on for four hours and nobody has been in the kitchen since:
    HouseMind asks whether it should switch it off – it does not do it by itself. After two
    hours it was still nothing to ask about."""
    hm, clock, ha = await _house(tmp_path)
    await _lamp_on_for(hm, clock, ha, 2)
    assert await _asked(hm) is None
    await _hours(hm, clock, 3)
    asked = await _asked(hm)
    assert asked is not None
    assert "4 uur" in asked["text"] and asked["text"].endswith("Zal ik hem uitzetten?")
    assert asked["labels"] == {"yes": "Ja, zet uit", "no": "Nee, laat aan"}
    assert ha.states[LAMP].state == "on"  # the core goes on as it was
    # Vesta asks it too, within the person's question budget (E-294), when there is room today.
    hm.router._sent_today.clear()
    asks = await hm.vesta_asks("demo-user-1", 3, True)
    assert [a["kind"] for a in asks if a["text_key"].startswith("notify.unusual")] == ["unusual"]

    await hm.stop()


async def test_yes_switches_it_off_and_no_leaves_it_alone(tmp_path: Path) -> None:
    """'Ja, zet uit' switches the lamp off through the safety gate, as a person's answer.
    'Nee, laat aan' leaves it on, and HouseMind does not ask about that lamp again for a month."""
    hm, clock, ha = await _house(tmp_path)
    await _lamp_on_for(hm, clock, ha, 5)
    await _answer(hm, tmp_path, "yes")
    assert ha.states[LAMP].state == "off"
    assert await _asked(hm) is None

    await _lamp_on_for(hm, clock, ha, 5)
    assert await _asked(hm) is not None
    await _answer(hm, tmp_path, "no")
    assert ha.states[LAMP].state == "on" and await _asked(hm) is None
    await _hours(hm, clock, 24)
    assert await _asked(hm) is None
    await hm.stop()


class Reasoner(FakeAI):
    """An AI that thinks of a reason for what the core saw."""

    def __init__(self, reply: Any) -> None:
        super().__init__([])
        self.reply = reply
        self.asked: list[tuple[str, dict[str, Any]]] = []

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        self.sent.append((system, user, model))
        if "out of the ordinary" not in system:
            return AIResult(True, "{}", 10, 2)
        data = json.loads(user.split("\n", 1)[1].rsplit("\n", 1)[0])
        self.asked.append((system, data))
        reply = self.reply(data) if callable(self.reply) else self.reply
        return AIResult(True, json.dumps(reply), 300, 60)


async def test_with_ai_a_possible_reason_and_the_hours_of_the_core(tmp_path: Path) -> None:
    """With AI the question comes with a possible reason. The AI gets labels, never names, and
    the hours HouseMind counted; Vesta says its words as they are, without asking an AI again."""
    hm, clock, ha = await _house(tmp_path)

    def reason(data: dict[str, Any]) -> dict[str, str]:
        return {
            "text": f"Het licht in {data['room']} brandt al {data['hours']} uur; misschien "
            "vergeten na het koken. Zal ik het uitzetten?"
        }

    ai = Reasoner(reason)
    await enable(hm, ai)
    await _lamp_on_for(hm, clock, ha, 5)
    assert len(ai.asked) == 1
    _system, data = ai.asked[0]
    assert data["hours"] == 4 and "Keuken" not in json.dumps(data, ensure_ascii=False)
    asked = await _asked(hm)
    assert asked is not None
    assert asked["text"].startswith("Het licht in de keuken brandt al 4 uur; misschien")
    before = len(ai.sent)
    await hm.vesta_line("demo-user-1", 2, True, "nl")
    assert len(ai.sent) == before  # her line is the AI's question as it is
    await hm.stop()


async def test_an_ai_that_changes_the_hours_is_not_used(tmp_path: Path) -> None:
    """An AI that makes four hours into eight is not believed: the question comes in HouseMind's
    own words."""
    hm, clock, ha = await _house(tmp_path)
    ai = Reasoner({"text": "Het licht brandt al 8 uur. Zal ik het uitzetten?"})
    await enable(hm, ai)
    await _lamp_on_for(hm, clock, ha, 5)
    asked = await _asked(hm)
    assert asked is not None and "8 uur" not in asked["text"] and "4 uur" in asked["text"]
    await hm.stop()


async def test_no_second_question_when_housemind_already_proposes_it(tmp_path: Path) -> None:
    """When HouseMind has learned to propose switching that lamp off when the room is empty, it
    already asks that – the unusual lamp never becomes a second question about it at the same
    time: one asked before goes away when the proposal comes."""
    hm, clock, ha = await start(tmp_path)
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    await ha.simulate(LAMP, "on", by=Source.PHYSICAL)
    await ha.simulate("binary_sensor.kitchen_motion", "off")
    proposed_seen = asked_seen = False
    for _ in range(12 * 6):
        await clock.advance(300)
        await hm.tick()
        proposed = [p for p in await hm.proposals.open(hm.clock.now()) if p.entity_id == LAMP]
        asked = await _asked(hm)
        assert not (proposed and asked), "two questions about one lamp"
        proposed_seen = proposed_seen or bool(proposed)
        asked_seen = asked_seen or asked is not None
    assert proposed_seen and asked_seen
    await hm.stop()
