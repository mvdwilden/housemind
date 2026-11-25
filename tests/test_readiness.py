"""Klaar voor autonomie? (E-256, B-100), from the user's side: per part, per skill × room and per
automation how far HouseMind is, what is still needed in plain words, the same thresholds as
learning itself, more per level, and Vesta who says it. The card changes nothing. Each test
fails without it; what it checks is its docstring."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.learning import SITUATIONS, Result
from housemind.brain.readiness import readiness
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind
from housemind.server import views
from housemind.server.web import create_app
from tests.test_automations import HALL, HALL_CONFIG, start
from tests.test_knowing import _said
from tests.test_vesta_asks import _until

CONTEXT = "workday|19:30"


def _seen(
    hm: HouseMind,
    skill: str,
    area: str,
    step: Step | None,
    days: float,
    hits: int = 0,
    misses: int = 0,
    yes: int = 0,
    corrections: int = 0,
) -> None:
    """What HouseMind saw of one skill in one room: right, different, answers, undone."""
    learning, now = hm.learning, hm.clock.now()
    assert learning is not None
    for result, times in ((Result.HIT, hits), (Result.MISS, misses)):
        for _ in range(times):
            learning._add(skill, area, CONTEXT, result, now)
    for _ in range(yes):
        learning.answered(skill, area, CONTEXT, True, now)
    for _ in range(corrections):
        learning._add(skill, area, CONTEXT, Result.CORRECTION, now)
    if step is not None:
        learning.steps[(skill, area)] = step
    learning.first_seen[(skill, area)] = now - timedelta(days=days)


def _part(parts: list[dict[str, Any]], domain: str) -> dict[str, Any]:
    return next(p for p in parts if p["domain"] == domain)


def _skill(part: dict[str, Any], what: str) -> dict[str, Any]:
    return next(s for s in part["skills"] if s["what"] == what)


async def test_observe_says_what_is_still_needed(tmp_path: Path) -> None:
    """The administrator's example, for a habit HouseMind learns all by itself: watching the
    kitchen light, 6 of 10 situations and 5 of 7 days, the score against 85% – and the
    sentence "Nog 4 situaties en 2 dagen, dan stel ik dit voor"."""
    hm, _clock, _ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.start_knowledge.discard("light_on_entry")  # not general knowledge (E-160)
    _seen(hm, "light_on_entry", "kitchen", Step.OBSERVE, days=5, hits=6)
    lighting = _part(await readiness(hm, "nl", 3, True), "lighting")
    kitchen = _skill(lighting, "licht bij binnenkomen in de keuken")
    assert kitchen["step"] == "observe" and kitchen["stepName"] == "Observeren"
    assert kitchen["bar"] == {"value": 100, "target": 85}
    counters = [(c["label"], c["value"], c["target"]) for c in kitchen["counters"]]
    assert counters == [("situaties", 6, 10), ("dagen", 5, 7)]
    assert kitchen["text"] == "Nog 4 situaties en 2 dagen, dan stel ik dit voor."
    assert lighting["counts"]["observe"] >= 1
    await hm.stop()


async def test_counters_say_seen_and_needed_not_a_fraction(tmp_path: Path) -> None:
    """The administrator's question from the real house: "situaties 96/20" read like a
    fraction. Now each counter says what is there and what is needed, with a check when it is
    enough – "96 situaties gezien · 10 nodig" ✓ – in every language, and the corrections that
    count against say when a step back comes."""
    hm, _clock, _ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.start_knowledge.discard("light_on_entry")  # a habit it learns by itself
    _seen(hm, "light_on_entry", "kitchen", Step.OBSERVE, days=5, hits=90, misses=6)
    _seen(hm, "light_on_entry", "hall", Step.PROPOSE, days=8, yes=1)
    _seen(hm, "light_on_entry", "living", Step.AUTONOMOUS, days=20, corrections=1)
    lighting = _part(await readiness(hm, "nl", 3, True), "lighting")
    kitchen = _skill(lighting, "licht bij binnenkomen in de keuken")
    assert [(c["text"], c["met"]) for c in kitchen["counters"]] == [
        ("96 situaties gezien · 10 nodig", True),
        ("5 dagen bezig · 7 nodig", False),
    ]
    hall = _skill(lighting, "licht bij binnenkomen in de gang")
    assert [(c["text"], c["met"]) for c in hall["counters"]] == [
        ("1 situatie gezien · 10 nodig", False),
        ("8 dagen bezig · 7 nodig", True),
        ("1 antwoord gekregen · 15 nodig", False),
    ]
    living = next(s for s in lighting["skills"] if s["area"] == "living")
    assert [(c["text"], c["met"]) for c in living["counters"]] == [
        ("1 keer terug of nee deze week · bij 2 een stap terug", False),
    ]
    for lang, said in (
        ("en", "96 situations seen · 10 needed"),
        ("de", "96 Situationen gesehen · 10 nötig"),
        ("fr", "96 situations · il en faut 10"),
    ):
        part = _part(await readiness(hm, lang, 3, True), "lighting")
        skill = next(s for s in part["skills"] if s["area"] == "kitchen")
        assert skill["counters"][0]["text"] == said
    await hm.stop()


async def test_the_device_list_says_the_same_step_as_the_card(tmp_path: Path) -> None:
    """Found while testing the coloured step labels: an administrator put Verlichting on
    Autonoom, the card said "1 Autonoom" (the hall, which an automation taught), and
    Apparaten en entiteiten still said Observeren for every lamp. Now both say Autonoom – and
    since every room with lamps has its rules at once (E-485), the card says so for each."""
    hm, _clock, _ha = await start(tmp_path)
    assert await hm.set_admin_step("lighting", "autonomous", None)
    lighting = _part(await readiness(hm, "nl", 3, True), "lighting")
    assert lighting["counts"]["autonomous"] > 1
    hall = [r for r in lighting["skills"] if r["area"] == "hall" and r["skill"] == "light_on_entry"]
    assert hall and hall[0]["step"] == "autonomous"
    lamps = [row for row in views.devices(hm, "nl") if row["part"] == "lighting"]
    assert lamps and {row["step"] for row in lamps} == {"autonomous"}
    await hm.stop()


async def test_each_row_has_its_skill_and_room_apart_for_the_filters(tmp_path: Path) -> None:
    """The administrator's wish from the real house: filter and group the card per part and
    per room, like Apparaten en entiteiten. Each row carries the skill and the room apart, and
    each automation the rooms it teaches, so the list can filter on them."""
    hm, _clock, _ha = await start(tmp_path)
    _seen(hm, "light_on_entry", "kitchen", Step.OBSERVE, days=5, hits=14, misses=2)
    lighting = _part(await readiness(hm, "nl", 3, True), "lighting")
    kitchen = _skill(lighting, "licht bij binnenkomen in de keuken")
    assert kitchen["skillName"] == "licht bij binnenkomen"
    assert kitchen["room"] == hm.model.areas["kitchen"].name
    automation = lighting["automations"][0]
    taught = {a for s, a in hm.automations.readings[automation["id"]].learned}
    assert automation["rooms"] == sorted(hm.model.areas[a].name for a in taught)
    assert automation["rooms"]
    await hm.stop()


async def test_general_knowledge_goes_by_its_own_threshold(tmp_path: Path) -> None:
    """Found while testing: almost everything HouseMind does is general knowledge (E-160), which
    it proposes at the first situation – unless people often did otherwise: then from 40%
    again. The card says that, not the 85% of a new habit, and the step that applies."""
    hm, clock, _ha = await start(tmp_path)
    await clock.advance(3 * 3600)  # past the first two hours
    _seen(hm, "light_on_entry", "kitchen", Step.OBSERVE, days=3, hits=2, misses=6)
    kitchen = _skill(
        _part(await readiness(hm, "nl", 3, True), "lighting"),
        "licht bij binnenkomen in de keuken",
    )
    assert kitchen["step"] == "observe" and kitchen["bar"] == {"value": 25, "target": 40}
    assert kitchen["text"] == (
        "Dit is algemene kennis, maar jullie doen het hier vaak anders: 25% zoals ik het zou "
        "doen. Vanaf 40% stel ik het weer voor."
    )
    assert hm.learning is not None
    await hm.learning.review_steps(clock.now())  # what learning itself does with it
    assert hm.learning.steps[("light_on_entry", "kitchen")] is Step.OBSERVE
    _seen(hm, "light_on_entry", "kitchen", None, days=3, hits=2)
    await hm.learning.review_steps(clock.now())  # 4 of 10: from 40% it proposes again
    assert hm.learning.steps[("light_on_entry", "kitchen")] is Step.PROPOSE
    await hm.stop()


async def test_propose_counts_answers_and_low_scores_say_so(tmp_path: Path) -> None:
    """Proposing: how often it went well against 85%, the situations and days for the one
    question (E-460), and the answers when there are any. Where people often do it differently,
    she says so – with the 85% that is needed."""
    hm, _clock, _ha = await start(tmp_path)
    _seen(hm, "light_on_entry", "hall", Step.PROPOSE, days=5, yes=3)
    _seen(hm, "light_on_entry", "kitchen", Step.OBSERVE, days=9, hits=12, misses=10)
    assert hm.learning is not None
    hm.learning.start_knowledge.discard("light_on_entry")  # a habit it learns by itself
    lighting = _part(await readiness(hm, "nl", 3, True), "lighting")
    hall = _skill(lighting, "licht bij binnenkomen in de gang")
    assert hall["bar"] == {"value": 100, "target": 85}
    assert [(c["key"], c["value"], c["target"]) for c in hall["counters"]] == [
        ("situations", 3, 10),
        ("days", 5, 7),
        ("answers", 3, 15),
    ]
    assert hall["text"] == (
        "Nog 7 situaties en 2 dagen, dan vraag ik of ik het voortaan zelf mag doen."
    )
    kitchen = _skill(lighting, "licht bij binnenkomen in de keuken")
    assert kitchen["text"] == (
        "Jullie doen het vaak anders: 55% zoals ik het zou doen, en nodig is 85%. "
        "Ik blijf meekijken."
    )
    await hm.stop()


async def test_held_back_by_the_limit_says_so_and_only_admins_get_the_button(
    tmp_path: Path,
) -> None:
    """Ready for Autonomous, but the administrator keeps Verlichting at Voorstellen: the card
    says so, shows the step that applies, and only an administrator gets the button to the
    limit. It changes nothing itself. Autonomous elsewhere: what putting it back twice does."""
    hm, _clock, _ha = await start(tmp_path)
    _seen(hm, "light_on_entry", "living", Step.AUTONOMOUS, days=10)
    _seen(hm, "covers_close_dark", "living", Step.AUTONOMOUS, days=10, corrections=1)
    await hm.set_house_setting("admin_max_step", {"light": "propose"})
    admin = await readiness(hm, "nl", 3, True)
    lighting = _part(admin, "lighting")
    living = _skill(lighting, "licht bij binnenkomen in de woonkamer")
    assert living["step"] == "propose" and living["heldBack"]
    assert living["text"] == "Klaar voor Autonoom, maar Verlichting mag tot Voorstellen."
    assert lighting["capName"] == "Voorstellen" and lighting["limit"]
    assert lighting["text"].startswith("Het dichtst bij de volgende stap: licht bij binnenkomen")
    assert not _part(await readiness(hm, "nl", 3, False), "lighting")["limit"]
    assert hm.learning is not None
    assert hm.learning.steps[("light_on_entry", "living")] is Step.AUTONOMOUS  # unchanged
    covers = _part(admin, "covers")
    curtain = covers["skills"][0]
    assert curtain["step"] == "autonomous"
    assert curtain["text"] == (
        "Ik doe dit zelf. Deze week zette iemand het 1 keer terug; bij 2 keer vraag ik het weer "
        "eerst."
    )
    assert [(c["key"], c["value"], c["target"]) for c in curtain["counters"]] == [
        ("corrections", 1, 2)  # two in a week: a step back (20.9)
    ]
    await hm.stop()


async def test_automations_show_how_close_a_takeover_is(tmp_path: Path) -> None:
    """Per automation: in how many of its rooms HouseMind is on Autonomous (20.12) and what is
    missing in the nearest one; ready, it asks an administrator (E-42); one that also does
    what HouseMind does not, stays – and the card says so."""
    both = {
        **HALL_CONFIG,
        "id": "1003",
        "alias": "Hall and kitchen light",
        "triggers": [
            {"trigger": "state", "entity_id": "binary_sensor.hall_motion", "to": "on"},
            {"trigger": "state", "entity_id": "binary_sensor.kitchen_motion", "to": "on"},
        ],
        "actions": [
            {
                "action": "light.turn_on",
                "target": {"entity_id": ["light.hall", "light.kitchen"]},
                "data": {},
            }
        ],
    }
    message = {
        **HALL_CONFIG,
        "id": "1004",
        "alias": "Hall light and a message",
        "actions": [
            *HALL_CONFIG["actions"],
            {"action": "notify.mobile_app_phone_1", "data": {"message": "Hi"}},
        ],
    }
    hm, _clock, _ha = await start(
        tmp_path, {HALL: HALL_CONFIG, "automation.both": both, "automation.msg": message}
    )
    automations = {
        a["id"]: a for a in _part(await readiness(hm, "nl", 3, True), "lighting")["automations"]
    }
    assert automations[HALL]["text"].startswith(
        "0 van 1 ruimtes klaar. Het dichtst bij de volgende stap: licht bij binnenkomen in de gang."
    )
    assert automations["automation.msg"]["state"] == "stays"
    assert "doet ook dingen die ik niet doe" in automations["automation.msg"]["text"]
    _seen(hm, "light_on_entry", "hall", Step.PROPOSE, days=8, hits=20)
    automations = {
        a["id"]: a for a in _part(await readiness(hm, "nl", 3, True), "lighting")["automations"]
    }
    assert automations[HALL]["ready"] == 0  # knowing it well is not enough (20.12)
    _seen(hm, "light_on_entry", "hall", Step.AUTONOMOUS, days=8)
    automations = {
        a["id"]: a for a in _part(await readiness(hm, "nl", 3, True), "lighting")["automations"]
    }
    assert (automations[HALL]["ready"], automations[HALL]["total"]) == (1, 1)
    assert automations[HALL]["state"] == "ready"
    two = automations["automation.both"]
    assert (two["ready"], two["total"]) == (1, 2)
    assert two["text"].startswith(
        "1 van 2 ruimtes klaar. Het dichtst bij de volgende stap: licht bij binnenkomen in de "
        "keuken."
    )
    await hm.stop()


async def test_more_per_level_and_in_place_of_the_learning_table(tmp_path: Path) -> None:
    """Levels 1 and 2: per part one sentence and the steps; level 3: per room and the
    automations; level 4: the numbers too. The old table "Wat ik leer" is gone, and a sheet
    can ask for the card on any level."""
    hm, _clock, _ha = await start(tmp_path)
    _seen(hm, "light_on_entry", "kitchen", Step.OBSERVE, days=5, hits=14, misses=2)
    simple = _part(await readiness(hm, "nl", 2, False), "lighting")
    assert "skills" not in simple and "automations" not in simple and simple["text"]
    expert = _skill(
        _part(await readiness(hm, "nl", 4, True), "lighting"), "licht bij binnenkomen in de keuken"
    )
    assert expert["numbers"]["score"] == 88 and expert["numbers"]["hits"] == 14
    assert "numbers" not in _skill(
        _part(await readiness(hm, "nl", 3, True), "lighting"), "licht bij binnenkomen in de keuken"
    )
    await hm.set_house_setting("new_person_level", 2)  # new people begin Carefree (E-287)
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    snapshot = await views.snapshot(hm, viewer)
    assert "readiness" in snapshot and "learning" not in snapshot
    async with TestClient(TestServer(create_app(hm, tmp_path, ingress_only=False))) as client:
        ws = await client.ws_connect("/api/ws", headers={"X-Remote-User-Id": "demo-user-1"})
        await ws.send_json({"type": "hello", "browserLanguage": "nl"})
        await _until(ws, "snapshot")
        await ws.send_json({"type": "get", "what": "readiness"})
        data = (await _until(ws, "data"))["data"]
        assert data["what"] == "readiness" and _part(data["data"], "lighting")
        await ws.close()
    await hm.stop()


async def test_vesta_says_how_far_and_offers_the_card(tmp_path: Path) -> None:
    """ "Hoe ver ben je met de verlichting?": Vesta says the sentence of that part and offers
    to show the card; through Assist (no screen) she only says it. "Hoe ver ben je?" gives the
    parts that have something to say."""
    hm, _clock, _ha = await start(tmp_path)
    _seen(hm, "light_on_entry", "kitchen", Step.OBSERVE, days=5, hits=14, misses=2)
    part = _part(await readiness(hm, "nl", 1, True), "lighting")
    reply = await hm.talk("Hoe ver ben je met de verlichting?", "nl", 3, "demo-user-1")
    assert reply["open"] == {"sheet": "readiness", "section": "ready-lighting"}
    assert reply["text"] in _said("know_ready", part="Verlichting", text=part["text"])
    spoken = await hm.talk(
        "Hoe ver ben je met de lampen?", "nl", 3, "demo-user-1", channel="assist"
    )
    assert "open" not in spoken
    assert spoken["text"] in _said("know_ready_spoken", part="Verlichting", text=part["text"])
    overall = await hm.talk("Hoe ver ben je?", "nl", 3, "demo-user-1")
    assert overall["open"] == {"sheet": "readiness", "section": "readiness"}
    assert f"Verlichting: {part['text']}" in overall["text"]
    await hm.stop()


async def test_the_counts_on_the_card_are_the_counts_of_learning(tmp_path: Path) -> None:
    """The card computes nothing of its own: the situations it shows are the plain count that
    decides the step (E-56)."""
    hm, _clock, _ha = await start(tmp_path)
    _seen(hm, "light_on_entry", "kitchen", Step.OBSERVE, days=5, hits=3)
    assert hm.learning is not None
    situations = hm.learning.counts[("light_on_entry", "kitchen", "*", SITUATIONS)].weight
    kitchen = _skill(
        _part(await readiness(hm, "nl", 3, True), "lighting"), "licht bij binnenkomen in de keuken"
    )
    assert kitchen["counters"][0]["value"] == round(situations) == 3
    await hm.stop()
