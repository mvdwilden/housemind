"""An alarm heard or measured outside (found in a real house before its trial): cameras on the
terrace and the driveway report "smoke alarm heard" and "CO alarm heard". Outside, that is a
message – also at night – and nothing more; inside, the full emergency reaction stays."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Entity, State
from housemind.demo.houses import load_demo_house
from housemind.runtime import HouseMind, Paths

ROOT = Path(__file__).resolve().parents[1]
NIGHT = datetime(2026, 10, 20, 3, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


async def test_a_smoke_alarm_heard_outside_is_a_message_inside_the_full_reaction(
    tmp_path: Path,
) -> None:
    clock = SimulatedClock(NIGHT.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    ha = house.ha
    for entity_id, area in (
        ("binary_sensor.terrace_camera_smoke", "garden"),
        ("binary_sensor.hall_camera_smoke", "hall"),
    ):
        ha.model.entities[entity_id] = Entity(
            entity_id, f"r-{entity_id}", "Rookalarm gehoord", area_id=area, device_class="smoke"
        )
        ha.states[entity_id] = State(entity_id, "off", {}, clock.now())
    hm = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
    )
    await hm._refresh_people()
    assert hm.area_roles["garden"] == "outside" and hm.area_roles["hall"] == "hall"

    before = len(ha.calls)
    await ha.simulate("binary_sensor.terrace_camera_smoke", "on")
    assert len(ha.calls) == before  # no lamp, no speaker
    assert hm.status.emergency is None and hm.status.orb == "attention"  # "Let op", not red
    told = [n for n in await hm.notifications() if n["topic"].endswith("terrace_camera_smoke")]
    assert told and told[0]["kind"] == "emergency"  # it reaches people, also at night
    text = hm.languages.text("nl", told[0]["text_key"], **json.loads(told[0]["params"]))
    assert text.startswith("Een sensor buiten (Tuin) hoorde een rookalarm")
    await ha.simulate("binary_sensor.terrace_camera_smoke", "off")
    assert not [n for n in await hm.notifications() if "terrace" in str(n["topic"])]
    assert hm.status.orb != "attention"

    await ha.simulate("binary_sensor.hall_camera_smoke", "on")
    assert hm.status.emergency is not None and hm.status.orb == "emergency"
    assert any(action == "light.turn_on" for action, _, _ in ha.calls[before:])
    await hm.stop()
