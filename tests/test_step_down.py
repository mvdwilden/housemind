"""Back from Autonomous (E-255, B-99; bijgesteld 01-11-2026), from the user's side: when a part,
a row or learning goes back from Autonomous, the automations HouseMind took over go back on at
once – otherwise nobody does their work – and the administrators hear it. The wish of the
administrator: "zodra er iets op autonoom wordt gezet, dan dat het desbetreffende automatisering
uitgezet wordt, en deze weer aangezet wordt zodra het terug gezet wordt naar Voorstellen of naar
Observeren". Each test fails without it; what it checks is its docstring."""

from __future__ import annotations

from pathlib import Path

from housemind.brain.automations import Status
from housemind.brain.learning import Result
from housemind.knowledge.catalog import Step
from housemind.server import views
from tests.test_automations import HALL, start

PAIR = ("light_on_entry", "hall")
GIVEN_BACK = (
    "De automatisering ‘Hall light at motion’ staat weer aan. Dit doe ik niet meer zelf; zo "
    "blijft er niets liggen."
)


async def _taken_over(tmp_path: Path) -> tuple:
    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None and hm.gate is not None
    hm.activated, hm.gate.state.inactive = True, False  # past the introduction (E-275)
    hm.learning.steps[PAIR] = Step.AUTONOMOUS  # its own room (20.12)
    assert (await hm.turn_off_automation(HALL, "demo-user-1"))["ok"]
    assert ha.states[HALL].state == "off"
    return hm, clock, ha


async def _texts(hm, admin: bool = True) -> list[str]:  # type: ignore[no-untyped-def]
    return [str(n["text"]) for n in await views.notifications(hm, "nl", admin=admin)]


async def test_a_lower_limit_switches_them_back_on_at_once_and_admins_hear_it(
    tmp_path: Path,
) -> None:
    """Found in the real house: a lower step only stopped new questions; then HouseMind asked
    whether to switch them back on, and until someone answered nobody did the work. Now they
    go back on at once – as "seen", so HouseMind may take them over again later – and the
    administrators read why; the Logboek says it too. Nothing to answer."""
    hm, _clock, ha = await _taken_over(tmp_path)
    await hm.set_house_setting("admin_max_step", {"light": "propose"})
    assert ha.states[HALL].state == "on"
    assert hm.automations.status[HALL] is Status.SEEN  # not a "no" to this automation
    listed = await views.notifications(hm, "nl", admin=True)
    assert [n["text"] for n in listed if n["text"] == GIVEN_BACK]
    assert not [n for n in listed if "yes" in n["actions"]]  # nothing to answer
    assert GIVEN_BACK not in await _texts(hm, admin=False)
    back = next(
        r.decision
        for r in hm.recent.values()
        if r.decision.request.entity_id == HALL and r.decision.request.action.endswith("_on")
    )
    assert "ik dit niet meer zelf doe" in " ".join(hm.explain(back, 3, "nl"))
    await hm.stop()


async def test_back_to_autonomous_takes_it_over_again(tmp_path: Path) -> None:
    """The limit back to Autonomous: that is the administrator's yes, and the automation goes
    off again, since HouseMind does all of it. A limit on a part with nothing taken over
    changes nothing and says nothing."""
    hm, _clock, ha = await _taken_over(tmp_path)
    await hm.set_house_setting("admin_max_step", {"light": "observe"})
    assert ha.states[HALL].state == "on"
    await hm.set_house_setting("admin_max_step", {})
    assert ha.states[HALL].state == "off"
    assert hm.automations.status[HALL] is Status.TURNED_OFF
    before = await _texts(hm)
    await hm.set_house_setting("admin_max_step", {"cover": "propose"})  # nothing taken over
    assert ha.states[HALL].state == "off" and await _texts(hm) == before
    await hm.stop()


async def test_a_step_back_by_learning_switches_it_back_on(tmp_path: Path) -> None:
    """Two corrections in a week: HouseMind no longer does it itself (20.9). Then the
    automation does it again, at the same review – not only once an administrator answers."""
    hm, clock, ha = await _taken_over(tmp_path)
    assert hm.learning is not None
    for _ in range(2):
        hm.learning._add(*PAIR, "workday|19:30", Result.CORRECTION, clock.now(), 2.0)
        await clock.advance(16 * 60)
        await hm.tick()
    assert hm.learning.steps[PAIR] is Step.PROPOSE
    assert ha.states[HALL].state == "on"
    assert GIVEN_BACK in await _texts(hm)
    await hm.stop()


async def test_a_part_off_switches_it_back_on_and_on_again_takes_it_over(tmp_path: Path) -> None:
    """Licht off in the list of parts (E-400): HouseMind does nothing with light, so the
    automation does it again. On again, straight on Autonomous: off again."""
    hm, _clock, ha = await _taken_over(tmp_path)
    await hm.set_parts({"lighting": "off"}, "demo-user-1", True)
    assert ha.states[HALL].state == "on"
    await hm.set_parts({"lighting": "autonomous"}, "demo-user-1", True)
    assert ha.states[HALL].state == "off"
    await hm.stop()


async def test_kept_off_on_purpose_stays_off(tmp_path: Path) -> None:
    """An administrator who chose 'Laat uit' for an automation (E-317) keeps that choice: a
    lower step does not switch it on again."""
    hm, _clock, ha = await _taken_over(tmp_path)
    assert (await hm.keep_automation_off(HALL, "demo-user-1"))["ok"]
    await hm.set_house_setting("admin_max_step", {"light": "propose"})
    assert ha.states[HALL].state == "off"
    await hm.stop()
