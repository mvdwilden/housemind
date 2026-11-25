"""Home Assistant netjes, deel B (E-355, E-359, E-360, B-239, B-243, B-244): 'Bewust uit', the
choices, and switching off after a yes – through the safety gate, never deleting, always back."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain import settings_file, tidy
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, HouseModel
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import Layer
from housemind.server.tidy_view import tidy_view
from housemind.server.web import create_app

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
NOON = datetime(2026, 10, 25, 11, 0, tzinfo=UTC)


def _local(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 25, hour, minute, tzinfo=TZ)


def test_a_mark_is_quiet_only_in_its_time() -> None:
    hours = tidy.Mark("sensor.x", "hours", "22:00", "07:00")
    assert tidy.quiet_now(hours, _local(23))
    assert tidy.quiet_now(hours, _local(3))  # across midnight
    assert tidy.quiet_now(hours, _local(7, 20))  # half an hour after it ended
    assert not tidy.quiet_now(hours, _local(7, 40))
    assert not tidy.quiet_now(hours, _local(15))
    assert tidy.quiet_now(tidy.Mark("sensor.x", "always"), _local(15))
    until = tidy.Mark("sensor.x", "until", until="2026-10-25")
    assert tidy.quiet_now(until, _local(23)) and not tidy.quiet_now(
        until, _local(23) + timedelta(days=1)
    )
    dark = tidy.Mark("sensor.x", "dark")
    night = lambda moment: moment.hour >= 18 or moment.hour < 7  # noqa: E731
    assert tidy.quiet_now(dark, _local(22), night)
    assert tidy.quiet_now(dark, _local(7, 20), night)  # half an hour after sunrise
    assert not tidy.quiet_now(dark, _local(12), night)
    assert not tidy.quiet_now(dark, _local(22))  # without the sun: no dark


def _finding(kind: str, target: str, **detail: Any) -> tidy.Finding:
    return tidy.Finding(
        f"{kind}:{target}", kind, target, target, NOON - timedelta(hours=5), detail=detail
    )


def test_marks_cover_an_entity_its_device_and_its_integration() -> None:
    model = HouseModel()
    model.entities["sensor.garden_soil"] = Entity(
        "sensor.garden_soil", "r", "Bodem", device_id="d-garden", config_entry_id="e-zha"
    )
    on_device = {"d-garden": tidy.Mark("d-garden", "always", review_at="2027-01-01T00:00:00+00:00")}
    findings = [_finding("unavailable", "sensor.garden_soil"), _finding("duplicate", "sensor.y")]
    kept = tidy.apply_marks(findings, on_device, model, NOON, NOON.astimezone(TZ))
    assert [f.kind for f in kept] == ["duplicate"]  # a duplicate is never 'bewust uit'
    group = _finding("unavailable_group", "e-zha", source="e-zha")
    on_entry = {"e-zha": tidy.Mark("e-zha", "always", review_at="2027-01-01T00:00:00+00:00")}
    assert tidy.apply_marks([group], on_entry, model, NOON, NOON.astimezone(TZ)) == []
    due = {"e-zha": tidy.Mark("e-zha", "always", review_at="2026-10-01T00:00:00+00:00")}
    back = tidy.apply_marks([group], due, model, NOON, NOON.astimezone(TZ))
    assert back[0].detail["review"] == "e-zha"  # 'Klopt dit nog?'


def _nights(count: int, start: tuple[int, int], end: tuple[int, int]) -> list[tuple[datetime, str]]:
    changes = []
    for day in range(count):
        away = datetime(2026, 10, 24 - day, *start, tzinfo=TZ)
        back = datetime(2026, 10, 25 - day, *end, tzinfo=TZ)
        changes += [(away.astimezone(UTC), "unavailable"), (back.astimezone(UTC), "21.5")]
    return changes


def test_a_pattern_is_offered_never_chosen() -> None:
    now = datetime(2026, 10, 25, 12, 0, tzinfo=TZ)
    found = tidy.suggest_window(_nights(6, (22, 10), (6, 50)), TZ, now)
    assert found == {"how": "hours", "start": "22:00", "end": "07:00"}
    assert tidy.suggest_window(_nights(4, (22, 10), (6, 50)), TZ, now) is None
    dark = lambda moment: moment.hour >= 18 or moment.hour < 7  # noqa: E731
    in_the_dark = tidy.suggest_window(_nights(6, (18, 20), (6, 50)), TZ, now, dark)
    assert in_the_dark == {"how": "dark"}


async def _start(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(NOON)
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


async def test_a_duplicate_off_after_a_yes_and_back(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path)
    key = "duplicate:sensor.garage_door_openings_2"
    assert key in _keys(hm)
    done = await hm.tidy_off(key, "demo-user-1")
    assert done["ok"] and ha.registry_writes == [("sensor.garage_door_openings_2", False)]
    assert hm.model.entities["sensor.garage_door_openings_2"].disabled
    assert key not in _keys(hm)
    view = tidy_view(hm)
    assert view["off"][0]["target"] == "sensor.garage_door_openings_2"
    assert view["off"][0]["reason"] == "duplicate"
    # The logbook keeps it, like every decision.
    rows = await hm.db.fetchall(
        "SELECT action, outcome FROM decisions WHERE entity_id = ?",
        ("sensor.garage_door_openings_2",),
    )
    assert [tuple(r) for r in rows] == [("registry.disable", "executed")]
    # Always back – also during a pause.
    assert hm.gate is not None
    hm.gate.state.paused_until = clock.now() + timedelta(hours=1)
    assert (await hm.tidy_on("sensor.garage_door_openings_2", "demo-user-1"))["ok"]
    assert ha.registry_writes[-1] == ("sensor.garage_door_openings_2", True)
    assert tidy_view(hm)["off"] == []


async def test_a_bundle_goes_off_one_by_one_and_all_comes_back(tmp_path: Path) -> None:
    hm, _, ha = await _start(tmp_path)
    key = "not_provided_group:demo-plugs"
    assert (await hm.tidy_off(key, "demo-user-1"))["done"] == 2
    assert {e for e, on in ha.registry_writes if not on} == {
        "switch.old_dryer",
        "switch.old_freezer",
    }
    assert await hm.tidy_all_on("demo-user-1") == []
    assert {e for e, on in ha.registry_writes if on} == {"switch.old_dryer", "switch.old_freezer"}


async def test_an_automation_that_points_to_what_is_gone(tmp_path: Path) -> None:
    hm, _, ha = await _start(tmp_path)
    assert (await hm.tidy_off("missing_ref:automation.attic_light_night", "demo-user-1"))["ok"]
    assert ha.states["automation.attic_light_night"].state == "off"
    assert (await hm.tidy_on("automation.attic_light_night", "demo-user-1"))["ok"]
    assert ha.states["automation.attic_light_night"].state == "on"


async def test_not_during_a_pause_and_never_what_is_not_allowed(tmp_path: Path) -> None:
    hm, clock, _ = await _start(tmp_path)
    assert hm.gate is not None
    hm.gate.state.paused_until = clock.now() + timedelta(hours=1)
    assert not (await hm.tidy_off("duplicate:sensor.garage_door_openings_2", "demo-user-1"))["ok"]
    hm.gate.state.paused_until = None
    smoke = next(e for e in hm.model.entities.values() if e.device_class == "smoke")
    denied = await hm.gate.submit_tidy(
        smoke.entity_id, False, Layer.PERSON_ANSWER, clock.now(), None, "x"
    )
    assert denied.reason == "not_allowed"
    back = await hm.gate.submit_tidy("light.attic", True, Layer.RESTORE, clock.now(), None, "undo")
    assert back.reason == "restore_only_own"  # back on: only what HouseMind switched off
    ai = await hm.gate.submit_tidy("light.attic", False, Layer.AI, clock.now(), None, "x")
    assert ai.reason == "person_only"


async def test_choices_hold_and_can_be_undone(tmp_path: Path) -> None:
    hm, clock, _ = await _start(tmp_path)
    assert await hm.tidy_choose("missing_ref:automation.attic_light_night", "leave", "demo-user-1")
    assert await hm.tidy_choose("duplicate:sensor.garage_door_openings_2", "not_duplicate", None)
    assert not await hm.tidy_choose(
        "duplicate:sensor.x", "leave", None
    )  # 'Laat zo' is for references
    await clock.advance(120)
    await hm.tidy_round()
    assert "missing_ref:automation.attic_light_night" not in _keys(hm)
    assert "duplicate:sensor.garage_door_openings_2" not in _keys(hm)
    assert {d["choice"] for d in tidy_view(hm)["decided"]} == {"leave", "not_duplicate"}
    assert await hm.tidy_undo_choice("missing_ref:automation.attic_light_night")
    assert "missing_ref:automation.attic_light_night" in _keys(hm)  # back at once


async def test_on_purpose_and_the_question_after_ninety_days(tmp_path: Path) -> None:
    hm, clock, _ = await _start(tmp_path)
    garden = next(f for f in hm.tidy_findings if f.kind == "device_down")
    assert await hm.tidy_mark(garden.target, "always", "demo-user-1")
    assert garden.key not in _keys(hm)
    assert tidy_view(hm)["marks"][0]["how"] == "always"
    await clock.advance(91 * 24 * 3600)
    await hm.tidy_round()
    back = next(f for f in hm.tidy_findings if f.key == garden.key)
    assert back.detail["review"] == garden.target
    assert await hm.tidy_review_ok(garden.target)  # 'Ja, laat zo': quiet for another 90 days
    assert garden.key not in _keys(hm)
    assert not await hm.tidy_mark(garden.target, "hours", None, "25:00", "07:00")  # no such time
    assert await hm.tidy_unmark(garden.target)
    assert garden.key in _keys(hm)  # 'Terugdraaien': the card is back at once


async def test_someone_switching_it_back_on_wins(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path)
    await hm.tidy_off("duplicate:sensor.garage_door_openings_2", "demo-user-1")
    await ha.set_entity_enabled("sensor.garage_door_openings_2", True)  # in Home Assistant
    hm.model.entities["sensor.garage_door_openings_2"] = ha.model.entities[
        "sensor.garage_door_openings_2"
    ]
    await clock.advance(120)
    await hm.tidy_round()
    assert tidy_view(hm)["off"] == []
    assert hm._tidy_choices["duplicate:sensor.garage_door_openings_2"]["choice"] == "someone_on"
    assert "duplicate:sensor.garage_door_openings_2" not in _keys(hm)  # not asked again


async def test_giving_back_switches_everything_back_on(tmp_path: Path) -> None:
    hm, _, ha = await _start(tmp_path)
    await hm.tidy_off("duplicate:sensor.garage_door_openings_2", "demo-user-1")
    result = await hm.give_back(True, "demo-user-1", unlink=False)
    assert result["ok"]
    assert ha.registry_writes[-1] == ("sensor.garage_door_openings_2", True)


async def test_choices_and_marks_travel_in_the_settings_file(tmp_path: Path) -> None:
    hm, _, _ = await _start(tmp_path / "a")
    garden = next(f for f in hm.tidy_findings if f.kind == "device_down")
    await hm.tidy_mark(garden.target, "hours", "demo-user-1", "22:00", "07:00")
    await hm.tidy_choose("missing_ref:automation.attic_light_night", "leave", "demo-user-1")
    await hm.tidy_off("duplicate:sensor.garage_door_openings_2", "demo-user-1")
    made = await settings_file.make(hm)
    assert "tidy_off" not in made["settings"]  # what HouseMind switched off stays in this house
    made["settings"]["tidy_marks"]["sensor.not_here"] = {"target": "sensor.not_here", "how": "x"}
    other, _, _ = await _start(tmp_path / "b")
    shown = await settings_file.preview(other, made)
    assert "sensor.not_here" in shown["skipped"]
    assert (await settings_file.apply(other, made))["ok"]
    assert other._tidy_marks[garden.target].start == "22:00"
    assert "missing_ref:automation.attic_light_night" in other._tidy_choices
    assert tidy_view(other)["off"] == []
    await hm.stop()
    await other.stop()


async def _next(ws: Any) -> dict[str, Any]:
    while True:
        message = await ws.receive_json(timeout=5)
        if message["type"] == "data" and message.get("what") == "tidy":
            return message["data"]  # type: ignore[no-any-return]


async def test_the_screen_asks_and_only_administrators_may(tmp_path: Path) -> None:
    hm, _, ha = await _start(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        other = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-2"})
        await other.send_json({"type": "hello", "browserLanguage": "nl"})
        await other.send_json(
            {"type": "tidy", "do": "off", "key": "duplicate:sensor.garage_door_openings_2"}
        )
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "get", "what": "tidy"})
        before = await _next(ws)
        assert ha.registry_writes == []  # not an administrator: nothing
        dup = next(b for b in before["broken"] if b["kind"] == "duplicate")
        assert dup["can"] == ["off", "not_duplicate"]
        garden = next(b for b in before["broken"] if b["kind"] == "device_down")
        assert garden["can"] == ["mark"] and garden["markTarget"] == garden["target"]
        await ws.send_json({"type": "tidy", "do": "off", "key": dup["key"]})
        after = await _next(ws)
        assert after["off"][0]["target"] == "sensor.garage_door_openings_2"
        await ws.send_json(
            {"type": "tidy", "do": "mark", "target": garden["markTarget"], "how": "dark"}
        )
        marked = await _next(ws)
        assert marked["marks"][0]["how"] == "dark"
