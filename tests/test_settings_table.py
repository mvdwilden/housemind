"""Every house setting in one table, and in memory (code QA 3, punt 1) – what an administrator
notices: what Huisinstellingen says is kept, is kept, for every setting – in 0.3.23 the choices
under Beveiliging were not, because the key was missing from one of the lists. And a screen
update no longer asks the database for the settings again and again. Each test fails without
the change; what it checks is its docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from housemind.brain import house_settings, security, settings_file
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.memory.database import Database
from housemind.runtime import HouseMind, Paths
from housemind.server import views, web

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Amsterdam")


async def _house(tmp_path: Path) -> HouseMind:
    clock = SimulatedClock(datetime(2026, 11, 25, 19, 0, tzinfo=TZ).astimezone(UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path / "data"))
    await hm.start(autorun=False)
    return hm


def test_the_lists_come_from_the_one_table() -> None:
    """What a screen may change, what travels in the settings file and what Beveiliging holds
    are made from the one table: a setting cannot be in one and missing from another."""
    assert web.HOUSE_KEYS == house_settings.SCREEN_KEYS
    assert settings_file.SETTING_KEYS == house_settings.FILE_KEYS
    assert security.SETTINGS == house_settings.SECURITY_KEYS
    assert set(security.SETTINGS) <= web.HOUSE_KEYS


async def test_what_huisinstellingen_saves_is_kept_for_every_setting(tmp_path: Path) -> None:
    """Every setting a screen may change, with a good value: saved, and read back as it was
    saved – a level, a tick, a list, Vesta's name and the choices under Beveiliging alike."""
    hm = await _house(tmp_path)
    tried = 0
    for key, setting in house_settings.TABLE.items():
        if not setting.screen or setting.example is None:
            continue
        assert await hm.set_house_setting(key, setting.example), key
        assert await hm.setting(key) == setting.example, key
        tried += 1
    assert tried >= 15
    assert not await hm.set_house_setting("new_person_level", 7)  # a level that is none
    assert not await hm.set_house_setting("vesta_window", "ja")  # a tick is yes or no
    assert not await hm.set_house_setting("no_such_setting", True)
    await hm.stop()


async def test_a_screen_update_reads_the_settings_from_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second picture for a screen asks the database for no setting: they are in memory.
    One that changes is read as it is now."""
    hm = await _house(tmp_path)
    viewer = views.Viewer("demo-user-1", "Resident 1", 4, True, {"language": "nl"})
    await views.snapshot(hm, viewer)
    asked: list[str] = []
    original = Database.fetchone

    async def counted(self: Database, sql: str, params: Any = ()) -> Any:
        if "FROM settings" in sql:
            asked.append(sql)
        return await original(self, sql, params)

    monkeypatch.setattr(Database, "fetchone", counted)
    await views.snapshot(hm, viewer)
    assert asked == []
    await hm.set_house_setting("vesta_window", False)
    assert (await views.house_settings(hm, "nl"))["vestaWindow"] is False
    await hm.stop()


async def test_a_setting_written_another_way_is_read_again(tmp_path: Path) -> None:
    """A day put back from a snapshot, or the factory settings, write the settings table
    themselves: what is in memory goes, and the setting is read as it is in the database."""
    hm = await _house(tmp_path)
    assert await hm.setting("lived_in") is True
    await hm.db.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("lived_in", json.dumps(False)),
    )
    assert await hm.setting("lived_in") is False
    gone = "DELETE FROM settings WHERE key = 'lived_in'"
    await hm.db.transaction(lambda conn: conn.execute(gone))
    assert await hm.setting("lived_in") is True  # its default again
    await hm.stop()
