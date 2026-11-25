"""When an update fails (E-301, 20.22): the old data is back and stays untouched, HouseMind
starts in the safe mode where only the emergency reactions work, the app says so, and the
administrators hear it in Home Assistant. Each test fails without the build."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.memory import migrations
from housemind.memory.database import Database, MigrationError
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from tests.test_house_modes import NOON, ROOT


async def _old_house(data: Path) -> None:
    """A house that ran before: its own data, at the schema of now."""
    db = Database(data / "housemind.db")
    await db.open()
    await db.set_setting("vesta_name", "Hestia")
    await db.set_setting("setup", "done")
    await db.close()


async def _start(data: Path) -> tuple[HouseMind, object]:
    clock = SimulatedClock(NOON)
    house = load_demo_house("family_home", clock, "nl")
    house.ha.notify_services.add("persistent_notification")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], data), set())
    await hm.start(autorun=False)
    return hm, house.ha


async def test_a_failed_conversion_starts_the_safe_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _old_house(tmp_path)
    broken = [*migrations.MIGRATIONS, ["THIS IS NOT SQL"]]
    monkeypatch.setattr("housemind.memory.database.MIGRATIONS", broken)
    hm, ha = await _start(tmp_path)
    assert hm.safe_mode == "failed" and not hm.activated
    assert hm.gate is not None and hm.gate.state.inactive  # nothing switches by itself
    # The old data is back, untouched; HouseMind works on a scratch memory.
    old = Database(tmp_path / "housemind.db")
    monkeypatch.setattr("housemind.memory.database.MIGRATIONS", migrations.MIGRATIONS)
    await old.open()
    assert await old.get_setting("vesta_name") == "Hestia"
    await old.close()
    # The app says it, and the administrators hear it in Home Assistant.
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    snap = await views.snapshot(hm, viewer)
    assert snap["safeMode"] == "failed" and snap["orb"] == "inactive"
    sent = [n for n in ha.notifications if n[0] == "notify.persistent_notification"]  # type: ignore[attr-defined]
    assert sent and sent[0][1] == "HouseMind staat in de veilige stand"
    # The emergency reactions keep working.
    await ha.simulate("binary_sensor.kitchen_smoke", "on")  # type: ignore[attr-defined]
    assert hm.status.emergency is not None
    assert ha.states["light.kitchen"].state == "on"  # type: ignore[attr-defined]
    await hm.stop()


async def test_data_from_a_newer_version_is_left_alone(tmp_path: Path) -> None:
    await _old_house(tmp_path)
    db = Database(tmp_path / "housemind.db")
    await db.open()
    await db.execute("PRAGMA user_version=99")
    await db.close()
    hm, _ = await _start(tmp_path)
    assert hm.safe_mode == "newer"
    raw = Database(tmp_path / "housemind.db")
    with pytest.raises(MigrationError):
        await raw.open()  # still the newer data, never converted down
    await hm.stop()


async def test_the_copy_carries_the_old_version_and_two_are_kept(tmp_path: Path) -> None:
    for version in (22, 23, 24):  # from 0.2.0 on; older ones are not converted (code QA 3)
        db = Database(tmp_path / "housemind.db")
        await db.open()
        await db.execute(f"PRAGMA user_version={version}")
        await db.close()
        db = Database(tmp_path / "housemind.db")
        await db.open()
        await db.close()
    kept = sorted(p.name for p in tmp_path.glob("housemind.v*.bak"))  # noqa: ASYNC240
    assert kept == ["housemind.v23.bak", "housemind.v24.bak"]


async def test_a_database_from_before_0_2_0_is_left_untouched(tmp_path: Path) -> None:
    """The steps of before HouseMind 0.2.0 are one now (code QA 3, punt 20). Data of such an
    old version is not converted half: HouseMind leaves it as it is, in the safe stand, and
    says why – a version that is too old, not a failed update."""
    path = tmp_path / "housemind.db"
    db = Database(path)
    await db.open()
    await db.set_setting("keep", "old")
    await db.execute("PRAGMA user_version=13")
    await db.close()
    with pytest.raises(MigrationError) as raised:
        await Database(path).open()
    assert raised.value.reason == "older"
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT value FROM settings WHERE key = 'keep'").fetchone() == ('"old"',)
    assert conn.execute("PRAGMA user_version").fetchone() == (13,)
    conn.close()
