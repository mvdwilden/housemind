"""One language per sentence (E-25, E-281): found in the real house – HA shows English, so the
house speaks English, but Vesta said to a Dutch person "Dit weet ik nu ook van jullie huis:
light when entering in the Hal boven – I will suggest it from now on." A message keeps what
it is about and is written in the language of who reads or hears it."""

from __future__ import annotations

import json
from pathlib import Path

from housemind.brain.learning import StepChange
from housemind.brain.voice import Moment
from housemind.knowledge.catalog import Step
from housemind.safety.gate import ActionRequest, Layer
from housemind.server import views
from tests.test_notify import start

ENGLISH = ("when entering", "I will", "suggest it", "from now on")


async def _dutch_resident(hm, level: int = 2) -> None:  # type: ignore[no-untyped-def]
    await hm.db.execute(
        "UPDATE people SET preferences = ? WHERE user_id = 'demo-user-1'",
        (json.dumps({"language": "nl"}),),
    )
    await hm._refresh_people()


async def test_what_she_learned_is_said_in_the_listeners_language(tmp_path: Path) -> None:
    hm, clock, _ha = await start(tmp_path, {1: 2})
    await hm.set_house_language("en")  # HA shows English: the house speaks English
    await _dutch_resident(hm)
    assert hm.world is not None
    change = StepChange("light_on_entry", "kitchen", Step.OBSERVE, Step.PROPOSE, "start")
    await hm._learned(change, hm.world.snapshot(clock.now()))
    viewer = await views.load_viewer(hm, "demo-user-1", "Resident 1", False)
    shown = [n["text"] for n in await views.notifications(hm, "nl", True, "demo-user-1")]
    assert shown and not any(word in text for text in shown for word in ENGLISH), shown
    line = await hm.vesta_line("demo-user-1", 2, True, "nl", Moment.TAP)
    assert not any(word in line.text for word in ENGLISH), line.text
    english = [n["text"] for n in await views.notifications(hm, "en", True, "demo-user-1")]
    assert any("when entering" in text for text in english), english
    assert viewer is not None
    await hm.stop()


async def test_a_question_reaches_each_person_in_their_own_language(tmp_path: Path) -> None:
    hm, _clock, ha = await start(tmp_path, {1: 3})
    await hm.set_house_language("en")
    await _dutch_resident(hm)
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
    pushed = [n for n in ha.notifications if n[0] == "notify.mobile_app_phone_1"]
    assert pushed and pushed[-1][2].startswith("Zal ik"), pushed[-1][2]
    shown = [n["text"] for n in await views.notifications(hm, "nl", True, "demo-user-1")]
    assert shown and all(text.startswith("Zal ik") for text in shown), shown
    await hm.stop()
