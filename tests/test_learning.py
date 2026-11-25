from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from housemind.brain.learning import Learning, Result, wilson_lower_bound
from housemind.core.model import Source, State, StateChange
from housemind.knowledge.catalog import Step
from housemind.memory.database import Database
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome

T0 = datetime(2026, 10, 19, 19, 0, tzinfo=UTC)
CTX = "workday|21:00"


def _req(
    entity: str = "light.living",
    action: str = "light.turn_on",
    skill: str = "light_on_entry",
    **data: object,
) -> ActionRequest:
    return ActionRequest(
        Layer.REFLEX,
        entity,
        action,
        dict(data),
        skill=skill,
        area_id="living",
        facts={"context": CTX},
    )


def _decision(request: ActionRequest, outcome: Outcome, at: datetime) -> Decision:
    return Decision(request, outcome, None, None, (), at)


def _change(
    entity: str, old: str, new: str, source: Source = Source.PHYSICAL, **attrs: object
) -> StateChange:
    return StateChange(
        entity, State(entity, old, {}, T0), State(entity, new, dict(attrs), T0), source
    )


async def _learning(tmp_path: Path, start: set[str] | None = None) -> Learning:
    db = Database(tmp_path / "h.db")
    await db.open()
    return Learning(db, installed_at=T0, start_knowledge=start or set())


def test_wilson_prefers_more_evidence() -> None:
    assert wilson_lower_bound(18, 20) > wilson_lower_bound(2, 2)
    assert wilson_lower_bound(0, 0) == 0.0


async def test_hit_miss_and_correction(tmp_path: Path) -> None:
    learning = await _learning(tmp_path)
    req = _req()
    learning.predicted(req, _decision(req, Outcome.OBSERVED, T0), T0)
    assert learning.observe(
        _change("light.living", "off", "on"), "living", CTX, T0 + timedelta(seconds=60)
    ) == [Result.HIT]
    learning.predicted(req, _decision(req, Outcome.OBSERVED, T0), T0)
    assert learning.expire(T0 + timedelta(minutes=10)) == [Result.MISS]
    learning.predicted(req, _decision(req, Outcome.EXECUTED, T0), T0)
    assert learning.observe(
        _change("light.living", "on", "off"), "living", CTX, T0 + timedelta(seconds=30)
    ) == [Result.CORRECTION]
    score, n, _ = learning.score("light_on_entry", "living", T0 + timedelta(minutes=11))
    assert n == 3  # three situations; the correction weighs double in the score
    assert score == pytest.approx(0.25, abs=0.01)
    await learning.db.close()


async def test_housemind_own_changes_are_not_learned(tmp_path: Path) -> None:
    learning = await _learning(tmp_path)
    req = _req()
    learning.predicted(req, _decision(req, Outcome.OBSERVED, T0), T0)
    assert (
        learning.observe(_change("light.living", "off", "on", Source.HOUSEMIND), "living", CTX, T0)
        == []
    )
    await learning.db.close()


async def test_values_are_learned_with_fallback(tmp_path: Path) -> None:
    learning = await _learning(tmp_path)
    assert learning.value("light_on_entry", "living", CTX, "brightness_pct") is None
    for minute in range(3):
        learning.observe(
            _change("light.living", "off", "on", brightness=77),
            "living",
            CTX,
            T0 + timedelta(minutes=minute),
        )
    assert learning.value("light_on_entry", "living", CTX, "brightness_pct") == 30.2
    assert (
        learning.value("light_on_entry", "living", "free|09:00", "brightness_pct") == 30.2
    )  # "*" fallback
    assert learning.usual_lights("living") == {"light.living"}
    await learning.db.close()


async def test_counts_fade_with_half_life(tmp_path: Path) -> None:
    learning = await _learning(tmp_path)
    learning.answered("light_on_entry", "living", CTX, True, T0)
    assert (
        round(
            learning.total("light_on_entry", "living", Result.APPROVED, T0 + timedelta(days=14)), 2
        )
        == 0.5
    )
    await learning.db.close()


async def test_steps_go_up_and_back_down(tmp_path: Path) -> None:
    learning = await _learning(tmp_path)
    now = T0
    for i in range(24):
        now = T0 + timedelta(hours=8 * i)
        req = _req()
        learning.predicted(req, _decision(req, Outcome.OBSERVED, now), now)
        learning.observe(
            _change("light.living", "off", "on"), "living", CTX, now + timedelta(seconds=30)
        )
    changes = await learning.review_steps(now)
    assert [(c.old, c.new, c.reason) for c in changes] == [(Step.OBSERVE, Step.PROPOSE, "learned")]
    for i in range(16):
        learning.answered("light_on_entry", "living", CTX, True, now + timedelta(hours=i))
    now += timedelta(days=1)
    changes = await learning.review_steps(now)
    assert changes and changes[0].new is Step.AUTONOMOUS
    assert learning.step("light_on_entry", "living") is Step.AUTONOMOUS
    for i in range(2):
        req = _req()
        learning.predicted(req, _decision(req, Outcome.EXECUTED, now), now)
        learning.observe(
            _change("light.living", "on", "off"), "living", CTX, now + timedelta(seconds=20 + i)
        )
    changes = await learning.review_steps(now + timedelta(minutes=1))
    assert changes and changes[0].new is Step.PROPOSE and changes[0].reason == "corrected"
    # Survives a restart.
    await learning.save()
    again = Learning(learning.db, installed_at=T0)
    await again.load()
    assert again.step("light_on_entry", "living") is Step.PROPOSE
    assert again.total("light_on_entry", "living", Result.CORRECTION, now) > 0
    await learning.db.close()


async def test_start_knowledge_can_be_proposed_early(tmp_path: Path) -> None:
    learning = await _learning(tmp_path, start={"light_on_entry"})
    for i in range(3):
        at = T0 + timedelta(minutes=40 * i)
        req = _req()
        learning.predicted(req, _decision(req, Outcome.OBSERVED, at), at)
        learning.observe(
            _change("light.living", "off", "on"), "living", CTX, at + timedelta(seconds=10)
        )
    # Start knowledge may be proposed two hours after installation, at the first situation.
    assert learning.step_at("light_on_entry", "living", T0 + timedelta(hours=1)) is Step.OBSERVE
    assert learning.step_at("light_on_entry", "living", T0 + timedelta(hours=3)) is Step.PROPOSE
    assert learning.step_at("light_off_leave", "living", T0 + timedelta(hours=3)) is Step.OBSERVE
    # ... unless people already often did otherwise there.
    doubt = await _learning(tmp_path / "doubt", start={"light_on_entry"})
    for i in range(3):
        at = T0 + timedelta(minutes=40 * i)
        req = _req()
        doubt.predicted(req, _decision(req, Outcome.OBSERVED, at), at)
        doubt.expire(at + timedelta(minutes=10))  # nobody wanted the light
    assert doubt.step_at("light_on_entry", "living", T0 + timedelta(hours=3)) is Step.OBSERVE
    await learning.db.close()
    await doubt.db.close()


async def test_waking_up_and_switching_off_is_no_correction(tmp_path: Path) -> None:
    """Found in four weeks of the demo: getting up and switching off the wake light counted
    as "not like that" every Saturday. After the wake time it is the natural end."""
    learning = await _learning(tmp_path)
    wake_at = T0 + timedelta(minutes=2)
    req = ActionRequest(
        Layer.PLANNER,
        "light.bedroom",
        "light.turn_on",
        {"brightness_pct": 60},
        skill="wake_light",
        area_id="bedroom",
        facts={"context": CTX, "until": wake_at.isoformat()},
    )
    learning.predicted(req, _decision(req, Outcome.EXECUTED, T0), T0)
    after = learning.observe(
        _change("light.bedroom", "on", "off"), "bedroom", CTX, T0 + timedelta(minutes=4)
    )
    assert after == [] and not learning.corrections
    learning.predicted(req, _decision(req, Outcome.EXECUTED, T0), T0)
    before = learning.observe(
        _change("light.bedroom", "on", "off"), "bedroom", CTX, T0 + timedelta(minutes=1)
    )
    assert before == [Result.CORRECTION]  # off before the wake time: not wanted this early
    await learning.db.close()


async def test_housemind_own_change_coming_back_is_no_correction(tmp_path: Path) -> None:
    """A cover reports "closed" a minute after HouseMind closed it, without a person: that
    is HouseMind's own result, not someone changing it back."""
    learning = await _learning(tmp_path)
    req = _req("cover.living", "cover.close_cover", "covers_close_dark")
    learning.predicted(req, _decision(req, Outcome.EXECUTED, T0), T0)
    assert (
        learning.observe(
            _change("cover.living", "closing", "closed"), "living", CTX, T0 + timedelta(seconds=40)
        )
        == []
    )
    assert not learning.corrections
    await learning.db.close()


# -- QA 2: honest learning (B-175 – B-178) --------------------------------------------------


async def test_own_actions_are_no_proof_of_what_people_do(tmp_path: Path) -> None:
    """QA 2 (H-4, B-175): an action HouseMind did itself that nobody put back counted as a hit
    and a situation – ten of its own actions gave 100% over 10 situations, and "Why?" said
    "you usually do this". Now it is kept apart: no hit, no situation."""
    learning = await _learning(tmp_path)
    at = T0
    for _ in range(10):
        req = _req()
        learning.predicted(req, _decision(req, Outcome.EXECUTED, at), at)
        at += timedelta(hours=1)
        learning.expire(at)
    share, n, _ = learning.score("light_on_entry", "living", at)
    assert (share, n) == (0.0, 0.0)
    assert learning.total("light_on_entry", "living", Result.KEPT, at) > 0
    assert learning.total("light_on_entry", "living", Result.HIT, at) == 0


async def test_a_trial_counts_only_with_someone_there(tmp_path: Path) -> None:
    """QA 2 (H-6, B-177): a trial counted as passed while nobody was home to put it back – the
    curtains closing in an empty house reached Autonomous. Now only with someone there."""
    learning = await _learning(tmp_path)
    req = _req()
    learning.predicted(req, _decision(req, Outcome.EXECUTED, T0), T0, trial=True)
    learning.expire(T0 + timedelta(hours=1))
    assert learning.total("light_on_entry", "living", Result.TRIAL_OK, T0) == 0
    learning.someone_there = lambda area_id: area_id == "living"
    learning.predicted(req, _decision(req, Outcome.EXECUTED, T0), T0, trial=True)
    learning.expire(T0 + timedelta(hours=1))
    assert learning.total("light_on_entry", "living", Result.TRIAL_OK, T0) > 0


async def test_do_it_is_a_yes_and_no_answer_is_no_no(tmp_path: Path) -> None:
    """QA 2 (M-12, B-178): after "Do it" the same moment also counted as "otherwise" (one yes
    gave 50%), and a question nobody answered counted as a miss. Now a yes is only a yes, and
    no answer, or "Not now", is no "no"."""
    learning = await _learning(tmp_path)
    req = _req()
    learning.predicted(req, _decision(req, Outcome.PROPOSED, T0), T0)
    learning.answered("light_on_entry", "living", CTX, True, T0)
    learning.expire(T0 + timedelta(hours=1))
    share, _, _ = learning.score("light_on_entry", "living", T0)
    assert share == 1.0
    other = _req(entity="light.reading")
    learning.predicted(other, _decision(other, Outcome.PROPOSED, T0), T0)
    learning.expire(T0 + timedelta(hours=1))  # nobody answered, nobody did otherwise
    assert learning.total("light_on_entry", "living", Result.MISS, T0) == 0


async def test_start_knowledge_may_ask_soon_but_try_only_with_proof(tmp_path: Path) -> None:
    """QA 2 (H-5, B-176): start knowledge is proposed after two hours (asking is harmless), but
    doing it without asking needs a confidence of at least 0.6 after a week of watching."""
    learning = await _learning(tmp_path, start={"light_on_entry"})
    later = T0 + timedelta(hours=3)
    assert learning.step_at("light_on_entry", "living", later) is Step.PROPOSE
    learning.first_seen[("light_on_entry", "living")] = T0
    for _ in range(3):
        learning._add("light_on_entry", "living", CTX, Result.HIT, later)
    assert not learning.may_try("light_on_entry", "living", later)  # too soon, too little
    week = T0 + timedelta(days=8)
    for _ in range(12):
        learning._add("light_on_entry", "living", CTX, Result.HIT, week)
    assert learning.may_try("light_on_entry", "living", week)


async def test_answers_are_shared_between_rooms(tmp_path: Path) -> None:
    """E-316 (B-182): for a skill that only learns from answers, a yes in another room counts
    half – but acting by itself needs 5 answers in the room itself. Before, each room had to
    earn 15 answers alone: heating reached Autonomous only after eight weeks."""
    learning = await _learning(tmp_path, start={"heat_on_presence", "light_on_entry"})
    learning.answers_only = {"heat_on_presence"}
    for n in range(24):
        learning.answered("heat_on_presence", "living", CTX, True, T0 + timedelta(hours=n))
    for n in range(4):
        learning.answered("heat_on_presence", "kitchen", CTX, True, T0 + timedelta(hours=n))
    learning.answered("light_on_entry", "living", CTX, True, T0)
    later = T0 + timedelta(days=8)
    _, _, answers = learning.approval("heat_on_presence", "kitchen", later)
    assert answers == pytest.approx(4 + 12)
    assert learning.approval("light_on_entry", "kitchen", later)[2] == 0  # not for habits
    for room in ("living", "kitchen"):
        learning.steps[("heat_on_presence", room)] = Step.PROPOSE
    await learning.review_steps(later)
    assert learning.steps[("heat_on_presence", "living")] is Step.AUTONOMOUS
    assert learning.steps[("heat_on_presence", "kitchen")] is Step.PROPOSE  # 4 of its own
    learning.answered("heat_on_presence", "kitchen", CTX, True, later)
    await learning.review_steps(later + timedelta(minutes=1))
    assert learning.steps[("heat_on_presence", "kitchen")] is Step.AUTONOMOUS


async def test_no_in_another_room_does_not_send_this_one_back(tmp_path: Path) -> None:
    """E-316: "mostly no" is about the room itself; other rooms' no counts only half towards
    the share of yes."""
    learning = await _learning(tmp_path, start={"heat_on_presence"})
    learning.answers_only = {"heat_on_presence"}
    for n in range(8):
        learning.answered(
            "heat_on_presence", "living", CTX, False, T0 + timedelta(hours=n), setback=False
        )
    for n in range(2):
        learning.answered("heat_on_presence", "kitchen", CTX, True, T0 + timedelta(hours=n))
    for room in ("living", "kitchen"):
        learning.steps[("heat_on_presence", room)] = Step.PROPOSE
    await learning.review_steps(T0 + timedelta(days=1))
    assert learning.steps[("heat_on_presence", "living")] is Step.OBSERVE  # mostly no here
    assert learning.steps[("heat_on_presence", "kitchen")] is Step.PROPOSE
