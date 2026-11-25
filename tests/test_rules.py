from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path

from housemind.brain.rules import HouseRule, RuleBook, parse_rule, read_back
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.i18n.catalog import load_languages
from housemind.knowledge.catalog import Step
from housemind.memory.database import Database
from housemind.safety.gate import ActionRequest, Layer

KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"
T0 = datetime(2026, 10, 19, 4, 30, tzinfo=UTC)
LANG = load_languages(KNOWLEDGE / "languages")


def _model(language: str = "nl"):  # type: ignore[no-untyped-def]
    return load_demo_house("apartment", SimulatedClock(T0), language).ha.model


def test_parse_dutch_never_before() -> None:
    model = _model("nl")
    rule = parse_rule("Nooit de gordijnen in de woonkamer open voor 7:00", "nl", LANG, model)
    assert rule is not None
    assert (rule.scope_type, rule.scope_id, rule.effect, rule.category) == (
        "area",
        "living",
        "never",
        "open",
    )
    assert rule.time_to == time(7, 0) and rule.time_from is None
    assert (
        read_back(rule, "nl", LANG, model)
        == "Ik zal in de Woonkamer voor 07:00 nooit de raambekleding openen."
    )


def test_parse_english_and_limit() -> None:
    model = _model("en")
    rule = parse_rule("never turn on the light in the bedroom after 23:00", "en", LANG, model)
    assert rule is not None and rule.category == "on" and rule.time_from == time(23, 0)
    assert rule.scope_type in ("area", "entity")
    limit = parse_rule("Nooit warmer dan 21,5 graden", "nl", LANG, _model("nl"))
    assert limit is not None and limit.effect == "limit" and limit.value == 21.5
    assert (
        read_back(limit, "nl", LANG, _model("nl"))
        == "Ik verwarm in het hele huis nooit warmer dan 21,5 °C."
    )
    assert parse_rule("doe iets leuks", "nl", LANG, _model("nl")) is None


def test_veto_window_limit_and_override() -> None:
    model = _model()
    book = RuleBook(
        model,
        [
            HouseRule("area", "living", "never", "open", time_to=time(7, 0), id=1),
            HouseRule("house", "*", "limit", "heat", 21.0, id=2),
            HouseRule("area", "bedroom", "always", skill="light_on_entry", id=3),
        ],
    )
    open_req = ActionRequest(
        Layer.PLANNER, "cover.living_curtain", "cover.open_cover", area_id="living"
    )
    assert book.veto(open_req, datetime(2026, 10, 19, 6, 30), "home") == "rule_1"
    assert book.veto(open_req, datetime(2026, 10, 19, 7, 30), "home") is None
    hot = ActionRequest(
        Layer.PLANNER, "climate.living", "climate.set_temperature", {"temperature": 22}
    )
    assert book.veto(hot, datetime(2026, 10, 19, 12, 0), "home") == "rule_2"
    assert book.step_override("light_on_entry", "bedroom") is Step.AUTONOMOUS
    assert book.step_override("light_on_entry", "living") is None
    conflict = book.conflicts(HouseRule("area", "bedroom", "never", skill="light_on_entry"))
    assert [r.id for r in conflict] == [3]


async def test_rules_are_stored(tmp_path: Path) -> None:
    db = Database(tmp_path / "h.db")
    await db.open()
    book = RuleBook(_model())
    rule_id = await book.add(
        db,
        HouseRule("area", "living", "never", "open", time_to=time(7, 0), source_text="x"),
        "u1",
        T0,
    )
    again = RuleBook(_model())
    await again.load(db)
    assert again.rules[0].id == rule_id and again.rules[0].time_to == time(7, 0)
    await again.remove(db, rule_id)
    assert again.rules == []
    await db.close()
