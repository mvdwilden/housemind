""" "Hey Vesta" (E-232): HouseMind makes no wake word of its own and never picks one with another
name. Has Home Assistant one with her name, then her own Assist pipeline gets it, after the
administrator's yes to that pipeline. Each test fails without it; what it checks is its
docstring."""

from __future__ import annotations

from pathlib import Path

from housemind.brain import setting_up
from housemind.core.model import Entity, State
from tests.test_automations import start
from tests.test_setting_up import AGENT, _link

ENGINE = "wake_word.local"


async def _engine(hm, ha, clock, words: list[tuple[str, str]]) -> None:  # type: ignore[no-untyped-def]
    ha.model.entities[ENGINE] = Entity(ENGINE, "r-wake", "Wakkerwoord")
    ha.states[ENGINE] = State(ENGINE, "unknown", {}, clock.now())
    ha.wake_word_ids[ENGINE] = words
    await hm._refresh_model()


async def test_a_wake_word_with_her_name_goes_into_her_pipeline(tmp_path: Path) -> None:
    """HA offers "Hey Vesta" among others: her pipeline listens for exactly that one."""
    hm, clock, ha = await start(tmp_path)
    await _link(hm, ha, clock)
    await _engine(hm, ha, clock, [("okay_nabu", "Okay Nabu"), ("hey_vesta", "Hey Vesta")])
    assert await setting_up.make_pipeline(hm)
    mine = next(p for p in ha.pipelines if p["conversation_engine"] == AGENT)
    assert (mine["wake_word_entity"], mine["wake_word_id"]) == (ENGINE, "hey_vesta")
    await hm.stop()


async def test_no_wake_word_with_another_name(tmp_path: Path) -> None:
    """Only other wake words in HA: her pipeline gets none – choosing one is up to the people."""
    hm, clock, ha = await start(tmp_path)
    await _link(hm, ha, clock)
    await _engine(hm, ha, clock, [("okay_nabu", "Okay Nabu")])
    assert await setting_up.make_pipeline(hm)
    mine = next(p for p in ha.pipelines if p["conversation_engine"] == AGENT)
    assert (mine["wake_word_entity"], mine["wake_word_id"]) == (None, None)
    await hm.stop()
