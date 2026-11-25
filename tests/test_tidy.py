"""Home Assistant netjes, deel A (E-351 – E-354, B-235 – B-238): the rules, carefully, one
finding per cause, and nothing before it was seen for an hour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from housemind.brain import tidy
from housemind.core.model import Device, Entity, HouseModel, State
from housemind.ha.adapter import HAConfigEntry
from housemind.ha.translate import refs_of

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(hours=30)


def _entity(entity_id: str, **kw: object) -> Entity:
    return Entity(entity_id, f"r-{entity_id}", kw.pop("name", entity_id), **kw)  # type: ignore[arg-type]


def _state(entity_id: str, value: str, at: datetime = LONG_AGO, **attrs: object) -> State:
    return State(entity_id, value, dict(attrs), at)


def _house(at: datetime = LONG_AGO) -> tuple[HouseModel, dict[str, State]]:
    model = HouseModel()
    model.devices["d-hue"] = Device("d-hue", "Hue-brug", None, "hue")
    model.devices["d-garden"] = Device("d-garden", "Tuinsensor", "garden", "zha")
    for entity_id, device, entry in (
        ("light.living", "d-hue", "e-hue"),
        ("light.kitchen", "d-hue", "e-hue"),
        ("sensor.garden_temperature", "d-garden", "e-zha"),
        ("sensor.garden_humidity", "d-garden", "e-zha"),
        ("sensor.living_temperature", None, "e-zha"),
        ("sensor.living_temperature_2", None, "e-zha"),
        ("switch.old_fridge", None, "e-shelly"),
        ("switch.coffee", None, "e-shelly"),
    ):
        model.entities[entity_id] = _entity(
            entity_id, device_id=device, platform=entry[2:], config_entry_id=entry
        )
    states = {
        "light.living": _state("light.living", "unavailable", at),
        "light.kitchen": _state("light.kitchen", "unavailable", at),
        "sensor.garden_temperature": _state("sensor.garden_temperature", "unavailable", at),
        "sensor.garden_humidity": _state("sensor.garden_humidity", "unavailable", at),
        "sensor.living_temperature": _state("sensor.living_temperature", "21.5"),
        "sensor.living_temperature_2": _state("sensor.living_temperature_2", "unavailable", at),
        "switch.old_fridge": _state("switch.old_fridge", "unavailable", at, restored=True),
        "switch.coffee": _state("switch.coffee", "off"),
    }
    return model, states


ENTRIES = [
    HAConfigEntry("e-hue", "hue", "Hue", "setup_retry"),
    HAConfigEntry("e-zha", "zha", "Zigbee", "loaded"),
    HAConfigEntry("e-shelly", "shelly", "Shelly", "loaded"),
]
LIGHTS_ON = tidy.Configured(
    "automation.hall_light",
    "Ganglamp ’s nachts",
    "automation",
    ("entity:binary_sensor.hall_motion", "entity:light.living"),
    "1700",
)


def _round(
    seen: dict[str, str] | None = None,
    at: datetime = NOW,
    entries: list[HAConfigEntry] | None = ENTRIES,
    configured: tuple[tidy.Configured, ...] = (LIGHTS_ON,),
    followed: frozenset[str] = frozenset(),
    model_states: tuple[HouseModel, dict[str, State]] | None = None,
) -> tidy.Round:
    model, states = model_states or _house()
    return tidy.find(model, states, entries, list(configured), set(followed), at, seen or {}, set())


def _seen_long_ago() -> dict[str, str]:
    """Everything the first round found, as if seen 30 hours ago."""
    first = _round(at=LONG_AGO)
    return dict.fromkeys(first.seen, LONG_AGO.isoformat())


def test_nothing_shows_before_it_was_away_for_an_hour() -> None:
    fresh = _house(NOW)  # everything went away just now
    first = _round(model_states=fresh)
    assert {f.kind for f in first.findings} == {"missing_ref"}  # B-238: only what stays
    assert "integration_down:e-hue" in first.seen
    later = _round(first.seen, NOW + timedelta(minutes=61), model_states=_house(NOW))
    assert {f.kind for f in later.findings} >= {"integration_down", "device_down", "duplicate"}


def test_away_counts_from_what_home_assistant_says() -> None:
    found = _round()  # first round, but Home Assistant says: unavailable for 30 hours
    down = next(f for f in found.findings if f.kind == "integration_down")
    assert down.since == LONG_AGO


def test_one_cause_for_an_integration_that_does_not_start() -> None:
    found = _round(_seen_long_ago()).findings
    down = next(f for f in found if f.kind == "integration_down")
    assert down.name == "Hue" and down.detail["state"] == "setup_retry"
    assert down.follows == ("light.kitchen", "light.living")
    assert down.depends == ("automation.hall_light",)  # an automation points to a follow-up
    assert down.heavy  # automations depend on it (B-238)
    # Its lights are follow-ups: no finding of their own (B-237).
    assert not any(f.target in ("light.kitchen", "light.living") for f in found if f is not down)


def test_a_whole_device_down_is_one_finding() -> None:
    found = _round(_seen_long_ago()).findings
    garden = next(f for f in found if f.kind == "device_down")
    assert garden.name == "Tuinsensor"
    assert garden.follows == ("sensor.garden_humidity", "sensor.garden_temperature")
    assert not any(f.kind == "unavailable" and "garden" in f.target for f in found)


def test_a_duplicate_and_one_no_longer_provided() -> None:
    found = {f.kind: f for f in _round(_seen_long_ago()).findings}
    assert found["duplicate"].target == "sensor.living_temperature_2"
    assert found["duplicate"].suggest == "sensor.living_temperature"
    assert found["not_provided"].target == "switch.old_fridge"


def test_not_provided_only_while_its_integration_runs() -> None:
    entries = [
        e
        if e.entry_id != "e-shelly"
        else HAConfigEntry("e-shelly", "shelly", "Shelly", "setup_retry")
        for e in ENTRIES
    ]
    found = _round(_seen_long_ago(), entries=entries).findings
    assert not any(f.kind == "not_provided" for f in found)  # the fridge follows Shelly now


def test_a_reference_to_something_gone_with_what_it_looks_like() -> None:
    model, states = _house()
    model.entities["binary_sensor.hall_motion_2"] = _entity(
        "binary_sensor.hall_motion_2", platform="zha", config_entry_id="e-zha"
    )
    states["binary_sensor.hall_motion_2"] = _state("binary_sensor.hall_motion_2", "off")
    found = _round(_seen_long_ago(), model_states=(model, states)).findings
    ref = next(f for f in found if f.kind == "missing_ref")
    assert ref.missing == ("entity:binary_sensor.hall_motion",)
    assert ref.suggest == "binary_sensor.hall_motion_2"


def test_switched_off_and_disabled_are_no_fault() -> None:
    model, states = _house()
    model.entities["light.living"] = _entity(
        "light.living", device_id="d-hue", platform="hue", config_entry_id="e-hue", disabled=True
    )
    entries = [
        HAConfigEntry("e-hue", "hue", "Hue", "not_loaded", disabled=True),
        *ENTRIES[1:],
    ]
    found = _round(_seen_long_ago(), entries=entries, model_states=(model, states)).findings
    assert not any(f.kind == "integration_down" for f in found)
    assert not any(f.target == "light.living" for f in found)


def test_unknown_counts_only_after_a_real_value() -> None:
    model, states = _house()
    states["switch.coffee"] = _state("switch.coffee", "unknown")
    seen = _seen_long_ago()
    never = tidy.find(model, states, ENTRIES, [], set(), NOW, seen, set())
    assert not any(f.target == "switch.coffee" for f in never.findings)
    seen["unavailable:switch.coffee"] = LONG_AGO.isoformat()
    had = tidy.find(model, states, ENTRIES, [], set(), NOW, seen, {"switch.coffee"})
    assert any(f.target == "switch.coffee" for f in had.findings)
    # It stays known as "had a value" while it is unknown.
    assert "switch.coffee" in tidy.valued_next({"switch.coffee"}, model, states)


def test_diagnostic_entities_only_count_with_their_device() -> None:
    model, states = _house()
    model.entities["sensor.coffee_signal"] = _entity(
        "sensor.coffee_signal",
        platform="shelly",
        config_entry_id="e-shelly",
        entity_category="diagnostic",
    )
    states["sensor.coffee_signal"] = _state("sensor.coffee_signal", "unavailable")
    found = _round(_seen_long_ago(), model_states=(model, states)).findings
    assert not any(f.target == "sensor.coffee_signal" for f in found)


def test_what_housemind_uses_makes_it_heavy_at_once() -> None:
    first = _round(at=NOW - timedelta(hours=2), followed=frozenset({"switch.old_fridge"}))
    found = _round(first.seen, followed=frozenset({"switch.old_fridge"})).findings
    fridge = next(f for f in found if f.target == "switch.old_fridge")
    assert fridge.used == 1 and fridge.heavy


def test_a_finding_that_is_gone_starts_over() -> None:
    seen = _seen_long_ago()
    model, states = _house()
    states["sensor.living_temperature_2"] = _state("sensor.living_temperature_2", "21.4")
    again = tidy.find(model, states, ENTRIES, [LIGHTS_ON], set(), NOW, seen, set())
    assert "duplicate:sensor.living_temperature_2" not in again.seen


def test_without_the_integrations_their_findings_keep_their_time() -> None:
    seen = _seen_long_ago()
    found = _round(seen, entries=None)
    assert found.seen["integration_down:e-hue"] == seen["integration_down:e-hue"]
    assert not any(f.kind in ("integration_down", "not_provided") for f in found.findings)


def test_without_anything_from_home_assistant_it_counts_from_now() -> None:
    model, states = _house()
    entries = [*ENTRIES, HAConfigEntry("e-empty", "demo", "Leeg", "setup_error")]
    found = tidy.find(model, states, entries, [], set(), NOW, {}, set())
    assert found.seen["integration_down:e-empty"] == NOW.isoformat()  # never in the future


def test_references_in_a_configuration() -> None:
    config = {
        "triggers": [{"trigger": "state", "entity_id": ["binary_sensor.a", "binary_sensor.b"]}],
        "conditions": [{"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"}],
        "actions": [
            {"action": "light.turn_on", "target": {"entity_id": "light.x", "area_id": "hall"}},
            {"action": "light.turn_on", "target": {"entity_id": "{{ states('input_text.l') }}"}},
            {"device_id": "abc123", "domain": "light", "type": "turn_off"},
            {"action": "light.turn_off", "target": {"entity_id": "all"}},
        ],
    }
    assert refs_of(config) == (
        "entity:binary_sensor.a",
        "entity:binary_sensor.b",
        "entity:sun.sun",
        "entity:light.x",
        "area:hall",
        "device:abc123",
    )
    blueprint = {"use_blueprint": {"path": "x.yaml", "input": {"light": {"entity_id": "light.y"}}}}
    assert refs_of(blueprint) == ("entity:light.y",)


def test_one_integration_s_entities_are_one_card() -> None:
    """B-258: two or more entities of one integration no longer provided, or unavailable,
    are one card; one alone keeps its own."""
    model, states = _house()
    for entity_id in ("switch.old_kettle", "sensor.old_power"):
        model.entities[entity_id] = _entity(
            entity_id, platform="shelly", config_entry_id="e-shelly"
        )
        states[entity_id] = _state(
            entity_id, "unavailable", NOW - timedelta(hours=2), restored=True
        )
    for entity_id in ("sensor.cost_today", "sensor.cost_month"):
        model.entities[entity_id] = _entity(entity_id, platform="template")
        states[entity_id] = _state(entity_id, "unavailable", NOW - timedelta(hours=3))
    found = {f.kind: f for f in _round(_seen_long_ago(), model_states=(model, states)).findings}
    shelly = found["not_provided_group"]
    assert shelly.name == "Shelly" and shelly.target == "e-shelly"
    assert shelly.follows == ("sensor.old_power", "switch.old_fridge", "switch.old_kettle")
    assert shelly.since == LONG_AGO and shelly.heavy  # the oldest, and heavy as one of them is
    assert "not_provided" not in found  # no card of their own
    templates = found["unavailable_group"]  # no integration: the platform counts
    assert templates.target == "platform:template" and len(templates.follows) == 2
    assert templates.detail["domain"] == "template"


def test_a_single_one_keeps_its_own_card() -> None:
    found = {f.kind: f for f in _round(_seen_long_ago()).findings}
    assert found["not_provided"].target == "switch.old_fridge"
    assert found["not_provided"].detail["source_name"] == "Shelly"


def test_a_scene_an_automation_makes_itself_is_not_missing() -> None:
    """Found in the real house: an automation saves the lights with scene.create (scene_id
    "huidig") and turns scene.huidig on later. Before its first run that scene does not exist,
    and that is no fault."""
    config = {
        "actions": [
            {"action": "scene.create", "data": {"scene_id": "huidig", "snapshot_entities": []}},
            {"action": "scene.turn_on", "target": {"entity_id": "scene.huidig"}},
        ]
    }
    refs = refs_of(config)
    assert "creates:scene.huidig" in refs and "entity:scene.huidig" in refs
    doorbell = tidy.Configured("automation.doorbell", "Deurbel", "automation", refs, "1")
    found = _round(_seen_long_ago(), configured=(doorbell,)).findings
    assert not any(f.kind == "missing_ref" for f in found)
    other = tidy.Configured(  # another automation that only turns it on: also fine
        "automation.hall", "Hal", "automation", ("entity:scene.huidig",), "2"
    )
    found = _round(_seen_long_ago(), configured=(doorbell, other)).findings
    assert not any(f.kind == "missing_ref" for f in found)


def test_a_card_has_the_name_home_assistant_shows() -> None:
    model, states = _house()
    model.entities["switch.old_fridge"] = _entity(
        "switch.old_fridge", name="Vermogen", platform="shelly", config_entry_id="e-shelly"
    )
    states["switch.old_fridge"] = _state(
        "switch.old_fridge", "unavailable", restored=True, friendly_name="Oude koelkast Vermogen"
    )
    found = {f.kind: f for f in _round(_seen_long_ago(), model_states=(model, states)).findings}
    assert found["not_provided"].name == "Oude koelkast Vermogen"  # not just 'Vermogen'
    # Without a state: the device's name before the entity's, as Home Assistant makes it.
    model.entities["sensor.garden_battery"] = _entity(
        "sensor.garden_battery", name="Batterij", device_id="d-garden"
    )
    assert tidy.shown_name("sensor.garden_battery", model, {}) == "Tuinsensor Batterij"
    model.entities["sensor.garden_own"] = _entity(
        "sensor.garden_own", name="Tuinsensor accu", device_id="d-garden"
    )
    assert tidy.shown_name("sensor.garden_own", model, {}) == "Tuinsensor accu"
