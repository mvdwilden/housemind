"""One question budget per person, to whom it concerns; new residents and residents without
a phone; the calm screen of level 1 (E-294, E-287, E-252, E-293, E-283; design 20.5 – 20.8).
Each test fails without the change; the finding or the requirement is in its docstring."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.learning import Result
from housemind.brain.notify import Kind
from housemind.brain.proposals import Answer
from housemind.core.model import Entity, State
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind
from housemind.safety.gate import ActionRequest, Layer, Outcome
from housemind.server import views
from housemind.server.logbook import logbook
from housemind.world.house_mode import HouseMode, ModeInputs
from tests.test_notify import per_user, start

TZ = ZoneInfo("Europe/Amsterdam")
DAY = datetime(2026, 10, 19, 15, 0, tzinfo=TZ).astimezone(UTC)  # Monday 15:00


def _kitchen_light() -> ActionRequest:
    return ActionRequest(
        Layer.REFLEX,
        "light.kitchen",
        "light.turn_on",
        reason="entry",
        skill="light_on_entry",
        area_id="kitchen",
    )


async def test_one_question_budget_per_level(tmp_path: Path) -> None:
    """E-294: all questions together, per person per day: level 2 one, level 3 three, level
    4 as chosen (level 1 none: E-17). Before, level 3 got six (the budget of messages)."""
    hm, clock, _ha = await start(tmp_path, {1: 3, 2: 2, 3: 4}, at=DAY)
    assert hm.router is not None
    for person in hm._people:
        if person.level == 4:
            person.preferences = {"questions_per_day": 2}
    local = clock.now().astimezone(TZ)
    for n in range(5):
        await hm.router.raise_(
            Kind.LEARNING, f"proposal:{n}", "notify.proposal", {"question": "?"},
            hm._people, clock.now(), local, False,
        )  # fmt: skip
    assert per_user(hm) == {"demo-user-1": 3, "demo-user-2": 1, "demo-user-3": 2}
    row = await hm.db.fetchone("SELECT delivered FROM notifications WHERE topic = 'proposal:4'")
    assert row is not None and json.loads(row["delivered"])["demo-user-1"] == "wait"
    await hm.stop()


async def test_what_does_not_fit_waits_in_the_logbook(tmp_path: Path) -> None:
    """E-294: a question over the budget waits in Logbook, with its buttons."""
    hm, clock, _ha = await start(tmp_path, {1: 2}, at=DAY)
    assert hm.router is not None
    local = clock.now().astimezone(TZ)
    for n in range(2):
        await hm.router.raise_(
            Kind.LEARNING, f"proposal:{n}", "notify.proposal", {"question": "Licht aan?"},
            hm._people, clock.now(), local, False,
        )  # fmt: skip
    assert per_user(hm) == {"demo-user-1": 1}
    row = await hm.db.fetchone("SELECT delivered FROM notifications WHERE topic = 'proposal:1'")
    assert row is not None and json.loads(row["delivered"]) == {"demo-user-1": "wait"}
    viewer = await views.load_viewer(hm, "demo-user-1", "Resident 1", False)
    log = await logbook(hm, viewer, "demo-user-1", "nl")
    waiting = [i for i in log["items"] if i.get("type") == "notice" and i.get("proposal") == 1]
    assert waiting and waiting[0]["actions"]
    await hm.stop()


async def test_vesta_asks_from_the_same_budget(tmp_path: Path) -> None:
    """E-294, E-252: her questions in the window count in the same budget. Before, she had
    her own count (3 or 5) next to the phone's."""
    hm, _clock, _ha = await start(tmp_path, {1: 2}, at=DAY)
    assert hm.learning is not None and hm.router is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    hm.learning.steps[("light_on_entry", "living")] = Step.PROPOSE
    await hm.consider(_kitchen_light())
    first = await hm.vesta_asks("demo-user-1", 2, True)
    assert [a["kind"] for a in first] == ["proposal"]
    await hm.consider(
        ActionRequest(
            Layer.REFLEX, "light.living_ceiling", "light.turn_on", reason="entry",
            skill="light_on_entry", area_id="living",
        )
    )  # fmt: skip
    asks = await hm.vesta_asks("demo-user-1", 2, True)
    proposals = [a for a in asks if a["kind"] == "proposal"]
    assert len(proposals) == 1 and proposals[0]["proposal"] == first[0]["proposal"]
    await hm.stop()


async def test_never_like_this_does_not_come_back(tmp_path: Path) -> None:
    """E-294: after "Never like this" a question does not come back. Before, it waited a
    week and came again."""
    hm, clock, _ha = await start(tmp_path, {1: 2}, at=DAY)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    await hm.consider(_kitchen_light())
    [proposal] = await hm.open_proposals()
    await hm.answer(proposal.id, Answer.NEVER, "demo-user-1")
    await clock.advance(timedelta(days=30).total_seconds())
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    hm._last_request.clear()
    await hm.consider(_kitchen_light())
    assert await hm.open_proposals() == []
    await hm.stop()


async def test_a_question_does_not_come_back_while_the_house_sleeps(tmp_path: Path) -> None:
    """E-294: a question that comes back never comes back while the house sleeps."""
    hm, clock, _ha = await start(tmp_path, {1: 3}, at=DAY)
    assert hm.learning is not None and hm.world is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    await hm.consider(_kitchen_light())
    [proposal] = await hm.open_proposals()
    await hm.answer(proposal.id, Answer.NOT_NOW, "demo-user-1")
    await clock.advance(timedelta(hours=4).total_seconds())
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    hm.world.modes.mode = HouseMode.SLEEP
    hm._last_request.clear()
    await hm.consider(_kitchen_light())
    assert await hm.open_proposals() == []
    hm.world.modes.mode = HouseMode.HOME
    hm._last_request.clear()
    await hm.consider(_kitchen_light())
    assert len(await hm.open_proposals()) == 1
    await hm.stop()


def _night_light() -> ActionRequest:
    return ActionRequest(
        Layer.REFLEX, "light.kitchen", "light.turn_on", reason="night",
        skill="night_light", area_id="kitchen",
    )  # fmt: skip


async def test_never_for_one_question_leaves_another_on_the_same_device(tmp_path: Path) -> None:
    """E-294, E-315: "Never like this" is about one question. Before, "Never" to "lower at
    night" also stopped "warm in the morning" on the same thermostat, for good."""
    hm, clock, _ha = await start(tmp_path, {1: 3}, at=DAY)
    assert hm.learning is not None
    hm.learning.steps[("night_light", "kitchen")] = Step.PROPOSE
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    await hm.consider(_night_light())
    [proposal] = await hm.open_proposals()
    await hm.answer(proposal.id, Answer.NEVER, "demo-user-1")
    await clock.advance(timedelta(hours=1).total_seconds())
    hm._last_request.clear()
    await hm.consider(_kitchen_light())
    assert [p.skill for p in await hm.open_proposals()] == ["light_on_entry"]
    await hm.stop()


async def test_a_night_question_comes_back_the_next_night(tmp_path: Path) -> None:
    """E-294, E-315: not again in the same night, but a question that only fits the night
    (lower heating while asleep) may come back the next one. Before, one "Not now" or one
    unanswered question at bedtime stopped it for good."""
    hm, clock, _ha = await start(tmp_path, {1: 3}, at=DAY)
    assert hm.learning is not None and hm.world is not None
    hm.learning.steps[("night_light", "kitchen")] = Step.PROPOSE
    hm.world.modes.mode = HouseMode.SLEEP
    await hm.consider(_night_light())
    [proposal] = await hm.open_proposals()
    await hm.answer(proposal.id, Answer.NOT_NOW, "demo-user-1")
    await clock.advance(timedelta(hours=4).total_seconds())
    hm.learning.steps[("night_light", "kitchen")] = Step.PROPOSE
    hm.world.modes.mode = HouseMode.SLEEP
    hm._last_request.clear()
    await hm.consider(_night_light())
    assert await hm.open_proposals() == []  # the same night
    await clock.advance(timedelta(hours=20).total_seconds())
    hm.learning.steps[("night_light", "kitchen")] = Step.PROPOSE
    hm.world.modes.mode = HouseMode.SLEEP
    hm._last_request.clear()
    await hm.consider(_night_light())
    assert len(await hm.open_proposals()) == 1  # the next night
    await hm.stop()


async def test_only_carefree_concerned_gets_a_try_instead(tmp_path: Path) -> None:
    """E-287: only people on Carefree concerned (at home): a try (E-57) instead of a
    question. Before, a try happened only when everyone in the house was Carefree."""
    hm, _clock, ha = await start(tmp_path, {1: 1, 2: 3}, at=DAY)
    assert hm.learning is not None
    await ha.simulate("person.resident_2", "not_home")
    await hm._refresh_people()
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    _proven(hm, "light_on_entry", "kitchen")  # a trial needs proof (QA 2, B-176)
    decision = await hm.consider(_kitchen_light())
    assert decision is not None and decision.outcome is Outcome.EXECUTED
    assert decision.request.layer is Layer.TRIAL
    assert await hm.open_proposals() == []
    await hm.stop()


def _proven(hm: HouseMind, skill: str, area: str) -> None:
    """A week of watching in which people mostly did it so: enough to try it (B-176)."""
    assert hm.learning is not None
    now = hm.clock.now()
    hm.learning.first_seen[(skill, area)] = now - timedelta(days=8)
    for _ in range(12):
        hm.learning._add(skill, area, "", Result.HIT, now)


async def test_without_proof_no_try_on_carefree(tmp_path: Path) -> None:
    """QA 2 (H-5, B-176): start knowledge was tried on Carefree after two hours, from one
    situation and 40%. Doing it without asking now needs a week of watching and a confidence
    of at least 0.6; until then HouseMind only watches there."""
    hm, _clock, ha = await start(tmp_path, {1: 1, 2: 3}, at=DAY)
    assert hm.learning is not None
    await ha.simulate("person.resident_2", "not_home")
    await hm._refresh_people()
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    decision = await hm.consider(_kitchen_light())
    assert decision is not None and decision.outcome is not Outcome.EXECUTED
    assert not hm.learning.may_try("light_on_entry", "kitchen", hm.clock.now())
    await hm.stop()


async def test_a_question_about_a_room_goes_to_who_is_there(tmp_path: Path) -> None:
    """E-287: with a room sensor on the phones (Bluetooth positioning), a question about a
    room goes to who is there, not to everyone at home."""
    hm, _clock, ha = await start(tmp_path, {1: 3, 2: 3}, at=DAY)
    assert hm.learning is not None
    kitchen = hm.model.areas["kitchen"].name
    for i, room in ((1, kitchen), (2, hm.model.areas["living"].name)):
        tracker = Entity(f"device_tracker.phone_{i}", f"t{i}", f"Phone {i}", f"device.phone_{i}")
        area = Entity(f"sensor.phone_{i}_area", f"a{i}", f"Phone {i} area", f"device.phone_{i}")
        now = hm.clock.now()
        await ha.add_entity(tracker, State(tracker.entity_id, "home", {}, now))
        await ha.add_entity(area, State(area.entity_id, room, {}, now))
    await hm._refresh_model()  # HA's registry changed
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    await hm.consider(_kitchen_light())
    assert per_user(hm) == {"demo-user-1": 1}
    await hm.stop()


async def test_a_new_resident_begins_where_the_administrator_chose(tmp_path: Path) -> None:
    """E-287: Carefree unless the administrator chose otherwise when setting up. Before, every
    new person began on Informed."""
    hm, _clock, _ha = await start(tmp_path, {}, at=DAY)
    first = await views.load_viewer(hm, "demo-user-1", "Resident 1", False)
    assert first.level == 1
    await hm.set_house_setting("new_person_level", 3)
    second = await views.load_viewer(hm, "demo-user-2", "Resident 2", False)
    assert second.level == 3
    await hm.stop()


async def test_someone_home_without_a_phone_is_no_unexpected_visitor(tmp_path: Path) -> None:
    """E-293: with residents without a phone, someone inside while every phone is away is
    someone at home; shared screens count them as Carefree."""
    hm, clock, _ha = await start(tmp_path, {1: 3}, at=DAY)
    assert hm.world is not None
    await hm.set_house_setting("phoneless_residents", True)
    modes = hm.world.modes
    local = clock.now().astimezone(TZ)
    for minutes in (0, 5):
        modes.update(
            ModeInputs(local + timedelta(minutes=minutes), 0, frozenset({"living"}), local, False)
        )
    assert modes.mode is HouseMode.HOME and not modes.guests_unconfirmed
    assert 1 in hm._levels_home()
    await hm.stop()


async def test_carefree_sees_only_an_emergency_card(tmp_path: Path) -> None:
    """E-283: one calm screen; only an emergency card can come with it."""
    hm, _clock, ha = await start(tmp_path, {1: 1}, at=DAY)
    assert hm.world is not None
    snap = hm.world.snapshot(hm.clock.now())
    await hm._raise(Kind.IMPORTANT, "tip:a", "notify.learned", {"what": "x"}, snap)
    viewer = await views.load_viewer(hm, "demo-user-1", "Resident 1", False)
    assert (await views.snapshot(hm, viewer)).get("notifications") == []
    await ha.simulate("binary_sensor.kitchen_smoke", "on")
    shown = (await views.snapshot(hm, viewer))["notifications"]
    assert shown and all(n["kind"] == "emergency" for n in shown)
    await hm.stop()


async def test_a_message_for_one_person_is_not_in_someone_elses_lists(tmp_path: Path) -> None:
    """20.5: a message for one person or for administrators is only in their lists. Before,
    everyone from level 2 saw them on Huis and in Logbook."""
    hm, _clock, _ha = await start(tmp_path, {1: 2, 2: 2}, at=DAY)
    assert hm.world is not None
    snap = hm.world.snapshot(hm.clock.now())
    await hm._raise(
        Kind.IMPORTANT, "care:x", "notify.learned", {"what": "x"}, snap, "user:demo-user-1"
    )
    await hm._raise(Kind.IMPORTANT, "tip:admin", "notify.learned", {"what": "y"}, snap, "admins")
    other = await views.load_viewer(hm, "demo-user-2", "Resident 2", False)
    topics = {n["text"] for n in (await views.snapshot(hm, other))["notifications"]}
    assert not topics
    log = await logbook(hm, other, "demo-user-2", "nl")
    assert not [i for i in log["items"] if i.get("type") == "notice"]
    mine = await views.load_viewer(hm, "demo-user-1", "Resident 1", False)
    assert len((await views.snapshot(hm, mine))["notifications"]) == 2
    await hm.stop()
