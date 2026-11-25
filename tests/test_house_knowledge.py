"""Je huis begrijpen (E-201 – E-208, design 50.2, B-343 – B-346), from the user's side. The
administrator's choices: AI never takes the real decisions and the core never waits for it
(E-414); what AI says counts at once for watching and learning, marked "van AI", and only
after Klopt for proposing and doing (50.8); the make and type of a device may go to the AI,
never a name someone gave; reading the house uses at most half the month's budget. Each test
fails without the build; what it checks is its docstring."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.ai.providers import AIResult
from housemind.brain import start_over
from housemind.core.clock import SimulatedClock
from housemind.core.model import Area, Device, Entity, State
from housemind.demo.houses import load_demo_house
from housemind.ha.adapter import HAUser
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.knowledge.matcher import recognise
from housemind.parts.thinking import MODEL_SETTLE
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer, Outcome
from housemind.server import views
from housemind.server.web import create_app
from tests.knowledge_page import confirmed, rejected, to_confirm
from tests.test_ai import FakeAI, enable
from tests.test_screens import _next
from tests.test_speed import START

ROOT = Path(__file__).resolve().parents[1]
PLUG = "switch.hall_plug"
BEDTIME = "automation.bedtime"
# What someone in the house named: none of it may reach an AI (50.8). Only the question about
# a room has that room's own name, without the person in it (QA 2, B-174).
NAMES = ("Emma", "Stekker", "Bedtijd", "emma", "hall_plug", "bedtime", "slaapt", "Gang")
ADMIN = {"X-Remote-User-Id": "demo-user-1"}


class LaterAI(FakeAI):
    """A fake AI whose answers are made when it is asked: with the labels of that moment. What
    each entity is (design 51) has tests of its own (tests/test_entity_functions_ai.py): here
    the AI is never sure of it, and that question is kept apart in ``functions_sent``."""

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        if '"entities"' in user:
            sent = self.__dict__.setdefault("functions_sent", [])
            sent.append((system, user, model))
            return AIResult(ok=True, text='{"functions": {}}', input_tokens=900, output_tokens=20)
        if self.replies and callable(self.replies[0]):
            self.replies[0] = self.replies[0]()
        return await super().complete(system, user, model, max_tokens)


def _bedtime() -> dict[str, Any]:
    """An automation the rules cannot place: a flag and a script, after motion in the hall
    late in the evening."""
    return {
        "id": "bedtime",
        "alias": "Bedtijd van Emma",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.hall_motion", "to": "on"}],
        "conditions": [
            {
                "condition": "and",
                "conditions": [{"condition": "time", "after": "22:00:00"}],
            }
        ],
        "actions": [
            {
                "action": "input_boolean.turn_on",
                "target": {"entity_id": "input_boolean.emma_slaapt"},
            },
            {"action": "script.emma_welterusten", "data": {"message": "Slaap lekker Emma"}},
        ],
    }


async def _plug(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    """A smart plug in the hall that measures power – nothing says what it switches."""
    ha.model.devices["device.plug"] = Device(
        "device.plug", "Stekker van Emma", "hall", "zha", manufacturer="Acme", model="Plug X1"
    )
    for entity_id, name, device_class in (
        (PLUG, "Stekker van Emma", "outlet"),
        ("sensor.hall_plug_power", "Stekker van Emma vermogen", "power"),
    ):
        entity = Entity(
            entity_id,
            f"reg-{entity_id}",
            name,
            device_id="device.plug",
            area_id="hall",
            device_class=device_class,
            platform="zha",
        )
        await ha.add_entity(entity, State(entity_id, "off", {}, clock.now()))


async def _house(
    tmp_path: Path, plug: bool = True, level: int = 3
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    """The family home with a name in a room, a plug and an automation of the house's own;
    its administrator on Betrokken."""
    clock = SimulatedClock(START.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    ha.users_list[0] = HAUser("demo-user-1", "Emma", is_admin=True)
    ha.model.areas["hobby"] = Area("hobby", "Hobbyhoek van Emma", None)
    if plug:
        await _plug(ha, clock)
    await ha.add_automation(BEDTIME, "Bedtijd van Emma", _bedtime())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-1", "Emma", level, clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, clock, ha


def _answers(hm: HouseMind) -> list[dict[str, Any]]:
    """What the AI says to the three questions, in the order they are asked."""
    labels = hm.ai.labels
    room, plug = labels.area("hobby"), labels.to_label[PLUG]
    motion = labels.to_label["binary_sensor.hall_motion"]
    return [
        {"roles": {room: "office"}, "certainty": {room: 0.8}, "why": {room: "Een hobbyhoek."}},
        {"switches": {plug: {"role": "light", "certainty": 0.7, "why": "Een stekker."}}},
        {
            "automations": {
                "automation_1": {
                    "summary": f"Bij beweging bij {motion} na 22 uur gaat de bedtijd in.",
                    "part": "other",
                    "take_over": "no",
                }
            }
        },
    ]


def _later(hm: HouseMind, *which: int) -> list[Any]:
    return [lambda i=i: _answers(hm)[i] for i in which or (0, 1, 2)]


async def _read(hm: HouseMind) -> FakeAI:
    """AI on, and the house read once – in the queue beside the core."""
    fake = LaterAI([])
    await enable(hm, fake)
    fake.replies = _later(hm)
    assert hm.read_house_soon()
    await hm.ai_jobs.idle()
    return fake


async def _ws(client: TestClient) -> Any:
    ws = await client.ws_connect("/api/ws", headers=ADMIN)
    await ws.send_json({"type": "hello", "browserLanguage": "nl"})
    await _next(ws, "snapshot")
    return ws


async def _page(ws: Any) -> dict[str, Any]:
    while True:
        message = await _next(ws, "data")
        if message["what"] == "house_knowledge":
            return message["data"]  # type: ignore[no-any-return]


async def test_a_room_waits_for_klopt_and_then_is_that_room(tmp_path: Path) -> None:
    """A room the words do not know gets a role from AI. Until an administrator says Klopt it
    only shows, "van AI", with how sure and why: the house goes on calling it "other". After
    Klopt it is that room, as the person's own, also after a restart."""
    hm, _clock, ha = await _house(tmp_path)
    await _read(hm)
    assert hm.area_roles["hobby"] == "other"
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        await ws.send_json({"type": "get", "what": "house_knowledge"})
        page = await _page(ws)
        room = next(r for r in to_confirm(page) if r["kind"] == "area")
        assert (room["name"], room["what"], room["fromAI"]) == (
            "Hobbyhoek van Emma",
            "Werkkamer",
            True,
        )
        assert room["certainty"] == 80 and room["why"] == "Een hobbyhoek."
        assert {"id": "office", "name": "Werkkamer"} in page["roles"]
        await ws.send_json({"type": "knowledge", "do": "yes", "id": "area:hobby:role"})
        page = await _page(ws)
        assert [r["name"] for r in confirmed(page)] == ["Hobbyhoek van Emma"]
        assert confirmed(page)[0]["source"] == "ai" and not confirmed(page)[0]["fromAI"]
        await ws.close()
    assert hm.area_roles["hobby"] == "office"
    row = await hm.db.fetchone("SELECT role, role_source FROM areas WHERE area_id = 'hobby'")
    assert row is not None and (row["role"], row["role_source"]) == ("office", "person")
    await hm.stop()
    again = HouseMind(hm.clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert again.area_roles["hobby"] == "office"
    await again.stop()


async def test_klopt_niet_is_never_asked_again_and_another_kind_is_the_persons(
    tmp_path: Path,
) -> None:
    """Klopt niet: the room stays as it was, and reading the house again – even with Nu
    bekijken – never asks the AI about it again. An administrator may also choose another
    kind of room: then that is the room, as their own."""
    hm, _clock, _ha = await _house(tmp_path)
    fake = await _read(hm)
    assert await hm.answer_knowledge("area:hobby:role", False)
    assert hm.area_roles["hobby"] == "other"
    page = views.house_knowledge(hm, "nl")
    assert not [r for r in to_confirm(page) if r["kind"] == "area"] and rejected(page) == 1
    asked = len(fake.sent)
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    assert all("Hobbyhoek" not in sent for _, sent, _ in fake.sent[asked:])
    assert await hm.answer_knowledge("area:hobby:role", True, "utility")
    assert hm.area_roles["hobby"] == "utility"
    assert confirmed(views.house_knowledge(hm, "nl"))[0]["source"] == "person"
    assert not await hm.answer_knowledge("area:hobby:role", True, "castle")  # not a kind
    await hm.stop()


async def test_a_switch_from_ai_is_watched_as_a_lamp_but_not_switched_until_klopt(
    tmp_path: Path,
) -> None:
    """The plug in the hall comes in after the start (Home Assistant reports it) and AI says
    it powers a lamp. From then on HouseMind watches and learns it as a lamp – but when
    someone walks into the dark hall it switches only the hall light, not the plug, and says
    why. A person's own request still works. After Klopt the same walk in switches the plug
    too."""
    hm, clock, ha = await _house(tmp_path, plug=False)
    assert hm.gate is not None and hm.learning is not None
    await hm.set_house_setting("consent_learning", True)
    fake = LaterAI([])
    await enable(hm, fake)
    await _plug(ha, clock)  # Home Assistant reports a new device
    fake.replies = _later(hm)
    await clock.advance(MODEL_SETTLE.total_seconds() + 1)
    await hm.tick()  # the house is read again, and with it what is new
    await hm.ai_jobs.idle()
    plug = hm.model.entities[PLUG]
    kind = recognise(hm.catalog, plug, hm.gate.state.roles.get(PLUG)).kind
    assert kind is not None and kind.key == "switch.light"  # watched as a lamp at once
    row = next(r for r in to_confirm(views.house_knowledge(hm, "nl")) if r["kind"] == "entity")
    assert row["what"] == "een lamp" and row["fromAI"]
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    await clock.advance((START + timedelta(hours=17, minutes=30) - clock.now()).total_seconds())
    await hm.tick()
    await ha.simulate("binary_sensor.hall_motion", "on")
    turned = [(action, entity) for action, entity, _ in ha.calls]
    assert ("light.turn_on", "light.hall") in turned and ("switch.turn_on", PLUG) not in turned
    thought = hm.last_not_done[PLUG]
    assert thought.outcome is Outcome.OBSERVED and thought.reason == "ai_unconfirmed"
    assert any(p.entity_id == PLUG for p in hm.learning.pending)  # it learns from it
    assert "alleen AI" in hm.why_not(PLUG, "nl")
    own = ActionRequest(Layer.PERSON_ANSWER, PLUG, "switch.turn_on", created_at=clock.now())
    assert hm.gate.evaluate(own).outcome is Outcome.EXECUTED
    assert await hm.answer_knowledge(f"entity:{PLUG}:role", True)
    again = replace(thought.request, created_at=clock.now())  # the same walk into the hall
    assert hm.gate.evaluate(again).outcome is Outcome.EXECUTED
    await hm.stop()


async def test_klopt_niet_takes_the_switch_role_away(tmp_path: Path) -> None:
    """Klopt niet on the plug: it is a plain switch again, only watched, and the AI is never
    asked about it again."""
    hm, _clock, _ha = await _house(tmp_path)
    assert hm.gate is not None
    fake = await _read(hm)
    assert hm.gate.state.roles.get(PLUG) == "light" and PLUG in hm.gate.state.from_ai
    assert await hm.answer_knowledge(f"entity:{PLUG}:role", False)
    assert PLUG not in hm.gate.state.roles and not hm.gate.state.from_ai
    asked = len(fake.sent)
    hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    assert all(hm.ai.labels.to_label[PLUG] not in sent for _, sent, _ in fake.sent[asked:])
    await hm.stop()


async def test_what_an_automation_does_shows_in_the_automations(tmp_path: Path) -> None:
    """An automation the rules cannot place gets one plain sentence from AI, in the house's
    language with its own names, at Bestaande automatiseringen – "van AI", only to read."""
    hm, _clock, ha = await _house(tmp_path)
    await _read(hm)
    rows = {r["id"]: r for r in (await views.house_settings(hm, "nl"))["automations"]}
    does = rows[BEDTIME]["does"]
    assert does["text"].startswith("Bij beweging bij ") and "binary_sensor_" not in does["text"]
    assert "na 22 uur gaat de bedtijd in" in does["text"]
    assert (does["part"], does["takeOver"]) == ("other", "no")
    assert views.house_knowledge(hm, "nl")["described"] == 1
    assert ha.automation_configs[BEDTIME] == _bedtime()  # AI changes nothing (E-03)
    await hm.stop()


async def test_no_name_anyone_gave_reaches_the_ai(tmp_path: Path) -> None:
    """What goes out to read the house: the room's name without the person in it, the plug's
    make, type, class, integration and what else is on its device, and the automation's
    structure with labels – never a name of a person, device, plug or automation, the text it
    sends, or an entity id."""
    hm, _clock, _ha = await _house(tmp_path)
    fake = await _read(hm)
    assert [model for _, _, model in fake.sent] and len(fake.sent) == 3
    for _, sent, _ in fake.sent:
        for name in NAMES:
            assert name not in sent, (name, sent)
    assert ["Hobbyhoek" in sent for _, sent, _ in fake.sent] == [True, False, False]
    for _, sent, _ in fake.__dict__.get("functions_sent", []):  # what each entity is (51.4)
        for name in NAMES:
            assert name not in sent, (name, sent)
    for _, sent, _ in fake.sent:
        assert "Slaap lekker" not in sent and "input_boolean.emma" not in sent
    rooms, switches, automations = (
        json.loads(sent.removeprefix("<data>\n").removesuffix("\n</data>"))
        for _, sent, _ in fake.sent
    )
    assert [re.sub(r"\d+", "N", r) for r in rooms["rooms"].values()] == ["Hobbyhoek van resident_N"]
    facts = switches["switches"][hm.ai.labels.to_label[PLUG]]
    assert facts == {
        "domain": "switch",
        "device_class": "outlet",
        "integration": "zha",
        "manufacturer": "Acme",
        "model": "Plug X1",
        "with": ["sensor:power"],
    }
    structure = automations["automations"]["automation_1"]
    assert structure["conditions"] == [{"type": "time", "at": "22:00:00"}]
    assert structure["actions"] == [
        {"do": "input_boolean.turn_on", "on": ["input_boolean"]},
        {"do": "script"},
    ]
    log = await hm.ai.log()
    assert {row["purpose"] for row in log} == {"roles", "functions", "switches", "automations"}
    finding = hm.knowledge.get("entity", PLUG, "role")
    assert finding is not None and finding.ai_log in {row["id"] for row in log}
    await hm.stop()


async def test_reading_the_house_uses_at_most_half_the_budget(tmp_path: Path) -> None:
    """Once reading the house used half the month's budget it waits – Vesta still answers –
    and next month it goes on by itself at the quarter-hour look."""
    hm, clock, _ha = await _house(tmp_path)
    fake = LaterAI([])
    await enable(hm, fake, monthly_budget=2.0)
    await hm.db.execute(
        "INSERT INTO ai_log(at, purpose, provider, model, sent, received, input_tokens, "
        "output_tokens, cost, ok) VALUES(?, 'switches', 'anthropic', 'm', '', '', 0, 0, 1.0, 1)",
        (clock.now().isoformat(),),
    )
    hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    assert fake.sent == [] and not hm.knowledge.findings
    fake.replies = [{"answer": "Ja."}]
    assert (await hm.ask("Is het warm genoeg?", "nl", "demo-user-1"))["ok"]
    fake.replies = _later(hm)
    asked = len(fake.sent)
    await clock.advance((START.replace(month=12, day=1) - clock.now()).total_seconds() + 3600)
    await hm.tick()
    await hm.ai_jobs.idle()
    assert len(fake.sent) == asked + 3 and hm.knowledge.get("area", "hobby", "role") is not None
    await hm.stop()


async def test_a_local_ai_has_no_limit(tmp_path: Path) -> None:
    """An AI in the house costs nothing: reading goes on whatever the cloud budget says."""
    hm, _clock, _ha = await _house(tmp_path)
    fake = LaterAI([], base_url="http://192.168.1.20:11434/v1")
    await enable(hm, fake, monthly_budget=0.0)
    fake.replies = _later(hm)
    hm.read_house_soon()
    await hm.ai_jobs.idle()
    assert len(fake.sent) == 3
    await hm.stop()


async def test_without_ai_nothing_is_asked_and_the_page_says_so(tmp_path: Path) -> None:
    """With AI off nothing is queued, not at the start, not at the quarter-hour look, not
    after a change; the page says AI could help and shows what the catalogue recognised.
    Only administrators from Betrokken on see it."""
    hm, clock, ha = await _house(tmp_path)
    await hm.tick()
    await ha.add_entity(
        Entity("switch.extra", "reg-switch.extra", "Extra"),
        State("switch.extra", "off", {}, clock.now()),
    )
    await clock.advance(MODEL_SETTLE.total_seconds() + 1)
    await hm.tick()
    assert not hm.read_house_soon(now=True)
    assert hm.ai_jobs.busy == [] and hm.ai_jobs.finished == []
    page = views.house_knowledge(hm, "nl")
    assert page["ai"] is False and to_confirm(page) == [] and page["recognised"] > 10
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        await ws.send_json({"type": "knowledge", "do": "look"})
        assert "Met AI kan HouseMind" in (await _next(ws, "saved"))["text"]
        await ws.close()
    await hm.db.execute("UPDATE people SET level = 2 WHERE user_id = 'demo-user-1'")
    await hm._refresh_people()
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await _ws(client)
        await ws.send_json({"type": "get", "what": "house_knowledge"})
        await ws.send_json({"type": "get", "what": "muted"})
        message = await _next(ws, "data")
        assert message["what"] == "muted"  # nothing about the house's knowledge on Geïnformeerd
        await ws.close()
    await hm.stop()


async def test_reading_again_once_a_week(tmp_path: Path) -> None:
    """The house is read again once a week at the quarter-hour look; an AI's "not sure" is
    asked again then, not before."""
    hm, clock, _ha = await _house(tmp_path)
    fake = LaterAI([])
    await enable(hm, fake)
    unsure = _answers(hm)
    unsure[1] = {"switches": {hm.ai.labels.to_label[PLUG]: {"role": "unknown"}}}
    fake.replies = unsure
    hm.read_house_soon()
    await hm.ai_jobs.idle()
    assert hm.knowledge.get("entity", PLUG, "role") is None
    asked = len(fake.sent)
    await clock.advance(2 * 24 * 3600)
    await hm.tick()
    await hm.ai_jobs.idle()
    assert len(fake.sent) == asked  # within the week: nothing again
    fake.replies = _later(hm, 1)
    await clock.advance(6 * 24 * 3600)
    await hm.tick()
    await hm.ai_jobs.idle()
    assert hm.knowledge.get("entity", PLUG, "role") is not None
    await hm.stop()


async def test_the_factory_settings_forget_what_ai_said(tmp_path: Path) -> None:
    """Vergeet alles: after the factory settings HouseMind knows nothing AI said."""
    hm, _clock, ha = await _house(tmp_path)
    await _read(hm)
    assert hm.knowledge.findings
    await hm.stop()
    start_over.wipe(hm.paths)
    fresh = HouseMind(hm.clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await fresh.start(autorun=False)
    assert not fresh.knowledge.findings and fresh.gate is not None
    assert PLUG not in fresh.gate.state.roles
    await fresh.stop()


async def test_a_failing_ai_leaves_the_house_as_it_was(tmp_path: Path) -> None:
    """An AI that fails or answers nonsense adds nothing, and the reading is tried again at
    the next look."""
    hm, _clock, _ha = await _house(tmp_path)
    fake = FakeAI([AIResult(ok=False, error="timeout"), "nonsense", {"automations": {"x": 1}}])
    await enable(hm, fake)
    hm.read_house_soon()
    await hm.ai_jobs.idle()
    assert not hm.knowledge.findings
    assert set(hm._house_unread) == {"roles", "functions", "switches", "automations"}
    await hm.stop()
