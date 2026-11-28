"""The integration in Home Assistant (B-06, E-96, E-97, E-167 – E-170): the App's bridge,
Assist, house rules by voice, forwarding only when allowed, and placing the integration."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from aiohttp.test_utils import TestClient, TestServer

from housemind import __version__
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "integration" / "custom_components" / "housemind"
TZ = ZoneInfo("Europe/Amsterdam")


async def start(
    tmp_path: Path, ha_config: Path | None = None
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(datetime(2026, 10, 19, 19, 0, tzinfo=TZ).astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path / "data", ha_config))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, clock, house.ha


async def test_only_home_assistant_with_the_secret_gets_in(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    secret = await hm.integration_secret()
    app = create_app(hm, tmp_path, ingress_only=True)
    async with TestClient(TestServer(app)) as client:
        # The test client is not on the Supervisor network: blocked before any secret check.
        response = await client.get(
            "/integration/state", headers={"Authorization": f"Bearer {secret}"}
        )
        assert response.status == 403
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/integration/state")).status == 401
        bad = {"Authorization": "Bearer wrong"}
        assert (await client.get("/integration/state", headers=bad)).status == 401
        good = {"Authorization": f"Bearer {secret}"}
        state = await (await client.get("/integration/state", headers=good)).json()
        # One name, also in Home Assistant and Assist: hers (E-265).
        assert state["version"] == __version__ and state["name"] == "Vesta"
        assert state["mode"] == "home" and state["health"] == "ok" and state["paused"] is False
        # Actions go through the same gate as everything else (E-110).
        await client.post("/integration/action", json={"action": "pause"}, headers=good)
        state = await (await client.get("/integration/state", headers=good)).json()
        assert state["paused"] is True and state["health"] == "paused"
        await client.post("/integration/action", json={"action": "resume"}, headers=good)
        response = await client.post(
            "/integration/action", json={"action": "house_mode", "mode": "away"}, headers=good
        )
        assert (await response.json())["ok"] is True
        assert (await (await client.get("/integration/state", headers=good)).json())[
            "mode"
        ] == "away"
        response = await client.post(
            "/integration/action", json={"action": "unlock_everything"}, headers=good
        )
        assert response.status == 400
    await hm.stop()


async def test_assist_house_questions_rules_by_voice_and_an_honest_no(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    reply = await hm.assist("hoe gaat het", "nl", "c1", "demo-user-1")
    assert reply["text"] and reply["continue"] is False
    # A house rule by voice: read back, then yes (E-87).
    reply = await hm.assist("nooit het licht in de gang aan na 23:00", "nl", "c2", "demo-user-1")
    assert reply["continue"] is True and reply["text"].startswith("Begrijp ik het goed")
    reply = await hm.assist("ja", "nl", "c2", "demo-user-1")
    assert reply["text"] == "De huisregel is bewaard." and len(hm.rules.rules) == 1
    # Not about the house, nothing allowed: an honest answer, nothing sent (E-167).
    reply = await hm.assist("hoe hoog is de Mount Everest", "nl", "c3", "demo-user-1")
    assert reply["text"] == "Dat weet ik niet, maar ik help je graag met je huis."
    assert ha.conversed == []
    # An agent that can control the house is never used: that would be a way around the gate
    # and the hard limits (QA 2, B-169) – HouseMind says it does not know instead.
    ha.agents["conversation.controls"] = "De garagedeur gaat open."
    await ha.simulate("conversation.controls", "unknown", {"supported_features": 1})
    await hm.db.set_setting("assist_forward", "agent:conversation.controls")
    reply = await hm.assist("hoe hoog is de Mount Everest", "nl", "c4", "demo-user-1")
    assert reply["text"] == "Dat weet ik niet, maar ik help je graag met je huis."
    assert ha.conversed == [] and "conversation.controls" not in hm.forward_agents()
    # The administrator allows another HA agent: only the question goes along (E-169).
    ha.agents["conversation.other"] = "8849 meter."
    await ha.simulate("conversation.other", "unknown", {"supported_features": 0})
    assert hm.forward_agents() == ["conversation.other"]
    await hm.db.set_setting("assist_forward", "agent:conversation.other")
    reply = await hm.assist("hoe hoog is de Mount Everest", "nl", "c5", "demo-user-1")
    assert reply["text"] == "8849 meter."
    assert ha.conversed == [("conversation.other", "hoe hoog is de Mount Everest")]
    log = await hm.ai.log()
    assert log and log[0]["purpose"] == "forward"
    await hm.stop()


async def test_a_scene_by_voice(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    reply = await hm.assist("film", "nl", None, "demo-user-1")
    assert reply["text"].startswith("Goed: Film")
    assert ha.states["cover.living_curtain"].state == "closed"
    await hm.stop()


async def test_placing_and_updating_the_integration(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    hm, _, ha = await start(tmp_path, ha_config=config)
    status = await hm.integration_status()
    assert status["available"] and status["installed"] is None and not status["loaded"]
    result = await hm.place_integration()
    target = config / "custom_components" / "housemind"
    assert result["ok"] and (target / "manifest.json").exists()
    connection = json.loads((target / "connection.json").read_text())
    assert connection["secret"] == await hm.integration_secret() and connection["port"] == 8099
    assert (await hm.integration_status())["installed"] == status["current"]
    # An older integration is replaced at the next start; the connection stays.
    manifest = json.loads((target / "manifest.json").read_text())
    manifest["version"] = "0.0.1"
    (target / "manifest.json").write_text(json.dumps(manifest))
    await hm._update_integration()
    assert (await hm.integration_status())["installed"] == status["current"]
    assert json.loads((target / "connection.json").read_text()) == connection
    # Changed files with the same version are replaced too (during development).
    (target / "config_flow.py").write_text("# old\n")
    await hm._update_integration()
    assert (target / "config_flow.py").read_text() == (INTEGRATION / "config_flow.py").read_text()
    # Restarting HA only happens when asked.
    assert ha.restarts == 0
    assert await hm.restart_ha() and ha.restarts == 1
    await hm.stop()


def test_the_integration_package_is_complete() -> None:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    assert manifest["domain"] == "housemind" and manifest["config_flow"] is True
    assert manifest["version"] == __version__
    for path in INTEGRATION.glob("*.py"):
        ast.parse(path.read_text(), filename=str(path))
    source = (INTEGRATION / "__init__.py").read_text()
    services = yaml.safe_load((INTEGRATION / "services.yaml").read_text())
    for name in services:
        assert f'"{name}"' in source  # every documented action is registered
    keys: dict[str, Any] = {}
    for path in sorted((INTEGRATION / "translations").glob("*.json")):
        keys[path.stem] = json.loads(path.read_text())
    assert set(keys) == {"en", "nl", "de", "fr"}

    def flat(value: Any, prefix: str = "") -> set[str]:
        if isinstance(value, dict):
            return set().union(*(flat(v, f"{prefix}.{k}") for k, v in value.items()))
        return {prefix}

    english = flat(keys["en"])
    for lang, data in keys.items():
        assert flat(data) == english, lang
    assert set(keys["en"]["services"]) == set(services)


async def test_an_integration_from_someone_else_is_never_replaced_silently(tmp_path: Path) -> None:
    """Found in a real house: an earlier build had left its own "housemind" integration."""
    config = tmp_path / "config"
    old = config / "custom_components" / "housemind"
    old.mkdir(parents=True)
    (old / "manifest.json").write_text(json.dumps({"domain": "housemind", "version": "0.3.1"}))
    hm, _, _ = await start(tmp_path, ha_config=config)  # start: no automatic update
    assert json.loads((old / "manifest.json").read_text())["version"] == "0.3.1"
    status = await hm.integration_status()
    assert status["foreign"] is True
    result = await hm.place_integration()
    assert result["ok"] is False and result["reason"] == "foreign"
    assert json.loads((old / "manifest.json").read_text())["version"] == "0.3.1"
    result = await hm.place_integration(replace_foreign=True)  # the administrator said yes
    assert result["ok"] and result["foreign"] is False
    await hm.stop()


async def test_after_the_restart_the_app_says_to_confirm_it_in_home_assistant(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    hm, _, ha = await start(tmp_path, ha_config=config)
    await hm.place_integration()
    assert (await hm.integration_status())["setup"] is None  # HA not restarted yet
    ha.integrations["housemind"] = "discovered"
    assert (await hm.integration_status())["setup"] == "discovered"
    ha.integrations["housemind"] = "configured"
    assert (await hm.integration_status())["setup"] == "configured"
    await hm.stop()


async def test_the_demo_house_and_the_real_house_share_the_app_s_secret(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Found in the real house: with demo mode on (its own memory) the integration in Home
    Assistant got "not allowed". The secret belongs to the App, and the App tells HA where it
    is at every start, so HA's entry follows by itself."""
    import housemind.ha.integration as runtime_module

    announced: list[dict[str, Any]] = []

    async def announce(
        token: str, config: dict[str, Any], earlier: list[str] | None = None
    ) -> str | None:
        announced.append(config)
        return f"announcement-{len(announced)}"

    monkeypatch.setattr(runtime_module, "announce", announce)
    app_data, config = tmp_path / "data", tmp_path / "config"
    config.mkdir()
    old = "an-earlier-secret-of-at-least-32-characters"
    clock = SimulatedClock(datetime(2026, 10, 19, 19, 0, tzinfo=TZ).astimezone(UTC))

    async def run(data: Path, ha_config: Path | None) -> HouseMind:
        house = load_demo_house("family_home", clock, "nl")
        paths = Paths([ROOT / "knowledge"], data, ha_config, "token", app_data)
        hm = HouseMind(clock, house.ha, paths)
        await hm.start(autorun=False)
        return hm

    # An earlier App kept the secret in the real house's memory.
    hm = await run(app_data, config)
    await hm.db.set_setting("integration_secret", old)
    (app_data / "integration.json").unlink(missing_ok=True)
    await hm.stop()
    hm = await run(app_data, config)
    assert await hm.integration_secret() == old  # taken over, so HA's entry still fits
    await hm.place_integration()
    connection = json.loads((config / "custom_components/housemind/connection.json").read_text())
    assert connection["secret"] == old and announced[-1]["secret"] == old
    await hm.stop()
    # Demo mode: its own memory, but the same App – the same secret, and HA hears it again.
    announced.clear()
    demo = await run(app_data / "demo", None)
    assert await demo.integration_secret() == old
    assert announced and announced[-1]["secret"] == old
    app = create_app(demo, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        good = {"Authorization": f"Bearer {old}"}
        assert (await client.get("/integration/state", headers=good)).status == 200
    await demo.stop()


def test_a_successful_state_request_does_not_fill_the_log() -> None:
    import logging

    from housemind.server import web

    quiet = web._QuietBridge()

    def line(text: str) -> logging.LogRecord:
        return logging.LogRecord("aiohttp.access", logging.INFO, "", 0, text, None, None)

    ok = '172.30.32.1 [17/Oct/2026:18:55:16 +0000] "GET /integration/state HTTP/1.1" 200 636'
    assert not quiet.filter(line(ok))
    assert quiet.filter(line(ok.replace('" 200 ', '" 401 ')))  # a failure is still logged
    assert quiet.filter(line('172.30.32.2 [x] "GET / HTTP/1.1" 200 526'))
