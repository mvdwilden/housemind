"""Part G of "Duidelijk en volledig" (20.20, 20.21): 👍/👎 on what HouseMind did, "Was dat
goed?", the counts per part, and Vesta speaking as herself – her name above a push message,
in the language Home Assistant shows that person. Each test fails without the build."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.learning import Result
from housemind.brain.notify import Kind
from housemind.brain.readiness import readiness
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import Layer
from housemind.server import views
from housemind.server.web import create_app
from housemind.world.house_mode import HouseMode
from tests.test_automations import start
from tests.test_vesta import PHRASES
from tests.test_vesta_asks import _until

ROOT = Path(__file__).resolve().parents[1]
KITCHEN = ("light_on_entry", "kitchen")
ME = {"X-Remote-User-Id": "demo-user-1"}


async def _did_something(tmp_path: Path, level: int = 3) -> tuple[HouseMind, Any, int]:
    """A house where HouseMind just turned on the kitchen light itself."""
    hm, _clock, ha = await start(tmp_path)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", level, json.dumps({}), hm.clock.now().isoformat()),
    )
    await hm._refresh_people()
    assert hm.world is not None and hm.learning is not None
    hm.world.modes.mode = HouseMode.HOME
    hm.learning.steps[KITCHEN] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    done = [i for i, r in hm.recent.items() if r.decision.executed and r.decision.request.skill]
    assert done and ha.states["light.kitchen"].state == "on"
    return hm, ha, done[-1]


async def _tried_something(tmp_path: Path) -> tuple[HouseMind, Any, int]:
    """A house where the kitchen light is still on Voorstellen, and only a resident on
    Zorgeloos is home: instead of asking, HouseMind tried it itself (E-57, E-287). The
    administrator, away, is the one "Was dat goed?" is for."""
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


def _was_it_good(asks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in asks if a["kind"] == "thumbs"]


async def _tick(hm: HouseMind, minutes: int = 16) -> None:
    await hm.clock.advance(minutes * 60)  # type: ignore[attr-defined]
    await hm.tick()


async def _settled(check: Any, wait: float = 3) -> None:
    """A message over the socket is handled a moment later."""
    for _ in range(int(wait * 20)):
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("not handled")


# -- 👍/👎 (E-298, 20.20) --------------------------------------------------------------------


async def test_a_thumb_is_kept_per_person_and_part_and_teaches(tmp_path: Path) -> None:
    """A thumb was only a light lesson, gone right after. Now it is kept with the decision,
    the person and the part, so it can be counted; 👎 is a correction (20.9)."""
    hm, _ha, decision = await _did_something(tmp_path)
    assert hm.learning is not None
    assert (await hm.feedback(False, decision, "demo-user-1"))["ok"]
    assert (await hm.feedback(True, decision, "demo-user-1"))["ok"]
    rows = await hm.db.fetchall(
        "SELECT user_id, part, good FROM thumbs WHERE decision_id = ? ORDER BY id", (decision,)
    )
    # One thumb per person per action (QA 2, B-179): the newest is shown, it teaches once.
    assert [(r["user_id"], r["part"], r["good"]) for r in rows] == [("demo-user-1", "lighting", 1)]
    assert await hm.thumbs_week(hm.clock.now()) == {"lighting": (1, 0)}
    assert hm.learning.total(*KITCHEN, Result.CORRECTION, hm.clock.now()) > 0
    assert hm.learning.total(*KITCHEN, Result.APPROVED, hm.clock.now()) == 0
    # 👎 and Undo on the same action are one correction together (B-179).
    assert len(hm.learning.recent_setbacks(*KITCHEN, hm.clock.now())) == 1
    assert (await hm.undo(decision, "demo-user-1"))["ok"]
    assert len(hm.learning.recent_setbacks(*KITCHEN, hm.clock.now())) == 1
    await hm.stop()


async def test_she_asks_was_it_good_once_a_day_and_the_answer_is_a_thumb(tmp_path: Path) -> None:
    """Now and then Vesta asks "Was dat goed?" about something she tried herself on
    Voorstellen (E-498), at most once a day per person, with 👍 Goed / 👎 Niet goed – in the
    list and in her window. The answer is a thumb on that decision, and the question goes."""
    hm, _ha, decision = await _tried_something(tmp_path)
    await _tick(hm)
    asks = [
        a for a in await hm.vesta_asks("demo-user-1", 3, True, every=True) if a["kind"] == "thumbs"
    ]
    assert len(asks) == 1 and asks[0]["actions"] == ["good", "bad"]
    text = await hm.vesta_ask_text("demo-user-1", "nl", asks[0])
    assert "?" in text
    listed = [
        n
        for n in await views.notifications(hm, "nl", True, "demo-user-1")
        if "Was dat goed?" in str(n["text"])
    ]
    assert len(listed) == 1 and str(listed[0]["text"]).startswith("Ik heb net dit gedaan:")
    assert "Keuken" in str(listed[0]["text"]) or "keuken" in str(listed[0]["text"])
    await _tick(hm)  # the same day: not again
    again = await hm.vesta_asks("demo-user-1", 3, True, every=True)
    assert len([a for a in again if a["kind"] == "thumbs"]) == 1
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ME)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json(
            {"type": "notification", "id": asks[0]["notification"], "action": "good"}
        )

        async def answered() -> bool:
            row = await hm.db.fetchone(
                "SELECT good FROM thumbs WHERE decision_id = ? AND user_id = 'demo-user-1'",
                (decision,),
            )
            return row is not None and row["good"] == 1

        async def closed() -> bool:  # a moment after the thumb (it raced on a busy machine)
            gone = await hm.db.fetchone(
                "SELECT resolved_at FROM notifications WHERE id = ?", (asks[0]["notification"],)
            )
            return gone is not None and gone["resolved_at"] is not None

        await _settled(answered)
        await _settled(closed)
        await ws.close()
    await hm.stop()


async def _answer_was_it_good(
    hm: HouseMind, tmp_path: Path, notification: int, action: str
) -> list[str]:
    """Tap 👍 Goed or 👎 Niet goed under "Was dat goed?" in the list, as a person does; what
    the screen then says."""
    said: list[str] = []
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ME)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "notification", "id": notification, "action": action})
        # Until she says it, at most ten seconds: on a busy machine the first picture after
        # hello alone can take longer than half a second, and a short silence is no answer.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 10
        while (left := deadline - loop.time()) > 0:
            try:
                message = await ws.receive_json(timeout=left)
            except TimeoutError:
                break
            if message.get("type") == "saved":
                said.append(str(message["text"]))
                break
        await ws.close()
    return said


async def test_was_it_good_also_counts_when_the_action_is_no_longer_in_memory(
    tmp_path: Path,
) -> None:
    """Found in the real house: a "Was dat goed?" from the morning did nothing at noon – the
    question stayed and the screen said "Gedaan.". Only the last 50 decisions are in memory,
    and none after a restart; the database keeps them all. Now the thumb counts, the question
    goes and she says thanks."""
    hm, _ha, decision = await _tried_something(tmp_path)
    await _tick(hm)
    asks = [
        a for a in await hm.vesta_asks("demo-user-1", 3, True, every=True) if a["kind"] == "thumbs"
    ]
    assert len(asks) == 1
    hm.recent.clear()  # as after a restart, or fifty decisions later
    said = await _answer_was_it_good(hm, tmp_path, asks[0]["notification"], "good")
    assert said == ["Dank je, ik onthoud het."]
    row = await hm.db.fetchone(
        "SELECT good, learned FROM thumbs WHERE decision_id = ? AND user_id = 'demo-user-1'",
        (decision,),
    )
    assert row is not None and row["good"] == 1 and row["learned"] == "yes"
    gone = await hm.db.fetchone(
        "SELECT resolved_at FROM notifications WHERE id = ?", (asks[0]["notification"],)
    )
    assert gone is not None and gone["resolved_at"] is not None
    await hm.stop()


async def test_was_it_good_about_an_action_that_is_gone_says_so_and_goes(tmp_path: Path) -> None:
    """When the action can no longer be found at all, the tap still does something: the
    question goes and she says honestly that it does not count – no "Gedaan."."""
    hm, _ha, decision = await _tried_something(tmp_path)
    await _tick(hm)
    asks = [
        a for a in await hm.vesta_asks("demo-user-1", 3, True, every=True) if a["kind"] == "thumbs"
    ]
    hm.recent.clear()
    await hm.db.execute("DELETE FROM decisions WHERE id = ?", (decision,))
    said = await _answer_was_it_good(hm, tmp_path, asks[0]["notification"], "bad")
    assert said == [hm.languages.text("nl", "ui.thumbs.gone")]
    assert await hm.db.fetchone("SELECT id FROM thumbs WHERE decision_id = ?", (decision,)) is None
    gone = await hm.db.fetchone(
        "SELECT resolved_at FROM notifications WHERE id = ?", (asks[0]["notification"],)
    )
    assert gone is not None and gone["resolved_at"] is not None
    await hm.stop()


async def test_an_unanswered_was_it_good_goes_by_itself_after_a_day(tmp_path: Path) -> None:
    """QA 2 (H-10): "Was dat goed?" came once a day and stayed open for ever – after a year
    about 360 open questions. It is about something just done, so after a day it goes."""
    hm, _ha, _decision = await _tried_something(tmp_path)
    await _tick(hm)
    asks = [
        a for a in await hm.vesta_asks("demo-user-1", 3, True, every=True) if a["kind"] == "thumbs"
    ]
    assert len(asks) == 1
    await _tick(hm, 60 * 25)  # a day later, unanswered
    row = await hm.db.fetchone(
        "SELECT resolved_at FROM notifications WHERE id = ?", (asks[0]["notification"],)
    )
    assert row is not None and row["resolved_at"] is not None
    still = await hm.vesta_asks("demo-user-1", 3, True, every=True)
    assert asks[0]["notification"] not in [a.get("notification") for a in still]
    await hm.stop()


async def test_on_autonomous_she_does_it_and_asks_nothing_afterwards(tmp_path: Path) -> None:
    """The administrator (11-11-2026): "Volgens mij moet je in Autonoom stand helemaal geen
    vragen meer stellen; enkel bij Voorstellen." In the real house "Ik heb net dit gedaan: ...
    Was dat goed?" came about rules on Autonoom. Now a rule on Autonoom does it and asks
    nothing, not even afterwards (E-498); the thumbs under the message stay for who wants."""
    hm, _ha, _decision = await _did_something(tmp_path)
    await _tick(hm)
    assert _was_it_good(await hm.vesta_asks("demo-user-1", 3, True, every=True)) == []
    listed = await views.notifications(hm, "nl", True, "demo-user-1")
    assert not [n for n in listed if "Was dat goed?" in str(n["text"])]
    await hm.stop()


async def test_once_a_day_also_after_a_restart(tmp_path: Path) -> None:
    """In the real house "Was dat goed?" came six times on one day: HouseMind kept "asked
    today" only in memory, and the App was updated five times that day. Now it holds across a
    restart (B-417): a try after the restart, the same day, brings no second question."""
    hm, ha, _decision = await _tried_something(tmp_path)
    await _tick(hm)
    assert len(_was_it_good(await hm.vesta_asks("demo-user-1", 3, True, every=True))) == 1
    clock = hm.clock
    await hm.stop()
    again = HouseMind(clock, ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    await again._refresh_people()
    assert again.world is not None and again.learning is not None
    again.world.modes.mode = HouseMode.HOME
    again.learning.steps[KITCHEN] = Step.PROPOSE
    await ha.simulate("binary_sensor.kitchen_motion", "off")
    await ha.simulate("light.kitchen", "off")
    await _tick(again, 30)
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    assert any(
        r.decision.executed and r.decision.request.layer is Layer.TRIAL
        for r in again.recent.values()
    )
    await _tick(again)
    assert len(_was_it_good(await again.vesta_asks("demo-user-1", 3, True, every=True))) == 1
    await again.stop()


async def test_her_why_has_thumbs_from_level_2_and_she_thanks_in_her_window(
    tmp_path: Path,
) -> None:
    """Under her "why" in the window: 👍/👎 from level 2. In her window she thanks in her own
    words; on the message after an action a short "Dank je" is enough. Level 1: no thumbs."""
    hm, _ha, decision = await _did_something(tmp_path, level=2)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ME)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "why", "id": decision})
        said = (await _until(ws, "say"))["say"]
        assert said["window"] is True and said["thumbs"] == decision
        await ws.send_json({"type": "thumbs", "id": decision, "good": True, "via": "vesta"})
        thanks = (await _until(ws, "say"))["say"]
        assert thanks["text"] in PHRASES.variants("nl", "answer_thumbs", False)
        await ws.send_json({"type": "thumbs", "id": decision, "good": False})
        assert (await _until(ws, "saved"))["saved"]["text"] == "Dank je, ik onthoud het."
        await ws.close()
        # One thumb per person per action: the 👎 replaced the 👍 (QA 2, B-179).
        assert await hm.thumbs_week(hm.clock.now()) == {"lighting": (0, 1)}
        await hm.db.execute("UPDATE people SET level = 1 WHERE user_id = 'demo-user-1'")
        await hm._refresh_people()
        ws = await client.ws_connect("/api/ws", headers=ME)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "why", "id": decision})
        assert "thumbs" not in (await _until(ws, "say"))["say"]
        await ws.send_json({"type": "thumbs", "id": decision, "good": True})
        await ws.close()
    assert await hm.thumbs_week(hm.clock.now()) == {"lighting": (0, 1)}  # not on level 1
    await hm.stop()


async def test_the_brain_and_the_week_report_count_the_thumbs_from_level_3(
    tmp_path: Path,
) -> None:
    """ "👍 2 · 👎 1 deze week, 67%" per part in the Brain from level 3, and a line in the week
    report; level 2 keeps it short."""
    hm, _ha, decision = await _did_something(tmp_path)
    for person, good in (("demo-user-1", True), ("demo-user-2", True), ("demo-user-3", False)):
        await hm.feedback(good, decision, person)  # one thumb each (B-179)
    parts = {p["domain"]: p for p in await readiness(hm, "nl", 3, True)}
    assert parts["lighting"]["thumbs"]["text"] == "👍 2 · 👎 1 deze week, 67%"
    assert "thumbs" not in parts.get("covers", {})
    two = {p["domain"]: p for p in await readiness(hm, "nl", 2, True)}
    assert "thumbs" not in two["lighting"]
    data = await hm.week_data(hm.clock.now())
    assert (data["up"], data["down"]) == (2, 1)
    assert "Jullie duimen: 👍 2 · 👎 1, 67% goed." in hm.report_text(data, 3, "nl")
    assert not any("duimen" in line for line in hm.report_text(data, 2, "nl"))
    await hm.stop()


# -- she speaks as herself (E-299, E-170, E-25, 20.21) ---------------------------------------


def _strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in _strings(v, f"{path}.{k}" if path else k)]
    return [(path, node)] if isinstance(node, str) else []


def test_what_she_says_never_calls_her_housemind() -> None:
    """Her messages, answers and reasons said "HouseMind heeft …" – as if someone else did
    it. Now she says "ik" in every language; only the app's own screens name the app."""
    for lang in ("nl", "en", "de", "fr"):
        texts = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        hers = [
            (path, text)
            for top in ("notify", "talk", "gate", "rules", "status", "reason")
            for path, text in _strings(texts.get(top, {}), top)
        ]
        assert len(hers) > 100
        assert [p for p, text in hers if "HouseMind" in text] == [], lang
        vesta = (ROOT / "knowledge" / "vesta" / f"{lang}.yaml").read_text()
        assert "HouseMind" not in vesta


async def test_a_push_message_carries_her_name_in_the_persons_ha_language(
    tmp_path: Path,
) -> None:
    """A push said "HouseMind" above it, in the house's language. Now her name – the one the
    administrator gave her – and the language Home Assistant shows that person, also while
    the app is closed; their own choice in HouseMind still comes first."""
    hm, ha, _decision = await _did_something(tmp_path)
    await hm.set_house_setting("vesta_name", "Hestia")  # the name the administrator gave her
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ME)
        await ws.send_json({"type": "hello", "browserLanguage": "nl", "haLanguage": "en"})

        async def kept() -> bool:
            row = await hm.db.fetchone(
                "SELECT preferences FROM people WHERE user_id = 'demo-user-1'"
            )
            return row is not None and json.loads(row["preferences"]).get("ha_language") == "en"

        await _settled(kept)
        await ws.close()
    me = next(p for p in hm._people if p.user_id == "demo-user-1")
    assert me.language == "en"
    assert me.notify_services, "the demo resident has a phone"
    assert hm.world is not None
    snapshot = hm.world.snapshot(hm.clock.now())
    await hm._raise(
        Kind.IMPORTANT,
        "open:door",
        "notify.open_when_away",
        {"device": "Back door"},
        snapshot,
        concerns="user:demo-user-1",
    )
    pushed = [n for n in ha.notifications if n[0] in me.notify_services]
    assert pushed and pushed[-1][1] == "Hestia"
    assert "HouseMind" not in pushed[-1][1] + pushed[-1][2]
    # Their own choice in HouseMind wins over Home Assistant's.
    await hm.db.execute(
        "UPDATE people SET preferences = ? WHERE user_id = 'demo-user-1'",
        (json.dumps({"language": "de", "ha_language": "en"}),),
    )
    await hm._refresh_people()
    assert next(p for p in hm._people if p.user_id == "demo-user-1").language == "de"
    await hm.stop()


# -- time to talk and to read (E-299, 20.20) -------------------------------------------------


async def test_the_time_to_talk_and_read_is_one_choice_that_goes_with_the_person(
    tmp_path: Path,
) -> None:
    """Six seconds to talk and ten to read were too short for some. Now each person picks
    Normaal, Langer or Lang once, kept with them, so every device uses it."""
    hm, _ha, _decision = await _did_something(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ME)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        assert (await _until(ws, "snapshot"))["snapshot"]["snapshot"]["prefs"]["timing"] == "normal"
        await ws.send_json({"type": "set_pref", "key": "timing", "value": "slow"})  # ignored
        await ws.send_json({"type": "set_pref", "key": "timing", "value": "long"})

        async def kept() -> bool:
            row = await hm.db.fetchone(
                "SELECT preferences FROM people WHERE user_id = 'demo-user-1'"
            )
            return row is not None and json.loads(row["preferences"]).get("timing") == "long"

        await _settled(kept)
        await ws.close()
        other = await client.ws_connect("/api/ws", headers=ME)  # another device of theirs
        await other.send_json({"type": "hello", "browserLanguage": "nl"})
        assert (await _until(other, "snapshot"))["snapshot"]["snapshot"]["prefs"][
            "timing"
        ] == "long"
        await other.close()
    await hm.stop()


# -- a thumb in the logbook (E-298), asked for after trying it in the real house -----------


def _done_line(log: dict[str, Any], decision: int) -> dict[str, Any]:
    return next(i for i in log["items"] if i["type"] == "done" and i["id"] == decision)


async def test_the_logbook_shows_the_answer_and_what_vesta_learned(tmp_path: Path) -> None:
    """After 👍 or 👎 on her "why", nothing showed that an answer was given or that Vesta
    learned from it. Now the line in the logbook says who answered (you or someone, never a
    name), and what she did with it: a yes, or a correction – with how far from a step back."""
    from housemind.server.logbook import logbook
    from housemind.server.views import load_viewer

    hm, _ha, decision = await _did_something(tmp_path)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ME)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "thumbs", "id": decision, "good": False, "via": "vesta"})
        await _until(ws, "say")
        await ws.close()
    viewer = await load_viewer(hm, "demo-user-1", None, trusted_local=False)
    line = _done_line(await logbook(hm, viewer, "demo-user-1", "nl"), decision)
    [answer] = line["answers"]
    assert answer["good"] is False
    assert answer["text"].startswith("👎 Niet goed – jij, ")
    assert "correctie voor licht bij binnenkomen in de Keuken: 1 van de 2" in answer["text"]
    await hm.feedback(True, decision, "demo-user-2")
    line = _done_line(await logbook(hm, viewer, "demo-user-1", "nl"), decision)
    assert line["answers"][1]["text"].startswith("👍 Goed – iemand, ")
    assert "een ja voor licht bij binnenkomen in de Keuken" in line["answers"][1]["text"]
    await hm.stop()


async def test_no_thumbs_under_what_she_did_not_do(tmp_path: Path) -> None:
    """In the real house the thumbs showed under "not done" (someone operated the lamp by
    hand), and a thumb there landed on the last thing she did. Now: no thumbs under what she
    left, and a thumb for it is not moved to another action."""
    from housemind.safety.gate import Decision, Outcome

    hm, _ha, decision = await _did_something(tmp_path)
    done = hm.recent[decision].decision
    left = Decision(done.request, Outcome.DENIED, "people_win", None, (), hm.clock.now())
    left_id = await hm._record(left)
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers=ME)
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await ws.send_json({"type": "why", "id": left_id})
        said = (await _until(ws, "say"))["say"]
        assert "thumbs" not in said and said["text"].startswith("Ik wilde")
        await ws.close()
    assert (await hm.feedback(False, left_id, "demo-user-1")) == {
        "ok": False,
        "reason": "not_done",
    }
    assert await hm.thumbs_week(hm.clock.now()) == {}  # not on the last action either
    await hm.stop()


async def test_forget_me_takes_the_name_off_the_thumbs(tmp_path: Path) -> None:
    """A thumb carries who gave it; after "Vergeet mij" it still counts, without the name."""
    hm, _ha, decision = await _did_something(tmp_path)
    await hm.feedback(True, decision, "demo-user-1")
    assert await hm.forget_me("demo-user-1")
    rows = await hm.db.fetchall("SELECT user_id FROM thumbs")
    assert [r["user_id"] for r in rows] == [None]
    assert await hm.thumbs_week(hm.clock.now()) == {"lighting": (1, 0)}
    await hm.stop()


def test_a_column_already_there_counts_as_added_and_other_errors_still_fail() -> None:
    """Migration 18 adds a column; over a schema that already has it (an older version number
    on a newer file) that is no failure, anything else still is (E-301)."""
    import sqlite3

    import pytest

    from housemind.memory.database import _apply

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE thumbs (id INTEGER PRIMARY KEY)")
    _apply(conn, "ALTER TABLE thumbs ADD COLUMN learned TEXT")
    _apply(conn, "ALTER TABLE thumbs ADD COLUMN learned TEXT")  # again: already there
    with pytest.raises(sqlite3.OperationalError):
        _apply(conn, "ALTER TABLE nothing ADD COLUMN learned TEXT")
    with pytest.raises(sqlite3.OperationalError):
        _apply(conn, "CREATE TABLE thumbs (id INTEGER)")
    conn.close()
