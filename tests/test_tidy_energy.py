"""Home Assistant netjes, deel A2 (E-369 – E-371, B-265 – B-267): meters from the energy
settings that stand still, an integration that measures nothing, and the energy settings."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from housemind.brain import tidy
from housemind.core.model import Entity, HouseModel, State
from housemind.ha.adapter import HAConfigEntry

NOW = datetime(2026, 10, 25, 18, 0, tzinfo=UTC)
PREFS = {
    "energy_sources": [
        {
            "type": "grid",
            "flow_from": [{"stat_energy_from": "sensor.grid_in"}],
            "flow_to": [{"stat_energy_to": "sensor.grid_out"}],
        },
        {"type": "solar", "stat_energy_from": "sensor.solar_today"},
        {
            "type": "battery",
            "stat_energy_from": "sensor.battery_out",
            "stat_energy_to": "sensor.battery_in",
        },
        {"type": "water", "stat_energy_from": "sensor.water"},
        {"type": "gas", "stat_energy_from": "external:gas_from_elsewhere"},
    ],
    "device_consumption": [{"stat_consumption": "sensor.washer"}],
}
VICTRON = HAConfigEntry("e-victron", "victron", "Victron", "loaded")


def _model(*entity_ids: str, entry: str | None = None) -> HouseModel:
    model = HouseModel()
    for entity_id in entity_ids:
        model.entities[entity_id] = Entity(
            entity_id, f"r-{entity_id}", entity_id, platform="demo", config_entry_id=entry
        )
    return model


def _state(entity_id: str, value: str, at: datetime = NOW, **attrs: object) -> State:
    return State(entity_id, value, dict(attrs), at)


def test_the_meters_of_the_energy_settings() -> None:
    meters = {m.entity_id: m.role for m in tidy.meters_of(PREFS)}
    assert meters == {
        "sensor.grid_in": "grid",
        "sensor.grid_out": "grid",
        "sensor.solar_today": "solar",
        "sensor.battery_out": "battery",
        "sensor.battery_in": "battery",
        "sensor.water": "water",
        "sensor.washer": "device",
    }  # a statistic from elsewhere has no state: left out


def test_the_memory_keeps_its_time_through_away_and_back() -> None:
    meters = [tidy.Meter("sensor.solar_today", "solar")]
    start = (0.0, (NOW - timedelta(hours=10)).isoformat())
    away = tidy.meters_memory(
        meters,
        {"sensor.solar_today": _state("sensor.solar_today", "unavailable")},
        {"sensor.solar_today": start},
        NOW,
    )
    assert away["sensor.solar_today"] == start
    back = tidy.meters_memory(
        meters, {"sensor.solar_today": _state("sensor.solar_today", "0.0")}, away, NOW
    )
    assert back["sensor.solar_today"] == start  # the same value: still since then
    moved = tidy.meters_memory(
        meters, {"sensor.solar_today": _state("sensor.solar_today", "12.0")}, back, NOW
    )
    assert moved["sensor.solar_today"] == (12.0, NOW.isoformat())


def test_since_when_from_the_history() -> None:
    day = NOW.replace(hour=0)
    changes = [
        (day - timedelta(hours=4), "9396.0"),
        (day, "unknown"),
        (day + timedelta(hours=5), "0.0"),
        (day + timedelta(hours=11), "unavailable"),
        (day + timedelta(hours=13), "0.0"),
    ]
    assert tidy.still_since(changes, 0.0) == day + timedelta(hours=5)
    assert tidy.still_since([], 0.0) is None


def _still(
    role: str,
    since: datetime,
    *,
    forecast: dict[datetime, float] | None = None,
    sun: float | None = None,
    away: bool = False,
    entity_ids: tuple[str, ...] = (),
) -> list[tidy.Finding]:
    ids = entity_ids or (f"sensor.{role}",)
    meters = [tidy.Meter(e, role) for e in ids]
    states = {e: _state(e, "0.0", unit_of_measurement="kWh") for e in ids}
    memory = {e: (0.0, since.isoformat()) for e in ids}
    return tidy.meters_still(
        meters,
        _model(*ids, entry="e-victron"),
        states,
        memory,
        NOW,
        [VICTRON],
        forecast,
        (lambda start, end: sun) if sun is not None else None,
        away=away,
    )


def test_the_sun_counts_only_when_sun_was_expected() -> None:
    since = NOW.replace(hour=5)
    sunny = {NOW.replace(hour=h): 600.0 for h in range(9, 16)}
    found = _still("solar", since, forecast=sunny)
    assert found and found[0].detail["expected_wh"] == 4200 and found[0].heavy is False
    assert found[0].used == 1  # HouseMind uses it: heavy once weighed
    grey = {NOW.replace(hour=12): 300.0}
    assert not _still("solar", since, forecast=grey)  # 0,3 kWh expected: a grey day
    assert _still("solar", since, sun=7.0)  # no forecast: seven hours of sun
    assert not _still("solar", since, sun=3.0)


def test_the_other_meters_and_their_times() -> None:
    assert _still("grid", NOW - timedelta(hours=13), entity_ids=("sensor.in", "sensor.out"))
    assert not _still("grid", NOW - timedelta(hours=11), entity_ids=("sensor.in", "sensor.out"))
    assert _still("battery", NOW - timedelta(days=2, hours=1), entity_ids=("sensor.c", "sensor.d"))
    assert not _still("battery", NOW - timedelta(days=1))
    assert _still("water", NOW - timedelta(days=4))
    assert not _still("water", NOW - timedelta(days=4), away=True)  # away: no water is right
    assert not _still("gas", NOW - timedelta(days=6))
    assert _still("device", NOW - timedelta(days=8))


def test_grid_and_battery_count_only_when_both_ways_stand_still() -> None:
    ids = ("sensor.grid_in", "sensor.grid_out")
    meters = [tidy.Meter(e, "grid") for e in ids]
    states = {e: _state(e, "5.0") for e in ids}
    memory = {
        "sensor.grid_in": (5.0, (NOW - timedelta(hours=20)).isoformat()),
        "sensor.grid_out": (5.0, (NOW - timedelta(hours=2)).isoformat()),  # it returned power
    }
    found = tidy.meters_still(meters, _model(*ids), states, memory, NOW, [], None)
    assert found == []


def test_still_meters_of_one_integration_are_one_card() -> None:
    model = _model("sensor.battery_in", "sensor.battery_out", entry="e-victron")
    ids = ("sensor.battery_in", "sensor.battery_out")
    meters = [tidy.Meter(e, "battery") for e in ids]
    states = {e: _state(e, "0.0") for e in ids}
    memory = {e: (0.0, (NOW - timedelta(days=3)).isoformat()) for e in ids}
    extra = tidy.meters_still(meters, model, states, memory, NOW, [VICTRON], None)
    found = tidy.find(model, states, [VICTRON], [], set(), NOW, {}, set(), extra)
    card = next(f for f in found.findings if f.kind == "meter_still_group")
    assert card.name == "Victron" and card.follows == ids and card.heavy


def test_an_integration_that_measures_nothing_is_one_card() -> None:
    ids = tuple(f"sensor.dsmr_{n}" for n in range(5))
    model = _model(*ids, entry="e-dsmr")
    entry = HAConfigEntry("e-dsmr", "dsmr_reader", "DSMR Reader", "loaded")
    ten_days = NOW - timedelta(days=10)
    states = {e: _state(e, "unknown", ten_days) for e in ids}
    found = tidy.find(model, states, [entry], [], set(), NOW, {}, set())
    card = next(f for f in found.findings if f.kind == "integration_silent")
    assert card.name == "DSMR Reader" and len(card.follows) == 5 and card.since == ten_days
    assert card.heavy  # ten days
    # One entity with a value: the integration measures something.
    states["sensor.dsmr_0"] = _state("sensor.dsmr_0", "12.3")
    found = tidy.find(model, states, [entry], [], set(), NOW, {}, set())
    assert not any(f.kind == "integration_silent" for f in found.findings)
    # Away for an hour only: not yet.
    states = {e: _state(e, "unknown", NOW - timedelta(hours=1)) for e in ids}
    found = tidy.find(model, states, [entry], [], set(), NOW, {}, set())
    assert not any(f.kind == "integration_silent" for f in found.findings)


def test_no_grid_meter_with_what_looks_like_one() -> None:
    prefs = {"energy_sources": [{"type": "solar", "stat_energy_from": "sensor.solar_today"}]}
    model = _model("sensor.dsmr_electricity_delivered_1", "sensor.dsmr_electricity_cost")
    states = {
        "sensor.dsmr_electricity_delivered_1": _state(
            "sensor.dsmr_electricity_delivered_1",
            "12034.5",
            device_class="energy",
            state_class="total_increasing",
        ),
        "sensor.dsmr_electricity_cost": _state(
            "sensor.dsmr_electricity_cost", "4.2", device_class="energy", state_class="total"
        ),
        "sensor.solar_today": _state(
            "sensor.solar_today", "0.0", device_class="energy", state_class="total_increasing"
        ),
    }
    found = tidy.energy_settings(
        prefs, {}, model, states, ["delivered", "electricity"], ["cost", "solar"], NOW
    )
    assert [f.kind for f in found] == ["energy_no_grid"]
    assert found[0].detail["candidates"] == ["sensor.dsmr_electricity_delivered_1"]
    assert tidy.energy_settings(PREFS, {}, model, states, ["delivered"], ["cost"], NOW) == []


def test_what_home_assistant_finds_in_its_energy_settings() -> None:
    validation = {
        "energy_sources": [
            [{"type": "entity_unavailable", "affected_entities": [["sensor.solar_today", None]]}],
            [],
        ],
        "device_consumption": [
            [
                {
                    "type": "entity_unexpected_unit_energy",
                    "affected_entities": [["sensor.washer", "W"]],
                }
            ]
        ],
    }
    found = tidy.energy_settings(PREFS, validation, HouseModel(), {}, [], [], NOW)
    assert [f.kind for f in found] == ["energy_issues"]
    assert found[0].detail["issues"] == [
        {"type": "entity_unavailable", "entity": "sensor.solar_today"},
        {"type": "entity_unexpected_unit_energy", "entity": "sensor.washer"},
    ]


def test_energy_findings_keep_the_time_they_were_first_seen() -> None:
    first = tidy.energy_settings({"energy_sources": []}, {}, HouseModel(), {}, [], [], NOW)
    earlier = (NOW - timedelta(days=3)).isoformat()
    found = tidy.find(
        HouseModel(), {}, [], [], set(), NOW, {"energy_no_grid:energy": earlier}, set(), first
    )
    card = next(f for f in found.findings if f.kind == "energy_no_grid")
    assert card.since.isoformat() == earlier and card.heavy  # HouseMind uses the grid meter


def test_the_grid_meter_found_in_the_real_house() -> None:
    """Found in the real house: the smart meter's readings are called "Verbruik laag tarief" and
    "Verbruik hoog tarief" ("tarief" is no price: a price is no energy meter), and a heat pump's
    daily consumption for heating is no grid meter."""
    from pathlib import Path

    from housemind.brain.rhythm import daily_words
    from housemind.i18n.catalog import load_languages

    languages = load_languages([Path(__file__).resolve().parents[1] / "knowledge" / "languages"])
    meter = {"device_class": "energy", "state_class": "total_increasing"}
    names = {
        "sensor.dsmr_reading_electricity_delivered_1": "Verbruik laag tarief",
        "sensor.dsmr_reading_electricity_delivered_2": "Verbruik hoog tarief",
        "sensor.dsmr_reading_electricity_returned_1": "Teruggeleverd laag tarief",
        "sensor.altherma_climatecontrol_heating_daily_electrical_consumption": (
            "Altherma ClimateControl Dagelijks elektriciteitsverbruik verwarmen"
        ),
    }
    model = HouseModel()
    states = {}
    for entity_id, name in names.items():
        model.entities[entity_id] = Entity(entity_id, f"r-{entity_id}", name, platform="demo")
        states[entity_id] = _state(entity_id, "20099.697", friendly_name=name, **meter)
    found = tidy.energy_settings(
        {"energy_sources": [{"type": "solar", "stat_energy_from": "sensor.solar_today"}]},
        {},
        model,
        states,
        daily_words(languages, "grid_meter"),
        daily_words(languages, "not_grid_meter"),
        NOW,
    )
    assert found[0].detail["candidates"] == [
        "sensor.dsmr_reading_electricity_delivered_1",
        "sensor.dsmr_reading_electricity_delivered_2",
    ]
