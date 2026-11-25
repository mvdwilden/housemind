"""Learning from the history in Home Assistant (E-257, B-101), from the user's side: HouseMind
reads the days Home Assistant kept, once, after the yes to learning, and plays them again –
switching nothing; it learns at half weight, never further than Propose, counts an automation
for half and its own doing not at all, reads again without counting twice, and tells about
it: per automation, at "Waarom?" and in a short message. Each test fails without it; what it
checks is its docstring."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from housemind.brain.learning import HISTORY, Result
from housemind.core.clock import SimulatedClock
from housemind.core.model import Source
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.gate import ActionRequest, Decision, Layer, Outcome
from housemind.server import views
from tests.test_automations import EVENING, ROOT

LAMP = "light.kitchen"
MOTION = "binary_sensor.kitchen_motion"
SLOTS = ((17, 0), (17, 30), (18, 0), (18, 30))  # UTC: 19:00 – 20:30 in Amsterdam, dark


def _evenings(
    ha: Any,
    days: int,
    by: Source = Source.PERSON,
    cause: str | None = None,
    slots: tuple[tuple[int, int], ...] = SLOTS,
    undo_first: bool = False,
) -> None:
    """Someone comes into the dark kitchen and the lamp goes on, each slot of each day."""
    for d in range(1, days + 1):
        day = EVENING - timedelta(days=d)
        for i, (hour, minute) in enumerate(slots):
            at = day.replace(hour=hour, minute=minute, second=0)
            ha.record(MOTION, "on", at)
            ha.record(LAMP, "on", at + timedelta(seconds=20), {"brightness": 180}, by, cause)
            ha.record(MOTION, "off", at + timedelta(minutes=1))
            undone = undo_first and d == 1 and i == 0
            ha.record(LAMP, "off", at + timedelta(minutes=5 if undone else 12), by=Source.PERSON)


async def _house(tmp_path: Path, prepare: Any = None, automations: dict | None = None) -> tuple:
    clock = SimulatedClock(EVENING)
    house = load_demo_house("family_home", clock, "nl")
    for entity_id, config in (automations or {}).items():
        await house.ha.add_automation(entity_id, str(config["alias"]), config)
    if prepare is not None:
        prepare(house.ha)
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    return hm, clock, house.ha


async def _consent(hm: HouseMind) -> None:
    await hm.set_house_setting("consent_learning", True)
    assert hm._history_task is not None
    await hm._history_task


def _hist(hm: HouseMind, outcome: Result, clock: SimulatedClock) -> float:
    assert hm.learning is not None
    return sum(
        c.weight
        for (s, a, b, r), c in hm.learning.counts.items()
        if s == "light_on_entry" and a == "kitchen" and b.startswith(HISTORY) and r == outcome.value
    )


async def test_ten_days_of_history_bring_a_habit_to_propose_and_no_further(tmp_path: Path) -> None:
    """Ten evenings of someone switching the kitchen light on when coming in: after the yes to
    learning HouseMind knows it well enough to propose it, with the days counted – never
    Autonomous, which needs real answers. It switches nothing while reading."""
    hm, clock, ha = await _house(tmp_path, lambda ha: _evenings(ha, 10))
    assert hm.learning is not None
    hm.learning.start_knowledge.discard("light_on_entry")  # a habit it learns by itself
    calls = len(ha.calls)
    await _consent(hm)
    assert len(ha.calls) == calls  # read and played only
    score, situations, days = hm.learning.score("light_on_entry", "kitchen", clock.now())
    assert situations == 20  # 40 evenings at half weight
    assert score == 1.0 and days >= 9.5
    assert hm.learning.knows("light_on_entry", "kitchen", clock.now())
    changes = await hm.learning.review_steps(clock.now())
    assert hm.learning.steps[("light_on_entry", "kitchen")] is Step.PROPOSE
    assert any(c.reason == "learned" for c in changes)
    await clock.advance(8 * 86400)
    await hm.learning.review_steps(clock.now())
    assert hm.learning.steps[("light_on_entry", "kitchen")] is Step.PROPOSE  # no answers
    assert hm.history_status.view()["state"] == "done"
    await hm.stop()


async def test_an_automation_counts_half_and_housemind_itself_not_at_all(tmp_path: Path) -> None:
    """What an automation did teaches at half the weight of a person; what HouseMind did
    itself teaches nothing."""

    def prepare(ha: Any) -> None:
        _evenings(ha, 3, by=Source.PERSON, slots=SLOTS[:1])
        _evenings(ha, 3, by=Source.AUTOMATION, cause="automation.kitchen", slots=SLOTS[1:2])
        _evenings(ha, 3, by=Source.HOUSEMIND, slots=SLOTS[2:3])

    hm, _clock, _ha = await _house(tmp_path, prepare)
    await _consent(hm)
    assert hm.learning is not None
    by_time: dict[str, dict[str, float]] = {}
    for (s, a, b, r), c in hm.learning.counts.items():
        if s == "light_on_entry" and a == "kitchen" and b.startswith(HISTORY):
            slot = by_time.setdefault(b.rsplit("|", 1)[1], {})
            slot[r] = slot.get(r, 0) + c.weight
    person, automation = by_time["19:00"]["hit"], by_time["19:30"]["hit"]
    assert abs(automation - person / 2) < 0.01  # the automation's at half
    assert "20:00" not in by_time  # HouseMind's own: neither right nor "not like that"
    await hm.stop()


async def test_six_days_are_no_week_and_reading_again_counts_nothing_twice(tmp_path: Path) -> None:
    """Fewer than seven days of history do not count as "at least a week"; reading again
    replaces what the first reading taught."""
    hm, clock, _ha = await _house(tmp_path, lambda ha: _evenings(ha, 6))
    await _consent(hm)
    assert hm.learning is not None
    first = (
        _hist(hm, Result.HIT, clock),
        hm.learning.history_situations("light_on_entry", "kitchen"),
    )
    _score, _n, days = hm.learning.score("light_on_entry", "kitchen", clock.now())
    assert days < 1  # not backdated
    assert hm.start_history() and hm._history_task is not None
    await hm._history_task
    again = (
        _hist(hm, Result.HIT, clock),
        hm.learning.history_situations("light_on_entry", "kitchen"),
    )
    assert again == first and first[1] == 12
    await hm.stop()


async def test_per_automation_and_at_why_the_history_speaks(tmp_path: Path) -> None:
    """Per automation: "Deed de afgelopen 10 dagen 40 keer iets; 1 keer zette iemand het terug.
    Ik doe hem nu 100% goed na". At Waarom?: on how many workdays it happened at that time.
    And a short message from Vesta at Meldingen."""
    config = {
        "id": "k",
        "alias": "Keukenlicht",
        "triggers": [{"trigger": "state", "entity_id": MOTION, "to": "on"}],
        "actions": [{"action": "light.turn_on", "target": {"entity_id": LAMP}}],
    }
    hm, clock, _ha = await _house(
        tmp_path,
        lambda ha: _evenings(ha, 10, Source.AUTOMATION, "automation.kitchen", undo_first=True),
        {"automation.kitchen": config},
    )
    await _consent(hm)
    rows = {r["id"]: r for r in (await views.house_settings(hm, "nl"))["automations"]}
    assert rows["automation.kitchen"]["history"] == (
        "Deed de afgelopen 10 dagen 40 keer iets; 1 keer zette iemand het terug. "
        "Ik doe hem nu 100% goed na."
    )
    request = ActionRequest(
        Layer.REFLEX,
        LAMP,
        "light.turn_on",
        {},
        skill="light_on_entry",
        area_id="kitchen",
        facts={"context": "workday|19:00"},
    )
    decision = Decision(request, Outcome.PROPOSED, None, Step.PROPOSE, (), clock.now())
    assert hm.explainer is not None
    said = hm.explainer.evidence("nl", decision, clock.now())
    assert said == "In de historie gebeurde dit hier 6 van de 7 werkdagen rond 19:00."
    listed = await views.notifications(hm, "nl", admin=True)
    told = next(n["text"] for n in listed if "historie van Home Assistant" in n["text"])
    assert told.startswith("Ik heb 10 dagen uit de historie van Home Assistant bekeken.")
    await hm.stop()


async def test_switched_off_it_reads_nothing_and_the_screens_say_so(tmp_path: Path) -> None:
    """With "Leer ook van de historie" off nothing is read; the house settings and the snapshot
    say how it stands."""
    hm, clock, _ha = await _house(tmp_path, lambda ha: _evenings(ha, 10))
    await hm.set_house_setting("history_learning", False)
    await hm.set_house_setting("consent_learning", True)
    if hm._history_task is not None:
        await hm._history_task
    assert _hist(hm, Result.HIT, clock) == 0
    settings = await views.house_settings(hm, "nl")
    assert settings["historyLearning"] is False and settings["history"]["state"] == "off"
    await hm.set_house_setting("history_learning", True)
    assert hm._history_task is not None
    await hm._history_task
    assert _hist(hm, Result.HIT, clock) > 0
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    view = (await views.snapshot(hm, viewer))["history"]
    assert view["state"] == "done" and view["days"] == 10 and view["available"] == 10
    await hm.stop()


async def test_the_message_is_in_the_readers_language_only(tmp_path: Path) -> None:
    """Found in the real house: Home Assistant speaks English, the app Dutch, and the message
    said "Ik heb 4 dagen uit de historie … Light when entering in the badkamer I already know
    well …": two sentences were written in the house's language beforehand. Now each reader
    gets every sentence in their own language."""
    hm, _clock, _ha = await _house(tmp_path, lambda ha: _evenings(ha, 10))
    assert hm.learning is not None
    hm.learning.start_knowledge.discard("light_on_entry")
    await hm.set_house_language("en")  # Home Assistant in English, as in the real house
    await _consent(hm)
    dutch = next(
        n["text"]
        for n in await views.notifications(hm, "nl", admin=True)
        if "historie van Home Assistant" in n["text"]
    )
    assert "ken ik al goed" in dutch
    assert "I already know" not in dutch and "I don't know" not in dutch
    english = next(
        n["text"]
        for n in await views.notifications(hm, "en", admin=True)
        if "Home Assistant's history" in n["text"]
    )
    assert "I already know well" in english and "ken ik" not in english
    await hm.stop()


def _unused(_: datetime) -> None:  # keeps the datetime import for type checkers
    return None
