"""The holes the ethical hack of 24-11-2026 found, closed – from the side of the people in the
house and of someone who means harm (QA 3, gaten 1 – 4). Each test fails without the change;
what it checks is its docstring."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.notify import Kind
from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, State
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer
from housemind.server import web
from housemind.server.manual import ESCAPE
from tests.test_screens import _next

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Amsterdam")
ADMIN, RESIDENT = "demo-user-1", "demo-user-2"  # the demo house's administrator and a resident


async def _house(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, Any]:
    clock = SimulatedClock(datetime(2026, 10, 19, 19, 0, tzinfo=TZ).astimezone(UTC))
    demo = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, demo.ha, Paths([ROOT / "knowledge"], tmp_path / "data"))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    assert hm.gate is not None
    hm.gate.state.inactive = False  # a house in use: the introduction is done
    return hm, clock, demo.ha


def _add(hm: HouseMind, ha: Any, entity: Entity, state: str, device: Device | None = None) -> None:
    if device is not None:
        hm.model.devices[device.id] = device
    hm.model.entities[entity.entity_id] = entity
    ha.states[entity.entity_id] = State(entity.entity_id, state, {}, hm.clock.now())


def _try(hm: HouseMind, entity_id: str, action: str, layer: Layer = Layer.PERSON_ANSWER) -> str:
    assert hm.gate is not None
    skill = "light_on_entry" if layer is Layer.REFLEX else None
    request = ActionRequest(layer, entity_id, action, created_at=hm.clock.now(), skill=skill)
    decision = hm.gate.evaluate(request)
    return f"{decision.outcome.value} {decision.reason}"


# -- gat 1: a garage door or gate behind a switch or a button ------------------------------


async def test_a_garage_door_relay_never_opens_whatever_it_is_called_later(
    tmp_path: Path,
) -> None:
    """A relay called "Garagedeur" and a button "Poort open" are door openers. Even when a
    label, an AI finding or the energy setup later says it is a lamp or a machine to start,
    HouseMind never switches it on or presses it – not after a yes, not by itself."""
    hm, _clock, ha = await _house(tmp_path)
    assert hm.gate is not None
    _add(hm, ha, Entity("switch.garagedeur", "d-gd", "Garagedeur", platform="shelly"), "off")
    _add(hm, ha, Entity("button.poort_open", "d-po", "Poort open", platform="esphome"), "unknown")
    hm._opener_roles()  # as at every rebuild of the house
    hm.gate.state.roles["switch.garagedeur"] = "light"  # a label yes, a Klopt …
    hm.gate.state.roles["button.poort_open"] = "appliance_start"  # … or the energy setup
    hm._opener_roles()
    for layer in (Layer.PERSON_ANSWER, Layer.REFLEX):
        assert "hard_limit:garage_gate_door_open" in _try(
            hm, "switch.garagedeur", "switch.turn_on", layer
        )
    assert "hard_limit:garage_gate_door_open" in _try(hm, "button.poort_open", "button.press")
    await hm.stop()


async def test_a_switch_on_a_garage_door_device_is_an_opener_a_washing_machine_is_not(
    tmp_path: Path,
) -> None:
    """A relay whose device reports a garage door is protected, whatever its name; a washing
    machine's plug in the garage is no door: HouseMind may still switch it."""
    hm, _clock, ha = await _house(tmp_path)
    kit = Device("dev-kit", "Shelly 1", area_id=None)
    relay = Entity("switch.shelly_1_relay", "r-sr", "Relay", device_id="dev-kit")
    _add(hm, ha, relay, "off", kit)
    contact = Entity(
        "binary_sensor.shelly_1_input",
        "r-si",
        "Input",
        device_id="dev-kit",
        device_class="garage_door",
    )
    _add(hm, ha, contact, "off")
    _add(hm, ha, Entity("switch.garage_wasmachine", "d-wm", "Garage - Wasmachine"), "off")
    hm._opener_roles()
    assert hm.gate is not None
    assert "switch.shelly_1_relay" in hm.gate.state.openers
    assert "switch.garage_wasmachine" not in hm.gate.state.openers
    assert "hard_limit" in _try(hm, "switch.shelly_1_relay", "switch.turn_on")
    assert "hard_limit" not in _try(hm, "switch.garage_wasmachine", "switch.turn_on")
    await hm.stop()


async def test_a_door_opener_is_never_asked_about_as_a_lamp(tmp_path: Path) -> None:
    """HouseMind asks nobody whether a door opener is a lamp, and never hands it to an AI to
    guess what it is."""
    hm, _clock, ha = await _house(tmp_path)
    _add(hm, ha, Entity("switch.deuropener", "d-do", "Deuropener voordeur"), "off")
    hm._opener_roles()
    answers = await hm._label_answers()
    assert "switch.deuropener" not in {e for e, _ in hm._lamp_candidates(answers)}
    assert "switch.deuropener" not in {e.entity_id for e in await hm.unknown_switches()}
    await hm.stop()


# -- gat 2: a script in the manual through Vesta's name --------------------------------------


async def test_a_name_with_markup_is_never_kept(tmp_path: Path) -> None:
    """A name that could be markup or code is refused – on the screen and from a settings
    file – and one saved by an older version is not used; an ordinary name is."""
    hm, _clock, _ha = await _house(tmp_path)
    assert not await hm.set_house_setting("vesta_name", "<script>x</script>")
    assert not await hm.set_house_setting("vesta_name", "Juno<b>")
    assert await hm.set_house_setting("vesta_name", "Jean-Luc")
    assert hm.vesta_name("nl") == "Jean-Luc"
    await hm.db.set_setting("vesta_name", "<img src=x onerror=alert(1)>")  # an older version
    await hm.stop()
    clock = SimulatedClock(datetime(2026, 10, 20, 19, 0, tzinfo=TZ).astimezone(UTC))
    again = HouseMind(clock, load_demo_house("family_home", clock, "nl").ha, hm.paths)
    await again.start(autorun=False)
    assert again.vesta_name("nl") == "Vesta"
    await again.stop()


async def test_the_manual_runs_only_its_own_scripts(tmp_path: Path) -> None:
    """The manual page with another name for her: the name shows as text, and the page's
    rules let only the manual's own scripts run – never one a name or anything else put in."""
    hm, _clock, _ha = await _house(tmp_path)
    hm._vesta_name = "O'Neil"
    source = hm.library.manuals["nl"]
    async with TestClient(TestServer(web.create_app(hm, tmp_path, ingress_only=False))) as client:
        response = await client.get("/manual/nl")
        page = await response.text()
        rules = response.headers["Content-Security-Policy"]
    assert "O&#x27;Neil" in page
    own = set(re.findall(r"<script>(.*?)</script>", source, re.S)) | {ESCAPE}
    assert rules.count("'sha256-") == len(own)
    hm._vesta_name = None
    await hm.stop()


# -- gat 3: what someone who is not an administrator can do ---------------------------------


async def _told(ws: Any) -> str:
    """The next thing the screen is told that is more than "Bewaard."."""
    while True:
        text = str((await _next(ws, "saved"))["text"])
        if text != "Bewaard.":
            return text


async def _ws(client: TestClient, user: str, tablet: str | None = None) -> Any:
    ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": user})
    hello: dict[str, Any] = {"type": "hello", "browserLanguage": "nl"}
    if tablet:
        hello["tablet"] = tablet
    await ws.send_json(hello)
    await _next(ws, "snapshot")
    return ws


async def test_someone_who_is_no_administrator_pauses_an_hour_and_the_logbook_says_who(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resident who chooses "until I switch it on again" gets an hour, is told why, the
    Logboek says who paused, and the administrators get a message. An administrator can
    pause until resumed."""
    monkeypatch.setattr(web, "INGRESS_PROXY_IP", "127.0.0.1")  # the test plays Ingress
    hm, clock, _ha = await _house(tmp_path)
    assert hm.gate is not None
    app = web.create_app(hm, tmp_path, ingress_only=True)
    async with TestClient(TestServer(app)) as client:
        ws = await _ws(client, RESIDENT)
        await ws.send_json({"type": "pause", "minutes": 0})
        told = await _next(ws, "saved")
        assert "beheerder" in told["text"]
        await ws.close()
        until = hm.gate.state.paused_until
        assert until is not None and until - clock.now() == timedelta(minutes=60)
        raised = [t for t in hm._raised if t.startswith("paused_by:")]
        assert raised and hm._raised[raised[0]][1] == "admins"

        admin = await _ws(client, ADMIN)
        await admin.send_json({"type": "set_level", "level": 4})  # the Logboek is from level 2
        await admin.send_json({"type": "pause", "minutes": 0})
        await admin.send_json({"type": "get", "what": "logbook"})
        while True:
            log = await _next(admin, "data")
            if log.get("what") == "logbook":
                break
        await admin.close()
    assert hm.gate.state.paused_until is not None and hm.gate.state.paused_until.year == 9999
    texts = [i["text"] for i in log["data"]["items"] if i.get("type") == "part"]
    assert any("gepauzeerd door" in t and "tot 20:00" in t for t in texts)
    await hm.stop()


async def test_a_wall_tablet_and_an_automation_pause_at_most_an_hour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anyone at a wall tablet, and HA's pause action without a person (an automation, a
    satellite), pause an hour at most; an administrator through HA as long as they like."""
    monkeypatch.setattr(web, "INGRESS_PROXY_IP", "127.0.0.1")
    hm, clock, _ha = await _house(tmp_path)
    assert hm.gate is not None
    area = next(iter(hm.model.areas))
    async with TestClient(TestServer(web.create_app(hm, tmp_path, ingress_only=True))) as client:
        ws = await _ws(client, ADMIN, tablet=area)
        await ws.send_json({"type": "pause", "minutes": -1})
        await _next(ws, "saved")
        await ws.close()
    assert hm.gate.state.paused_until == clock.now() + timedelta(minutes=60)
    assert await hm.pause(0, None, await hm.admin_user(None)) == 60
    assert await hm.pause(0, ADMIN, await hm.admin_user(ADMIN)) == 0
    await hm.stop()


async def test_expert_and_wide_house_rules_are_for_administrators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resident goes no higher than Involved unless an administrator allows more; a house
    rule for the whole house is refused with a reason, one for a single device is not."""
    monkeypatch.setattr(web, "INGRESS_PROXY_IP", "127.0.0.1")
    hm, _clock, _ha = await _house(tmp_path)
    async with TestClient(TestServer(web.create_app(hm, tmp_path, ingress_only=True))) as client:
        ws = await _ws(client, RESIDENT)
        await ws.send_json({"type": "set_level", "level": 4})
        await ws.send_json({"type": "set_level", "level": 3})
        snap = (await _next(ws, "snapshot"))["snapshot"]
        assert max(o["level"] for o in snap["levels"]) == 3
        await ws.send_json(
            {"type": "rule_build", "scopeType": "house", "scope": "*", "category": "on"}
        )
        assert "beheerder" in await _told(ws)
        await ws.close()
        row = await hm.db.fetchone("SELECT level FROM people WHERE user_id = ?", (RESIDENT,))
        assert row is not None and row["level"] == 3
        assert await hm.set_house_setting("resident_top_level", 4)
        ws = await _ws(client, RESIDENT)
        await ws.send_json({"type": "set_level", "level": 4})
        snap = (await _next(ws, "snapshot"))["snapshot"]
        await ws.close()
    assert snap["level"] == 4
    assert not hm.rules.rules
    await hm.stop()


# -- gat 4: an emergency message put away for everyone -------------------------------------


async def test_an_emergency_stays_when_someone_says_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke: someone at a screen sends Later for the emergency card. It stays, for everyone,
    and they are told it stays while the emergency lasts. Later on an ordinary card is for that
    person only."""
    monkeypatch.setattr(web, "INGRESS_PROXY_IP", "127.0.0.1")
    hm, _clock, _ha = await _house(tmp_path)
    assert hm.router is not None and hm.world is not None
    snapshot = hm.world.look(hm.clock.now())
    emergency = await hm._raise(Kind.EMERGENCY, "emergency:smoke:x", "notify.paused", {}, snapshot)
    info = await hm._raise(Kind.IMPORTANT, "unusual:x", "notify.paused", {}, snapshot)
    async with TestClient(TestServer(web.create_app(hm, tmp_path, ingress_only=True))) as client:
        ws = await _ws(client, RESIDENT)
        await ws.send_json({"type": "notification", "id": emergency, "action": "later_morning"})
        assert "noodmelding" in (await _next(ws, "saved"))["text"].lower()
        await ws.send_json({"type": "notification", "id": info, "action": "later_morning"})
        await ws.close()
    open_ids = {int(r["id"]) for r in await hm.router.open_list(hm.clock.now())}
    assert {emergency, info} <= open_ids  # nobody's Later hid them for the house
    await hm.stop()


# -- gaten 5, 6 and 10: the watchdog, a spoken yes, and the small ones ----------------------


def test_the_watchdog_in_home_assistant_never_gives_back_a_door_lock_or_tap() -> None:
    """The watchdog in HA acts without the gate. Whatever list it is handed, it never carries
    out anything that could open a garage door, gate or door, a lock, an alarm or a tap."""
    import importlib.util

    path = ROOT / "integration" / "custom_components" / "housemind" / "watchdog.py"
    spec = importlib.util.spec_from_file_location("hm_watchdog_holes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handed = [
        {"entity_id": e, "action": "switch.turn_on", "data": {}}
        for e in ("switch.garage_door_relay", "switch.voordeur_slot", "switch.hoofdkraan")
    ] + [{"entity_id": "switch.boiler", "action": "switch.turn_on", "data": {}}]
    assert [i["entity_id"] for i in module.valid(handed)] == ["switch.boiler"]


async def test_a_yes_on_a_shared_speaker_approves_nothing(tmp_path: Path) -> None:
    """Assist without a person – a shared speaker, an automation – says yes to a suggestion:
    nothing is done, and she says where yes works. Her no still counts."""
    from housemind.knowledge.catalog import Step

    hm, _clock, _ha = await _house(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "toilet")] = Step.PROPOSE
    request = ActionRequest(
        Layer.REFLEX,
        next(e for e in hm.model.entities if e.startswith("light.") and "toilet" in e),
        "light.turn_on",
        {},
        reason="skill:light_on_entry",
        skill="light_on_entry",
        area_id="toilet",
        created_at=hm.clock.now(),
        facts={"context": "workday|12:00"},
    )
    found = await hm.proposals.add(hm.gate.evaluate(request))
    assert found is not None
    said = await hm.assist("ja", "nl", "speaker", None)
    assert "telefoon" in said["text"]
    assert [p.id for p in await hm.proposals.open(hm.clock.now())] == [found]
    await hm.stop()


async def test_the_screen_talks_only_to_its_own_house(tmp_path: Path) -> None:
    """The live connection may go only to this App's own host, the camera and location are
    never asked for, and outside Home Assistant a page that points its own name at this
    computer is turned away."""
    hm, _clock, _ha = await _house(tmp_path)
    async with TestClient(TestServer(web.create_app(hm, tmp_path, ingress_only=False))) as client:
        response = await client.get("/api/health")
        rules = response.headers["Content-Security-Policy"]
        assert "connect-src 'self' wss://127.0.0.1" in rules and " ws: " not in rules
        assert "camera=()" in response.headers["Permissions-Policy"]
        status = (await client.get("/api/health", headers={"Host": "evil.example"})).status
    assert status == 403
    await hm.stop()


def test_a_name_with_a_backslash_does_not_break_an_answer() -> None:
    """A person's name with a backslash in it comes back as it is in an AI's answer."""
    from housemind.ai.privacy import Labels

    labels = Labels()
    labels._add("light.x", "light_1", r"Lamp C:\xbox")
    for name in (r"Lamp C:\xbox", r"Lamp \1"):
        assert labels.restore("Ik zet light_1 aan.", {"light.x": name}) == f"Ik zet {name} aan."
