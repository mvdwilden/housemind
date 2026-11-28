"""The HouseMind database (H6): one SQLite file in ``/data``, WAL mode, versioned schema.

Migrations run at start inside one transaction. Before migrating, HouseMind copies the
database file; if a migration fails, the copy is put back (H6.5).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from housemind.memory.migrations import FIRST, MIGRATIONS

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")


# table, which rows, kept for how many days (E-18, H6.3)
KEEP_DAYS = 30  # raw events, decisions, conversations … (E-18)
KEEP_LONGER = (
    ("proposals", "status != 'open' AND at < ?", 90),
    ("notifications", "resolved_at IS NOT NULL AND resolved_at < ?", 30),
    ("corrections", "at < ?", 90),
    ("reports", "at < ?", 731),
    ("energy_days", "day < ?", 731),
)


class MigrationError(RuntimeError):
    """The database could not be brought to this version: ``failed`` (the old data is back)
    or ``newer`` (it comes from a newer HouseMind)."""

    def __init__(self, message: str, reason: str = "failed") -> None:
        super().__init__(message)
        self.reason = reason


class Database:
    """Async-friendly wrapper around one SQLite connection.

    SQLite work runs in a worker thread; a lock keeps access serial.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self.settings_changed = 0  # how often a setting was written, to know a picture is old
        # The settings as they are, in memory (code QA 3, punt 1): one screen update asked the
        # database 8 to 23 times. Key → its JSON, None when it is not there. Every write that
        # can touch the settings table empties it.
        self._settings: dict[str, str | None] = {}
        self._settings_gen = 0  # raised by every write: a read from before it is not kept
        self.energy_written = 0  # writes that may touch the energy tables (code QA 3, punt 18)

    # -- lifecycle ----------------------------------------------------------------------

    async def open(self) -> int:
        """Open (and migrate) the database. Returns the schema version."""
        self._settings.clear()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return await asyncio.to_thread(self._open_sync)

    async def open_scratch(self, path: Path) -> None:
        """The safe mode (E-301): the same object, now on an empty scratch file, so everything
        that holds this database keeps working while the real data stays untouched. A
        statement that fails there is skipped: the scratch must open whatever happened."""
        self.path = path
        self._settings.clear()
        for name in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            name.unlink(missing_ok=True)

        def run() -> None:
            self._conn = self._connect()
            for statements in MIGRATIONS:
                for statement in statements:
                    try:
                        self._conn.execute(statement)
                    except sqlite3.Error:
                        _LOGGER.warning("Safe mode: skipped a broken statement")
            self._conn.execute(f"PRAGMA user_version={latest()}")

        await asyncio.to_thread(run)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        return conn

    def _open_sync(self) -> int:
        existed = self.path.exists()
        self._conn = self._connect()
        current = self._version(self._conn)
        target = latest()
        if 0 < current < FIRST:
            # From before HouseMind 0.2.0, whose first steps are merged (code QA 3, punt 20):
            # it stays untouched, as a newer one does.
            self._conn.close()
            self._conn = None
            raise MigrationError(
                f"database schema {current} is older than HouseMind 0.2.0 ({FIRST})", "older"
            )
        if current > target:
            self._conn.close()
            self._conn = None
            raise MigrationError(
                f"database schema {current} is newer than this HouseMind version ({target})",
                "newer",
            )
        if current == target:
            return current
        # The copy carries the old version's number; the last two are kept (E-301).
        backup = self.path.with_name(f"housemind.v{current}.bak")
        if existed:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            shutil.copy2(self.path, backup)
            self._prune_backups()
        try:
            self._conn.execute("BEGIN")
            # A new database gets the schema of 0.2.0 at once, then every step after it.
            for step in MIGRATIONS[0 if current == 0 else current - FIRST + 1 :]:
                for statement in step:
                    _apply(self._conn, statement)
            self._conn.execute(f"PRAGMA user_version={target}")
            self._conn.execute("COMMIT")
        except sqlite3.Error as err:
            self._conn.execute("ROLLBACK")
            self._conn.close()
            self._conn = None
            if existed and backup.exists():
                shutil.copy2(backup, self.path)
            raise MigrationError(f"migration to schema {target} failed: {err}", "failed") from err
        _LOGGER.info("Database migrated from schema %s to %s", current, target)
        return target

    def _prune_backups(self) -> None:
        def number(path: Path) -> int:
            try:
                return int(path.name.split(".")[1][1:])
            except (IndexError, ValueError):
                return -1

        copies = sorted(self.path.parent.glob("housemind.v*.bak"), key=number)
        for old in copies[:-2]:
            old.unlink(missing_ok=True)

    @staticmethod
    def _version(conn: sqlite3.Connection) -> int:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])

    async def close(self) -> None:
        self._settings.clear()
        if self._conn is not None:
            conn = self._conn
            self._conn = None
            await asyncio.to_thread(conn.close)

    async def checkpoint(self) -> None:
        """Write everything into the main file (before an HA backup, H6.6)."""
        await self._run(lambda c: c.execute("PRAGMA wal_checkpoint(TRUNCATE)"))

    # -- generic access -----------------------------------------------------------------

    async def _run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        if self._conn is None:
            raise RuntimeError("database is not open")
        conn = self._conn
        async with self._lock:
            return await asyncio.to_thread(fn, conn)

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        def run(conn: sqlite3.Connection) -> int:
            return conn.execute(sql, tuple(params)).rowcount

        try:
            return await self._run(run)
        finally:
            self._forget_settings(sql)

    def _forget_settings(self, sql: str | None = None) -> None:
        """A write that can touch the settings: read them from the database again. And one
        that can touch the energy tables: what was read of them is old."""
        lower = sql.lower() if sql is not None else None
        if lower is None or "settings" in lower:
            self._settings_gen += 1
            self._settings.clear()
        if lower is None or "energy_" in lower:
            self.energy_written += 1

    async def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        def run(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN")
            try:
                conn.executemany(sql, [tuple(r) for r in rows])
                conn.execute("COMMIT")
            except sqlite3.Error:
                conn.execute("ROLLBACK")
                raise

        try:
            await self._run(run)
        finally:
            self._forget_settings(sql)

    async def transaction(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run ``fn`` as one whole: everything or, on an error, nothing."""

        def run(conn: sqlite3.Connection) -> T:
            conn.execute("BEGIN")
            try:
                result = fn(conn)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return result

        try:
            return await self._run(run)
        finally:
            self._forget_settings()  # a transaction may write anything, the settings too

    async def execute_insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run one INSERT and return the new row id."""

        def run(conn: sqlite3.Connection) -> int:
            return int(conn.execute(sql, tuple(params)).lastrowid or 0)

        try:
            return await self._run(run)
        finally:
            self._forget_settings(sql)

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        def run(conn: sqlite3.Connection) -> list[sqlite3.Row]:
            return list(conn.execute(sql, tuple(params)).fetchall())

        return await self._run(run)

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        def run(conn: sqlite3.Connection) -> sqlite3.Row | None:
            row: sqlite3.Row | None = conn.execute(sql, tuple(params)).fetchone()
            return row

        return await self._run(run)

    # -- settings (key/value) -----------------------------------------------------------

    async def get_setting(self, key: str, default: Any = None) -> Any:
        if key in self._settings:
            raw = self._settings[key]
        else:
            gen = self._settings_gen
            row = await self.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
            raw = str(row["value"]) if row else None
            if gen == self._settings_gen:  # nothing was written meanwhile
                self._settings[key] = raw
        # A fresh object each time: whoever changes what it read does not change the memory.
        return json.loads(raw) if raw is not None else default

    async def set_setting(self, key: str, value: Any) -> None:
        self.settings_changed += 1
        self._settings_gen += 1
        self._settings.pop(key, None)
        raw = json.dumps(value)

        def run(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, raw),
            )

        await self._run(run)  # only this key changes in memory, the others stay
        self._settings[key] = raw

    # -- retention (E-18, H6.3) ---------------------------------------------------------

    async def cleanup(self, now: datetime, keep_days: int = KEEP_DAYS) -> dict[str, int]:
        """Remove raw data older than ``keep_days``. Learned summaries are kept."""
        cutoff = (now - timedelta(days=keep_days)).isoformat()
        removed = {}
        for table in (
            "events",
            "decisions",
            "conversations",
            "presence_history",
            "ai_log",
            "detections",
            "part_changes",  # parts turned off and on (E-400)
            "alarm_log",  # the alarm on, off and gone off (E-533)
            "thumbs",  # from before they left the app (B-505): gone after the same days
        ):
            removed[table] = await self.execute(f"DELETE FROM {table} WHERE at < ?", (cutoff,))  # noqa: S608
        removed["energy_quarters"] = await self.execute(
            "DELETE FROM energy_quarters WHERE start < ?", (cutoff,)
        )
        removed["spoken"] = await self.execute("DELETE FROM spoken WHERE at < ?", (cutoff,))
        # What Vesta said to whom goes after 30 days like the conversations (E-300); the last
        # line per person and topic stays, so what was told once is not told again.
        removed["vesta_said"] = await self.execute(
            "DELETE FROM vesta_said WHERE at < ? AND id NOT IN "
            "(SELECT max(id) FROM vesta_said GROUP BY user_id, topic)",
            (cutoff,),
        )
        removed["day_notes"] = await self.execute(
            "DELETE FROM day_notes WHERE day < ?", (cutoff[:10],)
        )
        # What was answered or settled is kept a little longer (the weekly report, S-01, the
        # energy comparison with last year), then it goes too. The newest row always stays,
        # so a number is never given out twice.
        for table, where, days in KEEP_LONGER:
            before = (now - timedelta(days=days)).isoformat()
            removed[table] = await self.execute(
                f"DELETE FROM {table} WHERE {where} AND id < (SELECT max(id) FROM {table})"  # noqa: S608
                if table in ("proposals", "notifications")
                else f"DELETE FROM {table} WHERE {where}",  # noqa: S608
                (before[:10] if table == "energy_days" else before,),
            )
        await self._run(lambda c: c.execute("PRAGMA incremental_vacuum"))
        # A copy from before an update holds everything of then: it goes after the same 30
        # days (QA 2, M-5); a failed update is noticed long before that (E-301).
        old_copies = [
            copy
            for copy in self.path.parent.glob("housemind.v*.bak")
            if copy.stat().st_mtime < (now - timedelta(days=keep_days)).timestamp()
        ]
        for copy in old_copies:
            copy.unlink(missing_ok=True)
        removed["copies"] = len(old_copies)
        return removed


def latest() -> int:
    """The schema version this HouseMind writes: the merged start, then one per step."""
    return FIRST + len(MIGRATIONS) - 1


def _apply(conn: sqlite3.Connection, statement: str) -> None:
    """One migration statement. A column that is already there counts as added: SQLite has no
    "ADD COLUMN IF NOT EXISTS", and a migration may run again over a schema that has it (an
    older version number on a newer file); everything else still fails the migration."""
    try:
        conn.execute(statement)
    except sqlite3.OperationalError as err:
        adds_column = statement.lstrip().upper().startswith("ALTER TABLE") and "ADD COLUMN" in (
            statement.upper()
        )
        if not (adds_column and "duplicate column name" in str(err)):
            raise
