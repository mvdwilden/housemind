"""Round 6, from the full retest: messages that disappeared for good, and too many
questions. Each test fails without the fix; the finding is in its docstring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.notify import Kind
from housemind.brain.proposals import Proposals
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome
from tests.test_notify import per_user, start

TZ = ZoneInfo("Europe/Amsterdam")
DAY = datetime(2026, 10, 19, 10, 0, tzinfo=TZ).astimezone(UTC)
NIGHT = datetime(2026, 10, 20, 3, 0, tzinfo=TZ).astimezone(UTC)


async def test_safety_messages_always_come_and_ignored_ones_come_back(tmp_path: Path) -> None:
    """After five ignored messages of a kind, or with a full day budget, it never came again –
    also "the front door is open while nobody is home"."""
    hm, clock, _ha = await start(tmp_path, {1: 2}, at=DAY)
    assert hm.router is not None
    local = clock.now().astimezone(TZ)
    for n in range(8):
        await hm.router.raise_(
            Kind.IMPORTANT,
            f"tip:{n}",
            "notify.guests_unconfirmed",
            {},
            hm._people,
            clock.now(),
            local,
            False,
        )
    sent_before = per_user(hm).get("demo-user-1", 0)
    assert sent_before < 8  # ignored and over budget: held back …
    await hm.router.raise_(
        Kind.IMPORTANT,
        "open_when_away:door",
        "notify.guests_unconfirmed",
        {},
        hm._people,
        clock.now(),
        local,
        False,
    )
    assert per_user(hm)["demo-user-1"] == sent_before + 1  # … but safety always comes
    tomorrow = local + timedelta(days=1)
    bundled = await hm.router.bundle(hm._people, tomorrow.replace(hour=19, minute=30))
    assert bundled and "•" in bundled[0].message  # what was held back is in the bundle
    await hm.router.acknowledged_topic("tip:", "demo-user-1")
    assert not any(k[1] == "tip" for k in hm.router._ignored)  # an answer resets it
    await hm.stop()


async def test_no_questions_on_a_phone_at_night(tmp_path: Path) -> None:
    """With everyone away, questions went to phones at 03:11."""
    hm, clock, _ha = await start(tmp_path, {1: 3}, at=NIGHT)
    assert hm.router is not None
    local = clock.now().astimezone(TZ)
    await hm.router.raise_(
        Kind.LEARNING,
        "proposal:9",
        "notify.guests_unconfirmed",
        {},
        hm._people,
        clock.now(),
        local,
        False,
    )
    assert per_user(hm) == {}
    await hm.stop()


def _decision(at: datetime) -> Decision:
    request = ActionRequest(
        Layer.REFLEX,
        "light.hall",
        "light.turn_off",
        {},
        skill="light_off_leave",
        area_id="hall",
        created_at=at,
    )
    return Decision(request, Outcome.PROPOSED, None, None, (), at)


async def test_the_same_question_waits_longer_each_time(tmp_path: Path) -> None:
    """The same question came back every three hours, 20 to 86 a day."""
    hm, _clock, _ha = await start(tmp_path, {1: 3}, at=DAY)
    proposals: Proposals = hm.proposals
    first = await proposals.add(_decision(DAY))
    assert first is not None
    await hm.db.execute("UPDATE proposals SET status = 'expired' WHERE id = ?", (first,))
    assert await proposals.add(_decision(DAY + timedelta(hours=2))) is None
    second = await proposals.add(_decision(DAY + timedelta(hours=4)))
    assert second is not None  # 3 hours after the first time
    await hm.db.execute("UPDATE proposals SET status = 'expired' WHERE id = ?", (second,))
    # Then a day, then a week (E-294, since 20-10-2026; it was 12 hours).
    assert await proposals.add(_decision(DAY + timedelta(hours=17))) is None
    third = await proposals.add(_decision(DAY + timedelta(hours=29)))
    assert third is not None
    await hm.db.execute("UPDATE proposals SET status = 'expired' WHERE id = ?", (third,))
    assert await proposals.add(_decision(DAY + timedelta(days=7))) is None
    assert await proposals.add(_decision(DAY + timedelta(days=9))) is not None
    await hm.stop()
