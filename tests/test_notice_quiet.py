"""Fewer messages, by the person's own choice (E-372, E-373) – the administrator's wish from
the real house: 29 camera messages in three hours while nobody was home, a "Later" that only
hid one card for an hour, and no way to say "not this any more". Now the cameras are one
message while nobody is home, "Later" asks how long, and "Niet meer tonen" switches a kind of
message off – for the whole house since E-412 – to switch on again in Logboek. Each test fails
without it; what it checks is its docstring."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.notify import ALL_CAMERAS, CAMERA_BUNDLE
from housemind.runtime import HouseMind
from housemind.server import views
from housemind.server.logbook import logbook
from housemind.server.web import create_app
from tests.test_cameras import blink, camera_pushes, everyone_away, fire, start
from tests.test_screens import _next

DRIVEWAY_VEHICLE = "camera:driveway:vehicle"


async def _bundle(hm: HouseMind) -> dict[str, Any]:
    row = await hm.db.fetchone(
        "SELECT * FROM notifications WHERE topic = ? AND resolved_at IS NULL", (CAMERA_BUNDLE,)
    )
    assert row is not None
    return dict(row)


async def _phone(ha: Any, phone: str) -> list[Any]:
    return [p for p in camera_pushes(ha) if p[0] == f"notify.mobile_app_{phone}"]


async def test_all_cameras_are_one_message_while_nobody_is_home(tmp_path: Path) -> None:
    """E-373: someone on the driveway, a vehicle there and someone in the garden while nobody
    is home – one message with a line for each; another detection updates it quietly (no new
    sound or buzz); after half an hour without one, a new message."""
    hm, clock, ha = await start(tmp_path, {1: 2})
    await everyone_away(hm, clock, ha)
    await blink(ha, "binary_sensor.driveway_person")
    await clock.advance(60)
    await blink(ha, "binary_sensor.driveway_vehicle")
    await clock.advance(60)
    await fire(ha, clock, "event.garden_detection", "person")
    rows = await hm.db.fetchall("SELECT * FROM notifications WHERE topic LIKE 'camera%'")
    assert [r["topic"] for r in rows] == [CAMERA_BUNDLE]
    pushes = await _phone(ha, "phone_1")
    first, last = pushes[0], pushes[-1]
    assert first[3].get("alert_once") is None  # the first one may sound
    assert last[3]["alert_once"] is True and last[3]["push"]["interruption-level"] == "passive"
    assert "Iemand bij Tuin" in last[2] and "Een voertuig bij Oprit" in last[2]
    assert "Iemand bij Oprit" in last[2] and last[2].endswith("Er is niemand thuis.")
    await clock.advance(31 * 60)
    await blink(ha, "binary_sensor.driveway_person")
    rows = await hm.db.fetchall("SELECT * FROM notifications WHERE topic = ?", (CAMERA_BUNDLE,))
    assert len(rows) == 2 and sum(1 for r in rows if r["resolved_at"] is None) == 1
    await hm.stop()


async def test_later_asks_how_long_and_holds_that_kind_for_that_person(tmp_path: Path) -> None:
    """E-372: Later with a choice – here 4 hours. Until then this person gets no new camera
    message, not on the phone and not in the app, while another resident still does; after
    the 4 hours it comes again."""
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 2})
    await everyone_away(hm, clock, ha)
    await blink(ha, "binary_sensor.driveway_person")
    card = await _bundle(hm)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await _next(ws, "snapshot")
        await ws.send_json(
            {"type": "notification", "id": card["id"], "action": "snooze", "for": "4h"}
        )
        await _next(ws, "snapshot")
    assert hm.router is not None
    until = hm.router.later_of("demo-user-1", ALL_CAMERAS, clock.now())
    assert until is not None and round((until - clock.now()).total_seconds() / 3600) == 4
    before = len(await _phone(ha, "phone_1"))
    await clock.advance(31 * 60)
    await blink(ha, "binary_sensor.driveway_person")  # a new message
    assert len(await _phone(ha, "phone_1")) == before  # not for this person
    assert len(await _phone(ha, "phone_2")) == 2  # another resident still gets it
    mine = await views.notifications(hm, "nl", False, "demo-user-1")
    theirs = await views.notifications(hm, "nl", False, "demo-user-2")
    assert not [n for n in mine if n["id"] == (await _bundle(hm))["id"]]
    assert [n for n in theirs if n["id"] == (await _bundle(hm))["id"]]
    await clock.advance(4 * 3600)
    await blink(ha, "binary_sensor.driveway_person")
    assert len(await _phone(ha, "phone_1")) == before + 1  # after 4 hours it comes again
    await hm.stop()


async def test_not_shown_any_more_per_line_and_back_from_the_logbook(tmp_path: Path) -> None:
    """E-372: Niet meer tonen on the camera message offers each line – "een voertuig bij
    Oprit" – and all cameras. After switching the vehicle off, its line is gone from this
    person's message and phone, and a message with only vehicles is not shown to them at all;
    Logboek still has it, as not shown, with Weer tonen – which brings it back. Only what the
    card offers can be switched off; an emergency never."""
    hm, clock, ha = await start(tmp_path, {1: 2})
    assert hm.router is not None
    await everyone_away(hm, clock, ha)
    await blink(ha, "binary_sensor.driveway_vehicle")
    card = await _bundle(hm)
    item = views.notice_item(hm, "nl", card, True, "demo-user-1")  # an administrator (E-412)
    assert item is not None
    assert [c["key"] for c in item["mute"]] == [DRIVEWAY_VEHICLE, ALL_CAMERAS]
    assert item["mute"][0]["text"] == "een voertuig bij Oprit"
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await _next(ws, "snapshot")
        fake = {"type": "notification", "id": card["id"], "action": "mute", "key": "open:x"}
        await ws.send_json(fake)  # not on the card: nothing switched off
        assert (await _next(ws, "saved"))["text"] == "Gedaan."
        assert hm.router.muted() == {}
        await ws.send_json({**fake, "key": DRIVEWAY_VEHICLE})
        said = await _next(ws, "saved")
        assert "Niemand in huis" in said["text"]
        assert set(hm.router.muted()) == {DRIVEWAY_VEHICLE}
        # The vehicle-only message is now not shown to this person – but in Logboek it is.
        shown = await views.notifications(hm, "nl", False, "demo-user-1")
        assert card["id"] not in [n["id"] for n in shown]
        viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
        log = await logbook(hm, viewer, "demo-user-1", "nl")
        line = next(i for i in log["items"] if i["type"] == "notice" and i["id"] == card["id"])
        assert line["hidden"] == "muted" and line["unmute"][0]["key"] == DRIVEWAY_VEHICLE
        # Someone on the driveway: a message, without the vehicle line.
        before = len(await _phone(ha, "phone_1"))
        await clock.advance(60)
        await blink(ha, "binary_sensor.driveway_person")
        await blink(ha, "binary_sensor.driveway_vehicle")
        text = (await _phone(ha, "phone_1"))[-1][2]
        assert len(await _phone(ha, "phone_1")) > before
        assert "Iemand bij Oprit" in text and "voertuig" not in text
        # Weer tonen, as Logboek and Huisinstellingen send it.
        await ws.send_json({"type": "unmute", "key": DRIVEWAY_VEHICLE})
        assert "weer" in (await _next(ws, "saved"))["text"]
        assert hm.router.muted() == {}
    emergency = {**card, "kind": "emergency"}
    assert "mute" not in (views.notice_item(hm, "nl", emergency, True, "demo-user-1") or {})
    await hm.stop()


async def test_the_choices_are_kept_over_a_restart(tmp_path: Path) -> None:
    """E-372, E-412: what was switched off is kept over a restart and shows in
    Huisinstellingen in words; 'Vergeet mij' takes what a person put off with Later."""
    hm, clock, _ha = await start(tmp_path, {1: 2})
    assert hm.router is not None
    label = {"text_key": "notify.camera.mute.vehicle", "params": {"where": "Oprit"}}
    await hm.router.mute(DRIVEWAY_VEHICLE, label, clock.now())
    await hm.router.later("demo-user-1", ALL_CAMERAS, clock.now() + timedelta(hours=4), clock.now())
    await hm.router.load()
    assert set(hm.router.muted()) == {DRIVEWAY_VEHICLE}
    assert views.muted_list(hm, "nl") == [
        {"key": DRIVEWAY_VEHICLE, "text": "een voertuig bij Oprit"}
    ]
    assert hm.router.later_of("demo-user-1", ALL_CAMERAS, clock.now()) is not None
    await hm.router.forget("demo-user-1", clock.now())
    await hm.router.load()
    assert hm.router.later_of("demo-user-1", ALL_CAMERAS, clock.now()) is None
    await hm.stop()
