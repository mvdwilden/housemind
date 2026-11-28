"""QA 2, group 1, part 2 – what HouseMind keeps and shows (M-5, M-6, M-9, M-10, L5, L6). Each
test fails without its fix; what it checks is its docstring."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from aiohttp.test_utils import TestClient, TestServer

from housemind.brain import start_over
from housemind.memory.database import Database
from housemind.server import views
from housemind.server.web import create_app
from tests.test_notify import start as notify_start
from tests.test_outbound import FLOWS
from tests.test_safety_gaps import _house
from tests.test_start_over import _hello, _next, _snapshot

ROOT = Path(__file__).resolve().parents[1]
RESIDENT = {"X-Remote-User-Id": "demo-user-2"}


async def test_the_factory_settings_also_take_the_copies_and_snapshots(tmp_path: Path) -> None:
    """ "Dan verdwijnt alles" (E-274): the copies of the database from before an update and the
    daily snapshots of what was learned go too, not only the database."""
    hm, _ha = await _house(tmp_path)
    data = hm.paths.data
    (data / "snapshots").mkdir(exist_ok=True)
    (data / "snapshots" / "2026-10-18.json").write_text("{}")
    (data / "housemind.v18.bak").write_text("old")
    (data / "housemind.pre-migration").write_text("old")
    await hm.stop()
    start_over.wipe(hm.paths)
    assert not (data / "snapshots").exists()
    assert (
        not list(data.glob("housemind.v*.bak")) and not (data / "housemind.pre-migration").exists()
    )


async def test_a_copy_from_before_an_update_goes_after_30_days(tmp_path: Path) -> None:
    """A copy from before an update holds everything of then: it goes after the same 30 days
    as the rest; a newer one stays for a failed update (E-301)."""
    db = Database(tmp_path / "housemind.db")
    await db.open()
    old, new = tmp_path / "housemind.v17.bak", tmp_path / "housemind.v18.bak"
    old.write_text("old")
    new.write_text("new")
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    month_ago = (now - timedelta(days=31)).timestamp()
    os.utime(old, (month_ago, month_ago))
    os.utime(new, (now.timestamp(), now.timestamp()))
    removed = await db.cleanup(now)
    assert removed["copies"] == 1 and not old.exists() and new.exists()
    await db.close()


async def test_no_raw_events_and_no_empty_home_and_away(tmp_path: Path) -> None:
    """Nothing read the raw event rows, and they held who was home and where: HouseMind no
    longer keeps them. "My data" no longer promises a home-and-away list that is always empty."""
    hm, ha = await _house(tmp_path)
    await ha.simulate("person.resident_1", "not_home")
    await ha.simulate("light.hall", "on")
    row = await hm.db.fetchone("SELECT count(*) AS n FROM events")
    assert row is not None and row["n"] == 0
    assert "home_and_away" not in await hm.export_me("demo-user-1")
    await hm.stop()


async def test_old_thumbs_go_and_an_administrator_can_forget_someone_who_left(
    tmp_path: Path,
) -> None:
    """Old thumbs (from before they left the app) go after 30 days with the decisions. Someone
    who is no longer a user in Home Assistant can no longer choose "Vergeet mij": an
    administrator can, after a yes; another resident cannot."""
    hm, _ha = await _house(tmp_path)
    long_ago = (hm.clock.now() - timedelta(days=40)).isoformat()
    await hm.db.execute(
        "INSERT INTO thumbs(at, decision_id, user_id, part, good, learned) VALUES(?,?,?,?,?,?)",
        (long_ago, 1, "demo-user-1", "lighting", 1, None),
    )
    await hm.db.cleanup(hm.clock.now())
    assert (await hm.db.fetchone("SELECT count(*) AS n FROM thumbs"))["n"] == 0
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("gone-user", "Oud-bewoner", 2, json.dumps({}), hm.clock.now().isoformat()),
    )
    assert await views.departed(hm) == [{"id": "gone-user", "name": "Oud-bewoner"}]
    for headers, gone in ((RESIDENT, False), (None, True)):
        async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
            ws = await (_hello(client, headers) if headers else _hello(client))
            await _snapshot(ws)
            await ws.send_json({"type": "forget_person", "id": "gone-user"})
            await ws.send_json({"type": "set_pref", "key": "timing", "value": "normal"})
            await _next(ws, "saved")
            await ws.close()
        row = await hm.db.fetchone("SELECT 1 FROM people WHERE user_id = 'gone-user'")
        assert (row is None) is gone
    await hm.stop()


async def test_an_important_message_is_not_readable_on_a_locked_phone(tmp_path: Path) -> None:
    """ "Nobody home, the front door is open" shows on a locked phone without its text
    (private); an emergency stays readable for everyone."""
    hm, clock, ha = await notify_start(tmp_path, {1: 3, 2: 3, 3: 3})
    for i in (1, 2, 3):
        await ha.simulate(f"person.resident_{i}", "not_home")
    await clock.advance(11 * 60)
    await hm.tick()
    await ha.simulate("binary_sensor.front_door", "on")
    await hm.tick()
    door = [n for n in ha.notifications if "staat open" in n[2]]
    assert door and all(n[3].get("visibility") == "private" for n in door)
    await ha.simulate("binary_sensor.kitchen_smoke", "on")
    smoke = [n for n in ha.notifications if n[3].get("push", {}).get("interruption-level")]
    assert smoke and all("visibility" not in n[3] for n in smoke)
    await hm.stop()


def test_the_privacy_statement_says_what_is_on_by_default_and_who_else_to_tell() -> None:
    """ "Standaard niets" was not true – knowledge updates are on by default – and the statement
    said nothing about guests or cameras that see the street (E-165). In every language."""
    updates = next(f for f in FLOWS if f.key == "updates")
    for lang in ("nl", "en", "de", "fr"):
        texts = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        first = texts["privacy"]["out"].split("\n")[0].lower()
        assert updates.privacy[lang].lower().split()[0][:6] in first, lang
        see = texts["privacy"]["see"]
        assert len(see.split("\n")) == 4 and "{name}" not in see, lang
