"""Home Assistant netjes, deel A, from the user's side (E-351 – E-354, B-235, B-245): in the
extended demo house, after Home Assistant settled, the group shows what is broken – and only to
administrators."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.tidy_view import tidy_view
from housemind.server.web import create_app

ROOT = Path(__file__).resolve().parents[1]
NOON = datetime(2026, 10, 25, 11, 0, tzinfo=UTC)


async def _start(tmp_path: Path) -> tuple[HouseMind, SimulatedClock]:
    clock = SimulatedClock(NOON)
    house = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    return hm, clock


async def _review(hm: HouseMind, clock: SimulatedClock, minutes: int) -> None:
    await clock.advance(minutes * 60)
    hm._last_review = None
    await hm.tick()
    await hm.settled()  # the look runs beside the core


async def _next(ws: Any, what: str) -> dict[str, Any]:
    while True:
        message = await ws.receive_json(timeout=5)
        if message["type"] == "data" and message.get("what") == what:
            return message["data"]  # type: ignore[no-any-return]
        if message["type"] == "saved" and what == "saved":
            return message  # type: ignore[no-any-return]


async def test_the_round_waits_for_home_assistant_and_finds_what_is_broken(
    tmp_path: Path,
) -> None:
    hm, clock = await _start(tmp_path)
    await _review(hm, clock, 5)
    assert hm.tidy_checked is None  # B-238: 10 minutes after the start, not before
    await _review(hm, clock, 11)
    assert hm.tidy_checked is not None
    view = tidy_view(hm)
    kinds = {item["kind"]: item for item in view["broken"]}
    assert set(kinds) == {
        "integration_down",
        "device_down",
        "duplicate",
        "not_provided_group",
        "missing_ref",
        "unavailable",
        "meter_still",
        "integration_silent",
        "energy_issues",
    }
    assert len(view["broken"]) == 9
    # B-240, B-241: two automations that have not run for long, and a backup of 9 days old;
    # E-397: one card for the old loose backups.
    assert view["count"] == 9 + len(view["unused"]) + 2 == 13
    down = kinds["integration_down"]
    assert down["name"] == "Weerstation" and down["state"] == "setup_retry"
    assert down["followsCount"] == 2
    # Each card opens Home Assistant already showing what it is about (wish of the administrator):
    # this integration picked out, the entities of one integration, or one entity's dialog.
    assert (
        down["open"] == "/config/integrations/integration/weather_station#config_entry=demo-weather"
    )
    assert kinds["device_down"]["name"] == "tuinsensor" and kinds["device_down"]["heavy"]
    assert kinds["duplicate"]["suggest"]["entityId"] == "sensor.garage_door_openings"
    assert kinds["missing_ref"]["missing"] == ["binary_sensor.attic_motion", "light.attic_old"]
    # Two plugs of one integration no longer provided: one card with both (B-258).
    plugs = kinds["not_provided_group"]
    assert plugs["name"] == "Slimme stekkers" and plugs["followsCount"] == 2
    assert {e["entityId"] for e in plugs["follows"]} == {"switch.old_dryer", "switch.old_freezer"}
    assert plugs["open"] == "/config/entities?config_entry=demo-plugs"
    assert plugs["source"]["name"] == "Slimme stekkers"
    assert sorted(plugs["rooms"]) == ["Bijkeuken", "Garage"]
    # A2 (E-369 – E-371): a device meter that stands still, an old meter without values, and
    # what Home Assistant itself finds in its energy settings.
    # The dishwasher's meter has stood still for 200 hours; the others only since the start.
    still = kinds["meter_still"]
    assert still["entity"]["entityId"] == "sensor.dishwasher_power"
    assert still["meter"]["role"] == "device" and still["heavy"]
    assert still["open"] == (
        "/config/entities?more-info-entity-id=sensor.dishwasher_power&more-info-view=history"
    )
    assert kinds["duplicate"]["open"] == (
        "/config/entities?more-info-entity-id=sensor.garage_door_openings_2&more-info-view=settings"
    )
    silent = kinds["integration_silent"]
    assert silent["name"] == "Oude meter" and silent["followsCount"] == 3
    assert silent["open"] == "/config/entities?config_entry=demo-oldmeter"
    issues = kinds["energy_issues"]
    assert issues["open"] == "/config/energy"
    assert issues["issues"][0]["type"] == "entity_unexpected_unit_energy"
    assert issues["issues"][0]["entityId"] == "sensor.washer_power"
    assert kinds["missing_ref"]["open"] == "/config/automation/edit/demo-2"
    # The phone's alarm went away just now: not yet (even away is no fault).
    assert not any("phone" in item["target"] for item in view["broken"])
    # Since when is kept, also over a restart of HouseMind.
    assert await hm.db.get_setting("tidy_seen", {})


async def test_only_administrators_see_it_and_check_now_waits_a_minute(tmp_path: Path) -> None:
    hm, clock = await _start(tmp_path)
    await _review(hm, clock, 11)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "get", "what": "tidy"})
        data = await _next(ws, "tidy")
        assert data["count"] == 13 and data["checkedAt"]  # 9 broken, 2 old automations, 2 backup
        await clock.advance(120)
        await ws.send_json({"type": "tidy", "do": "check"})
        again = await _next(ws, "tidy")
        assert again["checkedAt"] > data["checkedAt"]
        await ws.send_json({"type": "tidy", "do": "check"})  # right after: not again
        said = await _next(ws, "saved")
        assert "minuut" in said["text"]
        other = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-2"})
        await other.send_json({"type": "hello", "browserLanguage": "nl"})
        await other.send_json({"type": "get", "what": "tidy"})
        await other.send_json({"type": "tidy", "do": "check"})
        await other.send_json({"type": "get", "what": "my_data"})
        while True:  # my_data comes back; tidy never does for someone who is no administrator
            message = await other.receive_json(timeout=5)
            assert message.get("what") != "tidy"
            if message.get("what") == "my_data":
                break
