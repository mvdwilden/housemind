"""The AI of Home Assistant as a service (E-422), and more with an AI in the house (E-423).
An administrator can choose the AI task that is already set up in Home Assistant: HouseMind keeps
no key, says whether that AI works in the house or in the cloud, and – because it cannot see
what it costs – asks a cloud AI at most so many questions a day. An AI in the house has no
budget and reads the house more often. Each test fails without the change; what it checks is
its docstring."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

import housemind.ai.service as service
from housemind.ai.service import AIConfig
from housemind.core.model import Entity, State
from housemind.parts.understanding import READING
from housemind.server import views
from tests.test_ai import start

ROOT = Path(__file__).resolve().parents[1]
CLOUD = "ai_task.openai_ai_task"
LOCAL = "ai_task.ollama_ai_task"


async def _house(tmp_path: Path, reply: Any = None) -> Any:
    """The family home with two AI tasks in Home Assistant: one in the cloud, one in the house."""
    hm, clock, ha = await start(tmp_path)
    for entity_id, name, platform in (
        (CLOUD, "OpenAI AI Task", "openai_conversation"),
        (LOCAL, "Ollama AI Task", "ollama"),
    ):
        ha.model.entities[entity_id] = Entity(entity_id, f"r-{entity_id}", name, platform=platform)
        ha.states[entity_id] = State(entity_id, "unknown", {}, clock.now())
    await hm._refresh_model()
    ha.ai_task_reply = reply if reply is not None else (lambda *_: {"answer": "Het is 20 graden."})
    return hm, clock, ha


async def _choose(hm: Any, entity_id: str) -> None:
    await hm.ai.configure(AIConfig(enabled=True, provider="home_assistant", base_url=entity_id))
    await hm.ai.give_consent("demo-user-1")


async def test_the_ai_of_home_assistant_answers_without_a_key(tmp_path: Path) -> None:
    """With 'De AI van Home Assistant' chosen, a question goes to the AI task in Home Assistant
    – with the same instruction and the same labels, never names – and HouseMind keeps no key.
    House settings list the AI tasks there are and say where each works."""
    hm, _clock, ha = await _house(tmp_path)
    await _choose(hm, CLOUD)
    assert hm.ai.status() == "on"
    answer = await hm.ask("Is het warm genoeg in de woonkamer?", "nl", "demo-user-1")
    assert answer["ok"] and answer["message"] == "Het is 20 graden."
    entity_id, task, instructions = ha.ai_tasks[-1][:3]
    assert entity_id == CLOUD and task == "HouseMind"
    assert "Reply with one JSON object only." in instructions and "<data>" in instructions
    assert "Woonkamer" not in instructions and "Emma" not in instructions
    assert not hm.ai.keys.has("ai:home_assistant")
    log = await hm.ai.log(1)
    assert log[0]["provider"] == "home_assistant" and log[0]["instruction"]
    settings = await views.house_settings(hm, "nl")
    places = {t["id"]: t["place"] for t in settings["ai"]["aiTasks"]}
    assert places == {CLOUD: "cloud", LOCAL: "local"}
    assert settings["ai"]["local"] is False
    await hm.stop()


async def test_a_cloud_ai_task_has_a_limit_a_day(tmp_path: Path, monkeypatch: Any) -> None:
    """HouseMind cannot see what a question costs through Home Assistant: a cloud AI task gets
    at most so many questions a day, and then HouseMind says so in plain words."""
    monkeypatch.setattr(service, "AI_TASK_PER_DAY", 2)
    hm, clock, _ha = await _house(tmp_path)
    await _choose(hm, CLOUD)
    for _ in range(2):
        assert (await hm.ask("Is het warm genoeg?", "nl", "demo-user-1"))["ok"]
    third = await hm.ask("Is het warm genoeg?", "nl", "demo-user-1")
    assert third == {
        "ok": False,
        "message": "Voor vandaag heb ik de AI genoeg gevraagd. Morgen kan het weer.",
    }
    await clock.advance(24 * 3600)
    assert (await hm.ask("Is het warm genoeg?", "nl", "demo-user-1"))["ok"]
    await hm.stop()


async def _read_two_days_ago(hm: Any, clock: Any) -> list[str]:
    """The house was read two days ago; a quarter of an hour passes. Which kinds of reading
    were read again?"""
    before = clock.now() - timedelta(days=2)
    await hm.db.set_setting("house_read", dict.fromkeys(READING, before.isoformat()))
    hm._house_unread.clear()
    hm._last_review = None
    await hm.tick()
    await hm.ai_jobs.idle()
    read = await hm.db.get_setting("house_read", {})
    return [k for k in READING if datetime.fromisoformat(read[k]) > before]


async def test_an_ai_in_the_house_has_no_limit_and_reads_the_house_more_often(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """An AI task that works in the house: no limit and no budget, and HouseMind reads the house
    again after a day instead of a week."""
    monkeypatch.setattr(service, "AI_TASK_PER_DAY", 2)
    hm, clock, ha = await _house(tmp_path, reply=lambda *_: {"answer": "Ja."})
    await _choose(hm, LOCAL)
    assert hm.ai.local is True
    for _ in range(3):
        assert (await hm.ask("Is het warm genoeg?", "nl", "demo-user-1"))["ok"]
    ha.ai_task_reply = lambda *_: {}
    assert await _read_two_days_ago(hm, clock)  # read again after two days
    await hm.stop()


async def test_a_cloud_ai_task_reads_the_house_once_a_week(tmp_path: Path) -> None:
    """In the cloud the house is read once a week, as with any cloud AI."""
    hm, clock, _ha = await _house(tmp_path, reply=lambda *_: {})
    await _choose(hm, CLOUD)
    assert await _read_two_days_ago(hm, clock) == []
    await hm.stop()


def test_every_reason_an_ai_gave_no_answer_has_words_in_every_language() -> None:
    """An answer cut off, or a limit for the day: the app says it in words, never as a code."""
    reasons = ("budget", "too_soon", "timeout", "unreachable", "bad_answer", "error")
    reasons += ("cut_off", "day_limit")
    for lang in ("nl", "en", "de", "fr"):
        texts = yaml.safe_load((ROOT / "knowledge" / "languages" / f"{lang}.yaml").read_text())
        why = texts["ai"]["why"]
        assert set(reasons) <= set(why), (lang, sorted(set(reasons) - set(why)))
    assert json.dumps(reasons)
