"""Labels and categories from Home Assistant as a hint (E-345, B-218 – B-221): read with the
house, a hint only where HouseMind says "other", operating only after an administrator's yes, one
question per label for system devices, and in the lists. Each test fails without it; what it
checks is in its docstring."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from housemind.brain.notify import Kind
from housemind.core.clock import SimulatedClock
from housemind.core.model import Category, Device, Entity, Label, State
from housemind.demo.houses import load_demo_house
from housemind.ha.translate import build_model
from housemind.knowledge.labels import SYSTEM, entity_hint, hint, load_label_hints
from housemind.knowledge.matcher import recognise
from housemind.parts.labels import YES_NO
from housemind.parts.thinking import MODEL_SETTLE
from housemind.runtime import HouseMind, Paths
from housemind.server import views

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
HINTS = load_label_hints([ROOT / "knowledge"])


def test_a_label_points_to_one_part_in_any_language() -> None:
    """The name (any language, with or without accents) or the pictogram says the part; a
    label that fits two parts, or none, gives no hint; Home Assistant itself is 'system'."""
    assert hint(HINTS, "Lights") == "lighting"
    assert hint(HINTS, "Verlichting") == "lighting"
    assert hint(HINTS, "Éclairage") == "lighting"
    assert hint(HINTS, "Beleuchtung") == "lighting"
    assert hint(HINTS, "Woonkamer", "mdi:curtains") == "covers"
    assert hint(HINTS, "Media (TV, Audio)") == "media"
    assert hint(HINTS, "Lights and curtains") is None
    assert hint(HINTS, "Office") is None
    assert hint(HINTS, "Home Assistant") == SYSTEM
    assert hint(HINTS, "Netwerk") == SYSTEM


def test_the_registries_bring_labels_and_categories() -> None:
    """Home Assistant's label and category registries come into the house model, and every
    entity, device and room keeps its own (B-218)."""
    model = build_model(
        [],
        [{"area_id": "hall", "name": "Hal", "labels": ["lights"]}],
        [{"id": "d1", "name": "Plug", "labels": ["network"]}],
        [
            {
                "entity_id": "switch.plug",
                "device_id": "d1",
                "labels": ["lights"],
                "categories": {"automation": "c1"},
            }
        ],
        {},
        [{"label_id": "lights", "name": "Lights", "icon": "mdi:lightbulb"}],
        {"automation": [{"category_id": "c1", "name": "Curtains"}]},
    )
    assert model.labels["lights"] == Label("lights", "Lights", "mdi:lightbulb")
    assert model.categories["c1"] == Category("c1", "automation", "Curtains", None)
    assert model.entities["switch.plug"].labels == ("lights",)
    assert model.entities["switch.plug"].categories == {"automation": "c1"}
    assert model.devices["d1"].labels == ("network",) and model.areas["hall"].labels == ("lights",)


async def _house(tmp_path: Path, labelled: bool = True) -> tuple[HouseMind, Any]:
    """A house with a plug (a switch) whose labels say lamp, a light labelled Curtains, and a
    router on a device with the label 'Home Assistant'. Also, labelled Lights: a bulb with its
    own 'Niet storen' switch, a light program with four switches and a relay with one; and,
    labelled 'Home Assistant', a curtain motor and an inverter whose battery the energy setup
    uses – as in a real house, where that label is on some 70 devices."""
    clock = SimulatedClock(EVENING)
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    if labelled:
        ha.model.labels.update(
            {
                "lights": Label("lights", "Lights", "mdi:lightbulb"),
                "curtains": Label("curtains", "Curtains", "mdi:curtains"),
                "ha": Label("ha", "Home Assistant", "hacs:hacs"),
            }
        )
        ha.model.devices["router"] = Device("router", "Router", labels=("ha",))
    more: tuple[tuple[str, str, tuple[str, ...], str | None], ...] = ()
    if labelled:
        for device_id, name, label in (
            ("bulb", "Lamp", "lights"),
            ("program", "Lichtprogramma", "lights"),
            ("relay", "Relais", "lights"),
            ("motor", "Gordijnmotor", "ha"),
            ("inverter", "Omvormer", "ha"),
        ):
            ha.model.devices[device_id] = Device(device_id, name, labels=(label,))
        more = (
            ("light.bulb", "Lamp", (), "bulb"),
            ("switch.bulb_do_not_disturb", "Niet storen", (), "bulb"),
            ("switch.program", "Lichtprogramma", (), "program"),
            ("switch.program_sleep", "Slaapstand", (), "program"),
            ("switch.program_brightness", "Helderheid aanpassen", (), "program"),
            ("switch.program_colour", "Kleur aanpassen", (), "program"),
            ("switch.garden_relay", "Tuinrelais", (), "relay"),
            ("cover.motor", "Gordijn", (), "motor"),
            ("select.inverter_mode", "Batterijstand", (), "inverter"),
            ("sensor.inverter_soc", "Batterij", (), "inverter"),
        )
    for entity_id, name, labels, device in (
        ("switch.reading_plug", "Leesplug", ("lights",) if labelled else (), None),
        ("switch.router_restart", "Router herstarten", (), "router" if labelled else None),
        *more,
    ):
        entity = Entity(entity_id, f"reg-{entity_id}", name, area_id="living", device_id=device)
        await ha.add_entity(
            replace(entity, labels=labels), State(entity_id, "off", {}, clock.now())
        )
    if labelled:
        light = next(e for e in ha.model.entities.values() if e.domain == "light")
        ha.model.entities[light.entity_id] = replace(light, labels=("curtains",))
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    return hm, ha


async def test_a_label_fills_in_only_what_housemind_calls_other(tmp_path: Path) -> None:
    """The plug HouseMind calls 'other' gets Verlichting from its label, with the label named;
    a light labelled 'Curtains' stays Verlichting: its own kind comes first (B-220)."""
    hm, _ha = await _house(tmp_path)
    rows = {row["id"]: row for row in views.devices(hm, "nl")}
    plug = rows["switch.reading_plug"]
    assert plug["part"] == "lighting" and plug["labelHint"] == "Lights"
    assert plug["labels"] == ["Lights"]
    light = next(
        r for r in rows.values() if r["typeKey"] == "light" and r["labels"] == ["Curtains"]
    )
    assert light["part"] == "lighting" and light["labelHint"] is None
    assert "label Lights" in hm.why_not("switch.reading_plug", "nl")
    await hm.stop()


async def test_a_switch_labelled_lamp_becomes_a_lamp_only_after_a_yes(tmp_path: Path) -> None:
    """The label never unlocks anything (E-109): the plug stays a plain switch until an
    administrator says yes to 'Is het een lamp?'; then it is a lamp. A no is never asked
    again."""
    hm, _ha = await _house(tmp_path)
    assert hm.gate is not None and hm.world is not None
    plug = hm.model.entities["switch.reading_plug"]
    before = recognise(hm.catalog, plug, hm.gate.state.roles.get(plug.entity_id)).kind
    await hm._ask_labels(hm.world.snapshot(hm.clock.now()))
    assert "label_lamp:switch.reading_plug" in hm._raised
    assert hm.gate.state.roles.get(plug.entity_id) is None  # asking changes nothing yet
    await hm.answer_label("label_lamp:switch.reading_plug", True)
    after = recognise(hm.catalog, plug, hm.gate.state.roles.get(plug.entity_id)).kind
    assert after is not None and after != before and after.match.role == "light"
    await hm.stop()
    # Kept over a restart.
    again = HouseMind(hm.clock, hm.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    assert again.gate is not None and again.gate.state.roles.get(plug.entity_id) == "light"
    await again.stop()


async def test_a_no_to_the_lamp_is_not_asked_again(tmp_path: Path) -> None:
    """After a no the plug stays as it was and the question does not come back."""
    hm, _ha = await _house(tmp_path)
    assert hm.gate is not None and hm.world is not None
    snapshot = hm.world.snapshot(hm.clock.now())
    await hm._ask_labels(snapshot)
    await hm.answer_label("label_lamp:switch.reading_plug", False)
    assert "label_lamp:switch.reading_plug" not in hm._raised
    await hm._ask_labels(snapshot)
    assert "label_lamp:switch.reading_plug" not in hm._raised
    assert hm.gate.state.roles.get("switch.reading_plug") is None
    await hm.stop()


async def test_a_system_label_asks_once_to_leave_its_devices_alone(tmp_path: Path) -> None:
    """Devices with the label 'Home Assistant' get one question; a yes puts what HouseMind
    could operate on them on 'met rust laten', also for a device that gets the label later."""
    hm, ha = await _house(tmp_path)
    assert hm.gate is not None and hm.world is not None
    await hm._ask_labels(hm.world.snapshot(hm.clock.now()))
    assert "label_system:ha" in hm._raised
    await hm.answer_label("label_system:ha", True)
    assert "switch.router_restart" in hm.gate.state.excluded
    assert "switch.reading_plug" not in hm.gate.state.excluded
    entity = Entity("switch.router_led", "reg-led", "Router led", device_id="router")
    await ha.add_entity(entity, State("switch.router_led", "on", {}, hm.clock.now()))
    await hm.clock.advance(MODEL_SETTLE.total_seconds() + 1)  # HouseMind looks again when quiet
    await hm.tick()
    assert "switch.router_led" in hm.gate.state.excluded
    await hm.stop()


async def test_without_labels_nothing_changes(tmp_path: Path) -> None:
    """A house without labels or categories: no questions, no hints, the parts as before."""
    hm, _ha = await _house(tmp_path, labelled=False)
    assert hm.world is not None
    await hm._ask_labels(hm.world.snapshot(hm.clock.now()))
    assert not [t for t in hm._raised if t.startswith("label_")]
    rows = {row["id"]: row for row in views.devices(hm, "nl")}
    assert rows["switch.reading_plug"]["part"] == "other"
    assert rows["switch.reading_plug"]["labelHint"] is None
    assert hm.labels_used() == 0
    assert entity_hint(hm.model, HINTS, hm.model.entities["switch.reading_plug"]) is None
    await hm.stop()


async def test_the_questions_have_a_plain_yes_and_no(tmp_path: Path) -> None:
    """The buttons of 'Is het een lamp?' said 'Ja, zet uit / Nee, laat aan' – those of the
    automation question. Now a question has a plain Ja and Nee (in the app and on a phone), the
    automation question keeps its own words, and only administrators see the label questions."""
    hm, _ha = await _house(tmp_path)
    assert hm.world is not None
    t = hm.languages.text
    assert (t("nl", "notify.answer.yes"), t("nl", "notify.answer.no")) == ("Ja", "Nee")
    assert t("nl", "notify.answer.turn_off") == "Ja, zet uit"
    await hm._ask_labels(hm.world.snapshot(hm.clock.now()))
    rows = [dict(r) for r in await hm.notifications()]
    lamp = next(r for r in rows if str(r["topic"]).startswith("label_lamp:"))
    card = views.notice_item(hm, "nl", lamp, admin=True)
    assert card is not None and card["actions"] == ["yes", "no"] and "labels" not in card
    assert views.notice_item(hm, "nl", lamp, admin=False) is None
    automation = {**lamp, "topic": "automation:automation.x"}
    shown = views.notice_item(hm, "nl", automation, admin=True)
    assert shown is not None and shown["labels"] == {"yes": "Ja, zet uit", "no": "Nee, laat aan"}
    await hm.stop()


async def test_a_setting_switch_on_a_lamp_is_never_asked_about(tmp_path: Path) -> None:
    """Through a device's label only its one switch counts, on a device without a lamp: a
    bulb's 'Niet storen' and a light program's four switches never get 'Is het een lamp?' – in
    a real house 'Adapt Brightness' was asked. A relay with one switch is asked, after the plug
    that has the label itself."""
    hm, _ha = await _house(tmp_path)
    assert hm.world is not None
    snapshot, asked = hm.world.snapshot(hm.clock.now()), []
    for _ in range(10):
        await hm._ask_labels(snapshot)
        topic = next((t for t in hm._raised if t.startswith("label_lamp:")), None)
        if topic is None:
            break
        asked.append(topic.split(":", 1)[1])
        await hm.answer_label(topic, False)
    assert asked == ["switch.reading_plug", "switch.garden_relay"]
    await hm.stop()


async def test_a_question_that_no_longer_fits_is_withdrawn(tmp_path: Path) -> None:
    """A lamp question already standing about a setting switch (asked before this rule) goes
    away by itself; the next question is about a real candidate."""
    hm, _ha = await _house(tmp_path)
    assert hm.world is not None
    snapshot = hm.world.snapshot(hm.clock.now())
    params = {"device": "Helderheid aanpassen", "label": "Lights"}
    await hm._raise(
        Kind.LEARNING,
        "label_lamp:switch.program_brightness",
        "notify.label_lamp",
        params,
        snapshot,
        concerns="admins",
        actions=YES_NO,
    )
    await hm._ask_labels(snapshot)
    assert "label_lamp:switch.program_brightness" not in hm._raised
    assert "label_lamp:switch.reading_plug" in hm._raised
    rows = [dict(r) for r in await hm.notifications()]
    assert not [r for r in rows if r["topic"] == "label_lamp:switch.program_brightness"]
    await hm.stop()


async def test_a_system_label_leaves_alone_only_what_housemind_does_not_know(
    tmp_path: Path,
) -> None:
    """'Home Assistant' is on the router, a curtain motor and an inverter. A yes leaves only the
    router's switch alone: the curtain is a curtain, and the energy setup uses the inverter's
    battery (its mode and its meter). The question reads as a sentence, not as a text key – in
    a real house it showed 'notify.label_system.one'."""
    hm, _ha = await _house(tmp_path)
    assert hm.gate is not None and hm.world is not None and hm.energy is not None
    hm.energy.setup.battery_soc = "sensor.inverter_soc"
    hm.gate.state.roles["select.inverter_mode"] = "battery_mode"
    await hm._ask_labels(hm.world.snapshot(hm.clock.now()))
    rows = [dict(r) for r in await hm.notifications()]
    row = next(r for r in rows if r["topic"] == "label_system:ha")
    card = views.notice_item(hm, "nl", row, admin=True)
    assert card is not None and card["text"].startswith(
        "Het apparaat met het label Home Assistant lijkt geen huisapparaat."
    )
    assert "notify." not in views.notice_item(hm, "en", row, admin=True)["text"]  # type: ignore[index]
    await hm.answer_label("label_system:ha", True)
    excluded = hm.gate.state.excluded
    assert "switch.router_restart" in excluded
    assert not {"cover.motor", "select.inverter_mode", "sensor.inverter_soc"} & excluded
    await hm.stop()


async def test_what_a_label_left_alone_can_come_back(tmp_path: Path) -> None:
    """An administrator who takes a device back from 'met rust laten' keeps it back; one that a
    part starts to use later (here: an appliance to start) comes back by itself."""
    hm, ha = await _house(tmp_path)
    assert hm.gate is not None and hm.world is not None
    led = Entity("switch.router_led", "reg-led", "Router led", device_id="router")
    await ha.add_entity(led, State("switch.router_led", "on", {}, hm.clock.now()))
    await hm.clock.advance(MODEL_SETTLE.total_seconds() + 1)
    await hm.tick()
    await hm.answer_label("label_system:ha", True)
    assert {"switch.router_restart", "switch.router_led"} <= hm.gate.state.excluded
    taken_back = sorted(hm.gate.state.excluded - {"switch.router_led"})
    await hm.set_house_setting("excluded", taken_back)
    await hm._apply_label_roles()
    assert "switch.router_led" not in hm.gate.state.excluded  # the administrator's choice
    hm.gate.state.roles["switch.router_restart"] = "appliance_start"
    await hm._apply_label_roles()
    assert "switch.router_restart" not in hm.gate.state.excluded
    await hm.stop()
