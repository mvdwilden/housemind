"""Vesta listens (E-349, chapter 32), from the user's side: an answer to her own question belongs
to that question. Without an AI she is no AI: every question of hers is a choice, with buttons,
and what is not one of her choices she says honestly – she never guesses. With an AI she is
free and smart, with the conversation along, and does something only after your yes. Each test
fails without it; what it checks is in its docstring."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.notify import Kind
from housemind.server.web import create_app
from housemind.world.house_mode import HouseMode
from tests.test_ai import FakeAI, enable
from tests.test_vesta import PHRASES, _ask, _house, _next, rendered

USER = {"X-Remote-User-Id": "demo-user-1"}
SAY_CHOICES = [line.split("{choices}")[0] for line in PHRASES.variants("nl", "ask_choices", False)]


async def _tap(client: TestClient, mic: bool) -> tuple[Any, dict[str, Any]]:
    """Her window after a tap: she asks whether to switch off the reading lamp."""
    ws = await client.ws_connect("/api/ws", headers=USER)
    await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": mic})
    await ws.send_json({"type": "orb_tap"})
    said = await _next(ws, "say")
    assert "leeslamp" in said["text"] and said["text"].endswith("?")
    return ws, said


async def _answer(ws: Any, ha: Any, text: str, spoken: bool) -> dict[str, Any]:
    if spoken:
        ha.heard = [text]
        await ws.send_json({"type": "listen_start"})
        await ws.send_bytes(b"\x01\x00" * 16000)
        await ws.send_json({"type": "listen_end"})
    else:
        await ws.send_json({"type": "talk", "text": text})
    return await _next(ws, "say")


async def _status(hm: Any, proposal: int) -> str:
    """ "open", or the answer given: "do", "not_now" …"""
    row = await hm.db.fetchone("SELECT status, answer FROM proposals WHERE id = ?", (proposal,))
    return str(row["answer"]) if row["status"] == "answered" else str(row["status"])


async def _reading(hm: Any) -> int:
    return await _ask(hm, "light_off_leave", "light.living_reading", "living", "light.turn_off")


# -- without an AI: choices -------------------------------------------------------------


async def test_after_a_tap_her_question_has_buttons_and_a_typed_yes_answers_it(
    tmp_path: Path,
) -> None:
    """She asked "Zal ik de leeslamp uitdoen?" after a tap without any buttons, and a typed
    "Ja" got "Dat weet ik helaas niet": only a spoken answer on a screen with a microphone was
    linked to her question. Now her question has the buttons of a proposal and Waarom?, and
    "Ja", typed, answers it like Doe het."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    proposal = await _reading(hm)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, said = await _tap(client, mic=False)
        assert said["ask"]["actions"] == ["do", "not_now", "from_now", "never", "why"]
        assert said["ask"]["proposal"] == proposal
        reply = await _answer(ws, ha, "Ja", spoken=False)
        assert reply["text"] in PHRASES.variants("nl", "answer_yes", False)
        assert await _status(hm, proposal) == "do"
        await ws.close()
    await hm.stop()


async def test_the_words_of_her_buttons_answer_her_question(tmp_path: Path) -> None:
    """Spoken or typed, the words on a button answer her question as that button does: "Nu
    niet" brings it back later, a plain "Nee" is a real no (20.9), "Doe het" does it."""
    for n, (said, spoken, status) in enumerate(
        [("Nu niet", False, "not_now"), ("Nee", True, "not_now"), ("Doe het maar", True, "do")]
    ):
        hm, ha, _clock = await _house(tmp_path / str(n), level=3)
        proposal = await _reading(hm)
        async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
            ws, _ = await _tap(client, mic=spoken)
            reply = await _answer(ws, ha, said, spoken)
            assert "more" not in reply, (said, reply["text"])
            assert await _status(hm, proposal) == status, (said, reply["text"])
            await ws.close()
        await hm.stop()


async def test_why_tells_the_reason_and_her_question_stays(tmp_path: Path) -> None:
    """ "Waarom?" got "Ik heb vandaag nog niets gedaan" (about the last action). Now it is a
    button and a word: she says why she asks and asks again, and her question stays open."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    proposal = await _reading(hm)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, _ = await _tap(client, mic=True)
        why = await _answer(ws, ha, "Waarom?", spoken=True)
        assert "niemand" in why["text"] and why["text"].endswith("uitdoen?"), why["text"]
        assert why["ask"]["proposal"] == proposal  # the buttons stay
        assert await _status(hm, proposal) == "open"
        yes = await _answer(ws, ha, "Ja", spoken=True)  # still her question
        assert yes["text"] in PHRASES.variants("nl", "answer_yes", False)
        await ws.close()
    await hm.stop()


async def test_what_is_not_one_of_her_choices_she_never_guesses(tmp_path: Path) -> None:
    """Without an AI "Graag" read out a piece of the Help about the alarm, "Liever niet" one
    about tapping Vesta. Now what is not one of her choices she says honestly – she names her
    choices – and her question stays open; a question she knows for sure ("Hoe heet je?") she
    answers in between, and her question still stays open."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    proposal = await _reading(hm)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, _ = await _tap(client, mic=False)
        for said in ("Graag", "Liever niet", "Blauwe banaan"):
            reply = await _answer(ws, ha, said, spoken=False)
            assert "more" not in reply, (said, reply["text"])
            assert any(reply["text"].startswith(lead) for lead in SAY_CHOICES), reply["text"]
            assert "doe het" in reply["text"] and "waarom" in reply["text"]
            assert reply["ask"]["proposal"] == proposal
        name = await _answer(ws, ha, "Hoe heet je?", spoken=False)
        assert "Vesta" in name["text"] and name["ask"]["proposal"] == proposal
        assert await _status(hm, proposal) == "open"
        await ws.close()
        fresh = await client.ws_connect("/api/ws", headers=USER)
        await fresh.send_json({"type": "hello", "browserLanguage": "nl"})
        await fresh.send_json({"type": "talk", "text": "Graag"})
        alone = await _next(fresh, "say")
        assert "more" not in alone, alone["text"]  # one word is no piece of the Help (B-230)
        assert alone["text"] in PHRASES.variants("nl", "know_unknown", False)
        await fresh.close()
    await hm.stop()


async def test_a_question_in_her_window_is_answered_by_voice_and_by_its_words(
    tmp_path: Path,
) -> None:
    """A question in her window that is no proposal ("Zijn jullie op vakantie?", a task)
    could only be answered with its buttons, and she listened only after a proposal. Now she
    listens after every kind, and "Ja" or the word on a button answers it."""
    hm, ha, clock = await _house(tmp_path, level=3)
    assert hm.world is not None
    hm.activated = True  # set up: she asks things of her own accord (E-275)
    snapshot = hm.world.snapshot(clock.now())
    await hm._raise(
        Kind.IMPORTANT,
        "vacation_suggestion",
        "notify.vacation_suggestion",
        {},
        snapshot,
        actions=(("yes", "notify.answer.yes"), ("no", "notify.answer.no")),
    )
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=USER)
        await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": True})
        ask = await _next(ws, "vesta_ask")
        assert ask["kind"] == "vacation"
        await ws.send_json({"type": "ask_shown", "key": ask["key"]})
        reply = await _answer(ws, ha, "Ja hoor", spoken=True)
        assert reply["text"] in PHRASES.variants("nl", "answer_yes", False)
        for _ in range(50):  # she says so first, then it happens
            if hm.world.modes.mode is HouseMode.VACATION:
                break
            await asyncio.sleep(0.02)
        assert hm.world.modes.mode is HouseMode.VACATION
        await ws.close()
    await hm.stop()


async def test_a_tap_without_an_ai_ends_with_choices(tmp_path: Path) -> None:
    """Without an AI a tap ended with "Wat kan ik voor je doen?" – an open question she could
    not keep. Now she offers her choices as buttons; Moet ik nog iets doen? she answers, and
    "Pauze" said aloud opens the pause menu in the app."""
    hm, ha, _clock = await _house(tmp_path, level=2)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=USER)
        await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": True})
        await ws.send_json({"type": "orb_tap"})
        said = await _next(ws, "say")
        assert [c["id"] for c in said["choices"]] == ["status", "todo", "pause", "help"]
        assert said["text"] in rendered("nl", "offer_choices") and said["listen"] is True
        await ws.send_json({"type": "choice", "choice": "todo"})
        todo = await _next(ws, "say")
        assert todo["text"] in PHRASES.variants("nl", "know_todo_none", False)
        assert [c["id"] for c in todo["choices"]] == ["status", "todo", "pause", "help"]
        ha.heard = ["Pauze"]
        await ws.send_json({"type": "listen_start"})
        await ws.send_bytes(b"\x01\x00" * 16000)
        await ws.send_json({"type": "listen_end"})
        assert (await _next(ws, "choice"))["choice"] == "pause"
        await ws.close()
    await hm.stop()


# -- with an AI: free and smart ---------------------------------------------------------


async def test_with_an_ai_your_own_words_go_with_her_question_to_the_stronger_model(
    tmp_path: Path,
) -> None:
    """With an AI, an answer in your own words went to the AI without her question, to the
    cheapest model – or was read as a new question. Now it goes with her question and the
    last sentences, as labels (E-15), to the stronger model; the AI's "reply" answers her
    question the way its button does, through the safety gate, with "via AI" under it."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    proposal = await _reading(hm)
    fake = FakeAI(["{}", {"answer": "Prima, ik doe hem uit.", "reply": "do"}])
    await enable(hm, fake)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, said = await _tap(client, mic=False)
        reply = await _answer(ws, ha, "Dat lijkt me een prima plan", spoken=False)
        assert reply["text"].endswith("Prima, ik doe hem uit.") and reply.get("ai") is True
        assert await _status(hm, proposal) == "do"
        _system, user, model = fake.sent[-1]
        assert model == "claude-sonnet-5-5"
        sent = json.loads(user.split("\n")[1])  # <data> … </data>
        talk = sent["conversation"]
        assert talk["asked"]["choices"] == ["do", "not_now", "from_now", "never", "why"]
        said = json.dumps(talk, ensure_ascii=False).lower()
        assert "woonkamer" not in said and "living_" in said, said  # labels only (E-15)
        await ws.close()
    await hm.stop()


async def test_with_an_ai_something_to_do_is_asked_first_and_done_after_yes(
    tmp_path: Path,
) -> None:
    """ "Doe de lamp aan" with an AI: the AI only suggests (B-17); Vesta asks it
    right away with the buttons of a proposal, and it happens only after "Ja", through the
    safety gate. The AI may also ask back with choices, which become buttons."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    hm.activated, hm.gate.state.inactive = True, False  # set up: proposals may come (E-275)
    fake = FakeAI(
        [
            {"answer": "Welke bedoel je?", "choices": ["De keukenlamp", "Alle lampen"]},
            {
                "answer": "Dat kan ik voorstellen.",
                "suggestions": [{"device": "", "action": "light.turn_on"}],
            },
            "{}",  # her words for the question: her own
        ]
    )
    await enable(hm, fake)
    kitchen = hm.ai.labels.to_label["light.kitchen"]
    fake.replies[1]["suggestions"][0]["device"] = kitchen
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=USER)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "talk", "text": "Doe de lamp aan"})
        back = await _next(ws, "say")
        assert [c["text"] for c in back["choices"]] == ["De keukenlamp", "Alle lampen"]
        await ws.send_json({"type": "talk", "text": "De keukenlamp"})
        await _next(ws, "say")  # the AI's words
        asked = await _next(ws, "say")  # her question, with its buttons
        assert asked["ask"]["kind"] == "proposal" and "keuken" in asked["text"]
        assert ha.states["light.kitchen"].state == "off"  # nothing yet: only a proposal
        await _answer(ws, ha, "Ja", spoken=False)
        assert ha.states["light.kitchen"].state == "on"  # after your yes, through the gate
        await ws.close()
    await hm.stop()


async def test_with_the_ai_away_she_says_so_and_keeps_to_her_choices(tmp_path: Path) -> None:
    """With the month's AI budget used up, an answer she does not know herself went to the AI
    and came back as "Het AI-budget is op". Now she says that, and keeps to her choices."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    proposal = await _reading(hm)
    fake = FakeAI([])
    await enable(hm, fake, monthly_budget=0.0)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, _ = await _tap(client, mic=False)
        reply = await _answer(ws, ha, "Hmm, wat denk jij zelf?", spoken=False)
        assert reply["text"].startswith("Het AI-budget voor deze maand is op."), reply["text"]
        assert "more" not in reply and reply["ask"]["proposal"] == proposal
        assert await _status(hm, proposal) == "open"
        await ws.close()
    await hm.stop()


# -- through Assist ---------------------------------------------------------------------


async def test_through_assist_yes_answers_the_question_she_asked_there(tmp_path: Path) -> None:
    """Through Assist a short "ja" answered the newest open proposal of the house, also when
    she had asked something else in that conversation. Now the conversation keeps her question:
    she asks the AI's suggestion and keeps listening, "Waarom?" tells why, and "ja" answers
    that question – not a newer proposal from elsewhere."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    hm.activated, hm.gate.state.inactive = True, False  # set up: proposals may come (E-275)
    fake = FakeAI(
        [
            {
                "answer": "Dat kan ik voorstellen.",
                "suggestions": [{"device": "", "action": "light.turn_on"}],
            },
            "{}",  # her words for the question: her own
        ]
    )
    await enable(hm, fake)
    fake.replies[0]["suggestions"][0]["device"] = hm.ai.labels.to_label["light.kitchen"]
    first = await hm.assist("Doe de lamp in de keuken aan", "nl", "c1", "demo-user-1")
    assert first["continue"] is True and "keuken" in first["text"] and first["text"].endswith("?")
    newer = await _reading(hm)  # a newer proposal, asked nowhere in this conversation
    why = await hm.assist("Waarom?", "nl", "c1", "demo-user-1")
    assert "vraag" in why["text"] and why["continue"] is True
    yes = await hm.assist("Ja", "nl", "c1", "demo-user-1")
    assert yes["text"] in PHRASES.variants("nl", "answer_yes", False)
    assert ha.states["light.kitchen"].state == "on"
    assert await _status(hm, newer) == "open"
    await hm.stop()
