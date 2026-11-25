"""QA 2, group 1, part 1 – the last safety gaps (M-2, M-3, L1 – L4). Each test fails without
its fix; what it checks is its docstring."""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain import settings_file
from housemind.brain.notify import Kind
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.energy.model import EnergySettings
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer, Outcome
from housemind.server.web import create_app
from tests.test_start_over import ADMIN, _hello, _next, _snapshot

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 10, 19, 20, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
RESIDENT = {"X-Remote-User-Id": "demo-user-2"}


async def _house(
    tmp_path: Path, extra: list[tuple[Entity, str, dict[str, Any]]] = (), config: Path | None = None
) -> tuple[HouseMind, FakeHomeAssistant]:
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    for entity, state, attributes in extra:
        house.ha.model.entities[entity.entity_id] = entity
        house.ha.states[entity.entity_id] = State(entity.entity_id, state, attributes, clock.now())
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path / "data", config))
    await hm.start(autorun=False)
    return hm, house.ha


def _file(**parts: Any) -> dict[str, Any]:
    return {"format": settings_file.FORMAT, "version": settings_file.VERSION, **parts}


async def _open(hm: HouseMind, entity_id: str) -> Outcome:
    assert hm.gate is not None
    decision = await hm.gate.submit(
        ActionRequest(
            Layer.PERSON_ANSWER, entity_id, "cover.open_cover", {}, created_at=hm.clock.now()
        )
    )
    return decision.outcome


# -- M-2: a settings file goes through the same checks ----------------------------------------


async def test_a_settings_file_never_loosens_safety(tmp_path: Path) -> None:
    """A file read back in (E-296) no longer bypasses the checks: the protected list only
    grows, the garage keeps its role and no room becomes "outside", a voice that is no HA
    voice is refused, and no consent travels with the file – a yes is given in this house."""
    hm, _ha = await _house(tmp_path)
    await hm.set_house_setting("consent_learning", True)
    await hm.db.set_setting("protected", ["light.garden"])
    await settings_file.apply(
        hm,
        _file(
            settings={
                "protected": [],
                "vesta_voice": {"engine": "evil", "voice": "x"},
                "consent_learning": False,
                "assist_forward": "agent:conversation.anything",
            },
            area_roles={"garage": "other", "kitchen": "outside", "living": "office"},
        ),
    )
    assert await hm.db.get_setting("protected") == ["light.garden"]
    assert hm.area_roles["garage"] == "garage" and hm.area_roles["kitchen"] != "outside"
    assert hm.area_roles["living"] == "office"
    assert await hm.db.get_setting("vesta_voice") is None
    assert await hm.db.get_setting("consent_learning") is True
    assert await hm.db.get_setting("assist_forward") in (None, "off")
    exported = await settings_file.make(hm)
    assert "consent_learning" not in exported["settings"]
    await hm.stop()


def test_energy_from_a_file_stays_within_the_screens_bounds() -> None:
    """A file cannot choose an unknown goal or a peak limit outside the bounds of the screen,
    else the house keeps its own; what an earlier version had for the battery and the car is
    skipped (E-363)."""
    base = EnergySettings()
    read = EnergySettings.within(
        {"goal": "fire", "peak_limit_kw": 500, "main_fuse_a": 200, "charge_mode": "fast"}, base
    )
    assert read == base
    assert EnergySettings.within({"goal": "co2", "peak_limit_kw": 5}, base).peak_limit_kw == 5
    assert EnergySettings.from_dict({"battery_reserve_pct": 30, "goal": "mix"}).goal == "mix"


# -- M-3: garage-door recognition -------------------------------------------------------------


async def test_a_garage_door_in_a_group_or_named_garage_stays_shut(tmp_path: Path) -> None:
    """A roller door HA calls a shutter, named "Garage" but in the hall, stays shut; so does a
    group "Alle rolluiken" with the garage door in it, whatever class the group shows."""
    hm, _ha = await _house(
        tmp_path,
        [
            (
                Entity("cover.garage", "r-g", "Garage", area_id="hall", device_class="shutter"),
                "closed",
                {},
            ),
            (
                Entity("cover.all_shutters", "r-all", "Alle rolluiken", device_class="shutter"),
                "closed",
                {"entity_id": ["cover.bedroom_curtain", "cover.garage_door"]},
            ),
        ],
    )
    assert await _open(hm, "cover.garage") is Outcome.DENIED
    assert await _open(hm, "cover.all_shutters") is Outcome.DENIED
    assert await _open(hm, "cover.bedroom_curtain") is not Outcome.DENIED  # a real shutter may
    await hm.stop()


async def test_a_relay_shown_as_a_lamp_stays_as_it_is_at_smoke(tmp_path: Path) -> None:
    """At smoke all lamps go on to see the way out – but not a relay that HA only shows as a
    lamp ("switch as x"): it may power a heater or a door opener."""
    hm, ha = await _house(
        tmp_path,
        [
            (
                Entity(
                    "light.relay", "r-relay", "Schakelaar", area_id="hall", platform="switch_as_x"
                ),
                "off",
                {},
            )
        ],
    )
    await ha.simulate("binary_sensor.hall_smoke", "on")
    assert ha.states["light.hall"].state == "on"
    assert ha.states["light.relay"].state == "off"
    await hm.stop()


# -- L4: frost -------------------------------------------------------------------------------


async def test_frost_also_warms_a_thermostat_that_is_off(tmp_path: Path) -> None:
    """Frost in a room whose thermostat is off: it goes to heating at 8 °C (E-212) – frost
    does not wait until someone switches it on. One that cannot heat stays off."""
    hm, ha = await _house(
        tmp_path,
        [
            (
                Entity(
                    "binary_sensor.bedroom_frost",
                    "r-bf",
                    "Vorst",
                    area_id="bedroom",
                    device_class="cold",
                ),
                "off",
                {},
            ),
            (
                Entity("climate.bedroom_ac", "r-ac", "Airco", area_id="bedroom"),
                "off",
                {"hvac_modes": ["off", "cool"]},
            ),
        ],
    )
    await ha.simulate("climate.bedroom", "off", {"temperature": 18, "hvac_modes": ["off", "heat"]})
    await ha.simulate("binary_sensor.bedroom_frost", "on")
    assert ha.states["climate.bedroom"].state == "heat"
    assert ha.states["climate.bedroom"].attributes["temperature"] == 8
    assert ha.states["climate.bedroom_ac"].state == "off"
    await hm.stop()


# -- L1, L3: the app ---------------------------------------------------------------------------


async def test_another_website_cannot_talk_to_housemind(tmp_path: Path) -> None:
    """A page on another site, in a browser logged in to Home Assistant, gets no live
    connection and cannot send a settings file (Origin); the App itself and the HA app can."""
    hm, _ha = await _house(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        own = f"http://{client.host}:{client.port}"
        refused = await client.get("/api/ws", headers={**ADMIN, "Origin": "https://evil.example"})
        assert refused.status == 403
        posted = await client.post(
            "/api/settings-file", json={}, headers={**ADMIN, "Origin": "https://evil.example"}
        )
        assert posted.status == 403
        ws = await client.ws_connect("/api/ws", headers={**ADMIN, "Origin": own})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await _snapshot(ws)
        await ws.close()
        ws = await client.ws_connect("/api/ws", headers=ADMIN)  # no Origin: the HA app
        await ws.close()
    await hm.stop()


async def test_only_who_a_message_is_for_can_close_it(tmp_path: Path) -> None:
    """A message for the administrators cannot be closed by another resident; an
    administrator can."""
    hm, _ha = await _house(tmp_path)
    assert hm.router is not None and hm.world is not None
    await hm._raise(
        Kind.IMPORTANT,
        "test:admins",
        "notify.automation_take_over",
        {"name": "Hall"},
        hm.world.snapshot(hm.clock.now()),
        concerns="admins",
    )
    row = await hm.db.fetchone("SELECT id FROM notifications WHERE topic = 'test:admins'")
    assert row is not None
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, RESIDENT)
        await _snapshot(ws)
        await ws.send_json({"type": "notification", "id": row["id"], "action": "dismiss"})
        await ws.send_json({"type": "set_pref", "key": "timing", "value": "normal"})
        await _next(ws, "saved")  # handled in order: the dismiss came first
        await ws.close()
    still = await hm.db.fetchone("SELECT resolved_at FROM notifications WHERE id = ?", (row["id"],))
    assert still is not None and still["resolved_at"] is None
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client)
        await _snapshot(ws)
        await ws.send_json({"type": "notification", "id": row["id"], "action": "dismiss"})
        await ws.send_json({"type": "set_pref", "key": "timing", "value": "normal"})
        await _next(ws, "saved")  # handled in order: the dismiss came first
        await ws.close()
    gone = await hm.db.fetchone("SELECT resolved_at FROM notifications WHERE id = ?", (row["id"],))
    assert gone is not None and gone["resolved_at"] is not None
    await hm.stop()


# -- L2: the bridge secret --------------------------------------------------------------------


async def test_the_bridge_secret_is_only_for_housemind(tmp_path: Path) -> None:
    """The secret HA's integration uses to reach HouseMind is a file only its owner reads
    (0600), not every App that may read /config."""
    config = tmp_path / "config"
    config.mkdir()
    hm, _ha = await _house(tmp_path, config=config)
    assert (await hm.place_integration())["ok"]
    secret = config / "custom_components" / "housemind" / "connection.json"
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    await hm.stop()
