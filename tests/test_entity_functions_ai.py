"""Every entity's function, part B: the AI reads along (E-429, E-430, design 51.4). Where the
rules cannot tell, an AI gets neutral facts and what the integration calls the entity – never a
name a person gave. What it says makes HouseMind do less at once (a child lock is a setting,
not a switch); a control only after Klopt. In the real house, without the integration's name,
the AI called an oven's child lock "the power switch of a cooking appliance". Each test fails
without the build; what it checks is its docstring."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from housemind.ai.providers import AIResult
from housemind.brain.functions import counts_for_acting
from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity
from housemind.ha.fake import FakeHomeAssistant
from housemind.server import views
from tests.knowledge_page import by_device, to_confirm
from tests.test_ai import FakeAI, enable
from tests.test_house_knowledge_derived import _add, _start

LOCK = "switch.oven_child_lock"
POWER = "switch.garage_relay_output"
OWN = "switch.kitchen_corner"  # a person named it: that name never goes along
SAID = {"Child Lock": ("setting", 0.9), "Output 1": ("appliance_power", 0.8)}


class ReadingAI(FakeAI):
    """An AI that says what each entity is from what it was sent – nothing about the rest."""

    def __init__(self, sure: float | None = None) -> None:
        super().__init__([])
        self.sure = sure

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        self.sent.append((system, user, model))
        data = json.loads(user.removeprefix("<data>\n").removesuffix("\n</data>"))
        reply: dict[str, Any] = {}
        if "entities" in data:
            reply["functions"] = {}
            for label, facts in data["entities"].items():
                function, sure = SAID.get(facts.get("name", ""), ("unknown", 0.3))
                reply["functions"][label] = {
                    "function": function,
                    "certainty": self.sure if self.sure is not None else sure,
                    "why": "Zo heet het bij de integratie.",
                }
        return AIResult(ok=True, text=json.dumps(reply), input_tokens=900, output_tokens=300)


async def _oven(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    """An oven with a child lock, a relay nothing says what it switches, and a switch someone
    named after a person."""
    ha.model.devices["device.oven"] = Device(
        "device.oven", "Oven", "kitchen", "appliances", "Bakeware", "Bake 1"
    )
    oven = {"device_id": "device.oven", "platform": "appliances"}
    await _add(ha, clock, Entity(LOCK, "r1", "Kinderslot", original_name="Child Lock", **oven))
    ha.model.devices["device.relay"] = Device("device.relay", "Relais", "garage", "relays")
    relay = {"device_id": "device.relay", "platform": "relays"}
    await _add(ha, clock, Entity(POWER, "r2", "Uitgang", original_name="Output 1", **relay))
    await _add(
        ha,
        clock,
        Entity(
            OWN,
            "r3",
            "Hoek van Emma",
            area_id="kitchen",
            original_name="Switch 1",
            named_by_person=True,
        ),
    )


async def _read(tmp_path: Path, sure: float | None = None) -> tuple[Any, ReadingAI]:
    hm, _clock, _ha = await _start(tmp_path, setup=_oven)
    fake = ReadingAI(sure)
    await enable(hm, fake)
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    return hm, fake


def _sent(fake: ReadingAI) -> dict[str, Any]:
    _, user, _ = next(s for s in fake.sent if '"entities"' in s[1])
    return dict(json.loads(user.removeprefix("<data>\n").removesuffix("\n</data>"))["entities"])


async def test_the_ai_gets_what_the_integration_calls_it_never_a_person_s_name(
    tmp_path: Path,
) -> None:
    """The child lock and the relay go with the integration's names; the switch a
    person named goes without any name, and "Emma" is nowhere."""
    hm, fake = await _read(tmp_path)
    sent = _sent(fake)
    names = {facts.get("name") for facts in sent.values()}
    assert {"Child Lock", "Output 1"} <= names
    assert all("Emma" not in json.dumps(facts) for facts in sent.values())
    assert any(facts.get("name") is None and facts["domain"] == "switch" for facts in sent.values())
    await hm.stop()


async def test_a_child_lock_is_a_setting_at_once_and_never_a_switch(tmp_path: Path) -> None:
    """The AI says the child lock is a setting: from then on it is one – also in the room, with
    "van AI" – and it is not asked again as a switch to use."""
    hm, fake = await _read(tmp_path)
    assert hm.world is not None and hm.gate is not None
    assert hm.world.functions[LOCK] == "setting"
    assert hm.world.function_sources[LOCK] == "ai"
    assert LOCK not in hm.gate.state.roles
    switches = [
        json.loads(u.removeprefix("<data>\n").removesuffix("\n</data>")) for _, u, _ in fake.sent
    ]
    asked_as_switch = [d for d in switches if "switches" in d]
    assert all(len(d["switches"]) <= 2 for d in asked_as_switch)  # the lock is not among them
    room = next(r for r in views.rooms(hm, "nl", 3) if r["id"] == "kitchen")
    lock = next(i for i in room["devices"] if i["id"] == LOCK)
    assert lock["function"] == "instelling" and lock["fromAi"] and lock["kind"] == "setting"
    await hm.stop()


async def test_a_control_from_ai_waits_for_klopt_before_acting(tmp_path: Path) -> None:
    """The AI says the relay powers an appliance: HouseMind watches it as such at once, but
    proposing or doing waits for an administrator's Klopt."""
    hm, _fake = await _read(tmp_path)
    assert hm.world is not None
    assert hm.world.functions[POWER] == "appliance_power"
    assert not counts_for_acting("appliance_power", hm.world.function_sources[POWER])
    page = views.house_knowledge(hm, "nl")
    rows = [r for g in by_device(page) for r in g["items"]]  # per device (E-432)
    row = next(r for r in rows if r["id"] == f"entity:{POWER}:function")
    assert row["what"] == "stroom van een apparaat" and row["label"] == "Wat het is"
    assert await hm.answer_knowledge(f"entity:{POWER}:function", True)
    assert hm.world.function_sources[POWER] == "person"
    assert counts_for_acting("appliance_power", hm.world.function_sources[POWER])
    await hm.stop()


async def test_not_sure_enough_is_not_known_and_asked_again_later(tmp_path: Path) -> None:
    """An answer the AI is not sure of counts for nothing: the child lock stays unknown, and
    it is not asked again the same week."""
    hm, fake = await _read(tmp_path, sure=0.4)
    assert hm.world is not None
    assert hm.world.functions[LOCK] == "unknown"
    assert hm.knowledge.get("entity", LOCK, "function") is None
    before = len(fake.sent)
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    assert not any('"entities"' in u for _, u, _ in fake.sent[before:])
    await hm.stop()


class CuttingAI(ReadingAI):
    """An AI whose answer for more than ten entities does not fit: cut off."""

    def __init__(self) -> None:
        super().__init__()
        self.sizes: list[int] = []

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        data = json.loads(user.removeprefix("<data>\n").removesuffix("\n</data>"))
        if "entities" not in data:
            return await super().complete(system, user, model, max_tokens)
        self.sizes.append(len(data["entities"]))
        if len(data["entities"]) > 10:
            return AIResult(ok=False, text='{"functions": {"entity_1"', error="cut_off")
        reply = {
            "functions": {
                label: {"function": "device_state", "certainty": 0.9, "why": "Een stand."}
                for label in data["entities"]
            }
        }
        return AIResult(ok=True, text=json.dumps(reply), input_tokens=900, output_tokens=300)


async def _many_relays(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    """A relay board with 24 outputs nothing says anything about."""
    ha.model.devices["device.board"] = Device("device.board", "Relaisbord", "garage", "relays")
    for i in range(1, 25):
        output = Entity(
            f"switch.board_output_{i}",
            f"b{i}",
            f"Uitgang {i}",
            device_id="device.board",
            original_name=f"Output {i}",
        )
        await _add(ha, clock, output)


async def test_an_answer_that_did_not_fit_is_asked_again_in_smaller_groups(
    tmp_path: Path,
) -> None:
    """In the real house the answer for 40 entities was cut off and counted for nothing. Now a
    group whose answer does not fit is asked again at once, half as big – and those answers
    count."""
    hm, _clock, _ha = await _start(tmp_path, setup=_many_relays)
    fake = CuttingAI()
    await enable(hm, fake)
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    assert fake.sizes[:2] == [20, 10]
    said = [f for f in hm.knowledge.of("entity", "function") if f.source == "ai"]
    assert len(said) >= 10
    await hm.stop()


LED = "light.bedroom_radar_led"


class DoubtAI(ReadingAI):
    """An AI that, where the rules doubt, says a light is a status light when the integration
    calls it a LED – and a lamp when it does not know what it is called."""

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        data = json.loads(user.removeprefix("<data>\n").removesuffix("\n</data>"))
        if "questions" not in data:
            return await super().complete(system, user, model, max_tokens)
        self.sent.append((system, user, model))
        picks = {
            label: {
                "pick": "yes" if q.get("device", {}).get("name") == "LED" else "no",
                "certainty": 0.9,
                "why": "Zo heet het.",
            }
            for label, q in data["questions"].items()
        }
        return AIResult(
            ok=True, text=json.dumps({"doubts": picks}), input_tokens=9, output_tokens=9
        )


async def _radar(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    ha.model.devices["device.radar"] = Device("device.radar", "Radar", "bedroom", "esphome")
    radar = {"device_id": "device.radar"}
    await _add(ha, clock, Entity(LED, "rl", "LED", original_name="LED", **radar), "on")
    sensor = Entity("binary_sensor.bedroom_radar", "rr", "Radar", device_class="occupancy", **radar)
    await _add(ha, clock, sensor)


async def test_an_old_guess_without_the_name_is_asked_once_more(tmp_path: Path) -> None:
    """In the real house the AI had called a sensor's LED "a lamp", without its name. Now the
    doubt goes again, with what the integration calls it: a status light – and not again."""
    hm, _clock, _ha = await _start(tmp_path, setup=_radar)
    old = {"yes": False, "rules": True, "why": None}
    await hm.knowledge.suggest(
        "entity", LED, "status_led", old, 0.6, hm.clock.now(), None, "ai", again=True
    )
    fake = DoubtAI()
    await enable(hm, fake)
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    finding = hm.knowledge.get("entity", LED, "status_led")
    assert finding is not None and finding.value["yes"] is True and finding.value["named"]
    asked = sum('"questions"' in u for _, u, _ in fake.sent)
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    assert sum('"questions"' in u for _, u, _ in fake.sent) == asked
    await hm.stop()


async def _named_after_a_room(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    """An older integration that puts the room in its own name for an entity."""
    await _oven(ha, clock)
    room = ha.model.areas["kitchen"].name
    relay = Entity(
        "switch.kitchen_old_relay",
        "r9",
        f"{room} Relay",
        area_id="kitchen",
        original_name=f"{room} Relay",
    )
    await _add(ha, clock, relay)


async def test_a_room_in_the_integration_s_name_stays_home(tmp_path: Path) -> None:
    """What the integration calls an entity goes along, but a room's name in it becomes a
    label: "Relay" goes, the room's name does not."""
    hm, _clock, _ha = await _start(tmp_path, setup=_named_after_a_room)
    fake = ReadingAI()
    await enable(hm, fake)
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    room = hm.model.areas["kitchen"].name
    names = [facts.get("name", "") for facts in _sent(fake).values()]
    relay = next(n for n in names if n.endswith("Relay"))
    assert room not in relay and relay.startswith("kitchen_")
    assert "Child Lock" in names
    await hm.stop()


class UnsureDoubtAI(DoubtAI):
    """An AI that, asked again with the name, is not sure."""

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        data = json.loads(user.removeprefix("<data>\n").removesuffix("\n</data>"))
        if "questions" not in data:
            return await super().complete(system, user, model, max_tokens)
        self.sent.append((system, user, model))
        picks = {label: {"pick": "unknown", "certainty": 0.3} for label in data["questions"]}
        return AIResult(
            ok=True, text=json.dumps({"doubts": picks}), input_tokens=9, output_tokens=9
        )


async def test_an_old_guess_the_ai_is_no_longer_sure_of_goes_back_to_the_rules(
    tmp_path: Path,
) -> None:
    """In the real house, asked again with the name "LED", the AI was not sure. Its old guess
    "a lamp" then no longer stands: the rules' own guess, a status light, does."""
    hm, _clock, _ha = await _start(tmp_path, setup=_radar)
    old = {"yes": False, "rules": True, "why": None}
    await hm.knowledge.suggest(
        "entity", LED, "status_led", old, 0.6, hm.clock.now(), None, "ai", again=True
    )
    await enable(hm, UnsureDoubtAI())
    assert hm.read_house_soon(now=True)
    await hm.ai_jobs.idle()
    finding = hm.knowledge.get("entity", LED, "status_led")
    assert finding is not None and finding.source == "rules" and finding.value["yes"] is True
    page = views.house_knowledge(hm, "nl")
    row = next(r for r in to_confirm(page) if r["id"] == f"entity:{LED}:status_led")
    assert row["what"] == "een statuslampje van een apparaat, geen gewone lamp"
    await hm.stop()
