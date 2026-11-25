"""What the house does when the alarm goes off, or at a fire (E-542 – E-546), from the side of
the people at home. The administrator's choice (23-11-2026): "jouw voorstel alarmreactie mag je
gewoon volledig bouwen" – music quiet and the warning again, red signal lamps, the wall
tablet's red screen, buttons on the phone and once more after two minutes, and at a fire the
shutters up, the air off and which door is locked. Generic: what each device reports. Each
test fails without the change; what it checks is its docstring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from housemind.brain import security
from housemind.brain.speech import speakers
from housemind.core.model import Entity, State
from housemind.server import views
from tests.test_cameras import EVENING, start

ALARM = "alarm_control_panel.home"
SIGNAL = "light.living_mood"  # can show colours, cannot flash by itself


async def _house(tmp_path: Path) -> tuple[Any, Any, Any]:
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 1}, at=EVENING)
    satellite = "assist_satellite.hall"
    hm.model.entities[satellite] = Entity(satellite, "r-sat", "Spraak", area_id="hall")
    ha.states[satellite] = State(satellite, "idle", {}, clock.now())
    hm.speech.devices = speakers(hm.model)
    return hm, clock, ha


def _calls(ha: Any, entity_id: str, action: str) -> list[dict[str, Any]]:
    return [d for a, e, d in ha.calls if e == entity_id and a == action]


def _pushes(ha: Any, tag: str) -> list[Any]:
    return [n for n in ha.notifications if n[3].get("tag") == tag]


async def test_a_break_in_quiet_music_red_lamps_and_the_warning_again(tmp_path: Path) -> None:
    """The alarm goes off while the television plays: it pauses; the lamp an administrator
    chose flashes red – HouseMind flashes it itself, calmly, as it cannot – and after three
    minutes it stays on, red; the other lamps are full and white as before. The warning is
    said again every half minute, at most five minutes, and stops when the alarm is off."""
    hm, clock, ha = await _house(tmp_path)
    await ha.simulate("media_player.living_tv", "playing")
    settings = security.settings_view(hm, "nl")
    lamp = next(x for x in settings["signalLamps"] if x["id"] == SIGNAL)
    assert lamp["suggested"]  # a colour lamp in the living room: suggested, not chosen
    assert await security.set_setting(hm, "alarm_signal_lamps", [SIGNAL, "light.hall"])
    assert hm.security.signal == {SIGNAL}  # only lamps that can show red
    await ha.simulate(ALARM, "triggered")
    assert ha.states["media_player.living_tv"].state == "paused"
    assert _calls(ha, SIGNAL, "light.turn_on")[0]["color_name"] == "red"
    assert "color_name" not in _calls(ha, "light.hall", "light.turn_on")[-1]  # white
    assert len(ha.spoken) == 1
    for _ in range(4):
        await clock.advance(1)
        for _ in range(10):  # the flashing runs beside; settled() would wait three minutes
            await asyncio.sleep(0)
    assert _calls(ha, SIGNAL, "light.turn_off")  # flashing
    for _ in range(4):
        await clock.advance(10)
        await hm.tick()
    assert len(ha.spoken) == 2  # again after half a minute
    for _ in range(20):
        await clock.advance(10)
        await hm.tick()
    assert ha.states[SIGNAL].state == "on"  # three minutes: it stays on, red
    flashes = len(_calls(ha, SIGNAL, "light.turn_off"))
    said = len(ha.spoken)
    await ha.simulate(ALARM, "disarmed")
    for _ in range(12):
        await clock.advance(10)
        await hm.tick()
    assert len(ha.spoken) == said and len(_calls(ha, SIGNAL, "light.turn_off")) == flashes
    await hm.stop()


async def test_the_phone_gets_buttons_and_once_more_after_two_minutes(tmp_path: Path) -> None:
    """The emergency message has buttons: the camera, the alarm in Home Assistant, and the
    emergency number of the country. Two minutes later whoever did not react gets it once
    more; whoever did, not."""
    hm, clock, ha = await _house(tmp_path)
    await ha.simulate(ALARM, "triggered")
    first = _pushes(ha, f"alarm:{ALARM}")
    links = [a for a in first[0][3]["actions"] if a["action"] == "URI"]
    assert [a["title"] for a in links] == ["Bekijk de camera", "Naar het alarm", "Bel 112"]
    assert links[1]["uri"] == f"entityId:{ALARM}" and links[2]["uri"] == "tel:112"
    assert links[0]["uri"].startswith("entityId:camera.")
    row = await hm.db.fetchone(
        "SELECT id FROM notifications WHERE topic = ? AND resolved_at IS NULL", (f"alarm:{ALARM}",)
    )
    assert hm.router is not None
    await hm.router.acknowledged(int(row["id"]), "demo-user-1")  # seen in the app
    for _ in range(13):
        await clock.advance(10)
        await hm.tick()
    again = _pushes(ha, f"alarm:{ALARM}")[len(first) :]
    assert again and "notify.mobile_app_phone_1" not in {n[0] for n in again}
    await hm.stop()


async def test_the_wall_tablet_turns_red(tmp_path: Path) -> None:
    """While the alarm goes off the wall tablet shows a red screen: what, where it began and
    when, and the button to the alarm; at a fire only what and where – nothing that distracts.
    When it is over the screen goes."""
    hm, _clock, ha = await _house(tmp_path)
    await ha.simulate("binary_sensor.hall_motion", "on")
    await ha.simulate(ALARM, "triggered")
    screen = views.tablet_view(hm, "hall", "nl")["alarm"]
    assert screen["title"] == "Het alarm gaat af" and screen["where"] == "Gang"
    assert screen["alarm"] == ALARM
    await ha.simulate(ALARM, "disarmed")
    assert views.tablet_view(hm, "hall", "nl")["alarm"] is None
    await ha.simulate("binary_sensor.hall_smoke", "on")
    screen = views.tablet_view(hm, "hall", "nl")["alarm"]
    assert screen["title"] == "Rook" and screen["where"] == "Gang" and "alarm" not in screen
    await ha.simulate("binary_sensor.hall_smoke", "off")
    assert views.tablet_view(hm, "hall", "nl")["alarm"] is None
    await hm.stop()


async def test_at_a_fire_the_shutters_go_up_the_air_off_and_the_lock_said(tmp_path: Path) -> None:
    """Smoke in the hall: the shutter an administrator chose goes up to get out – never a
    garage door –, an air conditioner that blows air goes off, the music pauses, and the
    message says the front door is locked: HouseMind never opens it. The phone has 'Bel 112'."""
    hm, _clock, ha = await _house(tmp_path)
    await ha.simulate("cover.bedroom_shutter", "closed")
    await ha.simulate(
        "climate.living",
        "cool",
        {"hvac_modes": ["heat", "cool", "off"], "fan_modes": ["auto", "low"], "temperature": 21},
    )
    await ha.simulate("media_player.kitchen_speaker", "playing")
    covers = [c["id"] for c in security.settings_view(hm, "nl")["fireCovers"]]
    assert "cover.bedroom_shutter" in covers and "cover.garage_door" not in covers
    assert await security.set_setting(
        hm, "fire_open_covers", ["cover.bedroom_shutter", "cover.garage_door"]
    )
    assert hm.security.fire_covers == {"cover.bedroom_shutter"}
    await ha.simulate("binary_sensor.hall_smoke", "on")
    assert ha.states["cover.bedroom_shutter"].state == "open"
    assert ha.states["cover.garage_door"].state == "closed"
    assert ha.states["climate.living"].state == "off"
    assert ha.states["climate.bedroom"].state == "heat"  # a radiator blows no air
    assert ha.states["media_player.kitchen_speaker"].state == "paused"
    assert ha.states["lock.front_door"].state == "locked"
    push = _pushes(ha, "emergency:binary_sensor.hall_smoke")[0]
    assert "Let op: Voordeur zit op slot." in push[2] or "zit op slot" in push[2]
    assert "rolluiken" in push[2] and "airco" in push[2]
    titles = [a["title"] for a in push[3]["actions"] if a["action"] == "URI"]
    assert titles == ["Bel 112"]
    await hm.stop()


async def test_gas_switches_nothing_more(tmp_path: Path) -> None:
    """Gas: no music paused – nothing that could spark –, no warning again; the tablet and
    the phone with the emergency number, as for the rest."""
    hm, clock, ha = await _house(tmp_path)
    await ha.simulate("media_player.kitchen_speaker", "playing")
    await ha.simulate("binary_sensor.kitchen_gas", "on")
    assert ha.states["media_player.kitchen_speaker"].state == "playing"
    said = len(ha.spoken)
    for _ in range(6):
        await clock.advance(10)
        await hm.tick()
    assert len(ha.spoken) == said
    screen = views.tablet_view(hm, "hall", "nl")["alarm"]
    assert screen["title"] == "Gas"
    await hm.stop()


async def test_only_an_emergency_may_pause_music(tmp_path: Path) -> None:
    """Pausing music is marked in the catalogue for the emergency rule only: an everyday
    request to pause it is never done by HouseMind itself."""
    from housemind.safety.gate import ActionRequest, Layer, Outcome

    hm, _clock, ha = await _house(tmp_path)
    await ha.simulate("media_player.living_tv", "playing")
    assert hm.gate is not None
    decision = hm.gate.evaluate(
        ActionRequest(Layer.REFLEX, "media_player.living_tv", "media_player.media_pause", {}, "x")
    )
    assert decision.outcome is not Outcome.EXECUTED
    await hm.stop()
