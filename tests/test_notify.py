"""Notifications: kinds, budgets per level, one topic one message, signed phone buttons."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.learning import StepChange
from housemind.brain.notify import Kind, Signer
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer, Outcome
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)  # Monday 10:00 local
TZ = ZoneInfo("Europe/Amsterdam")


async def start(
    tmp_path: Path,
    levels: dict[int, int],
    consents: dict[int, dict[str, bool]] | None = None,
    at: datetime = T0,
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at)
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    for i, level in levels.items():
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (
                f"demo-user-{i}",
                f"Resident {i}",
                level,
                json.dumps((consents or {}).get(i, {})),
                clock.now().isoformat(),
            ),
        )
    await hm._refresh_people()
    return hm, clock, house.ha


def per_user(hm: HouseMind) -> dict[str, int]:
    assert hm.router is not None
    counts: dict[str, int] = {}
    for d in hm.router.deliveries:
        counts[d.user_id] = counts.get(d.user_id, 0) + 1
    return counts


async def test_emergency_reaches_everyone_at_night_carefree_unless_they_said_no(
    tmp_path: Path,
) -> None:
    """Since E-291 (20-10-2026) only an explicit no stops an emergency message on Carefree."""
    night = datetime(2026, 10, 19, 1, 0, tzinfo=UTC)  # 03:00 local
    hm, _, ha = await start(tmp_path, {1: 2, 2: 1, 3: 1}, {2: {"emergency_push": False}}, night)
    await ha.simulate("binary_sensor.kitchen_smoke", "on")
    phones = {n[0] for n in ha.notifications}
    assert phones == {"notify.mobile_app_phone_1", "notify.mobile_app_phone_3"}
    _, title, message, data = ha.notifications[0]
    assert "nood" in title and "Rook" in message
    assert data["push"]["interruption-level"] == "critical"
    assert hm.status.orb == "emergency"
    # Solved: the notification disappears (E-122).
    await ha.simulate("binary_sensor.kitchen_smoke", "off")
    assert not [n for n in await hm.notifications() if n["kind"] == "emergency"]
    await hm.stop()


async def test_door_open_while_away_reports_once_and_disappears(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path, {1: 2, 2: 3, 3: 1})
    for i in (1, 2, 3):
        await ha.simulate(f"person.resident_{i}", "not_home")
    await clock.advance(11 * 60)
    await hm.tick()
    assert hm.world is not None and hm.world.modes.mode is HouseMode.AWAY
    await ha.simulate("binary_sensor.front_door", "on")
    await hm.tick()
    await hm.tick()
    # Informed and Involved get it once; Carefree is not disturbed (E-17). Leaving also asks
    # once whether the alarm may go on (E-188): that is a message of its own.
    assert hm.router is not None
    door = [d.user_id for d in hm.router.deliveries if "staat open" in d.message]
    assert sorted(door) == ["demo-user-1", "demo-user-2"]
    # HouseMind only reports: it never locks or closes anything for security in R1.
    assert not [c for c in ha.calls if c[0].startswith(("lock.", "alarm_control_panel."))]
    await ha.simulate("binary_sensor.front_door", "off")
    await hm.tick()
    assert not [n for n in await hm.notifications() if n["topic"].startswith("open_when_away")]
    await hm.stop()


async def test_proposal_answered_with_a_signed_phone_button(tmp_path: Path) -> None:
    hm, _, ha = await start(tmp_path, {1: 3})
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "living")] = Step.PROPOSE
    decision = await hm.consider(
        ActionRequest(
            Layer.REFLEX,
            "light.living_ceiling",
            "light.turn_on",
            reason="entry",
            skill="light_on_entry",
            area_id="living",
        )
    )
    assert decision is not None and decision.outcome is Outcome.PROPOSED
    service, _, message, data = ha.notifications[-1]
    assert service == "notify.mobile_app_phone_1"
    assert message.startswith("Zal ik")
    do_button = data["actions"][0]["action"]
    assert data["actions"][0]["title"] == "Doe het"
    # A forged button does nothing (E-86).
    await ha.press_notification_action("HM|1|do|0000000000000000")
    await ha.press_notification_action(do_button.replace("|do|", "|always|"))
    assert ha.states["light.living_ceiling"].state == "off"
    # The real button does.
    await ha.press_notification_action(do_button)
    assert ha.states["light.living_ceiling"].state == "on"
    assert await hm.open_proposals() == []
    assert not [n for n in await hm.notifications() if n["topic"].startswith("proposal:")]
    await hm.stop()


async def test_daily_budget_per_level(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {1: 1, 2: 2, 3: 3})
    assert hm.router is not None
    local = clock.now().astimezone(TZ)
    for i in range(8):
        await hm.router.raise_(
            Kind.IMPORTANT,
            f"topic{i}",
            "notify.guests_unconfirmed",
            {},
            hm._people,
            clock.now(),
            local,
            False,
        )
    assert per_user(hm) == {"demo-user-2": 3, "demo-user-3": 6}
    await hm.stop()


async def test_informed_slider_changes_the_budget(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {1: 2, 2: 2})
    assert hm.router is not None
    hm._people[0].preferences = {"amount": "less"}
    hm._people[1].preferences = {"amount": "more"}
    local = clock.now().astimezone(TZ)
    for i in range(8):
        await hm.router.raise_(
            Kind.IMPORTANT,
            f"topic{i}",
            "notify.guests_unconfirmed",
            {},
            hm._people,
            clock.now(),
            local,
            False,
        )
    assert per_user(hm) == {"demo-user-1": 2, "demo-user-2": 5}
    await hm.stop()


async def test_ignored_kind_goes_to_the_bundle(tmp_path: Path) -> None:
    """Ignored is three hours without a reaction (E-127, since 20-10-2026)."""
    hm, clock, _ = await start(tmp_path, {1: 4})
    assert hm.router is not None
    for i in range(8):
        at = clock.now() + timedelta(hours=3, minutes=1) * (1 if i >= 5 else 0)
        await hm.router.raise_(
            Kind.IMPORTANT,
            f"door:{i}",
            "notify.guests_unconfirmed",
            {},
            hm._people,
            at,
            at.astimezone(TZ),
            False,
        )
    assert per_user(hm) == {"demo-user-1": 5}
    await hm.stop()


async def test_informed_gets_at_most_one_question_a_day(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {1: 2, 2: 3})
    assert hm.router is not None
    local = clock.now().astimezone(TZ)
    for i in range(3):
        await hm.router.raise_(
            Kind.LEARNING,
            f"proposal:{i}",
            "notify.proposal",
            {"question": "?"},
            hm._people,
            clock.now(),
            local,
            False,
        )
    assert per_user(hm) == {"demo-user-1": 1, "demo-user-2": 3}
    await hm.stop()


async def test_quiet_while_sleeping_except_emergencies(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {1: 3})
    assert hm.router is not None
    local = clock.now().astimezone(TZ)
    await hm.router.raise_(
        Kind.IMPORTANT, "a", "notify.guests_unconfirmed", {}, hm._people, clock.now(), local, True
    )
    assert per_user(hm) == {}
    await hm.router.raise_(
        Kind.EMERGENCY,
        "b",
        "notify.emergency_leak",
        {"area": "x"},
        hm._people,
        clock.now(),
        local,
        True,
    )
    # An emergency also reaches who has a phone but no profile yet (found in a retest: after a
    # fresh install nobody got a push at smoke).
    assert per_user(hm) == {"demo-user-1": 1, "demo-user-2": 1, "demo-user-3": 1}
    await hm.stop()


async def test_one_topic_is_one_notification(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {1: 3})
    assert hm.router is not None
    local = clock.now().astimezone(TZ)
    args = (Kind.IMPORTANT, "door:x", "notify.guests_unconfirmed", {})
    assert await hm.router.raise_(*args, hm._people, clock.now(), local, False) is not None
    assert await hm.router.raise_(*args, hm._people, clock.now(), local, False) is None
    assert per_user(hm) == {"demo-user-1": 1}
    assert await hm.router.resolve("door:x", clock.now())
    await hm.router.raise_(*args, hm._people, clock.now(), local, False)
    assert per_user(hm) == {"demo-user-1": 2}
    await hm.stop()


async def test_unanswered_important_escalates_to_admins(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {1: 2, 2: 2})
    assert hm.router is not None
    admin, other = hm._people
    assert admin.is_admin and not other.is_admin
    admin.home = False
    local = clock.now().astimezone(TZ)
    await hm.router.raise_(
        Kind.IMPORTANT, "x", "notify.guests_unconfirmed", {}, hm._people, clock.now(), local, False
    )
    assert per_user(hm) == {"demo-user-2": 1}
    later = clock.now() + timedelta(minutes=10)
    assert await hm.router.escalate(hm._people, later, local, False) == 0
    later = clock.now() + timedelta(minutes=16)
    assert await hm.router.escalate(hm._people, later, local, False) == 1
    assert per_user(hm) == {"demo-user-1": 1, "demo-user-2": 1}
    assert await hm.router.escalate(hm._people, later, local, False) == 0
    await hm.stop()


async def test_info_is_bundled_at_seven_in_the_evening(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {1: 2, 2: 1})
    assert hm.router is not None
    local = clock.now().astimezone(TZ)
    for i in range(2):
        await hm.router.raise_(
            Kind.INFO,
            f"learned:{i}",
            "notify.learned",
            {"what": f"thing {i}"},
            hm._people,
            clock.now(),
            local,
            False,
        )
    assert per_user(hm) == {}
    assert await hm.router.bundle(hm._people, local.replace(hour=18, minute=59)) == []
    sent = await hm.router.bundle(hm._people, local.replace(hour=19, minute=0))
    assert [d.user_id for d in sent] == ["demo-user-1"]
    assert sent[0].message.count("•") == 2
    assert await hm.router.bundle(hm._people, local.replace(hour=19, minute=30)) == []
    await hm.stop()


async def test_learning_something_new_becomes_info(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path, {1: 2})
    assert hm.world is not None
    change = StepChange("light_on_entry", "living", Step.OBSERVE, Step.PROPOSE, "start")
    await hm._learned(change, hm.world.snapshot(clock.now()))
    open_items = await hm.notifications()
    assert [n["kind"] for n in open_items] == ["info"]
    await hm.stop()


def test_signer_rejects_forged_and_foreign_buttons() -> None:
    signer = Signer("secret")
    signed = signer.sign("12|do")
    assert signer.verify(signed) == "12|do"
    assert Signer("other").verify(signed) is None
    assert signer.verify(signed.replace("12", "13", 1)) is None
    assert signer.verify("HM|12|do") is None
    assert signer.verify("garbage") is None


async def test_information_leaves_the_list_after_a_week_but_important_stays(
    tmp_path: Path,
) -> None:
    """Found in a year in the time machine: weekly tips piled up in the list."""
    hm, clock, _ = await start(tmp_path, {1: 3})
    assert hm.world is not None
    snapshot = hm.world.snapshot(clock.now())
    await hm._raise(Kind.INFO, "tip:1", "notify.base_load", {"watt": 80}, snapshot)
    await hm._raise(
        Kind.IMPORTANT,
        "test:important",
        "notify.ha_too_old",
        {"version": "2025.1", "oldest": "2026.4"},
        snapshot,
    )
    await clock.advance(timedelta(days=8).total_seconds())
    await hm.tick()
    open_topics = {n["topic"] for n in await hm.notifications()}
    assert "tip:1" not in open_topics and "test:important" in open_topics
    assert "tip:1" not in hm._raised  # if it still holds, it may be said again
    await hm.stop()


async def test_a_message_for_one_chosen_person_is_never_passed_on(tmp_path: Path) -> None:
    """Care (E-21): the message is for the chosen contact only. Found by the manual: after a
    quarter of an hour without a reaction it still went to every administrator."""
    hm, clock, _ = await start(tmp_path, {1: 2, 2: 2})
    assert hm.router is not None
    local = clock.now().astimezone(TZ)
    await hm.router.raise_(
        Kind.IMPORTANT,
        "care:morning:2026-10-19",
        "notify.care.morning",
        {"name": "Resident 3"},
        hm._people,
        clock.now(),
        local,
        False,
        concerns="user:demo-user-2",
    )
    assert per_user(hm) == {"demo-user-2": 1}
    later = clock.now() + timedelta(minutes=30)
    assert await hm.router.escalate(hm._people, later, local, False) == 0
    assert per_user(hm) == {"demo-user-2": 1}
    await hm.stop()
