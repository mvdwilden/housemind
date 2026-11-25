"""Write the database WAL into the main file before an HA backup (H6.6).

Called by the Supervisor through ``backup_pre`` while HouseMind keeps running.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def checkpoint(path: Path) -> None:
    if not path.exists():
        return
    with sqlite3.connect(path, timeout=30) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


if __name__ == "__main__":
    checkpoint(Path(sys.argv[1] if len(sys.argv) > 1 else "/data/housemind.db"))
