"""Round 7, from the full retest: privacy with the AI, and learning only after the "yes".
Each test fails without the fix; the finding is in its docstring."""

from __future__ import annotations

from pathlib import Path

from housemind.ai.privacy import Labels
from housemind.ai.service import AIConfig
from housemind.brain.proposals import Answer
from housemind.core.model import Area, Entity, HouseModel
from housemind.knowledge.catalog import Step
from tests.test_ai import FakeAI, enable, start


def test_parts_of_names_are_replaced_and_people_are_no_devices() -> None:
    """Only a full HA name was replaced: "Anna" alone went to the AI literally, and a
    person was labelled "[device]"."""
    model = HouseModel()
    model.areas["living"] = Area("living", "Woonkamer")
    model.entities["person.anna"] = Entity("person.anna", "r1", "Anna van der Berg")
    labels = Labels.build(model, {"living": "living"}, ["Anna van der Berg", "Oma Jet"])
    text = labels.pseudonymise("Anna vraagt of oma Jet komt")
    assert "Anna" not in text and "Jet" not in text and "[device]" not in text
    assert "resident_" in text


async def test_a_yes_for_one_ai_service_is_not_a_yes_for_another(tmp_path: Path) -> None:
    """A yes for a local AI counted for a cloud AI later; a yes could not be taken back."""
    hm, _, _ = await start(tmp_path)
    await enable(hm, FakeAI([]))
    assert hm.ai.status() == "on"
    other = AIConfig(
        enabled=True,
        provider="openai",
        consent_at=hm.ai.config.consent_at,
        consent_by=hm.ai.config.consent_by,
        consent_for=hm.ai.config.consent_for,
    )
    hm.ai.config = other
    assert hm.ai.status() == "needs_consent"  # another service needs its own yes
    await hm.ai.give_consent("demo-user-1", yes=True)
    await hm.ai.give_consent("demo-user-1", yes=False)
    assert hm.ai.config.consent_at is None  # taken back
    await hm.stop()


async def test_nothing_is_learned_without_the_yes(tmp_path: Path) -> None:
    """Without the yes to learning, answers still taught and skills became autonomous."""
    hm, clock, ha = await start(tmp_path)
    assert hm.learning is not None
    hm.learning_allowed = False
    hm.learning.steps[("light_off_leave", "living")] = Step.PROPOSE
    await ha.simulate("binary_sensor.living_presence", "on")
    await ha.simulate("light.living_reading", "on")
    await ha.simulate("binary_sensor.living_presence", "off")
    for _ in range(50):
        await clock.advance(30)
        await hm.tick()
    open_now = await hm.open_proposals()
    assert open_now
    before = dict(hm.learning.counts)
    await hm.answer(open_now[0].id, Answer.ALWAYS, "demo-user-1")
    assert dict(hm.learning.counts) == before  # the answer taught nothing
    await hm.stop()


def test_a_name_the_integration_gave_stays_a_plain_word_in_a_question() -> None:
    """The administrator asked "Zet de lichten in de garage aan." and the AI got "Zet de
    [device] in de garage_1 aan.": a sensor that is not shared is called "Lichten" by its
    integration, and every name of such an entity was hidden. The AI could not know what was
    meant (E-453). A name a person gave is still hidden."""
    model = HouseModel()
    model.areas["garage"] = Area("garage", "Garage")
    model.entities["sensor.lights"] = Entity("sensor.lights", "r1", "Lichten")  # its own name
    model.entities["camera.tuin"] = Entity(
        "camera.tuin", "r2", "Camera Achtertuin", named_by_person=True
    )
    labels = Labels.build(model, {"garage": "garage"})
    assert (
        labels.pseudonymise("Zet de lichten in de garage aan.")
        == "Zet de lichten in de garage_1 aan."
    )
    assert (
        labels.pseudonymise("Is er iets op de Camera Achtertuin?") == "Is er iets op de [device]?"
    )
