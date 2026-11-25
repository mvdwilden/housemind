"""Parts on and off (E-399 – E-401, B-325), from the user's side: an Expert administrator turns
a part off in Huisinstellingen; then HouseMind does nothing with it and learns nothing from it,
says why, and shows it in Brein and the Logboek. What it learned stays – without fading – and
on again it goes on where it was. The emergency reactions always work. Each test fails without
it; what it checks is its docstring."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.learning import Result
from housemind.core.clock import SimulatedClock
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind
from housemind.safety.gate import ActionRequest, Layer, Outcome
from housemind.server import views
from housemind.server.web import create_app
from tests.test_light_on_entry import CEILING, _counts, _home
from tests.test_presence_at_start import LIVING, READING, _wait
from tests.test_start_over import ADMIN, _hello, _next, _snapshot


async def _person(hm: HouseMind, level: int) -> None:
    await hm.db.execute(
        "INSERT OR REPLACE INTO people(user_id, display_name, level, consents, created) "
        "VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", level, json.dumps({}), hm.clock.now().isoformat()),
    )
    await hm._refresh_people()


async def _parts(hm: HouseMind, tmp_path: Path, change: dict[str, str]) -> dict[str, Any]:
    """Change the list in Huisinstellingen, as the administrator; the settings come back."""
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, ADMIN)
        await _snapshot(ws)
        await ws.send_json({"type": "house_setting", "key": "parts", "value": change})
        await _next(ws, "saved")
        await ws.close()
    return await views.house_settings(hm, "nl")


def _state(settings: dict[str, Any], part: str) -> str:
    return str(next(p["state"] for p in settings["parts"] if p["part"] == part))


async def _logbook(hm: HouseMind, tmp_path: Path) -> list[str]:
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _hello(client, ADMIN)
        await _snapshot(ws)
        await ws.send_json({"type": "get", "what": "logbook"})
        while (message := await _next(ws, "data"))["what"] != "logbook":
            pass
        log = message["data"]
        await ws.close()
    return [str(i["text"]) for i in log["items"] if i["type"] == "part"]


async def _come_in(hm: HouseMind, clock: SimulatedClock, ha: Any) -> None:
    """Someone comes into the dark living room and switches the reading lamp on."""
    await ha.simulate(LIVING, "on")
    await _wait(hm, clock, 20)
    await ha.simulate(READING, "on")
    await _wait(hm, clock, 15 * 60)


async def _go_out(hm: HouseMind, clock: SimulatedClock, ha: Any) -> None:
    await ha.simulate(READING, "off")
    await ha.simulate(LIVING, "off")
    await _wait(hm, clock, 30 * 60)


async def test_lighting_off_does_and_learns_nothing_and_on_again_it_goes_on(
    tmp_path: Path,
) -> None:
    """Off: coming in and switching a lamp on teaches nothing, HouseMind does not switch a lamp,
    "Waarom niet?" says the part is off, Brein shows it grey and the Logboek says who did it.
    On again: coming in the next evening counts again."""
    hm, clock, ha = await _home(tmp_path)
    await _person(hm, 4)
    settings = await _parts(hm, tmp_path, {"lighting": "off"})
    assert _state(settings, "lighting") == "off"

    await _come_in(hm, clock, ha)
    assert _counts(hm, clock) == (0, 0, 0)  # nothing learned
    assert hm.gate is not None
    decision = await hm.gate.submit(
        ActionRequest(Layer.LEARNING, CEILING, "light.turn_on", created_at=clock.now())
    )
    assert decision.outcome is Outcome.DENIED and decision.reason == "part_off:lighting"
    assert await hm.why_device(CEILING, "nl") == "Verlichting staat uit."
    [lighting] = [p for p in await views.readiness(hm, "nl", 4, True) if p["domain"] == "lighting"]
    assert lighting["off"] and lighting["text"].startswith("Verlichting staat uit.")
    await _go_out(hm, clock, ha)

    await _parts(hm, tmp_path, {"lighting": "autonomous"})
    await clock.advance(timedelta(hours=22).total_seconds())  # the next evening
    await _wait(hm, clock, 60)
    await _come_in(hm, clock, ha)
    hits, misses, situations = _counts(hm, clock)
    assert (round(hits, 2), misses, situations) == (1.0, 0, 1)
    assert await _logbook(hm, tmp_path) == [
        "Verlichting weer aangezet door Resident 1: ik ga verder waar ik was.",
        "Verlichting uitgezet door Resident 1",
    ]
    await hm.stop()


async def test_what_was_learned_does_not_fade_while_a_part_is_off(tmp_path: Path) -> None:
    """A month off is no month of forgetting: on again, what HouseMind learned counts as much as
    on the day it went off (half-life of two weeks: without it a quarter was left)."""
    hm, clock, ha = await _home(tmp_path)
    await _person(hm, 4)
    await _come_in(hm, clock, ha)
    assert hm.learning is not None
    before = hm.learning.total("light_on_entry", "living", Result.HIT, clock.now())
    assert before > 0.9
    await _parts(hm, tmp_path, {"lighting": "off"})
    await clock.advance(timedelta(days=28).total_seconds())
    await hm.tick()
    await _parts(hm, tmp_path, {"lighting": "autonomous"})
    after = hm.learning.total("light_on_entry", "living", Result.HIT, clock.now())
    assert round(after, 2) == round(before, 2)
    await hm.stop()


async def test_the_emergency_reactions_work_with_every_part_off(tmp_path: Path) -> None:
    """Smoke with everything off: the lamps still go on (E-80) – emergencies are no part."""
    hm, _clock, ha = await _home(tmp_path)
    await _person(hm, 4)
    settings = await _parts(
        hm, tmp_path, {p["part"]: "off" for p in (await views.house_settings(hm, "nl"))["parts"]}
    )
    assert {p["state"] for p in settings["parts"]} == {"off"}
    before = len(ha.calls)
    await ha.simulate("binary_sensor.hall_smoke", "on")
    assert hm.status.emergency is not None
    assert any(action == "light.turn_on" for action, _, _ in ha.calls[before:])
    await hm.stop()


async def test_a_core_part_goes_off_only_on_expert(tmp_path: Path) -> None:
    """On Betrokken an administrator switches the experimental parts, not a core part off."""
    hm, _clock, _ha = await _home(tmp_path)
    await _person(hm, 3)
    settings = await _parts(hm, tmp_path, {"lighting": "off", "media": "propose"})
    assert _state(settings, "lighting") == "autonomous"
    assert _state(settings, "media") == "propose"
    assert "media" in hm.experimental
    await hm.stop()


async def test_the_settings_of_before_become_the_list(tmp_path: Path) -> None:
    """After the update: the limit on lighting and the ticked experimental part are in the
    list, the other experimental parts are off – and the limit still holds in the gate."""
    hm, _clock, _ha = await _home(tmp_path)
    await hm.db.set_setting("admin_max_step", {"light": "propose"})
    await hm.db.set_setting("experimental", ["media"])
    await hm.db.execute("DELETE FROM settings WHERE key IN ('parts', 'parts_off')")
    await hm._load_house_settings()
    settings = await views.house_settings(hm, "nl")
    assert _state(settings, "lighting") == "propose"
    assert _state(settings, "media") == "autonomous"
    assert _state(settings, "garden") == "off"
    assert hm.gate is not None and hm.gate.state.admin_max_step["light"] is Step.PROPOSE
    await hm.stop()
