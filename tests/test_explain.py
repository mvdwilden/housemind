from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from housemind.brain.explain import Explainer
from housemind.brain.learning import Learning, Result
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.i18n.catalog import load_languages
from housemind.memory.database import Database
from housemind.safety.gate import ActionRequest, Check, Decision, Layer, Outcome

KNOWLEDGE = Path(__file__).resolve().parents[1] / "knowledge"
T0 = datetime(2026, 10, 19, 19, 30, tzinfo=UTC)
CTX = "workday|21:30"


def _decision(
    outcome: Outcome = Outcome.EXECUTED,
    reason: str | None = None,
    skill: str = "light_on_entry",
    **facts: object,
) -> Decision:
    request = ActionRequest(
        Layer.REFLEX,
        "light.living_ceiling",
        "light.turn_on",
        {"brightness_pct": 60},
        reason=f"skill:{skill}",
        skill=skill,
        area_id="living",
        facts={"context": CTX, **facts},
    )
    return Decision(
        request, outcome, reason, None, (Check("1_valid", True), Check("2_allowed", True)), T0
    )


async def _explainer(tmp_path: Path) -> Explainer:
    house = load_demo_house("apartment", SimulatedClock(T0), "nl")
    db = Database(tmp_path / "h.db")
    await db.open()
    learning = Learning(db, T0)
    for i in range(20):
        learning._add(
            "light_on_entry",
            "living",
            CTX,
            Result.HIT if i < 18 else Result.MISS,
            T0 - timedelta(minutes=i),
        )
    return Explainer(load_languages(KNOWLEDGE / "languages"), house.ha.model, learning)


async def test_explanation_per_level(tmp_path: Path) -> None:
    explainer = await _explainer(tmp_path)
    l2 = explainer.explain(_decision(), 2, "nl", T0)
    assert l2 == [
        "Ik heb Woonkamer plafondlamp aangedaan, omdat het donker is "
        "en er iemand in de Woonkamer kwam."
    ]
    l3 = explainer.explain(_decision(), 3, "nl", T0)
    assert l3[1] == "Op werkdagen rond 21:30 doe je dat meestal zo (18 van de 20 keer)."
    l4 = explainer.explain(_decision(), 4, "en", T0)
    assert l4[0].startswith("I turned on Living room ceiling light") or l4[0].startswith(
        "I turned on"
    )
    # In words, never codes (QA 3): how sure, and the checks.
    assert any("% sure" in line for line in l4)
    assert any("checks passed" in line or "Stopped at" in line for line in l4)
    assert not any("1_valid" in line or "{" in line for line in l4)
    await explainer.learning.db.close()  # type: ignore[union-attr]


async def test_dim_warning_and_proposal_wording(tmp_path: Path) -> None:
    explainer = await _explainer(tmp_path)
    warning = explainer.explain(_decision(skill="light_off_leave", warning=True), 2, "nl", T0)[0]
    # "in a moment": a toilet's lamps that wait one minute dim for half a minute (E-444)
    assert "gedimd" in warning and "zo meteen gaat het licht uit" in warning
    proposed = explainer.explain(_decision(Outcome.PROPOSED), 2, "nl", T0)
    assert proposed[0].startswith("Ik wilde")
    assert proposed[1] == "Ik stelde het voor, omdat ik nog niet zeker genoeg ben."
    await explainer.learning.db.close()  # type: ignore[union-attr]


async def test_why_not(tmp_path: Path) -> None:
    explainer = await _explainer(tmp_path)
    assert (
        explainer.why_not(_decision(Outcome.DENIED, "people_win"), "nl")
        == "Iemand heeft dit apparaat met de hand bediend."
    )
    assert (
        explainer.why_not(_decision(Outcome.DENIED, "hard_limit:lock_open"), "en")
        == "I never do this myself."  # she speaks as herself (20.21)
    )
    assert (
        explainer.why_not(_decision(Outcome.OBSERVED), "nl")
        == "Ik kijk nog mee en doe dit nog niet zelf."
    )
    assert explainer.why_not(None, "nl").startswith("Ik heb hier nog niets mee gedaan.")
    await explainer.learning.db.close()  # type: ignore[union-attr]


async def test_what_did_not_happen_says_what_she_wanted_and_what_stopped_it(
    tmp_path: Path,
) -> None:
    """Found in the real house: "I did not dimmed one of the TVs …, because nobody has been in
    the room" – wrong English, and the reason for wanting it instead of the one that stopped
    it. Now: what she wanted and why, then what stopped her."""
    explainer = await _explainer(tmp_path)
    lines = explainer.explain(_decision(Outcome.DENIED, "people_win"), 3, "en", T0)
    assert lines[0].startswith("I wanted to turn on") and "because" in lines[0]
    assert lines[1] == "Someone operated this device by hand."
    assert not any("did not" in line for line in lines)
    nl = explainer.explain(_decision(Outcome.DENIED, "people_win"), 2, "nl", T0)
    assert (
        nl[0].startswith("Ik wilde") and nl[1] == "Iemand heeft dit apparaat met de hand bediend."
    )
    await explainer.learning.db.close()  # type: ignore[union-attr]
