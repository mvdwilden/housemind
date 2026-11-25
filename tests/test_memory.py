from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from housemind.memory import database as database_module
from housemind.memory.database import Database, MigrationError

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


async def test_open_creates_schema_and_settings(tmp_path: Path) -> None:
    db = Database(tmp_path / "housemind.db")
    version = await db.open()
    assert version == database_module.latest()
    await db.set_setting("energy_goal", "lowest_cost")
    assert await db.get_setting("energy_goal") == "lowest_cost"
    assert await db.get_setting("missing", 42) == 42
    await db.close()
    # Reopening does not migrate again.
    db2 = Database(tmp_path / "housemind.db")
    assert await db2.open() == version
    assert await db2.get_setting("energy_goal") == "lowest_cost"
    await db2.close()


async def test_cleanup_removes_only_old_raw_data(tmp_path: Path) -> None:
    db = Database(tmp_path / "h.db")
    await db.open()
    old = (NOW - timedelta(days=31)).isoformat()
    recent = (NOW - timedelta(days=2)).isoformat()
    await db.executemany(
        "INSERT INTO events(at, entity_id, new_state, source) VALUES(?, ?, ?, ?)",
        [(old, "light.a", "on", "physical"), (recent, "light.a", "off", "person")],
    )
    await db.execute(
        "INSERT INTO skill_counts VALUES(?, ?, ?, ?, ?, ?)",
        ("light_on_entry", "kitchen", "wd-18:00", "on", 3.0, old),
    )
    removed = await db.cleanup(NOW)
    assert removed["events"] == 1
    rows = await db.fetchall("SELECT new_state FROM events")
    assert [r["new_state"] for r in rows] == ["off"]
    # Learned summaries stay.
    assert await db.fetchone("SELECT * FROM skill_counts") is not None
    await db.close()


async def test_settled_proposals_and_notifications_go_after_their_keeping_time(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "h.db")
    await db.open()
    long_ago = (NOW - timedelta(days=120)).isoformat()
    for status in ("answered", "open", "answered"):
        await db.execute(
            "INSERT INTO proposals(at, entity_id, action, data, reason, status) "
            "VALUES(?,?,?,?,?,?)",
            (long_ago, "light.a", "light.turn_on", "{}", "skills.x", status),
        )
    for resolved in (long_ago, None, long_ago):
        await db.execute(
            "INSERT INTO notifications(at, kind, topic, text_key, params, severity, resolved_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (long_ago, "info", "t", "notify.x", "{}", 1, resolved),
        )
    await db.cleanup(NOW)
    # The open proposal and the unresolved notification stay, and so does the newest row.
    proposals = await db.fetchall("SELECT id, status FROM proposals ORDER BY id")
    assert [(r["id"], r["status"]) for r in proposals] == [(2, "open"), (3, "answered")]
    notifications = await db.fetchall("SELECT id FROM notifications ORDER BY id")
    assert [r["id"] for r in notifications] == [2, 3]
    await db.close()


async def test_failed_migration_restores_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.db"
    db = Database(path)
    await db.open()
    await db.set_setting("keep", True)
    await db.close()
    broken = [*database_module.MIGRATIONS, ["CREATE TABLE settings (x TEXT)"]]
    monkeypatch.setattr(database_module, "MIGRATIONS", broken)
    with pytest.raises(MigrationError):
        await Database(path).open()
    monkeypatch.setattr(database_module, "MIGRATIONS", broken[:-1])
    db3 = Database(path)
    await db3.open()
    assert await db3.get_setting("keep") is True
    await db3.close()


async def test_newer_schema_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "h.db"
    db = Database(path)
    await db.open()
    await db.close()
    monkeypatch.setattr(database_module, "MIGRATIONS", [])
    with pytest.raises(MigrationError):
        await Database(path).open()
