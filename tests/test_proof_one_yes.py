"""Proof and one yes (E-460, B-375): "hoe zou HouseMind sneller over kunnen gaan naar voorstellen
en dan naar autonoom, maar dan ook echt naar autonoom?" In the real house 0 of 44 rules were on
Autonomous after twenty days: repetition did not count for that step, only answers did, and 34
rules could not ask because an automation steers them. Now repetition is the proof and one yes
is enough. Each test fails without the build."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from housemind.brain.automations import Status
from housemind.brain.learning import Result
from housemind.brain.proposals import Answer
from housemind.brain.readiness import readiness
from housemind.brain.skills import SkillEngine
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source, State, StateChange
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.parts.proof import PROOF_ASK_AGAIN
from housemind.runtime import HouseMind
from housemind.safety.gate import ActionRequest, Layer, Outcome, watched
from housemind.server import views
from tests.test_automations import HALL, HALL_CONFIG, hall_light_on, start
from tests.test_group_first import TABLE, _dining
from tests.test_group_first import _house as _group_house

CONTEXT = "workday|19:30"
ON = ("light_on_entry", "hall")
OFF = ("light_off_leave", "hall")
KITCHEN = ("light_on_entry", "kitchen")
HALL_OFF = "automation.hall_light_off"
HALL_OFF_CONFIG: dict[str, Any] = {
    "id": "1004",
    "alias": "Hall light off at no motion",
    "triggers": [{"trigger": "state", "entity_id": "binary_sensor.hall_motion", "to": "off"}],
    "actions": [
        {"action": "light.turn_off", "target": {"entity_id": "light.hall"}, "data": {}},
    ],
}


async def _people(hm: HouseMind, admin_level: int = 4, resident: bool = False) -> None:
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        ("demo-user-1", "Resident 1", admin_level, json.dumps({}), hm.clock.now().isoformat()),
    )
    await hm._refresh_people()


def _prove(hm: HouseMind, pair: tuple[str, str], hits: int = 12, days: float = 8.0) -> None:
    """HouseMind saw it go right this often, this long; the rule is on Proposing."""
    assert hm.learning is not None
    now = hm.clock.now()
    for _ in range(hits):
        hm.learning._add(*pair, CONTEXT, Result.HIT, now)
    hm.learning.first_seen[pair] = now - timedelta(days=days)
    hm.learning.steps[pair] = Step.PROPOSE


async def _review(hm: HouseMind) -> None:
    """Fifteen minutes on: HouseMind reviews the steps and asks what is proven."""
    await hm.clock.advance(16 * 60)  # type: ignore[attr-defined]
    await hm.tick()


async def _waiting(
    hm: HouseMind, admin: bool = True, user: str | None = "demo-user-1"
) -> list[Any]:
    return await views.notifications(hm, "nl", admin, user)


async def _about(hm: HouseMind, start_of: str, admin: bool = True) -> dict[str, Any] | None:
    for item in await _waiting(hm, admin):
        row = await hm.db.fetchone("SELECT topic FROM notifications WHERE id = ?", (item["id"],))
        if row is not None and str(row["topic"]).startswith(start_of):
            return dict(item)
    return None


async def _row(hm: HouseMind, skill: str, area: str, level: int = 4) -> dict[str, Any]:
    for part in await readiness(hm, "nl", level, True):
        for row in part.get("skills", []):
            if (row["skill"], row["area"]) == (skill, area):
                return dict(row)
    raise AssertionError(f"no row {skill} {area}")


# -- what proof is ---------------------------------------------------------------------------


async def test_proof_is_counting_ten_times_right_over_a_week(tmp_path: Path) -> None:
    """Standard 85%, 10 times, 7 days; the way a new house starts. Nine times, or six days, or
    too often otherwise, is not proof."""
    hm, clock, _ = await start(tmp_path, {})
    learning = hm.learning
    assert learning is not None
    now = clock.now()
    _prove(hm, KITCHEN, hits=9)
    assert not learning.proven(*KITCHEN, now)
    learning._add(*KITCHEN, CONTEXT, Result.HIT, now)
    assert learning.proven(*KITCHEN, now)
    learning.first_seen[KITCHEN] = now - timedelta(days=6)
    assert not learning.proven(*KITCHEN, now)
    learning.first_seen[KITCHEN] = now - timedelta(days=8)
    for _ in range(3):
        learning._add(*KITCHEN, CONTEXT, Result.MISS, now)
    assert not learning.proven(*KITCHEN, now)  # 10 of 13 is 77%
    await hm.stop()


async def test_a_house_keeps_its_own_limits(tmp_path: Path) -> None:
    """The limits per part are the house's (E-297): nothing fixed for one house."""
    hm, clock, _ = await start(tmp_path, {})
    assert hm.learning is not None
    values: dict[str, Any] = {"propose_score": 70, "propose_situations": 3, "propose_days": 1}
    assert await hm.set_thresholds("lighting", values)
    _prove(hm, KITCHEN, hits=3, days=2)
    assert hm.learning.proven(*KITCHEN, clock.now())
    await hm.stop()


async def test_what_learns_from_answers_proves_itself_there(tmp_path: Path) -> None:
    """Saving heat: people rarely do that by hand, so doing nothing proves nothing."""
    hm, clock, _ = await start(tmp_path, {})
    assert hm.learning is not None
    _prove(hm, ("setback_sleep", "kitchen"), hits=30)
    assert not hm.learning.proven("setback_sleep", "kitchen", clock.now())
    await hm.stop()


async def test_undone_at_once_counts_against_the_proof_and_is_no_step_back(tmp_path: Path) -> None:
    """An automation does what HouseMind expected and someone undoes it within five minutes:
    that is not "right". It is no correction of HouseMind, so no step back."""
    hm, clock, _ = await start(tmp_path, {})
    learning = hm.learning
    assert learning is not None and hm.gate is not None
    now = clock.now()
    _prove(hm, OFF, hits=2)
    on = State("light.hall", "on", {}, now)
    off = State("light.hall", "off", {}, now)
    for _ in range(4):
        decision = await hm.gate.submit(hall_light_off(clock))
        learning.predicted(decision.request, decision, now)
        learning.observe(
            StateChange("light.hall", on, off, Source.AUTOMATION), "hall", CONTEXT, now
        )
        learning.observe(StateChange("light.hall", off, on, Source.PERSON), "hall", CONTEXT, now)
    assert learning.total(*OFF, Result.PUT_BACK, now) == 2.0  # 4 times, with the automation's 0.5
    assert learning.put_back_share(*OFF, now) > 0
    assert learning.recent_setbacks(*OFF, now) == []
    await hm.stop()


def hall_light_off(clock: SimulatedClock) -> ActionRequest:
    return ActionRequest(
        Layer.REFLEX,
        "light.hall",
        "light.turn_off",
        {},
        reason="left",
        skill=OFF[0],
        area_id=OFF[1],
        created_at=clock.now(),
        facts={"context": CONTEXT},
    )


# -- one question about one rule -------------------------------------------------------------


async def test_a_proven_rule_gets_one_question_that_stays_and_one_yes_is_enough(
    tmp_path: Path,
) -> None:
    """The heart of it: no twelve answers. One question in Wacht op jou, also after hours and
    days; a yes puts the rule on Autonomous at once, and the gate then does it."""
    hm, clock, _ = await start(tmp_path, {})
    await _people(hm)
    _prove(hm, KITCHEN)
    await _review(hm)
    card = await _about(hm, "ready:")
    assert card is not None
    assert card["actions"] == ["yes", "later", "no"]
    assert card["labels"]["yes"] == "Ja, voortaan zelf"
    assert "licht bij binnenkomen in de Keuken" in card["text"]
    assert "{" not in card["text"]
    await clock.advance(3 * 86400)  # it does not expire
    await _review(hm)
    assert await _about(hm, "ready:") is not None
    assert hm.learning is not None and hm._skill_step(*KITCHEN) is Step.PROPOSE
    done = await hm.answer_ready("ready:light_on_entry@kitchen", "yes", "demo-user-1")
    assert done["ok"]
    assert hm._skill_step(*KITCHEN) is Step.AUTONOMOUS
    assert await _about(hm, "ready:") is None
    request = ActionRequest(
        Layer.REFLEX,
        "light.kitchen",
        "light.turn_on",
        {},
        skill=KITCHEN[0],
        area_id=KITCHEN[1],
        created_at=clock.now(),
    )
    assert (await hm.gate.submit(request)).outcome is Outcome.EXECUTED  # type: ignore[union-attr]
    await _review(hm)
    assert await _about(hm, "ready:") is None  # asked once
    await hm.stop()


async def test_not_yet_comes_back_in_a_week_and_never_does_not(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {})
    await _people(hm)
    _prove(hm, KITCHEN, hits=30)
    await _review(hm)
    assert (await hm.answer_ready("ready:light_on_entry@kitchen", "later", None))["ok"]
    await _review(hm)
    assert await _about(hm, "ready:") is None
    row = await _row(hm, *KITCHEN)
    assert "Nog niet" in row["text"]
    assert hm._skill_step(*KITCHEN) is Step.PROPOSE  # the question at the moment goes on
    await clock.advance(PROOF_ASK_AGAIN.total_seconds())
    await _review(hm)
    assert await _about(hm, "ready:") is not None
    assert (await hm.answer_ready("ready:light_on_entry@kitchen", "no", None))["ok"]
    await clock.advance(5 * PROOF_ASK_AGAIN.total_seconds())
    await _review(hm)
    assert await _about(hm, "ready:") is None
    await hm.stop()


async def test_a_question_goes_when_the_rule_is_no_longer_proven(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {})
    await _people(hm)
    _prove(hm, KITCHEN, hits=10)
    await _review(hm)
    assert await _about(hm, "ready:") is not None
    assert hm.learning is not None
    for _ in range(10):
        hm.learning._add(*KITCHEN, CONTEXT, Result.MISS, clock.now())
    await _review(hm)
    assert await _about(hm, "ready:") is None
    await hm.stop()


async def test_a_step_back_asks_again_only_after_new_proof(tmp_path: Path) -> None:
    """Two corrections in a week: HouseMind asks first again, and only what came since counts,
    for at least a week (20.9)."""
    hm, clock, _ = await start(tmp_path, {})
    await _people(hm)
    _prove(hm, KITCHEN)
    await _review(hm)
    await hm.answer_ready("ready:light_on_entry@kitchen", "yes", None)
    learning = hm.learning
    assert learning is not None
    for _ in range(2):
        learning._add(*KITCHEN, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    await _review(hm)
    assert hm._skill_step(*KITCHEN) is Step.PROPOSE
    assert await _about(hm, "ready:") is None
    assert not learning.proven(*KITCHEN, clock.now())
    for _ in range(12):
        learning._add(*KITCHEN, CONTEXT, Result.HIT, clock.now())
    assert not learning.proven(*KITCHEN, clock.now())  # enough times, but not a week yet
    await clock.advance(8 * 86400)
    await _review(hm)
    assert await _about(hm, "ready:") is not None
    await hm.stop()


async def test_a_lock_never_gets_the_question(tmp_path: Path, monkeypatch: Any) -> None:
    """What only may be proposed never goes along: the catalogue's limit stays."""
    hm, clock, _ = await start(tmp_path, {})
    await _people(hm)
    assert hm.skills is not None
    _prove(hm, KITCHEN)
    assert hm.may_prove(*KITCHEN, clock.now())
    entity = hm.skills.units_of(*KITCHEN)[0]
    kind = hm.skills._kind(entity)
    assert kind is not None
    monkeypatch.setattr(SkillEngine, "_kind", lambda self, e: replace(kind, max_step=Step.PROPOSE))
    assert not hm.may_prove(*KITCHEN, clock.now())
    await hm.stop()


async def test_an_administrators_limit_holds_the_question_back(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {})
    await _people(hm)
    _prove(hm, KITCHEN)
    await hm.set_house_setting("admin_max_step", {"light": "propose"})
    assert hm.domain_cap("lighting") is Step.PROPOSE
    assert not hm.may_prove(*KITCHEN, clock.now())
    await hm.stop()


# -- the automation --------------------------------------------------------------------------


async def test_an_automation_gets_one_question_and_a_yes_takes_it_over(tmp_path: Path) -> None:
    """The circle is gone: the rule cannot ask, so when it is proven the question is about the
    automation. Only an administrator sees it. A yes puts its rule on Autonomous and switches it
    off in Home Assistant, and HouseMind does the work at once."""
    hm, clock, ha = await start(tmp_path, {HALL: HALL_CONFIG})
    await _people(hm)
    await hm._sync_automations()
    _prove(hm, ON)
    await _review(hm)
    assert await _about(hm, "ready:") is None
    card = await _about(hm, "automation:")
    assert card is not None
    assert card["actions"] == ["yes", "later", "no"]
    assert "‘Hall light at motion’ gaat dan uit" in card["text"]
    assert await _about(hm, "automation:", admin=False) is None
    done = await hm.answer_automation(HALL, True, "demo-user-1")
    assert done["ok"]
    assert hm.automations.status[HALL] is Status.TURNED_OFF
    assert ha.states[HALL].state == "off"
    assert hm._skill_step(*ON) is Step.AUTONOMOUS
    assert await _about(hm, "automation:") is None
    assert (await hm.gate.submit(hall_light_on(clock))).outcome is Outcome.EXECUTED  # type: ignore[union-attr]
    await hm.stop()


async def test_two_automations_on_the_same_lamp_are_one_question(tmp_path: Path) -> None:
    """A very common pair: one puts the lamp on, one puts it out. One at a time would leave the
    lamp watched and nobody doing the work, so they go together, both proven, in one question;
    a step back brings both on again."""
    hm, clock, ha = await start(tmp_path, {HALL: HALL_CONFIG, HALL_OFF: HALL_OFF_CONFIG})
    await _people(hm)
    await hm._sync_automations()
    _prove(hm, ON)
    await _review(hm)
    assert await _about(hm, "automation:") is None  # the other is not proven yet
    assert (await _row(hm, *ON))["proven"]
    assert "Zodra alles" in (await _row(hm, *ON))["text"]
    _prove(hm, OFF)
    await _review(hm)
    card = await _about(hm, "automation:")
    assert card is not None
    assert "gaan dan uit" in card["text"]
    assert "Hall light at motion" in card["text"] and "Hall light off" in card["text"]
    row = await hm.db.fetchone("SELECT topic FROM notifications WHERE id = ?", (card["id"],))
    assert row is not None
    head = str(row["topic"]).split(":", 1)[1]
    assert (await hm.answer_automation(head, True, "demo-user-1"))["ok"]
    assert ha.states[HALL].state == "off"
    assert ha.states[HALL_OFF].state == "off"
    assert hm._skill_step(*ON) is Step.AUTONOMOUS and hm._skill_step(*OFF) is Step.AUTONOMOUS
    learning = hm.learning
    assert learning is not None
    for _ in range(2):
        learning._add(*OFF, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    await _review(hm)
    assert ha.states[HALL_OFF].state == "on"
    assert ha.states[HALL].state == "on"  # nothing left undone
    texts = " ".join(str(n["text"]) for n in await _waiting(hm))
    assert "Hall light off" in texts
    await hm.stop()


async def test_no_question_while_something_cannot_be_taken_over(tmp_path: Path) -> None:
    """Something else steers the lamp too and teaches nothing: taking the first over would
    leave the lamp watched and nobody doing it. The card says why."""
    other = {
        "id": "1005",
        "alias": "Hall light when the door opens",
        "triggers": [{"trigger": "time", "at": "12:00:00"}],
        "actions": [{"action": "light.toggle", "target": {"entity_id": "light.hall"}, "data": {}}],
    }
    hm, _clock, _ha = await start(tmp_path, {HALL: HALL_CONFIG, "automation.other": other})
    await _people(hm)
    await hm._sync_automations()
    _prove(hm, ON)
    await _review(hm)
    assert await _about(hm, "automation:") is None
    row = await _row(hm, *ON)
    assert row["step"] == "observe"
    assert row["steer"]["text"].startswith("De automatiseringen")
    assert "niet overnemen" in row["text"]
    await hm.stop()


async def test_never_leaves_the_automation_on_and_not_yet_asks_again(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path, {HALL: HALL_CONFIG})
    await _people(hm)
    await hm._sync_automations()
    _prove(hm, ON, hits=30)
    await _review(hm)
    assert (await hm.answer_automation(HALL, False, "demo-user-1", later=True))["ok"]
    assert hm.automations.status[HALL] is Status.SEEN
    assert ha.states[HALL].state == "on"
    await _review(hm)
    assert await _about(hm, "automation:") is None
    await clock.advance(PROOF_ASK_AGAIN.total_seconds())
    await _review(hm)
    assert await _about(hm, "automation:") is not None
    assert (await hm.answer_automation(HALL, False, "demo-user-1"))["ok"]
    assert hm.automations.status[HALL] is Status.DECLINED
    assert ha.states[HALL].state == "on"
    await clock.advance(5 * PROOF_ASK_AGAIN.total_seconds())
    await _review(hm)
    assert await _about(hm, "automation:") is None
    await hm.stop()


async def test_what_stops_with_it_is_in_the_question(tmp_path: Path) -> None:
    """HouseMind does not copy a message: the question says so before anyone says yes."""
    more = {
        "id": "1006",
        "alias": "Hall light and a message",
        "triggers": [{"trigger": "state", "entity_id": "binary_sensor.hall_motion", "to": "on"}],
        "actions": [
            {"action": "light.turn_on", "target": {"entity_id": "light.hall"}, "data": {}},
            {"action": "notify.mobile_app_phone_1", "data": {"message": "Hello"}},
        ],
    }
    hm, _clock, ha = await start(tmp_path, {HALL: more})
    await _people(hm)
    await hm._sync_automations()
    _prove(hm, ON)
    await _review(hm)
    card = await _about(hm, "automation:")
    assert card is not None
    assert "Dan stopt ook:" in card["text"]
    assert (await hm.answer_automation(HALL, True, "demo-user-1"))["ok"]
    assert ha.states[HALL].state == "off"
    await hm.stop()


# -- one lamp in a group ---------------------------------------------------------------------


def test_one_lamp_does_not_hold_a_group_at_least_half_does() -> None:
    lamps = ("light.a", "light.b", "light.c", "light.d")
    assert watched({"light.group"}, "light.group", lamps)
    assert not watched({"light.a"}, "light.group", lamps)
    assert watched({"light.a", "light.b"}, "light.group", lamps)
    assert watched({"light.a"}, "light.a")
    assert not watched({"light.b"}, "light.a")


# -- the card --------------------------------------------------------------------------------


async def test_the_bar_says_how_often_it_went_well_not_how_often_yes(tmp_path: Path) -> None:
    """Garage stood on 0% while it was right 491 of 491 times."""
    hm, _clock, _ = await start(tmp_path, {})
    await _people(hm)
    _prove(hm, KITCHEN, hits=12)
    row = await _row(hm, *KITCHEN)
    assert row["bar"]["value"] == 100
    assert row["good"] == 100
    assert row["step"] == "propose"
    assert row["ready"] and row["proven"]
    assert [c["key"] for c in row["counters"]] == ["situations", "days"]
    await hm.stop()


async def test_a_rule_an_automation_holds_says_so_and_is_no_proposing(tmp_path: Path) -> None:
    """Its name and its count: HouseMind asks nothing there, so the card does not call it
    Voorstellen, and it says what is still needed."""
    hm, _clock, _ = await start(tmp_path, {HALL: HALL_CONFIG})
    await _people(hm)
    await hm._sync_automations()
    assert hm.learning is not None
    hm.learning.steps[ON] = Step.PROPOSE
    row = await _row(hm, *ON)
    assert row["watching"] is True
    assert row["step"] == "observe"
    assert row["stepName"] == "Alleen meekijken"
    assert "Hall light at motion" in row["steer"]["text"]
    assert "Nog " in row["text"]
    await hm.stop()


async def test_nothing_judged_says_nothing_for_the_ring(tmp_path: Path) -> None:
    hm, _clock, _ = await start(tmp_path, {})
    await _people(hm)
    assert hm.learning is not None
    hm.learning.steps[KITCHEN] = Step.PROPOSE
    assert (await _row(hm, *KITCHEN))["good"] is None
    await hm.stop()


# -- what you do yourself --------------------------------------------------------------------


async def test_doing_it_by_hand_answers_the_open_question(tmp_path: Path) -> None:
    """Four times someone did exactly what HouseMind asked, and the count stayed still."""
    hm, clock, ha = await start(tmp_path, {})
    await _people(hm)
    assert hm.learning is not None and hm.proposals is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    hm.learning.installed_at = clock.now() - timedelta(days=30)
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    asked = await hm.proposals.open(clock.now())
    assert asked, "the question comes"
    before = hm.learning.own_answers(*KITCHEN)
    await clock.advance(30)
    lamp = asked[0].entity_id
    await ha.simulate(lamp, "on", by=Source.PERSON)
    assert hm.learning.own_answers(*KITCHEN) == before + 1
    assert await hm.proposals.open(clock.now()) == []
    await hm.stop()


async def test_doing_it_by_hand_without_a_question_is_only_a_hit(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path, {})
    await _people(hm)
    assert hm.learning is not None and hm.proposals is not None
    hm.learning.steps[KITCHEN] = Step.PROPOSE
    hm.learning.installed_at = clock.now() - timedelta(days=30)
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    for proposal in await hm.proposals.open(clock.now()):
        await hm.proposals.expire(proposal.id)
    lamp = "light.kitchen"
    await ha.simulate(lamp, "on", by=Source.PERSON)
    assert hm.learning.own_answers(*KITCHEN) == 0
    await hm.stop()


async def test_putting_the_lamp_out_and_walking_away_counts(tmp_path: Path) -> None:
    """The toilet went on 52 times and 'light off' stood on 5: who puts it out and leaves is
    still seen by the sensor for a moment."""
    hm, clock, _ = await start(tmp_path, {})
    await _people(hm)
    learning = hm.learning
    assert learning is not None
    now = clock.now()
    on = State("light.kitchen", "on", {}, now)
    off = State("light.kitchen", "off", {}, now)
    change = StateChange("light.kitchen", on, off, Source.PERSON)
    learning.observe(change, "kitchen", CONTEXT, now, still_there=True)
    assert "kitchen" in learning.off_seen
    assert learning.total("light_off_leave", "kitchen", Result.HIT, now) == 0
    learning.settle_off(lambda area: False, now)
    assert "kitchen" in learning.off_seen  # still there
    learning.settle_off(lambda area: area == "kitchen", now)
    assert learning.total("light_off_leave", "kitchen", Result.HIT, now) > 0
    # Back on before leaving: nothing to count.
    learning.observe(change, "hall", CONTEXT, now, still_there=True)
    learning.observe(StateChange("light.hall", off, on, Source.PERSON), "hall", CONTEXT, now)
    assert "hall" not in learning.off_seen
    await hm.stop()


async def test_from_now_on_on_a_question_needs_no_proof(tmp_path: Path) -> None:
    """Whoever wants it sooner: it is done now and HouseMind does it itself from now on, as a
    step that goes back after two corrections, no house rule."""
    hm, clock, ha = await start(tmp_path, {})
    await _people(hm)
    assert hm.learning is not None and hm.proposals is not None
    hm.learning.steps[KITCHEN] = Step.PROPOSE
    hm.learning.installed_at = clock.now() - timedelta(days=30)
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    asked = await hm.proposals.open(clock.now())
    assert asked
    result = await hm.answer(asked[0].id, Answer.FROM_NOW, "demo-user-1")
    assert result["ok"] and result["executed"]
    assert "suggest_rule" not in result
    assert hm._skill_step(*KITCHEN) is Step.AUTONOMOUS
    assert hm.rules.step_override(*KITCHEN) is None
    for _ in range(2):
        hm.learning._add(*KITCHEN, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    await _review(hm)
    assert hm._skill_step(*KITCHEN) is Step.PROPOSE
    await hm.stop()


# -- the garage and the night light ----------------------------------------------------------


async def test_the_light_in_a_garage_only_where_an_automation_does_it(tmp_path: Path) -> None:
    hm, _clock, _ = await start(tmp_path, {})
    assert hm.skills is not None
    assert hm.skills._not_here("light_on_entry", "any", "garage")
    assert hm.skills._not_here("light_on_entry", "any", "outside")
    hm.skills.with_automation = {("light_on_entry", "any")}
    assert not hm.skills._not_here("light_on_entry", "any", "garage")
    assert hm.skills._not_here("light_on_entry", "any", "outside")
    await hm.stop()


async def test_the_night_light_goes_along_with_the_automation(tmp_path: Path) -> None:
    """The automation put the lamp on at night too. Left on Proposing, no lamp goes on any more
    at night; so it goes along, and the question says so."""
    hm, _clock, _ha = await start(tmp_path, {HALL: HALL_CONFIG})
    await _people(hm)
    await hm._sync_automations()
    hm.area_roles["hall"] = "hall"
    _prove(hm, ON)
    await _review(hm)
    card = await _about(hm, "automation:")
    assert card is not None and "nachtlicht in de Gang" in card["text"]
    assert (await hm.answer_automation(HALL, True, "demo-user-1"))["ok"]
    assert hm._skill_step("night_light", "hall") is Step.AUTONOMOUS
    await hm.stop()


async def test_the_question_is_written_in_each_language(tmp_path: Path) -> None:
    hm, _clock, ha = await start(tmp_path, {HALL: HALL_CONFIG})
    await _people(hm)
    await hm._sync_automations()
    _prove(hm, ON)
    await _review(hm)
    for lang in ("nl", "en", "de", "fr"):
        items = await views.notifications(hm, lang, True, "demo-user-1")
        text = next(i["text"] for i in items if "Hall light at motion" in i["text"])
        assert "{" not in text and "skills." not in text and "notify." not in text
    assert isinstance(ha, FakeHomeAssistant)
    await hm.stop()


async def test_nothing_is_asked_on_a_wall_tablet(tmp_path: Path) -> None:
    """For good is not for a wall tablet: nobody knows who stands there."""
    hm, _clock, _ = await start(tmp_path, {})
    await _people(hm)
    _prove(hm, KITCHEN)
    await _review(hm)
    items = await views.notifications(hm, "nl", False, None)
    card = next(i for i in items if "Keuken" in i["text"])
    assert card["actions"] == []
    assert isinstance(_clock, SimulatedClock)
    await hm.stop()


async def test_one_lamp_of_a_group_under_an_automation_leaves_the_group_free(
    tmp_path: Path,
) -> None:
    """Found in the real house: an automation on one lamp held a whole group, so a whole room
    could only watch, and the card did not say so."""
    hm, ha, clock = await _group_house(tmp_path, _dining)
    assert hm.learning is not None and hm.gate is not None
    await ha.add_automation(
        "automation.one_lamp",
        "One lamp at noon",
        {
            "id": "1007",
            "alias": "One lamp at noon",
            "triggers": [{"trigger": "time", "at": "12:00:00"}],
            "actions": [
                {"action": "light.turn_on", "target": {"entity_id": TABLE[0]}, "data": {}},
            ],
        },
    )
    await hm._sync_automations()
    assert TABLE[0] in hm.gate.state.other_system
    hm.learning.steps[KITCHEN] = Step.PROPOSE
    request = ActionRequest(
        Layer.REFLEX,
        "light.table",
        "light.turn_on",
        {},
        skill=KITCHEN[0],
        area_id=KITCHEN[1],
        created_at=clock.now(),
    )
    assert hm.gate.evaluate(request).outcome is Outcome.PROPOSED
    assert hm.rule_hold(*KITCHEN) == (False, [])
    await hm.stop()


async def test_what_learns_from_answers_says_it_can_only_watch(tmp_path: Path) -> None:
    """It gets no answers while it is only watched, so it never proves itself: the card does not
    promise a question that will not come."""
    hm, _clock, _ = await start(tmp_path, {HALL: HALL_CONFIG})
    await _people(hm)
    await hm._sync_automations()
    assert hm.learning is not None
    hm.learning.answers_only.add(ON[0])
    hm.learning.steps[ON] = Step.PROPOSE
    row = await _row(hm, *ON)
    assert row["watching"] is True
    assert "niet overnemen" in row["text"]
    await hm.stop()
