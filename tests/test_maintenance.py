"""Maintenance and reminders (domain 16): batteries, silent devices, filters, the bins."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")


async def start(
    at: datetime, tmp_path: Path, extra: list[tuple[Entity, State]] | None = None
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    for entity, state in extra or []:
        house.ha.model.entities[entity.entity_id] = entity
        house.ha.states[entity.entity_id] = state
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, clock, house.ha


async def review(hm: HouseMind, clock: SimulatedClock, minutes: int = 16) -> None:
    await clock.advance(minutes * 60)
    hm._last_review = None
    await hm.tick()


async def open_topics(hm: HouseMind) -> set[str]:
    return {str(n["topic"]) for n in await hm.notifications()}


async def test_a_low_battery_and_a_silent_device_get_a_word(tmp_path: Path) -> None:
    at = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)
    long_ago = at.astimezone(UTC) - timedelta(hours=30)
    battery = Entity(
        "sensor.front_door_battery",
        "r-b",
        "Voordeur batterij",
        device_id="binary_sensor.front_door",
        device_class="battery",
        unit="%",
    )
    hm, clock, ha = await start(
        at,
        tmp_path,
        [
            (
                battery,
                State("sensor.front_door_battery", "9", {"unit_of_measurement": "%"}, long_ago),
            )
        ],
    )
    ha.states["light.toilet"] = State("light.toilet", "unavailable", {}, long_ago)
    hm.world.states["light.toilet"] = ha.states["light.toilet"]  # type: ignore[union-attr]
    await review(hm, clock)
    topics = await open_topics(hm)
    assert "maintenance:battery:sensor.front_door_battery" in topics
    # A lamp that has not answered for a day is part of Home Assistant netjes now, for the
    # administrators (E-354, B-238): no maintenance word for everyone at home any more.
    assert "maintenance:offline:light.toilet" not in topics
    rows = await hm.db.fetchall("SELECT kind, text_key, params FROM notifications")
    texts = [hm.languages.text("nl", r["text_key"], **json.loads(r["params"])) for r in rows]
    assert any("bijna leeg (9%)" in t for t in texts)
    # A new battery: the word goes away by itself (E-122).
    await ha.simulate("sensor.front_door_battery", "100")
    await review(hm, clock)
    assert "maintenance:battery:sensor.front_door_battery" not in await open_topics(hm)
    await hm.stop()


async def test_a_silent_smoke_detector_is_important_at_once(tmp_path: Path) -> None:
    at = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)
    hm, clock, ha = await start(at, tmp_path)
    long_ago = at.astimezone(UTC) - timedelta(hours=26)
    ha.states["binary_sensor.hall_smoke"] = State(
        "binary_sensor.hall_smoke", "unavailable", {}, long_ago
    )
    hm.world.states["binary_sensor.hall_smoke"] = ha.states["binary_sensor.hall_smoke"]  # type: ignore[union-attr]
    await review(hm, clock)
    pushed = [n for n in ha.notifications if "veiligheidssensor" in n[2]]
    assert pushed  # important: straight to the phone, not in the evening bundle
    await hm.stop()


async def test_a_camera_with_its_smoke_detection_off_is_no_silent_smoke_detector(
    tmp_path: Path,
) -> None:
    """A camera that also offers smoke and CO detection: with those switched off in its own
    settings, Home Assistant reports them as unavailable while the camera itself works. That
    gave an important 'safety sensor does not answer' – also after Niet meer tonen. Now a
    safety sensor counts only when its whole device is silent; a real silent smoke detector
    still gets the word, even if a diagnostic part of it keeps a value."""
    at = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)
    long_ago = at.astimezone(UTC) - timedelta(hours=26)
    now = at.astimezone(UTC)

    def part(
        entity_id: str, device: str, state: str, cls: str | None = None, cat: str | None = None
    ) -> tuple[Entity, State]:
        entity = Entity(
            entity_id,
            f"r-{entity_id}",
            entity_id,
            device_id=device,
            device_class=cls,
            entity_category=cat,
        )
        return entity, State(entity_id, state, {}, long_ago if state == "unavailable" else now)

    hm, clock, ha = await start(
        at,
        tmp_path,
        [
            part("camera.garage_roof", "dev-camera", "idle"),
            part("binary_sensor.garage_roof_motion", "dev-camera", "off", "motion"),
            part("binary_sensor.garage_roof_smoke", "dev-camera", "unavailable", "smoke"),
            part("binary_sensor.garage_roof_co", "dev-camera", "unavailable", "carbon_monoxide"),
            part("binary_sensor.attic_smoke", "dev-attic", "unavailable", "smoke"),
            part("sensor.attic_smoke_battery", "dev-attic", "80", "battery", "diagnostic"),
        ],
    )
    assert hm.world is not None
    for entity_id in [e for e in ha.states if "garage_roof" in e or "attic" in e]:
        hm.world.states[entity_id] = ha.states[entity_id]
    await review(hm, clock)
    topics = await open_topics(hm)
    assert "maintenance:offline:dev-camera" not in topics  # the camera answers
    assert "maintenance:offline:dev-attic" in topics  # the smoke detector does not
    await hm.stop()


async def test_the_evening_before_the_bins_are_collected(tmp_path: Path) -> None:
    at = datetime(2026, 10, 19, 17, 30, tzinfo=TZ)
    hm, clock, ha = await start(at, tmp_path)
    await hm.set_house_setting("calendar", True)  # the administrator's yes (20.19)
    ha.states["calendar.waste"] = State("calendar.waste", "off", {}, clock.now())
    hm.world.states["calendar.waste"] = ha.states["calendar.waste"]  # type: ignore[union-attr]
    ha.events["calendar.waste"] = [
        {"start": "2026-10-20", "summary": "GFT"},
        {"start": "2026-10-20", "summary": "Tandarts"},
    ]
    await review(hm, clock)
    assert "reminder:waste:2026-10-20" in await open_topics(hm)
    rows = await hm.db.fetchall(
        "SELECT text_key, params FROM notifications WHERE topic LIKE 'reminder:%'"
    )
    text = hm.languages.text("nl", rows[0]["text_key"], **json.loads(rows[0]["params"]))
    assert text == "Morgen wordt het afval opgehaald: GFT. Zet de container buiten."
    # The next day it is gone by itself.
    await review(hm, clock, minutes=24 * 60)
    assert "reminder:waste:2026-10-20" not in await open_topics(hm)
    await hm.stop()


async def test_updates_waiting_in_home_assistant_go_into_the_report(tmp_path: Path) -> None:
    at = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)
    hm, clock, _ha = await start(at, tmp_path)
    for name in ("update.core", "update.zigbee"):
        hm.world.states[name] = State(name, "on", {}, clock.now())  # type: ignore[union-attr]
    data = await hm.week_data(clock.now())
    assert "Home Assistant heeft 2 updates klaarstaan." in hm.report_text(data, 3, "nl")
    assert not any("update" in line.lower() for line in hm.report_text(data, 2, "nl"))
    await hm.stop()


async def test_a_home_battery_is_no_battery_to_replace(tmp_path: Path) -> None:
    at = datetime(2026, 10, 19, 10, 0, tzinfo=TZ)
    soc = Entity(
        "sensor.house_battery_soc",
        "r-s",
        "Thuisbatterij",
        device_id="dev.bat",
        device_class="battery",
        unit="%",
    )
    power = Entity(
        "sensor.house_battery_power",
        "r-p",
        "Thuisbatterij vermogen",
        device_id="dev.bat",
        device_class="power",
        unit="W",
    )
    now = at.astimezone(UTC)
    hm, clock, _ = await start(
        at,
        tmp_path,
        [
            (soc, State(soc.entity_id, "8", {"unit_of_measurement": "%"}, now)),
            (power, State(power.entity_id, "0", {"unit_of_measurement": "W"}, now)),
        ],
    )
    await review(hm, clock)
    assert not [t for t in await open_topics(hm) if "battery" in t]
    await hm.stop()
