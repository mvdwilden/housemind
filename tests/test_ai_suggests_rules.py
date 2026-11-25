"""House rules the AI suggests (E-417, design 50.3): once a week, from what HouseMind learned,
the house rules there are and what the residents refused, the AI may suggest at most three house
rules. HouseMind checks each – only 'never' or a maximum temperature, only rooms and devices it
knows, only times and values from what it learned, never one that is there already or was
refused – and an administrator decides. Each test fails without the change; what it checks is
its docstring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.ai.providers import AIResult
from housemind.brain.rules import HouseRule
from housemind.server import views
from housemind.server.web import create_app
from tests.test_ai import FakeAI, enable
from tests.test_ai_learns_in_words import _learned, _quarter
from tests.test_start_over import _hello, _next


class Suggester(FakeAI):
    """An AI that suggests house rules."""

    def __init__(self, reply: Any) -> None:
        super().__init__([])
        self.reply = reply
        self.asked: list[dict[str, Any]] = []

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        self.sent.append((system, user, model))
        if "suggest house rules" not in system:
            return AIResult(True, "{}", 10, 2)
        data = json.loads(user.split("\n", 1)[1].rsplit("\n", 1)[0])
        self.asked.append(data)
        reply = self.reply(data) if callable(self.reply) else self.reply
        return AIResult(True, json.dumps(reply), 800, 200)


def _never(scope: str, category: str, why: str, **more: Any) -> dict[str, Any]:
    rule = {"scope_type": "area", "scope": scope, "effect": "never", "category": category}
    return {**rule, "time_from": None, "time_to": None, "days": None, "why": why, **more}


async def _suggested(hm: Any, admin: bool = True, user: str = "demo-user-1") -> list[Any]:
    rows = [n for n in await hm.notifications() if n["topic"].startswith("rule_suggest:")]
    return [views.notice_item(hm, "nl", row, admin, user) for row in rows]


async def _answer(hm: Any, tmp_path: Path, notice_id: int, action: str) -> None:
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client)
        await _next(ws, "snapshot")
        await ws.send_json({"type": "notification", "id": notice_id, "action": action})
        await _next(ws, "snapshot")
        await ws.close()


async def test_once_a_week_the_ai_may_suggest_a_house_rule(tmp_path: Path) -> None:
    """With AI on, once a week: the AI gets what HouseMind learned per room – labels, never the
    names of the house – and suggests a house rule with why. An administrator gets the question;
    only after their yes it is a house rule. Another resident does not see it."""
    hm = await _learned(tmp_path)
    bedroom = hm.ai.labels.area("bedroom")
    ai = Suggester(
        {"suggestions": [_never(bedroom, "open", f"In {bedroom} doe je dat altijd zelf.")]}
    )
    await enable(hm, ai)
    before = len(hm.rules.rules)
    await _quarter(hm)
    assert len(ai.asked) == 1
    sent = json.dumps(ai.asked[0], ensure_ascii=False)
    assert ai.asked[0]["learned_per_room"]
    assert "Slaapkamer" not in sent and "Keuken" not in sent and "Emma" not in sent
    [asked] = await _suggested(hm)
    assert asked["actions"] == ["yes", "no"]
    assert asked["text"].startswith("Voorstel voor een huisregel:")
    assert "Waarom: In de slaapkamer doe je dat altijd zelf." in asked["text"]
    assert await _suggested(hm, admin=False, user="demo-user-2") == [None]
    assert len(hm.rules.rules) == before  # not yet: a person decides
    await _answer(hm, tmp_path, asked["id"], "yes")
    made = [r for r in hm.rules.rules if r.scope_id == "bedroom" and r.category == "open"]
    assert len(made) == 1 and made[0].effect == "never"
    assert await _suggested(hm) == []
    await _quarter(hm)
    assert len(ai.asked) == 1  # once a week
    await hm.stop()


async def test_only_rules_housemind_can_check_and_never_what_was_refused(tmp_path: Path) -> None:
    """What the AI suggests is only asked when HouseMind can check it: not 'always' (that would
    make HouseMind act), not a room the house does not have, not a time HouseMind never
    learned, not a rule that is there already. And after 'Nee' it is not suggested again: the
    AI hears what was refused."""
    hm = await _learned(tmp_path)
    bedroom, hall = hm.ai.labels.area("bedroom"), hm.ai.labels.area("hall")
    await hm.save_rule(HouseRule("area", "bedroom", "never", "open"), "demo-user-1")
    good = _never(hall, "on", f"In {hall} gaat het licht vanzelf aan.")
    ai = Suggester(
        {
            "suggestions": [
                _never(bedroom, "open", "Er is al zo'n regel."),
                {**_never(hall, "off", "Altijd uit."), "effect": "always"},
                _never("attic_9", "on", "Een zolder."),
                _never(bedroom, "close", "Pas na 7:15.", time_from="07:15"),
                good,
            ]
        }
    )
    await enable(hm, ai)
    await _quarter(hm)
    suggested = await _suggested(hm)
    assert len(suggested) == 1 and "gang" in suggested[0]["text"].lower()
    await _answer(hm, tmp_path, suggested[0]["id"], "no")
    assert await _suggested(hm) == []
    assert not [r for r in hm.rules.rules if r.scope_id == "hall"]

    await hm.clock.advance(8 * 24 * 3600)
    await _quarter(hm)
    assert len(ai.asked) == 2
    assert any("hall" in text for text in ai.asked[1]["refused"])  # it hears what was refused
    assert ai.asked[1]["house_rules"]  # and the rules there are
    assert await _suggested(hm) == []  # the same is not asked again
    await hm.stop()
