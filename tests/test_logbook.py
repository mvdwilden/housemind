"""The logbook (E-263, B-117), from the user's side: one timeline, the newest on top, of what
HouseMind did (with Why? and Undo), the notifications, your own conversations with Vesta and the
house report – and per level more or less of each line. Each test fails without it; what it
checks is its docstring."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.notify import Kind
from housemind.knowledge.catalog import Step
from housemind.server.logbook import logbook
from housemind.server.web import create_app
from tests.test_screens import _next, kitchen_light_on, start, viewer


async def _log(ws: Any) -> list[dict[str, Any]]:
    await ws.send_json({"type": "get", "what": "logbook"})
    while True:
        message = await _next(ws, "data")
        if message["what"] == "logbook":
            return message["data"]["items"]  # type: ignore[no-any-return]


async def test_one_timeline_with_what_it_did_notices_and_your_talks(tmp_path: Path) -> None:
    """What HouseMind did (with Why? and Undo), a notification with its answers, and your
    question to Vesta with her answer – in one list, the newest on top. Why? and Undo work from
    the logbook."""
    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await _next(ws, "snapshot")
        await ws.send_json({"type": "set_level", "level": 3})
        hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
        await hm.consider(kitchen_light_on())
        await clock.advance(60)
        assert hm.world is not None
        snap = hm.world.snapshot(clock.now())
        await hm._raise(
            Kind.IMPORTANT, "open:door", "notify.open_when_away", {"device": "deur"}, snap
        )
        await clock.advance(60)
        await ws.send_json({"type": "talk", "text": "hoe gaat het?"})
        await _next(ws, "say")
        items = await _log(ws)
        kinds = [i["type"] for i in items]
        assert kinds[0] == "talk" and "notice" in kinds and "done" in kinds
        assert kinds.index("notice") < kinds.index("done")  # the newest on top
        talk = items[0]
        assert talk["question"] == "hoe gaat het?" and talk["answer"]
        notice = next(i for i in items if i["type"] == "notice")
        assert notice["open"] and notice["actions"] == ["snooze", "dismiss"]
        done = next(i for i in items if i["type"] == "done")
        assert done["outcome"] == "executed" and done["id"] and done["undo"] is True
        assert done["saw"] is not None and done["thought"] and done["did"]  # level 3
        assert "details" not in done
        await ws.send_json({"type": "why", "id": done["id"]})
        assert "omdat" in (await _next(ws, "say"))["text"]
        await ws.send_json({"type": "undo", "id": done["id"]})
        await _next(ws, "say")
        assert ha.states["light.kitchen"].state == "off"
        done = next(i for i in await _log(ws) if i["type"] == "done" and i["id"] == done["id"])
        assert done["undo"] is False  # once undone, no second Undo
        await ws.close()
    await hm.stop()


async def test_how_much_each_line_shows_follows_the_level(tmp_path: Path) -> None:
    """Level 2: one sentence per line and nothing left aside; level 4: also the details (the
    trace). Someone else's conversations never show."""
    hm, _clock, _ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await hm.consider(kitchen_light_on())
    await hm.db.execute(
        "INSERT INTO conversations(at, user_id, question, answer) VALUES(?,?,?,?)",
        (hm.clock.now().isoformat(), "demo-user-2", "iets van een ander", "antwoord"),
    )
    two = (await logbook(hm, await viewer(hm, 2), "demo-user-1", "nl"))["items"]
    done = next(i for i in two if i["type"] == "done")
    assert "saw" not in done and "details" not in done and done["text"]
    assert all(i.get("outcome") in ("executed", "proposed") for i in two if i["type"] == "done")
    assert not any(i["type"] == "talk" for i in two)
    four = (await logbook(hm, await viewer(hm, 4), "demo-user-1", "nl"))["items"]
    done = next(i for i in four if i["type"] == "done")
    assert done["details"]
    await hm.stop()


async def test_the_logbook_keeps_every_decision_also_after_a_restart(tmp_path: Path) -> None:
    """QA 2 (H-7, B-180): the logbook was made from the last 100 thoughts in memory – empty
    after a restart, and emergencies, Do it and Undo never in it. Now it comes from the
    database. What HouseMind only watched stays apart, and the same thing watched does not
    come back every minute (B-181)."""
    hm, clock, _ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    done = await hm.consider(kitchen_light_on())
    assert done is not None and done.executed
    hm.status.thoughts.clear()  # as after a restart: the brain's memory is empty
    hm.recent.clear()
    log = await logbook(hm, await viewer(hm, 4), "demo-user-1", "nl")
    lines = [i for i in log["items"] if i["type"] == "done"]
    assert [i["outcome"] for i in lines] == ["executed"] and lines[0]["id"] is not None
    # Why? works from the database too.
    assert await hm.why_stored(int(lines[0]["id"]), 4, "nl")
    # Only watched, twice in a minute: one thought.
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.OBSERVE
    hm._last_request.clear()
    for _ in range(2):
        await hm.consider(kitchen_light_on())
        await clock.advance(60)
        hm._last_request.clear()
    watched = [d for d in hm.status.thoughts if d.outcome.value == "observed"]
    assert len(watched) == 1
    await hm.stop()


async def test_a_period_shows_older_days_as_far_as_the_house_keeps_them(tmp_path: Path) -> None:
    """The administrator's wish (E-323): Logboek with search, Filter and Groeperen as in Brein,
    and a period. By default the last two days, as always; Laatste 7 dagen, Laatste 30 dagen
    and one chosen day show older lines – asked from the database, through the same message
    the app sends – each line with its part for Filter. Older than the 30 days the house keeps
    is not there."""
    hm, clock, _ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    done = await hm.consider(kitchen_light_on())
    assert done is not None and done.executed
    hm.status.thoughts.clear()
    hm.recent.clear()
    then = clock.now() - timedelta(days=5)
    await hm.db.execute("UPDATE decisions SET at = ?", (then.isoformat(),))
    v = await viewer(hm, 3)

    async def lines(choice: str | None = None, day: str | None = None) -> list[dict[str, Any]]:
        log = await logbook(hm, v, "demo-user-1", "nl", choice, day)
        return [i for i in log["items"] if i["type"] == "done"]

    assert await lines() == []  # the last two days: not there
    week = await lines("week")
    assert [i["outcome"] for i in week] == ["executed"] and week[0]["part"] == "lighting"
    assert len(await lines("month")) == 1
    local_day = week[0]["day"]
    assert len(await lines("day", local_day)) == 1
    before = (date.fromisoformat(local_day) - timedelta(days=1)).isoformat()
    assert await lines("day", before) == []
    assert await lines("today") == [] and await lines("yesterday") == []
    log = await logbook(hm, v, "demo-user-1", "nl", "month")
    today = date.fromisoformat(log["today"])
    assert log["period"] == "month" and log["oldest"] == (today - timedelta(days=29)).isoformat()
    # The app asks it with the period in the same message.
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await _next(ws, "snapshot")
        await ws.send_json({"type": "get", "what": "logbook", "period": "week"})
        while True:
            message = await _next(ws, "data")
            if message["what"] == "logbook":
                break
        assert message["data"]["period"] == "week"
        assert any(i["type"] == "done" for i in message["data"]["items"])
    await hm.stop()


async def test_an_administrator_sees_what_went_to_the_ai_and_what_came_back(
    tmp_path: Path,
) -> None:
    """The administrator's wish (E-348): to be better informed about AI. Each question to an
    AI is a line in Logboek, with what was sent – the neutral names the AI got – and what came
    back, or the error; Soort › AI in Filter. Only for an administrator on Expert, like the AI
    settings: another resident never sees it (QA 3: one place). It follows the chosen period."""
    hm, clock, _ha = await start(tmp_path)
    sent = '<data>{"lamp": "lamp 3", "room": "kamer 2"}</data>'
    await hm.db.execute(
        "INSERT INTO ai_log(at, purpose, provider, model, sent, received, input_tokens, "
        "output_tokens, cost, ok) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            clock.now().isoformat(),
            "vesta",
            "anthropic",
            "claude-haiku-5-5",
            sent,
            '{"text": "Ik deed lamp 3 aan."}',
            120,
            20,
            0.0003,
            1,
        ),
    )
    old = (clock.now() - timedelta(days=5)).isoformat()
    await hm.db.execute(
        "INSERT INTO ai_log(at, purpose, provider, model, sent, received, input_tokens, "
        "output_tokens, cost, ok) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (old, "rule", "anthropic", "claude-haiku-5-5", "<data>{}</data>", "timeout", 0, 0, 0, 0),
    )
    admin = await viewer(hm, 4)
    assert admin.is_admin
    log = await logbook(hm, admin, "demo-user-1", "nl")
    ai = [i for i in log["items"] if i["type"] == "ai"]
    assert len(ai) == 1  # the last two days
    assert ai[0]["purpose"] == "vesta" and ai[0]["sent"] == sent and ai[0]["ok"] is True
    assert ai[0]["received"] == '{"text": "Ik deed lamp 3 aan."}' and ai[0]["cost"] == 0.0003
    week = await logbook(hm, admin, "demo-user-1", "nl", "week")
    failed = [i for i in week["items"] if i["type"] == "ai" and not i["ok"]]
    assert [i["received"] for i in failed] == ["timeout"]  # the error, in its period
    admin.is_admin = False  # a resident who does not administer the house
    log = await logbook(hm, admin, "demo-user-1", "nl", "week")
    assert not [i for i in log["items"] if i["type"] == "ai"]
    await hm.stop()
