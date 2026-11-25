"""Home Assistant netjes, deel C (E-356 – E-358, B-240 – B-242): automations that have not run
for a long time, the system (backup, database, disk) and what changed after an update."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain import tidy_system as ts
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, HouseModel, State
from housemind.demo.houses import load_demo_house
from housemind.ha.adapter import HAConfigEntry
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server.tidy_view import tidy_view
from housemind.server.web import create_app

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
SEASON = ["kerst", "vakantie", "zomer", "winter"]


def _auto(name: str, on: bool = True, ran: int | None = None, seen: int = 10) -> ts.Auto:
    return ts.Auto(
        f"automation.{name.lower().replace(' ', '_')}",
        name,
        on,
        NOW - timedelta(days=ran) if ran is not None else None,
        NOW - timedelta(days=seen),
        words=name.lower(),
    )


def test_an_automation_that_has_not_run_for_long() -> None:
    found = ts.unused(
        [
            _auto("Oprit verlichting", ran=500),
            _auto("Verwarming uit", ran=99),
            _auto("Bedlamp", ran=30),  # ran a month ago: fine
            _auto("Batterij nachtmodus", ran=None, seen=10),  # never ran, seen 10 days: wait
            _auto("Oude regel", ran=None, seen=120),  # never ran, seen 120 days
            _auto("Kerstverlichting aan", ran=287),  # a season waits a year
            _auto("Zomertijd rolluik", ran=400),
            _auto("Alle lichten uit", on=False, ran=256),  # off and not run: E-356 as chosen
            _auto("Nieuwe uit", on=False, ran=20),
        ],
        NOW,
        SEASON,
        skip={"automation.verwarming_uit"},
    )
    by = {f.name: f for f in found}
    assert set(by) == {"Oprit verlichting", "Oude regel", "Zomertijd rolluik", "Alle lichten uit"}
    assert (
        by["Oprit verlichting"].kind == "unused_on"
        and by["Oprit verlichting"].detail["days"] == 500
    )
    assert by["Oude regel"].detail["never"]
    assert by["Zomertijd rolluik"].detail["season"]
    assert by["Alle lichten uit"].kind == "unused_off"
    assert found[0].name == "Oprit verlichting"  # the longest first
    assert ts.is_season("vakantie - presence simulation", SEASON)
    assert not ts.is_season("bewinterd", SEASON)  # a season word starts a word


def test_the_backup_tile() -> None:
    def backup(days: float, ha: bool = True) -> dict[str, Any]:
        return {"date": (NOW - timedelta(days=days)).isoformat(), "homeassistant_included": ha}

    assert ts.backup_tile([backup(0.3)], NOW, own=False)["state"] == "ok"
    old = ts.backup_tile([backup(9), backup(1, ha=False)], NOW, own=False)
    assert old["state"] == "old" and old["days"] == 9 and old["heavy"]  # one app: no backup
    assert ts.backup_tile([backup(1, ha=False)], NOW, own=False)["state"] == "none"
    own = ts.backup_tile([backup(9)], NOW, own=True)
    assert own["state"] == "own" and own["days"] == 9 and not own["heavy"]
    assert ts.backup_tile(None, NOW, own=False)["state"] == "unknown"


def test_the_database_and_the_disk() -> None:
    assert ts.size_bytes("1508.73 MiB") == 1508.73 * 2**20
    assert ts.size_bytes("62.3 GB") == 62.3e9
    assert ts.size_bytes("lots") is None
    sizes = {"2026-10-20": 1.0e9, "2026-10-24": 1.2e9, "2026-10-27": 1.7e9}
    assert ts.growth_per_day(sizes) == 0.1e9  # 0.7 GB over 7 days
    assert ts.growth_per_day({"2026-10-27": 1.0e9}) is None
    health = {"recorder": {"info": {"estimated_db_size": "1700 MB", "database_engine": "sqlite"}}}
    database = ts.database_tile(health, {"2026-10-26": 1.5e9, "2026-10-27": 1.7e9}, [])
    assert database["state"] == "grows" and database["engine"] == "sqlite"
    assert ts.database_tile({}, {}, [])["state"] == "unknown"
    busy = ts.busiest({"sensor.p1": 43200, "sensor.t": 10, "light.x": 0}, 86400)
    assert busy[0] == {"entity": "sensor.p1", "every": 2.0} and len(busy) == 2

    def disk(used: str) -> dict[str, Any]:
        return ts.disk_tile({"hassio": {"info": {"disk_total": "100 GB", "disk_used": used}}})

    assert disk("65 GB")["state"] == "ok"
    assert disk("90 GB")["state"] == "filling"
    assert disk("96 GB")["state"] == "full" and disk("96 GB")["heavy"]
    assert ts.disk_tile({})["state"] == "unknown"  # no Supervisor


def _model() -> HouseModel:
    model = HouseModel()
    for entity_id, entry in (("light.hue_1", "hue"), ("sensor.a", "zig"), ("sensor.b", "zig")):
        model.entities[entity_id] = Entity(entity_id, entity_id, entity_id, config_entry_id=entry)
    return model


def _states(**values: str) -> dict[str, State]:
    return {
        e.replace("_", ".", 1): State(e.replace("_", ".", 1), v, {}, NOW) for e, v in values.items()
    }


def test_after_an_update_what_no_longer_works_and_what_came_back() -> None:
    model = _model()
    entries = [
        HAConfigEntry("hue", "hue", "Hue", "loaded"),
        HAConfigEntry("zig", "zha", "Zigbee", "loaded"),
    ]
    before_states = {
        "light.hue_1": State("light.hue_1", "on", {}, NOW),
        "sensor.a": State("sensor.a", "12", {}, NOW),
        "sensor.b": State("sensor.b", "unavailable", {}, NOW),
    }
    working, loaded = ts.working(model, before_states, entries)
    assert working == ["light.hue_1", "sensor.a"] and loaded == ["hue", "zig"]
    record = {"at": NOW.isoformat(), "before": {"working": working, "loaded": loaded}, "checks": []}
    assert not ts.check_due(record, NOW + timedelta(minutes=20))
    assert ts.check_due(record, NOW + timedelta(minutes=30))
    after = {**before_states, "light.hue_1": State("light.hue_1", "unavailable", {}, NOW)}
    after["sensor.a"] = State("sensor.a", "unavailable", {}, NOW)
    now_entries = [HAConfigEntry("hue", "hue", "Hue", "setup_retry"), entries[1]]
    lost, lost_entries = ts.compare(record["before"], model, after, now_entries, {"zig"})
    assert lost == ["light.hue_1"] and lost_entries == ["hue"]  # 'Bewust uit' on zig: not counted
    record = ts.check(record, lost, lost_entries, NOW + timedelta(minutes=30))
    assert ts.next_check(record) == NOW + timedelta(hours=2)
    record = ts.check(record, [], [], NOW + timedelta(hours=2))
    assert record["lost"] == [] and record["back"] == ["light.hue_1"]
    for hours in (6, 24):
        record = ts.check(record, [], [], NOW + timedelta(hours=hours))
    assert "before" not in record and ts.next_check(record) is None
    assert not ts.check_due(record, NOW + timedelta(days=2))


def test_the_versions_of_an_update() -> None:
    states = {
        "update.home_assistant_operating_system_update": State(
            "update.home_assistant_operating_system_update",
            "off",
            {"installed_version": "18.3", "title": "Home Assistant Operating System"},
            NOW,
        ),
        "update.home_assistant_supervisor_update": State(
            "update.home_assistant_supervisor_update",
            "off",
            {
                "installed_version": "2026.10.3",
                "release_url": "https://github.com/home-assistant/supervisor/releases",
            },
            NOW,
        ),
    }
    found = ts.versions("2026.10.4", states)
    assert found == {"core": "2026.10.4", "os": "18.3", "supervisor": "2026.10.3"}
    assert ts.changed({"core": "2026.10.2", "os": "18.3"}, found) == ["core"]
    assert ts.changed({}, found) == []  # the first time: nothing to compare


# -- in the demo house (the extended one) ----------------------------------------------------


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


def _keys(hm: HouseMind) -> set[str]:
    return {f.key for f in hm.tidy_findings}


async def test_the_demo_shows_automations_the_system_and_no_update_yet(tmp_path: Path) -> None:
    hm, _, _ = await _start(tmp_path)
    keys = _keys(hm)
    assert "unused_on:automation.tablet_charging" in keys
    assert "unused_off:automation.old_alarm_clock" in keys
    assert not any("christmas" in k for k in keys)  # a season: after a year
    view = tidy_view(hm)
    assert {u["kind"] for u in view["unused"]} == {"unused_on", "unused_off"}
    assert not any(b["kind"].startswith("unused") for b in view["broken"])
    on = next(u for u in view["unused"] if u["kind"] == "unused_on")
    assert on["can"] == ["off", "keep_on"] and on["days"] == 410
    assert on["open"] == "/config/automation/edit/demo-3"
    off = next(u for u in view["unused"] if u["kind"] == "unused_off")
    assert off["can"] == ["keep"]
    system = view["system"]
    assert system["backup"]["state"] == "old" and system["backup"]["days"] == 9
    assert system["disk"]["state"] == "ok" and round(system["disk"]["share"], 2) == 0.65
    assert system["database"]["engine"] == "sqlite"
    assert view["update"] is None
    assert view["count"] == len(hm.tidy_findings) + 2  # the old backup and old loose ones too
    await hm.stop()


async def test_switch_off_an_old_automation_and_the_choices(tmp_path: Path) -> None:
    hm, _, ha = await _start(tmp_path)
    done = await hm.tidy_off("unused_on:automation.tablet_charging", "demo-user-1")
    assert done["ok"] and ha.states["automation.tablet_charging"].state == "off"
    off = tidy_view(hm)["off"]
    assert off[0]["target"] == "automation.tablet_charging" and off[0]["reason"] == "unused"
    await hm.tidy_round()
    assert "unused_off:automation.tablet_charging" not in _keys(hm)  # at 'Wat HouseMind uitzette'
    assert await hm.tidy_choose("unused_off:automation.old_alarm_clock", "keep", "demo-user-1")
    assert not await hm.tidy_choose("unused_off:automation.old_alarm_clock", "keep_on", None)
    await hm.tidy_round()
    assert "unused_off:automation.old_alarm_clock" not in _keys(hm)
    assert await hm.tidy_choose("backup:home_assistant", "own_backups", "demo-user-1")
    assert tidy_view(hm)["system"]["backup"]["state"] == "own"
    assert await hm.tidy_undo_choice("backup:home_assistant")
    assert tidy_view(hm)["system"]["backup"]["state"] == "old"
    await hm.stop()


async def test_the_night_round_measures_the_database_and_starts_counting_again(
    tmp_path: Path,
) -> None:
    hm, clock, ha = await _start(tmp_path)
    for value in ("1", "2", "3", "4"):
        await ha.simulate("sensor.office_air_quality", value)
    assert hm._tidy_counts["sensor.office_air_quality"] >= 4
    ha.health["recorder"] = {"info": {"estimated_db_size": "2600.00 MiB"}}
    night = datetime(2026, 10, 28, 2, 35, tzinfo=UTC)  # 03:35 in Amsterdam
    await clock.advance((night - clock.now()).total_seconds())
    hm._last_review = None
    await hm.tick()
    await hm.settled()  # the look runs beside the core
    sizes = await hm.db.get_setting("tidy_db")
    assert sizes["2026-10-28"] == 2600.0 * 2**20 and len(sizes) == 2
    assert hm._tidy_counts == {} or "sensor.office_air_quality" not in hm._tidy_counts
    busy = await hm.db.get_setting("tidy_busy")
    assert busy[0]["entity"] == "sensor.office_air_quality"
    assert tidy_view(hm)["system"]["database"]["busiest"][0]["entityId"] == busy[0]["entity"]
    await hm.stop()


async def test_after_an_update_home_assistant_is_looked_at_again(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path)
    await clock.advance(3600)
    await hm.tidy_round()  # what works now: the state from before a next update
    before = await hm.db.get_setting("tidy_before")
    assert "light.living_ceiling" in before["working"]
    await ha.drop()
    ha.states["light.living_ceiling"] = State(
        "light.living_ceiling", "unavailable", {}, clock.now()
    )
    await ha.come_back(version="2026.11.0")
    await clock.advance(11 * 60)
    hm._last_review = None
    await hm.tick()  # 10 minutes after the new connection a round comes, not only after an hour
    await hm.settled()  # the look runs beside the core
    assert hm.tidy_update is not None
    assert hm.tidy_update["from"] == {"core": "2026.10.0"} and hm.tidy_update["to"] == {
        "core": "2026.11.0"
    }
    await clock.advance(20 * 60)  # 31 minutes after the new connection
    hm._last_review = None
    await hm.tick()
    await hm.settled()  # the look runs beside the core
    update = tidy_view(hm)["update"]
    assert len(update["checks"]) == 1
    group = next(
        g
        for g in update["lost"]
        if any(e["entityId"] == "light.living_ceiling" for e in g["entities"])
    )
    if group["count"] == 1:  # one entity: its own dialog opens in Home Assistant
        assert group["open"].startswith("/config/entities?more-info-entity-id=light.living_ceiling")
    await ha.simulate("light.living_ceiling", "off")
    await clock.advance(2 * 3600)
    hm._last_review = None
    await hm.tick()
    await hm.settled()  # the look runs beside the core
    update = tidy_view(hm)["update"]
    assert update["lost"] == [] or not any(
        e["entityId"] == "light.living_ceiling" for g in update["lost"] for e in g["entities"]
    )
    assert any(e["entityId"] == "light.living_ceiling" for e in update["back"])
    await hm.stop()


async def _next(ws: Any) -> dict[str, Any]:
    while True:
        message = await ws.receive_json(timeout=5)
        if message["type"] == "data" and message.get("what") == "tidy":
            return message["data"]  # type: ignore[no-any-return]


async def test_the_screen_keeps_it_on(tmp_path: Path) -> None:
    hm, _, _ = await _start(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "get", "what": "tidy"})
        before = await _next(ws)
        on = next(u for u in before["unused"] if u["kind"] == "unused_on")
        await ws.send_json({"type": "tidy", "do": "keep_on", "key": on["key"]})
        after = await _next(ws)
        assert on["key"] not in {u["key"] for u in after["unused"]}
        assert any(d["choice"] == "keep_on" for d in after["decided"])
    await hm.stop()
