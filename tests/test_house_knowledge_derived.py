"""Je huis begrijpen, deel 2 (E-203, E-207, E-414, design 50.2, B-358 – B-361), from the user's
side: what HouseMind derives from behaviour without AI, where the rules doubt and an
administrator decides, Vesta's one question a day on Geïnformeerd, and Waarom? saying where
the knowledge came from. The administrator's choices hold: AI never takes the real decisions
and the core never waits for it or for reading the history (E-414); what nobody confirmed is
watched and learned, never proposed or done (50.8). Each test fails without the build; what
it checks is its docstring."""

from __future__ import annotations

import asyncio
import json
import time as timer
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, State
from housemind.demo.houses import load_demo_house
from housemind.ha.adapter import HAUser
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.knowledge.matcher import recognise
from housemind.parts.thinking import MODEL_SETTLE
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome
from housemind.server import views
from housemind.server.web import create_app
from tests.knowledge_page import confirmed, derived, rejected, to_confirm
from tests.test_ai import FakeAI, enable
from tests.test_house_knowledge import PLUG, LaterAI, _page, _plug, _ws
from tests.test_screens import _next
from tests.test_speed import START, _stopwatch

ROOT = Path(__file__).resolve().parents[1]
RELAY = "switch.hall_relay"
LDR = "sensor.living_ldr"
WARM, COOL = "sensor.living_warm", "sensor.living_cool"
CONTACT = "binary_sensor.garage_relay_contact"
KITCHEN, HALL = "binary_sensor.kitchen_contact", "binary_sensor.hall_contact"
LED = "light.bedroom_sensor_led"


async def _start(
    tmp_path: Path, level: int = 3, setup: Any = None
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    """The family home with its administrator; ``setup`` adds to the house before the start.
    Learning is allowed: reading the history waits for that yes (E-257)."""
    clock = SimulatedClock(START.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    ha.users_list[0] = HAUser("demo-user-1", "Emma", is_admin=True)
    if setup is not None:
        await setup(ha, clock)
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-1", "Emma", level, clock.now().isoformat()),
    )
    await hm._refresh_people()
    await hm.set_house_setting("consent_learning", True)
    if hm._history_task is not None:
        # The reading of the history that yes starts, done before the test's own last week:
        # else it may end in the middle of a test and teach the hall's usual lamp from it.
        await hm._history_task
    return hm, clock, ha


async def _add(
    ha: FakeHomeAssistant, clock: SimulatedClock, entity: Entity, state: str = "off"
) -> None:
    await ha.add_entity(entity, State(entity.entity_id, state, {}, clock.now()))


async def _derive(hm: HouseMind) -> None:
    """The quarter-hour look, and what it started beside the core."""
    await hm.tick()
    await hm.settled()


def _ran_like_a_washing_machine(ha: FakeHomeAssistant, now: datetime) -> None:
    """Three times last week the plug's power ran about two hours, heating at about 2 kW."""
    for days in (6, 4, 2):
        begin = now - timedelta(days=days) + timedelta(hours=10)
        for minutes, watts in ((0, "2050"), (25, "180"), (80, "2100"), (110, "2")):
            ha.record("sensor.hall_plug_power", watts, begin + timedelta(minutes=minutes))


async def _relay(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    """A relay in the hall that nothing says what it switches; and one in the garage, which
    goes on and off by itself."""
    await _add(ha, clock, Entity(RELAY, f"reg-{RELAY}", "Relais gang", area_id="hall"))
    await _add(ha, clock, Entity("switch.garage_relay", "reg-gr", "Relais", area_id="garage"))


def _went_with_the_hall_light(ha: FakeHomeAssistant, now: datetime) -> None:
    """Last week the hall relay went on and off eight times, each time with the hall light
    within two seconds; the garage relay did too, with no lamp."""
    ha.record(RELAY, "off", now - timedelta(days=6))  # the recorder goes back a week
    for i in range(8):
        at = now - timedelta(days=5) + timedelta(hours=10 * i)
        state = "on" if i % 2 == 0 else "off"
        ha.record(RELAY, state, at)
        ha.record("light.hall", state, at + timedelta(seconds=2))
        ha.record("switch.garage_relay", state, at + timedelta(hours=3))


async def test_a_plug_that_runs_two_hours_at_two_kilowatt_is_derived_without_ai(
    tmp_path: Path,
) -> None:
    """With AI off, the plug in the hall ran three times for about two hours at about 2 kW.
    Once a day, beside the core, HouseMind derives that it is probably a washing machine or
    dishwasher: under Afgeleid, "afgeleid uit gedrag", with how sure and why. It knows the plug
    as an appliance's start at once, but never switches it on its own before a Klopt; and once
    AI is on, the AI is not asked about it – it only gets what is left (E-201)."""

    async def setup(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
        await _plug(ha, clock)
        await _add(ha, clock, Entity("switch.extra", "reg-extra", "Extra"))  # nothing to see

    hm, clock, ha = await _start(tmp_path, setup=setup)
    assert hm.gate is not None
    _ran_like_a_washing_machine(ha, clock.now())
    await _derive(hm)
    finding = hm.knowledge.get("entity", PLUG, "role")
    assert finding is not None and (finding.source, finding.status) == ("behaviour", "suggested")
    assert finding.value["role"] == "appliance_start" and finding.certainty == 0.8
    page = views.house_knowledge(hm, "nl")
    assert to_confirm(page) == [] and [r["name"] for r in derived(page)] == ["Stekker van Emma"]
    row = derived(page)[0]
    assert row["why"] == (
        "Draaide 3 keer ongeveer 1,8 uur op ongeveer 2,1 kW. "
        "Dat is waarschijnlijk een wasmachine of vaatwasser."
    )
    assert (row["source"], row["certainty"]) == ("behaviour", 80)
    kind = recognise(hm.catalog, hm.model.entities[PLUG], hm.gate.state.roles.get(PLUG)).kind
    assert kind is not None and kind.key == "switch.appliance_start"
    start = ActionRequest(
        Layer.PLANNER, PLUG, "switch.turn_on", skill="energy_appliance", created_at=clock.now()
    )
    held = hm.gate.evaluate(start)
    assert held.outcome is Outcome.OBSERVED and held.reason == "behaviour_unconfirmed"
    fake = LaterAI([])
    await enable(hm, fake)
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    asked = [sent for _, sent, _ in fake.sent if '"switches"' in sent]
    assert len(asked) == 1 and hm.ai.labels.to_label["switch.extra"] in asked[0]
    assert hm.ai.labels.to_label[PLUG] not in asked[0]
    await hm.stop()


async def test_a_switch_that_goes_with_a_lamp_is_watched_as_a_lamp_and_switched_after_klopt(
    tmp_path: Path,
) -> None:
    """A relay in the hall went on and off with the hall light eight times out of eight:
    HouseMind derives that it powers a lamp. A relay that changed without any lamp gets
    nothing. When someone walks into the dark hall, the hall light goes on but the relay not –
    and Waarom niet? says HouseMind derived it from behaviour. After Klopt the same walk in
    switches it, and Waarom? says where that came from."""
    hm, clock, ha = await _start(tmp_path, setup=_relay)
    assert hm.gate is not None and hm.learning is not None
    _went_with_the_hall_light(ha, clock.now())
    await _derive(hm)
    finding = hm.knowledge.get("entity", RELAY, "role")
    assert finding is not None and finding.value["role"] == "light"
    assert finding.certainty == 0.95 and finding.value["facts"]["times"] == 8
    assert hm.knowledge.get("entity", "switch.garage_relay", "role") is None
    row = derived(views.house_knowledge(hm, "nl"))[0]
    assert row["why"] == "Ging 100% van de 8 keer tegelijk aan en uit met Gang lamp."
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await clock.advance((START + timedelta(hours=17, minutes=30) - clock.now()).total_seconds())
    await ha.simulate("binary_sensor.hall_motion", "on")
    turned = [(action, entity) for action, entity, _ in ha.calls]
    assert ("light.turn_on", "light.hall") in turned and ("switch.turn_on", RELAY) not in turned
    assert RELAY in hm.last_not_done, (
        sorted(hm.last_not_done),
        hm.skills and hm.skills.usual_lights("hall"),
    )
    thought = hm.last_not_done[RELAY]
    assert thought.outcome is Outcome.OBSERVED and thought.reason == "behaviour_unconfirmed"
    assert any(p.entity_id == RELAY for p in hm.learning.pending)  # it learns from it
    assert "leidde ik af uit wat het deed" in hm.why_not(RELAY, "nl")
    assert await hm.answer_knowledge(f"entity:{RELAY}:role", True)
    again = replace(thought.request, created_at=clock.now())
    done = hm.gate.evaluate(again)
    assert done.outcome is Outcome.EXECUTED
    why = hm.explain(done, 3, "nl")
    assert (
        "Volgens wat HouseMind van je huis weet (afgeleid uit gedrag, bevestigd door jou)." in why
    )
    await hm.stop()


async def test_a_sensor_that_goes_dark_at_sunset_measures_light_only_after_klopt(
    tmp_path: Path,
) -> None:
    """A sensor in the living room that says nothing about what it measures was light all day
    and dark from half an hour after sunset, four days in a row: HouseMind derives that it
    measures light. It changes what HouseMind decides, so the living room's light level only
    uses it after Klopt."""

    async def setup(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
        ha.config = replace(ha.config, latitude=52.1, longitude=5.1)
        await _add(ha, clock, Entity(LDR, f"reg-{LDR}", "Meting", area_id="living"), "300")

    hm, clock, ha = await _start(tmp_path, setup=setup)
    assert hm.world is not None
    now = clock.now()
    for days in (4, 3, 2, 1):
        morning = now - timedelta(days=days) + timedelta(hours=8)
        ha.record(LDR, "300", morning)
        ha.record(LDR, "2", morning + timedelta(hours=9, minutes=10))  # 17:10, after sunset
    ha.record("light.hall", "off", now - timedelta(days=4))
    await _derive(hm)
    finding = hm.knowledge.get("entity", LDR, "measures")
    assert finding is not None and finding.source == "behaviour" and finding.certainty == 0.95
    row = next(r for r in derived(views.house_knowledge(hm, "nl")) if r["name"] == "Meting")
    assert row["what"] == "meet licht"
    assert row["why"] == "Werd op 4 van de 4 dagen donker bij zonsondergang."
    assert hm.world.snapshot(clock.now()).lux["living"] is None
    assert await hm.answer_knowledge(f"entity:{LDR}:measures", True)
    assert hm.world.snapshot(clock.now()).lux["living"] == 300.0
    await hm.stop()


async def _two_sensors(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    """Two thermometers in the living room that only measure the air, reading far apart."""
    for entity_id, name, value in (
        (WARM, "Thermometer kast", "24.0"),
        (COOL, "Thermometer", "19.0"),
    ):
        entity = Entity(entity_id, f"reg-{entity_id}", name, area_id="living")
        await _add(ha, clock, replace(entity, device_class="temperature", unit="°C"), value)


def _climate(hm: HouseMind) -> Decision:
    """A heating decision in the living room, to ask Waarom? about."""
    now = hm.clock.now()
    request = ActionRequest(
        Layer.LEARNING,
        "climate.living",
        "climate.set_temperature",
        {"temperature": 20.0},
        skill="climate_comfort",
        area_id="living",
        created_at=now,
    )
    return Decision(request, Outcome.EXECUTED, None, Step.AUTONOMOUS, (), now)


async def test_a_room_with_two_thermometers_is_a_doubt_an_administrator_settles(
    tmp_path: Path,
) -> None:
    """The living room has two thermometers: the rules are unsure which is the room's own, so
    they average them and offer their guess for Klopt – also without AI. The administrator
    picks the other one: from then on that is the room's temperature, also after a restart,
    and Waarom? for heating there says the administrator chose it."""
    hm, _clock, ha = await _start(tmp_path, setup=_two_sensors)
    assert hm.world is not None
    states = hm.world.states
    assert hm.temperatures.of_room("living", states) == round((24.0 + 19.0 + 20.1) / 3, 1)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        await ws.send_json({"type": "get", "what": "house_knowledge"})
        row = next(r for r in to_confirm(await _page(ws)) if r["field"] == "temperature")
        assert (row["source"], row["name"], row["label"]) == (
            "rules",
            "Woonkamer",
            "Temperatuur van de ruimte",
        )
        assert {o["id"] for o in row["options"]} == {WARM, COOL}
        guess = hm.knowledge.get("area", "living", "temperature")
        assert guess is not None
        other = COOL if guess.value["sensor"] == WARM else WARM
        await ws.send_json({"type": "knowledge", "do": "role", "id": row["id"], "role": other})
        page = await _page(ws)
        assert confirmed(page)[0]["what"].startswith(hm.model.entities[other].name)
        await ws.close()
    assert hm.temperatures.sources("living") == [other, "climate.living"]
    assert "Volgens wat HouseMind van je huis weet (door jou gekozen)." in hm.explain(
        _climate(hm), 3, "nl"
    )
    await hm.stop()
    again = HouseMind(hm.clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert again.temperatures.sources("living") == [other, "climate.living"]
    await again.stop()


async def test_a_door_on_a_relay_and_a_light_on_a_presence_sensor_are_asked_and_then_used(
    tmp_path: Path,
) -> None:
    """A door contact on a relay's device: the rules leave it out as an appliance's, unsure.
    Klopt niet: it is a door of the house from then on – heating and "open while away" count
    it. A contact of the plain class "opening" in the kitchen may be the dishwasher's: the
    rules count it, unsure; Klopt niet takes it out. The same in the hall is not asked. A light
    on the bedroom's presence sensor, named LED: the rules call it a status light, unsure;
    until an answer that guess holds and the lighting leaves it alone (E-426). Klopt keeps
    it so."""

    async def setup(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
        ha.model.devices["device.opener"] = Device("device.opener", "Opener", "garage", "shelly")
        ha.model.devices["device.radar"] = Device("device.radar", "Radar", "bedroom", "esphome")
        for entity in (
            Entity(CONTACT, "reg-c", "Contact", device_id="device.opener", device_class="door"),
            Entity("switch.opener", "reg-o", "Opener", device_id="device.opener"),
            Entity(KITCHEN, "reg-k", "Contact", area_id="kitchen", device_class="opening"),
            Entity(HALL, "reg-h", "Contact", area_id="hall", device_class="opening"),
            Entity(LED, "reg-l", "LED", device_id="device.radar", area_id="bedroom"),
            Entity(
                "binary_sensor.bedroom_radar",
                "reg-r",
                "Radar",
                device_id="device.radar",
                area_id="bedroom",
                device_class="presence",
            ),
        ):
            await _add(ha, clock, entity)

    hm, _clock, _ha = await _start(tmp_path, setup=setup)
    assert hm.skills is not None and hm.world is not None
    page = views.house_knowledge(hm, "nl")
    rows = {r["id"].split(":")[1]: r for r in to_confirm(page) if r["source"] == "rules"}
    assert set(rows) == {CONTACT, KITCHEN, LED}
    assert rows[CONTACT]["what"] == "geen deur of raam van het huis"
    assert rows[KITCHEN]["what"] == "een deur of raam van het huis"
    assert rows[LED]["what"] == "een statuslampje van een apparaat, geen gewone lamp"
    assert CONTACT not in hm.openings and {KITCHEN, HALL} <= hm.openings
    assert await hm.answer_knowledge(f"entity:{KITCHEN}:opening", False)
    assert KITCHEN not in hm.openings
    assert LED not in [e.entity_id for e in hm.skills._entities("bedroom", "light")]
    assert await hm.answer_knowledge(f"entity:{CONTACT}:opening", False)
    assert CONTACT in hm.openings and hm.skills.openings is not None
    assert CONTACT in hm.skills.openings
    assert await hm.answer_knowledge(f"entity:{LED}:status_led", True)
    assert LED not in [e.entity_id for e in hm.skills._entities("bedroom", "light")]
    assert hm.world.status_leds == {LED: True}
    page = views.house_knowledge(hm, "nl")
    assert [r["field"] for r in confirmed(page)] == ["status_led"] and rejected(page) == 2
    await hm.stop()


async def test_with_ai_a_doubt_gets_the_ai_s_pick_from_neutral_facts_used_after_klopt(
    tmp_path: Path,
) -> None:
    """With AI on, the two thermometers go to the AI with neutral facts only – the kind of
    the room and of each device – never a name. Its pick is offered for Klopt in place of the
    rules' guess, but the room keeps the rules' average until an administrator says Klopt.
    Then it is the room's, and Waarom? says it came from AI, confirmed – AI is never the
    decider."""
    hm, _clock, _ha = await _start(tmp_path, setup=_two_sensors)
    assert hm.world is not None
    guess = hm.knowledge.get("area", "living", "temperature")
    assert guess is not None
    pick = COOL if guess.value["sensor"] == WARM else WARM
    fake = LaterAI([])
    await enable(hm, fake)
    fake.replies = [  # rooms, switches and automations: nothing to ask in this house
        lambda: {
            "doubts": {
                "doubt_1": {
                    "pick": hm.ai.labels.to_label[pick],
                    "certainty": 0.7,
                    "why": "Een losse thermometer.",
                }
            }
        },
    ]
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    _, sent, _ = next(s for s in fake.sent if "room_temperature" in s[1])
    data = json.loads(sent.removeprefix("<data>\n").removesuffix("\n</data>"))
    question = data["questions"]["doubt_1"]
    assert question["room"] == "living" and len(question["options"]) == 2
    for name in ("Thermometer", "kast", "Woonkamer", WARM, COOL):
        assert name not in sent
    finding = hm.knowledge.get("area", "living", "temperature")
    assert finding is not None and (finding.source, finding.value["sensor"]) == ("ai", pick)
    assert finding.value["rules"] == guess.value["sensor"]
    row = next(
        r for r in to_confirm(views.house_knowledge(hm, "nl")) if r["field"] == "temperature"
    )
    assert (row["source"], row["why"], row["certainty"]) == ("ai", "Een losse thermometer.", 70)
    assert set(hm.temperatures.sources("living")) == {WARM, COOL, "climate.living"}
    assert await hm.answer_knowledge("area:living:temperature", True)
    assert hm.temperatures.sources("living") == [pick, "climate.living"]
    lines = hm.explain(_climate(hm), 3, "nl")
    assert "Volgens wat HouseMind van je huis weet (van AI, bevestigd door jou)." in lines
    assert not any("AI besl" in line for line in lines)
    await hm.stop()


async def test_an_administrator_on_informed_gets_one_question_a_day_with_klopt(
    tmp_path: Path,
) -> None:
    """An administrator on Geïnformeerd has no page for this: Vesta asks one question a day
    about something to confirm, in her own words, with Klopt and Klopt niet – also on the
    phone, within the question budget. A second waits for the next day. Answering it settles
    the finding; the next day the next question comes."""

    async def setup(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
        await _two_sensors(ha, clock)
        ha.model.devices["device.opener"] = Device("device.opener", "Opener", "garage", "shelly")
        contact = Entity(
            CONTACT, "reg-c", "Contact", device_id="device.opener", device_class="door"
        )
        await _add(ha, clock, contact)
        await _add(ha, clock, Entity("switch.opener", "reg-o", "Opener", device_id="device.opener"))

    hm, clock, ha = await _start(tmp_path, level=2, setup=setup)
    assert hm.router is not None and hm.gate is not None
    # The heating only watches today: no proposal takes the day's one question first.
    hm.gate.state.admin_max_step["climate"] = Step.OBSERVE
    await clock.advance(10 * 3600)  # 10:00, outside quiet hours, the house awake
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    await hm.tick()
    open_ = [r for r in await hm.notifications() if str(r["topic"]).startswith("knowledge:")]
    assert [r["topic"] for r in open_] == ["knowledge:area:living:temperature"]
    assert any("de temperatuur van deze ruimte" in d.message for d in hm.router.deliveries)
    asks = await hm.vesta_asks("demo-user-1", 2, True)
    ask = next(a for a in asks if a["kind"] == "knowledge")
    assert ask["actions"] == ["yes", "no"]
    said = await hm.vesta_ask_text("demo-user-1", "nl", ask)
    assert "de temperatuur van deze ruimte" in said
    await clock.advance(3600)
    await hm.tick()  # the same day: no second question
    assert len([r for r in await hm.notifications() if "knowledge:" in str(r["topic"])]) == 1
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        await ws.send_json({"type": "notification", "id": int(open_[0]["id"]), "action": "yes"})
        await _next(ws, "snapshot")
        await ws.close()
    finding = hm.knowledge.get("area", "living", "temperature")
    assert finding is not None and finding.status == "confirmed"
    assert not [r for r in await hm.notifications() if "knowledge:" in str(r["topic"])]
    await hm.tick()
    assert not [r for r in await hm.notifications() if "knowledge:" in str(r["topic"])]
    await clock.advance(24 * 3600)
    await hm.tick()  # the next day: the next one
    topics = [r["topic"] for r in await hm.notifications() if "knowledge:" in str(r["topic"])]
    assert topics == [f"knowledge:entity:{CONTACT}:opening"]
    row = next(r for r in await hm.notifications() if r["topic"] == topics[0])
    item = views.notice_item(hm, "nl", dict(row), True, "demo-user-1")
    assert item is not None and item["labels"] == {"yes": "Klopt", "no": "Klopt niet"}
    await hm.stop()


async def test_no_question_for_residents_for_the_page_s_levels_or_without_anything_to_confirm(
    tmp_path: Path,
) -> None:
    """Vesta's question is only for administrators on Geïnformeerd: a resident on that level
    who is no administrator gets nothing, an administrator on Betrokken has the page and gets
    no question, and with nothing to confirm nobody is asked."""
    hm, clock, _ha = await _start(tmp_path, level=3, setup=_two_sensors)
    assert hm.router is not None
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-2", "Bewoner", 2, clock.now().isoformat()),
    )
    await hm._refresh_people()
    await clock.advance(10 * 3600)
    await hm.tick()
    rows = to_confirm(views.house_knowledge(hm, "nl"))
    assert [r["id"] for r in rows] == ["area:living:temperature"]  # on the page instead
    assert not [r for r in await hm.notifications() if "knowledge:" in str(r["topic"])]
    assert not [a for a in await hm.vesta_asks("demo-user-2", 2, False) if a["kind"] == "knowledge"]
    await hm.stop()
    plain, clock, _ha = await _start(tmp_path / "plain", level=2)
    await clock.advance(10 * 3600)
    await plain.tick()
    assert to_confirm(views.house_knowledge(plain, "nl")) == []
    assert not [r for r in await plain.notifications() if "knowledge:" in str(r["topic"])]
    await plain.stop()


async def test_reading_the_history_never_slows_the_light_in_the_hall(tmp_path: Path) -> None:
    """Deriving from behaviour reads the history beside the core: with a recorder that never
    answers – and the AI's question about switches waiting for it – someone walks into the
    dark hall and the light command still goes out within half a second (E-85, E-414)."""
    hm, clock, ha = await _start(tmp_path, setup=_plug)
    assert hm.learning is not None
    reading = asyncio.Event()
    recorder = ha.history

    async def hanging(entity_ids: list[str], start: datetime, end: datetime) -> list[Any]:
        if entity_ids != ["sensor.hall_plug_power"]:
            return await recorder(entity_ids, start, end)
        reading.set()  # the plug's power, for deriving from behaviour: never answers
        await asyncio.Event().wait()
        return []

    ha.record("light.hall", "off", clock.now() - timedelta(days=3))
    ha.history = hanging  # type: ignore[method-assign]
    await enable(hm, FakeAI([]))
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await clock.advance((START + timedelta(hours=17, minutes=30) - clock.now()).total_seconds())
    await hm.tick()
    hm.read_house_soon(now=True)
    await asyncio.wait_for(reading.wait(), 1)
    await asyncio.sleep(0.05)
    assert "switches" in hm.ai_jobs.busy  # waits for what behaviour says, beside the core
    moments = _stopwatch(ha)
    started = timer.perf_counter()
    await asyncio.wait_for(ha.simulate("binary_sensor.hall_motion", "on"), 0.5)
    assert moments and moments[0] - started < 0.5
    await asyncio.wait_for(hm.stop(), 2)


async def test_a_new_device_from_home_assistant_brings_its_doubt(tmp_path: Path) -> None:
    """A second thermometer added in Home Assistant later: after the house is read again, the
    living room is a doubt to confirm, and nothing changes in the house before an answer."""
    hm, clock, ha = await _start(tmp_path)
    assert hm.knowledge.get("area", "living", "temperature") is None
    await _two_sensors(ha, clock)
    await clock.advance(MODEL_SETTLE.total_seconds() + 1)
    await hm.tick()
    finding = hm.knowledge.get("area", "living", "temperature")
    assert finding is not None and finding.source == "rules" and finding.status == "suggested"
    assert set(hm.temperatures.sources("living")) == {WARM, COOL, "climate.living"}
    await hm.stop()
