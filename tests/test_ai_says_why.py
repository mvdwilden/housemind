"""What HouseMind asks an AI, and why (E-169, E-222, E-49) – the administrator's question on
30-10-2026: "Does HouseMind tell the AI why it asks and what answer it expects? In the logbook I
often see only a dump of switches or device names, and then the AI improves the text – that is
not the point, is it?" Each test fails without the change; what it checks is its docstring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from aiohttp import web
from aiohttp.test_utils import TestServer

from housemind.ai.providers import AIResult, AnthropicProvider, OpenAICompatibleProvider
from housemind.ai.service import TASKS
from housemind.brain.notify import Kind
from housemind.server.logbook import logbook
from tests.test_ai import FakeAI
from tests.test_screens import viewer
from tests.test_vesta import _raise, _with_ai

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("nl", "en", "de", "fr")


async def test_the_logbook_shows_why_it_asked_and_what_answer_it_wanted(
    tmp_path: Path,
) -> None:
    """Every question to an AI goes with an instruction – why HouseMind asks and what answer it
    wants – but the log kept only the data, so the administrator saw a dump of names. Now the
    line in Logboek on Expert – the one place for it (QA 3) – shows that instruction too, word
    for word as the AI got it."""
    hm, fake = await _with_ai(tmp_path, [])
    await _raise(hm, Kind.INFO, "done:w", "notify.house.appliance_done", {"device": "Wasmachine"})
    fake.replies.append({"text": "De wasmachine is klaar, geen haast."})
    await hm.vesta_line("demo-user-9", 2, False, "nl")
    system = fake.sent[-1][0]
    assert "Say only the messages in the data" in system
    admin = await viewer(hm, 4)
    log = await logbook(hm, admin, "demo-user-1", "nl")
    line = next(i for i in log["items"] if i["type"] == "ai")
    assert line["instruction"] == system
    await hm.stop()


def test_every_kind_of_question_has_a_name_in_every_language() -> None:
    """The logbook showed 'ui.logbook.ai_purpose.automations': reading the house (switches,
    automations, doubts) had no name. Every kind of question to an AI has one, in every
    language."""
    purposes = {p for ps in TASKS.values() for p in ps} | {"forward"}
    for lang in LANGUAGES:
        texts = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        named = texts["ui"]["logbook"]["ai_purpose"]
        assert purposes <= set(named), (lang, sorted(purposes - set(named)))


async def test_an_important_message_is_said_exactly_as_housemind_says_it(
    tmp_path: Path,
) -> None:
    """A safety sensor that stops answering is important. The AI got HouseMind's finished
    sentence and only said it again in other words – every hour anew, at a cost. Now Vesta says
    an important message in HouseMind's own words, and the AI is not asked. Her own words say
    it is a safety sensor, as the message does – not 'is it still plugged in?'."""
    hm, fake = await _with_ai(tmp_path, [{"text": "Let op, de rookmelder reageert niet meer."}])
    await _raise(
        hm,
        Kind.IMPORTANT,
        "maintenance:offline:smoke",
        "notify.maintenance.offline_safety",
        {"device": "Rookmelder"},
    )
    line = await hm.vesta_line("demo-user-9", 2, False, "nl")
    assert not fake.sent
    assert "Rookmelder" in line.text and "veiligheidssensor" in line.text
    await hm.stop()


async def test_the_same_message_is_not_sent_to_the_ai_twice(tmp_path: Path) -> None:
    """The same message for two people who hear her the same way: the AI is asked once, and
    both hear the words it gave."""
    hm, fake = await _with_ai(tmp_path, [{"text": "De wasmachine is klaar, geen haast."}])
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-8", "Resident 8", 2, json.dumps({}), hm.clock.now().isoformat()),
    )
    await _raise(hm, Kind.INFO, "done:w", "notify.house.appliance_done", {"device": "Wasmachine"})
    one = await hm.vesta_line("demo-user-9", 2, False, "nl")
    two = await hm.vesta_line("demo-user-8", 2, False, "nl")
    assert len(fake.sent) == 1
    assert one.text == two.text == "De wasmachine is klaar, geen haast."
    await hm.stop()


class _Room(FakeAI):
    """An AI that notes how much room HouseMind gives it for an answer."""

    def __init__(self, replies: list[Any]) -> None:
        super().__init__(replies)
        self.room: list[int] = []

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        self.room.append(max_tokens)
        return await super().complete(system, user, model, max_tokens)


async def test_there_is_room_to_think_before_the_answer(tmp_path: Path) -> None:
    """The newest models think a little before they answer, and that counts towards the length
    of the answer: with room for 250 tokens the answer at 13:06 was cut off mid-sentence and
    could not be used. Now there is room for the thinking as well."""
    hm, _fake = await _with_ai(tmp_path, [])
    room = _Room([{"text": "De wasmachine is klaar."}])
    hm.ai.provider = room
    await _raise(hm, Kind.INFO, "done:w", "notify.house.appliance_done", {"device": "Wasmachine"})
    await hm.vesta_line("demo-user-9", 2, False, "nl")
    assert room.room and room.room[0] >= 2000
    await hm.stop()


async def _server(path: str, payload: dict[str, Any]) -> TestServer:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response(payload)

    app = web.Application()
    app.router.add_post(path, handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def test_an_answer_that_was_cut_off_is_not_taken_for_an_answer() -> None:
    """An answer cut off because it ran out of room was kept as if it were whole. Now it counts
    as cut off – Vesta uses her own words, and the log says why."""
    claude = await _server(
        "/v1/messages",
        {
            "content": [{"type": "text", "text": '{"text": "Dit is een veiligheidssensor, dus'}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 300, "output_tokens": 250},
        },
    )
    provider = AnthropicProvider("k", str(claude.make_url("/v1")))
    result = await provider.complete("sys", "user", "claude-haiku-5-5", 250)
    assert not result.ok and result.error == "cut_off" and result.output_tokens == 250
    await provider.close()
    await claude.close()
    other = await _server(
        "/v1/chat/completions",
        {
            "choices": [{"message": {"content": '{"text": "half'}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 250},
        },
    )
    provider = OpenAICompatibleProvider(str(other.make_url("/v1")), "k")
    result = await provider.complete("sys", "user", "some-model", 250)
    assert not result.ok and result.error == "cut_off"
    await provider.close()
    await other.close()


async def test_the_cheapest_model_costs_what_it_costs(tmp_path: Path) -> None:
    """Claude Haiku 5.5 costs $0.10 per million tokens in and $0.50 out, and Claude Sonnet 5.5
    $2 and $10; HouseMind counted ten times the price of the first, so the logbook and the
    budget showed far more than was spent."""
    hm, _fake = await _with_ai(tmp_path, [])
    hm.ai.config.price_in = hm.ai.config.price_out = None
    assert hm.ai._price("claude-haiku-5-5") == (0.10, 0.50)
    assert hm.ai._price("claude-sonnet-5-5") == (2.0, 10.0)
    await hm.stop()
