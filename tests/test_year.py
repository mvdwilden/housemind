"""A whole year in the time machine (E-99, E-158, H12.1 simulation): the database stays bounded.

Slow (minutes), so it only runs on request: ``uv run pytest -m slow``. Every month the size of
the database file and the rows per table are written down. After the first months (30 days of
raw data plus what HouseMind learned) the file must stop growing: the daily cleanup (E-18)
keeps it flat for the rest of the year.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from housemind.brain.proposals import Answer
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.demo.timemachine import standard_routine
from housemind.memory.database import KEEP_LONGER
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
START = datetime(2026, 1, 5, 0, 0, tzinfo=TZ)  # a Monday, so the year has every season
DAYS = 365
MAX_MB = 25.0  # on a Raspberry Pi the database must stay small (E-47, E-158)


def _measure(path: Path) -> tuple[float, dict[str, int]]:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        rows = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    return path.stat().st_size / 1_000_000, rows


@pytest.mark.slow
@pytest.mark.parametrize("key", ["family_home", "energy_home"])
async def test_a_year_keeps_the_database_bounded(key: str, tmp_path: Path) -> None:
    clock = SimulatedClock(START.astimezone(UTC))
    house = load_demo_house(key, clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-1", "Resident", 3, clock.now().isoformat()),
    )
    await hm._refresh_people()
    routine = standard_routine(house, TZ)
    rng = random.Random(11)
    months: list[tuple[int, float, dict[str, int]]] = []
    for day in range(DAYS):
        date = START + timedelta(days=day)
        pending = sorted(routine(date.date(), rng))
        until = (date + timedelta(days=1)).astimezone(UTC)
        while clock.now() < until:
            next_tick = clock.now() + timedelta(minutes=5)
            while pending and pending[0].at <= next_tick:
                step = pending.pop(0)
                if step.at > clock.now():
                    await clock.advance((step.at - clock.now()).total_seconds())
                await house.ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)
            if next_tick > clock.now():
                await clock.advance((next_tick - clock.now()).total_seconds())
            await hm.tick()
            for proposal in await hm.open_proposals():
                answer = Answer.DO if rng.random() < 0.9 else Answer.NOT_NOW
                await hm.answer(proposal.id, answer, "demo-user-1")
        if (day + 1) % 30 == 0 or day + 1 == DAYS:
            size, rows = _measure(hm.paths.data / "housemind.db")
            months.append((day + 1, size, rows))

    report = "\n".join(
        f"day {d:3}: {s:6.2f} MB  " + " ".join(f"{t}={n}" for t, n in sorted(r.items()) if n)
        for d, s, r in months
    )
    print(f"\n{key}\n{report}")
    _, after_three, _ = next(m for m in months if m[0] == 90)
    _, at_end, _ = months[-1]
    assert at_end <= MAX_MB, report
    # Flat after the first months: at most a quarter more than after three months.
    assert at_end <= after_three * 1.25 + 0.2, report
    # Nothing is older than its keeping time (E-18): raw history 30 days, the rest as listed.
    now = clock.now()
    month_ago = (now - timedelta(days=31)).isoformat()
    for table in ("events", "decisions", "conversations", "presence_history", "ai_log", "spoken"):
        old = await hm.db.fetchone(f"SELECT count(*) AS n FROM {table} WHERE at < ?", (month_ago,))
        assert old is not None and old["n"] == 0, table
    for table, where, days in KEEP_LONGER:
        before = (now - timedelta(days=days + 1)).isoformat()
        old = await hm.db.fetchone(
            f"SELECT count(*) AS n FROM {table} WHERE {where}",
            (before[:10] if table == "energy_days" else before,),
        )
        assert old is not None and old["n"] <= 1, table  # the newest row always stays
    # The list of open notifications does not pile up (information leaves after a week).
    still_open = await hm.notifications()
    assert len(still_open) <= 20, sorted({str(n["topic"]).split(":")[0] for n in still_open})
    await hm.stop()
