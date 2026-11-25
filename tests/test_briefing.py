"""Speaking by itself, the morning briefing and the bedtime check (E-19, E-22, E-107, B-32)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, Source, State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")


def local(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 10, day, hh, mm, tzinfo=TZ)


async def start(
    at: datetime,
    tmp_path: Path,
    levels: tuple[int, ...] = (2, 2, 2),
    satellites: tuple[str, ...] = ("kitchen",),
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    for area in satellites:
        entity_id = f"assist_satellite.{area}"
        ha.model.entities[entity_id] = Entity(entity_id, f"r-{entity_id}", "Spraak", area_id=area)
        ha.states[entity_id] = State(entity_id, "idle", {}, clock.now())
    ha.states["weather.home"] = State("weather.home", "cloudy", {"temperature": 9.4}, clock.now())
    ha.forecasts["weather.home"] = [
        {
            "datetime": at.date().isoformat() + "T12:00:00+00:00",
            "condition": "rainy",
            "temperature": 14.2,
            "templow": 7.8,
            "precipitation_probability": 80,
        }
    ]
    ha.states["calendar.family"] = State("calendar.family", "off", {}, clock.now())
    ha.events["calendar.family"] = [
        {"start": local(at.day, 9, 30).isoformat(), "summary": "Tandarts"},
        {"start": local(at.day, 15, 0).isoformat(), "summary": "Zwemles"},
    ]
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    await hm.set_house_setting("calendar", True)  # the administrator's yes (20.19)
    for i, level in enumerate(levels, start=1):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (f"demo-user-{i}", f"Resident {i}", level, json.dumps({}), clock.now().isoformat()),
        )
    await hm._refresh_people()
    return hm, clock, ha


async def wake_up(hm: HouseMind, clock: SimulatedClock, ha: FakeHomeAssistant) -> None:
    """The house was asleep; someone walks into the kitchen."""
    assert hm.world is not None
    hm.world.modes.mode = HouseMode.SLEEP
    hm.world.modes.since = clock.now() - timedelta(hours=8)
    hm._last_mode = HouseMode.SLEEP
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    await clock.advance(30)
    await hm.tick()


async def evening_on_the_sofa(hm: HouseMind, clock: SimulatedClock, ha: FakeHomeAssistant) -> None:
    """Someone sits in the living room until bedtime, then walks to the bedroom."""
    await ha.simulate("binary_sensor.living_presence", "on")
    for _ in range(80):
        await clock.advance(30)
        await hm.tick()
    await ha.simulate("binary_sensor.living_presence", "off")
    await clock.advance(30)
    await hm.tick()
    assert hm.world is not None and hm.world.modes.mode is HouseMode.HOME


async def test_the_morning_briefing_is_spoken_once_where_someone_is(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 6, 55), tmp_path)
    await wake_up(hm, clock, ha)
    assert len(ha.spoken) == 1
    target, text = ha.spoken[0]
    assert target == "assist_satellite.kitchen"
    frames = [
        v.split("{text}")[0].strip() for v in hm.vesta_phrases.variants("nl", "morning", False)
    ]
    assert any(text.startswith(f) for f in frames)  # in Vesta's words (B-80)
    assert "regen" in text and "tussen 8 en 14 graden" in text and "80%" in text
    assert "om 09:30: Tandarts" in text and "2 dingen" in text
    for _ in range(6):  # once a day
        await clock.advance(60)
        await hm.tick()
    assert len(ha.spoken) == 1
    await hm.stop()


async def test_nobody_on_carefree_at_home_or_nothing_is_said(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 6, 55), tmp_path, levels=(2, 1, 3))
    await wake_up(hm, clock, ha)
    assert ha.spoken == []  # the calmest level at home decides (E-22)
    await hm.stop()


async def test_the_briefing_on_the_home_screen_follows_your_choice(tmp_path: Path) -> None:
    hm, clock, _ha = await start(local(19, 7, 30), tmp_path)
    # The card in the Logboek, from the same function (the screen reads it there, QA 3).
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    card = await views.morning_card(hm, viewer, "nl")
    assert card is not None and "Goedemorgen." in card and "Tandarts" in card
    await hm.db.execute(
        "UPDATE people SET preferences = ? WHERE user_id = ?",
        (
            json.dumps({"briefing": {"weather": True, "agenda": False, "energy": True}}),
            "demo-user-1",
        ),
    )
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    card = await views.morning_card(hm, viewer, "nl")
    assert card is not None and "Tandarts" not in card and "regen" in card
    snap = await views.snapshot(hm, viewer)
    assert snap["prefs"]["briefing"]["agenda"] is False
    await clock.advance(4 * 3600)  # after 11:00 the card is gone
    assert await views.morning_card(hm, viewer, "nl") is None
    await hm.stop()


async def test_bedtime_check_reports_what_is_open_and_never_acts(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 21, 50), tmp_path, satellites=())
    await evening_on_the_sofa(hm, clock, ha)
    await ha.simulate("binary_sensor.front_door", "on")
    await ha.simulate("lock.front_door", "unlocked")
    calls_before = len(ha.calls)
    await ha.simulate("light.bedroom", "on", by=Source.PHYSICAL)  # someone goes to bed
    pushed = [n for n in ha.notifications if "Voor het slapen" in n[2]]
    assert len(pushed) >= 1
    message = pushed[0][2]
    assert "staat open" in message and "niet op slot" in message
    assert not [c for c in ha.calls[calls_before:] if c[0].startswith("lock.")]  # never acts
    await ha.simulate("light.bedroom", "off", by=Source.PHYSICAL)
    await ha.simulate("light.bedroom", "on", by=Source.PHYSICAL)
    assert len([n for n in ha.notifications if "Voor het slapen" in n[2]]) == len(pushed)
    await hm.stop()


async def test_all_closed_is_said_quietly_in_the_bedroom(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 21, 50), tmp_path, satellites=("bedroom",))
    await evening_on_the_sofa(hm, clock, ha)
    await ha.simulate("light.bedroom", "on", by=Source.PHYSICAL)
    ok = hm.vesta_phrases.variants("nl", "bedtime_ok", False)
    assert [t for t, _ in ha.spoken] == ["assist_satellite.bedroom"] and ha.spoken[0][1] in ok
    await hm.stop()


async def test_an_emergency_is_spoken_everywhere_also_at_night(tmp_path: Path) -> None:
    hm, _clock, ha = await start(
        local(19, 3, 0), tmp_path, levels=(1, 1, 1), satellites=("kitchen", "bedroom")
    )
    assert hm.world is not None
    hm.world.modes.mode = HouseMode.SLEEP
    await ha.simulate("binary_sensor.kitchen_smoke", "on")
    assert {target for target, _ in ha.spoken} == {
        "assist_satellite.kitchen",
        "assist_satellite.bedroom",
    }
    await hm.stop()


async def test_an_expert_chooses_how_often_houseMind_speaks(tmp_path: Path) -> None:
    hm, clock, ha = await start(local(19, 6, 55), tmp_path, levels=(4, 4, 4))
    for i in (1, 2, 3):
        await hm.db.execute(
            "UPDATE people SET preferences = ? WHERE user_id = ?",
            (json.dumps({"spoken_per_day": 0}), f"demo-user-{i}"),
        )
    await hm._refresh_people()
    await wake_up(hm, clock, ha)
    assert ha.spoken == []  # zero a day: HouseMind does not speak by itself
    await hm.stop()


async def test_an_ai_never_gets_a_calendar_title_or_a_lock(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """QA 2 (H-8, B-173): the morning briefing put the title of an appointment in a sentence
    that went to the AI that words Vesta's lines; the bedtime check sent "... is not locked".
    Now an AI gets only that there is something and when; a lock never. What Vesta says in
    the house still names the appointment."""
    hm, clock, ha = await start(local(19, 6, 55), tmp_path)
    sent: list[dict[str, Any]] = []

    async def fake_vesta(self: Any, persona: Any, messages: Any, language: str, names: Any) -> None:
        sent.extend(messages)
        return None  # she keeps her own words

    monkeypatch.setattr(type(hm.ai), "status", lambda self: "on")
    monkeypatch.setattr(type(hm.ai), "vesta", fake_vesta)
    await wake_up(hm, clock, ha)
    assert "Tandarts" in ha.spoken[0][1]  # spoken in the house
    morning = [m for m in sent if m["kind"] == "morning"]
    assert morning and "om 09:30" in morning[0]["facts"]["text"]
    assert not any("Tandarts" in json.dumps(m) or "Zwemles" in json.dumps(m) for m in sent)
    await hm.stop()

    hm, clock, ha = await start(local(19, 21, 50), tmp_path / "evening", satellites=("bedroom",))
    sent.clear()
    monkeypatch.setattr(type(hm.ai), "status", lambda self: "on")
    monkeypatch.setattr(type(hm.ai), "vesta", fake_vesta)
    await evening_on_the_sofa(hm, clock, ha)
    await ha.simulate("binary_sensor.front_door", "on")
    await ha.simulate("lock.front_door", "unlocked")
    await ha.simulate("light.bedroom", "on", by=Source.PHYSICAL)
    # The check before bed is important: Vesta says it in HouseMind's own words, so it does
    # not go to the AI at all – nor does the lock in it (E-222).
    assert not [m for m in sent if m["kind"] == "bedtime_found"]
    assert not any("slot" in json.dumps(m) for m in sent)
    await hm.stop()
