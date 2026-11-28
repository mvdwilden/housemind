"""The thumbs left the app (28-11-2026, B-504, B-505). The administrator chose: no 👍/👎 and no
"Was dat goed?" any more; HouseMind learns from what someone puts back themselves and from
Undo. Checked from the user's side: after a try, under her "why", in Home Assistant, in the
counts, in the Logboek, in every text – and an update keeps the house as it was, closing only
an old open "Was dat goed?". What each test checks is its docstring."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.learning import Result
from housemind.brain.readiness import readiness
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import Layer
from housemind.server import views
from housemind.server.logbook import logbook
from housemind.server.web import create_app
from housemind.world.house_mode import HouseMode
from tests.test_automations import start
from tests.test_vesta_as_herself import KITCHEN, ME, ROOT, _did_something
from tests.test_vesta_asks import _until

CODES = ("nl", "en", "de", "fr")
THUMB = re.compile(r"👍|👎|\bthumbs?\b|was_it_good|answer_thumbs", re.IGNORECASE)


async def _tried_something(tmp_path: Path) -> tuple[HouseMind, Any, int]:
    """The kitchen light is still on Voorstellen and only a resident on Zorgeloos is home:
    instead of asking, HouseMind tries it itself (E-57, E-287). Before, "Was dat goed?" came
    to the administrator after such a try."""
    hm, _clock, ha = await start(tmp_path)
    for i, level in ((1, 3), (2, 1)):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (f"demo-user-{i}", f"Resident {i}", level, json.dumps({}), hm.clock.now().isoformat()),
        )
    await ha.simulate("person.resident_1", "not_home")
    await hm._refresh_people()
    assert hm.world is not None and hm.learning is not None
    hm.world.modes.mode = HouseMode.HOME
    hm.learning.steps[KITCHEN] = Step.PROPOSE
    now = hm.clock.now()
    hm.learning.first_seen[KITCHEN] = now - timedelta(days=8)  # a week of proof (B-176)
    for _ in range(12):
        hm.learning._add(*KITCHEN, "", Result.HIT, now)
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    tried = [
        i
        for i, r in hm.recent.items()
        if r.decision.executed and r.decision.request.layer is Layer.TRIAL
    ]
    assert tried and ha.states["light.kitchen"].state == "on"
    return hm, ha, tried[-1]


def _learned(hm: HouseMind) -> list[float]:
    """What the kitchen light has counted, per kind of result."""
    assert hm.learning is not None
    now = hm.clock.now()
    return [hm.learning.total(*KITCHEN, r, now) for r in Result]


async def _old_thumbs(hm: HouseMind, decision: int) -> None:
    """Thumbs given this week, before the update: the table stays (B-505)."""
    now = hm.clock.now().isoformat()
    for user_id, good in (("demo-user-1", 1), ("demo-user-2", 0)):
        await hm.db.execute(
            "INSERT INTO thumbs(at, decision_id, user_id, part, good, learned) VALUES(?,?,?,?,?,?)",
            (now, decision, user_id, "lighting", good, "yes" if good else "correction"),
        )


async def test_after_a_try_on_voorstellen_she_asks_nothing_afterwards(tmp_path: Path) -> None:
    """After a try on Voorstellen Vesta asked "Ik heb net dit gedaan: … Was dat goed?" with
    👍 Goed / 👎 Niet goed. Now nothing comes afterwards: not in her window, not in the list,
    not on a phone – also not a day later."""
    hm, ha, _decision = await _tried_something(tmp_path)
    for minutes in (16, 16, 24 * 60):
        await hm.clock.advance(minutes * 60)  # type: ignore[attr-defined]
        await hm.tick()
    for user_id, level in (("demo-user-1", 3), ("demo-user-2", 1)):
        asks = await hm.vesta_asks(user_id, level, user_id == "demo-user-1", every=True)
        assert all(a["kind"] != "thumbs" for a in asks), asks
        listed = await views.notifications(hm, "nl", True, user_id)
        assert not any("Was dat goed?" in str(n["text"]) for n in listed)
    row = await hm.db.fetchone(
        "SELECT count(*) AS n FROM notifications WHERE topic LIKE 'thumbs:%' OR "
        "text_key = 'notify.was_it_good'"
    )
    assert row is not None and row["n"] == 0
    assert not any("Was dat goed?" in str(n[2]) for n in ha.notifications)
    await hm.stop()


async def test_her_why_has_no_thumbs_and_an_old_screen_cannot_give_one(tmp_path: Path) -> None:
    """Under her "why" in the window stood 👍/👎 from Geïnformeerd. Now only her answer. A
    screen that still has the old app open and sends a thumb changes nothing: nothing is kept
    and nothing is learned from it."""
    hm, _ha, decision = await _did_something(tmp_path, level=2)
    before = _learned(hm)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ME)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "why", "id": decision})
        said = (await _until(ws, "say"))["say"]
        assert said["window"] is True and said["text"]
        assert "thumbs" not in said
        for message in (
            {"type": "thumbs", "id": decision, "good": True, "via": "vesta"},
            {"type": "thumbs", "id": decision, "good": False},
        ):
            await ws.send_json(message)
        await ws.send_json({"type": "set_pref", "key": "timing", "value": "normal"})
        await _until(ws, "saved")  # handled in order: the thumbs came before it
        await ws.close()
    row = await hm.db.fetchone("SELECT count(*) AS n FROM thumbs")
    assert row is not None and row["n"] == 0
    assert _learned(hm) == before
    await hm.stop()


async def test_home_assistant_has_no_thumbs_action_any_more(tmp_path: Path) -> None:
    """Home Assistant had the action "Feedback" (thumbs up or down on the last action). It is
    gone from the integration; an old integration that still sends it gets a plain no, and
    nothing is kept or learned."""
    integration = ROOT / "integration" / "custom_components" / "housemind"
    services = yaml.safe_load((integration / "services.yaml").read_text(encoding="utf-8"))
    assert "feedback" not in services
    assert '"feedback"' not in (integration / "__init__.py").read_text(encoding="utf-8")
    for code in CODES:
        texts = json.loads((integration / "translations" / f"{code}.json").read_text("utf-8"))
        assert "feedback" not in texts["services"], code
    hm, _ha, _decision = await _did_something(tmp_path)
    before = _learned(hm)
    secret = await hm.integration_secret()
    good = {"Authorization": f"Bearer {secret}"}
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        response = await client.post(
            "/integration/action", json={"action": "feedback", "good": False}, headers=good
        )
        assert response.status == 400
    row = await hm.db.fetchone("SELECT count(*) AS n FROM thumbs")
    assert row is not None and row["n"] == 0
    assert _learned(hm) == before
    await hm.stop()


async def test_the_counts_and_the_logbook_have_no_thumbs_also_with_old_ones_kept(
    tmp_path: Path,
) -> None:
    """The Brain showed "👍 1 · 👎 1 deze week, 50%" per part, the week report "Jullie duimen:
    … % goed", and the Logboek the thumbs under a line. Thumbs from before the update stay in
    the table, but nothing counts or shows them. What someone put back with Undo still counts,
    per part and in the week report."""
    hm, _ha, decision = await _did_something(tmp_path)
    await _old_thumbs(hm, decision)
    assert (await hm.undo(decision, "demo-user-1"))["ok"]
    for level in (3, 4):
        lighting = {p["domain"]: p for p in await readiness(hm, "nl", level, True)}["lighting"]
        assert "thumbs" not in lighting, level
        assert lighting["corrections"]["weeks"][-1] == 1
    data = await hm.week_data(hm.clock.now())
    assert "up" not in data and "down" not in data
    assert data["undone"] == 1
    for level in (2, 3, 4):
        lines = hm.report_text(data, level, "nl")
        assert not any(THUMB.search(line) or "duim" in line.lower() for line in lines), lines
    for level in (2, 3, 4):
        log = await logbook(
            hm, views.Viewer("demo-user-1", "R", level, True, {}), "demo-user-1", "nl"
        )
        assert all("answers" not in item for item in log["items"]), level
        assert not THUMB.search(json.dumps(log, ensure_ascii=False))
    await hm.stop()


async def test_an_update_closes_an_open_was_it_good_and_keeps_everything_else(
    tmp_path: Path,
) -> None:
    """An update keeps the house as it is (E-578). A "Was dat goed?" still open from before
    the update closes at the start, also on the phone it reached; every other open question,
    what HouseMind learned, its settings and the old thumbs stay as they were."""
    hm, ha, decision = await _did_something(tmp_path)
    await _old_thumbs(hm, decision)
    await hm.set_house_setting("vesta_name", "Hestia")
    assert hm.learning is not None
    for _ in range(5):  # what it learned in the kitchen
        hm.learning._add(*KITCHEN, "", Result.HIT, hm.clock.now())
    learned = _learned(hm)
    assert any(learned)
    now = hm.clock.now().isoformat()
    for topic, key in ((f"thumbs:{decision}", "notify.was_it_good"), ("calendar_consent", "x")):
        await hm.db.execute(
            "INSERT INTO notifications(at, kind, topic, text_key, params, severity, delivered, "
            "concerns) VALUES(?,?,?,?,?,?,?,?)",
            (
                now,
                "learning",
                topic,
                key,
                "{}",
                2,
                json.dumps({"demo-user-1": "push"}),
                "user:demo-user-1",
            ),
        )
    setup = await hm.db.get_setting("setup")
    clock = hm.clock
    await hm.stop()
    again = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    rows = {
        str(r["topic"]): r["resolved_at"]
        for r in await again.db.fetchall("SELECT topic, resolved_at FROM notifications")
    }
    assert rows[f"thumbs:{decision}"] is not None
    assert rows["calendar_consent"] is None  # another question stays open
    me = next(p for p in again._people if p.user_id == "demo-user-1")
    assert me.notify_services
    assert [c for c in ha.cleared if c[1] == f"thumbs:{decision}"]  # off the phone
    assert not [c for c in ha.cleared if c[1] == "calendar_consent"]
    assert await again.setting("vesta_name") == "Hestia"
    assert await again.db.get_setting("setup") == setup
    assert _learned(again) == pytest.approx(learned)
    row = await again.db.fetchone("SELECT count(*) AS n FROM thumbs")
    assert row is not None and row["n"] == 2  # the table and its rows stay
    await again.stop()


def test_no_text_screen_or_phrase_has_thumbs() -> None:
    """No 👍, 👎, "thumbs" or "Was dat goed?" in the texts of the app in any language, in
    Vesta's phrases, in the Help, in the manual or on any screen."""
    found: list[str] = []
    for code in CODES:
        for folder in ("languages", "vesta"):
            path = ROOT / "knowledge" / folder / f"{code}.yaml"
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if THUMB.search(line):
                    found.append(f"{path.relative_to(ROOT)}:{i}")
        manual = (ROOT / "knowledge" / "manual" / f"{code}.html").read_text(encoding="utf-8")
        if THUMB.search(manual) or "uitleg-duimen" in manual:
            found.append(f"knowledge/manual/{code}.html")
    for path in sorted((ROOT / "frontend" / "src").rglob("*")):
        if path.suffix in (".svelte", ".ts") and not path.name.endswith(".test.ts"):
            text = (
                path.read_text(encoding="utf-8")
                .replace("slider-thumb", "")
                .replace("range-thumb", "")
            )
            if THUMB.search(text):
                found.append(str(path.relative_to(ROOT)))
    help_map = (ROOT / "docs" / "help-map.yaml").read_text(encoding="utf-8")
    links = (ROOT / "knowledge" / "manual" / "help-links.yaml").read_text(encoding="utf-8")
    if "feedback" in help_map or "duimen" in links:
        found.append("help")
    assert found == []
