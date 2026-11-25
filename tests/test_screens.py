"""The screens per level: what each level sees, conversation, undo, settings and consent."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.rules import HouseRule
from housemind.brain.talk import find_target, intent
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.demo.life import live
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer, Outcome
from housemind.server import views, web
from housemind.server.web import create_app
from tests.helpers import next_message as _next

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 10, 19, 17, 30, tzinfo=UTC)  # Monday 19:30 local, dark


async def start(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(EVENING)
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    return hm, clock, house.ha


async def viewer(hm: HouseMind, level: int, user_id: str = "demo-user-1") -> views.Viewer:
    v = await views.load_viewer(hm, user_id, None, trusted_local=False)
    await hm.db.execute("UPDATE people SET level = ? WHERE user_id = ?", (level, user_id))
    v.level = level
    v.preferences["language"] = "nl"
    return v


def kitchen_light_on() -> ActionRequest:
    return ActionRequest(
        Layer.REFLEX,
        "light.kitchen",
        "light.turn_on",
        {"brightness_pct": 80},
        reason="entry",
        skill="light_on_entry",
        area_id="kitchen",
        facts={"context": "workday|19:30"},
    )


async def test_each_level_sees_its_own_screen(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    snaps = {level: await views.snapshot(hm, await viewer(hm, level)) for level in (1, 2, 3, 4)}
    assert "rooms" not in snaps[1] and "day" not in snaps[1]  # Carefree: Orb and one line
    assert snaps[1]["line"] and snaps[1]["orb"] == "calm"
    assert {"day", "rooms", "notifications"} <= set(snaps[2]) and "proposals" not in snaps[2]
    assert {"proposals", "floors", "rules", "readiness"} <= set(snaps[3])
    assert "readiness" in snaps[2] and "learning" not in snaps[3]  # the card (E-256)
    assert "trace" not in snaps[3]
    # No decision trail, stream of thoughts or meter (QA 3): the reaction time for Over.
    assert not {"trace", "thoughts", "meter", "steps"} & set(snaps[4]) and "reaction" in snaps[4]
    living = next(r for r in snaps[2]["rooms"] if r["id"] == "living")
    assert living["name"] == "Woonkamer" and living["devices"]
    # Garage doors and awnings are no curtains.
    garage = next(r for r in snaps[2]["rooms"] if r["id"] == "garage")
    assert "gordijnen" not in garage["summary"]
    assert snaps[2]["day"].startswith("Vandaag nog niets")
    await hm.stop()


async def test_language_follows_choice_then_ha_profile_then_ha_then_browser(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)  # Home Assistant itself is in Dutch
    v = await views.load_viewer(hm, "demo-user-2", None, trusted_local=False)
    v.browser_language = "en-GB"
    # HA's system language beats the browser: never the browser's language when HA has one.
    assert v.language(hm) == "nl"
    v.ha_language = "en"  # the HA profile language (E-25) beats both
    assert v.language(hm) == "en"
    v.ha_language = "it"  # not available: the next choice counts
    assert v.language(hm) == "nl"
    v.preferences["language"] = "en"  # an own choice beats everything
    assert v.language(hm) == "en"
    # HA in a language HouseMind does not have: then the browser.
    v.preferences["language"] = "auto"
    assert hm.config is not None
    hm.config = replace(hm.config, language="it")
    assert v.language(hm) == "en"
    await hm.stop()


async def test_same_as_ha_follows_what_ha_shows(tmp_path: Path) -> None:
    """Found in a real house: HA in English (the profile, kept in HA), a Dutch browser, and
    HouseMind in Dutch. The app now tells the language HA shows around it, also later."""
    hm, _, _ = await start(tmp_path)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-2"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl-NL", "haLanguage": "en"})
        assert (await _next(ws, "snapshot"))["snapshot"]["language"] == "en"
        # Picked another language in the HA profile while HouseMind was open.
        await ws.send_json({"type": "hello", "browserLanguage": "nl-NL", "haLanguage": "nl"})
        snap = (await _next(ws, "snapshot"))["snapshot"]
        while snap["language"] != "nl":  # one from before this hello may come first
            snap = (await _next(ws, "snapshot"))["snapshot"]
        await ws.close()
    await hm.stop()


async def test_undo_puts_it_back_and_counts_as_a_correction(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    seen: list[int] = []

    async def on_action(decision_id: int, _decision: Any) -> None:
        seen.append(decision_id)

    hm.on_action(on_action)
    decision = await hm.consider(kitchen_light_on())
    assert decision is not None and decision.outcome is Outcome.EXECUTED
    assert ha.states["light.kitchen"].state == "on" and len(seen) == 1
    result = await hm.undo(seen[0], "demo-user-1")
    assert result == {"ok": True}
    assert ha.states["light.kitchen"].state == "off"
    assert hm.gate is not None and "light.kitchen" in hm.gate.state.people_win_until
    assert (await hm.undo(seen[0], "demo-user-1"))["ok"] is False  # only once
    assert len(seen) == 1  # an undo is not announced as a new action
    await hm.stop()


async def test_conversation_without_ai(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    assert intent("Hoe gaat het?", "nl", hm.languages) == "status"
    assert intent("waarom niet de lamp in de keuken", "nl", hm.languages) == "why_not"
    assert intent("Waarom?", "nl", hm.languages) == "why"
    assert intent("maak ongedaan", "nl", hm.languages) == "undo"
    assert intent("nooit de gordijnen open voor 7:00", "nl", hm.languages) == "rule"
    assert intent("never open the curtains", "en", hm.languages) == "rule"
    assert intent("what is the meaning of life", "en", hm.languages) is None
    assert find_target("waarom niet de keuken lamp", hm.model) == ("light.kitchen", "kitchen")
    reply = await hm.talk("hoe gaat het", "nl", 2, "demo-user-1", "18,6 °C binnen")
    calm = [
        line.replace("{moment}", hm.vesta_phrases.moment("nl", part))
        for topic in ("calm", "calm_light", "sleeping")
        for line in hm.vesta_phrases.variants("nl", topic, False)
        for part in ("morning", "afternoon", "evening", "night")
    ]
    assert reply["text"] in calm  # Vesta answers, in her own words (E-220)
    reply = await hm.talk("wat is de zin van het leven", "nl", 2, "demo-user-1")
    # Honest, in her own words, when neither the house nor the Help knows (E-247).
    assert reply["text"] in hm.vesta_phrases.variants("nl", "know_unknown", False)
    reply = await hm.talk("nooit de gordijnen in de woonkamer open voor 7:00", "nl", 3, None)
    assert reply["text"].startswith("Begrijp ik het goed") and "confirm_rule" in reply
    assert not reply["text"].endswith(".?")
    reply = await hm.talk("pauze", "nl", 2, None)
    assert hm.paused_until() is not None
    await hm.talk("ga verder", "nl", 2, None)
    assert hm.paused_until() is None
    await hm.stop()


async def test_learning_needs_a_yes(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    assert hm.learning is not None
    await hm.set_house_setting("consent_learning", False)
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    await ha.simulate("light.kitchen", "on")
    assert not hm.learning.counts and not hm.learning.pending
    await hm.set_house_setting("consent_learning", True)
    await ha.simulate("light.living_ceiling", "on", {"brightness": 128})
    assert hm.learning.usual_lights("living") == {"light.living_ceiling"}
    await hm.stop()


async def test_house_settings_cap_steps_exclude_and_pets(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    assert hm.learning is not None and hm.gate is not None and hm.world is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    assert await hm.set_house_setting("admin_max_step", {"light": "propose"})
    decision = await hm.consider(kitchen_light_on())
    assert decision is not None and decision.outcome is Outcome.PROPOSED
    assert await hm.set_house_setting("excluded", ["light.kitchen", "light.nonexistent"])
    assert hm.gate.state.excluded == {"light.kitchen"}
    assert await hm.set_house_setting("pet_areas", ["hall", "nowhere"])
    assert hm.world.occupancy.pet_areas == {"hall"}
    assert not await hm.set_house_setting("unknown", True)
    await hm.pause_domain("light", 60)
    assert "light" in hm.paused_domains()
    await hm.pause_domain("light", None)
    assert hm.paused_domains() == {}
    await hm.stop()


async def test_screen_flow_over_the_websocket(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path)
    assert hm.learning is not None
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "haLanguage": "nl", "browserLanguage": "en"})
        snap = (await _next(ws, "snapshot"))["snapshot"]
        assert snap["language"] == "nl" and snap["user"]["isAdmin"] is True
        assert snap["onboarding"]["person"] is True
        await ws.send_json({"type": "set_level", "level": 3})
        await ws.send_json({"type": "set_pref", "key": "onboarded", "value": True})
        await ws.send_json({"type": "set_pref", "key": "theme", "value": "purple"})  # ignored
        # Snapshots come whenever something changes (also the history reading, E-257): wait
        # for the one after both changes.
        snap = (await _next(ws, "snapshot"))["snapshot"]
        while snap["onboarding"]["person"]:
            snap = (await _next(ws, "snapshot"))["snapshot"]
        assert snap["level"] == 3 and snap["onboarding"]["person"] is False
        assert snap["prefs"]["theme"] == "auto"

        # A house rule by text: read back, confirmed, saved.
        await ws.send_json({"type": "talk", "text": "nooit de gordijnen in de woonkamer open"})
        said = await _next(ws, "say")
        assert said["confirm"]
        await ws.send_json({"type": "confirm_rule", "token": said["confirm"], "yes": True})
        assert (await _next(ws, "say"))["text"] == "De huisregel is bewaard."
        assert len(hm.rules.rules) == 1

        # On Autonoom HouseMind just does it: no message on the screen (E-513).
        hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
        hall = replace(kitchen_light_on(), entity_id="light.hall", area_id="hall", data={})
        done = await hm.consider(hall)
        assert done is not None and done.executed and ha.states["light.hall"].state == "on"
        # A try while the rule is still on Voorstellen: a toast with Why? and Undo (E-135).
        hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
        await hm.consider(replace(kitchen_light_on(), layer=Layer.TRIAL))
        toast = await _next(ws, "toast")  # the first: none came for the hall
        assert hm.recent[toast["id"]].decision.request.entity_id == "light.kitchen"
        assert toast["text"].startswith("Ik heb") and toast["undo"] is True
        await ws.send_json({"type": "why", "id": toast["id"]})
        assert "omdat" in (await _next(ws, "say"))["text"]
        await ws.send_json({"type": "undo", "id": toast["id"]})
        assert (await _next(ws, "say"))["text"].startswith("Teruggezet")
        assert ha.states["light.kitchen"].state == "off"

        await ws.send_json({"type": "get", "what": "devices"})
        devices = (await _next(ws, "data"))["data"]
        assert any(d["id"] == "light.kitchen" and d["controls"] for d in devices)
        await ws.send_json({"type": "get", "what": "settings"})
        settings = (await _next(ws, "data"))["data"]
        assert settings["consentLearning"] is True and settings["ai"]["status"] == "off"

        # Somebody who is not an administrator cannot change house settings (E-82).
        other = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-2"})
        await other.send_json({"type": "hello"})
        snap = (await _next(other, "snapshot"))["snapshot"]
        assert snap["user"]["isAdmin"] is False
        await other.send_json({"type": "house_setting", "key": "consent_learning", "value": False})
        await other.send_json({"type": "ai_consent"})
        await other.send_json({"type": "get", "what": "settings"})
        await asyncio.sleep(0.2)
        assert await hm.db.get_setting("consent_learning") is True
        assert hm.ai.config.consent_at is None
        await other.close()
        await ws.close()
    await hm.stop()


async def test_answer_always_asks_to_save_a_house_rule(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    decision = await hm.consider(kitchen_light_on())
    assert decision is not None and decision.outcome is Outcome.PROPOSED
    proposal = (await hm.open_proposals())[0]
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/ws")
        await ws.send_json({"type": "hello", "haLanguage": "nl"})
        await _next(ws, "snapshot")
        await ws.send_json({"type": "set_level", "level": 3})  # house rules from level 3 (E-103)
        await _next(ws, "snapshot")
        await ws.send_json({"type": "answer", "proposal": proposal.id, "answer": "always"})
        said = await _next(ws, "say")
        assert said["confirm"] and "Altijd zelf doen" in said["text"]
        await ws.send_json({"type": "confirm_rule", "token": said["confirm"], "yes": True})
        await _next(ws, "say")
        assert hm._skill_step("light_on_entry", "kitchen") is Step.AUTONOMOUS
        await ws.close()
    await hm.stop()


async def test_below_level_3_and_on_a_wall_tablet_no_house_rules(tmp_path: Path) -> None:
    """QA 2 (M-1, B-171): in the app the level was not checked – on level 2 'Altijd zo' became
    a house rule, and a guest at the wall tablet could make one. Now a house rule (also from
    'Altijd zo') needs level 3, and never on a wall tablet: there Vesta says where to make it.
    An 'always' rule never lifts an experimental part an administrator did not turn on."""
    hm, _, _ = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    await hm.consider(kitchen_light_on())
    proposal = (await hm.open_proposals())[0]
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/ws")
        await ws.send_json({"type": "hello", "haLanguage": "nl"})
        await _next(ws, "snapshot")
        await ws.send_json({"type": "set_level", "level": 2})
        await _next(ws, "snapshot")
        await ws.send_json({"type": "answer", "proposal": proposal.id, "answer": "always"})
        await ws.send_json({"type": "rule_build", "scopeType": "house", "category": "off"})
        await asyncio.sleep(0.3)
        assert hm.rules.rules == []
        await ws.close()
        tablet = await client.ws_connect("/api/ws")
        await tablet.send_json({"type": "hello", "haLanguage": "nl", "tablet": "kitchen"})
        await _next(tablet, "snapshot")
        await tablet.send_json({"type": "talk", "text": "nooit de gordijnen in de woonkamer open"})
        said = await _next(tablet, "say")
        assert not said.get("confirm") and "Een huisregel maak je in de app" in said["text"]
        await tablet.close()
    hm.rules.rules.append(
        HouseRule("area", "kitchen", "always", "", None, skill="vacuum_when_away")
    )
    assert hm._skill_step("vacuum_when_away", "kitchen") is Step.OBSERVE
    await hm.stop()


async def test_demo_house_lives_its_day(tmp_path: Path) -> None:
    clock = SimulatedClock(datetime(2026, 10, 19, 3, 0, tzinfo=UTC))
    house = load_demo_house("apartment", clock, "en")
    changes: list[str] = []

    async def record(change: Any) -> None:
        changes.append(change.entity_id)

    house.ha.on_state_change(record)
    task = asyncio.create_task(live(house, clock, "Europe/Amsterdam", seed=1))
    for _ in range(24 * 6):
        await clock.advance(timedelta(minutes=10).total_seconds())
        await asyncio.sleep(0)
    task.cancel()
    assert len(changes) > 10
    assert json.dumps(changes)  # plain entity ids


async def test_give_back_before_removal_but_emergencies_keep_working(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    # An administrator's cap never blocks an emergency reaction.
    await hm.set_house_setting("admin_max_step", {"light": "observe"})
    await hm.give_back(True)  # E-112: everything back to Observe
    decision = await hm.consider(kitchen_light_on())
    assert decision is not None and decision.outcome is Outcome.OBSERVED
    await ha.simulate("binary_sensor.kitchen_smoke", "on")
    assert ha.states["light.kitchen"].state == "on"  # the emergency light still goes on
    await ha.simulate("binary_sensor.kitchen_smoke", "off")
    await hm.give_back(False)
    await hm.set_house_setting("admin_max_step", {})
    await ha.simulate("light.kitchen", "off")
    await clock.advance(10)
    hm._last_request.clear()
    hm.gate.state.people_win_until.clear()  # type: ignore[union-attr]
    decision = await hm.consider(kitchen_light_on())
    assert decision is not None and decision.outcome is Outcome.EXECUTED
    await hm.stop()


async def test_in_a_demo_house_the_viewer_is_administrator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hm, _, _ = await start(tmp_path)
    monkeypatch.setattr(web, "INGRESS_PROXY_IP", "127.0.0.1")  # the test client plays Ingress
    app = create_app(hm, tmp_path, ingress_only=True, demo=True)
    real_user = {"X-Remote-User-Id": "a-real-ha-user"}  # unknown to the demo house
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/ws", headers=real_user)
        await ws.send_json({"type": "hello"})
        snap = (await _next(ws, "snapshot"))["snapshot"]
        assert snap["user"]["isAdmin"] is True
        await ws.send_json({"type": "get", "what": "settings"})
        assert (await _next(ws, "data"))["data"]["areas"]  # house settings are open to them
        await ws.close()
    await hm.stop()


async def test_the_language_choice_shows_real_language_names(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    snap = await views.snapshot(hm, await viewer(hm, 2))
    names = snap["languageNames"]
    assert names["en"] == "English" and names["nl"] == "Nederlands"
    assert all("." not in name for name in names.values())  # never a raw key
    await hm.stop()
