"""Home Assistant netjes, deel E (E-397, B-314): backups and a full disk. The margin counts in
backups that still fit, the backup tile says what Home Assistant keeps, and old loose backups
Home Assistant does not clear itself are one card with 'Bewust houden'. HouseMind makes and
deletes no backups, and never keeps the password of Home Assistant's backups. Each test fails
without the build."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.brain import tidy_system as ts
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.ha.websocket import WebSocketHomeAssistant, backup_settings
from housemind.runtime import HouseMind, Paths
from housemind.server.tidy_view import tidy_view

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
ADMIN = "demo-user-1"
GB = 2**30
SECRET = "geheim-wachtwoord-123"


def _disk(total: str, used: str) -> dict[str, Any]:
    return {"hassio": {"info": {"disk_total": total, "disk_used": used}}}


def _backup(
    backup_id: str,
    days: float,
    gb: float = 2.2,
    automatic: bool = False,
    ha: bool = True,
    agents: tuple[str, ...] = ("hassio.local",),
) -> dict[str, Any]:
    return {
        "backup_id": backup_id,
        "name": f"Back-up {backup_id}",
        "date": (NOW - timedelta(days=days)).isoformat(),
        "homeassistant_included": ha,
        "with_automatic_settings": automatic,
        "agents": {a: {"protected": True, "size": int(gb * GB)} for a in agents},
    }


def test_the_margin_counts_in_backups_whichever_comes_first() -> None:
    size = 2.344e9
    # The real house: 21.4 GB free, room for 9 more – fine.
    real = ts.disk_tile(_disk("62.3 GB", "40.9 GB"), size)
    assert real["state"] == "ok" and real["room"] == 9
    # 75 % full, but fewer than 4 fit: information before the percentage says so.
    assert ts.disk_tile(_disk("32 GB", "24 GB"), size)["state"] == "filling"
    # 87.5 % full, fewer than 2 fit: important, the next backup may not fit.
    tight = ts.disk_tile(_disk("32 GB", "28 GB"), size)
    assert tight["state"] == "full" and tight["heavy"] and tight["room"] == 1
    # A big disk at 96 %: the percentage comes first.
    assert ts.disk_tile(_disk("1000 GB", "960 GB"), size)["state"] == "full"
    # Without an automatic backup only the percentages, as before.
    assert ts.disk_tile(_disk("32 GB", "24 GB"))["state"] == "ok"
    assert "room" not in ts.disk_tile(_disk("32 GB", "24 GB"))
    assert ts.disk_tile(_disk("100 GB", "90 GB"))["state"] == "filling"


def test_the_measure_is_the_newest_automatic_backup_on_this_device() -> None:
    backups = [
        _backup("a", 1, gb=2.0, automatic=True),
        _backup("b", 0.2, gb=9.0, automatic=True, agents=("cloud.cloud",)),  # only in the cloud
        _backup("c", 0.1, gb=5.0),  # by hand: not the measure while there is an automatic one
    ]
    assert ts.backup_size(backups) == 2.0 * GB
    assert ts.backup_size([_backup("c", 0.1, gb=5.0)]) == 5.0 * GB  # none automatic
    assert ts.backup_size([_backup("d", 1, ha=False)]) is None  # one app: only percentages
    assert ts.backup_size(None) is None
    assert ts.local_size({"size": 1234}) == 1234.0  # an older Home Assistant: one size


def test_what_home_assistant_keeps_and_never_its_password() -> None:
    raw = {
        "automatic_backups_configured": True,
        "create_backup": {"agent_ids": ["hassio.local"], "password": SECRET, "name": None},
        "schedule": {"recurrence": "daily", "days": [], "time": None, "state": "daily"},
        "retention": {"copies": 3, "days": None},
        "agents": {"hassio.local": {"protected": True, "retention": None}},
        "last_completed_automatic_backup": NOW.isoformat(),
    }
    kept = backup_settings(raw)
    assert SECRET not in json.dumps(kept) and "create_backup" not in kept
    keep = ts.retention(kept)
    assert keep == {"recurrence": "daily", "days": [], "copies": 3, "keepDays": None}
    assert not ts.keeps_all(keep)
    # Fixed days, kept 14 days.
    days = {
        "schedule": {"recurrence": "custom_days", "days": ["mon", "thu"]},
        "retention": {"days": 14},
    }
    assert ts.retention(days) == {
        "recurrence": "custom_days",
        "days": ["mon", "thu"],
        "copies": None,
        "keepDays": 14,
    }
    # This device's own rule wins over the general one.
    own = {**kept, "agents": {"hassio.local": {"retention": {"copies": 5, "days": None}}}}
    assert ts.retention(own)["copies"] == 5  # type: ignore[index]
    # No maximum: the disk fills by itself.
    all_kept = {**kept, "retention": {"copies": None, "days": None}}
    assert ts.keeps_all(ts.retention(all_kept))
    # No automatic backups at all: nothing kept, no card.
    never = ts.retention({"schedule": {"recurrence": "never"}, "retention": {}})
    assert never is not None and not ts.keeps_all(never)
    assert ts.retention(None) is None and ts.retention({"schedule": {}}) is None


async def test_reading_the_settings_drops_the_password(tmp_path: Path) -> None:
    client = WebSocketHomeAssistant(SimulatedClock(NOW), "token", url="ws://localhost:1/api")
    sent: list[dict[str, Any]] = []

    async def command(message: dict[str, Any]) -> Any:
        sent.append(message)
        return {
            "config": {"create_backup": {"password": SECRET}, "schedule": {"recurrence": "daily"}}
        }

    client._command = command  # type: ignore[method-assign]
    result = await client.backup_config()
    assert sent == [{"type": "backup/config/info"}]  # reading only
    assert result is not None and SECRET not in json.dumps(result)


def test_old_loose_backups_only_on_this_device_and_not_kept() -> None:
    backups = [
        _backup("auto", 40, automatic=True),  # automatic: Home Assistant clears it itself
        _backup("young", 20),  # younger than 30 days
        _backup("cloud", 90, agents=("cloud.cloud",)),  # takes no room here
        _backup("app", 45, gb=0.4, ha=False),  # before an update of an app
        _backup("hand", 120, gb=1.1),  # made by hand
        _backup("kept", 200),
    ]
    found = ts.loose(backups, NOW, kept=["kept"])
    assert [b["id"] for b in found] == ["hand", "app"]  # the oldest first
    assert found[0]["size"] == int(1.1 * GB) and found[0]["name"] == "Back-up hand"
    assert ts.loose(None, NOW) == []


async def _start(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(NOW)
    house = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await clock.advance(16 * 60)
    hm._last_review = None
    await hm.tick()
    await hm.settled()  # the look runs beside the core
    return hm, clock, house.ha


async def _check(hm: HouseMind, clock: SimulatedClock) -> dict[str, Any]:
    await clock.advance(120)
    assert await hm.tidy_check()
    return tidy_view(hm)


async def test_the_demo_tiles_say_what_is_kept_and_how_many_fit(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path)
    system = tidy_view(hm)["system"]
    assert system["backup"]["keep"] == {
        "recurrence": "daily",
        "days": [],
        "copies": 3,
        "keepDays": None,
    }
    disk = system["disk"]
    assert disk["state"] == "ok" and disk["room"] == 5
    assert not system["keepAll"]
    assert [b["name"] for b in system["loose"]] == ["Voor de verhuizing", "Z-Wave-app 1.4.0"]
    nl = hm.languages.text("nl", "ui.tidy.system.keep_line", what="x", when="y")
    assert nl == "Home Assistant maakt y een back-up en x."
    # Home Assistant keeps them all: a card with 'Laat zo'.
    ha.backup_settings = {**(ha.backup_settings or {}), "retention": {"copies": None, "days": None}}
    view = await _check(hm, clock)
    assert view["system"]["keepAll"]
    assert await hm.tidy_choose("backup_keep_all:home_assistant", "leave", ADMIN)
    assert not tidy_view(hm)["system"]["keepAll"]
    await hm.stop()


async def test_keeping_loose_backups_on_purpose_until_a_new_one(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path)
    count = tidy_view(hm)["count"]
    assert await hm.tidy_choose("backup_loose:local", "keep_backups", ADMIN)
    view = tidy_view(hm)
    assert view["system"]["loose"] == [] and view["count"] == count - 1
    decided = next(d for d in view["decided"] if d["key"] == "backup_loose:local")
    assert decided["choice"] == "keep_backups" and decided["count"] == 2
    # A new old loose backup brings the card back, with only that one.
    assert ha.backup_list is not None
    ha.backup_list.append(_backup("new", 31, gb=0.5))
    view = await _check(hm, clock)
    assert [b["id"] for b in view["system"]["loose"]] == ["new"]
    # A kept backup that was deleted in Home Assistant leaves the choice.
    ha.backup_list = [b for b in ha.backup_list if b["backup_id"] != "demo3"]
    await _check(hm, clock)
    assert hm._tidy_choices["backup_loose:local"]["ids"] == ["demo2"]
    # 'Terugdraaien' brings them all back.
    assert await hm.tidy_undo_choice("backup_loose:local")
    assert len(tidy_view(hm)["system"]["loose"]) == 2
    await hm.stop()


async def test_the_next_backup_may_not_fit_is_important(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path)
    ha.health = {**ha.health, "hassio": {"info": {"disk_total": "32.0 GB", "disk_used": "29.0 GB"}}}
    await _check(hm, clock)
    rows = await hm.db.fetchall(
        "SELECT kind, concerns, text_key, params FROM notifications WHERE topic = 'tidy:disk'"
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "important" and row["concerns"] == "admins"
    params = json.loads(row["params"])
    nl = hm.languages.text("nl", row["text_key"], **params)
    assert "De volgende back-up past misschien niet meer" in nl
    assert "nog 2,8 GB vrij; een back-up is 2,0 GB" in nl
    en = hm.languages.text("en", row["text_key"], **params)
    assert "2.8 GB is free; a backup is 2.0 GB" in en
    disk = tidy_view(hm)["system"]["disk"]
    assert disk["room"] == 1 and disk["looseBytes"] == int(1.1 * GB) + int(0.4 * GB)
    await hm.stop()


def test_housemind_never_makes_or_deletes_a_backup() -> None:
    """Only ``backup/info`` and ``backup/config/info`` are read (E-03, E-359)."""
    src = ROOT / "src" / "housemind"
    commands = set()
    for path in src.rglob("*.py"):
        commands |= set(
            re.findall(r"\"(backup/[a-z_/]+|hassio/backups?[a-z_/]*)\"", path.read_text())
        )
    assert commands == {"backup/info", "backup/config/info"}


def test_what_all_backups_take_on_this_device_is_a_part_of_the_disk_ring() -> None:
    """The disk ring (E-451) shows what all backups take on this device; one that is only
    somewhere else, such as the cloud, takes no room here."""
    backups = [
        {"agents": {"hassio.local": {"size": 2_000}}},
        {"agents": {"cloud.cloud": {"size": 5_000}}},
        {"size": 1_000},  # an older Home Assistant: one size, on this device
    ]
    assert ts.on_device(backups) == 3_000
    assert ts.on_device(None) is None
