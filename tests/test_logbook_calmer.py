"""A calmer Logboek (QA 3, schermen 9 – 13, 19, 20, 24, 27) – from the side of the person who
reads it: one line per decision, the AI's questions on Expert only and in one place, and no
Later on a message that is only information. Each test fails without the change; what it
checks is its docstring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from housemind.brain.notify import Kind
from housemind.brain.proposals import Answer
from housemind.server import views
from housemind.server.logbook import logbook
from tests.test_proposals_calm import start, ticks


def _viewer(level: int) -> views.Viewer:
    return views.Viewer("demo-user-1", "Resident 1", level, True, {"language": "nl"})


async def test_a_question_its_answer_and_what_came_of_it_are_one_line(tmp_path: Path) -> None:
    """HouseMind asks to put out the lamps of the living room; the resident says yes and it
    is done. The Logboek has one line for it – the question, "Jij zei ja om …" and done – not
    the question, a handled message and the action apart."""
    hm, clock, ha = await start(tmp_path)
    await ha.simulate("binary_sensor.living_presence", "on")
    await ha.simulate("light.living_ceiling", "on")
    await ha.simulate("binary_sensor.living_presence", "off")
    await ticks(hm, clock, 25)
    question = next(p for p in await hm.open_proposals() if p.entity_id == "light.living_ceiling")
    await clock.advance(60)
    await hm.answer(question.id, Answer.DO, "demo-user-1")
    log = await logbook(hm, _viewer(3), "demo-user-1", "nl")
    about: list[dict[str, Any]] = [
        i for i in log["items"] if "living_ceiling" in str(i) or "Woonkamer" in str(i.get("area"))
    ]
    lines = [i for i in about if i["type"] == "done" and i.get("answered")]
    assert len(lines) == 1 and lines[0]["answered"].startswith("Jij zei ja om")
    assert lines[0]["outcome"] == "executed"
    assert not any(
        i["type"] == "notice" and str(i.get("topic", "")).startswith("proposal:") for i in about
    )
    await hm.stop()


async def test_a_message_for_information_has_no_later(tmp_path: Path) -> None:
    """A message that is only information can be closed; putting it off helps nobody. An
    important one still has Later."""
    hm, _clock, _ha = await start(tmp_path)
    assert hm.world is not None
    snapshot = hm.world.look(hm.clock.now())
    info = await hm._raise(Kind.INFO, "plain:a", "notify.paused", {}, snapshot)
    important = await hm._raise(Kind.IMPORTANT, "plain:b", "notify.paused", {}, snapshot)
    check_up = await hm._raise(Kind.INFO, "tidy:summary", "notify.paused", {}, snapshot)
    notices = {n["id"]: n for n in await views.notifications(hm, "nl", True, "demo-user-1")}
    assert notices[info]["actions"] == ["dismiss"]
    assert "snooze" in notices[important]["actions"]
    assert notices[check_up]["actions"] == ["view", "dismiss"]  # Check-up's too
    await hm.stop()


async def test_what_went_to_an_ai_is_in_the_logboek_on_expert_only(tmp_path: Path) -> None:
    """The questions to an AI are lines in the Logboek on Expert, and nowhere else: not on
    Betrokken, and not in the AI settings."""
    hm, clock, _ha = await start(tmp_path)
    await hm.db.execute(
        "INSERT INTO ai_log(at, purpose, provider, model, instruction, sent, received, "
        "input_tokens, output_tokens, cost, ok) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (clock.now().isoformat(), "talk", "x", "m", "", "vraag", "antwoord", 1, 1, 0.0, 1),
    )
    three = await logbook(hm, _viewer(3), "demo-user-1", "nl")
    four = await logbook(hm, _viewer(4), "demo-user-1", "nl")
    assert not any(i["type"] == "ai" for i in three["items"])
    assert any(i["type"] == "ai" for i in four["items"])
    await hm.stop()
