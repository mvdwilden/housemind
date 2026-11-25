"""'Mijn gegevens' (E-300, 20.17): what HouseMind keeps about one person, as a file, and
'Vergeet mij'. Each test fails without the build."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.proposals import Answer
from housemind.brain.rules import parse_rule
from housemind.runtime import HouseMind
from housemind.server import views
from housemind.server.web import create_app
from tests.test_house_modes import _question, _start


async def _talked(hm: HouseMind, user_id: str, question: str) -> None:
    await hm.db.execute(
        "INSERT INTO conversations(at, user_id, question, answer) VALUES(?,?,?,?)",
        (hm.clock.now().isoformat(), user_id, question, "Goed."),
    )


async def _house_with_two(tmp_path: Path) -> HouseMind:
    hm, _ = await _start(tmp_path)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-2", "Resident 2", 2, json.dumps({"emergency_push": True}), "2026-10-19"),
    )
    await _talked(hm, "demo-user-1", "Hoe gaat het?")
    await _talked(hm, "demo-user-2", "Wat is mijn geheim?")
    rule = parse_rule("nooit de gordijnen in de woonkamer open", "nl", hm.languages, hm.model)
    assert rule is not None
    await hm.save_rule(rule, "demo-user-1")
    await hm.answer(await _question(hm), Answer.DO, "demo-user-1")
    return hm


async def test_my_data_shows_what_is_kept_about_me(tmp_path: Path) -> None:
    hm = await _house_with_two(tmp_path)
    mine = await hm.my_data("demo-user-1", "nl")
    assert mine["known"] and mine["level"] == 3 and mine["conversations"] == 1
    assert [a["answer"] for a in mine["answers"]] == ["Doe het"]
    assert len(mine["rules"]) == 1 and "raambekleding" in mine["rules"][0]
    theirs = await hm.my_data("demo-user-2", "nl")
    assert theirs["rules"] == [] and theirs["answers"] == []
    await hm.stop()


async def test_the_file_has_only_my_own_data(tmp_path: Path) -> None:
    hm = await _house_with_two(tmp_path)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/my-data", headers={"X-Remote-User-Id": "demo-user-1"})
        assert response.status == 200
        assert "attachment" in response.headers["Content-Disposition"]
        data = await response.json()
    assert data["user"] == "demo-user-1"
    assert [c["question"] for c in data["conversations"]] == ["Hoe gaat het?"]
    assert "geheim" not in json.dumps(data)  # someone else's conversation never
    await hm.stop()


async def test_forget_me_forgets_me_and_keeps_the_house(tmp_path: Path) -> None:
    hm = await _house_with_two(tmp_path)
    await hm.db.execute(
        "INSERT INTO vesta_said(at, user_id, topic, variant, key) VALUES(?,?,?,?,?)",
        (hm.clock.now().isoformat(), "demo-user-1", "calm", 0, "calm.0"),
    )
    await hm.db.execute(
        "INSERT INTO notifications(at, kind, topic, text_key, params, severity, concerns) "
        "VALUES(?,?,?,?,?,?,?)",
        (hm.clock.now().isoformat(), "info", "x", "notify.learned", "{}", 0, "user:demo-user-1"),
    )
    assert await hm.forget_me("demo-user-1")
    for table, column in (
        ("people", "user_id"),
        ("conversations", "user_id"),
        ("vesta_said", "user_id"),
        ("proposals", "answered_by"),
        ("house_rules", "created_by"),
    ):
        row = await hm.db.fetchone(
            f"SELECT count(*) AS n FROM {table} WHERE {column} = ?",
            ("demo-user-1",),
        )
        assert row is not None and row["n"] == 0, table
    gone = await hm.db.fetchone(
        "SELECT count(*) AS n FROM notifications WHERE concerns = ?", ("user:demo-user-1",)
    )
    assert gone is not None and gone["n"] == 0
    # The house keeps the rule and the answer, without the name; the other resident is untouched.
    assert len(hm.rules.rules) == 1
    answered = await hm.db.fetchone("SELECT count(*) AS n FROM proposals WHERE answer = 'do'")
    assert answered is not None and answered["n"] == 1
    assert (await hm.my_data("demo-user-2", "nl"))["conversations"] == 1
    # Next time: new, with a short introduction of one's own.
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    assert not viewer.preferences.get("onboarded")
    await hm.stop()


async def test_what_vesta_said_goes_after_thirty_days_but_told_once_stays(tmp_path: Path) -> None:
    hm, _ = await _start(tmp_path)
    old = (hm.clock.now() - timedelta(days=40)).isoformat()
    for i, topic in enumerate(("calm", "calm", "first_time")):
        await hm.db.execute(
            "INSERT INTO vesta_said(at, user_id, topic, variant, key) VALUES(?,?,?,?,?)",
            (old, "demo-user-1", topic, i, f"{topic}.{i}"),
        )
    await hm.db.cleanup(hm.clock.now())
    rows = await hm.db.fetchall("SELECT topic FROM vesta_said ORDER BY id")
    assert [r["topic"] for r in rows] == ["calm", "first_time"]  # the last per topic stays
    await hm.stop()
