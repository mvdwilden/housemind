"""Cameras (E-180 – E-187, E-189, E-190; design 16.1 – 16.5, 16.7 – 16.9): what a detection
means, a message with an image only when everyone is away or in an alarm situation, the
doorbell on the wall tablet. Each test fails without the function; what it checks is its
docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.brain.cameras import CameraIndex, load_camera_words
from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, HouseModel, State, StateChange
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
WORDS = load_camera_words([ROOT / "knowledge" / "security"])
DAY = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)  # Monday 14:00 in the house
EVENING = datetime(2026, 10, 19, 19, 30, tzinfo=UTC)  # 21:30, dark


async def start(
    tmp_path: Path, levels: dict[int, int] | None = None, at: datetime = DAY, consents: Any = None
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at)
    house = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    for i, level in (levels or {1: 2, 2: 1}).items():
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (
                f"demo-user-{i}",
                f"Resident {i}",
                level,
                json.dumps((consents or {}).get(i, {})),
                clock.now().isoformat(),
            ),
        )
    await hm._refresh_people()
    return hm, clock, house.ha


async def everyone_away(hm: HouseMind, clock: SimulatedClock, ha: FakeHomeAssistant) -> None:
    for i in (1, 2, 3, 4):
        await ha.simulate(f"person.resident_{i}", "not_home")
    await clock.advance(11 * 60)
    await hm.tick()
    assert hm.world is not None and hm.world.modes.mode is HouseMode.AWAY


async def fire(ha: FakeHomeAssistant, clock: SimulatedClock, entity_id: str, kind: str) -> None:
    """An event entity reports one event, as Home Assistant does: a new time and its type."""
    await ha.simulate(entity_id, clock.now().isoformat(), {"event_type": kind})


async def blink(ha: FakeHomeAssistant, entity_id: str) -> None:
    await ha.simulate(entity_id, "on")
    await ha.simulate(entity_id, "off")


def camera_pushes(ha: FakeHomeAssistant, prefix: str = "camera:") -> list[Any]:
    return [n for n in ha.notifications if str(n[3].get("tag", "")).startswith(prefix)]


# -- recognising (16.1) ---------------------------------------------------------------------


async def test_every_camera_and_what_it_reports(tmp_path: Path) -> None:
    """E-180: event entities, binary sensors and the camera that goes with them – from the
    knowledge file, not per brand: the driveway's person and vehicle sensors, the garden's
    smart detections, the doorbell, the hall's motion and glass breaking."""
    hm, _, _ = await start(tmp_path)
    assert hm.cameras is not None
    sources = hm.cameras.sources
    assert (
        sources["binary_sensor.driveway_person"].kind,
        sources["binary_sensor.driveway_person"].camera,
    ) == ("person", "camera.driveway")
    assert sources["binary_sensor.driveway_vehicle"].kind == "vehicle"
    assert sources["event.garden_detection"].kinds == ("person", "vehicle", "animal", "package")
    assert sources["event.doorbell"].default == "doorbell"
    assert sources["event.doorbell"].camera == "camera.front_door"
    assert sources["binary_sensor.front_door_package"].kind == "package"
    assert sources["binary_sensor.hall_camera_motion"].kind == "motion"
    assert sources["binary_sensor.hall_glass_break"].kind == "glass"
    assert "binary_sensor.hall_smoke" not in sources  # HA's own smoke sensor stays with E-80
    await hm.stop()


def _model(*entities: Entity, device: str = "Babykamer") -> HouseModel:
    model = HouseModel()
    model.devices["d1"] = Device("d1", device, None, "x")
    model.entities["camera.baby"] = Entity("camera.baby", "r0", f"{device} camera", "d1")
    for entity in entities:
        model.entities[entity.entity_id] = entity
    return model


def test_who_it_is_never_counts_and_the_device_name_does_not_decide() -> None:
    """E-32, 16.1: a recognised face is "a person", a plate "a vehicle"; NFC, a fingerprint
    and talking are nothing. A camera in the "Babykamer" does not make every detection a
    crying baby: only the entity's own name counts."""
    person = Entity(
        "binary_sensor.babykamer_person_detected", "r1", "Babykamer Person detected", "d1"
    )
    finger = Entity("binary_sensor.babykamer_fingerprint", "r2", "Babykamer Fingerprint", "d1")
    events = Entity(
        "event.babykamer_smart",
        "r3",
        "Babykamer Smart",
        "d1",
        capabilities={"event_types": ["face_known", "license_plate", "nfc"]},
    )
    index = CameraIndex(_model(person, finger, events), WORDS)
    assert index.sources[person.entity_id].kind == "person"
    assert finger.entity_id not in index.sources
    assert index.sources[events.entity_id].kinds == ("person", "vehicle")
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    nfc = StateChange(
        events.entity_id,
        State(events.entity_id, "2026-10-19T11:00:00+00:00", {}, now),
        State(events.entity_id, now.isoformat(), {"event_type": "nfc"}, now),
    )
    assert index.detect(nfc, now) is None


def test_an_old_event_or_one_coming_back_is_no_new_detection() -> None:
    """16.1: after Home Assistant restarts, an event entity shows its last event again – that
    is no one at the door now."""
    doorbell = Entity("event.babykamer_ring", "r1", "Babykamer Ring", "d1", device_class="doorbell")
    index = CameraIndex(_model(doorbell), WORDS)
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    old = (now - timedelta(minutes=10)).isoformat()

    def change(before: str, after: str) -> StateChange:
        return StateChange(
            doorbell.entity_id,
            State(doorbell.entity_id, before, {}, now),
            State(doorbell.entity_id, after, {"event_type": "ring"}, now),
        )

    assert index.detect(change("unknown", now.isoformat()), now) is not None  # the first ring
    assert index.detect(change("unknown", old), now) is None  # ten minutes old
    assert index.detect(change("unavailable", now.isoformat()), now) is None  # back online


# -- what a detection means (16.2, 16.4) ----------------------------------------------------


async def test_someone_on_the_driveway_while_away_gets_one_message_with_the_image(
    tmp_path: Path,
) -> None:
    """E-182, E-186, 16.4: everyone away – an important message with the camera's image path,
    to Informed and up but not Carefree; a second detection within ten minutes updates the
    same message ("2 keer"), it does not send one more."""
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1})
    await everyone_away(hm, clock, ha)
    await blink(ha, "binary_sensor.driveway_person")
    pushes = camera_pushes(ha)
    assert [p[0] for p in pushes] == ["notify.mobile_app_phone_1"]  # not Carefree (E-190)
    _service, _title, message, data = pushes[0]
    assert data["image"] == "/api/camera_proxy/camera.driveway"
    assert data["url"] == "entityId:camera.driveway"
    assert message.startswith("Iemand bij Oprit")
    await clock.advance(120)
    await blink(ha, "binary_sensor.driveway_person")
    again = camera_pushes(ha)
    assert len(again) == 2 and again[1][3]["tag"] == data["tag"]  # replaced, same message
    assert again[1][2].startswith("2 keer iemand bij Oprit")
    rows = await hm.db.fetchall("SELECT id FROM notifications WHERE topic LIKE 'camera:%'")
    assert len(rows) == 1
    await hm.stop()


async def test_at_home_or_asleep_a_person_is_presence_not_a_message(tmp_path: Path) -> None:
    """E-181, E-182: at home a person on camera counts as someone in that room (inside also a
    camera's own motion); asleep there is no message for a detection either."""
    hm, clock, ha = await start(tmp_path)
    assert hm.world is not None
    await blink(ha, "binary_sensor.driveway_person")
    await blink(ha, "binary_sensor.hall_camera_motion")
    snap = hm.world.snapshot(clock.now())
    assert snap.occupied("driveway") and snap.occupied("hall")
    hm.world.modes.set_manual(HouseMode.SLEEP, clock.now())
    await clock.advance(15 * 60)
    await fire(ha, clock, "event.garden_detection", "person")
    assert camera_pushes(ha) == []
    await hm.stop()


async def test_an_animal_is_nothing_at_all(tmp_path: Path) -> None:
    """E-90, E-182: a cat in the garden while everyone is away – no message, no one in the
    garden, nothing kept."""
    hm, clock, ha = await start(tmp_path)
    await everyone_away(hm, clock, ha)
    await fire(ha, clock, "event.garden_detection", "animal")
    assert hm.world is not None
    assert not hm.world.snapshot(clock.now()).occupied("garden")
    assert camera_pushes(ha) == []
    assert await hm.db.fetchall("SELECT * FROM detections") == []
    await hm.stop()


async def test_a_package_is_information_and_a_line_in_the_day_summary(tmp_path: Path) -> None:
    """E-182, E-190: a package gives an information message (it waits for the day's bundle,
    no push now) and on Informed the day summary says "Pakket bezorgd om 14:02"."""
    hm, clock, ha = await start(tmp_path)
    await clock.advance(120)
    await blink(ha, "binary_sensor.front_door_package")
    assert camera_pushes(ha) == []
    rows = await hm.db.fetchall("SELECT kind FROM notifications WHERE topic LIKE 'camera:%'")
    assert [r["kind"] for r in rows] == ["info"]
    summary = await views.day_summary(hm, "nl")
    assert "Pakket bezorgd om 14:02." in summary
    await clock.advance(2 * 3600)  # a second package later: the same message, counted
    await hm.tick()
    await blink(ha, "binary_sensor.front_door_package")
    rows = await hm.db.fetchall(
        "SELECT params FROM notifications WHERE topic LIKE 'camera:%' AND resolved_at IS NULL"
    )
    assert [json.loads(r["params"])["count"] for r in rows] == [2]  # it waits for the bundle
    await hm.stop()


async def test_glass_breaking_inside_comes_at_night_with_the_image(tmp_path: Path) -> None:
    """16.3, B-64: glass breaking inside is an alarm situation – a message also while the
    house sleeps, with the image; Carefree gets it after a yes to messages in danger."""
    hm, clock, ha = await start(
        tmp_path, {1: 2, 2: 1, 3: 1}, at=EVENING, consents={2: {"emergency_push": True}}
    )
    assert hm.world is not None
    hm.world.modes.set_manual(HouseMode.SLEEP, clock.now())
    await blink(ha, "binary_sensor.hall_glass_break")
    pushes = camera_pushes(ha, "glass:")
    assert {p[0] for p in pushes} == {"notify.mobile_app_phone_1", "notify.mobile_app_phone_2"}
    assert pushes[0][3]["image"] == "/api/camera_proxy/camera.hall"
    await hm.stop()


async def test_outside_in_the_dark_a_person_switches_the_lamp_not_an_animal(
    tmp_path: Path,
) -> None:
    """E-183: the lamp on the driveway is asked for only for a person in the dark – not for
    an animal or a car, and not in daylight. It passes the gate like every action."""
    hm, clock, ha = await start(tmp_path, at=EVENING)
    hm.world.states["sun.sun"] = State(
        "sun.sun", "below_horizon", {"elevation": -12.0}, clock.now()
    )  # type: ignore[union-attr]
    await fire(ha, clock, "event.garden_detection", "animal")
    await blink(ha, "binary_sensor.driveway_vehicle")
    lamps = [d.request for d in hm.status.thoughts if d.request.entity_id == "light.driveway"]
    assert lamps == []
    await blink(ha, "binary_sensor.driveway_person")
    lamps = [d.request for d in hm.status.thoughts if d.request.entity_id == "light.driveway"]
    assert [(r.action, r.skill) for r in lamps] == [("light.turn_on", "light_on_person_outside")]
    await hm.stop()


async def test_optional_sounds_only_after_a_choice(tmp_path: Path) -> None:
    """16.2: barking is not used by default; after an administrator chooses it, it is an
    information message."""
    hm, clock, ha = await start(tmp_path)
    await fire(ha, clock, "event.garden_detection", "bark")
    assert await hm.db.fetchall("SELECT * FROM notifications WHERE topic LIKE 'camera:%'") == []
    assert await hm.set_house_setting("camera_sounds", ["bark", "nonsense"])
    await clock.advance(5)
    await fire(ha, clock, "event.garden_detection", "bark")
    rows = await hm.db.fetchall("SELECT kind FROM notifications WHERE topic LIKE 'camera:%'")
    assert [r["kind"] for r in rows] == ["info"]
    assert await hm.db.get_setting("camera_sounds") == ["bark"]
    await hm.stop()


# -- the doorbell on the wall tablet (16.5) -------------------------------------------------


async def test_the_doorbell_shows_on_the_wall_tablet_for_thirty_seconds(tmp_path: Path) -> None:
    """E-187, B-66: the doorbell's camera on every wall tablet, 30 seconds, the live image
    straight from Home Assistant with the camera's own key; at home no phone message."""
    hm, clock, ha = await start(tmp_path)
    assert hm.world is not None
    cam = hm.world.states["camera.front_door"]
    hm.world.states["camera.front_door"] = State(
        cam.entity_id, cam.state, {**cam.attributes, "access_token": "abc"}, cam.last_changed
    )
    await fire(ha, clock, "event.doorbell", "ring")
    bell = views.tablet_view(hm, "living", "nl")["doorbell"]  # type: ignore[index]
    assert bell["url"] == "/api/camera_proxy_stream/camera.front_door?token=abc"
    assert bell["where"] == "Gang" and bell["left"] == 30
    assert camera_pushes(ha) == []  # someone is home: the doorbell rings itself
    await clock.advance(31)
    await hm.tick()
    assert views.tablet_view(hm, "living", "nl")["doorbell"] is None  # type: ignore[index]
    await hm.stop()


async def test_the_doorbell_while_away_is_a_message_with_the_image(tmp_path: Path) -> None:
    """16.2: nobody home – someone at the door is an important message with the image. A
    package at the same camera stays a message of its own, and waits for the bundle when the
    message for while away goes because someone came home."""
    hm, clock, ha = await start(tmp_path)
    await everyone_away(hm, clock, ha)
    await blink(ha, "binary_sensor.front_door_package")
    await fire(ha, clock, "event.doorbell", "ring")
    pushes = camera_pushes(ha)
    assert pushes and pushes[0][3]["image"] == "/api/camera_proxy/camera.front_door"
    assert "aangebeld" in pushes[0][2]
    await ha.simulate("person.resident_1", "home")
    await hm.tick()
    open_ = await hm.db.fetchall(
        "SELECT text_key FROM notifications WHERE topic LIKE 'camera:%' AND resolved_at IS NULL"
    )
    assert [r["text_key"] for r in open_] == ["notify.camera.package"]
    await hm.stop()


# -- per level, privacy, the diagnostics (16.7 – 16.9) --------------------------------------


async def test_expert_sees_per_camera_what_is_followed(tmp_path: Path) -> None:
    """E-190: on the Devices page, per camera which detections HouseMind follows, and per
    detecting entity what it counts as."""
    hm, _, _ = await start(tmp_path)
    rows = {r["id"]: r for r in views.devices(hm, "nl")}
    assert rows["camera.driveway"]["detects"].startswith("Volgt: persoon, voertuig.")
    assert "nooit beelden" in rows["camera.driveway"]["detects"]
    assert rows["binary_sensor.driveway_person"]["detects"].startswith("Telt als persoon")
    await hm.stop()


async def test_a_detection_is_kept_as_a_kind_a_room_and_a_time_for_30_days(
    tmp_path: Path,
) -> None:
    """E-189, E-18, 16.8: only the kind, the room, the camera and the time; never an image
    path; gone after 30 days. The diagnostics count them per kind – numbers only (16.9)."""
    hm, clock, ha = await start(tmp_path)
    await everyone_away(hm, clock, ha)
    await blink(ha, "binary_sensor.driveway_person")
    rows = await hm.db.fetchall("SELECT * FROM detections")
    assert [dict(r).keys() - {"id", "at"} for r in rows] == [{"kind", "area_id", "camera"}]
    assert (rows[0]["kind"], rows[0]["area_id"]) == ("person", "driveway")
    saved = await hm.db.fetchall("SELECT params FROM notifications")
    assert not any("camera_proxy" in str(r["params"]) for r in saved)
    counted = (await hm.diagnostics())["detections"]
    assert counted["seen"] == {"person": 1}
    assert counted["followed"]["person"] == 2 and counted["followed"]["doorbell"] == 1
    await hm.db.cleanup(clock.now() + timedelta(days=31))
    assert await hm.db.fetchall("SELECT * FROM detections") == []
    await hm.stop()


def test_names_as_a_real_house_gives_them() -> None:
    """Found in the real house (20-10-2026): Dutch sensor names such as "Rookalarm
    gedetecteerd" without a class, event entities whose type only says "detected" (the kind is
    in their own name), NFC and fingerprint events, and "Spreken gedetecteerd"."""

    def entity(entity_id: str, name: str, **kw: Any) -> Entity:
        return Entity(entity_id, f"r-{entity_id}", f"Camera - Oprit {name}", "d1", **kw)

    model = HouseModel()
    model.devices["d1"] = Device("d1", "Camera - Oprit", None, "x")
    found = [
        entity("camera.camera_oprit_high", "High resolution channel"),
        entity("binary_sensor.camera_oprit_persoon_gedetecteerd", "Persoon gedetecteerd"),
        entity("binary_sensor.camera_oprit_rookalarm_gedetecteerd", "Rookalarm gedetecteerd"),
        entity("binary_sensor.camera_oprit_spreken_gedetecteerd", "Spreken gedetecteerd"),
        entity("binary_sensor.camera_oprit_is_donker", "Is donker"),
        entity("binary_sensor.camera_oprit_beweging", "Beweging", device_class="motion"),
        entity(
            "event.camera_oprit_voertuig", "Voertuig", capabilities={"event_types": ["detected"]}
        ),
        entity("event.camera_oprit_nfc", "NFC", capabilities={"event_types": ["scanned"]}),
        entity(
            "event.camera_oprit_vingerafdruk",
            "Vingerafdruk",
            capabilities={"event_types": ["identified", "not_identified"]},
        ),
    ]
    for e in found:
        model.entities[e.entity_id] = e
    index = CameraIndex(model, WORDS)
    assert {k: (s.kind or s.default) for k, s in index.sources.items()} == {
        "binary_sensor.camera_oprit_persoon_gedetecteerd": "person",
        "binary_sensor.camera_oprit_beweging": "motion",
        "event.camera_oprit_voertuig": "vehicle",
    }
    assert index.smoke == {"binary_sensor.camera_oprit_rookalarm_gedetecteerd": "smoke"}
    now = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
    seen = StateChange(
        "event.camera_oprit_voertuig",
        State("event.camera_oprit_voertuig", "unknown", {}, now),
        State("event.camera_oprit_voertuig", now.isoformat(), {"event_type": "detected"}, now),
    )
    detection = index.detect(seen, now)
    assert detection is not None and detection.kind == "vehicle"


async def test_a_ring_reported_twice_is_one_ring(tmp_path: Path) -> None:
    """Found in the real house: the doorbell is an event entity and a binary sensor; one
    ring is one ring, not "2 keer aangebeld"."""
    hm, clock, ha = await start(tmp_path)
    assert hm.cameras is not None
    await everyone_away(hm, clock, ha)
    sensor = "binary_sensor.front_door_ring"
    hm.model.entities[sensor] = Entity(
        sensor, "r-ring", "Gang deurbel", "device.doorbell_camera", "hall"
    )
    ha.states[sensor] = State(sensor, "off", {}, clock.now())
    hm.world.states[sensor] = ha.states[sensor]  # type: ignore[union-attr]
    from housemind.brain import security

    security.rebuild(hm)
    await fire(ha, clock, "event.doorbell", "ring")
    await blink(ha, sensor)
    pushes = camera_pushes(ha)
    assert len(pushes) == 1 and "aangebeld om" in pushes[0][2]
    assert len(await hm.db.fetchall("SELECT * FROM detections WHERE kind = 'doorbell'")) == 1
    await hm.stop()
