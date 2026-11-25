"""A question asked again gives the same reason as when it was asked. Found in the demo house
(01-11-2026): the card said HouseMind wanted to put the driveway lamp out "omdat iemand 5 min
geleden thuiskwam en er niemand meer buiten is", while Vesta, asking the same question, said
"omdat iemand in het donker thuiskwam". A proposal kept the facts of its reason, but asking it
again only passed on the moment of the day, so every skill with more than one reason fell back
to its plain one. The test fails without the change; what it checks is its docstring."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from housemind.brain.skills import ARRIVAL
from housemind.knowledge.catalog import Step
from housemind.server import views
from tests.test_automation_in_row import _expert
from tests.test_outside_arrival import DRIVE, _away
from tests.test_real_house_counts import _until

REASON = "omdat iemand 5 min geleden thuiskwam en er niemand meer buiten is"


async def test_vesta_and_the_list_ask_it_with_the_reason_of_the_card(tmp_path: Path) -> None:
    """HouseMind lit the driveway for someone coming home; then the administrator set it back to
    Propose. Five minutes later it asks whether it may put the lamp out. The card, the list of
    questions, Vesta's question and her answer to Waarom? all give the same reason – that
    someone came home 5 minutes ago and nobody is outside any more – not "in the dark"."""
    hm, clock, ha = await _away(tmp_path)
    await _expert(hm)
    assert hm.learning is not None
    await ha.simulate("person.resident_1", "home")
    assert ha.states[DRIVE].state == "on"  # lit by HouseMind for coming home
    hm.learning.steps[(ARRIVAL, "driveway")] = Step.PROPOSE
    await _until(hm, clock, clock.now() + timedelta(minutes=6), step=30)
    assert ha.states[DRIVE].state == "on"  # asked, not done
    asked = [p for p in await hm.open_proposals() if p.entity_id == DRIVE]
    assert len(asked) == 1 and asked[0].action == "light.turn_off"
    proposal = asked[0].id

    cards = await views.notifications(hm, "nl", True, "demo-user-1")
    card = next(n for n in cards if n.get("proposal") == proposal)
    assert REASON in card["text"]
    listed = next(q for q in await views.proposals(hm, "nl", 4) if q["id"] == proposal)
    assert listed["question"] == card["text"]

    ask = next(a for a in await hm.vesta_asks("demo-user-1", 4, True) if a["proposal"] == proposal)
    said = await hm.vesta_ask_text("demo-user-1", "nl", ask)
    assert REASON in said and "in het donker" not in said
    why = await hm.vesta_why_asked("demo-user-1", "nl", proposal)
    assert "5 min geleden thuiskwam" in why and "in het donker" not in why
    await hm.stop()
