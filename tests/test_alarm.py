"""The alarm (E-121, E-188; design 16.3, 16.6) and alarms a camera hears (E-185): the reaction
when it goes off, only asking to switch it on – once, when everyone leaves and when the house
goes to sleep – never switching it off, and its state in the house mode. Each test fails
without the function; what it checks is its docstring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from housemind.brain import security
from housemind.brain.speech import speakers
from housemind.core.model import Entity, State
from housemind.server import views
from housemind.world.house_mode import HouseMode, HouseModeMachine, ModeInputs
from tests.test_cameras import EVENING, camera_pushes, everyone_away, fire, start

ALARM = "alarm_control_panel.home"


def _alarm_attrs(hm, **attrs) -> None:  # type: ignore[no-untyped-def]
    state = hm.world.states[ALARM]
    hm.world.states[ALARM] = State(
        ALARM, state.state, {**state.attributes, **attrs}, state.last_changed
    )


async def test_when_the_alarm_goes_off_lights_covers_voice_and_an_image(tmp_path: Path) -> None:
    """E-121: all lights on, the window coverings open – never the garage door, the gate or
    the awning –, a spoken warning, and an emergency message with a camera's image to every
    phone. When it stops, the message goes."""
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1})
    satellite = "assist_satellite.hall"
    hm.model.entities[satellite] = Entity(satellite, "r-sat", "Spraak", area_id="hall")
    ha.states[satellite] = State(satellite, "idle", {}, clock.now())
    hm.speech.devices = speakers(hm.model)
    await ha.simulate("cover.bedroom_shutter", "closed")
    await ha.simulate("cover.living_curtain", "closed")
    # Only what an administrator chose opens (QA 2, B-170): curtains, blinds and shades –
    # a shutter (a garage roller door often is one) or a garage door cannot be chosen.
    assert await security.set_setting(
        hm,
        "alarm_open_covers",
        ["cover.living_curtain", "cover.bedroom_shutter", "cover.garage_door"],
    )
    assert hm.security.open_covers == {"cover.living_curtain"}
    await ha.simulate(ALARM, "triggered")
    assert ha.states["light.living_ceiling"].state == "on"
    assert ha.states["light.hall"].state == "on"
    assert ha.states["cover.living_curtain"].state == "open"
    for closed in (
        "cover.bedroom_shutter",
        "cover.garage_door",
        "cover.garden_gate",
        "cover.garden_awning",
    ):
        assert ha.states[closed].state == "closed"
    pushes = [n for n in ha.notifications if n[3].get("tag") == f"alarm:{ALARM}"]
    assert {p[0] for p in pushes} == {f"notify.mobile_app_phone_{i}" for i in (1, 2, 3, 4)}  # E-291
    _, title, message, data = pushes[0]
    assert "nood" in title and message.startswith("Het alarm gaat af")
    lamps = sum(  # the lamps themselves, not their groups as well (E-458)
        1
        for e, s in ha.states.items()
        if e.startswith("light.") and s.state == "on" and e not in hm.model.groups
    )
    assert f"Ik heb {lamps} lampen aangedaan." in message  # counted, not "all" (QA 2, M-15)
    assert data["push"]["interruption-level"] == "critical"
    assert data["image"].startswith("/api/camera_proxy/camera.")
    assert [target for target, _ in ha.spoken] == [satellite]  # on every speaker
    await ha.simulate(ALARM, "disarmed")
    assert not [n for n in await hm.notifications() if n["topic"] == f"alarm:{ALARM}"]
    await hm.stop()


async def test_without_a_choice_the_alarm_opens_nothing(tmp_path: Path) -> None:
    """QA 2 (H-3, B-170): the alarm opened every cover of the class shutter – and a garage
    roller door is often a shutter. Now nothing opens until an administrator chooses which
    window coverings may; the lights still go on."""
    hm, _clock, ha = await start(tmp_path, {1: 2, 2: 1})
    await ha.simulate("cover.bedroom_shutter", "closed")
    view = security.settings_view(hm, "nl")
    offered = [c["id"] for c in view["coverings"]]
    assert view["hasAlarm"] and view["open"] == []
    assert "cover.living_curtain" in offered and "cover.office_blinds" in offered
    for never in ("cover.bedroom_shutter", "cover.garage_door", "cover.garden_gate"):
        assert never not in offered
    await ha.simulate(ALARM, "triggered")
    assert ha.states["light.hall"].state == "on"
    assert ha.states["cover.bedroom_shutter"].state == "closed"
    await hm.stop()


async def test_when_everyone_leaves_one_question_and_a_yes_switches_it_on(tmp_path: Path) -> None:
    """E-188: "Alarm op afwezig zetten?" once, to who left; the yes on the phone switches it
    on through the safety gate. Afterwards no second question."""
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1})
    await everyone_away(hm, clock, ha)
    await hm.tick()
    asked = [n for n in ha.notifications if n[3].get("tag") == "alarm_arm:away"]
    assert [a[0] for a in asked] == ["notify.mobile_app_phone_1"]  # not Carefree
    assert "Zal ik het op afwezig zetten?" in asked[0][2]
    yes = asked[0][3]["actions"][0]
    assert yes["title"] == "Ja, graag"
    items = await views.notifications(hm, "nl", True, "demo-user-1")
    assert any(i["actions"] == ["plan_yes", "plan_no"] for i in items)  # in the app: "Ja, graag"
    await ha.press_notification_action(yes["action"])
    assert ha.states[ALARM].state == "armed_away"
    await clock.advance(600)
    await hm.tick()
    assert len([n for n in ha.notifications if n[3].get("tag") == "alarm_arm:away"]) == 1
    await hm.stop()


async def test_with_a_code_the_question_opens_the_alarm_in_home_assistant(tmp_path: Path) -> None:
    """E-188: HouseMind keeps no codes – when the alarm needs one, the message opens the
    alarm in Home Assistant instead of a yes."""
    hm, clock, ha = await start(tmp_path)
    _alarm_attrs(hm, code_arm_required=True, code_format="number")
    await everyone_away(hm, clock, ha)
    asked = [n for n in ha.notifications if n[3].get("tag") == "alarm_arm:away"]
    assert asked and "actions" not in asked[0][3]
    assert asked[0][3]["url"] == f"entityId:{ALARM}"
    assert "Tik hier" in asked[0][2]
    await hm.stop()


async def test_going_to_sleep_asks_for_the_night_setting_or_home(tmp_path: Path) -> None:
    """E-188: when the house goes to sleep, "Alarm op nacht zetten?" – the home setting when
    the alarm has no night setting. Without a yes nothing is switched."""
    hm, clock, ha = await start(tmp_path, at=EVENING)
    _alarm_attrs(hm, supported_features=3)  # home and away, no night
    assert hm.world is not None
    await hm.tick()
    hm.world.modes.set_manual(HouseMode.SLEEP, clock.now())
    await hm.tick()
    asked = [n for n in ha.notifications if str(n[3].get("tag")).startswith("alarm_arm:")]
    assert [a[3]["tag"] for a in asked] == ["alarm_arm:home"]
    assert "op thuis zetten?" in asked[0][2]
    assert ha.states[ALARM].state == "disarmed"
    await hm.stop()


def _second_alarm(hm, ha, clock, entity_id: str = "alarm_control_panel.garage") -> str:  # type: ignore[no-untyped-def]
    """A second alarm in the house, as an alarm of the router next to the real one."""
    hm.model.entities[entity_id] = Entity(
        entity_id, f"r-{entity_id}", "Garage alarm", area_id="garage"
    )
    hm.world.states[entity_id] = State(
        entity_id, "disarmed", {"supported_features": 2}, clock.now()
    )
    ha.states[entity_id] = hm.world.states[entity_id]
    security.rebuild(hm)
    return entity_id


async def test_with_two_alarms_and_none_in_use_an_administrator_chooses_once(
    tmp_path: Path,
) -> None:
    """B-67, E-311: two alarms, neither switched on lately – HouseMind does not guess. The first
    time it would ask, one question goes to an administrator, with a button per alarm that
    says its name, on the phone and in the app; a tap makes that one "the alarm"."""
    hm, clock, ha = await start(tmp_path, {1: 4, 2: 2})
    assert hm.world is not None
    other = _second_alarm(hm, ha, clock)
    await everyone_away(hm, clock, ha)
    topics = [str(r["topic"]) for r in await hm.db.fetchall("SELECT topic FROM notifications")]
    assert "alarm_choice" in topics and not any(t.startswith("alarm_arm:") for t in topics)
    asked = [n for n in ha.notifications if n[3].get("tag") == "alarm_choice"]
    assert [a[0] for a in asked] == ["notify.mobile_app_phone_1"]  # only an administrator
    names = [hm.model.entities[e].name for e in (other, ALARM)]
    assert [b["title"] for b in asked[0][3]["actions"]] == names
    items = await views.notifications(hm, "nl", True, "demo-user-1")
    choice = next(i for i in items if i["actions"] == [other, ALARM])
    assert choice["labels"] == dict(zip([other, ALARM], names, strict=True))
    assert not [
        i
        for i in await views.notifications(hm, "nl", False, "demo-user-2")
        if i["actions"] == [other, ALARM]
    ]
    view = security.settings_view(hm, "nl")
    assert [a["id"] for a in view["alarms"]] == [other, ALARM] and view["auto"] is None
    await ha.press_notification_action(asked[0][3]["actions"][1]["action"])  # the second: ALARM
    assert hm.world.alarm_entity == ALARM and security.the_alarm(hm) == ALARM
    assert "alarm_choice" not in hm._raised
    assert not await hm.set_house_setting("alarm_entity", "alarm_control_panel.nothing")
    await hm.stop()


async def test_the_alarm_in_use_is_taken_by_itself(tmp_path: Path) -> None:
    """E-311, a wish from the real house: two alarms, only one was switched on in the last
    days (Home Assistant's history). HouseMind takes that one by itself – no question – and
    House settings says so. An administrator's choice always wins over it."""
    hm, clock, ha = await start(tmp_path)
    assert hm.world is not None
    ha.record(ALARM, "armed_away", clock.now() - timedelta(days=3))
    ha.record(ALARM, "disarmed", clock.now() - timedelta(days=3, hours=-4))
    other = _second_alarm(hm, ha, clock)
    await hm.tick()
    assert security.the_alarm(hm) == ALARM and hm.world.alarm_entity == ALARM
    view = security.settings_view(hm, "nl")
    assert view["auto"] == ALARM and view["alarm"] is None
    assert {a["id"]: a["inUse"] for a in view["alarms"]} == {other: False, ALARM: True}
    await everyone_away(hm, clock, ha)
    topics = [str(r["topic"]) for r in await hm.db.fetchall("SELECT topic FROM notifications")]
    assert "alarm_arm:away" in topics and "alarm_choice" not in topics
    assert await hm.set_house_setting("alarm_entity", other)  # the choice wins
    assert security.the_alarm(hm) == other
    await hm.stop()


async def test_two_alarms_in_use_are_not_guessed(tmp_path: Path) -> None:
    """E-311: both were switched on lately – then it is not clear, and an administrator is
    asked. An alarm switched on now counts as in use at once."""
    hm, clock, ha = await start(tmp_path)
    ha.record(ALARM, "armed_night", clock.now() - timedelta(days=2))
    other = _second_alarm(hm, ha, clock)
    await hm.tick()
    assert security.the_alarm(hm) == ALARM
    await ha.simulate(other, "armed_away")
    assert security.the_alarm(hm) is None and hm.security.in_use == {ALARM, other}
    await hm.stop()


def test_the_alarm_counts_in_the_house_mode() -> None:
    """16.6: armed away is a strong sign for away – at once, not after ten minutes; armed for
    the night means asleep, also before 21:00, and the house stays asleep while it is."""
    base = datetime(2026, 10, 19, 20, 0, tzinfo=UTC)

    def inputs(
        at: datetime, residents: int, alarm: str, activity: datetime | None = None
    ) -> ModeInputs:
        return ModeInputs(at, residents, frozenset(), activity, False, alarm)

    machine = HouseModeMachine()
    machine.update(inputs(base, 0, "disarmed"))
    assert machine.mode is HouseMode.HOME  # without the alarm: ten minutes first
    machine.update(inputs(base + timedelta(minutes=1), 0, "armed_away"))
    assert machine.mode is HouseMode.AWAY
    machine = HouseModeMachine()
    machine.update(inputs(base, 2, "armed_night", activity=base))
    assert machine.mode is HouseMode.SLEEP
    morning = base + timedelta(hours=10)
    machine.update(inputs(morning, 2, "armed_night", activity=morning))
    assert machine.mode is HouseMode.SLEEP  # still armed for the night
    machine.update(inputs(morning, 2, "disarmed", activity=morning))
    assert machine.mode is HouseMode.HOME


async def test_a_smoke_alarm_a_camera_hears_is_an_emergency_sensor(tmp_path: Path) -> None:
    """E-185, E-80: a camera's own "smoke alarm heard" sensor without Home Assistant's smoke
    class is an emergency sensor like any other – outside only a message (E-178)."""
    hm, clock, ha = await start(tmp_path)
    assert hm.world is not None and hm.emergencies is not None
    sensor = "binary_sensor.garden_smoke_alarm"
    entity = Entity(sensor, "r-smoke", "Tuin rookalarm gehoord", "device.garden_camera", "garden")
    hm.model.entities[sensor] = entity
    hm.world.states[sensor] = State(sensor, "off", {}, clock.now())
    ha.states[sensor] = hm.world.states[sensor]
    security.rebuild(hm)
    assert hm.emergencies.emergency_for(sensor) is not None
    await ha.simulate(sensor, "on")
    outside = [n for n in await hm.notifications() if n["topic"] == f"emergency:{sensor}"]
    assert [n["text_key"] for n in outside] == ["notify.outside_smoke"]
    await hm.stop()


async def test_a_smoke_alarm_heard_inside_is_an_emergency_with_the_lights(tmp_path: Path) -> None:
    """E-185, E-80: smoke heard by a camera inside (an event, no sensor that stays on): the
    emergency message and the lights on to see the way out; it ends ten minutes after the
    last time it was heard."""
    hm, clock, ha = await start(tmp_path)
    assert hm.world is not None and hm.cameras is not None
    sensor = "event.hall_sound"
    hm.model.entities[sensor] = Entity(
        sensor,
        "r-sound",
        "Gang geluid",
        "device.hall_camera",
        "hall",
        capabilities={"event_types": ["smoke", "glass_break"]},
    )
    hm.world.states[sensor] = State(sensor, "unknown", {}, clock.now())
    ha.states[sensor] = hm.world.states[sensor]
    security.rebuild(hm)
    await fire(ha, clock, sensor, "smoke")
    heard = [n for n in ha.notifications if n[3].get("tag") == f"emergency:{sensor}"]
    assert heard and heard[0][2].startswith("Een camera hoort een rookmelder in de Gang")
    assert ha.states["light.hall"].state == "on"
    await clock.advance(11 * 60)
    await hm.tick()
    assert not [n for n in await hm.notifications() if n["topic"] == f"emergency:{sensor}"]
    assert camera_pushes(ha) == []
    await hm.stop()


async def test_the_question_is_of_the_moment(tmp_path: Path) -> None:
    """Found in the week in the time machine: "Alarm op nacht zetten?" stayed open all night
    and kept Vesta orange. Now a question goes after 30 minutes; and a late yes on the phone,
    when someone is home again, does not switch the alarm on (it could go off)."""
    hm, clock, _ha = await start(tmp_path, at=EVENING)
    assert hm.world is not None
    await hm.tick()
    hm.world.modes.set_manual(HouseMode.SLEEP, clock.now())
    await hm.tick()
    assert "alarm_arm:night" in hm._raised
    await clock.advance(31 * 60)
    await hm.tick()
    assert "alarm_arm:night" not in hm._raised
    hm2, clock2, ha2 = await start(tmp_path / "late")
    await everyone_away(hm2, clock2, ha2)
    asked = [n for n in ha2.notifications if n[3].get("tag") == "alarm_arm:away"]
    await ha2.simulate("person.resident_1", "home")
    await hm2.tick()
    await ha2.press_notification_action(asked[0][3]["actions"][0]["action"])
    assert ha2.states[ALARM].state == "disarmed"
    await hm.stop()
    await hm2.stop()
