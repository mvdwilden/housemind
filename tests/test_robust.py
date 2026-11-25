"""QA 2, group 4 – robust (M-28 – M-31). Each test fails without its fix; what it checks is
its docstring."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from housemind.runtime import HouseMind
from housemind.safety.emergency import Emergency
from tests.test_round1_safety import _start
from tests.test_vesta import _home_again


async def test_a_leak_that_starts_while_housemind_starts_is_not_missed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-28: HouseMind read the house first and listened only later, so a leak that began in
    between waited for the sensor's next change. Now it reads once more after it listens."""
    original = HouseMind._sync_automations
    first = [True]

    async def leak_meanwhile(self: HouseMind) -> None:
        if first.pop() if first else False:  # during the start, before HouseMind listens
            await self.ha.simulate("binary_sensor.hall_leak", "on")  # type: ignore[attr-defined]
        await original(self)

    monkeypatch.setattr(HouseMind, "_sync_automations", leak_meanwhile)
    hm, ha, _clock = await _start(tmp_path)
    assert hm.status.emergency is Emergency.LEAK
    assert ha.states["valve.water_main"].state == "closed"
    await hm.stop()


async def test_after_a_failed_return_of_home_assistant_the_next_tick_tries_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-29: when reading the house again after a reconnect failed, HouseMind stayed in
    "Home Assistant is away" and the tick stopped for good. Now the next tick tries again."""
    hm, ha, clock = await _start(tmp_path)
    await ha.drop()
    original = hm._refresh_model
    fails = [True]

    async def once_broken() -> None:
        if fails.pop() if fails else False:
            raise TimeoutError("no answer")
        await original()

    monkeypatch.setattr(hm, "_refresh_model", once_broken)
    await ha.come_back()
    assert hm.status.ha_lost is not None  # the first try failed
    await clock.advance(30)
    await hm.tick()
    assert hm.status.ha_lost is None
    await hm.stop()


async def test_one_failing_step_does_not_stop_the_rest_of_the_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-30: one error in the tick skipped every step after it, the clean-up included. Now
    the error is logged and the round goes on."""
    hm, _ha, _clock = await _start(tmp_path)
    ran: list[str] = []

    async def broken(*_: Any) -> None:
        raise RuntimeError("a bug in one step")

    async def later(*_: Any) -> None:
        ran.append("proposals")

    monkeypatch.setattr(hm, "_close_bedtime", broken)
    monkeypatch.setattr(hm, "_expire_proposal_notifications", later)
    monkeypatch.setattr(hm, "_review_automations", broken)
    monkeypatch.setattr(hm, "_check_version", lambda *_: later())
    hm._last_review = None
    await hm.tick()  # no error comes out
    assert ran == ["proposals", "proposals"]  # after the broken one, in both parts
    await hm.stop()


async def test_her_greeting_never_holds_up_the_next_sensor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M-31: her greeting (with AI up to 2.5 s) waited in the same queue as sensor to command,
    so the next motion waited for her words. Now it goes beside the queue."""
    hm, ha, clock = await _home_again(tmp_path, level=2)
    original = hm.vesta_line

    async def slow_words(*args: Any, **kwargs: Any) -> Any:
        import asyncio

        await asyncio.sleep(1.0)  # an AI that takes its time
        return await original(*args, **kwargs)

    monkeypatch.setattr(hm, "vesta_line", slow_words)
    await clock.advance(20)
    started = time.perf_counter()
    await ha.simulate("binary_sensor.hall_motion", "on")  # first seen at home: the greeting
    assert time.perf_counter() - started < 0.5
    await hm.settled()
    assert [1 for target, _ in ha.spoken if target == "assist_satellite.hall"]
    await hm.stop()


async def test_the_watchdog_hears_when_the_tick_stopped(tmp_path: Path) -> None:
    """M-34: /api/health always said ok. Now it says 503 when the round of thinking stopped
    for ten minutes, so Home Assistant's watchdog starts the App again."""
    from datetime import timedelta

    from aiohttp.test_utils import TestClient, TestServer

    from housemind.server.web import create_app

    hm, _ha, clock = await _start(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        hm.status.last_tick = clock.now()
        assert (await client.get("/api/health")).status == 200
        await clock.advance(11 * 60)
        assert (await client.get("/api/health")).status == 503
        hm.status.last_tick = clock.now() - timedelta(seconds=30)
        assert (await client.get("/api/health")).status == 200
    await hm.stop()


async def test_home_assistant_not_there_at_the_start_is_waited_for() -> None:
    """M-34: when Home Assistant was not there yet at the start, HouseMind stopped. Now it
    tries again until it is; a wrong token still stops at once."""
    import asyncio
    from datetime import UTC, datetime

    from aiohttp.test_utils import TestServer

    from housemind.core.clock import SimulatedClock
    from housemind.ha.websocket import HAAuthError, WebSocketHomeAssistant
    from tests.ha_ws_server import TOKEN, FakeWsServer

    server = FakeWsServer()
    server.accepting = False  # HA is still starting
    async with TestServer(server.app()) as test_server:
        url = str(test_server.make_url("/api/websocket"))
        clock = SimulatedClock(datetime(2026, 10, 19, 12, tzinfo=UTC))
        client = WebSocketHomeAssistant(clock, TOKEN, url=url, timeout_s=5)
        client.retry_delays = (0.05, 0.1)
        connecting = asyncio.create_task(client.connect())
        await asyncio.sleep(0.3)
        assert not connecting.done()
        server.accepting = True
        config = await asyncio.wait_for(connecting, 5)
        assert config.version
        await client.close()
        wrong = WebSocketHomeAssistant(clock, "wrong", url=url, timeout_s=5)
        with pytest.raises(HAAuthError):
            await wrong.connect()
        await wrong.close()


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-24T22:00:00+02:00", "2026-10-25T09:00:00+01:00"),  # winter time: 02–03 twice
        ("2027-03-27T22:00:00+01:00", "2027-03-28T09:00:00+02:00"),  # summer time: no 02–03
    ],
)
async def test_the_night_of_the_clock_change(
    tmp_path: Path, start: str, end: str, monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """L11: nothing tested the night the clocks change. Now the night runs through, every 15
    minutes: no step fails, and the clean-up runs exactly once that morning."""
    import logging
    from datetime import UTC, datetime

    from housemind.core.clock import SimulatedClock
    from housemind.demo.houses import load_demo_house
    from housemind.runtime import Paths
    from tests.test_round1_safety import ROOT

    clock = SimulatedClock(datetime.fromisoformat(start).astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    cleanups: list[str] = []
    original = hm.db.cleanup

    async def counted(now: Any) -> Any:
        cleanups.append(now.isoformat())
        return await original(now)

    monkeypatch.setattr(hm.db, "cleanup", counted)
    caplog.set_level(logging.ERROR)
    while clock.now() < datetime.fromisoformat(end).astimezone(UTC):
        await clock.advance(15 * 60)
        await hm.tick()
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Amsterdam")
    morning = [
        datetime.fromisoformat(c).astimezone(tz)
        for c in cleanups
        if datetime.fromisoformat(c).astimezone(tz).date() == datetime.fromisoformat(end).date()
    ]
    assert len(morning) == 1 and morning[0].hour == 3, cleanups  # once, at 03:00 local time
    assert not [r for r in caplog.records if "failed" in r.getMessage()], caplog.text[:500]
    await hm.stop()
