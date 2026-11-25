"""Starting again (E-271 – E-275), asked for by the administrator of the real house: learning
again, the introduction again and the factory settings, and "Nog niet actief" until the
introduction is done. From the user's side, over the same websocket the screens use. Each test
fails without it; what it checks is its docstring."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from housemind.brain import history, setting_up, start_over
from housemind.brain.automations import Status
from housemind.brain.rules import parse_rule
from housemind.core.model import Entity, State
from housemind.knowledge.catalog import Step
from housemind.memory.database import Database
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import Outcome
from housemind.server.web import create_app
from tests.helpers import next_message as _next
from tests.test_automations import HALL, ROOT, start
from tests.test_screens import kitchen_light_on

ADMIN = {"X-Remote-User-Id": "demo-user-1"}
SMOKE = "binary_sensor.hall_smoke"


async def _snapshot(ws: Any, until: Any = None) -> dict[str, Any]:
    """The next snapshot – or the first that shows ``until``."""
    while True:
        snap: dict[str, Any] = (await _next(ws, "snapshot"))["snapshot"]
        if until is None or until(snap):
            return snap


async def _told(ws: Any, text: str) -> None:
    """The short message that says ``text`` (others, such as "Bewaard.", may come first)."""
    while (await _next(ws, "saved"))["text"] != text:
        pass


async def _hello(client: TestClient, headers: dict[str, str] = ADMIN) -> Any:
    ws = await client.ws_connect("/api/ws", headers=headers)
    await ws.send_json({"type": "hello", "haLanguage": "nl", "browserLanguage": "nl"})
    return ws


async def _taken_over(hm: HouseMind, clock: Any) -> None:
    """HouseMind took over the hall's automation (E-42), as in a real house."""
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS  # its own room (20.12)
    await clock.advance(16 * 60)
    await hm.tick()
    assert (await hm.answer_automation(HALL, True, "demo-user-1"))["ok"]
    assert hm.automations.status[HALL] is Status.TURNED_OFF


async def _rule(hm: HouseMind) -> None:
    rule = parse_rule("nooit de gordijnen in de woonkamer open", "nl", hm.languages, hm.model)
    assert rule is not None
    await hm.save_rule(rule, "demo-user-1")


def _smoke_sensor(hm: HouseMind, ha: Any, clock: Any) -> None:
    ha.model.entities[SMOKE] = Entity(
        SMOKE, f"r-{SMOKE}", "Rookmelder", area_id="hall", device_class="smoke"
    )
    ha.states[SMOKE] = State(SMOKE, "off", {}, clock.now())


@pytest.mark.fresh_install
async def test_a_new_installation_waits_until_it_is_set_up(tmp_path: Path) -> None:
    """A new installation is "Nog niet actief": Vesta grey with those words, nothing switched
    or told of its own accord – but an emergency still gets its full reaction. The introduction
    alone does not end it (E-277): once the administrator set HouseMind up, it is on, with
    everything on Observe."""
    hm, clock, ha = await start(tmp_path, consent=False)
    assert hm.gate is not None
    assert not hm.activated and hm.orb_state() == "inactive"
    decision = await hm.consider(kitchen_light_on())
    assert decision is not None and decision.outcome is Outcome.DENIED
    assert decision.reason == "inactive"
    assert hm.languages.text("nl", "gate.reason.inactive").startswith("Ik ben nog niet actief")

    # An emergency is never held back (E-80).
    before = len(ha.calls)
    _smoke_sensor(hm, ha, clock)
    await hm._refresh_model()
    await ha.simulate(SMOKE, "on")
    assert hm.status.emergency is not None
    assert any(action == "light.turn_on" for action, _, _ in ha.calls[before:])
    await ha.simulate(SMOKE, "off")
    assert hm.status.emergency is None

    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        # Found in a try-out: an administrator who is no Home Assistant user (the local one)
        # also ends it.
        ws = await _hello(client, headers={})
        snap = await _snapshot(ws)
        assert snap["user"]["isAdmin"] and snap["orb"] == "inactive"
        assert "Nog niet actief" in snap["line"]
        assert snap["setup"]["state"] == "pending"
        await ws.send_json({"type": "setup_start"})
        await _snapshot(ws, lambda s: s["setup"]["state"] == "intro")
        await ws.send_json({"type": "house_setting", "key": "consent_learning", "value": False})
        await ws.send_json({"type": "set_pref", "key": "onboarded", "value": True})
        await _snapshot(ws, lambda s: not s["onboarding"]["person"])
        assert not hm.activated  # the introduction alone is not setting up
        for step in ("link", "vesta", "ai", "finish"):
            await ws.send_json({"type": "setup_step", "step": step})
        await ws.send_json({"type": "setup_done"})
        snap = await _snapshot(ws, lambda s: s["orb"] != "inactive")
        assert snap["orb"] != "inactive" and "Nog niet actief" not in snap["line"]
    assert hm.activated and not hm.gate.state.inactive
    assert await hm.db.get_setting("activated") is True
    told = [n for n in await hm.notifications() if n["topic"] == "activated"]
    assert told and hm.languages.text("nl", told[0]["text_key"]).startswith("Ik kijk nu mee")
    await hm.stop()


@pytest.mark.fresh_install
async def test_after_a_yes_to_learning_done_waits_for_the_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Said yes to learning: HouseMind reads the history while the administrator sets up, and
    "Klaar" waits until that is done (E-282) – it never goes on halfway the reading."""
    reading = asyncio.Event()
    real = history.run

    async def slow(hm: HouseMind) -> Any:
        hm.history_status.state = "reading"
        await reading.wait()
        return await real(hm)

    monkeypatch.setattr(history, "run", slow)
    hm, _, _ = await start(tmp_path, consent=False)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        await _snapshot(ws)
        await ws.send_json({"type": "setup_start"})
        await ws.send_json({"type": "house_setting", "key": "consent_learning", "value": True})
        await ws.send_json({"type": "set_pref", "key": "onboarded", "value": True})
        await _snapshot(ws, lambda s: not s["onboarding"]["person"])
        await ws.send_json({"type": "setup_step", "step": "finish"})
        await ws.send_json({"type": "setup_done"})
        await _told(ws, hm.languages.text("nl", "ui.setup.wait"))
        assert not hm.activated and await setting_up.state(hm) != "done"
        reading.set()
        await _snapshot(ws, lambda s: s["history"]["state"] == "done")
        await ws.send_json({"type": "setup_done"})
        await _snapshot(ws, lambda s: s["orb"] != "inactive")
    assert hm.activated and await setting_up.state(hm) == "done"
    await hm.stop()


@pytest.mark.fresh_install
async def test_a_house_that_already_runs_stays_on_after_the_update(tmp_path: Path) -> None:
    """The update brings "Nog niet actief" only to new installations: a house that already
    runs (the real house) stays on – also one that still has the schema of 0.2.0."""
    db = Database(tmp_path / "housemind.db")
    await db.open()
    await db.set_setting("installed_at", "2026-10-01T10:00:00+00:00")
    await db.set_setting("activated", True)
    await db.execute("PRAGMA user_version=22")
    await db.close()
    hm, _, _ = await start(tmp_path)
    assert hm.activated and hm.orb_state() != "inactive"
    await hm.stop()


async def test_learning_again_forgets_what_was_learned_and_keeps_the_rest(
    tmp_path: Path,
) -> None:
    """ "Opnieuw leren": the automation HouseMind took over goes back on first; what was learned
    goes and everything is on Observe; settings, house rules and the logbook stay; the history
    is read again."""
    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None
    await _taken_over(hm, clock)
    await ha.simulate("light.kitchen", "on")
    assert hm.learning.counts
    await hm.set_house_setting("pet_areas", ["hall"])
    logbook = (await hm.db.fetchone("SELECT COUNT(*) AS n FROM decisions"))["n"]
    assert logbook

    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        await _snapshot(ws)
        await ws.send_json({"type": "relearn", "yes": True})
        saved = await _next(ws, "saved")
        assert saved["text"] == hm.languages.text("nl", "ui.restart.learn.done")

    assert ("automation.turn_on", HALL, {}) in ha.calls and ha.states[HALL].state == "on"
    assert hm.automations.status[HALL] is Status.SEEN
    assert not hm.learning.counts and not hm.learning.steps
    assert hm.learning.step("light_on_entry", "kitchen") is Step.OBSERVE
    assert not await hm.db.fetchall("SELECT * FROM skill_counts")
    assert not await hm.db.fetchall("SELECT * FROM skill_steps")
    assert hm.learning_allowed and hm.world is not None and hm.pet_areas == {"hall"}
    assert (await hm.db.fetchone("SELECT COUNT(*) AS n FROM decisions"))["n"] >= logbook
    assert hm._history_task is not None  # the history is read again
    await hm._history_task
    assert hm.history_status.state == "done"
    await hm.stop()


async def test_the_factory_settings_need_the_word_and_then_start_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Terug naar fabrieksinstellingen": nothing happens without the typed word. With it,
    HouseMind gives everything back, takes its link out of Home Assistant and starts again
    with an empty memory – no house rules, no people, no AI key – "Nog niet actief", with the
    introduction from the start."""
    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None
    await _taken_over(hm, clock)
    ha.integrations["housemind"] = "configured"
    await hm.ai.configure(hm.ai.config, api_key="sk-test-key-for-this-house")
    assert (tmp_path / "secrets").exists()
    await _rule(hm)
    assert hm.rules.rules

    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        await _snapshot(ws)
        await ws.send_json({"type": "factory_reset", "word": "weg ermee"})
        saved = await _next(ws, "saved")
        assert saved["text"] == hm.languages.text("nl", "ui.restart.factory.wrong", word="WISSEN")
        assert hm.rules.rules and ha.states[HALL].state == "off" and not ha.unlinked
        assert not hm.restart_requested.is_set()

        await ws.send_json({"type": "factory_reset", "word": " wissen "})
        saved = await _next(ws, "saved")
        assert saved["text"] == hm.languages.text("nl", "ui.restart.factory.busy")
    assert ha.states[HALL].state == "on" and ha.unlinked and "housemind" not in ha.integrations
    assert hm.restart_requested.is_set()

    monkeypatch.setattr(HouseMind, "FRESH_INACTIVE", True)  # as in a real house
    fresh = await start_over.start_over(hm, ha, autorun=False)
    assert not fresh.activated and fresh.orb_state() == "inactive"
    assert await setting_up.state(fresh) == "pending"  # the start screen again (E-277)
    assert not fresh.rules.rules and not (tmp_path / "secrets").exists()
    assert not await fresh.db.fetchall("SELECT * FROM people")
    assert not await fresh.db.fetchall("SELECT * FROM skill_counts")
    assert await fresh.db.get_setting("consent_learning") is None
    assert fresh.automations.status.get(HALL) is not Status.TURNED_OFF
    app = create_app(fresh, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        snap = await _snapshot(ws)
        assert snap["onboarding"]["person"] and snap["onboarding"]["house"]
    # The old HouseMind no longer listens to the house.
    await ha.simulate("light.kitchen", "off")
    await fresh.stop()


async def test_the_introduction_again_keeps_the_answers(tmp_path: Path) -> None:
    """ "Kennismaking opnieuw": the introduction with the house questions once more, with the
    answers of now; nothing is erased, and afterwards it is done again."""
    hm, _, _ = await start(tmp_path)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        await ws.send_json({"type": "set_pref", "key": "onboarded", "value": True})
        await _snapshot(ws, lambda s: not s["onboarding"]["person"])
        await ws.send_json({"type": "intro_again"})
        snap = await _snapshot(ws, lambda s: s["onboarding"]["person"])
        assert snap["onboarding"] == {"person": True, "house": True, "again": True}
        assert hm.learning_allowed and hm.activated  # nothing changed
        await ws.send_json({"type": "set_pref", "key": "onboarded", "value": True})
        snap = await _snapshot(ws, lambda s: not s["onboarding"]["person"])
        assert snap["onboarding"] == {"person": False, "house": False, "again": False}
    await hm.stop()


async def test_the_copy_is_readable_and_keeps_no_secrets(tmp_path: Path) -> None:
    """ "Eerst een kopie bewaren": the administrator downloads the settings file (E-296) – the
    house settings and house rules, never a key, a token, a conversation or a person. Others
    cannot."""
    hm, _, _ = await start(tmp_path)
    await _rule(hm)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/export", headers=ADMIN)
        assert response.status == 200
        assert "housemind-instellingen-" in response.headers["Content-Disposition"]
        copy = json.loads(await response.text())
        assert "consent_learning" not in copy["settings"]  # a consent never travels (QA 2, M-2)
        assert copy["house_rules"]
        assert not [k for k in copy["settings"] if "secret" in k or "token" in k]
        assert "signing_secret" not in json.dumps(copy)
        other = await client.get("/api/export", headers={"X-Remote-User-Id": "demo-user-2"})
        assert other.status == 403
    await hm.stop()


async def test_the_screens_come_back_after_a_fresh_start(tmp_path: Path) -> None:
    """Found in a try-out: the fresh start after the factory settings waited a minute for an
    open screen. At a stop the screens are told to go; they come back by themselves."""
    hm, _, _ = await start(tmp_path)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        await _snapshot(ws)
        await app.shutdown()
        message = await ws.receive(timeout=5)
        while message.type is WSMsgType.TEXT:
            message = await ws.receive(timeout=5)
        assert message.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED)
    await hm.stop()


def test_the_paths_stay_with_the_app() -> None:
    """The factory settings never touch the knowledge updates or the own files."""
    paths = Paths([ROOT / "knowledge"], Path("/nonexistent-housemind"))
    assert start_over.DATABASE == ("housemind.db", "housemind.db-wal", "housemind.db-shm")
    start_over.wipe(paths)  # nothing there: no error
