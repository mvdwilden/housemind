"""Vesta asks the questions (E-252, B-96), from the user's side: what waits for an answer – a
proposal, a task – Vesta asks in her window, in her own words, one at a time. Each test fails
without it; what it checks is in its docstring."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.notify import Kind
from housemind.ha.adapter import Voice
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome
from housemind.server.web import create_app
from tests.test_vesta import PHRASES, _house, _raise


async def _proposal(hm: Any, skill: str, entity: str, area: str, action: str) -> int:
    request = ActionRequest(
        Layer.REFLEX, entity, action, {}, skill=skill, area_id=area, created_at=hm.clock.now()
    )
    decision = Decision(request, Outcome.PROPOSED, None, None, (), hm.clock.now())
    proposal = await hm.proposals.add(decision)
    assert proposal is not None
    await hm._ask(proposal, decision)  # as the brain does: it also waits in the list
    return int(proposal)


async def _until(ws: Any, *kinds: str, wait: float = 5) -> dict[str, dict[str, Any]]:
    """The first message of each kind, whatever comes in between or in which order."""
    found: dict[str, dict[str, Any]] = {}
    while set(kinds) - set(found):
        message = await ws.receive_json(timeout=wait)
        if message["type"] in kinds and message["type"] not in found:
            found[message["type"]] = message
    return found


async def test_a_proposal_is_asked_by_vesta_in_her_window(tmp_path: Path) -> None:
    """Proposals waited as a card and in the list. Now Vesta asks them herself, in her words
    and plain names, with Doe het / Nu niet / Voortaan zelf / Nooit zo; her voice follows once the
    window shows it; after "Doe het" she says so, and the next starts "Ik heb nog een vraag"."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    ha.voice = Voice("tts.cloud", "nl-NL", "Colette")
    hm.speech.voice = await hm.vesta_voice()
    await _proposal(hm, "light_off_leave", "light.living_reading", "living", "light.turn_off")
    await _proposal(hm, "covers_close_dark", "cover.living_curtain", "living", "cover.close_cover")
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        ask = (await _until(ws, "vesta_ask"))["vesta_ask"]
        actions = ["do", "not_now", "from_now", "never", "why"]  # Waarom? too (E-349)
        assert ask["kind"] == "proposal" and ask["actions"] == actions
        assert "?" in ask["text"] and "Woonkamer" not in ask["text"]  # her words, plain names
        assert ask["follow"] is False
        await ws.send_json({"type": "ask_shown", "key": ask["key"]})
        assert (await _until(ws, "voice"))["voice"]["audio"]
        await ws.send_json(
            {"type": "answer", "proposal": ask["proposal"], "answer": "do", "via": "vesta"}
        )
        got = await _until(ws, "say", "vesta_ask")
        assert got["say"]["text"] in PHRASES.variants("nl", "answer_yes", False)
        following = got["vesta_ask"]
        assert following["key"] != ask["key"] and following["follow"] is True
        lead = PHRASES.variants("nl", "next_question", False)
        assert any(following["text"].startswith(line) for line in lead)
        row = await hm.db.fetchone("SELECT status FROM proposals WHERE id = ?", (ask["proposal"],))
        assert row["status"] != "open"
        await ws.close()
    await hm.stop()


async def test_what_needs_you_comes_first_and_only_to_whom_it_concerns(tmp_path: Path) -> None:
    """A task (an important message) comes before a proposal, in plain names; a message for
    administrators is not asked to a resident, nor one meant for someone else; a wall tablet
    only asks the house's questions; level 1 gets none; asked once, not again within the hour."""
    hm, _ha, clock = await _house(tmp_path, level=3)
    await _proposal(hm, "light_off_leave", "light.living_reading", "living", "light.turn_off")
    snap_device = {"device": "Gang rookmelder"}
    await _raise(hm, Kind.IMPORTANT, "battery:x", "notify.maintenance.battery", snap_device)
    await _raise(hm, Kind.IMPORTANT, "a:x", "notify.learned", {"what": "x"}, concerns="admins")
    await _raise(
        hm, Kind.IMPORTANT, "u:x", "notify.learned", {"what": "y"}, concerns="user:demo-user-2"
    )
    resident = await hm.vesta_asks("demo-user-1", 3, False)
    assert [a["kind"] for a in resident] == ["task", "proposal"]
    assert len(await hm.vesta_asks("demo-user-1", 3, True)) == 3  # + the administrators' one
    assert [a["kind"] for a in await hm.vesta_asks("demo-user-1", 3, True, tablet=True)] == [
        "task",
        "proposal",
    ]
    assert await hm.vesta_asks("demo-user-1", 1, True) == []
    text = await hm.vesta_ask_text("demo-user-1", "nl", resident[0])
    assert "de rookmelder in de gang" in text.lower() and "Gang rookmelder" not in text
    hm.asked("demo-user-1", resident[0]["key"])
    assert [a["kind"] for a in await hm.vesta_asks("demo-user-1", 3, False)] == ["proposal"]
    await clock.advance(int(timedelta(minutes=61).total_seconds()))
    assert len(await hm.vesta_asks("demo-user-1", 3, False)) == 2
    await hm.stop()


async def test_a_task_answered_in_her_window_and_closing_means_later(tmp_path: Path) -> None:
    """ "Ik kijk ernaar" in her window – the same three answers as on the card (E-403) – closes
    the task and she says so in her words; closing her window without an answer leaves it in the
    list, and she does not ask the next right away."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
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
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        ask = (await _until(ws, "vesta_ask"))["vesta_ask"]
        assert ask["kind"] == "task" and ask["actions"] == ["seen", "snooze", "mute"]
        await ws.send_json({"type": "ask_shown", "key": ask["key"]})
        await ws.send_json(
            {"type": "notification", "id": ask["notification"], "action": "seen", "via": "vesta"}
        )
        got = await _until(ws, "say", "vesta_ask")
        assert got["say"]["text"] in PHRASES.variants("nl", "ask_seen", False)
        second = got["vesta_ask"]
        await ws.send_json({"type": "ask_shown", "key": second["key"]})
        await ws.send_json({"type": "ask_closed", "key": second["key"]})
        await hm._notify()  # something changes in the house: no new question right away
        with pytest.raises(asyncio.TimeoutError):
            await _until(ws, "vesta_ask", wait=1)
        still = await hm.db.fetchone(
            "SELECT resolved_at FROM notifications WHERE id = ?", (second["notification"],)
        )
        assert still["resolved_at"] is None  # it stays in the list
        await ws.close()
    await hm.stop()


async def test_her_voice_may_unlock_and_an_update_always_loads(tmp_path: Path) -> None:
    """Found while testing: the silent sound that unlocks her voice on the first tap was
    blocked by the page's own rules, and a cached page kept loading the old app."""
    hm, _ha, _clock = await _house(tmp_path)
    (tmp_path / "index.html").write_text("<!doctype html><title>HouseMind</title>")
    (tmp_path / "assets").mkdir(exist_ok=True)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        page = await client.get("/")
        assert "media-src 'self' data:" in page.headers["Content-Security-Policy"]
        assert page.headers["Cache-Control"] == "no-cache"
    await hm.stop()


async def test_her_window_is_one_choice_for_the_whole_house(tmp_path: Path) -> None:
    """Whether Vesta opens her window with a question by herself is one choice for the whole
    house (E-450; the administrator, 02-11-2026: "de expert kan de Vesta popup uitzetten ... en
    dan geldt het voor iedereen"). The administrator turns it off: the window goes, also for a
    resident, and the question still waits in the list. A resident cannot turn it on again,
    for the house nor for themselves. On again: her window comes back for everyone, also for
    someone who once chose 'off' for themselves (E-350: that own choice counts no more)."""
    hm, _ha, _clock = await _house(tmp_path, level=4)
    await hm.db.execute(
        "INSERT OR REPLACE INTO people(user_id, display_name, level, preferences, created) "
        "VALUES(?,?,?,?,?)",
        ("demo-user-2", "Bewoner", 3, '{"vesta_window": false}', "2026-10-01"),
    )
    await hm._refresh_people()
    await _proposal(hm, "light_off_leave", "light.living_reading", "living", "light.turn_off")
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        admin = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await admin.send_json({"type": "hello", "browserLanguage": "nl"})
        assert (await _until(admin, "vesta_ask"))["vesta_ask"]["kind"] == "proposal"
        # Huisinstellingen › Vesta: off, for everyone in the house.
        await admin.send_json({"type": "house_setting", "key": "vesta_window", "value": False})
        await _until(admin, "vesta_ask_gone")
        resident = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-2"})
        await resident.send_json({"type": "hello", "browserLanguage": "nl"})
        with pytest.raises(asyncio.TimeoutError):
            await _until(resident, "vesta_ask", wait=1.5)
        await resident.send_json({"type": "house_setting", "key": "vesta_window", "value": True})
        await resident.send_json({"type": "set_pref", "key": "vesta_window", "value": True})
        with pytest.raises(asyncio.TimeoutError):
            await _until(resident, "vesta_ask", wait=1.5)
        assert await hm.db.get_setting("vesta_window", True) is False
        rows = await hm.notifications()
        assert any(str(r["topic"]).startswith("proposal:") for r in rows)  # still in the list
        # On again: the window comes back, for the resident too.
        await admin.send_json({"type": "house_setting", "key": "vesta_window", "value": True})
        assert (await _until(resident, "vesta_ask"))["vesta_ask"]["kind"] == "proposal"
        await admin.close()
        await resident.close()
    await hm.stop()
