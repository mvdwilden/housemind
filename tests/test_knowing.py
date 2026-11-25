"""What Vesta knows herself (E-243 – E-245, B-90 – B-92), from the user's side: questions
about the house, the settings, what waits for you and the house rules, answered without an AI,
in her own words and plain names. Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.names import sentence_case
from housemind.brain.notify import Kind
from housemind.server.web import create_app
from tests.helpers import next_message as _next
from tests.test_names import LEXICONS
from tests.test_vesta import PHRASES, _house, _raise


def _said(topic: str, formal: bool = False, lang: str = "nl", **facts: Any) -> set[str]:
    """Every way she can say this answer."""
    return {
        sentence_case(line.format(**{k: str(v) for k, v in facts.items()}))
        for line in PHRASES.variants(lang, topic, formal)
    }


async def test_she_knows_whether_something_is_on_or_closed(tmp_path: Path) -> None:
    """Without an AI she said "Dat weet ik niet". Now: "Is de lamp in de keuken aan?" gets
    "Ja, de lamp in de keuken is aan."; a wrong guess a "Nee, …"; also by its name in HA."""
    hm, ha, _clock = await _house(tmp_path)
    await ha.simulate("light.kitchen", "on")
    await ha.simulate("binary_sensor.front_door", "off")
    reply = await hm.talk("Is de lamp in de keuken aan?", "nl", 2, "demo-user-1")
    assert reply["text"] in _said("know_state_yes", thing="de lamp in de keuken", state="aan")
    reply = await hm.talk("Staat de deur in de gang open?", "nl", 2, "demo-user-1")
    assert reply["text"] in _said("know_state_no", thing="de deur in de gang", state="dicht")
    reply = await hm.talk("Is Keuken lamp uit?", "nl", 2, "demo-user-1")
    assert reply["text"] in _said("know_state_no", thing="de lamp in de keuken", state="aan")
    await hm.stop()


async def test_which_are_still_on_per_kind_and_room(tmp_path: Path) -> None:
    """ "Welke lampen zijn nog aan?" lists them plainly, per room; none on: she says so."""
    hm, ha, _clock = await _house(tmp_path)
    for light in [e for e in hm.model.entities if e.startswith("light.")]:
        await ha.simulate(light, "off")
    reply = await hm.talk("Welke lampen zijn nog aan?", "nl", 2, "demo-user-1")
    assert reply["text"] in _said("know_none", state="aan")
    await ha.simulate("light.kitchen", "on")
    await ha.simulate("light.hall", "on")
    reply = await hm.talk("Welke lampen zijn nog aan?", "nl", 2, "demo-user-1")
    things = hm.names.group(["light.kitchen", "light.hall"], "nl")
    assert things == "de lampen in de keuken en de gang"
    assert reply["text"] in _said("know_many", things=things, state="aan")
    await hm.stop()


async def test_warmth_presence_and_who_is_home_without_names(tmp_path: Path) -> None:
    """How warm a room is, whether someone is there, and how many are home – never who."""
    hm, _ha, _clock = await _house(tmp_path)
    reply = await hm.talk("Hoe warm is het in de slaapkamer?", "nl", 2, "demo-user-1")
    assert "in de slaapkamer" in reply["text"].lower() and "°C" in reply["text"]
    reply = await hm.talk("Is er iemand in de keuken?", "nl", 2, "demo-user-1")
    where = "in de keuken"
    assert reply["text"] in _said("know_someone_yes", where=where) | _said(
        "know_someone_no", where=where
    )
    reply = await hm.talk("Wie is er thuis?", "nl", 2, "demo-user-1")
    count = hm.world.snapshot(hm.clock.now()).residents_home
    assert reply["text"] in _said("know_home_count", count=count) | _said("know_home_one") | _said(
        "know_home_none"
    )
    for person in hm._people:
        assert person.name not in reply["text"]
    await hm.stop()


async def test_where_a_setting_is_and_she_opens_it_after_a_yes(tmp_path: Path) -> None:
    """ "Hoe zet ik je stem uit?": where it is, and "Zal ik het voor je openen?"; a yes opens
    that part of the screen – she changes nothing herself. The house settings are for an
    administrator; through Assist (no screen) she only says where."""
    hm, _ha, _clock = await _house(tmp_path)
    reply = await hm.talk("Hoe zet ik je stem uit?", "nl", 2, "demo-user-1")
    assert reply["open"] == {"sheet": "preferences", "section": "voice"}
    assert reply["text"] in _said("know_setting_where", sheet="Mijn voorkeuren", section="Stem")
    level = await hm.talk("Hoe verander ik mijn niveau?", "nl", 2, "demo-user-1")
    assert level["text"].startswith(tuple(_said("know_setting_now", value="Geïnformeerd")))
    resident = await hm.knowing.answer(
        "Waar kan ik de AI instellen?", "nl", 2, "demo-user-1", False
    )
    assert resident is not None
    assert "open" not in resident
    assert resident["text"] in _said(
        "know_setting_admin", sheet="Huisinstellingen", section="AI (optioneel)"
    )
    spoken = await hm.talk("Hoe zet ik je stem uit?", "nl", 2, "demo-user-1", channel="assist")
    assert "open" not in spoken and "in de app" in spoken["text"]
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "talk", "text": "Hoe zet ik je stem uit?"})
        said = await _next(ws, "say")
        assert said["offer"] == {"sheet": "preferences", "section": "voice"}
        await ws.send_json({"type": "talk", "text": "ja"})
        opened = await _next(ws, "open")
        assert opened["sheet"] == "preferences" and opened["section"] == "voice"
        await ws.close()
    await hm.stop()


async def test_what_waits_for_you_and_the_house_rules(tmp_path: Path) -> None:
    """ "Moet ik nog iets doen?" names what waits, in her words and plain names; "Welke
    huisregels zijn er?" counts them and reads them; nothing there: she says so."""
    hm, _ha, _clock = await _house(tmp_path, level=3)
    nothing = await hm.talk("Moet ik nog iets doen?", "nl", 3, "demo-user-1")
    assert nothing["text"] in _said("know_todo_none")
    await _raise(
        hm, Kind.IMPORTANT, "battery:x", "notify.maintenance.battery", {"device": "Gang rookmelder"}
    )
    one = await hm.talk("Moet ik nog iets doen?", "nl", 3, "demo-user-1")
    assert one["text"].startswith(tuple(_said("know_todo_one")))
    assert "de rookmelder in de gang" in one["text"].lower()
    none = await hm.talk("Welke huisregels zijn er?", "nl", 3, "demo-user-1")
    assert none["text"] in _said("know_rules_none")
    rule = await hm.understand_rule_text("nooit het licht in de badkamer aan", "nl")
    await hm.save_rule(rule["rule"], "demo-user-1", [])
    some = await hm.talk("Welke huisregels zijn er?", "nl", 3, "demo-user-1")
    assert some["text"].startswith(tuple(_said("know_rules_one")))
    await hm.stop()


def test_states_agree_in_french_and_stay_plain_elsewhere() -> None:
    """ "La porte est fermée", "les volets sont fermés"; Dutch and German say it once."""
    assert LEXICONS["fr"].state("closed", "f") == "fermée"
    assert LEXICONS["fr"].state("on", "m", plural=True) == "allumés"
    assert LEXICONS["nl"].state("locked") == "op slot"
    assert LEXICONS["de"].state("open") == "offen"
