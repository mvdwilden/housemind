"""The house goes to sleep at night (E-88), also with lights that are no sign of someone awake.
Found in the real house: in ten days the house never slept at night, so HouseMind learned no
bedtime and no wake-up time. In the bedrooms a presence sensor's status LED and two cupboard
lamps were 'on' day and night; each counted as "a bedroom light is on". Each test fails
without the fix.

Found again (E-520): the presence sensor in the hall downstairs said "someone" for hours on
many nights – on every weekend night so far – so the house never slept and Brein › Ritme had
no weekend at all. In a hall people pass: coming in counts, staying does not."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.core.clock import SimulatedClock
from housemind.core.model import Device, Entity, Source, State
from housemind.world.house_mode import HouseMode
from tests.test_house_modes import _start, _ticks

FRIDAY_NOON = datetime(2026, 10, 23, 12, 0, tzinfo=ZoneInfo("Europe/Amsterdam")).astimezone(UTC)


async def _quiet_night(tmp_path: Path, start: datetime | None = None) -> tuple[object, object]:
    """The demo house at 22:30, everyone home; someone just left the living room."""
    hm, ha = await _start(tmp_path, SimulatedClock(start) if start else None)
    await ha.simulate("binary_sensor.living_presence", "on")  # type: ignore[attr-defined]
    await hm.clock.advance(10.5 * 3600)  # type: ignore[attr-defined]  # noon to 22:30
    await hm.tick()
    assert hm.world.modes.mode is HouseMode.HOME  # type: ignore[attr-defined]
    await ha.simulate("binary_sensor.living_presence", "off")  # type: ignore[attr-defined]
    return hm, ha


async def _add(hm: object, ha: object, entity: Entity, state: str, since_hours: float) -> None:
    clock = hm.clock  # type: ignore[attr-defined]
    since = clock.now() - timedelta(hours=since_hours)
    await ha.add_entity(entity, State(entity.entity_id, state, {}, since))  # type: ignore[attr-defined]
    hm.model.entities[entity.entity_id] = entity  # type: ignore[attr-defined]
    hm.world.states[entity.entity_id] = State(entity.entity_id, state, {}, since)  # type: ignore[attr-defined]


async def test_a_sensors_led_and_an_always_on_cupboard_lamp_let_the_house_sleep(
    tmp_path: Path,
) -> None:
    """A presence sensor in the child's bedroom has a status LED that HA shows as a light, and
    a cupboard lamp in the bedroom has been on for two days. Neither keeps the house awake:
    after 20 quiet minutes at night the house sleeps."""
    hm, ha = await _quiet_night(tmp_path)
    sensor = Device("sensor-child", "Aanwezigheid", "bedroom_child")
    ha.model.devices["sensor-child"] = hm.model.devices["sensor-child"] = sensor  # type: ignore[attr-defined]
    await _add(
        hm,
        ha,
        Entity(
            "binary_sensor.child_room_presence",
            "r-cp",
            "Aanwezigheid",
            "sensor-child",
            "bedroom_child",
            "occupancy",
        ),
        "off",
        48,
    )
    await _add(
        hm,
        ha,
        Entity("light.child_room_sensor_led", "r-led", "LED", "sensor-child", "bedroom_child"),
        "on",
        48,
    )
    await _add(hm, ha, Entity("light.bedroom_cupboard", "r-cup", "Kast", None, "bedroom"), "on", 48)
    await hm.tick()
    # A reconnect of the cupboard lamp's radio network makes it 'unavailable' for a moment:
    # it was on all along, so it still counts as always on.
    await ha.simulate("light.bedroom_cupboard", "unavailable", by=Source.UNKNOWN)  # type: ignore[attr-defined]
    await ha.simulate("light.bedroom_cupboard", "on", by=Source.UNKNOWN)  # type: ignore[attr-defined]
    await _ticks(hm, 45)  # type: ignore[arg-type]
    assert hm.world.modes.mode is HouseMode.SLEEP  # type: ignore[attr-defined]


async def test_a_lamp_someone_switched_on_keeps_the_house_awake(tmp_path: Path) -> None:
    """The other side: someone reading in bed with the bedroom lamp on is awake."""
    hm, ha = await _quiet_night(tmp_path)
    await ha.simulate("light.bedroom", "on", by=Source.PHYSICAL)  # type: ignore[attr-defined]
    await _ticks(hm, 45)  # type: ignore[arg-type]
    assert hm.world.modes.mode is HouseMode.HOME  # type: ignore[attr-defined]


async def test_always_on_is_remembered_over_a_restart(tmp_path: Path) -> None:
    """When HouseMind restarts, a lamp that was already on for hours keeps its start: after a
    restart it does not count as just switched on."""
    hm, ha = await _quiet_night(tmp_path)
    await _add(hm, ha, Entity("light.bedroom_cupboard", "r-cup", "Kast", None, "bedroom"), "on", 48)
    await hm.tick()
    first = hm.world.lamps_on_since["light.bedroom_cupboard"]  # type: ignore[attr-defined]
    await hm.stop()  # type: ignore[attr-defined]
    hm2, _ = await _start(tmp_path)
    assert hm2.world.lamps_on_since["light.bedroom_cupboard"] == first  # type: ignore[union-attr]
    await hm2.stop()


async def _hall_presence(hm: object, ha: object) -> str:
    """A presence sensor in the hall – off until now."""
    entity = Entity("binary_sensor.hall_presence", "r-hp", "Aanwezigheid", None, "hall", "presence")
    await _add(hm, ha, entity, "off", 48)
    hm.world.occupancy.index()  # type: ignore[attr-defined]
    return entity.entity_id


async def test_a_hall_sensor_that_keeps_seeing_someone_lets_the_house_sleep(
    tmp_path: Path,
) -> None:
    """E-520, found in the real house: on a Friday evening the presence sensor in the hall
    comes on and says "someone" all night, while nobody stands in the hall. In a hall people
    pass: coming in counts, staying does not. After 20 quiet minutes the house sleeps, and its
    bedtime and the next morning's wake-up count for the weekend in Brein › Ritme."""
    hm, ha = await _quiet_night(tmp_path, FRIDAY_NOON)
    hall = await _hall_presence(hm, ha)
    await hm.tick()  # type: ignore[attr-defined]
    await ha.simulate(hall, "on")  # type: ignore[attr-defined]
    await _ticks(hm, 45)  # type: ignore[arg-type]
    assert hm.world.modes.mode is HouseMode.SLEEP  # type: ignore[attr-defined]
    assert len(hm.rhythm.bed.get("free", [])) == 1  # type: ignore[attr-defined]
    # Saturday morning the sensor still sees "someone"; the house sleeps on until someone
    # really comes into the living room.
    await _ticks(hm, 540)  # type: ignore[arg-type]
    assert hm.world.modes.mode is HouseMode.SLEEP  # type: ignore[attr-defined]
    await ha.simulate("binary_sensor.living_presence", "on")  # type: ignore[attr-defined]
    await hm.tick()  # type: ignore[attr-defined]
    assert hm.world.modes.mode is HouseMode.HOME  # type: ignore[attr-defined]
    assert len(hm.rhythm.wake.get("free", [])) == 1  # type: ignore[attr-defined]


async def test_someone_on_the_sofa_keeps_the_house_awake(tmp_path: Path) -> None:
    """The other side: in the living room a presence sensor that keeps seeing someone is
    someone on the sofa – the house does not go to sleep."""
    hm, ha = await _quiet_night(tmp_path, FRIDAY_NOON)
    await ha.simulate("binary_sensor.living_presence", "on")  # type: ignore[attr-defined]
    await _ticks(hm, 45)  # type: ignore[arg-type]
    assert hm.world.modes.mode is HouseMode.HOME  # type: ignore[attr-defined]
