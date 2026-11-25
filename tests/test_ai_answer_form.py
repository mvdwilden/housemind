"""The form of an AI's answer (E-453). The administrator's question (02-11-2026): "Als je iets
aan AI vraagt, en AI geeft iets terug aan HM, hoe weet HM nou wat er staat en hoe je het moet
interpreteren? ... Volgens mij stuurt AI vaak iets terug waar HM er niks mee kan." In the real
house four of thirty answers were lost: after five lookups the AI gave nothing for "Zet de
lichten in de garage aan", twice the answer about what each device is did not fit, and once the
stories per room did not. So: each question goes with the form of its answer, and a service
that can holds the AI to it; of an answer that was cut off, what was whole counts; and an AI
that gives no answer in its form is asked once more, plainly. Each test fails without the
build; what it checks is in its docstring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

from housemind.ai.providers import (
    AIResult,
    AnthropicProvider,
    GeminiProvider,
    OpenAICompatibleProvider,
)
from housemind.ai.service import NUDGE
from housemind.core.clock import SimulatedClock
from housemind.ha.fake import FakeHomeAssistant
from tests.test_ai import FakeAI, enable, start
from tests.test_entity_functions_ai import ReadingAI, _many_relays
from tests.test_house_knowledge_derived import _start
from tests.test_vesta_smarter import LookingAI, _bathroom

FACTS = {
    "switch.oven_child_lock": {"domain": "switch", "name": "Child Lock"},
    "switch.garage_output": {"domain": "switch", "name": "Output 1"},
}


def _data(text: str) -> dict[str, Any]:
    return dict(json.loads(text.removeprefix("<data>\n").removesuffix("\n</data>")))


def _keys(form: Any) -> set[str]:
    """Every key anywhere in a form."""
    if isinstance(form, dict):
        return set(form) | {k for v in form.values() for k in _keys(v)}
    if isinstance(form, list):
        return {k for v in form for k in _keys(v)}
    return set()


def _answer(labels: list[str]) -> str:
    return json.dumps(
        {
            "functions": {
                label: {"function": "setting", "certainty": 0.9, "why": "Een instelling."}
                for label in labels
            }
        }
    )


async def test_each_question_goes_with_the_form_of_its_answer(tmp_path: Path) -> None:
    """The question what each device is goes to Claude, an OpenAI-compatible service and
    Gemini with its form: exactly the labels that were sent, each with one of the functions
    from the list – and no limit a service does not take. A service that does not take the
    form gets the same question at once without it, and from then on, and its answer counts."""
    hm, _, _ = await start(tmp_path)
    seen: dict[str, list[dict[str, Any]]] = {"a": [], "o": [], "g": []}
    refuse = {"on": False}

    async def anthropic(request: web.Request) -> web.Response:
        body = await request.json()
        seen["a"].append(body)
        if refuse["on"] and "output_config" in body:
            return web.json_response({"error": "unknown field"}, status=400)
        labels = list(_data(body["messages"][0]["content"])["entities"])
        reply = {"type": "text", "text": _answer(labels)}
        return web.json_response({"content": [reply], "stop_reason": "end_turn"})

    async def openai(request: web.Request) -> web.Response:
        body = await request.json()
        seen["o"].append(body)
        labels = list(_data(body["messages"][1]["content"])["entities"])
        return web.json_response({"choices": [{"message": {"content": _answer(labels)}}]})

    async def gemini(request: web.Request) -> web.Response:
        body = await request.json()
        seen["g"].append(body)
        labels = list(_data(body["contents"][0]["parts"][0]["text"])["entities"])
        part = {"text": _answer(labels)}
        return web.json_response({"candidates": [{"content": {"parts": [part]}}]})

    app = web.Application()
    app.router.add_post("/a/messages", anthropic)
    app.router.add_post("/o/chat/completions", openai)
    app.router.add_post("/g/models/{model}", gemini)
    server = TestServer(app)
    await server.start_server()
    providers = {
        "a": AnthropicProvider("k", str(server.make_url("/a"))),
        "o": OpenAICompatibleProvider(str(server.make_url("/o"))),
        "g": GeminiProvider("k", str(server.make_url("/g"))),
    }
    forms = {}
    for key, provider in providers.items():
        await enable(hm, FakeAI([]))
        hm.ai.provider = provider
        found = await hm.ai.suggest_functions(FACTS, "nl")
        assert found.reason is None and set(found.answers) == set(FACTS), found
        body = seen[key][-1]
        if key == "a":
            forms[key] = body["output_config"]["format"]["schema"]
        elif key == "o":
            assert body["response_format"]["json_schema"]["strict"] is True
            forms[key] = body["response_format"]["json_schema"]["schema"]
        else:
            assert body["generationConfig"]["responseMimeType"] == "application/json"
            forms[key] = body["generationConfig"]["responseJsonSchema"]
    form = forms["a"]
    assert forms["o"] == forms["g"] == form
    per_label = form["properties"]["functions"]
    assert per_label["required"] == ["entity_1", "entity_2"]  # the labels sent, and no other
    assert per_label["additionalProperties"] is False and form["additionalProperties"] is False
    function = per_label["properties"]["entity_1"]["properties"]["function"]
    assert {"lamp", "setting", "unknown"} <= set(function["enum"])
    assert not _keys(form) & {"minimum", "maximum", "maxLength", "pattern"}

    # A service that does not take the form: the same question without it, and from then on.
    refuse["on"] = True
    hm.ai.provider = providers["a"]
    before = len(seen["a"])
    found = await hm.ai.suggest_functions(FACTS, "nl")
    assert found.reason is None and set(found.answers) == set(FACTS)
    asked = seen["a"][before:]
    assert ["output_config" in body for body in asked] == [True, False]
    found = await hm.ai.suggest_functions(FACTS, "nl")
    assert found.reason is None and "output_config" not in seen["a"][-1]
    assert len(seen["a"]) == before + 3
    for provider in providers.values():
        await provider.close()
    await server.close()
    await hm.stop()


class CutOnceAI(ReadingAI):
    """An AI whose first answer about the devices does not fit: three answers are whole, the
    fourth is cut off halfway."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[list[str]] = []

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        data = _data(user)
        if "entities" not in data:
            return await super().complete(system, user, model, max_tokens)
        self.asked.append([facts.get("name", "") for facts in data["entities"].values()])
        whole = json.loads(_answer(list(data["entities"])))["functions"]
        if len(self.asked) > 1:
            return AIResult(ok=True, text=json.dumps({"functions": whole}), output_tokens=300)
        first = dict(list(whole.items())[:3])
        text = json.dumps({"functions": first})[:-2] + ', "entity_4": {"function": "sett'
        return AIResult(ok=False, text=text, error="cut_off", output_tokens=5000)


async def test_what_was_whole_of_an_answer_that_did_not_fit_counts(tmp_path: Path) -> None:
    """The answer about twenty devices ran out of room after three and a half. Those three
    count – the AI said what they are –, and only the others are asked again, half as many at
    a time."""

    async def setup(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
        await _many_relays(ha, clock)

    hm, _clock, _ha = await _start(tmp_path, setup=setup)
    fake = CutOnceAI()
    await enable(hm, fake)
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    first, second = fake.asked[0], fake.asked[1]
    assert len(first) == 20 and len(second) == 10
    assert not set(first[:3]) & set(second)  # what was whole is not asked again
    assert set(second) <= set(first[3:])  # the others, ten at a time
    said = {f.subject for f in hm.knowledge.of("entity", "function") if f.source == "ai"}
    names = {e.entity_id: e.original_name for e in hm.model.entities.values()}
    assert set(first[:3]) <= {names[s] for s in said}
    await hm.stop()


async def test_the_stories_per_room_that_were_whole_are_used(tmp_path: Path) -> None:
    """The stories per room ran out of room in the real house, and every room lost its story.
    Now the rooms whose story was whole get it."""
    hm, _, _ = await start(tmp_path)
    kitchen, bathroom = hm.ai.labels.area("kitchen"), hm.ai.labels.area("bathroom")
    assert kitchen and bathroom
    text = (
        f'{{"rooms": {{"{kitchen}": "In {kitchen} doe ik om 07:00 het licht aan.", '
        f'"{bathroom}": "In {bathroom} doe ik'
    )
    await enable(hm, FakeAI([AIResult(ok=False, text=text, error="cut_off")]))
    rooms = {
        kitchen: [f"In {kitchen} doe ik het licht om 07:00 aan."],
        bathroom: [f"In {bathroom} doe ik het licht uit na 2 minuten."],
    }
    names = {"area:kitchen": "de keuken", "area:bathroom": "de badkamer"}
    found = await hm.ai.habit_words(rooms, "nl", names)
    assert set(found) == {kitchen}
    assert found[kitchen][1] == "In de keuken doe ik om 07:00 het licht aan."
    await hm.stop()


async def test_after_five_lookups_an_empty_answer_is_asked_again(tmp_path: Path) -> None:
    """ "Zet de lichten in de garage aan": the AI looked things up five times and then gave
    nothing, and Vesta said she did not understand the AI. Now, done looking things up, it is
    told so – "the answer now, in its form" – and an empty answer or one of only words is
    asked once more, plainly; that answer counts."""
    hm, _clock, _ha = await _bathroom(tmp_path)
    room = hm.ai.labels.area("bathroom")
    look = {"calls": [{"name": "room", "args": {"room": room}}]}
    fake = LookingAI([look, look, look, look, look, "", {"answer": "Het licht is aan."}])
    await enable(hm, fake)
    answer = await hm.ask("Zet de lichten in de badkamer aan.", "nl", "demo-user-1")
    assert answer["ok"] and answer["message"] == "Het licht is aan.", answer
    assert [r["final"] for r in fake.rounds] == [False] * 5 + [True, True]
    told = fake.rounds[5]["turns"][-1]
    assert told.role == "tool" and told.text == NUDGE  # done looking: the answer now
    again = fake.rounds[6]["turns"][-1]
    assert NUDGE in again.text

    # Only words, no answer in its form: asked once more, plainly.
    fake = LookingAI(["De lampen zijn uit.", {"answer": "De lampen zijn uit."}])
    hm.ai.provider = fake
    answer = await hm.ask("Staan de lampen in de badkamer aan?", "nl", "demo-user-1")
    assert answer["ok"] and answer["message"] == "De lampen zijn uit.", answer
    assert fake.rounds[1]["turns"][-1].role == "user"
    assert fake.rounds[1]["turns"][-1].text == NUDGE
    await hm.stop()
