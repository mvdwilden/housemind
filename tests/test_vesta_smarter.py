"""Vesta slimmer (design 50.4, B-351 – B-354), from the user's side: with an AI she looks things
up in the house (E-419), changes a house rule in your own words only after your yes (E-420),
knows what the conversation was about (E-421), and her answer appears as it is written, with
her own answer after 20 seconds (E-159). The AI never decides and never acts: what it suggests
is a button through the safety gate (B-17). Each test fails without the build; what it checks
is in its docstring."""

from __future__ import annotations

import asyncio
import json
import time as timer
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from housemind.ai import service
from housemind.ai.providers import (
    AIResult,
    AnthropicProvider,
    GeminiProvider,
    OpenAICompatibleProvider,
)
from housemind.brain.rules import parse_rule, read_back
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, Source, State
from housemind.demo.houses import load_demo_house
from housemind.ha.adapter import HAUser
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer
from housemind.server.web import create_app
from tests.test_ai import FakeAI, enable
from tests.test_vesta import _ask, _house, _next

ROOT = Path(__file__).resolve().parents[1]
USER = {"X-Remote-User-Id": "demo-user-1"}
LOCAL = ZoneInfo("Europe/Amsterdam")
MONDAY = datetime(2026, 10, 19, 10, 0, tzinfo=LOCAL)
WARMTH = "sensor.bathroom_temperature"


class LookingAI(FakeAI):
    """An AI that can look things up and writes its answer piece by piece (E-419, E-159). A
    reply ``{"calls": [...]}`` asks for lookups; anything else is the answer."""

    tools = True
    streams = True

    def __init__(self, replies: list[Any], pieces: int = 6, pause: float = 0.0) -> None:
        super().__init__(replies)
        self.pieces, self.pause = pieces, pause
        self.rounds: list[dict[str, Any]] = []

    async def converse(
        self,
        system: str,
        turns: Any,
        model: str,
        max_tokens: int,
        tools: Any = (),
        final: bool = False,
        on_text: Any = None,
    ) -> AIResult:
        self.rounds.append(
            {
                "turns": list(turns),
                "tools": [t.name for t in tools],
                "final": final,
                "streamed": on_text is not None,
            }
        )
        self.sent.append((system, turns[0].text, model))
        reply = self.replies.pop(0) if self.replies else "{}"
        if isinstance(reply, AIResult):
            return reply
        if isinstance(reply, dict) and "calls" in reply:
            from housemind.ai.providers import ToolCall

            calls = tuple(
                ToolCall(f"c{len(self.rounds)}_{i}", c["name"], c.get("args", {}))
                for i, c in enumerate(reply["calls"])
            )
            return AIResult(ok=True, input_tokens=400, output_tokens=40, calls=calls)
        text = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        if on_text is not None:
            size = max(1, len(text) // self.pieces)
            for i in range(0, len(text), size):
                await on_text(text[i : i + size])
                await asyncio.sleep(self.pause)
        return AIResult(ok=True, text=text, input_tokens=1000, output_tokens=500)


def _found(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the lookups gave, as it went to the AI: the data in each tool result (the last
    round has every turn before it)."""
    out = []
    for turn in (t for t in rounds[-1]["turns"] if t.role == "tool"):
        for _call, text in turn.results:
            out.append(json.loads(text.removeprefix("<data>\n").removesuffix("\n</data>")))
    return out


async def _bathroom(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, Any]:
    """The demo family with a thermometer in the bathroom, a name in a room and on a lamp, and
    what the bathroom's temperature did since Sunday – in Home Assistant's recorder."""
    clock = SimulatedClock(MONDAY.astimezone(UTC))
    ha = load_demo_house("family_home", clock, "nl").ha
    ha.users_list[0] = HAUser("demo-user-1", "Emma", is_admin=True)
    reading = ha.model.entities["light.living_reading"]
    ha.model.entities["light.living_reading"] = replace(reading, name="Leeslamp van Emma")
    ha.model.entities[WARMTH] = Entity(
        WARMTH, "r-bt", "Badkamer temperatuur", area_id="bathroom", device_class="temperature"
    )
    unit = {"unit_of_measurement": "°C"}
    ha.states[WARMTH] = State(WARMTH, "20.5", unit, clock.now())
    sunday = MONDAY - timedelta(days=1)
    for hour, value in ((0, "18.0"), (6, "16.5"), (9, "17.0"), (18, "19.5")):
        ha.record(WARMTH, value, sunday.replace(hour=hour).astimezone(UTC), unit)
    ha.record(WARMTH, "20.5", MONDAY.replace(hour=7).astimezone(UTC), unit)
    lamp = "light.bathroom"
    ha.record(lamp, "on", sunday.replace(hour=7).astimezone(UTC), by=Source.PERSON)
    ha.record(lamp, "off", sunday.replace(hour=8, minute=30).astimezone(UTC), by=Source.PERSON)
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-1", "Emma", 3, clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, clock, ha


# -- Vesta zoekt het op (E-419, B-351) ---------------------------------------------------


async def test_vesta_looks_it_up_and_only_labels_leave_the_house(tmp_path: Path) -> None:
    """ "Waarom was het gisteren koud in de badkamer?" could only be answered from a snapshot
    of now. Now the AI may look up the bathroom's last days and what HouseMind did there: the
    summary per day (lowest 16.5 °C yesterday) and the decision trace reach it – with labels,
    never the names of rooms, devices or people – each round is in the AI log, and the answer
    comes back in plain names."""
    hm, clock, _ha = await _bathroom(tmp_path)
    assert hm.gate is not None
    request = ActionRequest(
        Layer.LEARNING,
        "light.bathroom",
        "light.turn_on",
        {},
        skill="light_on_entry",
        area_id="bathroom",
        created_at=clock.now(),
    )
    await hm._record(await hm.gate.submit(request))
    room = hm.ai.labels.area("bathroom")
    fake = LookingAI(
        [
            {
                "calls": [
                    {"name": "history", "args": {"about": room, "days": 2}},
                    {"name": "why", "args": {"about": room}},
                ]
            },
            {"answer": f"Gisteren was het in {room} 's ochtends maar 16,5 graden."},
        ]
    )
    await enable(hm, fake)
    answer = await hm.ask("Waarom was het gisteren koud in de badkamer?", "nl", "demo-user-1")
    assert answer["ok"], answer
    assert answer["message"] == "Gisteren was het in de badkamer 's ochtends maar 16,5 graden."
    first = fake.rounds[0]
    assert first["tools"] == ["room", "why", "history", "plan", "house_rules", "energy"]
    assert "house" in json.loads(first["turns"][0].text.split("\n")[1])  # the snapshot too
    history, why = _found(fake.rounds)
    thermometer = hm.ai.labels.to_label[WARMTH]
    yesterday = next(d for d in history["devices"][thermometer] if d["days_ago"] == 1)
    assert yesterday["lowest"] == 16.5 and yesterday["highest"] == 19.5
    lamp = hm.ai.labels.to_label["light.bathroom"]
    sunday = next(d for d in history["devices"][lamp] if d["days_ago"] == 1)
    assert sunday["went_on"] == 1 and sunday["hours_on"] == 1.5 and sunday["by"] == {"person": 1}
    assert why["decisions"] and why["decisions"][0]["device"] == lamp
    sent = json.dumps([r["turns"] for r in fake.rounds], default=str, ensure_ascii=False)
    for name in ("Badkamer", "badkamer", "Emma", "Leeslamp", "Woonkamer", "temperatuur"):
        assert name not in sent, name
    log = await hm.ai.log()
    assert [row["purpose"] for row in log] == ["ask", "ask"]  # each round, on level 4
    assert '"lookups"' in log[0]["sent"] and thermometer in log[0]["sent"]
    assert '"lookup": "history"' in log[1]["received"]  # what the AI asked for, also shown
    await hm.stop()


async def test_at_most_five_lookups_each_within_its_time(tmp_path: Path, monkeypatch: Any) -> None:
    """An AI that keeps looking things up gets at most five lookups for one question; then
    the home asks for the answer. A lookup that takes too long says so, and the AI goes on."""
    hm, _clock, ha = await _bathroom(tmp_path)
    monkeypatch.setattr(service, "LOOKUP_TIMEOUT", 0.05)

    async def slow(*_args: Any) -> list[Any]:
        await asyncio.sleep(5)
        return []

    ha.history = slow
    room = hm.ai.labels.area("bathroom")
    three = {"calls": [{"name": "room", "args": {"room": room}}] * 3}
    fake = LookingAI(
        [
            {"calls": [{"name": "history", "args": {"about": room}}]},
            three,
            three,
            {"answer": "Het is rustig in de badkamer."},
        ]
    )
    await enable(hm, fake)
    answer = await hm.ask("Hoe gaat het in de badkamer?", "nl", "demo-user-1")
    assert answer["ok"]
    found = _found(fake.rounds)
    assert found[0] == {"error": "timeout"}
    assert len([f for f in found if "error" not in f]) == 4  # 1 timed out + 4: five in all
    assert found[-1] == {"error": "at most 5 lookups for one question"}
    assert [r["final"] for r in fake.rounds] == [False, False, False, True]
    await hm.stop()


async def test_an_ai_without_lookups_gets_the_snapshot_of_now(tmp_path: Path) -> None:
    """An AI that cannot look things up – one without them, or one that refuses them – gets
    the snapshot of now, as before, and answers."""
    hm, _clock, _ha = await _bathroom(tmp_path)
    plain = FakeAI([{"answer": "Alles is rustig."}])
    await enable(hm, plain)
    assert (await hm.ask("Hoe is het in huis?", "nl", "demo-user-1"))["ok"]
    sent = json.loads(plain.sent[0][1].split("\n")[1])
    assert "rooms" in sent["house"]
    refusing = LookingAI([AIResult(ok=False, error="http_400"), {"answer": "Alles is rustig."}])
    hm.ai.provider = refusing
    answer = await hm.ask("Hoe is het in huis?", "nl", "demo-user-1")
    assert answer["ok"] and answer["message"] == "Alles is rustig."
    assert refusing.rounds[0]["tools"] and refusing.rounds[1]["tools"] == []
    await hm.stop()


# -- Huisregels aanpassen in je eigen woorden (E-420, B-352) -------------------------------


async def test_a_house_rule_changes_in_your_own_words_only_after_yes(tmp_path: Path) -> None:
    """ "Maak die regel in het weekend een uur later": the AI points at the rule and gives the
    new one; HouseMind reads it back in its own words – before 8:00 at the weekend, and still
    before 7:00 on weekdays – and changes nothing until the yes. After the yes the curtains stay
    closed until 8:00 on a Saturday, and on a Monday until 7:00; after a no nothing changes.
    Without an AI she does not guess which rule is meant: nothing changes, as before."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    rule = parse_rule(
        "nooit de gordijnen in de woonkamer open voor 7:00", "nl", hm.languages, hm.model
    )
    assert rule is not None
    await hm.save_rule(rule, "demo-user-1")
    alone = await hm.talk("Maak die regel in het weekend een uur later", "nl", 3, "demo-user-1")
    assert "confirm_rule" not in alone  # without an AI: no guessing
    living = hm.ai.labels.area("living")
    change = {
        "answer": "Dat kan.",
        "rule_change": {
            "number": 1,
            "rule": {
                "scope_type": "area",
                "scope": living,
                "effect": "never",
                "category": "open",
                "time_to": "08:00",
                "days": ["sat", "sun"],
            },
            "keep_other_days": True,
        },
    }
    fake = FakeAI([change, change])
    await enable(hm, fake)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=USER)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "talk", "text": "Maak die regel in het weekend een uur later"})
        asked = await _next(ws, "say")
        weekend = replace(rule, time_to=rule.time_to.replace(hour=8), days=(5, 6))  # type: ignore[union-attr]
        weekdays = replace(rule, days=(0, 1, 2, 3, 4))
        for kept in (weekdays, weekend):
            assert read_back(kept, "nl", hm.languages, hm.model) in asked["text"]
        assert "in het weekend" in asked["text"] and "op werkdagen" in asked["text"]
        assert asked["confirm"] and asked.get("ai") is True
        assert [(r.time_to, r.days) for r in hm.rules.rules] == [(rule.time_to, ())]  # not yet
        await ws.send_json({"type": "confirm_rule", "token": asked["confirm"], "yes": True})
        done = await _next(ws, "say")
        assert done["text"] == "De huisregel is aangepast."
        assert sorted((r.time_to.hour, r.days) for r in hm.rules.rules if r.time_to) == [
            (7, (0, 1, 2, 3, 4)),
            (8, (5, 6)),
        ]
        await ws.send_json({"type": "talk", "text": "Maak die regel in het weekend een uur later"})
        again = await _next(ws, "say")
        await ws.send_json({"type": "confirm_rule", "token": again["confirm"], "yes": False})
        await _next(ws, "say")
        assert len(hm.rules.rules) == 2  # a no changes nothing
        await ws.close()
    curtain = ActionRequest(Layer.LEARNING, "cover.living_curtain", "cover.open_cover", {})
    saturday = datetime(2026, 10, 24, 7, 30, tzinfo=LOCAL)
    monday = datetime(2026, 10, 26, 7, 30, tzinfo=LOCAL)
    assert hm.rules.veto(curtain, saturday, "home") is not None
    assert hm.rules.veto(curtain, monday, "home") is None
    await hm.stop()


# -- Doorpraten (E-421, B-353) -----------------------------------------------------------


async def test_en_in_de_keuken_ook_becomes_a_button_and_nothing_happens_without_it(
    tmp_path: Path,
) -> None:
    """After "Doe het licht in de woonkamer aan" and your yes, "en in de keuken ook" meant
    nothing to her. Now the AI knows what the conversation was about – the lamp, the room and
    the action, as labels – and makes it a question with buttons: the kitchen light goes on
    only after your yes, through the safety gate (B-17)."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    hm.activated, hm.gate.state.inactive = True, False  # set up: proposals may come (E-275)
    labels = hm.ai.labels
    ceiling, kitchen = labels.to_label["light.living_ceiling"], labels.to_label["light.kitchen"]
    fake = FakeAI(
        [
            {
                "answer": "Dat kan ik voorstellen.",
                "suggestions": [{"device": ceiling, "action": "light.turn_on"}],
            },
            "{}",  # her words for the question: her own
            {
                "answer": "Ook daar?",
                "suggestions": [{"device": kitchen, "action": "light.turn_on"}],
            },
            "{}",
        ]
    )
    await enable(hm, fake)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=USER)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "talk", "text": "Doe het licht in de woonkamer aan"})
        await _next(ws, "say")
        await _next(ws, "say")  # her question
        await ws.send_json({"type": "talk", "text": "Ja"})
        await _next(ws, "say")
        assert ha.states["light.living_ceiling"].state == "on"
        await ws.send_json({"type": "talk", "text": "en in de keuken ook"})
        await _next(ws, "say")  # the AI's words
        asked = await _next(ws, "say")  # her question, with its buttons
        assert asked["ask"]["kind"] == "proposal" and "keuken" in asked["text"].lower()
        about = json.loads(fake.sent[2][1].split("\n")[1])["conversation"]["about"]
        assert about == {
            "device": ceiling,
            "room": labels.area("living"),
            "action": "light.turn_on",
            "done": True,
        }
        await asyncio.sleep(0.05)
        assert ha.states["light.kitchen"].state == "off"  # nothing without the tap
        await ws.send_json({"type": "talk", "text": "Ja"})
        await _next(ws, "say")
        assert ha.states["light.kitchen"].state == "on"
        await ws.close()
    await hm.stop()


async def test_zet_dat_terug_undoes_what_she_did_in_this_conversation(tmp_path: Path) -> None:
    """Without an AI too: she asked to switch off the reading lamp, you said yes – and "zet
    dat terug" was no command, while "maak ongedaan" skipped what you had just said yes to. Now
    it puts back what she did in this conversation."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    ha.states["light.living_reading"] = State(
        "light.living_reading", "on", {"brightness": 200}, hm.clock.now()
    )
    hm.world.states["light.living_reading"] = ha.states["light.living_reading"]
    await _ask(hm, "light_off_leave", "light.living_reading", "living", "light.turn_off")
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=USER)
        await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": False})
        await ws.send_json({"type": "orb_tap"})
        said = await _next(ws, "say")
        assert "leeslamp" in said["text"]
        await ws.send_json({"type": "talk", "text": "Ja"})
        await _next(ws, "say")
        assert ha.states["light.living_reading"].state == "off"
        await ws.send_json({"type": "talk", "text": "Zet dat terug"})
        back = await _next(ws, "say")
        assert back["text"] == hm.languages.text("nl", "talk.undone")
        assert ha.states["light.living_reading"].state == "on"
        await ws.close()
    await hm.stop()


# -- Woord voor woord (E-159, B-354) -----------------------------------------------------


async def test_her_answer_appears_as_it_is_written_and_the_answer_replaces_it(
    tmp_path: Path,
) -> None:
    """With an AI her answer came all at once, after a long wait. Now it reaches her window as
    it is written – whole words, in plain names, never a label – and her answer replaces it."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    living = hm.ai.labels.area("living")
    text = f"In {living} is het nu twintig graden en verder is alles in huis rustig vandaag."
    fake = LookingAI([{"answer": text}], pieces=12)
    await enable(hm, fake)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=USER)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "talk", "text": "Hoe voelt het huis vandaag aan?"})
        partial: list[str] = []
        while True:
            message = await ws.receive_json(timeout=5)
            if message["type"] == "partial":
                partial.append(message["text"])
            elif message["type"] == "say":
                break
        await ws.close()
    final = "In de woonkamer is het nu twintig graden en verder is alles in huis rustig vandaag."
    assert message["text"].endswith(final) and message.get("ai") is True
    assert len(partial) >= 3 and partial == sorted(partial, key=len)
    assert all(final.startswith(p) for p in partial), partial
    assert not any("living" in p for p in partial)
    assert fake.rounds[0]["streamed"]
    await hm.stop()


async def test_after_twenty_seconds_she_answers_herself(tmp_path: Path, monkeypatch: Any) -> None:
    """An AI that takes long kept her window waiting. After 20 seconds she now says so and
    answers herself; what the AI still writes no longer reaches the window."""
    assert service.ANSWER_WAIT == 20.0
    monkeypatch.setattr(service, "ANSWER_WAIT", 0.3)
    hm, _ha, _clock = await _house(tmp_path, level=3)
    fake = LookingAI([{"answer": "Een heel laat antwoord over het huis en alles erin."}], 12, 0.1)
    await enable(hm, fake)
    shown: list[str] = []

    async def window(text: str) -> None:
        shown.append(text)

    started = timer.perf_counter()
    reply = await hm.talk(
        "Hoe voelt het huis vandaag aan?", "nl", 3, "demo-user-1", "", on_partial=window
    )
    assert timer.perf_counter() - started < 1.5
    assert reply["text"].startswith(hm.languages.text("nl", "ai.why.timeout"))
    seen = len(shown)
    await asyncio.sleep(1.5)  # the AI finishes in the background
    assert len(shown) == seen
    assert len(await hm.ai.log()) == 1  # still logged, and counted
    await hm.stop()


# -- the adapters speak tools and streams ---------------------------------------------------


async def _sse(request: web.Request, events: list[dict[str, Any]]) -> web.StreamResponse:
    response = web.StreamResponse(headers={"content-type": "text/event-stream"})
    await response.prepare(request)
    for event in events:
        await response.write(f"data: {json.dumps(event)}\n\n".encode())
    await response.write(b"data: [DONE]\n\n")
    await response.write_eof()
    return response


async def test_the_adapters_look_things_up_and_stream() -> None:
    """Anthropic, OpenAI-compatible (also Ollama) and Gemini send the lookups as their own
    tools and give back what the AI asks for; Anthropic and OpenAI-compatible stream the
    answer piece by piece; a lookup's result goes back with the call it belongs to."""
    from housemind.ai.providers import Tool, ToolCall, Turn

    seen: dict[str, Any] = {}
    tool = Tool(
        "room", "A room now.", {"type": "object", "properties": {"room": {"type": "string"}}}
    )

    async def anthropic(request: web.Request) -> web.StreamResponse:
        body = seen["anthropic"] = await request.json()
        if not body.get("stream"):
            return web.json_response(
                {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "room",
                            "input": {"room": "kitchen_1"},
                        }
                    ],
                    "usage": {"input_tokens": 5, "output_tokens": 2},
                }
            )
        return await _sse(
            request,
            [
                {"type": "message_start", "message": {"usage": {"input_tokens": 9}}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": '{"answer": "Al'},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": 'les goed"}'},
                },
                {"type": "message_delta", "usage": {"output_tokens": 4}},
            ],
        )

    async def openai(request: web.Request) -> web.StreamResponse:
        body = seen["openai"] = await request.json()
        if not body.get("stream"):
            call = {
                "id": "o1",
                "type": "function",
                "function": {"name": "room", "arguments": '{"room": "hall_1"}'},
            }
            return web.json_response(
                {"choices": [{"message": {"content": None, "tool_calls": [call]}}]}
            )
        return await _sse(
            request,
            [
                {"choices": [{"delta": {"content": '{"answer": '}}]},
                {"choices": [{"delta": {"content": '"Rustig"}'}}]},
                {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
            ],
        )

    async def gemini(request: web.Request) -> web.Response:
        seen["gemini"] = await request.json()
        part = {
            "functionCall": {"name": "room", "args": {"room": "attic_1"}},
            "thoughtSignature": "sig",
        }
        return web.json_response({"candidates": [{"content": {"parts": [part]}}]})

    app = web.Application()
    app.router.add_post("/a/messages", anthropic)
    app.router.add_post("/o/chat/completions", openai)
    app.router.add_post("/g/models/{model}", gemini)
    server = TestServer(app)
    await server.start_server()
    pieces: list[str] = []

    async def piece(text: str) -> None:
        pieces.append(text)

    first = Turn("user", "<data>{}</data>")
    one = AnthropicProvider("k", str(server.make_url("/a")))
    asked = await one.converse("sys", [first], "m", 100, [tool])
    assert asked.calls == (ToolCall("t1", "room", {"room": "kitchen_1"}),)
    assert seen["anthropic"]["tools"][0]["input_schema"]["properties"] == {
        "room": {"type": "string"}
    }
    back = (
        first,
        Turn("assistant", "", asked.calls),
        Turn("tool", results=((asked.calls[0], "{}"),)),
    )
    streamed = await one.converse("sys", back, "m", 100, [tool], final=True, on_text=piece)
    assert streamed.text == '{"answer": "Alles goed"}' and streamed.input_tokens == 9
    assert seen["anthropic"]["tool_choice"] == {"type": "none"}
    assert seen["anthropic"]["messages"][2]["content"][0]["tool_use_id"] == "t1"
    two = OpenAICompatibleProvider(str(server.make_url("/o")))
    asked = await two.converse("sys", [first], "m", 100, [tool])
    assert asked.calls == (ToolCall("o1", "room", {"room": "hall_1"}),)
    back = (
        first,
        Turn("assistant", "", asked.calls),
        Turn("tool", results=((asked.calls[0], "{}"),)),
    )
    streamed = await two.converse("sys", back, "m", 100, [tool], on_text=piece)
    assert streamed.text == '{"answer": "Rustig"}' and streamed.output_tokens == 3
    assert seen["openai"]["messages"][-1] == {"role": "tool", "tool_call_id": "o1", "content": "{}"}
    three = GeminiProvider("k", str(server.make_url("/g")))
    asked = await three.converse("sys", [first], "m", 100, [tool])
    assert asked.calls[0].name == "room" and asked.calls[0].args == {"room": "attic_1"}
    assert seen["gemini"]["tools"][0]["functionDeclarations"][0]["name"] == "room"
    back = (
        first,
        Turn("assistant", "", asked.calls),
        Turn("tool", results=((asked.calls[0], "{}"),)),
    )
    await three.converse("sys", back, "m", 100, [tool])
    assert seen["gemini"]["contents"][1]["parts"][0]["thoughtSignature"] == "sig"  # as it came
    assert pieces == ['{"answer": "Al', 'les goed"}', '{"answer": ', '"Rustig"}']
    for provider in (one, two, three):
        await provider.close()
    await server.close()
