"""Part E of "Duidelijk en volledig" (20.15, 20.16): what Vesta does herself and who is
speaking, vacation and guests, and looking lived-in. Each test fails without the build."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from aiohttp.test_utils import TestClient, TestServer

from housemind.brain.proposals import Answer
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome
from housemind.server import views
from housemind.server.web import create_app
from housemind.world.house_mode import HouseMode

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
NOON = datetime(2026, 10, 19, 12, 0, tzinfo=TZ).astimezone(UTC)


async def _start(
    tmp_path: Path, clock: SimulatedClock | None = None, away: bool = False
) -> tuple[HouseMind, Any]:
    """``away``: a restart while everyone is still away, as in a real house."""
    clock = clock or SimulatedClock(NOON)
    house = load_demo_house("family_home", clock, "nl")
    for i in (1, 2, 3) if away else ():
        entity_id = f"person.resident_{i}"
        house.ha.states[entity_id] = State(entity_id, "not_home", {}, clock.now())
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    known = await hm.db.fetchone("SELECT 1 FROM people WHERE user_id = 'demo-user-1'")
    if known is None:
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            ("demo-user-1", "Resident 1", 3, json.dumps({}), clock.now().isoformat()),
        )
    await hm._refresh_people()
    return hm, house.ha


async def _ticks(hm: HouseMind, minutes: int, every: int = 15) -> None:
    for _ in range(minutes // every):
        await hm.clock.advance(every * 60)  # type: ignore[attr-defined]
        await hm.tick()


async def _everyone_leaves(ha: Any) -> None:
    for i in (1, 2, 3):
        await ha.simulate(f"person.resident_{i}", "not_home")


# -- vacation and guests (20.16) -----------------------------------------------------------


async def test_a_chosen_vacation_survives_a_restart_and_ends_when_someone_is_home(
    tmp_path: Path,
) -> None:
    hm, ha = await _start(tmp_path)
    assert await hm.set_house_mode("vacation")
    await _everyone_leaves(ha)
    await _ticks(hm, 30)
    assert hm.world is not None and hm.world.modes.mode is HouseMode.VACATION
    clock = hm.clock
    await hm.stop()
    again, ha2 = await _start(tmp_path, clock)  # type: ignore[arg-type]
    assert again.world is not None and again.world.modes.mode is HouseMode.VACATION
    await _everyone_leaves(ha2)  # this test's fresh demo house starts with everyone home
    await _ticks(again, 15)
    assert again.world.modes.mode is HouseMode.VACATION
    await ha2.simulate("person.resident_1", "home")
    await _ticks(again, 15)
    assert again.world.modes.mode is HouseMode.HOME
    assert await again.db.get_setting("house_mode") is None  # nothing left to keep
    await again.stop()


async def test_away_keeps_its_start_over_a_restart(tmp_path: Path) -> None:
    """E-315: the heating goes lower only after 24 hours away; a restart in between does not
    start those 24 hours again."""
    hm, ha = await _start(tmp_path)
    await _everyone_leaves(ha)
    await _ticks(hm, 30)
    assert hm.world is not None and hm.world.modes.mode is HouseMode.AWAY
    since = hm.world.modes.since
    clock = hm.clock
    await hm.stop()
    await clock.advance(20 * 3600)  # type: ignore[attr-defined]
    house = load_demo_house("family_home", clock, "nl")  # type: ignore[arg-type]
    await _everyone_leaves(house.ha)
    again = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set())
    await again.start(autorun=False)
    await _ticks(again, 15)
    assert again.world is not None and again.world.modes.mode is HouseMode.AWAY
    assert again.world.modes.since == since
    assert again.skills is not None
    assert not again.skills._long_away(again.world.snapshot(clock.now()))
    await _ticks(again, 5 * 60, every=60)
    assert again.skills._long_away(again.world.snapshot(clock.now()))
    await house.ha.simulate("person.resident_1", "home")
    await _ticks(again, 15)
    assert again.world.modes.mode is HouseMode.HOME
    assert await again.db.get_setting("house_mode") is None
    await again.stop()


async def test_a_heat_pumps_wish_is_what_a_person_set(tmp_path: Path) -> None:
    """E-318: the wish of a heat pump that heats and cools is what a person set on it – not
    HouseMind's own change coming back later through a cloud integration – and it is kept over
    a restart, like the preset it had before HouseMind chose "away"."""
    hm, ha = await _start(tmp_path)
    clock = hm.clock
    now = clock.now()
    pump = {"preset_modes": ["away", "none"], "preset_mode": "none", "current_temperature": 20.0}
    await ha.simulate("climate.living", "heat_cool", {"temperature": 21.0, **pump},
                      by=Source.PERSON)  # fmt: skip
    assert hm._heat_cool["wish"]["climate.living"] == 21.0
    hm._hc_sent["climate.living"] = ("temperature", 21.5, now)  # HouseMind set 21.5
    await ha.simulate("climate.living", "heat_cool", {"temperature": 21.5, **pump},
                      by=Source.PHYSICAL)  # fmt: skip
    assert hm._heat_cool["wish"]["climate.living"] == 21.0  # its own echo is no wish
    await ha.simulate("climate.living", "heat_cool", {"temperature": 20.0, **pump},
                      by=Source.PHYSICAL)  # fmt: skip
    assert hm._heat_cool["wish"]["climate.living"] == 20.0  # someone at the device
    request = ActionRequest(
        Layer.PLANNER, "climate.living", "climate.set_preset_mode", {"preset_mode": "away"},
        skill="setback_away", area_id="living", facts={"before": "none"},
    )  # fmt: skip
    await hm._heat_cool_done(Decision(request, Outcome.EXECUTED, None, None, (), now))
    assert hm._heat_cool["away"]["climate.living"] == "none"
    await hm.stop()
    again, _ha = await _start(tmp_path, clock)  # type: ignore[arg-type]
    assert again._heat_cool["wish"]["climate.living"] == 20.0
    assert again._heat_cool["away"] == hm._heat_cool["away"]
    await again.stop()


async def test_on_vacation_and_with_guests_nothing_is_learned(tmp_path: Path) -> None:
    hm, ha = await _start(tmp_path)
    assert hm.learning is not None
    assert await hm.set_house_mode("guests")
    counts = dict(hm.learning.counts)
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    await ha.simulate("light.kitchen", "on")
    await _ticks(hm, 30)
    assert hm.learning.counts == counts and not hm.learning.pending
    # With guests the shared devices are as on Carefree: HouseMind tries, it does not ask.
    request = ActionRequest(
        Layer.REFLEX,
        "light.kitchen",
        "light.turn_on",
        {},
        skill="light_on_entry",
        area_id="kitchen",
        created_at=hm.clock.now(),
    )
    assert hm._asks_nobody(request)
    await hm.stop()


async def test_after_a_day_away_one_administrator_is_asked_once(tmp_path: Path) -> None:
    hm, ha = await _start(tmp_path)
    await _everyone_leaves(ha)
    await _ticks(hm, 25 * 60, every=30)
    rows = [r for r in await hm.notifications() if r["topic"] == "vacation_suggestion"]
    assert len(rows) == 1 and rows[0]["concerns"] == "user:demo-user-1"
    items = await views.notifications(hm, "nl", True, "demo-user-1")
    asked = next(i for i in items if str(i["text"]).endswith("Zijn jullie op vakantie?"))
    assert asked["actions"] == ["yes", "no"]
    await hm.answer_vacation(False, "demo-user-1")
    await _ticks(hm, 6 * 60, every=30)
    assert not [
        r
        for r in await hm.notifications()
        if r["topic"] == "vacation_suggestion" and r["resolved_at"] is None
    ]  # no: not asked again while everyone is away
    assert hm.world is not None and hm.world.modes.mode is HouseMode.AWAY
    await hm.stop()


async def test_no_to_the_question_holds_after_a_restart(tmp_path: Path) -> None:
    """'Nee' on 'Zijn jullie op vakantie?' was only in memory: after a restart, while everyone
    was still away, the same question came again (E-383, 37.4). Until someone is home."""
    hm, ha = await _start(tmp_path)
    await _everyone_leaves(ha)
    await _ticks(hm, 25 * 60, every=30)
    await hm.answer_vacation(False, "demo-user-1")
    await _ticks(hm, 30, every=15)
    clock = hm.clock
    await hm.stop()
    again, ha = await _start(tmp_path, clock, away=True)  # type: ignore[arg-type]
    await _ticks(again, 6 * 60, every=30)
    assert not [
        r
        for r in await again.notifications()
        if r["topic"] == "vacation_suggestion" and r["resolved_at"] is None
    ]
    await again.stop()


async def test_yes_to_the_question_turns_vacation_on(tmp_path: Path) -> None:
    hm, ha = await _start(tmp_path)
    await _everyone_leaves(ha)
    await _ticks(hm, 25 * 60, every=30)
    await hm.answer_vacation(True, "demo-user-1")
    assert hm.world is not None and hm.world.modes.mode is HouseMode.VACATION
    await hm.stop()


async def test_looking_lived_in_on_vacation(tmp_path: Path) -> None:
    """Living room lamps from dark to around bedtime, then a bedroom briefly; only lamps
    HouseMind may switch itself, and an administrator can turn it off (E-285)."""
    hm, ha = await _start(tmp_path)
    await _everyone_leaves(ha)
    assert await hm.set_house_mode("vacation")
    seen: dict[str, set[str]] = {}
    done: list[Any] = []
    # Noon to two at night, every 5 minutes: a bedroom lamp is on for 10 to 30 minutes, which
    # a look every 15 minutes can miss. What it did is gathered each round: the thought
    # stream keeps only the last 100 (both made this test fail about once in twenty runs).
    for _ in range(14 * 12):
        await hm.clock.advance(5 * 60)  # type: ignore[attr-defined]
        await hm.tick()
        for lamp in ("light.living_ceiling", "light.living_reading", "light.bedroom"):
            seen.setdefault(lamp, set()).add(ha.states[lamp].state)
        done += [d for d in hm.status.thoughts if d.request.reason == "lived_in" and d not in done]
    assert seen["light.living_ceiling"] == {"off", "on"}
    assert seen["light.bedroom"] == {"off", "on"}
    assert all(ha.states[lamp].state == "off" for lamp in seen)  # all off at night
    assert done and all(d.executed for d in done)
    assert hm.explainer is not None
    line = hm.explain(done[0], 2, "nl")[0]
    assert "bewoond" in line, line
    await hm.stop()


async def test_lived_in_keeps_to_the_limits_and_can_be_turned_off(tmp_path: Path) -> None:
    hm, _ha = await _start(tmp_path)
    await hm.set_house_setting("admin_max_step", {"light": "propose"})
    assert hm._lived_in_lamps() == ([], [])  # lighting held back: no lamps at all
    await hm.set_house_setting("admin_max_step", {})
    await hm.set_house_setting("excluded", ["light.living_reading"])
    living, bedroom = hm._lived_in_lamps()
    assert living == ["light.living_ceiling"] and bedroom == ["light.bedroom"]
    await hm.set_house_setting("lived_in", False)
    assert hm._lived_in_on is False
    assert (await views.house_settings(hm, "nl"))["livedIn"] is False
    await hm.stop()


# -- what Vesta does herself, and who speaks (20.15) ------------------------------------------


async def test_the_house_mode_by_voice(tmp_path: Path) -> None:
    hm, _ = await _start(tmp_path)
    assert hm.world is not None
    said = await hm.talk("We gaan op vakantie", "nl", 3, "demo-user-1")
    assert hm.world.modes.mode is HouseMode.VACATION and said["text"].startswith("Goed")
    await hm.talk("We zijn weer thuis", "nl", 3, "demo-user-1")
    assert hm.world.modes.mode is HouseMode.HOME
    await hm.talk("Er komt oppas", "nl", 3, "demo-user-1")
    assert hm.world.modes.mode is HouseMode.GUESTS
    await hm.talk("we zijn weer thuis", "nl", 3, "demo-user-1")
    await hm.talk("Hoe werkt de vakantiestand?", "nl", 3, "demo-user-1")
    assert hm.world.modes.mode is HouseMode.HOME  # a question about it changes nothing
    await hm.stop()


async def test_one_part_can_be_paused_by_voice(tmp_path: Path) -> None:
    hm, _ = await _start(tmp_path)
    said = await hm.talk("Pauzeer de verlichting", "nl", 3, "demo-user-1")
    assert set(hm.paused_domains()) == {"light"} and hm.paused_until() is None
    assert said["text"].startswith("Verlichting staat een uur op pauze")
    await hm.talk("Ga verder met de verlichting", "nl", 3, "demo-user-1")
    assert hm.paused_domains() == {}
    await hm.stop()


async def test_erasing_is_never_done_on_a_spoken_command(tmp_path: Path) -> None:
    hm, _ = await _start(tmp_path)
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.AUTONOMOUS
    for text in ("Vergeet alles", "Wis alles"):
        said = await hm.talk(text, "nl", 4, "demo-user-1")
        assert "Opnieuw beginnen" in said["text"], said
    assert hm.learning.steps[("light_on_entry", "kitchen")] is Step.AUTONOMOUS
    await hm.stop()


async def _question(hm: HouseMind) -> int:
    assert hm.gate is not None
    request = ActionRequest(
        Layer.REFLEX,
        "light.toilet",
        "light.turn_on",
        {},
        reason="skill:light_on_entry",
        skill="light_on_entry",
        area_id="toilet",
        created_at=hm.clock.now(),
        facts={"context": "workday|12:00"},
    )
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "toilet")] = Step.PROPOSE
    found = await hm.proposals.add(hm.gate.evaluate(request))
    assert found is not None
    return found


async def test_her_question_answered_through_assist(tmp_path: Path) -> None:
    hm, _ = await _start(tmp_path)
    assert hm.learning is not None
    asked = await _question(hm)
    reply = await hm.assist("nee", "nl", None, None)  # a shared speaker: "no" always counts
    row = await hm.db.fetchone("SELECT answer FROM proposals WHERE id = ?", (asked,))
    assert row is not None and row["answer"] == Answer.NOT_NOW.value
    assert reply["text"] and hm.learning.recent_setbacks(
        "light_on_entry", "toilet", hm.clock.now()
    ) == ["rejected"]
    assert (await hm.assist("ja", "nl", None, "demo-user-1"))["text"] == (
        "Er staat nu geen vraag van mij open."
    )
    await hm.stop()


async def test_a_shared_speaker_makes_no_house_rules(tmp_path: Path) -> None:
    hm, _ = await _start(tmp_path)
    reply = await hm.assist("nooit het licht in de gang aan na 23:00", "nl", "c1", None)
    assert reply["text"].startswith("Een huisregel maak je in de app")
    assert reply["continue"] is False and not hm.rules.rules
    await hm.stop()


async def test_a_house_rule_through_home_assistant_needs_someone_from_level_3(
    tmp_path: Path,
) -> None:
    hm, _ = await _start(tmp_path)
    secret = await hm.integration_secret()
    good = {"Authorization": f"Bearer {secret}"}
    rule = {"action": "house_rule", "text": "nooit het licht in de gang aan na 23:00"}
    app = create_app(hm, tmp_path, ingress_only=False)
    async with TestClient(TestServer(app)) as client:
        for who, ok in ((None, False), ("demo-user-1", True)):
            body = {**rule, "confirm": True, "language": "nl", "user_id": who}
            result = await (
                await client.post("/integration/action", json=body, headers=good)
            ).json()
            assert result["ok"] is ok, (who, result)
        await hm.db.execute("UPDATE people SET level = 2 WHERE user_id = 'demo-user-1'")
        body = {**rule, "confirm": True, "language": "nl", "user_id": "demo-user-1"}
        result = await (await client.post("/integration/action", json=body, headers=good)).json()
        assert result["ok"] is False and "Betrokken" in result["message"]
    assert len(hm.rules.rules) == 1
    await hm.stop()


async def test_the_one_tap_buttons_know_the_house_mode(tmp_path: Path) -> None:
    hm, _ = await _start(tmp_path)
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    assert (await views.snapshot(hm, viewer))["houseMode"] == "home"
    await hm.set_house_mode("guests")
    assert (await views.snapshot(hm, viewer))["houseMode"] == "guests"
    await hm.stop()


def test_home_assistant_gets_a_house_mode_choice() -> None:
    integration = ROOT / "integration" / "custom_components" / "housemind"
    source = (integration / "select.py").read_text()
    assert "async_select_option" in source and '"house_mode"' in source
    assert "Platform.SELECT" in (integration / "__init__.py").read_text()
    names = json.loads((integration / "translations" / "nl.json").read_text())
    assert names["entity"]["select"]["house_mode_choice"]["name"] == "Huisstand kiezen"
