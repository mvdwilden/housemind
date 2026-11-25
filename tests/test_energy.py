"""Energy (R2): measurements, country rules, setup detection, prices, meter and profiles."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from housemind.core.clock import SimulatedClock
from housemind.core.model import Source, State
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)


async def start(tmp_path: Path, key: str = "energy_home") -> tuple[HouseMind, SimulatedClock, Any]:
    clock = SimulatedClock(T0)
    house = load_demo_house(key, clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    return hm, clock, house.ha


async def test_measurements_only_update_the_world(tmp_path: Path) -> None:
    """Power sensors may change every second in a real house: no event rows, no screen
    refresh per value – the screens get the new values once per tick."""
    hm, clock, ha = await start(tmp_path)
    assert hm.world is not None
    pushes: list[int] = []

    async def push() -> None:
        pushes.append(1)

    hm.on_update(push)
    for watt in range(100, 200):
        await ha.simulate("sensor.grid_power", str(watt), by=Source.PHYSICAL)
        await clock.advance(1)
    rows = await hm.db.fetchone("SELECT count(*) AS n FROM events")
    assert rows is not None and int(rows["n"]) == 0
    assert pushes == []
    assert hm.world.states["sensor.grid_power"].state == "199"
    await hm.tick()
    assert pushes == [1]
    await hm.stop()


def test_country_rules_as_data() -> None:
    from datetime import date

    from housemind.energy.country import load_country

    dirs = [ROOT / "knowledge" / "countries"]
    nl = load_country(dirs, "NL")
    assert nl.vat == 0.21 and nl.energy_tax_per_kwh > 0
    assert nl.net_metering(date(2026, 12, 31)) and not nl.net_metering(date(2027, 1, 1))
    assert round(nl.all_in(0.10), 4) == round((0.10 + nl.energy_tax_per_kwh) * 1.21, 4)
    be = load_country(dirs, "be")
    assert be.capacity_minimum_kw == 2.5 and be.vat == 0.06
    assert load_country(dirs, "DE").controllable_minimum_kw == 4.2
    assert load_country(dirs, "FR").vat == 0.20
    unknown = load_country(dirs, "ZZ")
    assert unknown.country == "ZZ" and unknown.vat == 0.0


def test_prices_are_read_from_any_list_shape_and_unit() -> None:
    from housemind.energy.prices import parse_series, per_quarter, unit_factor

    assert unit_factor("EUR/kWh") == 1.0
    assert unit_factor("ct/kWh") == 0.01
    assert unit_factor("€/MWh") == 0.001
    hourly = State(
        "sensor.price",
        "25.0",
        {
            "unit_of_measurement": "ct/kWh",
            "prices_today": [
                {"time": "2026-10-19T10:00:00+02:00", "price": 25.0},
                {"time": "2026-10-19T11:00:00+02:00", "price": 18.0},
            ],
        },
        T0,
    )
    slots = parse_series(hourly)
    assert [round(s.price, 2) for s in slots] == [0.25, 0.18]
    assert slots[0].end == slots[1].start  # end from the next start
    quarters = per_quarter(slots, slots[0].start, 8, None)
    assert quarters[:4] == [0.25] * 4 and quarters[4:8] == [0.18] * 4
    other = State(
        "sensor.price2",
        "0.2",
        {"forecast": [{"startsAt": "2026-10-19T08:00:00Z", "total": 0.31}], "other": [1, 2]},
        T0,
    )
    assert parse_series(other)[0].price == 0.31
    assert parse_series(State("sensor.x", "1", {}, T0)) == []


async def test_energy_setup_is_found_without_manual_work(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    assert hm.energy is not None
    setup = hm.energy.setup
    assert setup.grid_import == ["sensor.grid_import"] and setup.grid_export == [
        "sensor.grid_export"
    ]
    assert setup.price == "sensor.electricity_price"
    assert setup.grid_power == "sensor.grid_power" and setup.solar_power == "sensor.solar_power"
    assert setup.battery_soc == "sensor.home_battery"
    assert setup.battery_power == "sensor.home_battery_power"
    assert setup.charger_power == "sensor.charge_point_power"  # only read (E-363)
    assert "select.home_battery_mode" not in setup.controllable()
    assert "switch.charge_point" not in setup.controllable()
    assert setup.car_soc == "sensor.car_battery"
    assert setup.car_plugged == "binary_sensor.charge_point_plug"
    assert setup.export_limit == "number.solar_export_limit"
    assert setup.boiler == "water_heater.boiler" and setup.heat_pump == "climate.heat_pump"
    assert [a.start for a in setup.appliances] == ["button.washer_start"]
    await hm.stop()


async def test_a_house_without_energy_has_no_setup(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path, "apartment")
    assert hm.energy is not None and not hm.energy.setup.has_any
    await hm.stop()


async def test_a_simulated_day_is_metered_and_learned(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    from housemind.demo.energy_sim import EnergySim

    hm, clock, ha = await start(tmp_path)
    assert hm.energy is not None and hm.energy.profiles is not None
    sim = EnergySim(ha, ZoneInfo("Europe/Amsterdam"))
    await sim.step(clock.now())
    for _ in range(24 * 12):
        await clock.advance(300)
        await sim.step(clock.now())
        await hm.tick()
    days = await hm.energy.meter.days()  # type: ignore[union-attr]
    assert days and sum(d["import_kwh"] for d in days) > 3
    assert sum(d["solar_kwh"] for d in days) > 1
    total_cost = sum(d["cost"] for d in days)
    assert 0 < total_cost < 20  # an ordinary day, in euros
    assert hm.energy.profiles.known("house") >= 90
    slots = hm.energy.forecast(hm.world.states, clock.now())  # type: ignore[union-attr]
    assert len(slots) == 144
    known = [s for s in slots if s.buy is not None]
    assert known and all(0.05 < s.buy < 0.6 for s in known)  # all-in: tax and VAT added
    assert any(s.solar_w > 0 for s in slots)
    await hm.stop()


async def test_another_energy_system_is_watched_not_steered(tmp_path: Path) -> None:
    """E-68: an integration that steers the charger and the home battery itself."""
    from housemind.core.model import Device, Entity

    clock = SimulatedClock(T0)
    house = load_demo_house("energy_home", clock, "nl")
    model = house.ha.model
    model.devices["device.manager"] = Device(
        "device.manager", "Energiebeheer", "utility", "evcc_intg"
    )
    for entity_id, name in (
        ("select.manager_charging_mode", "Laadpaal modus"),
        ("switch.manager_battery_lock", "Thuisbatterij ontladen blokkeren"),
    ):
        model.entities[entity_id] = Entity(
            entity_id,
            f"r-{entity_id}",
            name,
            device_id="device.manager",
            area_id="utility",
            platform="evcc_intg",
        )
        house.ha.states[entity_id] = State(entity_id, "pv", {"options": ["pv", "now"]}, clock.now())
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    assert hm.energy is not None and hm.gate is not None
    setup = hm.energy.setup
    assert set(setup.managed) == {"charger", "battery"}
    assert setup.grid_power == "sensor.grid_power"  # the meters still count
    assert "select.manager_charging_mode" in hm.gate.state.other_system
    viewer_sentence = hm.languages.text("nl", "energy.ui.managed_charger")
    assert "ander systeem" in viewer_sentence
    await hm.stop()


async def test_only_real_appliances_start_and_only_real_users_count(tmp_path: Path) -> None:
    """Found in a real house: an oven offers remote start and a pause button; an inverter has
    power sensors per phase. Neither may be planned or shown as an energy user."""
    from housemind.core.model import Device, Entity

    clock = SimulatedClock(T0)
    house = load_demo_house("energy_home", clock, "nl")
    model = house.ha.model

    def add(
        device: str, name: str, area: str, entities: list[tuple[str, str, str | None, str]]
    ) -> None:
        model.devices[device] = Device(device, name, area)
        for entity_id, label, device_class, state in entities:
            model.entities[entity_id] = Entity(
                entity_id,
                f"r-{entity_id}",
                label,
                device_id=device,
                area_id=area,
                device_class=device_class,
            )
            attrs = {"device_class": device_class} if device_class else {}
            house.ha.states[entity_id] = State(entity_id, state, attrs, clock.now())

    add(
        "dev.oven",
        "Oven",
        "utility",
        [
            ("binary_sensor.oven_start_op_afstand", "Start op afstand", None, "on"),
            ("binary_sensor.oven_afstandsbediening", "Afstandsbediening", None, "on"),
            ("button.oven_pauzeer_programma", "Pauzeer programma", None, "unknown"),
            ("button.oven_stop_programma", "Stop programma", None, "unknown"),
            ("switch.oven_inschakelen", "Inschakelen", None, "off"),
        ],
    )
    add(
        "dev.dishwasher",
        "Vaatwasser",
        "utility",
        [
            ("binary_sensor.dishwasher_start_op_afstand", "Start op afstand", None, "on"),
            ("binary_sensor.dishwasher_afstandsbediening", "Afstandsbediening", None, "on"),
            ("button.dishwasher_pauzeer_programma", "Pauzeer programma", None, "unknown"),
            ("button.dishwasher_programma_starten", "Programma starten", None, "unknown"),
        ],
    )
    add(
        "dev.inverter",
        "omvormer",
        "utility",
        [
            ("sensor.inverter_l1_power", "L1 power", "power", "2090"),
            ("sensor.inverter_power_total", "Power total", "power", "2090"),
            ("number.inverter_powerlimit", "Powerlimit", None, "100"),
        ],
    )
    add(
        "dev.plug",
        "Stekker tv",
        "utility",
        [
            ("switch.plug_tv", "Stekker tv", None, "on"),
            ("sensor.plug_tv_power", "Stekker tv vermogen", "power", "80"),
        ],
    )
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    assert hm.energy is not None
    setup = hm.energy.setup
    starts = {a.name: a.start for a in setup.appliances}
    assert "Oven" not in starts  # a cooking appliance is never started
    assert starts["Vaatwasser"] == "button.dishwasher_programma_starten"  # never pause
    assert len([a for a in setup.appliances if a.name == "Vaatwasser"]) == 1
    assert "button.oven_pauzeer_programma" not in setup.controllable()
    assert "sensor.plug_tv_power" in setup.users
    assert "sensor.washer_power" in setup.users  # an appliance without a switch also counts
    assert not {"sensor.inverter_l1_power", "sensor.inverter_power_total"} & set(setup.users)
    assert setup.grid_power not in setup.users and setup.solar_power not in setup.users
    insights = await hm.energy.insights(clock.now(), hm.world.states if hm.world else {})
    assert [u["entity"] for u in insights["users"]] == ["sensor.plug_tv_power"]
    await hm.stop()


def test_a_price_series_is_found_when_the_energy_settings_have_none() -> None:
    """E-72, found in a real house: tomorrow's prices were in HA, but not in the energy
    settings. Any integration's series counts; prices without a series are ignored."""
    from datetime import timedelta

    from housemind.energy.setup import find_price_series

    def hours(n: int, start: datetime) -> list[dict[str, Any]]:
        return [
            {
                "start": (start + timedelta(hours=h)).isoformat(),
                "end": (start + timedelta(hours=h + 1)).isoformat(),
                "value": 0.2 + h / 100,
            }
            for h in range(n)
        ]

    today = T0.replace(hour=0)
    states = {
        "sensor.day_prices": State(
            "sensor.day_prices",
            "0.22",
            {"unit_of_measurement": "EUR/kWh", "raw_today": hours(24, today), "raw_tomorrow": []},
            T0,
        ),
        "sensor.two_day_prices": State(
            "sensor.two_day_prices",
            "0.22",
            {
                "unit_of_measurement": "EUR/kWh",
                "raw_today": hours(24, today),
                "raw_tomorrow": hours(24, today + timedelta(days=1)),
            },
            T0,
        ),
        "sensor.session_price": State(
            "sensor.session_price", "0.33", {"unit_of_measurement": "€/kWh"}, T0
        ),
        "sensor.costs_today": State(
            "sensor.costs_today", "1.20", {"unit_of_measurement": "€", "prices": [1, 2]}, T0
        ),
    }
    assert find_price_series(states) == "sensor.two_day_prices"  # it looks furthest ahead
    del states["sensor.two_day_prices"], states["sensor.day_prices"]
    assert find_price_series(states) is None
