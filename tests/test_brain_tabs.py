"""Brein in tabjes (E-398, B-316): the tab someone had open last is theirs, also on another
device; a tab that does not exist is not kept. Which tabs a level has is in the frontend
(lib/braintabs.test.ts)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from housemind.server.web import create_app
from tests.test_safety_gaps import _house
from tests.test_start_over import ADMIN, _hello, _snapshot


async def test_the_open_brain_tab_is_kept_per_person(tmp_path: Path) -> None:
    hm, _ha = await _house(tmp_path)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 4, json.dumps({}), hm.clock.now().isoformat()),
    )
    await hm._refresh_people()
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, ADMIN)
        assert (await _snapshot(ws))["prefs"]["brainTab"] == "readiness"  # the first tab
        await ws.send_json({"type": "set_pref", "key": "brain_tab", "value": "devices"})
        await ws.send_json({"type": "set_pref", "key": "brain_tab", "value": "kitchen"})  # no tab
        for _ in range(100):  # until the server has it
            row = await hm.db.fetchone(
                "SELECT preferences FROM people WHERE user_id = 'demo-user-1'"
            )
            if row and json.loads(row["preferences"]).get("brain_tab") == "devices":
                break
            await asyncio.sleep(0.02)
        said = []  # choosing a tab is no setting: no "Bewaard"
        try:
            while True:
                said.append((await ws.receive_json(timeout=0.3))["type"])
        except TimeoutError:
            pass
        assert "saved" not in said
        await ws.close()
        # Another device of the same person: the same tab opens.
        other = await _hello(client, ADMIN)
        snap = await _snapshot(other)
        assert snap["prefs"]["brainTab"] == "devices"
        await other.close()
    await hm.stop()
