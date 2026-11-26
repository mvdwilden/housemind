"""HouseMind against a real Home Assistant (E-142, H12.1): run by ``tools/real_ha.py``.

Without a real Home Assistant (the environment variables below) these tests are skipped. They
check what the simulated house cannot: the real websocket API, the real registries, what HA
really calls things, contexts of real changes, and a real restart of Home Assistant.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.request
from pathlib import Path

import pytest
from aiohttp import web

from housemind.brain import start_over
from housemind.core.clock import SystemClock
from housemind.core.model import Source, StateChange
from housemind.ha.websocket import WebSocketHomeAssistant
from housemind.knowledge.matcher import recognise
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("HOUSEMIND_REAL_HA", "")
TOKEN = os.environ.get("HOUSEMIND_REAL_HA_TOKEN", "")  # HouseMind's own user
PERSON_TOKEN = os.environ.get("HOUSEMIND_REAL_HA_PERSON_TOKEN", "")  # someone in the house
VERSION = os.environ.get("HOUSEMIND_REAL_HA_VERSION", "")
CONTAINER = os.environ.get("HOUSEMIND_REAL_HA_CONTAINER", "")
CONFIG = os.environ.get("HOUSEMIND_REAL_HA_CONFIG", "")  # the test HA's /config, on this computer
BRIDGE_PORT = 18099  # where the test's HouseMind answers the integration
# How Home Assistant's container reaches this computer (tools/real_ha.py): on Linux it shares
# the network and calls 127.0.0.1; Docker Desktop on a Mac calls in as host.docker.internal.
BRIDGE_HOST = os.environ.get("HOUSEMIND_REAL_HA_BRIDGE_HOST", "host.docker.internal")
CORE_DOMAINS = ("light", "cover", "climate", "valve", "lock", "alarm_control_panel")

pytestmark = [
    pytest.mark.realha,
    pytest.mark.skipif(not BASE, reason="needs a real Home Assistant (tools/real_ha.py)"),
]


def _rest(path: str, data: dict[str, object]) -> None:
    """A person doing something in Home Assistant (not HouseMind)."""
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(data).encode(),
        headers={"Authorization": f"Bearer {PERSON_TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30):
        pass


async def _until(check: object, limit_s: float, what: str) -> None:
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        if check():  # type: ignore[operator]
            return
        await asyncio.sleep(0.2)
    raise AssertionError(f"waited {limit_s} s for {what}")


async def test_house_mind_on_a_real_home_assistant(tmp_path: Path) -> None:
    clock = SystemClock()
    ha = WebSocketHomeAssistant(clock, TOKEN, url=f"{BASE.replace('http', 'ws')}/api/websocket")
    ha.retry_delays = (1.0, 2.0, 5.0)
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path))
    started = time.monotonic()
    await hm.start(autorun=False)
    print(f"\nstart on Home Assistant {VERSION}: {time.monotonic() - started:.2f} s")
    assert hm.config is not None and hm.config.version == VERSION
    assert hm.config.time_zone == "Europe/Amsterdam" and hm.config.country == "NL"

    # What Home Assistant's own demo devices are, HouseMind recognises without help.
    core = [
        e
        for e in hm.model.entities.values()
        if e.domain in CORE_DOMAINS and not e.hidden and e.entity_id in hm.world.states  # type: ignore[union-attr]
    ]
    assert len(core) >= 10, [e.entity_id for e in core]
    unknown = [e.entity_id for e in core if recognise(hm.catalog, e, None).kind is None]
    assert not unknown, unknown
    others = sorted(
        {
            e.domain
            for e in hm.model.entities.values()
            if e.entity_id in hm.world.states  # type: ignore[union-attr]
            and recognise(hm.catalog, e, None).kind is None
        }
    )
    print(f"domains without any catalogue entry (only seen): {others}")

    # The self-check finds everything it uses.
    assert hm.self_check["broken_actions"] == [], hm.self_check

    # Existing automations are read (E-41), never changed.
    assert hm.automations.status, "the test automation was not seen"

    # HouseMind's own command comes back as HouseMind; a person's as a person.
    changes: list[StateChange] = []

    async def record(change: StateChange) -> None:
        changes.append(change)

    ha.on_state_change(record)

    def seen(entity_id: str, state: str, source: Source) -> bool:
        return any(
            c.entity_id == entity_id
            and c.new is not None
            and c.new.state == state
            and c.source is source
            for c in changes
        )

    result = await ha.call_action("light.turn_on", "light.bed_light", {"brightness_pct": 40})
    assert result.ok, result.error
    await _until(lambda: seen("light.bed_light", "on", Source.HOUSEMIND), 10, "own change")
    _rest("/api/services/light/turn_off", {"entity_id": "light.bed_light"})
    await _until(lambda: seen("light.bed_light", "off", Source.PERSON), 10, "a person's change")

    # A leak: the emergency reaction runs on real events.
    _rest(
        "/api/states/binary_sensor.basement_floor_wet",
        {"state": "on", "attributes": {"device_class": "moisture"}},
    )
    await _until(lambda: hm.status.orb == "emergency", 10, "the emergency")
    _rest(
        "/api/states/binary_sensor.basement_floor_wet",
        {"state": "off", "attributes": {"device_class": "moisture"}},
    )
    await _until(lambda: hm.status.orb != "emergency", 10, "the emergency to end")
    await hm.tick()

    # A real restart of Home Assistant: HouseMind waits, comes back by itself and checks.
    checked_before = hm.self_check["at"]
    restart = await asyncio.create_subprocess_exec("docker", "restart", CONTAINER)
    assert await restart.wait() == 0
    await _until(lambda: hm.status.ha_lost is not None, 60, "the connection to be lost")
    assert hm.status.orb == "attention"
    lost = time.monotonic()
    await _until(lambda: hm.status.ha_lost is None, 300, "the connection to come back")
    print(f"back after a restart of Home Assistant: {time.monotonic() - lost:.1f} s")
    # Back first, then the check: on a quick machine the test looks in between.
    await _until(lambda: hm.self_check["at"] != checked_before, 60, "the check after coming back")
    assert hm.self_check["broken_actions"] == [], hm.self_check
    # No more "no connection"; she may still say "let op" for a message that waits (20.7).
    assert hm.vesta_state()[1] != "ha_lost", hm.vesta_state()
    changes.clear()
    assert (await ha.call_action("light.turn_on", "light.bed_light")).ok
    await _until(lambda: seen("light.bed_light", "on", Source.HOUSEMIND), 10, "listening again")
    await hm.tick()

    # Last, so all of the above was checked on this version too: is it on the tested list
    # (E-155)? A beta is the coming month: until it is listed, HouseMind is careful there.
    status = hm.compatibility.status(VERSION)
    if re.search(r"(b|dev)\d*$", VERSION):
        assert status in ("tested", "newer"), (VERSION, status)
    else:
        month = ".".join(VERSION.split(".")[:2])
        assert status == "tested", f"{VERSION} passed: put {month} on knowledge/tested_ha.yaml"
    await hm.stop()


async def test_house_mind_finishes_its_own_link_in_a_real_home_assistant(tmp_path: Path) -> None:
    """ "Koppelen" on a real HA (20.23): HouseMind places the integration, restarts HA because
    a fresh HA does not know it yet, then finishes the setup through HA's own flow – the
    clicks an administrator made in Settings → Devices and services – and HA calls back with
    the right key. Afterwards the link is taken away again."""
    if not CONFIG:
        pytest.skip("needs the test HA's config folder")
    if BRIDGE_HOST not in ("127.0.0.1", "localhost"):
        # Outside Home Assistant, HouseMind only answers what is addressed to this computer
        # itself (QA 3, gat 10); in the App, HA is known by its own network instead.
        pytest.skip("HA must call this computer as 127.0.0.1: runs on Linux (GitHub)")
    clock = SystemClock()
    ha = WebSocketHomeAssistant(clock, TOKEN, url=f"{BASE.replace('http', 'ws')}/api/websocket")
    ha.retry_delays = (1.0, 2.0, 5.0)
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path / "data", Path(CONFIG)))
    await hm.start(autorun=False)
    runner = web.AppRunner(create_app(hm, tmp_path, ingress_only=False))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", BRIDGE_PORT).start()
    secret = await hm.integration_secret()

    async def connection() -> dict[str, object]:
        # In the App this is the App's own name in HA's network; here the computer itself.
        return {"host": BRIDGE_HOST, "port": BRIDGE_PORT, "secret": secret}

    hm._connection = connection  # type: ignore[method-assign]
    try:
        result = await hm.link_ha()
        print(f"\nKoppelen: {result}")
        assert result["step"] == "restarting", result  # a fresh HA does not know it yet
        await _until(lambda: hm.status.ha_lost is not None, 60, "HA to restart")
        await _until(lambda: hm.status.ha_lost is None, 300, "HA to come back")
        hm._finish_tried = None  # the tick does this a minute later; here at once
        await hm._finish_link(clock.now())
        assert hm._finish_result == "done", hm._finish_result
        assert await ha.integration_setup("housemind") == "configured"
        await _until(lambda: hm.bridge_state_word() == "ok", 90, "HA to call HouseMind")
        status = await hm.integration_status()
        assert status["contact"] == "ok" and not status["linking"]
        assert await start_over.remove_link(hm)  # and away again (E-112, E-274)
        assert await ha.integration_setup("housemind") != "configured"
    finally:
        await runner.cleanup()
        await hm.stop()
