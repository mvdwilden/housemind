"""Safety never drops away, quiet hours per person, emergencies for everyone with the HA app
(E-289, E-290, E-291, E-20, E-126, E-127; design 20.2 – 20.4). Each test fails without the
change; the finding or the requirement is in its docstring."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.notify import Kind, NotificationRouter, Signer, in_quiet
from housemind.server import views
from tests.test_notify import per_user, start

TZ = ZoneInfo("Europe/Amsterdam")
DAY = datetime(2026, 10, 19, 10, 0, tzinfo=TZ).astimezone(UTC)  # Monday 10:00
LATE = datetime(2026, 10, 19, 23, 30, tzinfo=TZ).astimezone(UTC)  # Monday 23:30


async def _raise(hm, clock, kind, topic, sleeping=False, concerns="home", local=None):  # type: ignore[no-untyped-def]
    assert hm.router is not None
    return await hm.router.raise_(
        kind,
        topic,
        "notify.guests_unconfirmed",
        {},
        hm._people,
        clock.now(),
        local or clock.now().astimezone(TZ),
        sleeping,
        concerns,
    )


async def test_an_emergency_reaches_who_chose_nothing_but_not_who_said_no(
    tmp_path: Path,
) -> None:
    """E-20, E-291: who chose nothing still gets an emergency; only an explicit no stops it.
    Before, Carefree got nothing unless they had said yes."""
    hm, _clock, ha = await start(tmp_path, {1: 1, 2: 1}, {2: {"emergency_push": False}}, at=DAY)
    await ha.simulate("binary_sensor.kitchen_smoke", "on")
    phones = {n[0] for n in ha.notifications}
    assert "notify.mobile_app_phone_1" in phones
    assert "notify.mobile_app_phone_2" not in phones
    await hm.stop()


async def test_every_phone_with_the_app_gets_an_emergency(tmp_path: Path) -> None:
    """E-291: also a phone HouseMind cannot link to a person (a tablet, a child's phone)."""
    hm, _clock, ha = await start(tmp_path, {1: 2}, at=DAY)
    ha.notify_services.add("mobile_app_kitchen_tablet")
    await hm._refresh_people()
    await ha.simulate("binary_sensor.kitchen_smoke", "on")
    assert "notify.mobile_app_kitchen_tablet" in {n[0] for n in ha.notifications}
    await hm.stop()


async def test_an_emergency_comes_before_housemind_is_set_up(tmp_path: Path) -> None:
    """E-277, E-291: before setting up, emergency reactions and their message still work –
    also for a phone that belongs to nobody HouseMind knows yet."""
    hm, _clock, ha = await start(tmp_path, {}, at=DAY)
    hm.activated = False
    ha.notify_services.add("mobile_app_guest_phone")
    await hm._refresh_people()
    await ha.simulate("binary_sensor.kitchen_smoke", "on")
    assert {
        "notify.mobile_app_phone_1",
        "notify.mobile_app_phone_2",
        "notify.mobile_app_guest_phone",
    } <= {n[0] for n in ha.notifications}
    await hm.stop()


async def test_safety_ignores_the_choice_per_kind_and_comes_at_night(tmp_path: Path) -> None:
    """E-289: "the door is open while nobody is home" comes always – also when someone on
    level 3 switched important messages off, and also while the house sleeps."""
    hm, clock, _ha = await start(tmp_path, {1: 3}, at=LATE)
    hm._people[0].preferences = {"kinds": {"important": False, "info": True, "learning": True}}
    await _raise(hm, clock, Kind.IMPORTANT, "open_when_away:door", sleeping=True)
    assert per_user(hm) == {"demo-user-1": 1}
    await _raise(hm, clock, Kind.IMPORTANT, "tip:1", sleeping=True)
    assert per_user(hm) == {"demo-user-1": 1}  # a normal message waits
    await hm.stop()


async def test_carefree_gets_safety_only_after_a_yes(tmp_path: Path) -> None:
    """Choice of the administrator (20.26): on Carefree safety comes only to who said yes to
    messages in danger; who chose nothing gets only emergencies."""
    hm, clock, _ha = await start(tmp_path, {1: 1, 2: 1}, {2: {"emergency_push": True}}, at=DAY)
    await _raise(hm, clock, Kind.IMPORTANT, "open_when_away:door")
    assert per_user(hm) == {"demo-user-2": 1}
    await hm.stop()


async def test_quiet_hours_hold_a_message_until_they_end(tmp_path: Path) -> None:
    """E-290: in your quiet hours only emergencies and safety come; the rest comes when they
    end, if it still matters. Before, "night" was only the house mode sleep."""
    hm, clock, _ha = await start(tmp_path, {1: 3}, at=LATE)
    person = hm._people[0]
    assert in_quiet(person, clock.now().astimezone(TZ))  # 23:00 – 07:00 until learned
    await _raise(hm, clock, Kind.IMPORTANT, "tip:1")
    assert per_user(hm) == {}
    assert hm.router is not None
    morning = clock.now() + timedelta(hours=8)  # 07:30
    sent = await hm.router.deliver_held(hm._people, morning, morning.astimezone(TZ))
    assert sent == 1 and per_user(hm) == {"demo-user-1": 1}
    # Solved in the meantime: it does not come.
    await _raise(hm, clock, Kind.IMPORTANT, "tip:2")
    await hm.router.resolve("tip:2", clock.now())
    assert await hm.router.deliver_held(hm._people, morning, morning.astimezone(TZ)) == 0
    await hm.stop()


async def test_expert_sets_own_quiet_hours(tmp_path: Path) -> None:
    """E-290, E-128: on level 4 the time window for messages is one's own."""
    hm, _clock, _ha = await start(tmp_path, {1: 4, 2: 3}, at=DAY)
    for user_id, prefs in (
        ("demo-user-1", '{"quiet_from": "21:00", "quiet_until": "06:15"}'),
        ("demo-user-2", '{"quiet_from": "21:00", "quiet_until": "06:15"}'),
    ):
        await hm.db.execute("UPDATE people SET preferences = ? WHERE user_id = ?", (prefs, user_id))
    await hm._refresh_people()
    quiet = {p.user_id: p.quiet for p in hm._people}
    assert quiet["demo-user-1"] == (time(21, 0), time(6, 15))
    assert quiet["demo-user-2"] == (time(23, 0), time(7, 0))  # level 3: learned
    await hm.stop()


async def test_a_message_counts_as_ignored_only_after_three_hours(tmp_path: Path) -> None:
    """E-127: ignoring is a few hours without a reaction. Before, every message counted as
    ignored the moment it went out, so after five a kind never came directly again."""
    hm, clock, _ha = await start(tmp_path, {1: 4}, at=DAY)
    assert hm.router is not None
    for n in range(7):
        await _raise(hm, clock, Kind.IMPORTANT, f"door:{n}")
    assert per_user(hm) == {"demo-user-1": 7}  # nothing ignored yet
    await hm.router.acknowledged_topic("door:", "demo-user-1")  # an answer
    later = clock.now() + timedelta(hours=4)
    local = later.astimezone(TZ)
    for n in range(7, 10):
        await hm.router.raise_(
            Kind.IMPORTANT,
            f"door:{n}",
            "notify.guests_unconfirmed",
            {},
            hm._people,
            later,
            local,
            False,
        )
    assert per_user(hm) == {"demo-user-1": 10}  # answered: not ignored
    even_later = later + timedelta(hours=4)
    for n in range(10, 13):
        await hm.router.raise_(
            Kind.IMPORTANT,
            f"door:{n}",
            "notify.guests_unconfirmed",
            {},
            hm._people,
            even_later,
            even_later.astimezone(TZ),
            False,
        )
    # Three more without an answer: not yet five ignored, so they still come.
    assert per_user(hm) == {"demo-user-1": 13}
    await hm.stop()


async def test_the_counters_survive_a_restart(tmp_path: Path) -> None:
    """20.2: a restart reset the day's budget and what was ignored (found in a retest)."""
    hm, clock, _ha = await start(tmp_path, {1: 2}, at=DAY)
    assert hm.router is not None
    for n in range(3):
        await _raise(hm, clock, Kind.IMPORTANT, f"tip:{n}")
    assert per_user(hm) == {"demo-user-1": 3}  # Informed: budget 3
    fresh = NotificationRouter(hm.db, hm.ha, hm.languages, Signer("x"))
    await fresh.load()
    hm.router = fresh
    await _raise(hm, clock, Kind.IMPORTANT, "tip:3")
    assert fresh.deliveries == []  # still over budget after the restart
    await hm.stop()


async def test_the_bundle_comes_once_at_ones_own_time_with_only_ones_own_things(
    tmp_path: Path,
) -> None:
    """B-30, E-126: one bundle a day per person, at their own time, only with what is for
    them. Before, it came every quarter from 19:00 and held messages for admins went to all."""
    hm, clock, _ha = await start(tmp_path, {1: 2, 2: 2}, at=DAY)
    admin, other = hm._people
    assert admin.is_admin and not other.is_admin
    other.preferences = {"bundle_at": "20:30"}
    assert hm.router is not None
    await _raise(hm, clock, Kind.INFO, "learned:1")
    await _raise(hm, clock, Kind.INFO, "selfcheck_info:1", concerns="admins")
    evening = clock.now().astimezone(TZ).replace(hour=19, minute=15)
    sent = await hm.router.bundle(hm._people, evening)
    assert [d.user_id for d in sent] == ["demo-user-1"]
    assert sent[0].message.count("•") == 2
    assert await hm.router.bundle(hm._people, evening.replace(minute=45)) == []
    sent = await hm.router.bundle(hm._people, evening.replace(hour=20, minute=30))
    assert [d.user_id for d in sent] == ["demo-user-2"]
    assert sent[0].message.count("•") == 1  # not the message for administrators
    await hm.stop()


async def test_the_screen_shows_not_chosen_as_not_chosen(tmp_path: Path) -> None:
    """E-20: yes, no, or not chosen yet – the screen and the introduction show which."""
    hm, _clock, _ha = await start(tmp_path, {1: 1}, at=DAY)
    viewer = await views.load_viewer(hm, "demo-user-1", "Resident 1", False)
    assert views.preferences(viewer)["emergencyPush"] is None
    assert views.preferences(viewer)["bundleAt"] == "19:00"
    await hm.stop()
