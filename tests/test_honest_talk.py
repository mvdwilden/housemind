"""QA 2, group 2 – Vesta says what really happened (M-14 to M-17, L7). Each test fails
without its fix; what it checks is its docstring."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from housemind.brain.library import gist
from housemind.brain.readiness import readiness
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from housemind.safety.emergency import Emergency
from housemind.safety.gate import ActionRequest, Layer
from housemind.server import views
from housemind.server.web import say
from tests.test_automations import start as start_automations
from tests.test_readiness import _seen
from tests.test_round1_safety import ROOT, _start, _texts
from tests.test_thumbs import _did_something


async def test_an_emergency_message_counts_the_lamps_that_really_went_on(tmp_path: Path) -> None:
    """ "Het licht is aan" was said for every smoke alarm, also when a lamp refused, and the
    Orb said it during gas, when nothing may switch. Now the message counts the lamps that
    really went on, and the Orb no longer claims the light."""
    hm, ha, _clock = await _start(tmp_path)
    lamps = [e for e, s in ha.states.items() if e.startswith("light.") and s.state == "off"]
    ha.failing_entities.add(lamps[0])
    await ha.simulate("binary_sensor.hall_smoke", "on")
    went_on = [e for e in lamps if ha.states[e].state == "on"]
    assert lamps[0] not in went_on and len(went_on) > 1
    [text] = await _texts(hm, "emergency:binary_sensor.hall_smoke")
    assert f"Ik heb {len(went_on)} lampen aangedaan." in text
    await ha.simulate("binary_sensor.hall_smoke", "off")
    await ha.simulate("binary_sensor.kitchen_gas", "on")
    assert hm.status.emergency is Emergency.GAS
    [gas] = await _texts(hm, "emergency:binary_sensor.kitchen_gas")
    for sentence in (gas, await say(hm, "nl")):
        assert not re.search(r"licht is aan|lampen aangedaan", sentence), sentence
    await hm.stop()


async def test_why_not_in_her_own_words_about_what_was_asked(tmp_path: Path) -> None:
    """ "Waarom staat de verwarming in de kinderkamer niet aan?" got "I see nothing that is
    still on" (M-14). Now she recognises the why-not anywhere in the sentence, answers about
    the kind of device it names (not a lamp in that room), says when the reason was, and says
    "Dat weet ik niet" when she did not try or stop anything there."""
    hm, _ha, clock = await _start(tmp_path)
    room = hm.model.areas["bedroom_child"].name
    question = f"Waarom staat de verwarming in de {room} niet aan?"

    async def answer() -> str:
        return str((await hm.talk(question, "nl", 3, "demo-user-1"))["text"])

    assert (await answer()).startswith("Dat weet ik niet.")
    await hm.pause(60)
    for entity_id, action in (
        ("climate.bedroom_child", "climate.set_temperature"),
        ("light.bedroom_child", "light.turn_on"),
    ):
        await hm.consider(
            ActionRequest(
                Layer.PLANNER,
                entity_id,
                action,
                {},
                area_id="bedroom_child",
                created_at=clock.now(),
            )
        )
    assert await answer() == "Ik sta op pauze."
    await hm.pause(None)
    await clock.advance(2 * 3600)
    said = await answer()
    assert said.startswith("Ik sta op pauze. Dat was om ") and said.endswith(".")
    await hm.stop()


async def test_an_answer_from_the_help_fits_the_level_of_who_asks(tmp_path: Path) -> None:
    """Vesta answered from the Help of every level, so someone on Ontzorgd got the Expert's
    answer about the safety gate. Now, as in the Help itself, only their own level and below
    – the manual stays for everyone."""
    hm, _ha, _clock = await _start(tmp_path)
    question = "Wat controleert de veiligheidspoort?"
    expert = hm.library.search(question, "nl")[0][1]
    assert expert.key == "help:l4:q4"
    for level, shown in ((1, False), (4, True)):
        reply = await hm.talk(question, "nl", level, "demo-user-1")
        assert (gist(expert.text) in str(reply["text"])) is shown, level
    await hm.stop()


async def test_undo_and_a_thumb_down_count_as_stepping_in_per_part_with_a_trend(
    tmp_path: Path,
) -> None:
    """S-01 asks for the count of corrections per part, with its trend, on levels 3 and 4:
    nowhere shown, and Undo and 👎 were not in the count (M-16). Now each part shows four
    weeks, oldest first; Undo and 👎 on the same action count once."""
    hm, ha, decision = await _did_something(tmp_path)
    assert (await hm.undo(decision, "demo-user-1"))["ok"]
    await hm.feedback(False, decision, "demo-user-1")  # the same action: still one
    parts = {p["domain"]: p for p in await readiness(hm, "nl", 3, True)}
    assert parts["lighting"]["corrections"]["weeks"] == [0, 0, 0, 1]
    assert (
        "corrections"
        not in {p["domain"]: p for p in await readiness(hm, "nl", 2, True)}["lighting"]
    )
    await hm.clock.advance(8 * 24 * 3600)  # type: ignore[attr-defined]
    await ha.simulate("binary_sensor.kitchen_motion", "off")
    await ha.simulate("light.kitchen", "off")
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    again = max(i for i, r in hm.recent.items() if r.decision.executed and r.decision.request.skill)
    assert again != decision
    await hm.feedback(False, again, "demo-user-2")
    parts = {p["domain"]: p for p in await readiness(hm, "nl", 3, True)}
    assert parts["lighting"]["corrections"] == {
        "weeks": [0, 0, 1, 1],
        "text": "Ingegrepen per week, de laatste 4 weken: 0 → 0 → 1 → 1.",
    }
    await hm.stop()


async def test_she_promises_only_what_she_will_do(tmp_path: Path) -> None:
    """ "In the evening I make the house look lived in" also when an administrator switched
    that off; "the car is charged by 6:00" while she does not charge it at all (M-17, E-365). Now
    she says what really happens."""
    hm, _ha, _clock = await _start(tmp_path)
    await hm.set_house_setting("lived_in", False)
    said = (await hm.talk("we gaan op vakantie", "nl", 3, "demo-user-1"))["text"]
    assert "bewoond laten lijken staat uit" in said and "laat ik het huis" not in said
    await hm.stop()

    clock = SimulatedClock(datetime(2026, 10, 19, 4, 0, tzinfo=UTC))
    house = load_demo_house("energy_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path / "energy"))
    await hm.start(autorun=False)
    assert hm.energy is not None
    # She does not charge the car (E-363, E-365): she says so, with when it is cheapest.
    said = (await hm.talk("laad de auto vol voor 7:00", "nl", 3, None))["text"]
    assert said.startswith("Het laden van de auto regel ik niet.")
    await hm.stop()


async def test_start_knowledge_needs_one_situation_and_the_card_says_so(tmp_path: Path) -> None:
    """For start knowledge (E-160) the first situation is enough to propose, but the card
    said "situaties 1 gezien · 20 nodig" (L7). Now it says 1 is needed."""
    hm, _clock, _ha = await start_automations(tmp_path)
    assert hm.learning is not None and "light_on_entry" in hm.learning.start_knowledge
    _seen(hm, "light_on_entry", "kitchen", Step.OBSERVE, days=0.5, hits=1)
    lighting = next(p for p in await readiness(hm, "nl", 3, True) if p["domain"] == "lighting")
    kitchen = next(s for s in lighting["skills"] if s["area"] == "kitchen")
    [situations] = kitchen["counters"]
    assert (situations["key"], situations["value"], situations["target"]) == ("situations", 1, 1)
    assert situations["met"]
    await hm.stop()


async def test_the_reaction_time_counts_from_trigger_to_done(tmp_path: Path) -> None:
    """The reaction time (once the Expert meter, now a line in Over HouseMind, QA 3) counts
    from the moment HA saw the trigger until the action is done – with HA's own delay (L8)."""
    hm, ha, _decision = await _did_something(tmp_path)
    shown = views.reaction(hm)
    assert shown["ms"] is not None and shown["ms"] > 0
    await ha.simulate("binary_sensor.kitchen_motion", "off")
    await ha.simulate("light.kitchen", "off")
    await hm.clock.advance(24 * 3600)  # type: ignore[attr-defined]  # the same hour, a day on
    await hm.tick()
    ha.delivery_lag = timedelta(milliseconds=300)  # HA saw it 300 ms before HouseMind heard it
    await ha.simulate("binary_sensor.kitchen_motion", "on")
    assert ha.states["light.kitchen"].state == "on"
    assert hm.status.reactions[-1] >= 300
    assert views.reaction(hm)["maxMs"] >= 300
    await hm.stop()
