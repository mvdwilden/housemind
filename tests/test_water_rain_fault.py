"""Three heads-ups from the proposal for more devices (23-11-2026), from the side of the people
at home: water that runs away quietly in the night (E-535), rain on its way while something is
open (E-536), and a device that reports a fault itself (E-537). Each test fails without the
change; what went wrong is in its docstring."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from housemind.core.model import Device, Entity, State
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind
from tests.test_boiler_cold import start

TZ = ZoneInfo("Europe/Amsterdam")
METER = "sensor.water_meter"
WINDOW = "binary_sensor.back_door"


def _add(ha: FakeHomeAssistant, hm: HouseMind, eid: str, state: str, **kw: Any) -> None:
    """A device HouseMind did not have yet, as Home Assistant reports it."""
    attrs = kw.pop("attrs", {})
    entity = Entity(eid, f"r-{eid}", kw.pop("name", eid), **kw)
    hm.model.entities[eid] = entity
    now = hm.clock.now()
    ha.states[eid] = State(eid, state, attrs, now)
    assert hm.world is not None
    hm.world.states[eid] = ha.states[eid]


async def _quarter(hm: HouseMind, clock: Any) -> None:
    await clock.advance(15 * 60)
    hm._last_review = None
    await hm.tick()


async def _open(hm: HouseMind) -> set[str]:
    return {str(n["topic"]) for n in await hm.notifications()}


async def _said(hm: HouseMind, topic: str) -> list[tuple[str, str]]:
    rows = await hm.db.fetchall(
        "SELECT kind, text_key, params FROM notifications WHERE topic = ?", (topic,)
    )
    return [
        (str(r["kind"]), hm.languages.text("nl", r["text_key"], **json.loads(r["params"])))
        for r in rows
    ]


async def test_water_that_runs_away_every_hour_of_the_night(tmp_path: Path) -> None:
    """A dripping tap or a toilet that keeps running went unnoticed: only water running for a
    long time at once got a word."""
    hm, clock, ha = await start(datetime(2026, 11, 23, 23, 50, tzinfo=TZ), tmp_path)
    litres = 208_000.0
    attrs = {"state_class": "total_increasing", "unit_of_measurement": "L"}
    _add(ha, hm, METER, f"{litres}", device_class="water", attrs=attrs)
    for _ in range(4 * 6):  # until about 05:50, 2 litres an hour
        await _quarter(hm, clock)
        litres += 0.5
        await ha.simulate(METER, f"{litres}", attrs)
    assert "maintenance:water_night" in await _open(hm)
    said = await _said(hm, "maintenance:water_night")
    assert len(said) == 1 and said[0][0] == "important"
    assert "elk uur water weg" in said[0][1] and "8 liter" in said[0][1], said
    assert "kraan" in said[0][1]
    # The next night only a toilet at three: no leak, the word goes in the morning.
    await clock.advance(17 * 3600)
    for i in range(4 * 7):  # from about 22:50 until after five
        await _quarter(hm, clock)
        if i == 12:
            litres += 6
            await ha.simulate(METER, f"{litres}", attrs)
    assert "maintenance:water_night" not in await _open(hm)
    assert len(await _said(hm, "maintenance:water_night")) == 1
    await hm.stop()


async def test_rain_on_its_way_with_a_window_open(tmp_path: Path) -> None:
    """Rain coming and a window open said nothing: only open while everyone was away did."""
    hm, clock, ha = await start(datetime(2026, 11, 23, 15, 0, tzinfo=TZ), tmp_path)
    assert WINDOW in hm.openings
    weather = sorted(e for e in ha.states if e.startswith("weather."))[0]
    soon = (clock.now() + timedelta(minutes=20)).astimezone(TZ).isoformat()
    ha.hourly[weather] = [{"datetime": soon, "condition": "rainy", "precipitation_probability": 90}]
    await _quarter(hm, clock)
    assert "maintenance:rain_open" not in await _open(hm)  # nothing open: nothing to say
    await ha.simulate(WINDOW, "on")
    await _quarter(hm, clock)
    assert "maintenance:rain_open" in await _open(hm)
    kind, text = (await _said(hm, "maintenance:rain_open"))[0]
    assert kind == "important" and text.startswith("Het gaat binnen een uur regenen, en ")
    assert text.endswith(" staat open."), text
    await ha.simulate(WINDOW, "off")
    await _quarter(hm, clock)
    assert "maintenance:rain_open" not in await _open(hm)  # closed: the word goes
    await ha.simulate(WINDOW, "on")
    await _quarter(hm, clock)
    assert len(await _said(hm, "maintenance:rain_open")) == 1  # not again within three hours
    await hm.stop()


async def test_no_word_for_rain_without_rain(tmp_path: Path) -> None:
    hm, clock, ha = await start(datetime(2026, 11, 23, 15, 0, tzinfo=TZ), tmp_path)
    weather = sorted(e for e in ha.states if e.startswith("weather."))[0]
    later = (clock.now() + timedelta(hours=3)).astimezone(TZ).isoformat()
    ha.hourly[weather] = [
        {"datetime": later, "condition": "rainy", "precipitation_probability": 90}
    ]
    await ha.simulate(WINDOW, "on")
    await _quarter(hm, clock)
    assert "maintenance:rain_open" not in await _open(hm)  # rain in three hours: not yet
    await hm.stop()


async def test_a_device_that_reports_a_fault(tmp_path: Path) -> None:
    """A heat pump's own fault, or a switch that overheats, said nothing in HouseMind."""
    hm, clock, ha = await start(datetime(2026, 11, 23, 15, 0, tzinfo=TZ), tmp_path)
    hm.model.devices["hp"] = Device("hp", "Warmtepomp", None)
    hm.model.devices["sw"] = Device("sw", "Keuken - Spots", None)
    fault, hot = "binary_sensor.heat_pump_fault", "binary_sensor.keuken_spots_overheating"
    _add(ha, hm, fault, "off", name="Storing", device_class="problem", device_id="hp")
    _add(ha, hm, hot, "off", name="Overheating", device_class="problem", device_id="sw")
    await ha.simulate(fault, "on")
    await ha.simulate(hot, "on")
    await _quarter(hm, clock)
    said = {}
    for sensor in (fault, hot):
        said[sensor] = (await _said(hm, f"maintenance:problem:{sensor}"))[0]
    assert said[fault][0] == "info" and said[fault][1].startswith("Warmtepomp meldt een storing")
    assert said[hot][0] == "important" and "gevaarlijke storing" in said[hot][1]
    await ha.simulate(fault, "off")
    await _quarter(hm, clock)
    assert f"maintenance:problem:{fault}" not in await _open(hm)  # over: the word goes
    await hm.stop()
