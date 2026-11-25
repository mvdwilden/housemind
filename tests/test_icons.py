"""An icon per device and per room (E-508 – E-510, design 79). The administrator (12-11-2026):
"Overal een icoontje per soort apparaat" – a lamp, a dimmed lamp with its percentage, a home
battery, solar panels, an oven, a fridge, a plug, a person, a pet, an alarm, smoke, a doorbell, a
camera, motion, media, a vacuum cleaner, curtains, heating … – chosen by HouseMind from what it
already knows, in one place, without guessing and without asking. Each test fails without the
build."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from housemind.brain.icons import ICONS, Choice, Icons, area_icon, from_ha, with_state
from housemind.core.model import Area, Entity, State
from housemind.i18n.catalog import load_languages

ROOT = Path(__file__).resolve().parents[1]
ICONS_ = Icons(load_languages([ROOT / "knowledge" / "languages"]))
NOW = datetime(2026, 11, 12, 20, 0, tzinfo=UTC)


def choose(entity_id: str, name: str, function: str | None = None, **kw: object) -> Choice:
    return ICONS_.of(Entity(entity_id, f"reg-{entity_id}", name, **kw), function)  # type: ignore[arg-type]


def test_a_person_s_own_choice_in_home_assistant_comes_first() -> None:
    """Someone gave a socket the fan icon in Home Assistant: that is its icon – also an icon
    with an extra part ("-outline"). An icon the set has no drawing for counts as none."""
    assert choose("switch.socket_hall", "Socket hall", "appliance_power", icon="mdi:fan") == Choice(
        "fan", "ha"
    )
    assert from_ha("mdi:fridge-outline") == "fridge"
    assert from_ha("mdi:smoke-detector-variant-alert") == "smoke"
    assert from_ha("mdi:rocket-launch") is None
    assert choose("switch.socket_hall", "Socket hall", None, icon="mdi:rocket").icon == "switch"


def test_what_it_does_then_the_appliance_behind_a_plug_by_its_name() -> None:
    """A switch with a lamp behind it is a lamp; a plug that measures an appliance is that
    appliance when its name says so, in any language; a dishwasher is no washing machine."""
    assert choose("switch.floor_lamp", "Schemerlamp", "lamp") == Choice("lamp", "function")
    assert choose("switch.plug_2", "Koelkast", "appliance_power") == Choice("fridge", "function")
    assert choose("switch.plug_3", "Fridge plug", "appliance_power").icon == "fridge"
    assert choose("sensor.kuehl_power", "Kühlschrank Leistung", "device_power").icon == "fridge"
    assert choose("switch.lave", "Lave-vaisselle", "appliance_power").icon == "dishwasher"
    assert choose("switch.dishwasher", "Dishwasher", "appliance_start").icon == "dishwasher"
    assert choose("switch.wasmachine", "Wasmachine", "appliance_start").icon == "washer"
    assert choose("climate.wp", "Warmtepomp", "room_climate").icon == "heat_pump"
    # A plug that only says it is a plug stays a plug; a short word never hides in another.
    assert choose("switch.plug_4", "Stekker 4", "appliance_power").icon == "plug"
    assert choose("switch.automation_x", "Automatische deur", "appliance_power").icon == "plug"


def test_the_class_makes_what_it_does_finer() -> None:
    """A cover that is a shutter, an opening that is a window, safety that is a leak, a player
    that is a tv – and a siren stays a siren whatever its function says."""
    assert choose("cover.b", "Rolluik", "cover", device_class="shutter").icon == "shutter"
    assert choose("cover.c", "Gordijn", "cover", device_class="curtain").icon == "curtains"
    assert choose("binary_sensor.w", "Raam", "opening", device_class="window").icon == "window"
    assert choose("binary_sensor.l", "Lek", "safety", device_class="moisture").icon == "water_leak"
    assert choose("binary_sensor.s", "Rookmelder", "safety", device_class="smoke").icon == "smoke"
    assert choose("media_player.tv", "Tv", "player", device_class="tv").icon == "tv"
    assert choose("siren.hall", "Sirene", "safety") == Choice("siren", "domain")


def test_then_the_class_then_the_domain_else_neutral() -> None:
    """Without a function: the class, then the domain; a gas meter is no smoke alarm; what
    HouseMind does not know gets the neutral square – never a guess."""
    assert choose("binary_sensor.door", "Voordeur", None, device_class="door") == Choice(
        "door", "class"
    )
    assert choose("sensor.gas", "Gasmeter", None, device_class="gas").icon == "energy"
    assert choose("binary_sensor.gas", "Gasmelder", None, device_class="gas").icon == "smoke"
    assert choose("vacuum.robot", "Robot", None) == Choice("vacuum", "domain")
    assert choose("camera.drive", "Oprit", None) == Choice("camera", "domain")
    assert choose("lawn_mower.robot", "Maaier", None) == Choice("device", "none")


def test_a_pet_is_a_pet_but_its_feeder_is_not() -> None:
    assert choose("device_tracker.hond", "Hond", None).icon == "pet"
    assert choose("person.resident_1", "Bewoner", None).icon == "person"
    assert choose("switch.voerbak_kat", "Voerbak kat", "appliance_power").icon == "plug"


def test_a_room_gets_the_icon_of_its_role_or_its_own() -> None:
    assert area_icon(Area("living", "Woonkamer"), "living") == "sofa"
    assert area_icon(Area("attic", "Zolder"), "other") == "room"
    assert area_icon(Area("den", "Hol", icon="mdi:bed"), "other") == "bed"


def test_the_icon_shows_the_state() -> None:
    """A lamp at 35%, a curtain that is closed, a shutter half down, an open lock, an open door,
    a battery's level."""

    def state(value: str, **attrs: object) -> State:
        return State("x.y", value, dict(attrs), NOW)

    lamp = Entity("light.a", "reg-a", "Lamp")
    assert with_state("lamp", lamp, state("on", brightness=89)) == {"icon": "lamp", "pct": 35}
    assert with_state("lamp", lamp, state("off")) == {"icon": "lamp"}
    cover = Entity("cover.a", "reg-c", "Gordijn")
    assert with_state("curtains", cover, state("closed"))["icon"] == "curtains_closed"
    assert with_state("shutter", cover, state("open", current_position=40))["closed"] == 60
    lock = Entity("lock.a", "reg-l", "Slot")
    assert with_state("lock", lock, state("unlocked"))["icon"] == "unlock"
    battery = Entity("sensor.bat", "reg-b", "Thuisbatterij")
    assert with_state("battery", battery, state("62"))["level"] == 62


def test_the_app_draws_every_icon_the_server_chooses() -> None:
    """The keys of the set are the same in the server and in the app."""
    source = (ROOT / "frontend" / "src" / "lib" / "deviceicons.ts").read_text()
    block = source[source.index("DEVICE_PATHS: Record") : source.index("};")]
    drawn = set(re.findall(r"^\s+([a-z_]+): \"", block, re.M)) | {"garage"}
    assert drawn == set(ICONS)
