"""Test het huis (E-474 – E-481, E-518, E-519, design 70, 85), from the user's side. The
administrator's wish (11-11-2026): "Is het ook misschien mogelijk om een teststatus te hebben of
een testknop, eventueel per ruimte, per actie, om inderdaad live te gaan testen?" – and
(14-11-2026): "als ik simuleer dat iemand binnenkomt, dan moet ook daadwerkelijk de lamp aan."
Speel het na says what HouseMind would do in a situation, switching nothing, with per line how
it goes for real; Doe het echt does it in the house and puts it back after a minute; looking
never moves the house mode. Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from housemind.core.model import Source
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind
from housemind.server import views
from housemind.server.web import create_app
from housemind.world.house_mode import HouseModeMachine
from tests.test_real_house_counts import _house

MORNING = datetime(2026, 11, 14, 9, 11, tzinfo=UTC)  # a Saturday, 10:11 in the house, light


def _states(hm: HouseMind) -> dict[str, str]:
    return {e: s.state for e, s in hm.ha.states.items()}  # type: ignore[attr-defined]


def _kept(hm: HouseMind) -> Any:
    assert hm.learning is not None
    return copy.deepcopy((hm.learning.counts, hm.learning.values, dict(hm.learning.steps)))


async def test_pretend_says_what_it_would_do_and_switches_nothing(tmp_path: Path) -> None:
    """Woonkamer, 'Iemand komt binnen' and 'Donker': HouseMind says it would switch a lamp on,
    that for real it only watches now (a new house), and that Doe het echt would do it. The
    house, what HouseMind learned, the house mode and the Logboek stay as they were."""
    hm, _clock, _ha = await _house(tmp_path)
    assert hm.world is not None
    before, kept, mode, recent = _states(hm), _kept(hm), hm.world.modes.mode, dict(hm.recent)
    view = hm.test_view("nl", "living", ["someone", "dark"])
    lamp = next(line for line in view["lines"] if "aandoen" in line["text"])
    assert lamp["real"] == "Staat op Observeren: in het echt kijk ik alleen mee."
    assert lamp["kind"] == "watch" and lamp["will"] and lamp["skip"] is None
    assert view["now"].startswith("Nu: ")
    assert {"key": "someone", "on": True, "implied": False} in view["situations"]
    assert _states(hm) == before and _kept(hm) == kept
    assert hm.world.modes.mode is mode and hm.recent == recent
    await hm.stop()


async def test_only_what_the_room_has_and_one_of_a_group(tmp_path: Path) -> None:
    """The buttons come from what a room has: the toilet has no sensor that sees people, the
    hall a window, the kitchen a smoke detector. Choosing 'Niemand meer' takes 'Iemand komt
    binnen' away, and one part of the day takes the other."""
    hm, _clock, _ha = await _house(tmp_path)

    def keys(area: str) -> list[str]:
        return [s["key"] for s in hm.test_view("nl", area, [])["situations"]]

    assert "someone" not in keys("toilet") and "window" not in keys("living")
    assert "window" in keys("hall") and "smoke" in keys("kitchen")
    assert hm.test_choose("living", ["someone", "dark"], "nobody", True) == ["nobody", "dark"]
    assert hm.test_choose("living", ["evening"], "night", True) == ["night"]
    assert hm.test_choose("living", ["dark"], "dark", False) == []
    assert hm.test_choose("toilet", [], "someone", True) == []  # not in this room
    # What contradicts itself does not go together (E-519): everyone away and someone coming in.
    assert hm.test_choose("living", ["someone", "evening"], "away", True) == ["away", "evening"]
    assert hm.test_choose("living", ["away", "dark"], "someone", True) == ["someone", "dark"]
    assert hm.test_choose("living", ["away"], "sleep", True) == ["sleep"]
    await hm.stop()


async def test_pretend_smoke_shows_the_reaction_without_it(tmp_path: Path) -> None:
    """Keuken, 'Rook': the lamp there would go on fully, the lamps elsewhere too, and an
    emergency notification would go out – and nothing happens."""
    hm, _clock, _ha = await _house(tmp_path)
    before = _states(hm)
    view = hm.test_view("nl", "kitchen", ["smoke"])
    texts = [line["text"] for line in view["lines"]]
    assert any("Keuken" in text and "aandoen" in text for text in texts)
    assert any(text.startswith("Ook ") and "lampen elders in huis" in text for text in texts)
    assert texts[-1] == "Ik stuur meteen een noodmelding."
    assert _states(hm) == before and hm.status.emergency is None
    await hm.stop()


async def test_looking_moves_no_house_mode(tmp_path: Path) -> None:
    """'Wat ik nu zou doen' and Test het huis only look (design 70.1): the house mode is not
    worked out again – before, every look of 'Wat ik nu zou doen' did that, every 10 seconds."""
    hm, _clock, _ha = await _house(tmp_path)
    real = HouseModeMachine.update
    with patch.object(HouseModeMachine, "update", autospec=True, side_effect=real) as moved:
        hm.simulate("nl")
        hm.test_view("nl", "living", ["someone"])
        assert not moved.called
        await hm.tick()  # HouseMind's own round does
        assert moved.called
    await hm.stop()


async def test_a_screen_moves_no_house_mode(tmp_path: Path) -> None:
    """Opening HouseMind – the home screen with its rooms and the morning card – and a wall
    tablet only look (design 70.1): the house mode is not worked out again for a screen; before,
    every new picture on every screen did that."""
    hm, _clock, _ha = await _house(tmp_path)
    real = HouseModeMachine.update
    with patch.object(HouseModeMachine, "update", autospec=True, side_effect=real) as moved:
        async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
            ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
            await ws.send_json({"type": "hello", "browserLanguage": "nl"})
            while (await ws.receive_json(timeout=5))["type"] != "snapshot":
                pass
            await ws.close()
        assert views.tablet_view(hm, "living", "nl") is not None
        assert not moved.called
    await hm.stop()


async def test_only_an_administrator_on_expert_can_test(tmp_path: Path) -> None:
    """The server answers Test het huis only for an administrator on Expert (B-388): a
    resident gets nothing, also when asking for it by hand."""
    hm, _clock, _ha = await _house(tmp_path)

    async def answer(ws: Any, *messages: dict[str, Any]) -> dict[str, Any] | None:
        """The last view of the panel the messages brought, once Vesta answered a tap after
        them – so they were all handled; None: nothing came."""
        for message in messages:
            await ws.send_json(message)
        await ws.send_json({"type": "orb_tap"})
        found = None
        while (message := await ws.receive_json(timeout=5))["type"] != "say":
            if message["type"] == "data" and message["what"] == "test":
                found = message["data"]
        return found

    ask = ({"type": "test", "do": "area", "area": "living"}, {"type": "get", "what": "test"})

    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        resident = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-2"})
        await resident.send_json({"type": "hello", "browserLanguage": "nl"})
        await resident.send_json({"type": "set_level", "level": 4})
        assert await answer(resident, *ask) is None
        await resident.close()
        admin = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await admin.send_json({"type": "hello", "browserLanguage": "nl"})
        await admin.send_json({"type": "set_level", "level": 3})
        assert await answer(admin, *ask) is None
        await admin.send_json({"type": "set_level", "level": 4})
        view = await answer(admin, *ask)
        assert view is not None and view["area"] == "living"
        view = await answer(admin, {"type": "test", "do": "pretend", "key": "someone", "on": True})
        on = {"key": "someone", "on": True, "implied": False}
        assert view is not None and on in view["situations"]
        await admin.close()
    await hm.stop()


# -- Doe het echt (E-476, E-479, E-480, E-518, B-390, B-394, B-444, B-445) -------------------


def _play(hm: HouseMind) -> dict[str, Any]:
    return hm.test_view("nl", "living", [])["plays"][0]  # type: ignore[no-any-return]


async def test_play_does_it_for_real_and_puts_it_back_after_a_minute(tmp_path: Path) -> None:
    """The administrator's wish: 'als ik simuleer dat iemand binnenkomt, dan moet ook
    daadwerkelijk de lamp aan.' Woonkamer, 'Iemand komt binnen' and 'Donker', Doe het echt: the
    lamps go on and the curtains close in the house, through the gate as a test – also on
    Observeren – the panel says until when, the Logboek says it was a test; after a minute all
    is as it was by itself, and what HouseMind learned stays as it was."""
    hm, clock, ha = await _house(tmp_path)
    kept = _kept(hm)
    assert ha.states["light.living_ceiling"].state == "off"
    assert ha.states["cover.living_curtain"].state == "open"
    assert await hm.test_play("nl", "living", ["someone", "dark"], "u") is None
    assert ha.states["light.living_ceiling"].state == "on"
    assert ha.states["cover.living_curtain"].state == "closed"
    play = _play(hm)
    assert play["running"] and play["until"]
    assert {line["state"] for line in play["lines"]} == {"done"}
    assert any(line.get("icon") == "curtains" for line in play["lines"])
    texts = [r["text"] for r in hm.test_view("nl", "living", [])["happened"] if r["test"]]
    assert any("Test het huis" in text for text in texts)
    live = views.live_path(hm, "nl")  # the network: the line flows to the room and the device
    assert live is not None and live["area"] == "living" and live["flowFor"] > 0
    await clock.advance(61)
    await hm.settled()
    assert ha.states["light.living_ceiling"].state == "off"
    assert ha.states["cover.living_curtain"].state == "open"
    play = _play(hm)
    assert not play["running"] and {line["state"] for line in play["lines"]} == {"back"}
    assert _kept(hm) == kept
    await hm.stop()


async def test_keep_it_keeps_everything(tmp_path: Path) -> None:
    """Laat zo keeps everything of the test after the minute: the lamps on, the curtains
    closed."""
    hm, clock, ha = await _house(tmp_path)
    await hm.test_play("nl", "living", ["someone", "dark"], "u")
    await hm.test_play_keep(_play(hm)["id"])
    await clock.advance(61)
    await hm.settled()
    assert ha.states["light.living_ceiling"].state == "on"
    assert ha.states["cover.living_curtain"].state == "closed"
    assert {line["state"] for line in _play(hm)["lines"]} == {"kept"}
    await hm.stop()


async def test_a_hand_in_between_leaves_it(tmp_path: Path) -> None:
    """When someone switches a lamp themselves during the minute, it stays as they left it –
    and that hand is no correction of anything; the rest goes back."""
    hm, clock, ha = await _house(tmp_path)
    assert hm.learning is not None
    lamp = "light.living_ceiling"
    kept = _kept(hm)
    await hm.test_play("nl", "living", ["someone", "dark"], "u")
    await ha.simulate(lamp, "on", {"brightness": 40}, by=Source.PERSON)  # a hand, within the minute
    await clock.advance(61)
    await hm.settled()
    assert ha.states[lamp].state == "on"
    assert ha.states["cover.living_curtain"].state == "open"
    states = {line["text"]: line["state"] for line in _play(hm)["lines"]}
    assert states["Woonkamer plafondlamp aandoen"] == "left"
    assert states["Woonkamer gordijnen sluiten"] == "back"
    assert _kept(hm) == kept and not hm.learning.corrections
    await hm.stop()


async def test_an_old_test_never_ends_a_new_one(tmp_path: Path) -> None:
    """Laat zo in the living room, and half a minute later a test in the kitchen: the kitchen
    lamp stays on for its own full minute – the first test's minute ending puts nothing of the
    second back, also when the database gave the second test the first one's numbers again."""
    hm, clock, ha = await _house(tmp_path)
    await hm.test_play("nl", "living", ["someone", "dark"], "u")
    await hm.test_play_keep(_play(hm)["id"])
    await clock.advance(30)
    await hm.test_play("nl", "kitchen", ["someone", "dark"], "u")
    assert ha.states["light.kitchen"].state == "on"
    await clock.advance(31)  # the living room's minute is over
    assert ha.states["light.kitchen"].state == "on"
    await clock.advance(30)  # and the kitchen's
    await hm.settled()
    assert ha.states["light.kitchen"].state == "off"
    assert ha.states["light.living_ceiling"].state == "on"  # Laat zo
    await hm.stop()


async def test_honest_per_line_and_what_a_test_does_not_do(tmp_path: Path) -> None:
    """Per line how it goes for real: on Autonoom 'doe ik ook echt', a lamp someone just
    switched 'in het echt wacht ik nog even' – the test still does it – and a lamp another
    system steers is left, with why, in the list and after Doe het echt. An emergency of Doe
    alsof is never played."""
    hm, _clock, ha = await _house(tmp_path)
    assert hm.gate is not None and hm.learning is not None
    hm.learning.steps[("covers_close_dark", "living")] = Step.AUTONOMOUS
    hm.gate.state.people_win_until["light.living_reading"] = hm.clock.now() + timedelta(hours=1)
    hm.gate.state.other_system.add("light.living_ceiling")
    view = hm.test_view("nl", "living", ["someone", "dark"])
    lines = {line["text"].split(" aandoen")[0].split(" sluiten")[0]: line for line in view["lines"]}
    curtain = lines["Ik zou Woonkamer gordijnen"]
    assert curtain["real"] == "Staat op Autonoom: doe ik ook echt." and curtain["will"]
    reading = lines["Ik zou Woonkamer leeslamp"]
    assert reading["kind"] == "hand" and reading["will"]
    assert reading["real"] == "Iemand bediende dit net zelf: in het echt wacht ik nog even."
    ceiling = lines["Ik zou Woonkamer plafondlamp"]
    assert ceiling["kind"] == "other" and not ceiling["will"]
    assert "Een ander systeem of een automatisering stuurt dit apparaat" in ceiling["real"]
    assert ceiling["skip"] == "Ook niet in de test."
    await hm.test_play("nl", "living", ["someone", "dark"], "u")
    assert ha.states["light.living_reading"].state == "on"
    assert ha.states["light.living_ceiling"].state == "off"
    said = {line["text"]: line["say"] for line in _play(hm)["lines"]}
    assert said["Woonkamer plafondlamp aandoen"].startswith("Niet gedaan: Een ander systeem")
    smoke = hm.test_view("nl", "kitchen", ["smoke"])
    assert smoke["lines"] and not any(line["will"] for line in smoke["lines"])
    before = _states(hm)
    said = await hm.test_play("nl", "kitchen", ["someone", "smoke"], "u")
    assert said == "Een noodreactie doe ik in een test niet echt." and _states(hm) == before
    await hm.stop()


async def test_a_house_rule_holds_a_test(tmp_path: Path) -> None:
    """A house rule says no to the curtains: the line says so, and Doe het echt does the lamps
    and leaves the curtains open, with the house rule as why (E-480)."""
    hm, _clock, ha = await _house(tmp_path)
    assert hm.gate is not None
    real = hm.gate.state.house_rule_veto
    hm.gate.state.house_rule_veto = lambda asked: (
        "no_curtains" if asked.entity_id == "cover.living_curtain" else real(asked)
    )
    view = hm.test_view("nl", "living", ["someone", "dark"])
    curtain = next(line for line in view["lines"] if "gordijnen" in line["text"])
    assert curtain["kind"] == "not" and not curtain["will"]
    assert curtain["real"] == "In het echt niet: Een huisregel zegt dat het niet mag."
    await hm.test_play("nl", "living", ["someone", "dark"], "u")
    assert ha.states["cover.living_curtain"].state == "open"
    assert ha.states["light.living_ceiling"].state == "on"
    await hm.stop()


async def test_while_it_plays_the_everyday_rules_keep_off(tmp_path: Path) -> None:
    """The administrator's case: at 10:11 in the morning he plays 'Avond'. The curtains the
    test closes stay closed for the whole minute, although 'gordijnen open in de ochtend' stands
    on Autonoom and it is morning for real: HouseMind's own rule waits – 'Wat er net gebeurde'
    says an administrator is testing it – and after the minute the curtains are open as before
    (E-518, B-445)."""
    hm, clock, ha = await _house(tmp_path, start=MORNING, located=True)
    assert hm.gate is not None and hm.learning is not None
    hm.learning.steps[("covers_open_morning", "living")] = Step.AUTONOMOUS
    curtain = "cover.living_curtain"
    await hm.test_play("nl", "living", ["evening"], "u")
    assert ha.states[curtain].state == "closed" and hm.gate.state.testing(curtain, clock.now())
    for _ in range(5):  # HouseMind's own rounds within the minute
        await clock.advance(10)
        await hm.tick()
        assert ha.states[curtain].state == "closed"
    said = [r["check"] or "" for r in hm.test_view("nl", "living", [])["happened"]]
    assert any("Een beheerder test dit apparaat nu bij Test het huis." in x for x in said), said
    await clock.advance(11)
    await hm.settled()
    assert not hm.gate.state.testing_until
    assert ha.states[curtain].state == "open"
    await hm.stop()


async def test_evening_brings_its_dark_and_closes_the_curtains(tmp_path: Path) -> None:
    """The administrator chose 'Avond' at 10:11 in the morning and HouseMind said it would do
    nothing: the evening was as light as the morning. Now 'Avond' in November is dark as the sun
    says at 20:00 that day – the Donker button is half on – so the curtains would close, and
    Doe het echt closes them; 'Licht' chosen by hand still wins (E-519)."""
    hm, _clock, ha = await _house(tmp_path, start=MORNING, located=True)
    assert not [x for x in hm.test_view("nl", "living", [])["lines"] if "gordijnen" in x["text"]]
    view = hm.test_view("nl", "living", ["evening"])
    assert {"key": "dark", "on": False, "implied": True} in view["situations"]
    assert any("gordijnen sluiten" in line["text"] for line in view["lines"])
    light = hm.test_view("nl", "living", ["evening", "light"])
    assert not any(s["implied"] for s in light["situations"])
    assert not any("gordijnen sluiten" in line["text"] for line in light["lines"])
    await hm.test_play("nl", "living", ["evening"], "u")
    assert ha.states["cover.living_curtain"].state == "closed"
    await hm.stop()


async def test_a_restart_in_the_minute_still_puts_it_back(tmp_path: Path) -> None:
    """HouseMind stops in the middle of a test: after the start the lamp and the curtains go
    back, as the test left them – what goes back is kept in the database (B-394)."""
    hm, _clock, ha = await _house(tmp_path)
    await hm.test_play("nl", "living", ["someone", "dark"], "u")
    assert ha.states["light.living_ceiling"].state == "on"
    await hm.stop()
    again = HouseMind(hm.clock, ha, hm.paths)
    await again.start(autorun=False)
    assert ha.states["light.living_ceiling"].state == "off"
    assert ha.states["cover.living_curtain"].state == "open"
    await again.stop()


# -- the testkwartier (part C: E-477, E-479, E-480, B-391, B-394) ----------------------------

PRESENCE = "binary_sensor.living_presence"
LAMPS = ("light.living_ceiling", "light.living_reading")


async def test_a_quarter_acts_with_the_real_sensor_and_everything_goes_back(
    tmp_path: Path,
) -> None:
    """Woonkamer on Observeren: without a test, coming in only makes HouseMind watch. In a
    testkwartier for the lighting, the real presence sensor turns the lamps on, as on
    Autonoom; the bar at the top says until when. After the quarter the step is back, the
    lamps are off again, and HouseMind learned nothing from it."""
    hm, clock, ha = await _house(tmp_path)
    assert hm.learning is not None and hm.gate is not None
    step = hm._skill_step("light_on_entry", "living")
    await hm.test_quarter("living", "lighting", 15, "u")
    assert [t["part"] for t in hm.test_running("nl")] == ["Verlichting"]
    assert hm.test_view("nl", "living", [])["quarter"]["parts"][0]["running"] is not None
    await ha.simulate(PRESENCE, "on")
    await hm.tick()
    assert any(ha.states[lamp].state == "on" for lamp in LAMPS)
    assert not any(p.entity_id in LAMPS for p in hm.learning.pending)
    texts = [r["text"] for r in hm.test_view("nl", "living", [])["happened"] if r["test"]]
    assert texts
    await clock.advance(15 * 60 + 1)
    await hm.settled()
    assert all(ha.states[lamp].state == "off" for lamp in LAMPS)
    assert hm._skill_step("light_on_entry", "living") is step and not hm.gate.state.tests
    assert hm.test_running("nl") == []
    await hm.stop()


async def test_a_quarter_ends_at_once_in_an_emergency(tmp_path: Path) -> None:
    """Smoke in the kitchen during a testkwartier: every test stops at once, and nothing is
    put back over the emergency reaction (E-480)."""
    hm, _clock, ha = await _house(tmp_path)
    assert hm.gate is not None
    await hm.test_quarter("living", "lighting", 15, "u")
    await ha.simulate("binary_sensor.kitchen_smoke", "on")
    await hm.tick()
    assert hm.test_running("nl") == [] and not hm.gate.state.tests
    await hm.stop()


async def test_stop_all_ends_every_test_and_only_real_parts_can_quarter(tmp_path: Path) -> None:
    """'Stop test en zet alles terug' ends a running quarter and a Doe het echt; a room offers a
    testkwartier only for what it has and may be tried for real."""
    hm, _clock, ha = await _house(tmp_path)
    parts = [p["key"] for p in hm.test_view("nl", "living", [])["quarter"]["parts"]]
    assert "lighting" in parts and "climate" in parts
    assert hm.test_view("nl", "guest_room", [])["quarter"]["parts"] == []
    await hm.test_quarter("living", "lighting", 15, "u")
    lamp = "light.living_reading"
    await hm.test_play("nl", "living", ["someone", "dark"], "u")
    assert ha.states[lamp].state == "on"
    await hm.test_stop_all()
    assert hm.test_running("nl") == [] and ha.states[lamp].state == "off"
    assert not hm.test_view("nl", "living", [])["anyRunning"]
    await hm.stop()


async def test_a_restart_in_a_quarter_puts_back_what_it_did(tmp_path: Path) -> None:
    """HouseMind stops during a testkwartier: after the start the lamps it switched are off
    again, and no test runs any more (B-394)."""
    hm, _clock, ha = await _house(tmp_path)
    await hm.test_quarter("living", "lighting", 15, "u")
    await ha.simulate(PRESENCE, "on")
    await hm.tick()
    lit = [lamp for lamp in LAMPS if ha.states[lamp].state == "on"]
    assert lit
    await hm.stop()
    await ha.simulate(PRESENCE, "off")
    again = HouseMind(hm.clock, ha, hm.paths)
    await again.start(autorun=False)
    assert all(ha.states[lamp].state == "off" for lamp in lit)
    assert again.test_running("nl") == []
    await again.stop()
