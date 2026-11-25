"""Vesta remembers your answer (E-381 – E-384, chapter 37), from the user's side. Found in a test
of the real house's version: a notice she mentioned (a camera that stopped answering) had no
buttons of its own, "negeer maar" was no choice, "Niet meer tonen" did not count for her, a
closed notice came back at the next round. Each test fails without the build.

The camera's 'reageert al een dag niet' went into Home Assistant netjes (E-354, B-238), for the
administrators; the same kind of notice – a maintenance word for everyone at home – is now a
battery that is almost empty."""

from __future__ import annotations

import json
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.notify import Kind
from housemind.brain.rules import parse_rule
from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, State
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths
from housemind.server.web import create_app
from housemind.world.house_mode import HouseMode
from tests.test_ai import FakeAI, enable
from tests.test_round5_retest import EVENING, ROOT
from tests.test_vesta import _house, _next

USER = {"X-Remote-User-Id": "demo-user-1"}
BAT = "sensor.tuinhuis_battery"
TOPIC = f"maintenance:battery:{BAT}"


def _battery(clock: Any, level: str) -> State:
    return State(BAT, level, {"unit_of_measurement": "%"}, clock.now())


async def _battery_low(hm: Any, ha: Any, clock: Any) -> None:
    """A sensor in the garden shed whose battery is almost empty."""
    device = Device("bat-shed", "Sensor Tuinhuis", "garden")
    entity = Entity(
        BAT, "r-bat", "Sensor Tuinhuis Batterij", "bat-shed", "garden", device_class="battery"
    )
    for model in (hm.model, ha.model):
        model.devices["bat-shed"] = device
        model.entities[BAT] = entity
    ha.states[BAT] = hm.world.states[BAT] = _battery(clock, "8")
    hm.activated = True
    await hm._maintenance(hm.world.snapshot(clock.now()))


async def _restart(tmp_path: Path) -> tuple[Any, Any, Any]:
    """The same house after a restart: what it stored is still there."""
    clock = SimulatedClock(EVENING.astimezone(UTC))
    ha = load_demo_house("family_home", clock, "nl").ha
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm._refresh_people()
    assert hm.world is not None
    hm.world.modes.mode = HouseMode.HOME
    return hm, ha, clock


async def _round(hm: Any, clock: Any) -> None:
    await clock.advance(120)
    await hm._maintenance(hm.world.snapshot(clock.now()))


async def _tap(client: TestClient) -> tuple[Any, dict[str, Any]]:
    """A tap on Vesta, typing (no microphone): she says what matters now."""
    ws = await client.ws_connect("/api/ws", headers=USER)
    await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": False})
    await ws.send_json({"type": "orb_tap"})
    return ws, await _next(ws, "say")


def _about_battery(said: dict[str, Any]) -> bool:
    text = said["text"].lower()  # in her words: "de batterij van de sensor in de tuin"
    return "batterij" in text and "tuin" in text


async def test_a_notice_she_mentions_has_its_own_buttons(tmp_path: Path) -> None:
    """She said the battery is almost empty, with only her general choices – or, with an AI,
    the house-mode buttons Vakantie and Gasten. Now the notice is her question, with Ik kijk
    ernaar and Niet meer tonen – no Later: it is for information (QA 3, schermen 27)."""
    hm, ha, clock = await _house(tmp_path, level=4)
    await _battery_low(hm, ha, clock)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, said = await _tap(client)
        assert _about_battery(said)
        ask = said["ask"]
        assert ask["actions"] == ["seen", "mute"]
        words = [hm.languages.text("nl", f"ui.notifications.{a}") for a in ask["actions"]]
        assert words == ["Ik kijk ernaar", "Niet meer tonen"]
        await ws.close()
    await hm.stop()


async def test_negeer_maar_is_never_again_also_after_a_restart(tmp_path: Path) -> None:
    """Typed "negeer maar" was "Dat is niet een van mijn keuzes". Now, without an AI, it means
    Niet meer tonen: she no longer mentions it, also after a restart."""
    hm, ha, clock = await _house(tmp_path, level=4)
    await _battery_low(hm, ha, clock)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, said = await _tap(client)
        assert _about_battery(said)
        await ws.send_json({"type": "talk", "text": "negeer maar"})
        reply = await _next(ws, "say")
        assert "keuzes" not in reply["text"]
        await ws.close()
        assert TOPIC in hm.router.muted()
        await hm.db.execute("DELETE FROM vesta_said")  # her memory of what she said: not it
        ws, said = await _tap(client)
        assert not _about_battery(said)
        await ws.close()
    await hm.stop()
    hm2, ha2, clock2 = await _restart(tmp_path)
    await _battery_low(hm2, ha2, clock2)
    async with TestClient(TestServer(create_app(hm2, tmp_path, ingress_only=False))) as client:
        ws, said = await _tap(client)
        assert not _about_battery(said)
        await ws.close()
    await hm2.stop()


async def test_klaar_on_the_card_does_not_come_back_while_it_stays_off(tmp_path: Path) -> None:
    """Klaar closed the card, and at the next round the same notice came back, as new. Now it
    stays closed while the battery stays empty."""
    hm, ha, clock = await _house(tmp_path, level=4)
    await _battery_low(hm, ha, clock)
    note = (await hm.notifications())[0]
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=USER)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "notification", "id": note["id"], "action": "dismiss"})
        await _next(ws, "snapshot")
        await ws.close()
    await _round(hm, clock)
    assert not [n for n in await hm.notifications() if n["topic"] == TOPIC]
    await hm.stop()


async def test_later_in_her_window_asks_how_long(tmp_path: Path) -> None:
    """Later in her window asks until when, as the card does; then she no longer names it.
    Later is for an important notice (a notice for information has none, QA 3)."""
    hm, ha, clock = await _house(tmp_path, level=4)
    await _battery_low(hm, ha, clock)
    await hm.db.execute("UPDATE notifications SET kind = 'important' WHERE topic = ?", (TOPIC,))
    note = (await hm.notifications())[0]
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, said = await _tap(client)
        assert _about_battery(said)
        await ws.send_json(
            {"type": "notification", "id": note["id"], "action": "snooze", "via": "vesta"}
        )
        how_long = await _next(ws, "say")
        assert how_long["ask"]["actions"] == ["later_1h", "later_4h", "later_8h", "later_morning"]
        await ws.send_json({"type": "talk", "text": "Over 4 uur"})
        reply = await _next(ws, "say")
        assert "keuzes" not in reply["text"]
        await ws.close()
        assert hm.router.later_of("demo-user-1", TOPIC, clock.now()) == clock.now() + timedelta(
            hours=4
        )
        await hm.db.execute("DELETE FROM vesta_said")
        ws, said = await _tap(client)
        assert not _about_battery(said)
        await ws.close()
    await hm.stop()


async def test_ik_kijk_ernaar_closes_it_until_something_changes(tmp_path: Path) -> None:
    """Ik kijk ernaar closes the notice; while the battery stays empty it does not come back,
    not after a restart either. Once it is full again and then empty again, it is a new
    notice."""
    hm, ha, clock = await _house(tmp_path, level=4)
    await _battery_low(hm, ha, clock)
    note = (await hm.notifications())[0]
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=USER)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "notification", "id": note["id"], "action": "seen"})
        await _next(ws, "snapshot")
        await ws.close()
    await _round(hm, clock)
    assert not [n for n in await hm.notifications() if n["topic"] == TOPIC]
    await hm.stop()
    hm, ha, clock = await _restart(tmp_path)
    await _battery_low(hm, ha, clock)
    assert not [n for n in await hm.notifications() if n["topic"] == TOPIC]
    ha.states[BAT] = hm.world.states[BAT] = _battery(clock, "100")  # a new battery
    await _round(hm, clock)
    ha.states[BAT] = hm.world.states[BAT] = _battery(clock, "8")  # empty again
    await _round(hm, clock)
    assert [n for n in await hm.notifications() if n["topic"] == TOPIC]
    await hm.stop()


async def test_klaar_holds_only_while_the_cause_stays(tmp_path: Path) -> None:
    """Klaar must not hold back a new notice once its cause went away: the fridge that is
    open, closed with Klaar, then shut and later open again, is a new notice."""
    hm, _ha, clock = await _house(tmp_path, level=4)
    hm.activated = True
    assert hm.world is not None
    topic = "house:fridge:kitchen"
    snapshot = hm.world.snapshot(clock.now())
    await hm._raise(Kind.IMPORTANT, topic, "notify.learned", {"what": "x"}, snapshot)
    await hm.close_notice(topic)
    await hm._raise(Kind.IMPORTANT, topic, "notify.learned", {"what": "x"}, snapshot)
    assert topic not in hm._raised  # still open: closed stays closed
    for old in hm._tracked("house:fridge:"):
        await hm._resolve(old)  # shut: the cause went away
    await hm._raise(Kind.IMPORTANT, topic, "notify.learned", {"what": "x"}, snapshot)
    assert topic in hm._raised  # open again: a new notice
    await hm.stop()


def _sent(fake: FakeAI) -> dict[str, Any]:
    """What the AI got last, as data."""
    user = fake.sent[-1][1]
    return dict(json.loads(user[user.index("{") : user.rindex("}") + 1]))


async def test_with_an_ai_the_notice_goes_along_and_your_words_answer_it(tmp_path: Path) -> None:
    """With an AI the notice she mentioned had no buttons (only Vakantie and Gasten), and the AI
    did not know of it: "die wil ik nooit meer horen" was no answer. Now the notice goes along
    as her question, and the AI's reply is its answer."""
    hm, ha, clock = await _house(tmp_path, level=4)
    await _battery_low(hm, ha, clock)
    answer = {"answer": "Goed, die laat ik voortaan weg.", "reply": "mute"}
    fake = FakeAI(["De batterij van de sensor in het tuinhuis is bijna leeg.", answer])
    await enable(hm, fake)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, said = await _tap(client)
        assert said["ask"]["actions"] == ["seen", "mute"]
        await ws.send_json({"type": "talk", "text": "die wil ik nooit meer horen"})
        await _next(ws, "say")
        await ws.close()
    sent = _sent(fake)
    assert sent["conversation"]["asked"]["choices"] == ["seen", "mute"]
    assert sent["house"]["notices"][0]["answers"] == ["seen", "mute"]
    assert TOPIC in hm.router.muted()
    await hm.stop()


async def test_the_ai_gets_the_house_rules_and_your_choices_as_labels(tmp_path: Path) -> None:
    """The AI knew rooms and devices, but not the house rules, nor what you switched off or
    said no to – so it could suggest the very thing you refused. Now it gets them, with labels
    instead of names (E-15)."""
    hm, ha, clock = await _house(tmp_path, level=4)
    await _battery_low(hm, ha, clock)
    await hm._refresh_labels()  # the sensor is new in this house
    rule = parse_rule("nooit de gordijnen in de woonkamer open", "nl", hm.languages, hm.model)
    assert rule is not None
    await hm.save_rule(rule, "demo-user-1")
    note = (await hm.notifications())[0]
    fake = FakeAI([{"answer": "Alles is rustig."}])
    await enable(hm, fake)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=USER)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "notification", "id": note["id"], "action": "mute"})
        await _next(ws, "snapshot")
        await ws.close()
    for i in (1, 2, 3):
        await ha.simulate(f"person.resident_{i}", "not_home")
    await hm.answer_vacation(False, "demo-user-1")  # 'Zijn jullie op vakantie?' – nee
    await hm.ask("Is er nog iets wat ik moet weten?", "nl", "demo-user-1")
    house = _sent(fake)["house"]
    rule_said = house["house_rules"][0]
    assert "raambekleding" in rule_said and "woonkamer" not in rule_said.lower()  # a label
    assert house["your_choices"]["no_vacation"] is True
    assert len(house["your_choices"]["not_shown"]) == 1
    assert "notices" not in house  # the one switched off is not brought up again
    assert "Sensor Tuinhuis" not in json.dumps(house)  # names stay home
    await hm.stop()


async def test_with_an_ai_her_choices_come_beside_the_notices_answers(tmp_path: Path) -> None:
    """When the AI asked back, its choices replaced the notice's own answers. Now both stay:
    the notice's own (for information: no Later), and at most four of the AI's."""
    hm, ha, clock = await _house(tmp_path, level=4)
    await _battery_low(hm, ha, clock)
    back = {
        "answer": "Bedoel je de sensor of de batterij?",
        "choices": ["De sensor", "De batterij"],
    }
    fake = FakeAI(["De batterij van de sensor in het tuinhuis is bijna leeg.", back])
    await enable(hm, fake)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws, _said = await _tap(client)
        await ws.send_json({"type": "talk", "text": "hoe komt dat eigenlijk"})
        reply = await _next(ws, "say")
        await ws.close()
    assert [c["text"] for c in reply["choices"]] == ["De sensor", "De batterij"]
    assert reply["ask"]["actions"] == ["seen", "mute"]
    await hm.stop()
