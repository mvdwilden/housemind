from __future__ import annotations

from pathlib import Path

from housemind.memory.checkpoint import checkpoint
from housemind.memory.database import Database


async def test_checkpoint_empties_wal(tmp_path: Path) -> None:
    path = tmp_path / "h.db"
    db = Database(path)
    await db.open()
    await db.set_setting("x", 1)
    checkpoint(path)
    wal = path.with_name(path.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0
    assert await db.get_setting("x") == 1
    await db.close()
    checkpoint(tmp_path / "missing.db")  # no error
