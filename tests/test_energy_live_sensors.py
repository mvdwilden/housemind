"""Energy "Nu en vandaag" reads the sensors that have a value, with the right meaning.

Found in the real house: the solar panels' device also has a grid, a consumption and a storage
power sensor, all "unknown", and HouseMind read the first of them as the sun. The grid was only
there per phase, and the home battery's own power sensor was offline while the system it is
part of gave the battery's power. The screen showed no sun, no grid and no house.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, HouseModel, State
from housemind.ha.adapter import HAConfig
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server.views import energy

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 2, 11, 0, tzinfo=UTC)

# entity id, device, name, device class, unit, state, integration
SENSORS: list[tuple[str, str, str, str, str, str, str]] = [
    ("sensor.solarbox_produced_energy", "inv", "Opgewekt", "energy", "kWh", "5000", "a"),
    ("sensor.solarbox_grid_power", "inv", "Netstroom", "power", "kW", "unknown", "a"),
    ("sensor.solarbox_current_power", "inv", "Huidig vermogen", "power", "W", "1150", "a"),
    ("sensor.solarbox_power_consumption", "inv", "Stroomverbruik", "power", "kW", "unknown", "a"),
    ("sensor.solarbox_solar_power", "inv", "Zonne-energie", "power", "kW", "1.2", "a"),
    ("sensor.solarbox_stored_power", "inv", "Opslagvermogen", "power", "kW", "unknown", "a"),
    ("sensor.solarbox_storage_level", "inv", "Opslagniveau", "battery", "%", "unknown", "a"),
    ("sensor.ess_system_grid_l1", "sys", "system grid L1", "power", "W", "40", "b"),
    ("sensor.ess_system_grid_l2", "sys", "system grid L2", "power", "W", "100", "b"),
    ("sensor.ess_system_grid_l3", "sys", "system grid L3", "power", "W", "3200", "b"),
    ("sensor.ess_system_pvongrid_l1", "sys", "system pvongrid L1", "power", "W", "0", "b"),
    ("sensor.ess_grid_export_limit", "sys", "grid export limit", "power", "W", "5000", "b"),
    ("sensor.ess_system_charger_power", "sys", "system charger power", "power", "W", "0", "b"),
    ("sensor.ess_system_battery_power", "sys", "system battery power", "power", "W", "-500", "b"),
    ("sensor.ess_system_battery_soc", "sys", "system battery soc", "battery", "%", "16", "b"),
    ("sensor.ess_battery_soc", "bat", "Battery SOC", "battery", "%", "16", "b"),
    ("sensor.ess_battery_power", "bat", "Power", "power", "W", "unavailable", "b"),
    ("sensor.ess_battery_charged", "bat", "Charged energy", "energy", "kWh", "800", "b"),
    ("sensor.ess_battery_discharged", "bat", "Discharged energy", "energy", "kWh", "700", "b"),
]
DEVICES = {"inv": "Solarbox", "sys": "settings", "bat": "battery"}
PREFS: dict[str, Any] = {
    "energy_sources": [
        {"type": "solar", "stat_energy_from": "sensor.solarbox_produced_energy"},
        {
            "type": "battery",
            "stat_energy_from": "sensor.ess_battery_discharged",
            "stat_energy_to": "sensor.ess_battery_charged",
        },
    ],
    "device_consumption": [],
}


async def started(tmp_path: Path) -> tuple[HouseMind, FakeHomeAssistant]:
    clock = SimulatedClock(T0)
    model = HouseModel()
    for key, name in DEVICES.items():
        model.devices[f"dev.{key}"] = Device(f"dev.{key}", name, None, "demo")
    states: dict[str, State] = {}
    for entity_id, device, name, kind, unit, state, entry in SENSORS:
        model.entities[entity_id] = Entity(
            entity_id=entity_id,
            registry_id=f"demo-{entity_id}",
            name=name,
            device_id=f"dev.{device}",
            device_class=kind,
            unit=unit,
            config_entry_id=entry,
            original_name=name,
        )
        attrs = {
            "friendly_name": f"{DEVICES[device]} {name}",
            "device_class": kind,
            "unit_of_measurement": unit,
        }
        states[entity_id] = State(entity_id, state, attrs, T0)
    config = HAConfig(version="2026.10.0", language="nl", country="NL")
    ha = FakeHomeAssistant(clock, model, states, config, energy=PREFS)
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    return hm, ha


async def test_now_shows_the_sun_the_grid_the_house_and_the_battery(tmp_path: Path) -> None:
    hm, _ = await started(tmp_path)
    view = await energy(hm, "nl", 3)
    assert view is not None
    live = view["live"]
    assert live["solar"] == 1200  # the solar power in kW, not the grid's "unknown"
    assert live["grid"] == 3340  # the three phases added up
    assert [(b["pct"], b["watts"]) for b in live["batteries"]] == [(16, -500)]
    assert live["house"] == 3340 + 1200 + 500  # the battery gives 500 W to the house
    await hm.stop()


async def test_a_sensor_without_a_value_gives_way_to_the_next(tmp_path: Path) -> None:
    hm, ha = await started(tmp_path)
    await ha.simulate("sensor.solarbox_solar_power", "unknown")
    await ha.simulate("sensor.ess_system_grid_l2", "unavailable")
    view = await energy(hm, "nl", 3)
    assert view is not None
    assert view["live"]["solar"] == 1150  # the inverter's own current power
    assert view["live"]["grid"] is None  # never two of three phases as the whole grid
    assert view["live"]["house"] is None
    await ha.simulate("sensor.ess_system_grid_l2", "100")
    await ha.simulate("sensor.solarbox_grid_power", "2.5", {"unit_of_measurement": "kW"})
    view = await energy(hm, "nl", 3)
    assert view is not None and view["live"]["grid"] == 2500  # one total before phases
    await hm.stop()


async def test_today_is_measured_from_the_live_power(tmp_path: Path) -> None:
    hm, _ = await started(tmp_path)
    assert hm.energy is not None and hm.world is not None
    meter = hm.energy.meter
    meter.sample(hm.energy.setup, hm.world.states)
    quarter = meter.close(hm.energy.setup, hm.world.states, T0, 0.25, None)
    assert round(quarter.import_kwh, 3) == round(3340 * 0.25 / 1000, 3)
    assert round(quarter.battery_out_kwh, 3) == round(500 * 0.25 / 1000, 3)
    assert quarter.export_kwh == 0.0
    await hm.stop()


async def test_a_battery_in_the_inverter_never_shows_the_sun(tmp_path: Path) -> None:
    hm, ha = await started(tmp_path)
    await ha.simulate("sensor.solarbox_storage_level", "50")
    view = await energy(hm, "nl", 3)
    assert view is not None
    batteries = {b["pct"]: b["watts"] for b in view["live"]["batteries"]}
    assert batteries == {16: -500, 50: None}  # its own storage power, not the 1.2 kW sun
    await hm.stop()
