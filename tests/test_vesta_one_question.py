"""One question at a time, and she listens after a tap (E-464, chapter 67), from the user's side.
The administrator (07-11-2026): "voordat ik überhaupt de kans krijg om een antwoord te geven,
krijg ik alweer een andere vraag erdoorheen", the buttons were not always there, and after an
answer she listened again by herself. Each test fails without it; what it checks is in its
docstring."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.notify import Kind
from housemind.server.web import create_app
from housemind.world.house_mode import HouseMode
from tests.test_ai import FakeAI, enable
from tests.test_vesta import PHRASES, _house, _next, _raise
from tests.test_vesta_asks import _proposal, _until
from tests.test_vesta_listens import _answer, _reading

USER = {"X-Remote-User-Id": "demo-user-1"}


async def _two_tasks(hm: Any) -> None:
    await _raise(
        hm, Kind.IMPORTANT, "battery:x", "notify.maintenance.battery", {"device": "Gang rookmelder"}
    )
    await _raise(
        hm,
        Kind.IMPORTANT,
        "battery:y",
        "notify.maintenance.battery",
        {"device": "Keuken rookmelder"},
    )


async def _screen(client: TestClient, mic: bool = False) -> Any:
    ws = await client.ws_connect("/api/ws", headers=USER)
    await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": mic})
    return ws


async def _nothing_more(ws: Any, *kinds: str, wait: float = 1) -> None:
    """None of these messages comes in the time given."""
    with pytest.raises(asyncio.TimeoutError):
        await _until(ws, *kinds, wait=wait)


# -- one question at a time ----------------------------------------------------------------


async def test_her_question_stays_until_you_answer_it(tmp_path: Path) -> None:
    """The question on the screen counted as asked, so the next time anything changed in the
    house it no longer "waited": its buttons went and the next question came in its place. Now
    it stays, however much changes, and the next comes after your answer, as the next."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    await _two_tasks(hm)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _screen(client)
        ask = (await _until(ws, "vesta_ask"))["vesta_ask"]
        await ws.send_json({"type": "ask_shown", "key": ask["key"]})
        await asyncio.sleep(0.1)
        for _ in range(3):
            await hm._notify()  # something changes in the house
        await _nothing_more(ws, "vesta_ask", "vesta_ask_gone")
        await ws.send_json(
            {"type": "notification", "id": ask["notification"], "action": "seen", "via": "vesta"}
        )
        got = await _until(ws, "say", "vesta_ask")
        assert got["say"]["text"] in PHRASES.variants("nl", "ask_seen", False)
        assert got["vesta_ask"]["key"] != ask["key"] and got["vesta_ask"]["follow"] is True
        await ws.close()
    await hm.stop()


async def test_a_question_within_todays_budget_stays_when_the_budget_is_used(
    tmp_path: Path,
) -> None:
    """A proposal counts as one of the person's questions for today once it is offered. With the
    budget used up it stays on the screen all the same: it was already asked."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    await _proposal(hm, "light_off_leave", "light.living_reading", "living", "light.turn_off")
    assert hm.router is not None
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _screen(client)
        ask = (await _until(ws, "vesta_ask"))["vesta_ask"]
        await ws.send_json({"type": "ask_shown", "key": ask["key"]})
        await asyncio.sleep(0.1)
        hm.router.question_budget = lambda person: 0  # type: ignore[method-assign]
        await hm._notify()
        await _nothing_more(ws, "vesta_ask_gone")
        await ws.close()
    await hm.stop()


async def test_a_question_answered_elsewhere_goes(tmp_path: Path) -> None:
    """On another phone, or in the Logboek: then there is nothing to answer any more, also not
    when it was still waiting for its moment on this screen."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    await _two_tasks(hm)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _screen(client)
        other = await _screen(client)
        ask = (await _until(ws, "vesta_ask"))["vesta_ask"]
        await _until(other, "vesta_ask")
        await other.send_json({"type": "notification", "id": ask["notification"], "action": "seen"})
        assert (await _next(other, "vesta_ask_gone"))["key"] == ask["key"]  # this screen too
        await hm._notify()  # as the brain does after a change
        assert (await _next(ws, "vesta_ask_gone"))["key"] == ask["key"]
        await ws.close()
        await other.close()
    await hm.stop()


# -- the buttons are there -----------------------------------------------------------------


async def test_a_question_she_mentions_on_a_tap_has_its_own_buttons(tmp_path: Path) -> None:
    """After a tap she said "Zijn jullie op vakantie?" with no Ja and no Nee, because only a
    notice with Ik kijk ernaar had its answers there. Now every question she mentions has its
    own buttons, and the button does what it says, with her words after it."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    assert hm.world is not None
    hm.activated = True
    await hm._raise(
        Kind.IMPORTANT,
        "vacation_suggestion",
        "notify.vacation_suggestion",
        {},
        hm.world.snapshot(hm.clock.now()),
        actions=(("yes", "notify.answer.yes"), ("no", "notify.answer.no")),
    )
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _screen(client)
        await ws.send_json({"type": "orb_tap"})
        said = await _next(ws, "say")
        assert "?" in said["text"], said["text"]
        assert said["ask"]["actions"] == ["yes", "no"] and said["ask"]["kind"] == "vacation"
        await ws.send_json(
            {
                "type": "notification",
                "id": said["ask"]["notification"],
                "action": "yes",
                "via": "vesta",
            }
        )
        reply = await _next(ws, "say")
        assert reply["text"] in PHRASES.variants("nl", "answer_yes", False)
        for _ in range(50):
            if hm.world.modes.mode is HouseMode.VACATION:
                break
            await asyncio.sleep(0.02)
        assert hm.world.modes.mode is HouseMode.VACATION
        await ws.close()
    await hm.stop()


# -- she listens after a tap ---------------------------------------------------------------


async def test_she_listens_after_a_tap_and_not_after_her_answer(tmp_path: Path) -> None:
    """After every answer the microphone opened again by itself. Now a tap opens it, once: her
    answer ends it, and for a new question you tap on her again."""
    hm, ha, _clock = await _house(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _screen(client, mic=True)
        await ws.send_json({"type": "orb_tap"})
        assert (await _next(ws, "say"))["listen"] is True
        answer = await _answer(ws, ha, "Hoe heet je?", spoken=True)
        assert answer["heard"] == "Hoe heet je?" and "listen" not in answer
        await ws.close()
    await hm.stop()


async def test_not_understood_is_no_answer_she_listens_once_more(tmp_path: Path) -> None:
    """She did not catch it, so nothing is answered yet: she listens one more time, not when you
    typed it."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    await _reading(hm)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _screen(client, mic=True)
        await ws.send_json({"type": "orb_tap"})
        await _next(ws, "say")
        assert (await _answer(ws, ha, "Banaan", spoken=True))["listen"] is True
        assert (await _answer(ws, ha, "Banaan", spoken=True))["listen"] is False
        await ws.send_json({"type": "orb_tap"})
        await _next(ws, "say")
        assert (await _answer(ws, ha, "Banaan", spoken=False))["listen"] is False  # typed
        await ws.close()
    await hm.stop()


async def test_her_own_question_does_not_open_the_microphone(tmp_path: Path) -> None:
    """A question that came up by itself opened the microphone a few seconds later, and the
    buttons went for as long as she listened. Now only a tap on her opens it, and then "Ja"
    still answers her question."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    proposal = await _proposal(
        hm, "light_off_leave", "light.living_reading", "living", "light.turn_off"
    )
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _screen(client, mic=True)
        ask = (await _until(ws, "vesta_ask"))["vesta_ask"]
        assert "listen" not in ask
        await ws.send_json({"type": "ask_shown", "key": ask["key"]})
        await asyncio.sleep(0.1)
        await ws.send_json({"type": "ask_why", "key": ask["key"]})
        why = await _next(ws, "say")
        assert "listen" not in why and why["ask"]["key"] == ask["key"]  # Waarom? keeps it open
        reply = await _answer(ws, ha, "Ja", spoken=True)
        assert reply["text"] in PHRASES.variants("nl", "answer_yes", False)
        assert "listen" not in reply
        row = await hm.db.fetchone("SELECT answer FROM proposals WHERE id = ?", (proposal,))
        assert row["answer"] == "do"
        await ws.close()
    await hm.stop()


async def test_her_question_still_counts_after_a_long_wait(tmp_path: Path) -> None:
    """It now stays until you answer, so also after the five minutes a conversation lasts: you
    come back, tap on her and say "Ja", and that answers what is on the screen."""
    hm, ha, clock = await _house(tmp_path, level=3)
    proposal = await _proposal(
        hm, "light_off_leave", "light.living_reading", "living", "light.turn_off"
    )
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _screen(client, mic=True)
        ask = (await _until(ws, "vesta_ask"))["vesta_ask"]
        await ws.send_json({"type": "ask_shown", "key": ask["key"]})
        await asyncio.sleep(0.1)
        await clock.advance(int(timedelta(minutes=20).total_seconds()))
        reply = await _answer(ws, ha, "Ja", spoken=True)
        assert reply["text"] in PHRASES.variants("nl", "answer_yes", False)
        row = await hm.db.fetchone("SELECT answer FROM proposals WHERE id = ?", (proposal,))
        assert row["answer"] == "do"
        await ws.close()
    await hm.stop()


async def test_with_an_ai_she_does_not_listen_by_herself_either(tmp_path: Path) -> None:
    """With an AI the same: its answer, its question back and what it suggests all come with
    buttons, and the microphone stays shut until you tap on her."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    await enable(hm, FakeAI([{"answer": "Daar weet ik iets van.", "choices": ["Deze", "Die"]}]))
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _screen(client, mic=True)
        await ws.send_json({"type": "orb_tap"})
        await _next(ws, "say")
        reply = await _answer(ws, ha, "Wat is de beste plek voor een plant in huis?", spoken=True)
        assert [c["id"] for c in reply["choices"]] == ["Deze", "Die"]
        assert "listen" not in reply
        await ws.close()
    await hm.stop()
