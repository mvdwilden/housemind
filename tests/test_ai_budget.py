"""The AI budget split (E-49, 20.19): Vesta's sentences use the cheapest model and at most
half the monthly budget; on Expert an administrator gives each task a model of its own, and
sees what each task cost. Each test fails without the build."""

from __future__ import annotations

from pathlib import Path

from housemind.ai.service import AIConfig
from tests.test_ai import FakeAI, enable, start


async def _spend(hm, purpose: str, cost: float) -> None:  # type: ignore[no-untyped-def]
    await hm.db.execute(
        "INSERT INTO ai_log(at, purpose, provider, model, sent, received, input_tokens, "
        "output_tokens, cost, ok) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (hm.clock.now().isoformat(), purpose, "anthropic", "m", "{}", "{}", 1, 1, cost, 1),
    )


async def test_vesta_uses_the_cheapest_model_and_half_the_budget(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    fake = FakeAI([{"text": "Hoi"}] * 5)
    await enable(hm, fake, monthly_budget=10.0, model_strong="big-model", model_routine="small")
    assert hm.ai.config.model_for("vesta", strong=True) == "small"  # never the strong one
    await _spend(hm, "vesta", 4.9)
    parsed, reason = await hm.ai._call("vesta", "say hi", {})
    assert reason is None and fake.sent[-1][2] == "small"
    await _spend(hm, "vesta", 0.2)  # 5.1 of 10: her half is used up
    parsed, reason = await hm.ai._call("vesta", "say hi", {})
    assert parsed is None and reason == "budget"
    # Other questions still have the other half.
    parsed, reason = await hm.ai._call("ask", "answer", {})
    assert reason is None
    await hm.stop()


async def test_a_model_per_task_and_what_each_task_cost(tmp_path: Path) -> None:
    hm, _, _ = await start(tmp_path)
    fake = FakeAI([{"ok": True}] * 3)
    await enable(hm, fake, monthly_budget=10.0, task_models={"rule": "rule-model"})
    await hm.ai._call("rule", "a rule", {})
    assert fake.sent[-1][2] == "rule-model"
    await _spend(hm, "general", 0.5)
    await _spend(hm, "ask", 0.25)
    await _spend(hm, "roles", 0.1)
    spent = await hm.ai.spent_by_task()
    assert spent["ask"] == 0.75 and spent["analysis"] == 0.1
    assert set(spent) == {"vesta", "ask", "rule", "analysis"}
    kept = AIConfig.from_dict({"task_models": {"vesta": "x"}})
    assert kept.task_models == {"vesta": "x"}
    await hm.stop()
