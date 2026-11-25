"""Speed in a big house (S1, S2): every change used to work out every skill in every room,
going through every device of the house – 220 ms per motion in a house of 1265 devices on a
fast computer, about a second on a small one. Now each room's devices are found once, and a
change is worked out only where it can matter."""

from __future__ import annotations

import json
import statistics
import time as timer
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from housemind.brain.skills import SkillEngine
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 10, 19, 20, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
PER_CHANGE_MS = 50.0  # in HouseMind itself, p95, also on a small computer (B-40)


def grow(ha: FakeHomeAssistant, copies: int, now: datetime) -> None:
    """The demo house copied into ``copies`` times as many rooms, each with its devices."""
    model = ha.model
    areas, devices, entities = dict(model.areas), dict(model.devices), dict(model.entities)
    for n in range(1, copies):
        for a in areas.values():
            model.areas[f"{a.id}_{n}"] = replace(a, id=f"{a.id}_{n}", name=f"{a.name} {n}")
        for d in devices.values():
            room = f"{d.area_id}_{n}" if d.area_id else None
            model.devices[f"{d.id}_{n}"] = replace(d, id=f"{d.id}_{n}", area_id=room)
        for e in entities.values():
            if e.domain in ("person", "zone", "sun", "weather"):
                continue
            new_id = f"{e.entity_id}_{n}"
            model.entities[new_id] = replace(
                e,
                entity_id=new_id,
                registry_id=f"{e.registry_id}-{n}",
                area_id=f"{e.area_id}_{n}" if e.area_id else None,
                device_id=f"{e.device_id}_{n}" if e.device_id else None,
            )
            if (old := ha.states.get(e.entity_id)) is not None:
                ha.states[new_id] = replace(old, entity_id=new_id, last_changed=now)


async def _big_house(tmp_path: Path, copies: int) -> tuple[HouseMind, FakeHomeAssistant, Any]:
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("extended", clock, "nl")
    grow(house.ha, copies, clock.now())
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    assert hm.world is not None and hm.learning is not None
    hm.world.modes.mode = HouseMode.HOME
    for area in hm.model.areas:
        hm.learning.steps[("light_on_entry", area)] = Step.AUTONOMOUS
    return hm, house.ha, clock


async def test_a_motion_is_worked_out_only_where_it_matters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A motion in one room works out that room (and its open space); a change without a
    room, such as a person coming home, still works out the whole house."""
    hm, ha, _clock = await _big_house(tmp_path, 3)
    assert hm.skills is not None
    seen: list[set[str] | None] = []
    original = SkillEngine.evaluate

    def counted(self: SkillEngine, world: Any, states: Any, rooms: Any = None) -> Any:
        seen.append(None if rooms is None else set(rooms))
        return original(self, world, states, rooms)

    monkeypatch.setattr(SkillEngine, "evaluate", counted)
    motion = next(e for e in ha.states if e.startswith("binary_sensor.") and "motion" in e)
    room = hm.model.area_of(motion)
    assert room is not None
    await ha.simulate(motion, "on")
    assert seen and seen[0] is not None and room.id in seen[0]
    assert len(seen[0]) <= 1 + len(hm.skills.open_space.get(room.id, ()))
    seen.clear()
    person = next(e for e in ha.states if e.startswith("person."))
    await ha.simulate(person, "not_home")
    assert seen and seen[0] is None
    await hm.stop()


@pytest.mark.slow
async def test_a_change_in_a_big_house_takes_milliseconds(tmp_path: Path) -> None:
    """In a house of about 1300 devices and 180 rooms, a motion takes HouseMind at most
    PER_CHANGE_MS (p95) – it took 220 ms here before S1 and S2."""
    hm, ha, clock = await _big_house(tmp_path, 12)
    motions = [e for e in ha.states if e.startswith("binary_sensor.") and "motion" in e][:40]
    for motion in motions:
        await ha.simulate(motion, "off")
    await clock.advance(3 * 3600)
    await hm.tick()
    took = []
    for motion in motions:
        started = timer.perf_counter()
        await ha.simulate(motion, "on")
        took.append((timer.perf_counter() - started) * 1000)
    p95 = statistics.quantiles(took, n=20)[-1]
    median = statistics.median(took)
    print(f"\n{len(ha.states)} devices: a motion takes {median:.1f} ms, p95 {p95:.1f}")
    assert p95 <= PER_CHANGE_MS
    await hm.stop()


async def test_a_screen_gets_only_what_changed(tmp_path: Path) -> None:
    """S4: every screen update carried the whole picture, both language files (340 KB)
    included. Now a screen that asks for it gets the whole picture once, and then only the
    parts that changed – the texts again only when its language changes."""
    from aiohttp.test_utils import TestClient, TestServer

    from housemind.server.web import create_app

    hm, _ha, _clock = await _big_house(tmp_path, 1)
    headers = {"X-Remote-User-Id": "demo-user-1"}
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=headers)
        hello = {"type": "hello", "haLanguage": "nl", "browserLanguage": "nl", "partial": True}
        await ws.send_json(hello)

        async def snapshot() -> dict[str, Any]:
            while True:
                message = await ws.receive_json(timeout=5)
                if message["type"] == "snapshot":
                    return message  # type: ignore[no-any-return]

        first = await snapshot()
        assert not first.get("partial") and first["snapshot"]["texts"]["orb.calm"]
        await ws.send_json({"type": "set_level", "level": 3})
        later = await snapshot()
        while later["snapshot"].get("level") != 3:
            later = await snapshot()
        assert later["partial"] and "texts" not in later["snapshot"]
        # What it weighs, not how many parts: new parts of Brein (E-524) are a few bytes each.
        assert len(json.dumps(later["snapshot"])) < len(json.dumps(first["snapshot"])) / 2
        await ws.send_json({"type": "set_pref", "key": "language", "value": "en"})
        english = await snapshot()
        while english["snapshot"].get("language") != "en":
            english = await snapshot()
        assert english["snapshot"]["texts"]["orb.calm"] == "All calm"
        await ws.close()
    await hm.stop()


@pytest.mark.slow
async def test_a_screen_update_in_a_big_house_is_quick(tmp_path: Path) -> None:
    """S4: in the big house a screen update on Expert took 104 ms, most of it the card "Klaar
    voor autonomie?" going through every device for every skill in every room. Now each
    room's devices are gathered once per update."""
    from housemind.server import views

    hm, _ha, _clock = await _big_house(tmp_path, 12)
    await hm.db.execute(
        "INSERT OR REPLACE INTO people(user_id, display_name, level, preferences, created)"
        " VALUES(?,?,?,?,?)",
        ("expert", "Expert", 4, '{"onboarded": true}', "2026-10-01"),
    )
    await hm._refresh_people()
    viewer = await views.load_viewer(hm, "expert", None, True)
    took = []
    for _ in range(5):
        started = timer.perf_counter()
        await views.snapshot(hm, viewer)
        took.append((timer.perf_counter() - started) * 1000)
    print(f"\na screen update on Expert in the big house: {min(took):.1f} ms")
    assert min(took) <= 60
    await hm.stop()


async def test_the_meter_keeps_what_it_measured_over_a_restart(tmp_path: Path) -> None:
    """E-322, L8: the meter on Expert ('trigger → actie') kept its measurements in memory only.
    In the real house it showed '–' for a day after an update, until HouseMind next switched
    something itself. Now the last 50 survive a restart."""
    hm, ha, clock = await _big_house(tmp_path, 1)
    motions = [e for e in ha.states if e.startswith("binary_sensor.") and "motion" in e][:3]
    for motion in motions:
        await ha.simulate(motion, "off")
    await clock.advance(3 * 3600)
    await hm.tick()
    for motion in motions:
        await ha.simulate(motion, "on")
    await hm.settled()
    measured = list(hm.status.reactions)
    assert measured
    await hm.stop()
    again = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert again.status.reactions == measured
    await again.stop()
