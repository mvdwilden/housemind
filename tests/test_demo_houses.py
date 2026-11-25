"""The three demo houses of the App options (the administrator's wish, 20-10-2026): a simple
house with five to twelve devices, an average house with twenty to forty, and an extended house
with every kind of device HouseMind knows. The test houses (apartment, family home, energy
home) stay as they are."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from housemind.__main__ import _demo_choice
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import SHOWN_HOUSES, TEST_HOUSES, DemoHouse, load_demo_house
from housemind.knowledge.catalog import Catalog
from housemind.knowledge.matcher import recognise
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
MONDAY = datetime(2026, 10, 19, 6, 0, tzinfo=UTC)

# Kinds in the catalogue the extended house leaves out, and why.
NOT_SHOWN = {
    # HouseMind never decides by itself that a switch is a lamp: nothing to show.
    "switch.light": "a switch counts as a lamp only by hand",
    # One charge point switches phases with a select or a number, never both.
    "number.charger_phases": "the charge point shows the select instead",
}
# Kinds without a catalogue entry of their own that HouseMind still does something with.
AROUND_THE_HOUSE = ("media_player", "vacuum", "lawn_mower", "event", "weather")


def _devices(house: DemoHouse) -> int:
    """Devices a person put in: not the phone, not the weather service."""
    return sum(
        1
        for device_id in house.ha.model.devices
        if device_id != "device.phone_1" and not device_id.startswith("weather.")
    )


def test_the_simple_house_has_five_to_twelve_devices() -> None:
    house = load_demo_house("simple", SimulatedClock(MONDAY), "nl")
    assert 5 <= _devices(house) <= 12
    assert house.name == "Eenvoudig huis"


def test_the_average_house_has_twenty_to_forty_devices() -> None:
    house = load_demo_house("average", SimulatedClock(MONDAY), "nl")
    assert 20 <= _devices(house) <= 40
    assert house.name == "Gemiddeld huis"


async def test_the_extended_house_has_every_kind_of_device(
    catalog: Catalog, tmp_path: Path
) -> None:
    clock = SimulatedClock(MONDAY)
    house = load_demo_house("extended", clock, "en")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    assert hm.gate is not None
    roles = hm.gate.state.roles  # the energy setup and the garage door give roles
    found = {
        kind.key
        for entity in hm.model.entities.values()
        if (kind := recognise(catalog, entity, roles.get(entity.entity_id)).kind) is not None
    }
    wanted = {kind.key for kind in catalog.kinds.values() if not kind.generic} - set(NOT_SHOWN)
    assert wanted <= found, sorted(wanted - found)
    domains = {entity.domain for entity in hm.model.entities.values()}
    assert set(AROUND_THE_HOUSE) <= domains
    # The light group steers its two pendants; the washer and the dishwasher may start later.
    assert hm.model.groups.get("light.dining_table") == ("light.dining_left", "light.dining_right")
    assert hm.energy is not None and len(hm.energy.setup.appliances) == 2
    await hm.stop()


def test_the_app_offers_exactly_the_three_houses() -> None:
    config = yaml.safe_load((ROOT / "ha_app_stable" / "config.yaml").read_text())
    choices = re.fullmatch(r"list\((.*)\)", config["schema"]["demo_home"])
    assert choices is not None and tuple(choices.group(1).split("|")) == SHOWN_HOUSES
    assert config["options"]["demo_home"] in SHOWN_HOUSES
    for language in ("nl", "en"):
        texts = yaml.safe_load(
            (ROOT / "ha_app_stable" / "translations" / f"{language}.yaml").read_text()
        )
        assert texts["configuration"]["demo_home"]["name"]


def test_an_older_choice_still_starts_a_house() -> None:
    assert _demo_choice({"demo_home": "extended"}) == "extended"
    assert _demo_choice({"demo_house": "family_home"}) == "family_home"  # an older App
    assert _demo_choice({"demo_house": "castle"}) == "average"
    assert _demo_choice({}) == "average"
    assert set(TEST_HOUSES).isdisjoint(SHOWN_HOUSES)


@pytest.mark.parametrize("key", SHOWN_HOUSES)
async def test_a_day_in_each_house_brings_proposals(key: str, tmp_path: Path) -> None:
    """Someone trying the demo sees HouseMind notice and propose on the first day."""
    from housemind.brain.proposals import Answer
    from housemind.demo.timemachine import standard_routine

    clock = SimulatedClock(MONDAY)
    house = load_demo_house(key, clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident", 3, "{}", clock.now().isoformat()),
    )
    await hm._refresh_people()
    import random

    steps = sorted(standard_routine(house, UTC)(MONDAY.date(), random.Random(1)))
    asked = 0
    end = MONDAY + timedelta(hours=18)
    while clock.now() < end:
        target = clock.now() + timedelta(minutes=5)
        while steps and steps[0].at <= target:
            step = steps.pop(0)
            if step.at > clock.now():
                await clock.advance((step.at - clock.now()).total_seconds())
            await house.ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)
        await clock.advance((target - clock.now()).total_seconds())
        await hm.tick()
        for proposal in await hm.open_proposals():
            asked += 1
            await hm.answer(proposal.id, Answer.DO, "demo-user-1")
    assert asked > 0
    await hm.stop()
