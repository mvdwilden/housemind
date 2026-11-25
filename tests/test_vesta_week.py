"""The seven states of Vesta, per viewer, and what she says on a tap (E-284, E-132, E-210;
design 20.7). Each test fails without the change; the finding or the requirement is in its
docstring. The last test plays a week in the time machine."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.notify import Kind
from housemind.brain.proposals import Answer
from housemind.brain.voice import Moment
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.demo.timemachine import ScenarioStep, standard_routine
from housemind.ha.fake import FakeHomeAssistant
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Layer
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
EVENING = datetime(2026, 10, 19, 20, 0, tzinfo=TZ).astimezone(UTC)


async def start(
    tmp_path: Path, levels: dict[int, int], at: datetime = EVENING
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(at)
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    for i, level in levels.items():
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (f"demo-user-{i}", f"Resident {i}", level, json.dumps({}), clock.now().isoformat()),
        )
    await hm._refresh_people()
    return hm, clock, house.ha


async def _raise(
    hm: HouseMind, kind: Kind, topic: str, key: str, params: dict[str, str], concerns: str = "home"
) -> None:
    assert hm.world is not None
    await hm._raise(kind, topic, key, params, hm.world.snapshot(hm.clock.now()), concerns)


async def test_a_very_hot_socket_is_red_for_everyone(tmp_path: Path) -> None:
    """E-210, E-284: an emergency message inside makes Vesta red, also when HouseMind switches
    nothing itself – for everyone. Before, it was orange, and only for administrators."""
    hm, _clock, _ha = await start(tmp_path, {1: 2, 2: 2})
    await _raise(
        hm,
        Kind.EMERGENCY,
        "device_hot:sensor.socket",
        "notify.device_very_hot",
        {"name": "Stopcontact", "temperature": "90"},
        concerns="admins",
    )
    assert hm.orb_state(admin=False, level=2, user_id="demo-user-2") == "emergency"
    assert hm.orb_state(admin=True, level=2, user_id="demo-user-1") == "emergency"
    await hm._resolve("device_hot:sensor.socket")
    assert hm.orb_state(admin=False, level=2, user_id="demo-user-2") == "calm"
    await hm.stop()


async def test_a_message_colours_only_who_it_concerns_and_not_when_put_off(
    tmp_path: Path,
) -> None:
    """E-284: an open important message that concerns you and is not put off. Before, a
    message for one person coloured nobody, and a snoozed one kept Vesta orange."""
    hm, _clock, _ha = await start(tmp_path, {1: 3, 2: 3})
    await _raise(hm, Kind.IMPORTANT, "care:x", "notify.learned", {"what": "x"}, "user:demo-user-2")
    assert hm.orb_state(admin=False, level=3, user_id="demo-user-2") == "attention"
    assert hm.orb_state(admin=True, level=3, user_id="demo-user-1") == "calm"
    assert hm.orb_state(admin=False, level=3) == "calm"  # a shared screen: nobody's own
    await _raise(hm, Kind.IMPORTANT, "tip:y", "notify.learned", {"what": "y"})
    assert hm.orb_state(admin=True, level=3, user_id="demo-user-1") == "attention"
    assert hm.router is not None
    row = await hm.db.fetchone("SELECT id FROM notifications WHERE topic = 'tip:y'")
    assert row is not None
    await hm.router.snooze(int(row["id"]), hm.clock.now() + timedelta(hours=1))
    assert hm.orb_state(admin=True, level=3, user_id="demo-user-1") == "calm"
    await hm.stop()


async def test_an_open_message_shows_also_while_the_house_sleeps(tmp_path: Path) -> None:
    """E-284: "let op bij een open melding" comes before sleeping. Before, the house mode
    sleep hid it."""
    hm, _clock, _ha = await start(tmp_path, {1: 3})
    assert hm.world is not None
    await _raise(hm, Kind.IMPORTANT, "tip:z", "notify.learned", {"what": "z"})
    hm.world.modes.mode = HouseMode.SLEEP
    assert hm.orb_state(admin=True, level=3, user_id="demo-user-1") == "attention"
    await hm._resolve("tip:z")
    assert hm.orb_state(admin=True, level=3, user_id="demo-user-1") == "sleeping"
    await hm.stop()


async def test_at_night_she_thinks_only_when_someone_asks(tmp_path: Path) -> None:
    """E-284 (thinking before sleeping) and the retest (no violet flashes at night): while
    the house sleeps she is violet only while she answers someone."""
    hm, clock, ha = await start(tmp_path, {1: 3})
    assert hm.world is not None and hm.learning is not None
    hm.world.modes.mode = HouseMode.SLEEP
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    await hm.consider(
        ActionRequest(
            Layer.REFLEX,
            "light.kitchen",
            "light.turn_on",
            reason="entry",
            skill="light_on_entry",
            area_id="kitchen",
        )
    )
    hm.world.modes.mode = HouseMode.SLEEP
    assert hm.orb_state() == "sleeping"
    await hm.talk("hoe gaat het", "nl", 3, "demo-user-1")
    hm.world.modes.mode = HouseMode.SLEEP
    assert hm.orb_state() == "thinking"
    await clock.advance(5)
    assert hm.orb_state() == "sleeping"
    assert ha is not None
    await hm.stop()


async def test_a_tap_says_what_makes_her_orange_also_the_second_time(tmp_path: Path) -> None:
    """E-132, E-284: what she says on a tap fits her colour. Before, a second tap on an
    orange Vesta asked "what can I do for you?" as if all was calm."""
    hm, _clock, _ha = await start(tmp_path, {1: 3})
    await _raise(
        hm,
        Kind.IMPORTANT,
        "open_when_away:binary_sensor.front_door",
        "notify.open_when_away",
        {"device": "Voordeur"},
    )
    for _ in range(2):
        line = await hm.vesta_line(
            "demo-user-1", 3, True, "nl", Moment.TAP, listen=True, offer=True
        )
        assert any(topic == "open_when_away" for topic, *_ in line.said), line.text
    await hm.stop()


async def test_without_home_assistant_she_says_so(tmp_path: Path) -> None:
    """E-284: no connection with HA is "let op" (from level 2, H7.4), and a tap says why.
    Before, a tap said "all is calm" while she was orange."""
    hm, clock, _ha = await start(tmp_path, {1: 2})
    hm.status.ha_lost = clock.now()
    assert hm.orb_state(admin=True, level=2, user_id="demo-user-1") == "attention"
    assert hm.orb_state(admin=True, level=1, user_id="demo-user-1") == "calm"
    line = await hm.vesta_line("demo-user-1", 2, True, "nl", Moment.TAP, listen=True, offer=True)
    assert any(topic == "ha_lost" for topic, *_ in line.said), line.text
    assert "Home Assistant" in line.text
    await hm.stop()


async def test_a_question_is_amber_for_an_hour_and_she_asks_it_on_a_tap(tmp_path: Path) -> None:
    """E-284: amber while a new question for you is open, at most an hour; not on level 1.
    A tap on level 2 asks it (before, she offered help while amber)."""
    hm, clock, _ha = await start(tmp_path, {1: 2})
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    await hm.consider(
        ActionRequest(
            Layer.REFLEX,
            "light.kitchen",
            "light.turn_on",
            reason="entry",
            skill="light_on_entry",
            area_id="kitchen",
        )
    )
    await clock.advance(10)
    assert hm.orb_state(admin=True, level=2, user_id="demo-user-1") == "learning"
    assert hm.orb_state(admin=True, level=1, user_id="demo-user-1") == "calm"
    line = await hm.vesta_line("demo-user-1", 2, True, "nl", Moment.TAP, listen=True, offer=True)
    assert line.question is not None, line.text
    await hm.stop()


async def test_a_week_in_the_time_machine(tmp_path: Path) -> None:
    """E-284: a week in the time machine – every state comes when it should and goes again.
    Found in the retest: one message kept her orange for days (54–71 % of the time)."""
    monday = datetime(2026, 10, 19, 6, 0, tzinfo=TZ).astimezone(UTC)
    hm, clock, ha = await start(tmp_path, {1: 3}, at=monday)
    hm.learning_allowed = True
    house = load_demo_house("family_home", clock, "nl")
    routine = standard_routine(house, TZ)
    rng = random.Random(7)
    told: list[str] = []

    async def listener() -> None:
        told.append(hm.status.orb)

    hm.on_update(listener)
    samples: list[tuple[str, str]] = []
    events = {
        (1, 21): "hot",  # day 1, 21:00: a very hot socket …
        (1, 22): "cool",  # … that cools down an hour later
        (2, 12): "ha_lost",
        (2, 13): "ha_back",
        (3, 10): "message",
        (3, 11): "solve",
        (4, 14): "pause",
    }
    for day in range(7):
        date = (monday.astimezone(TZ) + timedelta(days=day)).date()
        steps: list[ScenarioStep] = sorted(routine(date, rng))
        end = datetime.combine(date + timedelta(days=1), datetime.min.time(), TZ).astimezone(UTC)
        while clock.now() < end:
            target = clock.now() + timedelta(minutes=5)
            while steps and steps[0].at <= target:
                step = steps.pop(0)
                if step.at > clock.now():
                    await clock.advance((step.at - clock.now()).total_seconds())
                if step.entity_id in ha.states:
                    await ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)
            if target > clock.now():
                await clock.advance((target - clock.now()).total_seconds())
            local = clock.now().astimezone(TZ)
            event = events.get((day, local.hour)) if local.minute < 5 else None
            if event == "hot":
                await _raise(
                    hm,
                    Kind.EMERGENCY,
                    "device_hot:sensor.socket",
                    "notify.device_very_hot",
                    {"name": "Stopcontact", "temperature": "90"},
                    "admins",
                )
            elif event == "cool":
                await hm._resolve("device_hot:sensor.socket")
            elif event == "ha_lost":
                hm.status.ha_lost = clock.now()
            elif event == "ha_back":
                hm.status.ha_lost = None
            elif event == "message":
                await _raise(hm, Kind.IMPORTANT, "tip:week", "notify.learned", {"what": "x"})
            elif event == "solve":
                await hm._resolve("tip:week")
            elif event == "pause":
                await hm.pause(30)
            await hm.tick()
            for proposal in await hm.open_proposals():
                await hm.answer(proposal.id, Answer.DO, "demo-user-1")
            state, reason = hm.vesta_state(
                thinking=False, admin=False, level=3, user_id="demo-user-1"
            )
            samples.append((state, reason))
    seen = {s for s, _ in samples} | set(told)
    for state in ("emergency", "attention", "paused", "thinking", "sleeping", "learning", "calm"):
        assert state in seen, (state, sorted(seen))
    # Each comes and goes again: nothing stays orange or red, a question is amber ≤ 1 hour.
    share = sum(1 for s, _ in samples if s == "attention") / len(samples)
    assert share < 0.05, share
    assert samples[-1][0] in ("calm", "sleeping")
    longest: dict[str, int] = {}
    run, last = 0, ""
    for state, reason in samples:
        run = run + 1 if (state, reason) == ("learning", last) else 1
        last = reason if state == "learning" else ""
        if state == "learning":
            longest[reason] = max(longest.get(reason, 0), run)
    assert all(n <= 13 for n in longest.values()), longest  # 13 × 5 min ≈ an hour
    await hm.stop()
