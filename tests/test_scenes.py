"""Scenes per house (E-106): a start set per room role, people's own moments, by tap or voice."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server import views

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")


async def start(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(datetime(2026, 10, 19, 19, 0, tzinfo=TZ).astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, clock, house.ha


def pct(ha: FakeHomeAssistant, entity_id: str) -> int | None:
    state = ha.states[entity_id]
    return round(state.attributes["brightness"] * 100 / 255) if state.state == "on" else None


async def test_the_start_set_follows_the_room_and_its_lights(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    assert {s.key for s in hm.scenes("living")} == {"movie", "cosy", "reading"}
    assert {s.key for s in hm.scenes("kitchen")} == {"cooking", "dinner"}
    assert hm.scenes("garage") == []  # no moments without lights that fit
    movie = next(s for s in hm.scenes("living") if s.key == "movie")
    assert movie.steps["light.living_reading"] == ("light.turn_on", {"brightness_pct": 10})
    assert movie.steps["cover.living_curtain"] == ("cover.close_cover", {})
    # Only window coverings: never a garage door (E-31).
    assert all("garage" not in e for s in hm.scenes("living") for e in s.steps)
    await hm.stop()


async def test_say_film_and_the_living_room_gets_ready(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    reply = await hm.talk("Film", "nl", 3, "demo-user-1")
    living = hm.model.areas["living"].name
    assert reply["text"] == f"Goed: Film ({living})."
    assert pct(ha, "light.living_reading") == 10
    assert ha.states["cover.living_curtain"].state == "closed"
    reasons = {d.request.reason for d in hm.status.decisions[-3:]}
    assert reasons == {"scene:movie"}
    await hm.stop()


async def test_remember_this_makes_a_moment_of_your_own(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    await ha.simulate("light.kitchen", "on", {"brightness": 77})  # about 30%
    own = await hm.save_scene("kitchen", "  Ontbijt  ")
    assert own is not None and own.name == "Ontbijt" and own.source == "person"
    await ha.simulate("light.kitchen", "on", {"brightness": 255})
    reply = await hm.talk("ontbijt", "nl", 3, "demo-user-1")
    assert "Ontbijt" in reply["text"] and pct(ha, "light.kitchen") == 30
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    snap = await views.snapshot(hm, viewer)
    kitchen = next(r for r in snap["rooms"] if r["id"] == "kitchen")
    assert kitchen["scenes"][0]["name"] == "Ontbijt"  # most used first
    assert await hm.remove_scene(own.id)
    assert not [s for s in hm.scenes("kitchen") if s.source == "person"]
    assert not await hm.remove_scene("start:cooking")  # the start set stays
    await hm.stop()


async def test_a_long_sentence_is_no_scene_command(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    before = len(ha.calls)
    await hm.talk("wat eten we vanavond eigenlijk met z'n allen", "nl", 3, "demo-user-1")
    assert len(ha.calls) == before
    await hm.stop()
