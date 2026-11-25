"""What HouseMind learned, kept every night (E-94, 20.13).

At 04:00 HouseMind writes what it learned – the skills' counts, steps and values, what reading
the history taught, and the rhythm of the house – as one readable file in ``snapshots/``, the
last seven days. An administrator on Expert can put one day back: only what was learned goes
back; settings, house rules and the logbook stay.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from housemind.memory.database import Database

TABLES = (
    "skill_counts",
    "skill_steps",
    "skill_values",
    "skill_seen",
    "skill_earn",
    "history_evidence",
    "history_automations",
)
SETTINGS = ("rhythm", "lights_used", "light_levels")
KEEP = 7
_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def days(folder: Path) -> list[str]:
    """The days there is a snapshot of, newest first."""
    if not folder.is_dir():
        return []
    found = [p.stem for p in folder.glob("*.json") if _NAME.match(p.stem)]
    return sorted(found, reverse=True)


def _write(folder: Path, day: date, payload: dict[str, Any]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day.isoformat()}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
    for old in days(folder)[KEEP:]:
        (folder / f"{old}.json").unlink(missing_ok=True)
    return path


def _read(folder: Path, day: str) -> dict[str, Any] | None:
    path = folder / f"{day}.json"
    if not _NAME.match(day) or not path.is_file():
        return None
    found: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return found


async def take(db: Database, folder: Path, day: date) -> Path:
    """Write the snapshot of this day and keep only the last seven."""
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        rows = await db.fetchall(f"SELECT * FROM {table}")  # noqa: S608 – fixed names
        tables[table] = [dict(row) for row in rows]
    settings = {key: await db.get_setting(key) for key in SETTINGS}
    payload = {"day": day.isoformat(), "tables": tables, "settings": settings}
    return await asyncio.to_thread(_write, folder, day, payload)


async def restore(db: Database, folder: Path, day: str) -> bool:
    """Put back what was learned on that day, as one whole. False when there is no such
    snapshot or it does not fit (then nothing changed)."""
    raw = await asyncio.to_thread(_read, folder, day)
    if raw is None:
        return False
    tables = raw.get("tables") or {}
    settings = raw.get("settings") or {}

    def put_back(conn: sqlite3.Connection) -> None:
        for table in TABLES:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 – fixed names
            rows = tables.get(table) or []
            if not rows:
                continue
            columns = list(rows[0])
            if not all(re.fullmatch(r"[a-z_]+", c) for c in columns):
                raise ValueError(f"unexpected column in {table}")
            marks = ", ".join("?" for _ in columns)
            conn.executemany(
                f"INSERT INTO {table}({', '.join(columns)}) VALUES({marks})",
                [tuple(row.get(c) for c in columns) for row in rows],
            )
        for key in SETTINGS:
            value = settings.get(key)
            if value is None:
                conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )

    try:
        await db.transaction(put_back)
    except (sqlite3.Error, ValueError):
        return False
    return True
