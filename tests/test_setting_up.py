"""Setting HouseMind up (E-277 – E-282), asked for by the administrator of the real house: after a
new installation and after the factory settings there is only a start screen until the
administrator sets HouseMind up – the introduction, the link, Vesta and her own Assist pipeline
in the language HA shows the administrator (Dutch, English, German or French, else English),
the AI – and Done waits for the history. From the user's side, over the websocket the screens
use. Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from housemind.brain import setting_up, start_over
from housemind.brain.learning import HISTORY
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.ha.adapter import Voice
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from tests.test_automations import ROOT, start
from tests.test_history import _evenings as evenings
from tests.test_history import _house as history_house
from tests.test_start_over import ADMIN, _hello, _next, _snapshot, _told

OTHER = {"X-Remote-User-Id": "demo-user-2"}  # a resident who is no administrator
AGENT = "conversation.vesta"  # HouseMind's own Assist agent, once the link is there


async def _link(hm: HouseMind, ha: Any, clock: Any) -> None:
    """The link is placed and HA loaded it: HouseMind's Assist agent is there."""
    ha.model.entities[AGENT] = Entity(AGENT, "r-agent", "Vesta", platform="housemind")
    ha.states[AGENT] = State(AGENT, "unknown", {}, clock.now())
    for engine in ("tts.cloud",):
        ha.states[engine] = State(engine, "unknown", {}, clock.now())
    ha.voice = Voice("tts.cloud", "nl-NL", "Colette")
    ha.tts_locales["tts.cloud"] = ["de-DE", "en-US", "nl-NL"]
    ha.stt["stt.cloud"] = ["de-DE", "en-US", "nl-NL"]
    await hm._refresh_model()


@pytest.mark.fresh_install
async def test_only_the_start_screen_until_an_administrator_sets_it_up(tmp_path: Path) -> None:
    """Everyone sees only "Nog niet ingericht"; only an administrator can start, the house then
    speaks the language HA shows that administrator, and afterwards someone else gets their
    own short introduction."""
    hm, _, _ = await start(tmp_path, consent=False)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        other = await _hello(client, OTHER)
        assert (await _snapshot(other))["setup"]["state"] == "pending"
        await other.send_json({"type": "setup_start"})
        ws = await client.ws_connect("/api/ws", headers=ADMIN)
        await ws.send_json({"type": "hello", "haLanguage": "de", "browserLanguage": "nl"})
        snap = await _snapshot(ws)
        assert snap["setup"]["state"] == "pending"  # the resident could not start it
        await ws.send_json({"type": "setup_start"})
        snap = await _snapshot(ws, lambda s: s["setup"]["state"] == "intro")
        assert snap["setup"]["language"] == "de" and hm._language() == "de"
        assert snap["onboarding"]["person"] and snap["onboarding"]["house"]
        await ws.send_json({"type": "set_pref", "key": "onboarded", "value": True})
        for step in ("link", "vesta", "ai", "finish"):
            await ws.send_json({"type": "setup_step", "step": step})
        await ws.send_json({"type": "setup_done"})
        await _snapshot(ws, lambda s: s["setup"]["state"] == "done" and s["orb"] != "inactive")
        later = await _hello(client, OTHER)
        snap = await _snapshot(later)
        assert snap["onboarding"] == {"person": True, "house": False, "again": False}
    await hm.stop()


@pytest.mark.fresh_install
async def test_a_language_housemind_does_not_know_becomes_english(tmp_path: Path) -> None:
    """HA in Spanish for the administrator who sets up: the house speaks English."""
    hm, _, _ = await start(tmp_path, consent=False)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/ws", headers=ADMIN)
        await ws.send_json({"type": "hello", "haLanguage": "es", "browserLanguage": "nl"})
        await _snapshot(ws)
        await ws.send_json({"type": "setup_start"})
        snap = await _snapshot(ws, lambda s: s["setup"]["state"] == "intro")
        assert snap["setup"]["language"] == "en"
    await hm.stop()


@pytest.mark.fresh_install
async def test_a_step_is_kept_for_a_next_time(tmp_path: Path) -> None:
    """Stopped halfway: the next time starts at that step, also after a restart."""
    hm, clock, ha = await start(tmp_path, consent=False)
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        await ws.send_json({"type": "setup_start"})
        await ws.send_json({"type": "setup_step", "step": "vesta"})
        await _snapshot(ws, lambda s: s["setup"]["state"] == "vesta")
    await hm.stop()
    again = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert await setting_up.state(again) == "vesta" and not again.activated
    await again.stop()


@pytest.mark.fresh_install
async def test_vesta_gets_her_own_pipeline_and_nothing_else_is_touched(tmp_path: Path) -> None:
    """After a yes, Vesta gets her own Assist pipeline: HouseMind's agent, speech recognition and
    a voice in the language of the house. It follows her name; another pipeline stays as it is;
    the factory settings take only hers away."""
    hm, clock, ha = await start(tmp_path)
    ha.pipelines.append(
        {"id": "mine", "name": "Mijn assistent", "conversation_engine": "conversation.chatgpt"}
    )
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        await _snapshot(ws)
        await ws.send_json({"type": "get", "what": "integration"})
        status = (await _next(ws, "data"))["data"]
        assert status["pipeline"] == {"state": "no_agent"}  # the link comes first
        await _link(hm, ha, clock)
        await hm.set_house_language("en")
        await ws.send_json({"type": "integration", "do": "pipeline"})
        await _told(ws, hm.languages.text("nl", "ui.setup.pipeline_made"))
    made = [p for p in ha.pipelines if p["conversation_engine"] == AGENT]
    assert len(made) == 1 and made[0]["language"] == "en"
    assert (made[0]["tts_engine"], made[0]["tts_language"]) == ("tts.cloud", "en-US")
    assert (made[0]["stt_engine"], made[0]["stt_language"]) == ("stt.cloud", "en-US")
    assert await hm.vesta_voice() == Voice("tts.cloud", "en-US", "calm")  # her voice is hers
    assert await hm.set_house_setting("vesta_name", "Juno")
    assert made[0]["name"] == "Juno"
    assert ha.pipelines[0] == {
        "id": "mine",
        "name": "Mijn assistent",
        "conversation_engine": "conversation.chatgpt",
    }
    assert await ha.vesta_pipeline("mine", {"conversation_engine": "conversation.chatgpt"}) is None
    assert await start_over.remove_link(hm)
    assert [p["id"] for p in ha.pipelines] == ["mine"]
    await hm.stop()


async def test_a_pipeline_with_her_agent_is_only_taken_over_after_a_yes(tmp_path: Path) -> None:
    """There is already a pipeline with HouseMind's agent, made by the resident: HouseMind asks
    before it takes that one over, and then keeps its id."""
    hm, clock, ha = await start(tmp_path)
    await _link(hm, ha, clock)
    ha.pipelines.append({"id": "theirs", "name": "HouseMind", "conversation_engine": AGENT})
    assert await setting_up.pipeline_status(hm) == {"state": "other", "name": "HouseMind"}
    assert await setting_up.make_pipeline(hm, adopt=True)
    assert [p["id"] for p in ha.pipelines] == ["theirs"]
    assert ha.pipelines[0]["language"] == "nl" and ha.pipelines[0]["tts_language"] == "nl-NL"
    record = await hm._integration_record()
    assert record["pipeline"] == "theirs"
    assert (await setting_up.pipeline_status(hm))["state"] == "ours"
    assert json.dumps(record)  # kept in the App's own record
    await hm.stop()


# -- setting up, filled in (20.23): part H of "Duidelijk en volledig" ------------------------


async def _integration(ws: Any) -> dict[str, Any]:
    await ws.send_json({"type": "get", "what": "integration"})
    while True:
        message = await _next(ws, "data")
        if message["what"] == "integration":
            return message["data"]  # type: ignore[no-any-return]


async def test_the_link_counts_only_when_ha_really_reaches_housemind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In the real house the link step looked done after the factory settings, while Home
    Assistant asked with the old key and was refused. Now the step says "Gekoppeld" only when
    HA reaches HouseMind with the right key; an old key is told, and HA gets the new one
    through discovery – not more than once in ten minutes."""
    hm, clock, _ha = await start(tmp_path)
    told: list[dict[str, Any]] = []

    async def announce(
        token: str, config: dict[str, Any], earlier: list[str] | None = None
    ) -> str | None:
        told.append(config)
        return f"announcement-{len(told)}"

    monkeypatch.setattr("housemind.ha.integration.announce", announce)
    hm.paths.supervisor_token = "supervisor"
    secret = await hm.integration_secret()
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        assert (await _integration(ws))["contact"] == "none"
        old = {"Authorization": "Bearer from-before-the-factory-settings"}
        assert (await client.get("/integration/state", headers=old)).status == 401
        assert (await _integration(ws))["contact"] == "refused"
        assert len(told) == 1 and told[0]["secret"] == secret  # the new key, to HA
        assert (await client.get("/integration/state", headers=old)).status == 401
        assert len(told) == 1  # not again right away
        good = {"Authorization": f"Bearer {secret}"}
        assert (await client.get("/integration/state", headers=good)).status == 200
        assert (await _integration(ws))["contact"] == "ok"
        await clock.advance(5 * 60)
        assert (await _integration(ws))["contact"] == "none"  # HA stopped asking
        await ws.close()
    await hm.stop()


@pytest.mark.fresh_install
async def test_done_waits_until_the_reading_is_done_unless_it_stands_still(tmp_path: Path) -> None:
    """Klaar waited at most three minutes; then HouseMind went on with the history only half
    read. Now Klaar waits as long as the reading goes on – ten minutes here – and only when it
    stands still for three minutes can it after all. The reading's end turns HouseMind on."""
    hm, clock, _ = await start(tmp_path, consent=False)
    assert await setting_up.begin(hm, "demo-user-1", "nl")
    assert await setting_up.goto(hm, "finish")
    status = hm.history_status
    status.state, status.progress, status.moved_at = "reading", 0.1, clock.now()
    for _ in range(5):  # a big house: every two minutes the reading goes a step on
        await clock.advance(2 * 60)
        status.progress, status.moved_at = status.progress + 0.1, clock.now()
        assert await setting_up.finish(hm) == "reading"
        assert not setting_up.stalled(hm)
    await clock.advance(2 * 60)
    assert await setting_up.finish(hm) == "reading"
    await clock.advance(60)  # three minutes without a step on: it stands still
    assert setting_up.stalled(hm) and await setting_up.read_wait(hm) == 0
    assert await setting_up.finish(hm) == "done"
    assert await setting_up.state(hm) == "done" and not hm.activated  # still reading
    status.state = "done"
    assert await start_over.maybe_activate(hm) and hm.activated
    await hm.stop()


async def test_no_to_learning_after_all_stops_the_reading(tmp_path: Path) -> None:
    """Ja on 'Mag ik leren?' started the reading; going back and choosing Nee left it running,
    and what it read was taken over. Now Nee stops it, and nothing of it is learned."""
    hm, _clock, _ha = await history_house(tmp_path, lambda ha: evenings(ha, 10))
    assert hm.learning is not None
    await hm.set_house_setting("consent_learning", True)
    task = hm._history_task
    assert task is not None and not task.done()
    await hm.set_house_setting("consent_learning", False)
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert hm.history_status.state == "idle" and hm.history_status.read_at is None
    assert not [k for k in hm.learning.counts if k[2].startswith(HISTORY)]
    await hm.stop()


@pytest.mark.fresh_install
async def test_the_house_questions_come_in_step_one_and_are_not_asked_again(
    tmp_path: Path,
) -> None:
    """After the administrator's own introduction, step 1 asks about the house: where new
    residents start, people without a phone, and – with a calendar in HA – whether Vesta may
    read it. An answer there is not asked again once HouseMind runs."""
    hm, clock, ha = await start(tmp_path, consent=False)
    ha.model.entities["calendar.family"] = Entity("calendar.family", "r-cal", "Gezin")
    ha.states["calendar.family"] = State("calendar.family", "off", {}, clock.now())
    await hm._refresh_model()
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        await ws.send_json({"type": "setup_start"})
        await _snapshot(ws, lambda s: s["setup"]["state"] == "intro")
        await ws.send_json({"type": "get", "what": "settings"})
        settings = (await _next(ws, "data"))["data"]
        assert settings["hasCalendar"] and not settings["calendarAsked"]
        assert settings["newPersonLevel"] == 1 and settings["phonelessResidents"] is False
        for key, value in (
            ("new_person_level", 2),
            ("phoneless_residents", True),
            ("calendar", False),
        ):
            await ws.send_json({"type": "house_setting", "key": key, "value": value})
        await ws.send_json({"type": "get", "what": "settings"})
        settings = (await _next(ws, "data"))["data"]
        assert settings["newPersonLevel"] == 2 and settings["phonelessResidents"] is True
        assert settings["calendarAsked"] and settings["calendar"] is False
        await ws.close()
    assert await setting_up.goto(hm, "finish") and await setting_up.finish(hm) == "done"
    await clock.advance(16 * 60)
    await hm.tick()
    assert "calendar_consent" not in hm._raised  # answered while setting up: no again
    await hm.stop()


async def test_giving_everything_back_also_takes_the_link_away(tmp_path: Path) -> None:
    """Before removing HouseMind (E-112): the automations go back on, everything to Observe,
    and now also the link in HA – the device, the Assist agent and her own pipeline. "Toch
    weer aanzetten" takes it up again; the link can then be placed again."""
    hm, clock, ha = await start(tmp_path)
    await _link(hm, ha, clock)
    assert await setting_up.make_pipeline(hm)
    assert ha.pipelines and not ha.unlinked
    done = await hm.give_back(True, "demo-user-1")
    assert done["ok"] and done["link"] and ha.unlinked
    assert ha.pipelines == []  # her own pipeline went with it
    assert (await hm.give_back(False, "demo-user-1"))["ok"]
    assert (await hm.integration_status())["contact"] == "none"
    await hm.stop()


@pytest.mark.fresh_install
async def test_setting_up_with_the_usual_choices_takes_at_most_fifteen_minutes(
    tmp_path: Path,
) -> None:
    """S-04: from "Inrichten" to Klaar in at most 15 minutes. Walked through as the screens
    do, with the usual choices, half a minute to read and tap each screen or question, and
    HouseMind's own wait for the reading counted in full."""
    hm, clock, _ = await start(tmp_path, consent=False)
    began = clock.now()
    taps = 0

    async def tap(ws: Any, message: dict[str, Any] | None) -> None:
        """Half a minute to read a screen or question and tap; some taps send nothing."""
        nonlocal taps
        taps += 1
        await clock.advance(30)
        if message is not None:
            await ws.send_json(message)

    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        await tap(ws, {"type": "setup_start"})
        await _snapshot(ws, lambda s: s["setup"]["state"] == "intro")
        # Her own introduction, as the screen sends it (E-117: at most four questions).
        await tap(ws, {"type": "set_level", "level": 2})
        await tap(ws, {"type": "set_pref", "key": "emergency_push", "value": True})
        await tap(ws, {"type": "house_setting", "key": "consent_learning", "value": True})
        await tap(ws, None)  # no pets
        await tap(ws, {"type": "set_pref", "key": "onboarded", "value": True})
        # The questions about the house: the usual answers are already chosen.
        await tap(ws, None)  # new residents start on Carefree
        await tap(ws, {"type": "house_setting", "key": "phoneless_residents", "value": False})
        for step in ("link", "vesta", "ai", "finish"):
            if step == "vesta":
                await tap(ws, None)  # no link yet: "Toch verder?", then "Ja, later koppelen"
            await tap(ws, {"type": "setup_step", "step": step})
        await _snapshot(ws, lambda s: s["setup"]["state"] == "finish")
        waited = await setting_up.read_wait(hm)
        await clock.advance(waited)  # Klaar waits for the reading (here none to read)
        await tap(ws, {"type": "setup_done"})
        await _snapshot(ws, lambda s: s["setup"]["state"] == "done")
        await ws.close()
    assert waited <= setting_up.READ_STALL.total_seconds()
    assert clock.now() - began <= timedelta(minutes=15), (taps, waited)
    await hm.stop()


async def test_one_yes_links_restarts_and_finishes_the_link_by_itself(tmp_path: Path) -> None:
    """Linking took three things from the administrator: place it, restart HA, then click
    Configure and Submit in HA. Now one yes: HouseMind places it, restarts HA because HA does
    not know it yet, and after the restart finishes the setup in HA by itself – nobody clicks
    in HA. "Gekoppeld" follows once HA reaches HouseMind."""
    from tests.test_bridge import start as linked_start

    config = tmp_path / "config"
    config.mkdir()
    hm, clock, ha = await linked_start(tmp_path, ha_config=config)
    ha.restart_loads.add("housemind")  # HA reads the placed files when it starts again
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await _hello(client)
        await ws.send_json({"type": "integration", "do": "link", "yes": True})
        said = (await _next(ws, "say"))["text"]
        assert said.startswith("Home Assistant herstart nu")
        status = (await _next(ws, "data"))["data"]  # the screen gets the link's status at once
        assert status["linking"] and status["installed"] and ha.restarts == 1
        assert ha.integrations.get("housemind") is None  # not before HA knows it
        await clock.advance(70)  # HA is back
        await hm.tick()
        assert ha.integrations["housemind"] == "configured"  # finished in HA by HouseMind
        secret = await hm.integration_secret()
        headers = {"Authorization": f"Bearer {secret}"}
        assert (await client.get("/integration/state", headers=headers)).status == 200
        status = await _integration(ws)
        assert status["contact"] == "ok" and not status["linking"]
        await ws.close()
    assert ha.restarts == 1  # one restart, after the one yes
    await hm.stop()


async def test_when_finishing_fails_the_screen_shows_the_two_clicks_in_ha(
    tmp_path: Path,
) -> None:
    """If HA does not let HouseMind finish it (an older HA, other rights), HouseMind does not
    keep trying: the screen shows the steps and a button that opens the right page in HA,
    and "Opnieuw proberen"."""
    from tests.test_bridge import start as linked_start

    config = tmp_path / "config"
    config.mkdir()
    hm, clock, ha = await linked_start(tmp_path, ha_config=config)
    ha.known_integrations.add("housemind")  # no restart needed
    ha.refuse_flows = True
    result = await hm.link_ha()
    assert result == {"ok": False, "step": "failed"} and ha.restarts == 0
    status = await hm.integration_status()
    assert status["finish"] == "failed" and not status["linking"]
    tried = ha.integrations.copy()
    await clock.advance(5 * 60)
    await hm.tick()
    assert ha.integrations == tried  # no trying again by itself
    ha.refuse_flows = False
    assert (await hm.link_ha())["step"] == "done"  # "Opnieuw proberen"
    assert ha.integrations["housemind"] == "configured"
    await hm.stop()


async def test_the_link_status_comes_also_while_ha_restarts(tmp_path: Path) -> None:
    """Found in the real house: right after "Koppelen" HA went away for its restart, and the
    screen got no status at all ("Message integration failed"). Now it gets one."""
    hm, _clock, ha = await start(tmp_path)
    ha.restarting = True
    status = await hm.integration_status()
    assert status["loaded"] is False and status["speech"] is None
    await hm.stop()


async def test_a_screen_that_opens_while_ha_restarts_still_gets_its_hello(
    tmp_path: Path,
) -> None:
    """Found in the real house: the page opened while HA restarted, and its hello failed
    ("Message hello failed"), so it showed nothing. Now the people stay as they were."""
    hm, _clock, ha = await start(tmp_path)
    known = list(hm._people)
    ha.restarting = True
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/api/ws", headers=ADMIN)
        await ws.send_json({"type": "hello", "haLanguage": "en", "browserLanguage": "nl"})
        assert (await _snapshot(ws))["setup"]
        await ws.close()
    assert hm._people == known
    await hm.stop()


async def test_an_old_announcement_never_stays_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found in the real house: after the factory settings HA still offered HouseMind under
    "Ontdekt" with the key from before, and confirming it was refused. Now HouseMind takes
    back its earlier announcement before a new one, and all of them at the factory settings;
    and the integration takes the current key from the App's own file next to it."""
    told: list[tuple[str, list[str]]] = []
    taken_back: list[str] = []

    async def announce(
        token: str, config: dict[str, Any], earlier: list[str] | None = None
    ) -> str | None:
        for uuid in earlier or []:
            taken_back.append(uuid)
        told.append((str(config["secret"]), list(earlier or [])))
        return f"announcement-{len(told)}"

    async def withdraw(token: str, uuid: str) -> bool:
        taken_back.append(uuid)
        return True

    monkeypatch.setattr("housemind.ha.integration.announce", announce)
    monkeypatch.setattr("housemind.ha.integration.withdraw", withdraw)
    config, app = tmp_path / "config", tmp_path / "app"
    config.mkdir()
    app.mkdir()
    clock = SimulatedClock(datetime(2026, 10, 20, 12, 0, tzinfo=UTC))
    house = load_demo_house("family_home", clock, "nl")
    paths = Paths([ROOT / "knowledge"], app, config, "supervisor", app)
    hm = HouseMind(clock, house.ha, paths)
    await hm.start(autorun=False)
    assert (await hm.place_integration())["ok"]
    assert (await hm.place_integration())["ok"]  # again: the first one is taken back
    assert told[1][1] == ["announcement-1"] and taken_back == ["announcement-1"]
    assert await start_over.remove_link(hm)  # the factory settings
    assert taken_back == ["announcement-1", "announcement-2"]
    assert hm._announced_ids() == []
    await hm.stop()


def test_confirming_a_discovered_housemind_uses_the_current_key() -> None:
    """The integration's discovery step reads the App's connection next to it first."""
    source = (
        ROOT / "integration" / "custom_components" / "housemind" / "config_flow.py"
    ).read_text()
    step = source[source.index("async def async_step_hassio(") :]
    step = step[: step.index("async def async_step_hassio_confirm")]
    assert "_read_connection" in step and "connection = placed" in step
