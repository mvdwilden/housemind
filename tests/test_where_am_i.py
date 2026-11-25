""" "Waar ben ik?" (wish of the administrator, 20-10-2026): Vesta could not answer it. With a
room sensor on the phone she knows the room; otherwise she says honestly where she sees
someone, without pretending to know who is who."""

from __future__ import annotations

from pathlib import Path

from housemind.core.model import Entity, State
from tests.test_notify import start
from tests.test_vesta import PHRASES


def _lines(topic: str) -> list[str]:
    return [line.split("{")[0].strip() for line in PHRASES.variants("nl", topic, False)]


async def test_with_a_room_sensor_on_the_phone_she_knows_the_room(tmp_path: Path) -> None:
    hm, _clock, ha = await start(tmp_path, {1: 2})
    kitchen = hm.model.areas["kitchen"].name
    now = hm.clock.now()
    tracker = Entity("device_tracker.phone_1", "t1", "Phone 1", "device.phone_1")
    area = Entity("sensor.phone_1_area", "a1", "Phone 1 area", "device.phone_1")
    await ha.add_entity(tracker, State(tracker.entity_id, "home", {}, now))
    await ha.add_entity(area, State(area.entity_id, kitchen, {}, now))
    await hm._refresh_model()
    reply = await hm.talk("Waar ben ik?", "nl", 2, "demo-user-1")
    assert "keuken" in reply["text"].lower(), reply["text"]
    await hm.stop()


async def test_without_one_she_says_where_she_sees_someone(tmp_path: Path) -> None:
    hm, _clock, ha = await start(tmp_path, {1: 2})
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    reply = await hm.talk("Waar ben ik?", "nl", 2, "demo-user-1")
    assert ("keuken" in reply["text"].lower() and "waarschijnlijk" in reply["text"]) or any(
        reply["text"].startswith(start_) for start_ in _lines("know_me_maybe")
    ), reply["text"]
    await ha.simulate("binary_sensor.living_presence", "on")
    reply = await hm.talk("Waar ben ik?", "nl", 2, "demo-user-1")
    text = reply["text"]
    assert "in de keuken" in text and "in de woonkamer" in text, text  # not who is who
    assert any(text.startswith(line[:12]) for line in _lines("know_me_unsure")), text
    await hm.stop()
