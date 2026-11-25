"""A restart is no switching (E-459). The administrator (04-11-2026), about a smart plug with his
bed heating on it that HouseMind listed among the lamps of his bedroom: "'Bedverwarming' is
gekoppeld aan een smartswitch stopcontact … in mijn HA is het inderdaad bedverwarming en geen
lamp/licht."

In the real house that plug never went on in ten days. Each time Home Assistant or the radio
restarted, it came back from unavailable to off – and so did a lamp in the same room, at the
same moment. HouseMind counted that as "went on and off together with a lamp, 14 of 15 times",
and so did it for two firmware switches of meters and a sensor's relay. Each test fails without
the change; what it checks is its docstring."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, Label
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind
from housemind.server import views
from tests.knowledge_page import derived
from tests.test_house_knowledge_derived import (
    RELAY,
    _add,
    _derive,
    _relay,
    _start,
    _went_with_the_hall_light,
)

BED = "switch.bed_heating"


async def _bed_heating(ha: FakeHomeAssistant, clock: SimulatedClock, label: bool = False) -> None:
    """A smart plug in the bedroom with the bed heating on it: the device's own switch, so
    Home Assistant gives it no name of its own. With ``label``, the device has the label
    'Heating', as the administrator's has."""
    if label:
        ha.model.labels["heating"] = Label("heating", "Heating", "mdi:radiator")
    ha.model.devices["device.bed"] = Device(
        "device.bed", "Bedverwarming", "bedroom", "mqtt", labels=("heating",) if label else ()
    )
    entity = Entity(BED, f"reg-{BED}", "Bedverwarming", device_id="device.bed", area_id="bedroom")
    await _add(ha, clock, entity)


async def _house(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
    await _bed_heating(ha, clock)
    await _relay(ha, clock)


def _restarted(ha: FakeHomeAssistant, now: datetime) -> None:
    """Last week Home Assistant restarted eight times: the plug and the bedroom lamp went
    unavailable and came back off half a minute later, both at the same moment. The plug was
    never switched."""
    ha.record(BED, "off", now - timedelta(days=6, hours=12))  # the recorder goes back a week
    ha.record("light.bedroom", "off", now - timedelta(days=6, hours=12))
    for i in range(8):
        at = now - timedelta(days=6) + timedelta(hours=17 * i)
        for entity_id in (BED, "light.bedroom"):
            ha.record(entity_id, "unavailable", at)
            ha.record(entity_id, "unknown", at + timedelta(seconds=1))
            ha.record(entity_id, "off", at + timedelta(seconds=30))


def _bed_row(hm: HouseMind) -> dict[str, object]:
    return next(r for r in views.devices(hm, "nl") if r["id"] == BED)


async def test_a_restart_is_no_switching_so_the_bed_heating_is_no_lamp(tmp_path: Path) -> None:
    """The plug of the bed heating came back from a restart at the same moment as the bedroom
    lamp, eight times, and was never switched: HouseMind derives nothing from that. It is not
    under Afgeleid as a lamp, and Apparaten does not put it under lighting. The relay in the
    hall that really goes on and off with the hall light is still found."""
    hm, clock, ha = await _start(tmp_path, setup=_house)
    _restarted(ha, clock.now())
    _went_with_the_hall_light(ha, clock.now())
    await _derive(hm)
    assert hm.knowledge.get("entity", BED, "role") is None
    relay = hm.knowledge.get("entity", RELAY, "role")
    assert relay is not None and relay.value["role"] == "light" and relay.value["real"] is True
    names = [r["name"] for r in derived(views.house_knowledge(hm, "nl"))]
    assert "Bedverwarming" not in names and "Relais gang" in names
    row = _bed_row(hm)
    assert row["part"] != "lighting" and row["kind"] != "switch.light"
    await hm.stop()


async def test_a_guess_that_counted_restarts_is_looked_at_again_once(tmp_path: Path) -> None:
    """After the update, the lamps HouseMind derived before – when a restart still counted – are
    looked at again at once, not a day later: the bed heating is no lamp any more, the relay
    that really goes with the hall light is a lamp again. What a person said Klopt to stays."""

    async def setup(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
        await _house(ha, clock)
        await _add(ha, clock, Entity("switch.porch", "reg-porch", "Veranda", area_id="garden"))

    hm, clock, ha = await _start(tmp_path, setup=setup)
    assert hm.gate is not None
    _restarted(ha, clock.now())
    _went_with_the_hall_light(ha, clock.now())
    old = {"role": "light", "rule": "with_lamp", "facts": {"lamp": "light.bedroom", "pct": 100}}
    for entity_id in (BED, RELAY, "switch.porch"):
        await hm.knowledge.suggest(
            "entity", entity_id, "role", old, 0.93, clock.now(), None, "behaviour"
        )
    assert await hm.answer_knowledge("entity:switch.porch:role", True)
    hm._apply_knowledge()
    await hm.db.set_setting("behaviour_read", clock.now().isoformat())  # read today already
    assert _bed_row(hm)["kind"] == "switch.light"  # what the earlier version thought
    await _derive(hm)
    assert hm.knowledge.get("entity", BED, "role") is None
    assert hm.gate.state.roles.get(BED) is None
    row = _bed_row(hm)
    assert row["part"] != "lighting" and row["kind"] != "switch.light"
    relay = hm.knowledge.get("entity", RELAY, "role")
    assert relay is not None and relay.value.get("real") is True
    porch = hm.knowledge.get("entity", "switch.porch", "role")
    assert porch is not None and porch.status == "confirmed"
    await hm.stop()


async def test_a_plug_labelled_heating_is_no_lamp_even_when_it_goes_with_one(
    tmp_path: Path,
) -> None:
    """The bed heating's device has the label 'Heating' in Home Assistant. Even when it really
    goes on and off with the bedroom lamp every evening – one automation does both – HouseMind
    does not derive that it is a lamp: Home Assistant says what it is."""

    async def setup(ha: FakeHomeAssistant, clock: SimulatedClock) -> None:
        await _bed_heating(ha, clock, label=True)

    hm, clock, ha = await _start(tmp_path, setup=setup)
    now = clock.now()
    ha.record(BED, "off", now - timedelta(days=6, hours=12))
    for i in range(8):
        at = now - timedelta(days=6) + timedelta(hours=12 * i)
        state = "on" if i % 2 == 0 else "off"
        ha.record(BED, state, at)
        ha.record("light.bedroom", state, at + timedelta(seconds=2))
    await _derive(hm)
    assert hm.knowledge.get("entity", BED, "role") is None
    assert _bed_row(hm)["kind"] != "switch.light"
    await hm.stop()
