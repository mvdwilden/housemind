"""Vesta, the voice of the house (E-220 … E-232, chapter 17), from the user's side: what she
says on a tap, per level, in her own words, never twice the same. Each test fails without
Vesta; what it checks is in its docstring."""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.notify import Kind
from housemind.brain.voice import WHOLE, Phrases
from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.ha.adapter import Voice
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome
from housemind.server.web import SESSIONS_KEY, create_app
from housemind.world.house_mode import HouseMode
from tests.helpers import next_message as _next
from tests.test_ai import FakeAI, enable, start
from tests.test_round5_retest import _start

ROOT = Path(__file__).resolve().parents[1]
AFTERNOON = datetime(2026, 10, 19, 17, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))
PHRASES = Phrases.load([ROOT / "knowledge"])
# The facts each kind of line may use (B-79): a placeholder outside these would stay "{…}".
# A device and a room also come in the forms German needs after "von" or "bei" (E-248).
CASES = {"device": {"device_acc", "device_dat"}, "area": {"area_dat"}}
ALLOWED = {
    "outside_alarm": {"area", "what"},
    "appliance_done": {"device"},
    "cooking_away": {"device"},
    "fridge_open": {"device"},
    "open_when_away": {"device"},
    "water_running": {"minutes"},
    "water_valve_closed": {"device", "minutes"},
    "waste": {"what"},
    "battery": {"device"},
    "offline": {"device"},
    "offline_safety": {"device"},
    "filter": {"device"},
    "device_hot": {"device", "temperature"},
    "learned": {"what"},
    "question": {"question"},
    "emergency": {"text"},
    "did": {"text"},
    "considered": {"text"},
    "other": {"text"},
    "calm": {"moment"},
    "calm_light": {"moment"},
    "sleeping": {"moment"},
    "greeting": {"moment"},
    "offer": {"salute"},
    # Vesta listens (E-349): her choices without an AI, and why she asks.
    "offer_choices": {"salute"},
    "listen_choices": set(),
    "ask_choices": {"choices"},
    "ask_why": {"reason", "Reason"},
    "about_me": {"name"},
    # Answers about the house, settings and house rules (E-243 – E-245).
    "know_state_yes": {"thing", "state"},
    "know_state_no": {"thing", "state"},
    "know_state_is": {"thing", "state"},
    "know_state_unknown": {"thing"},
    "know_many": {"things", "state"},
    "know_none": {"state"},
    "know_climate": {"where", "temperature"},
    "know_climate_house": {"temperature"},
    "know_climate_unknown": {"where"},
    "know_humidity": {"where", "humidity"},
    "know_someone_yes": {"where"},
    "know_someone_no": {"where"},
    "know_me_here": {"where"},  # "Waar ben ik?" (20-10-2026)
    "know_me_maybe": {"where"},
    "know_me_unsure": {"rooms"},
    "know_me_none": set(),
    "know_home_count": {"count"},
    "know_did": {"text"},
    "know_energy_use": {"power"},
    "know_energy_sun": {"power"},
    "know_setting_where": {"sheet", "section"},
    "know_setting_spoken": {"sheet", "section"},
    "know_setting_admin": {"sheet", "section"},
    "know_setting_now": {"value"},
    "know_todo_some": {"count"},
    "know_todo_more": {"count"},
    "know_rules_some": {"count"},
    "know_rules_more": {"count"},
    "know_manual": {"text"},
    "know_unsure": {"title"},
    "know_via_ai": {"text"},
    "know_where_help": {"title"},
    "know_ready": {"part", "text"},
    "know_ready_spoken": {"part", "text"},
    "know_ready_all": {"text"},
    "know_ready_all_spoken": {"text"},
    "know_tidy_some": {"count", "text"},  # Home Assistant netjes (B-246)
    "know_tidy_one": {"text"},
    "know_tidy_backup": {"count"},
    "morning": {"text"},
    "bedtime_found": {"text"},
}


MOMENTS = ("morning", "afternoon", "evening", "night")


def rendered(lang: str, topic: str, formal: bool = False) -> set[str]:
    """Her lines as they can sound: with every time of day filled in."""
    out = set()
    for line in PHRASES.variants(lang, topic, formal):
        for part in MOMENTS:
            text = line.replace("{moment}", PHRASES.moment(lang, part))
            out.add(text.replace("{salute}", PHRASES.salute(lang, part)))
    return out


def _forms(lang: str, topic: str) -> list[list[str]]:
    forms = [PHRASES.variants(lang, topic, False)]
    if PHRASES.has_formal(lang):
        forms.append(PHRASES.variants(lang, topic, True))
    return forms


def test_her_own_words_in_every_language_and_form() -> None:
    """Every kind of line has at least five variants in every language, in the je- and the
    u-form – a factual answer ("know_…") at least three – and only uses the facts it gets."""
    topics = [k for k, v in PHRASES.banks["nl"].items() if isinstance(v, dict) and "je" in v]
    assert len(topics) >= 25
    for lang in ("nl", "en", "de", "fr"):
        assert PHRASES.name(lang) == "Vesta"
        for topic in topics:
            for lines in _forms(lang, topic):
                assert len(lines) >= (3 if topic.startswith("know_") else 5), (lang, topic)
                for line in lines:
                    used = set(re.findall(r"{(\w+)}", line))
                    allowed = set(ALLOWED.get(topic, set()))
                    for fact in list(allowed):
                        allowed |= CASES.get(fact, set())
                    assert used <= allowed, (lang, topic, line)
    assert PHRASES.has_formal("nl") and not PHRASES.has_formal("en")


def test_she_speaks_in_whole_flowing_sentences() -> None:
    """In the real house she often answered in one, two or three words ("Rustig."). Every
    line is now at least one whole sentence, and the first sentence – all she says on level
    1 – is never curt."""
    filled = {
        "device": "de wasmachine",
        "area": "de tuin",
        "what": "groente en fruit",
        "minutes": "20",
        "temperature": "72",
        "text": "het licht in de keuken ging net uit",
        "question": "zal ik het licht in de keuken uitdoen",
        "moment": "vanavond",
        "salute": "Goedenavond",
        "name": "Vesta",
        "device_acc": "die Waschmaschine",
        "device_dat": "der Waschmaschine",
        "area_dat": "dem Garten",
        "thing": "de lamp",
        "things": "de lampen in de keuken",
        "state": "aan",
        "where": "in de keuken",
        "rooms": "in de keuken en in de woonkamer",
        "humidity": "55",
        "count": "3",
        "power": "350 W",
        "sheet": "Mijn voorkeuren",
        "section": "Stem",
        "value": "Betrokken",
        "title": "Hoe praat ik terug met Vesta?",
        "part": "Verlichting",
        "reason": "er al een tijdje niemand in de keuken is",
        "Reason": "Er is al een tijdje niemand in de keuken.",
        "choices": "doe het, nu niet of waarom",
    }
    for lang in ("nl", "en", "de", "fr"):
        bank = PHRASES.banks[lang]
        for topic in [k for k, v in bank.items() if isinstance(v, dict) and "je" in v]:
            if topic == "more":
                continue  # a lead-in such as "Oh, and" runs on into the next line
            for lines in _forms(lang, topic):
                for line in lines:
                    text = line.format(**filled)
                    first = re.split(r"(?<=[.!?])\s+", text)[0]
                    assert len(text.split()) >= 4, (lang, topic, line)
                    whole = topic in WHOLE or "{text}" in line or "{question}" in line
                    if not whole:  # never cut to its first sentence: that one may be short
                        assert len(first.split()) >= 4, (lang, topic, line)


async def _house(tmp_path: Path, level: int = 2, formal: bool = False) -> tuple:
    hm, ha, clock = await _start(tmp_path)
    assert hm.world is not None
    hm.world.modes.mode = HouseMode.HOME  # awake (the demo family sleeps at 21:00)
    prefs = {"address": "u"} if formal else {}
    await hm.db.execute(
        "UPDATE people SET level = ?, preferences = ? WHERE user_id = ?",
        (level, json.dumps(prefs), "demo-user-1"),
    )
    hm._vesta_rng.seed(7)
    return hm, ha, clock


async def test_a_tap_on_a_calm_house_never_says_the_same_twice(tmp_path: Path) -> None:
    """A tap said the same fixed sentence every time ("All is calm"); now Vesta says it in
    her own words, never the same line twice in a row, in the u-form for someone who wants
    that, now and then with a dry joke."""
    hm, _ha, _clock = await _house(tmp_path, formal=True)
    lines = [(await hm.vesta_line("demo-user-1", 2, True, "nl")).text for _ in range(12)]
    calm = rendered("nl", "calm", True) | rendered("nl", "calm_light", True)
    assert all(line in calm for line in lines)
    assert all(a != b for a, b in itertools.pairwise(lines))
    assert len(set(lines)) >= 5
    assert any(line in rendered("nl", "calm_light", True) for line in lines)
    assert not any(" je " in f" {line} " for line in lines)  # u, not je
    await hm.stop()


async def _raise(hm, kind: Kind, topic: str, key: str, params: dict, concerns: str = "home"):
    snap = hm.world.snapshot(hm.clock.now())
    await hm._raise(kind, topic, key, params, snap, concerns=concerns)


async def test_what_matters_per_level(tmp_path: Path) -> None:
    """Level 1 heard nothing different from level 4. Now: level 1 only what asks for you now,
    in one sentence; level 2 also what is good to know; she never reads the message out."""
    hm, _ha, _clock = await _house(tmp_path)
    await _raise(hm, Kind.IMPORTANT, "open:door", "notify.open_when_away", {"device": "Achterdeur"})
    await _raise(
        hm, Kind.INFO, "done:washer", "notify.house.appliance_done", {"device": "Wasmachine"}
    )
    one = (await hm.vesta_line("demo-user-1", 1, True, "nl")).text
    assert "Achterdeur" in one and "Wasmachine" not in one
    assert one.count(".") + one.count("?") <= 1  # one sentence
    two = (await hm.vesta_line("demo-user-1", 2, True, "nl")).text
    assert "Achterdeur" in two and "Wasmachine" in two
    for topic, device in (("open_when_away", "Achterdeur"), ("appliance_done", "Wasmachine")):
        mine = [v.format(device=device) for v in PHRASES.variants("nl", topic, False)]
        assert any(m in two for m in mine)  # her own words, not the message read out
    again = (await hm.vesta_line("demo-user-1", 2, True, "nl")).text
    assert "Achterdeur" in again and "Wasmachine" not in again  # told is told; open stays
    await hm.stop()


async def test_an_emergency_always_and_alone(tmp_path: Path) -> None:
    """An emergency comes first, alone, every time – never a joke, never left out."""
    hm, _ha, _clock = await _house(tmp_path)
    await _raise(
        hm, Kind.INFO, "done:washer", "notify.house.appliance_done", {"device": "Wasmachine"}
    )
    await _raise(
        hm,
        Kind.EMERGENCY,
        "emergency:smoke",
        "notify.emergency_smoke",
        {"area": "keuken", "done": ""},
    )
    for _ in range(3):
        line = (await hm.vesta_line("demo-user-1", 4, True, "nl")).text
        assert "Rook gemeten in de keuken" in line and "Wasmachine" not in line
    await hm.stop()


async def test_messages_for_administrators_stay_with_them(tmp_path: Path) -> None:
    """A message for administrators reached everyone through the Orb."""
    hm, _ha, _clock = await _house(tmp_path)
    await _raise(
        hm,
        Kind.IMPORTANT,
        "battery:x",
        "notify.maintenance.battery",
        {"device": "Rookmelder"},
        concerns="admins",
    )
    assert "Rookmelder" not in (await hm.vesta_line("demo-user-1", 2, False, "nl")).text
    assert "Rookmelder" in (await hm.vesta_line("demo-user-1", 2, True, "nl")).text
    await hm.stop()


async def _ask(hm, skill: str, entity: str, area: str, action: str) -> int:
    request = ActionRequest(
        Layer.REFLEX, entity, action, {}, skill=skill, area_id=area, created_at=hm.clock.now()
    )
    decision = Decision(request, Outcome.PROPOSED, None, None, (), hm.clock.now())
    proposal = await hm.proposals.add(decision)
    assert proposal is not None
    return proposal


async def test_questions_from_level_3_a_few_a_day_never_the_same_kind(tmp_path: Path) -> None:
    """Level 3 hears a question that fits; at most three a day, never the same kind twice in
    a row; level 2 hears none."""
    hm, _ha, clock = await _house(tmp_path, level=3)
    await _ask(hm, "light_off_leave", "light.living_reading", "living", "light.turn_off")
    await _ask(hm, "light_off_leave", "light.kitchen", "kitchen", "light.turn_off")
    await _ask(hm, "covers_close_dark", "cover.living_curtain", "living", "cover.close_cover")
    await _ask(hm, "light_on_entry", "light.hall", "hall", "light.turn_on")
    await _ask(hm, "light_on_entry", "light.toilet", "toilet", "light.turn_on")
    assert (await hm.vesta_line("demo-user-1", 2, True, "nl")).question is None
    asked: list[int] = []
    kinds: list[str] = []
    for _ in range(6):
        line = await hm.vesta_line("demo-user-1", 3, True, "nl")
        if line.question is not None:
            asked.append(line.question)
            row = await hm.db.fetchone("SELECT skill FROM proposals WHERE id = ?", (line.question,))
            kinds.append(str(row["skill"]))
        await clock.advance(60)
    assert len(asked) == 3 and len(set(asked)) == 3  # three a day, each once
    assert all(a != b for a, b in itertools.pairwise(kinds))
    await clock.advance(int(timedelta(days=1).total_seconds()))
    await _ask(hm, "light_off_leave", "light.bathroom", "bathroom", "light.turn_off")
    assert (await hm.vesta_line("demo-user-1", 3, True, "nl")).question is not None  # a new day
    await hm.stop()


# -- her words from an AI (E-222, B-79) --------------------------------------------------


async def _with_ai(tmp_path: Path, replies: list) -> tuple:
    hm, _clock, _ha = await start(tmp_path)  # a household with names that must stay home
    fake = FakeAI(replies)
    await enable(hm, fake)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-9", "Resident 9", 2, json.dumps({}), hm.clock.now().isoformat()),
    )
    hm._vesta_rng.seed(3)
    return hm, fake


async def test_an_ai_makes_her_words_more_human_but_learns_no_names(tmp_path: Path) -> None:
    """With an AI on, her line comes from the AI – which gets the chosen message with labels,
    never a name of a person, room or device; for the listener the label becomes the plain
    name (E-248): "de leeslamp in de woonkamer", not "Leeslamp van Emma"."""
    hm, fake = await _with_ai(tmp_path, [])
    await _raise(
        hm, Kind.INFO, "done:x", "notify.house.appliance_done", {"device": "Leeslamp van Emma"}
    )
    label = hm.ai.labels.pseudonymise("Leeslamp van Emma")
    assert "Emma" not in label
    fake.replies.append({"text": f"Klaar: {label}. Geen haast."})
    line = await hm.vesta_line("demo-user-9", 2, False, "nl")
    system, user, _model = fake.sent[-1]
    assert "Vesta" in system and label in user and "Emma" not in user
    assert line.text == "Klaar: de leeslamp in de woonkamer. Geen haast."
    await hm.stop()


async def test_an_ai_that_makes_things_up_or_is_slow_is_not_heard(
    tmp_path: Path, monkeypatch
) -> None:
    """An AI that names something that was not in the message, or answers too late, is not
    used: Vesta says it in her own words. In an emergency the AI is not asked at all."""
    import housemind.parts.vesta as runtime

    class Slow(FakeAI):
        async def complete(self, system: str, user: str, model: str, max_tokens: int):
            await asyncio.sleep(1)
            return await super().complete(system, user, model, max_tokens)

    hm, _fake = await _with_ai(tmp_path, [{"text": "Ik heb climate_7 voor je opgewarmd."}])
    await _raise(hm, Kind.INFO, "done:w", "notify.house.appliance_done", {"device": "Wasmachine"})
    own = [v.format(device="Wasmachine") for v in PHRASES.variants("nl", "appliance_done", False)]
    line = (await hm.vesta_line("demo-user-9", 2, False, "nl")).text
    assert "climate_7" not in line and any(o in line for o in own)
    slow = Slow([{"text": "Je was is klaar!"}])
    hm.ai.provider = slow
    monkeypatch.setattr(runtime, "VESTA_AI_WAIT", 0.05)
    await _raise(hm, Kind.INFO, "done:d", "notify.house.appliance_done", {"device": "Droger"})
    own = [v.format(device="Droger") for v in PHRASES.variants("nl", "appliance_done", False)]
    line = (await hm.vesta_line("demo-user-9", 2, False, "nl")).text
    assert line != "Je was is klaar!" and any(o in line for o in own)
    before = len(slow.sent)
    await _raise(
        hm, Kind.EMERGENCY, "emergency:x", "notify.emergency_gas", {"area": "keuken", "done": ""}
    )
    line = (await hm.vesta_line("demo-user-9", 2, False, "nl")).text
    assert "Gas gemeten" in line and len(slow.sent) == before
    await hm.stop()


# -- her voice in the app and on speakers (E-223, E-230, B-82) ---------------------------


async def test_a_tap_brings_her_voice_to_this_device_in_the_chosen_voice(tmp_path: Path) -> None:
    """A tap only showed words. Now her voice follows on the device that tapped – in the voice
    of HouseMind's Assist pipeline, or the one an administrator chose – also for speakers."""
    hm, ha, _clock = await _house(tmp_path)
    ha.voice = Voice("tts.cloud", "nl-NL", "Colette")
    hm.speech.voice = await hm.vesta_voice()
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "orb_tap"})
        said = await _next(ws, "say")
        voice = await _next(ws, "voice")
        assert said["voice"] == "Vesta" and voice["audio"].startswith("/api/tts_proxy/")
        assert ha.sounds[-1] == (said["text"], Voice("tts.cloud", "nl-NL", "Colette"))
        assert await hm.set_house_setting(
            "vesta_voice", {"engine": "tts.local", "language": "nl-NL", "voice": "calm"}
        )
        assert await hm.set_house_setting("vesta_name", "Juno")
        await ws.send_json({"type": "orb_tap"})
        said = await _next(ws, "say")
        await _next(ws, "voice")
        assert said["voice"] == "Juno"
        assert ha.sounds[-1][1] == Voice("tts.local", "nl-NL", "calm")
        assert hm.speech.voice == Voice("tts.local", "nl-NL", "calm")  # speakers too
        await ws.close()
    await hm.stop()


# -- the greeting when someone comes home (E-224, B-81) ----------------------------------


async def _home_again(tmp_path: Path, level: int = 1) -> tuple:
    clock = SimulatedClock(AFTERNOON.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    satellite = Entity("assist_satellite.hall", "r-sat", "Hal satelliet", area_id="hall")
    ha.model.entities[satellite.entity_id] = satellite
    ha.states[satellite.entity_id] = State(satellite.entity_id, "idle", {}, clock.now())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", level, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    await ha.simulate("person.resident_1", "not_home")
    await clock.advance(600)
    await ha.simulate("person.resident_1", "home", {"user_id": "demo-user-1"})
    return hm, ha, clock


async def test_a_greeting_where_you_come_in_also_on_level_1(tmp_path: Path) -> None:
    """Coming home was silent. Now Vesta greets in the first room HouseMind sees you – on
    level 1 too, where she otherwise never speaks by herself – and it does not count for the
    day's spoken budget."""
    hm, ha, clock = await _home_again(tmp_path, level=1)
    await clock.advance(20)
    await ha.simulate("binary_sensor.hall_motion", "on")
    await hm.settled()  # her greeting goes beside the queue (M-31)
    spoken = [m for target, m in ha.spoken if target == "assist_satellite.hall"]
    assert len(spoken) == 1
    assert any(spoken[0].startswith(g) for g in rendered("nl", "greeting"))
    await ha.simulate("binary_sensor.hall_motion", "off")
    await ha.simulate("binary_sensor.hall_motion", "on")
    await hm.settled()
    assert len([1 for target, _ in ha.spoken if target == "assist_satellite.hall"]) == 1  # once
    allowed = await hm.speech.allowed("hall", [2], False, clock.now(), clock.now())
    assert allowed  # the greeting did not use the day's budget
    await hm.stop()


async def test_no_greeting_without_a_speaker_in_that_room_or_while_asleep(tmp_path: Path) -> None:
    """She greets only in the first room where you are seen, and only with a speaker there;
    never while the house sleeps."""
    hm, ha, clock = await _home_again(tmp_path, level=2)
    await clock.advance(20)
    await ha.simulate("binary_sensor.kitchen_motion", "on")  # first seen in the kitchen
    await ha.simulate("binary_sensor.hall_motion", "on")
    await hm.settled()
    assert not [1 for target, _ in ha.spoken if target == "assist_satellite.hall"]
    await hm.stop()
    hm, ha, clock = await _home_again(tmp_path / "night", level=2)
    assert hm.world is not None
    hm.world.modes.set_manual(HouseMode.SLEEP, clock.now())  # the house went to sleep
    await clock.advance(20)
    await ha.simulate("binary_sensor.hall_motion", "on")
    await hm.settled()
    assert not [1 for target, _ in ha.spoken if target == "assist_satellite.hall"]
    await hm.stop()


# -- talking back (E-225, E-226, B-83) ----------------------------------------------------


async def test_after_a_tap_she_listens_and_answers_what_she_heard(tmp_path: Path) -> None:
    """A tap could only be answered by typing. Now, on a screen with a microphone, she ends
    with an invitation, listens, sends the sound to HA's speech recognition and answers what
    she heard – aloud, on that device; the sound is not kept. Without a microphone she does
    not invite and does not listen."""
    hm, ha, _clock = await _house(tmp_path)
    ha.voice = Voice("tts.cloud", "nl-NL", "Colette")
    hm.speech.voice = await hm.vesta_voice()
    ha.heard = ["Is de achterdeur dicht?"]
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": True})
        await ws.send_json({"type": "orb_tap"})
        said = await _next(ws, "say")
        # Without an AI her choices, never an open question (E-349).
        assert said["listen"] is True and said["text"] in rendered("nl", "offer_choices")
        await ws.send_json({"type": "listen_start"})
        await ws.send_bytes(b"\x01\x00" * 16000)  # one second of sound
        await ws.send_json({"type": "listen_end"})
        answer = await _next(ws, "say")
        assert answer["heard"] == "Is de achterdeur dicht?" and answer["voice"] == "Vesta"
        await _next(ws, "voice")
        assert ha.listened == [32000]
        session = next(iter(client.app[SESSIONS_KEY]))
        assert not session.audio  # nothing kept
        ha.heard = []
        await ws.send_json({"type": "orb_tap"})
        await _next(ws, "say")
        await ws.send_json({"type": "listen_start"})
        await ws.send_bytes(b"\x01\x00" * 8000)
        await ws.send_json({"type": "listen_end"})
        missed = await _next(ws, "say")
        assert missed["text"] in PHRASES.variants("nl", "not_heard", False)
        quiet = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await quiet.send_json({"type": "hello", "browserLanguage": "nl"})  # no microphone
        await quiet.send_json({"type": "orb_tap"})
        assert (await _next(quiet, "say"))["listen"] is False
        await quiet.close()
        await ws.close()
    await hm.stop()


async def test_a_spoken_yes_answers_her_question(tmp_path: Path) -> None:
    """She asked a question and listened, but "ja" went nowhere. Now a spoken yes answers the
    proposal she asked about, and she confirms in her own words."""
    hm, ha, _clock = await _house(tmp_path, level=3)
    proposal = await _ask(hm, "light_off_leave", "light.living_reading", "living", "light.turn_off")
    ha.heard = ["Ja, doe maar"]
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": True})
        await ws.send_json({"type": "orb_tap"})
        said = await _next(ws, "say")
        assert said["listen"] is True and "?" in said["text"]
        await ws.send_json({"type": "listen_start"})
        await ws.send_bytes(b"\x01\x00" * 16000)
        await ws.send_json({"type": "listen_end"})
        reply = await _next(ws, "say")
        assert reply["text"] in PHRASES.variants("nl", "answer_yes", False)
        row = await hm.db.fetchone("SELECT status FROM proposals WHERE id = ?", (proposal,))
        assert row["status"] != "open"
        await ws.close()
    await hm.stop()


# -- what she says by herself (B-80), on open phones (E-223), Assist (E-220) -------------


async def test_what_asks_for_you_now_in_her_words_in_the_room_and_on_open_phones(
    tmp_path: Path,
) -> None:
    """By herself HouseMind read fixed sentences, and never told the open app anything. Now
    something that asks for you now is said once, in her words, in an occupied room with a
    speaker; whoever was not there hears it on their open app. An emergency comes in her
    words around the fixed safety sentence, on every speaker and every open app."""
    satellite = Entity("assist_satellite.kitchen", "r-sat", "Keuken satelliet", area_id="kitchen")
    hm, ha, clock = await _start(tmp_path, [(satellite, "idle")])
    assert hm.world is not None
    hm.world.modes.mode = HouseMode.HOME
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-2", "Resident 2", 2, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    hm._vesta_rng.seed(5)
    phones: list[tuple[str, str]] = []

    async def phone(user: str, text: str, audio: str | None, name: str) -> None:
        phones.append((user, text))

    hm.on_vesta_person(phone)
    for person in hm._people:
        person.home = person.user_id == "demo-user-1"  # resident 2 is out
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    snap = hm.world.snapshot(clock.now())
    await hm._raise(
        Kind.IMPORTANT, "house:fridge:x", "notify.house.fridge_open", {"device": "Koelkast"}, snap
    )
    said = [m for target, m in ha.spoken if target == "assist_satellite.kitchen"]
    mine = [v.format(device="Koelkast") for v in PHRASES.variants("nl", "fridge_open", False)]
    assert len(said) == 1 and said[0] in mine
    assert [u for u, _ in phones] == ["demo-user-2"]  # not for whoever heard it in the room
    await hm._raise(
        Kind.IMPORTANT, "house:fridge:x", "notify.house.fridge_open", {"device": "Koelkast"}, snap
    )
    assert len([1 for t, _ in ha.spoken if t == "assist_satellite.kitchen"]) == 1  # once
    await ha.simulate("binary_sensor.kitchen_smoke", "on")
    alarm = [m for target, m in ha.spoken if target == "assist_satellite.kitchen"][-1]
    frames = [v.split("{text}")[0].strip() for v in PHRASES.variants("nl", "emergency", False)]
    assert "Rook gemeten in de" in alarm and any(alarm.startswith(f) for f in frames)
    assert {u for u, text in phones if "Rook gemeten" in text} == {"demo-user-1", "demo-user-2"}
    await hm.stop()


async def test_what_the_house_switched_off_she_does_not_say_by_herself(tmp_path: Path) -> None:
    """Niet meer tonen kept a message out of the log and off the phones, but Vesta still said
    a new important one by herself, in the room and on the open apps. Now she leaves what the
    house switched off alone, and does not put on someone's phone what they put off with
    Later."""
    satellite = Entity("assist_satellite.kitchen", "r-sat", "Keuken satelliet", area_id="kitchen")
    hm, ha, clock = await _start(tmp_path, [(satellite, "idle")])
    assert hm.world is not None and hm.router is not None
    hm.world.modes.mode = HouseMode.HOME
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-2", "Resident 2", 2, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    phones: list[tuple[str, str]] = []

    async def phone(user: str, text: str, audio: str | None, name: str) -> None:
        phones.append((user, text))

    hm.on_vesta_person(phone)
    for person in hm._people:
        person.home = person.user_id == "demo-user-1"
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    snap = hm.world.snapshot(clock.now())
    await hm.router.mute("house:fridge:x", {"device": "Koelkast"}, clock.now())
    await hm._raise(
        Kind.IMPORTANT, "house:fridge:x", "notify.house.fridge_open", {"device": "Koelkast"}, snap
    )
    assert not [1 for t, _ in ha.spoken if t == "assist_satellite.kitchen"]
    assert phones == []
    # Put off with Later by one person: the room still hears it, their phone does not.
    await hm.router.later(
        "demo-user-2", "house:freezer:x", clock.now() + timedelta(hours=4), clock.now()
    )
    for person in hm._people:
        person.home = False
    await hm._raise(
        Kind.IMPORTANT, "house:freezer:x", "notify.house.fridge_open", {"device": "Vriezer"}, snap
    )
    assert [u for u, _ in phones] == ["demo-user-1"]
    await hm.stop()


async def test_how_are_things_is_answered_by_vesta_also_through_assist(tmp_path: Path) -> None:
    """ "How are things?" got a fixed status line, also in Assist; now Vesta answers in her
    own words – and with an AI on, the AI speaks as Vesta."""
    hm, fake = await _with_ai(tmp_path, [{"answer": "Alles is rustig.", "about_house": True}])
    reply = await hm.assist("hoe gaat het", "nl", "c1", "demo-user-9")
    quiet = rendered("nl", "calm") | rendered("nl", "calm_light") | rendered("nl", "sleeping")
    assert reply["text"] in quiet
    await hm.ask("Is het warm genoeg in huis?", "nl", "demo-user-9")
    assert "You are Vesta" in fake.sent[-1][0]
    await hm.stop()


async def test_stop_throws_it_away_and_a_failing_microphone_stops_the_invitation(
    tmp_path: Path,
) -> None:
    """In the Vesta window (B-85): Stop throws what was said away – nothing goes to speech
    recognition; when the microphone does not work she stops inviting on that screen, and
    after "Try again" she invites again."""
    hm, ha, _clock = await _house(tmp_path)
    ha.heard = ["Iets"]
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": True})
        await ws.send_json({"type": "orb_tap"})
        assert (await _next(ws, "say"))["listen"] is True
        await ws.send_json({"type": "listen_start"})
        await ws.send_bytes(b"\x01\x00" * 16000)
        await ws.send_json({"type": "listen_cancel"})
        await ws.send_json({"type": "listen_end"})  # too late: nothing to hear any more
        await ws.send_json({"type": "mic", "ok": False})
        await ws.send_json({"type": "orb_tap"})
        assert (await _next(ws, "say"))["listen"] is False
        assert ha.listened == []  # nothing went to speech recognition
        await ws.send_json({"type": "mic", "ok": True})
        await ws.send_json({"type": "orb_tap"})
        assert (await _next(ws, "say"))["listen"] is True
        await ws.close()
    await hm.stop()


async def test_a_tap_first_asks_what_she_can_do_like_a_butler(tmp_path: Path) -> None:
    """In the real house a tap first told how the house was, before asking if you wanted
    anything. Now, where she can listen, she first asks what she can do; only something that
    asks for you now comes before it, once; without a microphone she says what matters."""
    hm, _ha, _clock = await _house(tmp_path)
    offer = await hm.vesta_line("demo-user-1", 2, True, "nl", listen=True, offer=True)
    assert offer.text in rendered("nl", "offer") and offer.listens
    await _raise(hm, Kind.IMPORTANT, "fridge:x", "notify.house.fridge_open", {"device": "Koelkast"})
    first = await hm.vesta_line("demo-user-1", 2, True, "nl", listen=True, offer=True)
    invitations = PHRASES.variants("nl", "listen", False)
    assert "Koelkast" in first.text and any(first.text.endswith(i) for i in invitations)
    again = await hm.vesta_line("demo-user-1", 2, True, "nl", listen=True, offer=True)
    # Since E-284 (20-10-2026) a tap fits her colour: while the fridge keeps her orange,
    # every tap names it again.
    assert "Koelkast" in again.text
    status = await hm.vesta_line("demo-user-1", 2, True, "nl")  # no microphone here
    assert status.text not in rendered("nl", "offer")
    await hm.stop()


async def test_she_keeps_listening_after_her_answer_until_silence(tmp_path: Path) -> None:
    """After her answer the conversation ended; now she listens again, so you can keep
    talking. After "I didn't catch that" she listens once more, then the conversation ends."""
    hm, ha, _clock = await _house(tmp_path)
    ha.heard = ["Is de achterdeur dicht?", "Hoe gaat het?"]
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl", "mic": True})
        await ws.send_json({"type": "orb_tap"})
        await _next(ws, "say")
        for question in ("Is de achterdeur dicht?", "Hoe gaat het?"):
            await ws.send_json({"type": "listen_start"})
            await ws.send_bytes(b"\x01\x00" * 16000)
            await ws.send_json({"type": "listen_end"})
            answer = await _next(ws, "say")
            assert answer["heard"] == question and "listen" not in answer  # a tap again (E-464)
        missed = []
        for _ in range(2):
            await ws.send_json({"type": "listen_start"})
            await ws.send_bytes(b"\x01\x00" * 16000)
            await ws.send_json({"type": "listen_end"})
            missed.append((await _next(ws, "say"))["listen"])
        assert missed == [True, False]  # once more, then the conversation ends
        await ws.close()
    await hm.stop()


async def test_she_knows_her_own_name_in_every_language(tmp_path: Path) -> None:
    """Asked "Hoe heet je?" in the real house, she did not know. Now she says who she is, in
    her own words and in every language, in the app and through Assist – with the name the
    administrator gave her."""
    hm, _ha, _clock = await _house(tmp_path)
    questions = {
        "nl": "Hoe heet je?",
        "en": "What's your name?",
        "de": "Wie heißt du?",
        "fr": "Comment tu t'appelles ?",
    }
    for lang, question in questions.items():
        reply = await hm.talk(question, lang, 2, "demo-user-1", "")
        lines = PHRASES.variants(lang, "about_me", False)
        assert reply["text"] in {x.replace("{name}", "Vesta") for x in lines}
    reply = await hm.assist("wie ben jij", "nl", "c1", "demo-user-1")
    assert "Vesta" in reply["text"]
    assert await hm.set_house_setting("vesta_name", "Juno")
    reply = await hm.talk("Wat is je naam?", "nl", 4, "demo-user-1", "")
    assert "Juno" in reply["text"] and "Vesta" not in reply["text"]
    await hm.stop()
