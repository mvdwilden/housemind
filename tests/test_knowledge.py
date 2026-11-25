from __future__ import annotations

from pathlib import Path

import pytest

from housemind.core.model import Entity
from housemind.knowledge.catalog import Catalog, SafetyClass, Step, ValueChoices, ValueRange
from housemind.knowledge.loader import KnowledgeError, load_catalog
from housemind.knowledge.matcher import MatchQuality, recognise


def entity(entity_id: str, **kwargs: object) -> Entity:
    return Entity(entity_id=entity_id, registry_id=f"reg-{entity_id}", name=entity_id, **kwargs)  # type: ignore[arg-type]


def test_shipped_catalog_loads(catalog: Catalog) -> None:
    assert "light.dimmable" in catalog.kinds
    assert all(k.match.domain for k in catalog.kinds.values())


def test_dimmable_light_is_recognised(catalog: Catalog) -> None:
    r = recognise(
        catalog, entity("light.kitchen", capabilities={"supported_color_modes": ["brightness"]})
    )
    assert r.quality is MatchQuality.SPECIFIC
    assert r.kind is not None and r.kind.key == "light.dimmable"


def test_colour_light_prefers_colour_kind(catalog: Catalog) -> None:
    r = recognise(
        catalog, entity("light.living", capabilities={"supported_color_modes": ["color_temp"]})
    )
    assert r.kind is not None and r.kind.key == "light.color_temperature"


def test_switch_without_role_is_observe_only(catalog: Catalog) -> None:
    r = recognise(catalog, entity("switch.plug_1"))
    assert r.quality is MatchQuality.GENERIC
    assert r.kind is not None and r.kind.max_step is Step.OBSERVE


def test_switch_with_light_role_can_be_autonomous(catalog: Catalog) -> None:
    r = recognise(catalog, entity("switch.plug_1"), role="light")
    assert r.kind is not None and r.kind.key == "switch.light"
    assert r.kind.max_step is Step.AUTONOMOUS


def test_garage_door_is_propose_only_and_cannot_open(catalog: Catalog) -> None:
    r = recognise(catalog, entity("cover.garage", device_class="garage"))
    assert r.kind is not None and r.kind.key == "cover.garage_gate_door"
    assert r.kind.safety_class is SafetyClass.PROPOSE_ONLY
    assert r.kind.allowed("cover.open_cover") is None


def test_unknown_variant_falls_back_to_generic(catalog: Catalog) -> None:
    r = recognise(catalog, entity("cover.new_thing", device_class="something_new"))
    assert r.quality is MatchQuality.GENERIC
    assert r.kind is not None and r.kind.max_step is Step.OBSERVE


def test_unknown_domain_is_unknown(catalog: Catalog) -> None:
    r = recognise(catalog, entity("infrared.remote_1"))
    assert r.quality is MatchQuality.UNKNOWN and r.kind is None


def test_allowed_action_checks_values(catalog: Catalog) -> None:
    allowed = catalog.kinds["light.dimmable"].allowed("light.turn_on")
    assert allowed is not None
    assert allowed.accepts({"brightness_pct": 60}) is None
    assert allowed.accepts({"brightness_pct": 160}) == "value_out_of_range:brightness_pct"
    assert allowed.accepts({"rgb_color": [1, 2, 3]}) == "value_not_allowed:rgb_color"
    assert allowed.accepts({"brightness_pct": True}) == "value_not_numeric:brightness_pct"
    climate = catalog.kinds["climate.thermostat"].allowed("climate.set_hvac_mode")
    assert climate is not None
    assert climate.accepts({"hvac_mode": "off"}) is None
    assert climate.accepts({"hvac_mode": "dry"}) == "value_not_an_option:hvac_mode"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


BASE = """
schema: 1
kinds:
  - key: light.test
    match: {domain: light}
    safety_class: careful
    initiative: house
    max_step: autonomous
    actions:
      - action: light.turn_on
        values: {brightness_pct: [10, 90], effect: {one_of: [none, slow]}}
    min_interval_s: 10
"""


def test_later_layer_can_only_make_stricter(tmp_path: Path) -> None:
    _write(tmp_path / "base" / "a.yaml", BASE)
    _write(
        tmp_path / "user" / "b.yaml",
        """
schema: 1
kinds:
  - key: light.test
    match: {domain: light}
    safety_class: free
    initiative: house
    max_step: propose
    actions:
      - action: light.turn_on
        values: {brightness_pct: [0, 50], effect: {one_of: [slow, flash]}}
      - action: light.toggle
    min_interval_s: 5
""",
    )
    catalog, report = load_catalog([tmp_path / "base", tmp_path / "user"])
    kind = catalog.kinds["light.test"]
    assert kind.safety_class is SafetyClass.CAREFUL  # not loosened to free
    assert kind.max_step is Step.PROPOSE  # lowering is fine
    assert kind.min_interval_s == 10  # not shortened
    turn_on = kind.allowed("light.turn_on")
    assert turn_on is not None
    assert turn_on.values["brightness_pct"] == ValueRange(10, 50)
    assert turn_on.values["effect"] == ValueChoices(frozenset({"slow"}))
    assert kind.allowed("light.toggle") is None
    assert any("loosened" in w for w in report.warnings)
    assert any("light.toggle" in w for w in report.warnings)


def test_broken_shipped_file_stops_loading(tmp_path: Path) -> None:
    _write(tmp_path / "base" / "a.yaml", "schema: 1\nkinds: [{key: bad}]\n")
    with pytest.raises(KnowledgeError):
        load_catalog([tmp_path / "base"])


def test_broken_user_file_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path / "base" / "a.yaml", BASE)
    _write(tmp_path / "user" / "b.yaml", "schema: 2\n")
    catalog, report = load_catalog([tmp_path / "base", tmp_path / "user"])
    assert "light.test" in catalog.kinds
    assert report.warnings and "skipped" in report.warnings[0]


# What QA 2 reproduced (H-1): one own file in /config/housemind/catalog took a smoke detector's
# emergency reaction away, let the main valve open and put switches on Autonomous.
TAKE_OVER = """
schema: 1
kinds:
  - key: binary_sensor.smoke_detector
    match: {domain: binary_sensor, device_classes: [smoke]}
    safety_class: never
    initiative: house
    max_step: observe
  - key: valve.water_main
    match: {domain: valve, device_classes: [water], required_features: 1}
    safety_class: free
    initiative: house
    max_step: autonomous
    actions:
      - action: valve.open_valve
  - key: switch.outlet
    match: {domain: switch, device_classes: [outlet, switch]}
    safety_class: free
    initiative: house
    max_step: autonomous
    actions:
      - action: switch.turn_on
      - action: switch.turn_off
"""


def test_a_new_kind_in_an_own_file_cannot_take_over_a_shipped_kind(tmp_path: Path) -> None:
    """QA 2 (B-168): a later layer may add kinds, but never one that takes over a kind
    HouseMind ships; and a new kind is never looser than the general kind of its domain."""
    shipped = Path(__file__).resolve().parents[1] / "knowledge" / "catalog"
    _write(tmp_path / "own" / "take_over.yaml", TAKE_OVER)
    plain, _ = load_catalog([shipped])
    catalog, report = load_catalog([shipped, tmp_path / "own"])
    for e in (
        entity("binary_sensor.smoke_hall", device_class="smoke"),
        entity("valve.main", device_class="water", supported_features=3),
    ):
        assert recognise(catalog, e).kind == recognise(plain, e).kind, e.entity_id
    assert sum("cannot take over" in w for w in report.warnings) >= 2
    # A switch HouseMind only watches stays that way: the new kind is no looser than the
    # general switch (no actions, Observe).
    gate = recognise(catalog, entity("switch.gate", device_class="switch")).kind
    generic = plain.generic_for("switch")
    assert gate is not None and generic is not None
    assert gate.max_step <= generic.max_step
    assert {a.action for a in gate.actions} <= {a.action for a in generic.actions}
    # A kind for something HouseMind does not know is fine – within the general kind.
    _write(
        tmp_path / "own2" / "new.yaml",
        """
schema: 1
kinds:
  - key: switch.new_thing
    match: {domain: switch, device_classes: [brand_new_class]}
    safety_class: free
    initiative: house
    max_step: autonomous
    actions:
      - action: switch.turn_on
""",
    )
    catalog, _ = load_catalog([shipped, tmp_path / "own2"])
    new = catalog.kinds["switch.new_thing"]
    generic = catalog.generic_for("switch")
    assert generic is not None
    assert new.max_step <= generic.max_step and new.experimental
    assert recognise(catalog, entity("switch.x", device_class="brand_new_class")).kind == new


def test_a_smoke_or_water_sensor_stays_an_emergency_sensor_in_code(tmp_path: Path) -> None:
    """QA 2 (B-168): whatever a catalogue says, HA's smoke, CO, gas and moisture classes are an
    emergency."""
    from datetime import UTC, datetime

    from housemind.core.clock import SimulatedClock
    from housemind.core.model import HouseModel
    from housemind.knowledge.catalog import Catalog
    from housemind.safety.emergency import Emergency, EmergencyReactions

    model = HouseModel()
    for e in (
        entity("binary_sensor.smoke_hall", device_class="smoke"),
        entity("binary_sensor.leak_kitchen", device_class="moisture"),
    ):
        model.entities[e.entity_id] = e
    reactions = EmergencyReactions(
        SimulatedClock(datetime(2026, 10, 21, tzinfo=UTC)), Catalog(), model
    )
    assert reactions.emergency_for("binary_sensor.smoke_hall") is Emergency.SMOKE
    assert reactions.emergency_for("binary_sensor.leak_kitchen") is Emergency.LEAK
