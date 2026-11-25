"""Part D of "Duidelijk en volledig" (20.9 – 20.13): a step back and earning it again, an
administrator's own step, thresholds per part, forgetting one thing and going back to an
earlier day. Each test fails without the build."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from housemind.brain.learning import Result
from housemind.knowledge.catalog import Step
from housemind.memory import snapshots
from housemind.runtime import HouseMind
from housemind.server import views
from tests.test_automations import start

CONTEXT = "workday|19:30"
KITCHEN = ("light_on_entry", "kitchen")


async def _review(hm: HouseMind) -> None:
    """Fifteen minutes on: HouseMind reviews the steps."""
    await hm.clock.advance(16 * 60)  # type: ignore[attr-defined]
    await hm.tick()


async def _texts(hm: HouseMind, user_id: str | None = None) -> list[str]:
    """The step-back notices as someone reads them."""
    items = await views.notifications(hm, "nl", True, user_id)
    return [str(n["text"]) for n in items if str(n["text"]).startswith("Een stap terug")]


async def _admin(hm: HouseMind, user_id: str = "demo-user-1", level: int = 4) -> None:
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
        (user_id, "Resident", level, json.dumps({}), hm.clock.now().isoformat()),
    )
    await hm._refresh_people()


async def test_two_corrections_in_a_week_take_one_step_back_and_admins_hear_it(
    tmp_path: Path,
) -> None:
    hm, clock, _ = await start(tmp_path)
    assert hm.learning is not None
    await _admin(hm)
    hm.learning.steps[KITCHEN] = Step.AUTONOMOUS
    hm.learning._add(*KITCHEN, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    await _review(hm)
    assert hm.learning.steps[KITCHEN] is Step.AUTONOMOUS  # one is not enough
    await clock.advance(86400)
    hm.learning._add(*KITCHEN, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    await _review(hm)
    assert hm.learning.steps[KITCHEN] is Step.PROPOSE
    texts = await _texts(hm)
    assert texts == [
        "Een stap terug: licht bij binnenkomen in de Keuken – dit doe ik niet meer zelf. Twee "
        "keer zette iemand het deze week terug. Ik stel het weer voor."
    ], texts
    await hm.stop()


async def test_corrections_more_than_a_week_apart_are_no_step_back(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[KITCHEN] = Step.AUTONOMOUS
    hm.learning._add(*KITCHEN, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    await clock.advance(8 * 86400)
    hm.learning._add(*KITCHEN, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    await _review(hm)
    assert hm.learning.steps[KITCHEN] is Step.AUTONOMOUS
    await hm.stop()


async def test_two_no_answers_take_propose_back_to_observe(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path)
    assert hm.learning is not None
    await _admin(hm)
    hm.learning.steps[KITCHEN] = Step.PROPOSE
    for _ in range(10):
        hm.learning.answered(*KITCHEN, CONTEXT, True, clock.now())
    hm.learning.answered(*KITCHEN, CONTEXT, False, clock.now())
    hm.learning.answered(*KITCHEN, CONTEXT, False, clock.now())  # "no" twice
    await _review(hm)
    assert hm.learning.steps[KITCHEN] is Step.OBSERVE
    assert await _texts(hm) == [
        "Een stap terug: licht bij binnenkomen in de Keuken – dit stel ik niet meer voor. Twee "
        "keer zei iemand deze week nee. Ik kijk eerst weer mee."
    ]
    await hm.stop()


async def test_the_step_up_is_earned_again_from_the_step_back(tmp_path: Path) -> None:
    """Twenty yes before the step back do not count; fifteen after it and a week do."""
    hm, clock, _ = await start(tmp_path)
    assert hm.learning is not None
    learning = hm.learning
    learning.first_seen[KITCHEN] = clock.now() - timedelta(days=30)
    learning.steps[KITCHEN] = Step.AUTONOMOUS
    for _ in range(20):
        learning.answered(*KITCHEN, CONTEXT, True, clock.now())
    for _ in range(2):
        learning._add(*KITCHEN, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    await _review(hm)
    assert learning.steps[KITCHEN] is Step.PROPOSE
    await _review(hm)
    assert learning.steps[KITCHEN] is Step.PROPOSE  # it never jumps back by itself
    assert learning.earned(*KITCHEN, clock.now())[1] == 0  # no answers since
    for _ in range(15):
        learning.answered(*KITCHEN, CONTEXT, True, clock.now())
    await clock.advance(6 * 86400)
    await _review(hm)
    assert learning.steps[KITCHEN] is Step.PROPOSE  # not a week yet
    await clock.advance(86400)
    await _review(hm)
    assert learning.steps[KITCHEN] is Step.AUTONOMOUS
    await hm.stop()


async def test_a_step_back_is_kept_over_a_restart(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[KITCHEN] = Step.AUTONOMOUS
    hm.learning._add(*KITCHEN, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    hm.learning._add(*KITCHEN, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    await _review(hm)
    since = hm.learning.earn[KITCHEN].since
    await hm.learning.save()
    hm.learning._reset()
    await hm.learning.load()
    assert hm.learning.earn[KITCHEN].since == since
    assert hm.learning.earn[KITCHEN].back_to is Step.PROPOSE
    await hm.stop()


async def test_undo_counts_as_a_correction(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[KITCHEN] = Step.AUTONOMOUS
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    done = [i for i, r in hm.recent.items() if r.decision.executed]
    assert done and ha.states["light.kitchen"].state == "on"
    assert (await hm.undo(done[-1], "demo-user-1"))["ok"]
    assert hm.learning.recent_setbacks(*KITCHEN, clock.now()) == ["corrected"]
    assert hm.learning.total(*KITCHEN, Result.CORRECTION, clock.now()) > 0
    assert hm.learning.total(*KITCHEN, Result.REJECTED, clock.now()) == 0
    await hm.stop()


async def test_an_administrator_sets_a_step_and_it_goes_back_per_room(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path)
    assert hm.learning is not None
    await _admin(hm)
    assert hm._skill_step(*KITCHEN) is not Step.AUTONOMOUS
    assert await hm.set_admin_step("lighting", "autonomous", "demo-user-1")
    assert hm._skill_step(*KITCHEN) is Step.AUTONOMOUS
    assert hm._skill_step("light_on_entry", "hall") is Step.AUTONOMOUS
    assert not await hm.set_admin_step("nonsense", "autonomous", "demo-user-1")
    # Two corrections in the kitchen: there, and only there, one step back.
    for _ in range(2):
        hm.learning._add(*KITCHEN, CONTEXT, Result.CORRECTION, clock.now(), 2.0)
    await _review(hm)
    assert hm._skill_step(*KITCHEN) is Step.PROPOSE
    assert hm._skill_step("light_on_entry", "hall") is Step.AUTONOMOUS
    texts = await _texts(hm, "demo-user-1")
    assert texts == [
        "Een stap terug: licht bij binnenkomen in de Keuken – je zette dit zelf op Autonoom. "
        "Twee keer zette iemand het deze week terug. Daar ga ik terug naar Voorstellen."
    ], texts
    rows = await hm.notifications()
    row = next(r for r in rows if str(r["topic"]).startswith("stepped_back:"))
    assert row["concerns"] == "user:demo-user-1"  # to who set it
    # A new choice starts afresh; taking it away leaves what was earned.
    assert await hm.set_admin_step("lighting", "autonomous", "demo-user-1")
    assert hm._skill_step(*KITCHEN) is Step.AUTONOMOUS
    assert await hm.set_admin_step("lighting", None, "demo-user-1")
    assert hm._skill_step(*KITCHEN) is not Step.AUTONOMOUS
    await hm.stop()


async def test_the_gate_still_limits_an_administrators_step(tmp_path: Path) -> None:
    """A step set by hand never passes the catalogue or the administrator's own limit."""
    from housemind.brain.readiness import readiness

    hm, _, _ = await start(tmp_path)
    await _admin(hm)
    await hm.set_house_setting("admin_max_step", {"light": "propose"})
    parts = await readiness(hm, "nl", 3, True)
    lighting = next(p for p in parts if p["domain"] == "lighting")
    choices = {c["step"]: c for c in lighting["own"]["choices"]}
    assert choices["propose"]["allowed"] and not choices["autonomous"]["allowed"]
    assert choices["autonomous"]["why"] == "Je grens staat op Voorstellen."
    assert "skills" in lighting and "own" not in lighting["skills"][0]  # per room: Expert
    expert = next(p for p in await readiness(hm, "nl", 4, True) if p["domain"] == "lighting")
    assert expert["skills"][0]["own"]["key"].startswith("light")
    assert expert["skills"][0]["forget"] is True
    assert "own" not in next(
        p for p in await readiness(hm, "nl", 3, False) if p["domain"] == "lighting"
    )
    await hm.stop()


async def test_thresholds_per_part(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path)
    learning = hm.learning
    assert learning is not None
    for _ in range(3):
        learning._add(*KITCHEN, CONTEXT, Result.HIT, clock.now())
    assert not learning.knows(*KITCHEN, clock.now())
    values: dict[str, Any] = {"propose_score": 50, "propose_situations": 2, "propose_days": 0}
    assert await hm.set_thresholds("lighting", values)
    assert learning.knows(*KITCHEN, clock.now())
    assert learning.thresholds("covers_close_dark").propose_situations == 10  # other parts
    for wrong in (
        {"propose_score": 0},
        {"propose_score": 101},
        {"propose_days": -1},
        {"half_life_days": 0.5},
        {"propose_situations": "many"},
    ):
        assert not await hm.set_thresholds("lighting", wrong), wrong
    assert not await hm.set_thresholds("no_such_part", values)
    # Kept for the part (only the settings file shows them now, QA 3).
    assert learning.limits["lighting"].view()["propose_score"] == 50
    assert await hm.set_thresholds("lighting", None)  # "Standaard"
    assert not learning.knows(*KITCHEN, clock.now())
    await hm.stop()


async def test_a_shorter_half_life_forgets_old_habits_sooner(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path)
    learning = hm.learning
    assert learning is not None
    learning._add(*KITCHEN, CONTEXT, Result.HIT, clock.now())
    await clock.advance(7 * 86400)
    standard = learning.total(*KITCHEN, Result.HIT, clock.now())
    assert await hm.set_thresholds("lighting", {"half_life_days": 7})
    assert abs(learning.total(*KITCHEN, Result.HIT, clock.now()) - 0.5) < 0.01
    assert standard > 0.6
    await hm.stop()


async def test_forget_this_forgets_one_skill_in_one_room(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path)
    learning = hm.learning
    assert learning is not None
    learning.steps[KITCHEN] = Step.AUTONOMOUS
    for _ in range(5):
        learning._add(*KITCHEN, CONTEXT, Result.HIT, clock.now())
        learning._add("light_on_entry", "hall", CONTEXT, Result.HIT, clock.now())
    await learning.save()
    assert await hm.forget_pair(*KITCHEN)
    assert learning.steps[KITCHEN] is Step.OBSERVE
    assert learning.total(*KITCHEN, Result.HIT, clock.now()) == 0
    assert learning.total("light_on_entry", "hall", Result.HIT, clock.now()) > 4
    rows = await hm.db.fetchall(
        "SELECT * FROM skill_counts WHERE skill = ? AND area_id = ?", KITCHEN
    )
    assert not rows
    assert not await hm.forget_pair("light_on_entry", "nowhere")
    await hm.stop()


async def test_what_was_learned_is_kept_at_night_and_a_day_can_go_back(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path)
    learning = hm.learning
    assert learning is not None and hm.config is not None
    learning.steps[KITCHEN] = Step.AUTONOMOUS
    for _ in range(3):
        learning._add(*KITCHEN, CONTEXT, Result.HIT, clock.now())
    await learning.save()
    await learning._save_step(*KITCHEN, Step.AUTONOMOUS, clock.now(), "test")
    # The clock starts in the evening: through the night to past 04:00.
    for _ in range(12 * 4):
        await clock.advance(15 * 60)
        await hm.tick()
    days = hm.snapshot_days()
    assert days == ["2026-10-20", "2026-10-19"]  # the evening it started, and 04:00 on
    # Later something else is learned, then the administrator goes back to that day.
    await hm.forget_pair(*KITCHEN)
    assert learning.steps[KITCHEN] is Step.OBSERVE
    assert await hm.restore_snapshot(days[0])
    assert hm.learning is not None and hm.learning.steps[KITCHEN] is Step.AUTONOMOUS
    assert hm.learning.total(*KITCHEN, Result.HIT, clock.now()) > 2
    assert not await hm.restore_snapshot("2020-01-01")
    assert not await hm.restore_snapshot("../../etc/passwd")
    await hm.stop()


async def test_only_the_last_seven_days_are_kept(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path)
    folder = tmp_path / "snapshots"
    for day in range(10):
        await snapshots.take(hm.db, folder, (clock.now() + timedelta(days=day)).date())
    assert len(snapshots.days(folder)) == 7
    assert snapshots.days(folder)[0] == (clock.now() + timedelta(days=9)).date().isoformat()
    await hm.stop()


async def test_what_i_would_do_now_changes_nothing(tmp_path: Path) -> None:
    """Expert's 'Wat ik nu zou doen' (E-295, 20.14): someone comes into the dark kitchen;
    HouseMind says what it would do and what the gate thinks – and switches, keeps and
    learns nothing."""
    from housemind.core.model import Source, State, StateChange

    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None and hm.skills is not None and hm.world is not None
    hm.learning.steps[KITCHEN] = Step.PROPOSE
    before = hm.simulate("nl")
    assert not any(r["area"] == "Keuken" for r in before["rooms"]), before  # nobody there yet
    # The world sees someone come in; HouseMind itself has not thought about it yet.
    old = hm.world.states["binary_sensor.kitchen_motion"]
    new = State(old.entity_id, "on", dict(old.attributes), clock.now())
    hm.world.on_change(StateChange(old.entity_id, old, new, Source.PHYSICAL), clock.now())
    calls, thoughts = len(ha.calls), len(hm.status.thoughts)
    counts, entered = dict(hm.learning.counts), dict(hm.skills._entered)
    first = hm.simulate("nl")
    second = hm.simulate("nl")
    assert first["rooms"] == second["rooms"]  # asking again gives the same: nothing was kept
    kitchen = next(r for r in first["rooms"] if r["area"] == "Keuken")
    line = kitchen["items"][0]
    assert line["text"].startswith("Ik zou ") and ", omdat " in line["text"], line
    assert line["gate"] == "Mag: eerst vragen" and line["outcome"] == "proposed"
    assert len(ha.calls) == calls and len(hm.status.thoughts) == thoughts
    assert hm.learning.counts == counts and hm.skills._entered == entered
    assert ha.states["light.kitchen"].state == "off"
    assert not await hm.open_proposals()
    await hm.stop()


async def test_level_two_sees_what_it_would_have_done(tmp_path: Path) -> None:
    """On Observe, level 2 sees in the Logbook what HouseMind would have done (20.14)."""
    from housemind.server.views import mind_groups

    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[KITCHEN] = Step.OBSERVE
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    groups = mind_groups(hm, 2, 20, clock.now() - timedelta(hours=1))
    assert any(d.outcome.value == "observed" for d, _ in groups)
    await hm.stop()


async def test_not_now_is_no_step_back_but_a_spoken_no_is(tmp_path: Path) -> None:
    """'Nu niet' brings the question back later; it is no step back by itself (found in the
    four-week test: a resident saying yes 95% of the time lost the light). 'Nee' to Vesta and
    'Nooit zo' are."""
    from housemind.brain.proposals import Answer
    from housemind.safety.gate import ActionRequest, Layer

    hm, clock, _ha = await start(tmp_path)
    assert hm.learning is not None
    toilet = ("light_on_entry", "toilet")  # the hall has an automation of its own
    for pair in (KITCHEN, toilet):
        hm.learning.steps[pair] = Step.PROPOSE

    async def asked(area: str) -> int:
        """A question about the light in a room, as HouseMind puts it."""
        assert hm.gate is not None
        request = ActionRequest(
            Layer.REFLEX,
            f"light.{area}",
            "light.turn_on",
            {},
            reason="skill:light_on_entry",
            skill="light_on_entry",
            area_id=area,
            created_at=clock.now(),
            facts={"context": CONTEXT},
        )
        decision = hm.gate.evaluate(request)
        assert decision.outcome.value == "proposed"
        found = await hm.proposals.add(decision)
        assert found is not None
        return found

    await hm.answer(await asked("kitchen"), Answer.NOT_NOW, "demo-user-1")
    await hm.answer(await asked("toilet"), Answer.NOT_NOW, "demo-user-1", said_no=True)
    await clock.advance(4 * 3600)  # "Not now" comes back after three hours
    await hm.answer(await asked("kitchen"), Answer.NOT_NOW, "demo-user-1")
    await hm.answer(await asked("toilet"), Answer.NEVER, "demo-user-1")
    assert hm.learning.recent_setbacks(*KITCHEN, clock.now()) == []
    assert hm.learning.recent_setbacks(*toilet, clock.now()) == ["rejected", "rejected"]
    await hm.stop()
