"""Round 1 of the device audit: water, moisture, frost and the emergency state. Each test
fails without the fix; the finding it comes from is in its docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.water import flow_sensors
from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, HouseModel, State
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.safety.emergency import Emergency
from housemind.safety.gate import ActionRequest, Layer, Outcome

ROOT = Path(__file__).resolve().parents[1]
EVENING = datetime(2026, 10, 19, 20, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


def test_only_tap_water_flow_counts() -> None:
    """A ventilation unit's air flow counted as water running: a leak every day."""
    model = HouseModel()
    model.devices["hrv"] = Device("hrv", "Ventilation", None)
    model.devices["meter"] = Device("meter", "Water meter", None)
    model.devices["probe"] = Device("probe", "Flow probe", None)
    model.entities["fan.hrv"] = Entity("fan.hrv", "r1", "Ventilation", device_id="hrv")
    for eid, device, unit in (
        ("sensor.hrv_air_flow", "hrv", "m³/h"),
        ("sensor.meter_flow", "meter", "m³/h"),
        ("sensor.meter_total", "meter", "m³"),
        ("sensor.probe_flow", "probe", "L/min"),
    ):
        cls = "water" if eid.endswith("total") else "volume_flow_rate"
        model.entities[eid] = Entity(
            eid, f"r-{eid}", eid, device_id=device, device_class=cls, unit=unit
        )
    flows = flow_sensors(model, ["sensor.meter_total"])
    assert flows == ["sensor.meter_flow", "sensor.probe_flow"]  # never the air flow


async def _start(tmp_path: Path) -> tuple[HouseMind, FakeHomeAssistant, SimulatedClock]:
    clock = SimulatedClock(EVENING.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    ha.model.devices["weather"] = Device("weather", "Weerstation", "garden")
    ha.model.devices["freezer"] = Device("freezer", "Vriezer", "kitchen")
    extra = [
        (
            Entity(
                "valve.water_main", "r-valve", "Hoofdkraan", area_id="kitchen", device_class="water"
            ),
            "open",
        ),
        (
            Entity(
                "binary_sensor.hall_leak",
                "r-hl",
                "Lek hal",
                area_id="hall",
                device_class="moisture",
            ),
            "off",
        ),
        (
            Entity(
                "binary_sensor.rain",
                "r-rain",
                "Regensensor",
                area_id="hall",
                device_class="moisture",
            ),
            "off",
        ),
        (
            Entity(
                "binary_sensor.weather_wet",
                "r-ww",
                "Nat",
                device_id="weather",
                area_id="hall",
                device_class="moisture",
            ),
            "off",
        ),
        (
            Entity(
                "sensor.weather_rain",
                "r-wr",
                "Neerslag",
                device_id="weather",
                device_class="precipitation",
            ),
            "0",
        ),
        (
            Entity(
                "binary_sensor.freezer_cold",
                "r-fc",
                "Koud",
                device_id="freezer",
                device_class="cold",
            ),
            "off",
        ),
        (
            Entity(
                "binary_sensor.bedroom_frost",
                "r-bf",
                "Vorst",
                area_id="bedroom",
                device_class="cold",
            ),
            "off",
        ),
        (
            Entity(
                "binary_sensor.hidden_smoke",
                "r-hs",
                "Rook",
                area_id="hall",
                device_class="smoke",
                entity_category="diagnostic",
            ),
            "off",
        ),
        (
            Entity(
                "binary_sensor.hall_smoke",
                "r-hsm",
                "Rook hal",
                area_id="hall",
                device_class="smoke",
            ),
            "off",
        ),
        (
            Entity(
                "binary_sensor.kitchen_gas", "r-kg", "Gas", area_id="kitchen", device_class="gas"
            ),
            "off",
        ),
    ]
    for entity, value in extra:
        ha.model.entities[entity.entity_id] = entity
        ha.states[entity.entity_id] = State(entity.entity_id, value, {}, clock.now())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    return hm, ha, clock


async def _texts(hm: HouseMind, prefix: str) -> list[str]:
    out = []
    for n in await hm.notifications():
        if str(n["topic"]).startswith(prefix):
            out.append(hm.languages.text("nl", str(n["text_key"]), **json.loads(str(n["params"]))))
    return out


async def test_rain_and_plants_are_no_leak_a_real_leak_closes_the_valve(tmp_path: Path) -> None:
    """A rain or plant sensor closed the main water valve and froze the house."""
    hm, ha, _clock = await _start(tmp_path)
    await ha.simulate("binary_sensor.rain", "on")
    await ha.simulate("binary_sensor.weather_wet", "on")
    assert hm.status.emergency is None and ha.states["valve.water_main"].state == "open"
    await ha.simulate("binary_sensor.hall_leak", "on")
    assert hm.status.emergency is Emergency.LEAK
    assert ha.states["valve.water_main"].state == "closed"
    texts = await _texts(hm, "emergency:binary_sensor.hall_leak")
    assert texts == ["Lekkage gemeten in de Gang. Ik draai de hoofdkraan dicht."]
    await hm.stop()


async def test_no_valve_means_no_claim_that_it_is_closed(tmp_path: Path) -> None:
    """The message said "the main valve is closed" also when there was none."""
    hm, ha, _clock = await _start(tmp_path)
    del ha.model.entities["valve.water_main"]
    await hm._refresh_model()
    await ha.simulate("binary_sensor.hall_leak", "on")
    texts = await _texts(hm, "emergency:binary_sensor.hall_leak")
    assert texts == ["Lekkage gemeten in de Gang. Draai de hoofdkraan dicht als dat kan."]
    await hm.stop()


async def test_frost_warms_the_room_and_stops_nothing(tmp_path: Path) -> None:
    """A frost sensor froze every skill (the heating too); a freezer counted as frost."""
    hm, ha, _clock = await _start(tmp_path)
    await ha.simulate("binary_sensor.freezer_cold", "on")
    assert not await _texts(hm, "emergency:binary_sensor.freezer_cold")
    await ha.simulate("climate.bedroom", "heat", {"temperature": 6})
    await ha.simulate("binary_sensor.bedroom_frost", "on")
    assert hm.status.emergency is None  # the house carries on
    assert ha.states["climate.bedroom"].attributes["temperature"] == 8
    [text] = await _texts(hm, "emergency:binary_sensor.bedroom_frost")
    assert text.endswith("De verwarming daar staat op minstens 8 °C.")
    await hm.stop()


async def test_during_gas_nothing_else_switches_and_two_sensors_both_count(
    tmp_path: Path,
) -> None:
    """During a gas alarm energy and household actions still ran; a second sensor clearing
    ended the alarm while the first was still on; a diagnostic sensor counted."""
    hm, ha, _clock = await _start(tmp_path)
    await ha.simulate("binary_sensor.hidden_smoke", "on")
    assert hm.status.emergency is None
    await ha.simulate("binary_sensor.kitchen_gas", "on")
    assert hm.status.emergency is Emergency.GAS
    assert hm.gate is not None
    decision = await hm.gate.submit(
        ActionRequest(
            Layer.PLANNER, "light.living_reading", "light.turn_on", {}, created_at=hm.clock.now()
        )
    )
    assert decision.outcome is not Outcome.EXECUTED and decision.reason == "alarm"
    await ha.simulate("binary_sensor.hall_smoke", "on")
    assert hm.status.emergency is Emergency.SMOKE  # the most severe one
    await ha.simulate("binary_sensor.hall_smoke", "off")
    assert hm.status.emergency is Emergency.GAS  # the gas is still there
    await ha.simulate("binary_sensor.kitchen_gas", "off")
    assert hm.status.emergency is None and not hm.gate.state.alarm
    await hm.stop()


async def test_a_sensor_lost_during_its_alarm_is_released_after_two_hours(
    tmp_path: Path,
) -> None:
    """A detector that went unavailable during its alarm kept the house frozen forever."""
    hm, ha, clock = await _start(tmp_path)
    await ha.simulate("binary_sensor.hall_smoke", "on")
    await ha.simulate("binary_sensor.hall_smoke", "unavailable")
    assert hm.status.emergency is Emergency.SMOKE  # still careful
    await clock.advance(int(timedelta(hours=2, minutes=1).total_seconds()))
    await hm.tick()
    assert hm.status.emergency is None
    assert await _texts(hm, "emergency_lost:binary_sensor.hall_smoke")
    await hm.stop()
