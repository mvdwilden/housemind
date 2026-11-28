"""Part G of "Duidelijk en volledig" (20.21): Vesta speaks as herself – her name above a push
message, in the language Home Assistant shows that person – and the time to talk and read
goes with the person. Each test fails without the build."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import yaml
from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.notify import Kind
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind
from housemind.server.web import create_app
from housemind.world.house_mode import HouseMode
from tests.test_automations import start
from tests.test_vesta_asks import _until

ROOT = Path(__file__).resolve().parents[1]
KITCHEN = ("light_on_entry", "kitchen")
ME = {"X-Remote-User-Id": "demo-user-1"}


async def _did_something(tmp_path: Path, level: int = 3) -> tuple[HouseMind, Any, int]:
    """A house where HouseMind just turned on the kitchen light itself."""
    hm, _clock, ha = await start(tmp_path)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", level, json.dumps({}), hm.clock.now().isoformat()),
    )
    await hm._refresh_people()
    assert hm.world is not None and hm.learning is not None
    hm.world.modes.mode = HouseMode.HOME
    hm.learning.steps[KITCHEN] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    done = [i for i, r in hm.recent.items() if r.decision.executed and r.decision.request.skill]
    assert done and ha.states["light.kitchen"].state == "on"
    return hm, ha, done[-1]


async def _settled(check: Any, wait: float = 3) -> None:
    """A message over the socket is handled a moment later."""
    for _ in range(int(wait * 20)):
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("not handled")


# -- she speaks as herself (E-299, E-170, E-25, 20.21) ---------------------------------------


def _strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in _strings(v, f"{path}.{k}" if path else k)]
    return [(path, node)] if isinstance(node, str) else []


def test_what_she_says_never_calls_her_housemind() -> None:
    """Her messages, answers and reasons said "HouseMind heeft …" – as if someone else did
    it. Now she says "ik" in every language; only the app's own screens name the app."""
    for lang in ("nl", "en", "de", "fr"):
        texts = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        hers = [
            (path, text)
            for top in ("notify", "talk", "gate", "rules", "status", "reason")
            for path, text in _strings(texts.get(top, {}), top)
        ]
        assert len(hers) > 100
        assert [p for p, text in hers if "HouseMind" in text] == [], lang
        vesta = (ROOT / "knowledge" / "vesta" / f"{lang}.yaml").read_text()
        assert "HouseMind" not in vesta


async def test_a_push_message_carries_her_name_in_the_persons_ha_language(
    tmp_path: Path,
) -> None:
    """A push said "HouseMind" above it, in the house's language. Now her name – the one the
    administrator gave her – and the language Home Assistant shows that person, also while
    the app is closed; their own choice in HouseMind still comes first."""
    hm, ha, _decision = await _did_something(tmp_path)
    await hm.set_house_setting("vesta_name", "Hestia")  # the name the administrator gave her
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ME)
        await ws.send_json({"type": "hello", "browserLanguage": "nl", "haLanguage": "en"})

        async def kept() -> bool:
            row = await hm.db.fetchone(
                "SELECT preferences FROM people WHERE user_id = 'demo-user-1'"
            )
            return row is not None and json.loads(row["preferences"]).get("ha_language") == "en"

        await _settled(kept)
        await ws.close()
    me = next(p for p in hm._people if p.user_id == "demo-user-1")
    assert me.language == "en"
    assert me.notify_services, "the demo resident has a phone"
    assert hm.world is not None
    snapshot = hm.world.snapshot(hm.clock.now())
    await hm._raise(
        Kind.IMPORTANT,
        "open:door",
        "notify.open_when_away",
        {"device": "Back door"},
        snapshot,
        concerns="user:demo-user-1",
    )
    pushed = [n for n in ha.notifications if n[0] in me.notify_services]
    assert pushed and pushed[-1][1] == "Hestia"
    assert "HouseMind" not in pushed[-1][1] + pushed[-1][2]
    # Their own choice in HouseMind wins over Home Assistant's.
    await hm.db.execute(
        "UPDATE people SET preferences = ? WHERE user_id = 'demo-user-1'",
        (json.dumps({"language": "de", "ha_language": "en"}),),
    )
    await hm._refresh_people()
    assert next(p for p in hm._people if p.user_id == "demo-user-1").language == "de"
    await hm.stop()


# -- time to talk and to read (E-299, 20.20) -------------------------------------------------


async def test_the_time_to_talk_and_read_is_one_choice_that_goes_with_the_person(
    tmp_path: Path,
) -> None:
    """Six seconds to talk and ten to read were too short for some. Now each person picks
    Normaal, Langer or Lang once, kept with them, so every device uses it."""
    hm, _ha, _decision = await _did_something(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ME)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        assert (await _until(ws, "snapshot"))["snapshot"]["snapshot"]["prefs"]["timing"] == "normal"
        await ws.send_json({"type": "set_pref", "key": "timing", "value": "slow"})  # ignored
        await ws.send_json({"type": "set_pref", "key": "timing", "value": "long"})

        async def kept() -> bool:
            row = await hm.db.fetchone(
                "SELECT preferences FROM people WHERE user_id = 'demo-user-1'"
            )
            return row is not None and json.loads(row["preferences"]).get("timing") == "long"

        await _settled(kept)
        await ws.close()
        other = await client.ws_connect("/api/ws", headers=ME)  # another device of theirs
        await other.send_json({"type": "hello", "browserLanguage": "nl"})
        assert (await _until(other, "snapshot"))["snapshot"]["snapshot"]["prefs"][
            "timing"
        ] == "long"
        await other.close()
    await hm.stop()


def test_a_column_already_there_counts_as_added_and_other_errors_still_fail() -> None:
    """Migration 18 adds a column; over a schema that already has it (an older version number
    on a newer file) that is no failure, anything else still is (E-301)."""
    import sqlite3

    import pytest

    from housemind.memory.database import _apply

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE kept (id INTEGER PRIMARY KEY)")
    _apply(conn, "ALTER TABLE kept ADD COLUMN learned TEXT")
    _apply(conn, "ALTER TABLE kept ADD COLUMN learned TEXT")  # again: already there
    with pytest.raises(sqlite3.OperationalError):
        _apply(conn, "ALTER TABLE nothing ADD COLUMN learned TEXT")
    with pytest.raises(sqlite3.OperationalError):
        _apply(conn, "CREATE TABLE kept (id INTEGER)")
    conn.close()
