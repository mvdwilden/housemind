"""A device that drops off the network and comes back is nobody's hand (E-514).

What the real house showed (13-11-2026): the curtain motors went 'unavailable' for ten seconds a
few times a day and came back as they were. HouseMind took each return for a hand on them and
stayed off them for six hours – so at five in the evening it might not close any of them, all on
Autonomous, and Test het huis said "Niet: Iemand heeft dit apparaat met de hand bediend." In the
same panel the kitchen showed 77 °C: an oven's own sensor, not the room. Each test fails without
the change; what it checks is its docstring.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from housemind.core.model import Source
from housemind.safety.gate import ActionRequest, Layer
from tests.test_real_house_counts import _add, _house
from tests.test_rhythm import until

CURTAIN = "cover.living_curtain"


def _close(hm: object) -> ActionRequest:
    return ActionRequest(
        Layer.PLANNER,
        CURTAIN,
        "cover.close_cover",
        {},
        skill="covers_close_dark",
        area_id="living",
        created_at=hm.clock.now(),  # type: ignore[attr-defined]
    )


async def test_a_curtain_back_on_the_network_is_no_hand(tmp_path: Path) -> None:
    """The curtain is gone for ten seconds and comes back open: nothing keeps HouseMind off it.
    A real hand on it still does, as before."""
    hm, clock, ha = await _house(tmp_path)
    assert hm.gate is not None
    await ha.simulate(CURTAIN, "unavailable", by=Source.PHYSICAL)
    await until(clock, hm, clock.now() + timedelta(seconds=10))
    await ha.simulate(CURTAIN, "open", by=Source.PHYSICAL)
    await until(clock, hm, clock.now() + timedelta(seconds=5))
    assert CURTAIN not in hm.gate.state.people_win_until
    assert hm.gate.evaluate(_close(hm)).reason != "people_win"
    await ha.simulate(CURTAIN, "closed", by=Source.PHYSICAL)  # someone closes it by hand
    await until(clock, hm, clock.now() + timedelta(seconds=5))
    assert CURTAIN in hm.gate.state.people_win_until
    assert hm.gate.evaluate(_close(hm)).reason == "people_win"
    await hm.stop()


async def test_test_het_huis_shows_the_room_not_an_oven(tmp_path: Path) -> None:
    """Test het huis says what the room measures: the living room its thermostat's 20,1 °C,
    the kitchen nothing about its oven's 77 °C – and no 'Warmer' or 'Kouder' for a room that
    only has an oven to measure."""
    hm, _clock, _ha = await _house(
        tmp_path,
        prepare=lambda ha: _add(
            ha,
            "sensor.oven_temperature",
            "Oven",
            "kitchen",
            "77",
            device_class="temperature",
            unit_of_measurement="°C",
        ),
    )
    kitchen = hm.test_view("nl", "kitchen", [])
    assert "77" not in kitchen["now"]
    assert not {"warmer", "colder"} & {s["key"] for s in kitchen["situations"]}
    assert "20,1 °C" in hm.test_view("nl", "living", [])["now"]
    await hm.stop()
