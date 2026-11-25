"""Niet meer tonen is the house's choice (E-412), from the user's side. The administrator's
wish: the Expert who runs HouseMind decides which messages nobody needs any more; the other
residents, surely those on a lower level, need not bother with it – the choice holds for them
too. Before, each person had a list of their own in Mijn voorkeuren. Each test fails without
the build."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.ha.adapter import HAUser
from housemind.server import views
from housemind.server.logbook import logbook
from housemind.server.web import create_app
from tests.test_cameras import blink, everyone_away, start
from tests.test_notice_quiet import DRIVEWAY_VEHICLE, _bundle, _phone
from tests.test_screens import _next
from tests.test_vesta import _house
from tests.test_vesta_remembers import TOPIC, _about_battery, _battery_low

ADMIN = {"X-Remote-User-Id": "demo-user-1"}
ONE = ADMIN  # the same person, once they are no longer an administrator
RESIDENT = {"X-Remote-User-Id": "demo-user-2"}
VEHICLE = {"text_key": "notify.camera.mute.vehicle", "params": {"where": "Oprit"}}


async def _ws(client: TestClient, headers: dict[str, str]) -> Any:
    ws = await client.ws_connect("/api/ws", headers=headers)
    await ws.send_json({"type": "hello", "browserLanguage": "nl"})
    await _next(ws, "snapshot")
    return ws


async def _settled(ws: Any) -> None:
    """What was sent before has been handled: the Logboek it asks for has come."""
    await ws.send_json({"type": "get", "what": "logbook"})
    while True:
        message = await _next(ws, "data")
        if message.get("what") == "logbook":
            return


async def test_the_administrators_choice_holds_for_everyone(tmp_path: Path) -> None:
    """An administrator switches 'een voertuig bij Oprit' off. From then on nobody in the house
    gets it: not the other resident on their phone, nor on their screen. Their Logboek still
    shows it, as not shown, without Weer tonen – that is the administrator's."""
    hm, clock, ha = await start(tmp_path, {1: 4, 2: 2})
    await everyone_away(hm, clock, ha)
    await blink(ha, "binary_sensor.driveway_vehicle")
    card = await _bundle(hm)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client, ADMIN)
        mute = {"type": "notification", "id": card["id"], "action": "mute"}
        await ws.send_json({**mute, "key": DRIVEWAY_VEHICLE})
        assert "Niemand in huis" in (await _next(ws, "saved"))["text"]
        await ws.close()
    shown = await views.notifications(hm, "nl", False, "demo-user-2")
    assert card["id"] not in [n["id"] for n in shown]
    before = len(await _phone(ha, "phone_2"))
    await clock.advance(31 * 60)
    await blink(ha, "binary_sensor.driveway_vehicle")  # a new message, only vehicles
    assert len(await _phone(ha, "phone_2")) == before
    viewer = await views.load_viewer(hm, "demo-user-2", None, trusted_local=False)
    log = await logbook(hm, viewer, "demo-user-2", "nl")
    line = next(i for i in log["items"] if i["type"] == "notice" and i["id"] == card["id"])
    assert line["hidden"] == "muted" and "unmute" not in line
    await hm.stop()


async def test_a_resident_looks_or_puts_off_but_does_not_switch_off(tmp_path: Path) -> None:
    """A resident who is no administrator – here even on Expert – has Ik kijk ernaar and Later
    on the card, not Niet meer tonen. Sending it anyway switches nothing off, and Weer tonen
    brings nothing back; the house's list is not theirs to ask for."""
    hm, clock, ha = await start(tmp_path, {1: 4, 2: 4})
    assert hm.router is not None
    await everyone_away(hm, clock, ha)
    await blink(ha, "binary_sensor.driveway_vehicle")
    card = await _bundle(hm)
    theirs = views.notice_item(hm, "nl", card, False, "demo-user-2")
    assert theirs is not None and "mute" not in theirs
    assert theirs["actions"] == ["snooze", "dismiss"]
    mine = views.notice_item(hm, "nl", card, True, "demo-user-1")
    assert mine is not None and mine["mute"]
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client, RESIDENT)
        mute = {"type": "notification", "id": card["id"], "action": "mute"}
        await ws.send_json({**mute, "key": DRIVEWAY_VEHICLE})
        await _settled(ws)
        assert hm.router.muted() == {}
        await hm.router.mute(DRIVEWAY_VEHICLE, VEHICLE, clock.now())  # the administrator's
        await ws.send_json({"type": "unmute", "key": DRIVEWAY_VEHICLE})
        await ws.send_json({"type": "get", "what": "muted"})
        await _settled(ws)
        assert set(hm.router.muted()) == {DRIVEWAY_VEHICLE}
        await ws.close()
    await hm.stop()


async def test_negeer_maar_from_a_resident_is_ik_kijk_ernaar(tmp_path: Path) -> None:
    """When Vesta brings up a notice, a resident who is no administrator gets Ik kijk ernaar
    (a notice for information has no Later, QA 3). Their 'negeer maar' then means Ik kijk
    ernaar: the notice closes, and nothing is switched off for the house."""
    hm, ha, clock = await _house(tmp_path, level=4)
    ha.users_list[0] = HAUser("demo-user-1", "Resident 1", is_admin=False)  # Expert, no admin
    await hm._refresh_people()
    await _battery_low(hm, ha, clock)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ONE)
        await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": False})
        await ws.send_json({"type": "orb_tap"})
        said = await _next(ws, "say")
        assert _about_battery(said)
        assert said["ask"]["actions"] == ["seen"]
        await ws.send_json({"type": "talk", "text": "negeer maar"})
        reply = await _next(ws, "say")
        assert "keuzes" not in reply["text"] and "niemand in huis" not in reply["text"].lower()
        await ws.close()
    assert hm.router is not None and hm.router.muted() == {}
    row = await hm.db.fetchone(
        "SELECT resolved_at FROM notifications WHERE topic = ? ORDER BY id DESC", (TOPIC,)
    )
    assert row is not None and row["resolved_at"] is not None  # closed while its cause stays
    await hm.stop()


async def test_the_lists_from_before_become_the_houses(tmp_path: Path) -> None:
    """Before the update each person had their own list. After it, what anyone switched off is
    switched off for the house – once – and stays so after a restart and after 'Vergeet mij':
    it is the house's choice now, not that person's data."""
    hm, clock, _ha = await start(tmp_path, {1: 4, 2: 2})
    assert hm.router is not None
    old = {"demo-user-2": {"muted": {DRIVEWAY_VEHICLE: {**VEHICLE, "since": "2026-10-27"}}}}
    await hm.db.set_setting("notify_quiet", old)
    await hm.db.execute("DELETE FROM settings WHERE key = 'notify_muted'")
    await hm.router.load()
    assert set(hm.router.muted()) == {DRIVEWAY_VEHICLE}
    assert views.muted_list(hm, "nl") == [
        {"key": DRIVEWAY_VEHICLE, "text": "een voertuig bij Oprit"}
    ]
    await hm.router.forget("demo-user-2", clock.now())
    await hm.router.load()
    assert set(hm.router.muted()) == {DRIVEWAY_VEHICLE}
    await hm.stop()
