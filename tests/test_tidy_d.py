"""Home Assistant netjes, deel D (E-361, E-362, B-245 – B-247): who hears about it. Only
administrators; important at once with 'Bekijken', the rest one line a day in the bundle, the
first time one line with how many there are; what is solved goes by itself; Vesta answers
'Is er iets stuk in Home Assistant?' without an AI. Each test fails without the build."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.server.web import create_app
from tests.helpers import next_message as _next

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 27, 11, 0, tzinfo=UTC)
ADMIN = "demo-user-1"


async def _start(tmp_path: Path) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(NOW)
    house = load_demo_house("extended", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await clock.advance(16 * 60)
    hm._last_review = None
    await hm.tick()
    await hm.settled()  # the look runs beside the core
    return hm, clock, house.ha


async def _tidy(hm: HouseMind) -> list[dict[str, Any]]:
    rows = await hm.db.fetchall(
        "SELECT id, topic, kind, concerns, text_key, params, resolved_at FROM notifications "
        "WHERE topic LIKE 'tidy:%' ORDER BY id"
    )
    return [{**dict(r), "params": json.loads(r["params"])} for r in rows]


async def _tick(hm: HouseMind, clock: SimulatedClock, seconds: float) -> None:
    await clock.advance(seconds)
    hm._last_review = None
    await hm.tick()
    await hm.settled()  # the look runs beside the core


async def test_an_old_backup_at_once_and_one_line_with_how_many(tmp_path: Path) -> None:
    hm, clock, ha = await _start(tmp_path)
    rows = {r["topic"]: r for r in await _tidy(hm)}
    backup = rows["tidy:backup"]
    assert backup["kind"] == "important" and backup["concerns"] == "admins"
    text = hm.languages.text("nl", backup["text_key"], **backup["params"])
    assert "9 dagen geen nieuwe back-up" in text
    overview = rows["tidy:overview"]  # not one message per thing that was already there
    # The findings and the card of old loose backups (E-397); the old backup is important.
    assert overview["kind"] == "info" and overview["params"]["count"] == len(hm.tidy_findings) + 1
    assert len(rows) == 2
    await _tick(hm, clock, 3600)  # the next round the same day: nothing new
    assert len(await _tidy(hm)) == 2
    # The next day new things (meters that stood still a day in the demo): one line, about
    # the heaviest, in the bundle.
    await _tick(hm, clock, 24 * 3600)
    new = [r for r in await _tidy(hm) if r["topic"].startswith("tidy:new:")]
    assert len(new) == 1 and new[0]["kind"] == "info"  # one line a day: the heaviest new thing
    assert new[0]["text_key"].startswith("notify.tidy.found.")
    await _tick(hm, clock, 3600)
    assert len([r for r in await _tidy(hm) if r["topic"].startswith("tidy:new:")]) == 1
    overview = next(r for r in await _tidy(hm) if r["topic"] == "tidy:overview")
    assert overview["resolved_at"]  # yesterday's line went
    # A new backup: the important message goes by itself.
    ha.backup_list = [{"date": clock.now().isoformat(), "homeassistant_included": True}]
    await clock.advance(120)
    assert await hm.tidy_check()
    backup = next(r for r in await _tidy(hm) if r["topic"] == "tidy:backup")
    assert backup["resolved_at"]
    await hm.stop()


async def test_backups_outside_home_assistant_and_an_integration_that_does_not_start(
    tmp_path: Path,
) -> None:
    hm, clock, _ = await _start(tmp_path)
    await hm.tidy_choose("backup:home_assistant", "own_backups", ADMIN)
    await hm.tidy_round()
    assert next(r for r in await _tidy(hm) if r["topic"] == "tidy:backup")["resolved_at"]
    # An integration that has not started for an hour while automations depend on it.
    down = next(f for f in hm.tidy_findings if f.kind == "integration_down")
    hm.tidy_findings = [
        replace(
            down, depends=("automation.attic_light_night",), since=clock.now() - timedelta(hours=2)
        )
        if f is down
        else f
        for f in hm.tidy_findings
    ]
    await hm._tidy_tell(clock.now())
    row = next(r for r in await _tidy(hm) if r["topic"] == f"tidy:down:{down.target}")
    assert row["kind"] == "important" and row["concerns"] == "admins"
    text = hm.languages.text("nl", row["text_key"], **row["params"])
    assert "start al een uur niet" in text and f"{len(down.follows)} entiteiten" in text
    await hm.stop()


async def test_vesta_says_what_is_broken_without_an_ai(tmp_path: Path) -> None:
    hm, _, _ = await _start(tmp_path)
    broken = [f for f in hm.tidy_findings if not f.kind.startswith("unused")]
    said = await hm.knowing.answer("Is er iets stuk in Home Assistant?", "nl", 3, ADMIN, True)
    assert said is not None and f"{len(broken)} dingen" in said["text"]
    assert said["open"] == {"sheet": "settings", "section": "tidy"}
    member = await hm.knowing.answer("Is er iets stuk in Home Assistant?", "nl", 3, None, False)
    assert member is not None and "open" not in member and "beheerder" in member["text"]
    backup = await hm.knowing.answer("Hoe oud is de back-up?", "nl", 3, ADMIN, True)
    assert backup is not None and "9 dagen" in backup["text"]
    spoken = await hm.knowing.answer("Wat is er stuk?", "nl", 3, ADMIN, True, channel="assist")
    assert spoken is not None and "open" not in spoken  # Assist cannot open a page
    await hm.stop()


async def test_bekijken_opens_the_page_and_the_meter_shows_the_round(tmp_path: Path) -> None:
    hm, _, _ = await _start(tmp_path)
    notice = next(r for r in await _tidy(hm) if r["topic"] == "tidy:backup")
    row = await hm.db.fetchone("SELECT * FROM notifications WHERE id = ?", (notice["id"],))
    card = views.notice_item(hm, "nl", dict(row), True, ADMIN)
    assert card is not None and card["actions"] == ["view", "snooze", "dismiss"]
    assert card["mute"]  # 'Niet meer tonen' too (E-372)
    assert views.notice_item(hm, "nl", dict(row), False, "demo-user-2") is None  # admins only
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": ADMIN})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "notification", "id": notice["id"], "action": "view"})
        opened = await _next(ws, "open")
        assert opened["sheet"] == "settings" and opened["section"] == "tidy"
    assert hm.tidy_took is not None  # the round is timed (no meter shows it any more, QA 3)
    await hm.stop()
