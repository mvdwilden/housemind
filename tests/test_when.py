"""Wanneer doe ik wat (E-385 – E-387, B-287 – B-292), from the user's side: in Brein, on 'Klaar
voor autonomie?', each skill in a room says in one plain sentence when HouseMind does it, with
what it learned – Autonomous 'Ik doe …', Propose 'Ik vraag of …', Observe 'Ik zou …' – honest
about what it does not know yet, with a house rule that takes part; on Expert exactly why. Each
test fails without it; what it checks is its docstring."""

from __future__ import annotations

from datetime import time, timedelta
from pathlib import Path
from typing import Any

from housemind.brain.learning import Result
from housemind.brain.readiness import readiness
from housemind.brain.rules import HouseRule
from housemind.brain.when import STEP_KEYS, when
from housemind.core.clock import SimulatedClock
from housemind.core.model import WHOLE_HOUSE, Entity, State
from housemind.demo.houses import load_demo_house
from housemind.knowledge.catalog import Step
from housemind.runtime import HouseMind, Paths
from tests.test_automations import EVENING, HALL, HALL_CONFIG, ROOT

FRONTEND = ROOT / "frontend" / "src"
EVENING_SLOT = "workday|19:30"
NIGHT_SLOT = "workday|23:30"


async def _house(tmp_path: Path) -> HouseMind:
    """The family home with a lamp in the hall that dims and a light sensor there, and the
    hall automation that teaches light on entry (E-41)."""
    clock = SimulatedClock(EVENING)
    house = load_demo_house("family_home", clock, "nl")
    lamp = house.ha.model.entities["light.hall"]
    lamp.capabilities["supported_color_modes"] = ["brightness"]
    house.ha.states["light.hall"].attributes["supported_color_modes"] = ["brightness"]
    sensor = "sensor.hall_light_level"
    house.ha.model.entities[sensor] = Entity(
        entity_id=sensor,
        registry_id=f"demo-{sensor}",
        name="Gang lichtsterkte",
        device_id=sensor,
        area_id="hall",
        device_class="illuminance",
        unit="lx",
        platform="demo",
    )
    house.ha.states[sensor] = State(
        sensor, "12", {"device_class": "illuminance", "unit_of_measurement": "lx"}, clock.now()
    )
    await house.ha.add_automation(HALL, str(HALL_CONFIG["alias"]), HALL_CONFIG)
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    return hm


def _learned(hm: HouseMind, step: Step, hits: int = 14, misses: int = 2) -> None:
    """The hall light learned: 55% in the evening, 15% at night, and right 14 of 16 times."""
    learning, now = hm.learning, hm.clock.now()
    assert learning is not None
    for slot, pct in ((EVENING_SLOT, 55.0), (NIGHT_SLOT, 15.0)):
        learning.values[("light_on_entry", "hall", slot, "brightness_pct")] = (pct, 4.0, now)
    for result, times in ((Result.HIT, hits), (Result.MISS, misses)):
        for _ in range(times):
            learning._add("light_on_entry", "hall", EVENING_SLOT, result, now)
    learning.steps[("light_on_entry", "hall")] = step
    learning.first_seen[("light_on_entry", "hall")] = now - timedelta(days=10)


def _row(parts: list[dict[str, Any]], skill: str, area: str) -> dict[str, Any]:
    rows = [s for p in parts for s in p.get("skills", [])]
    return next(r for r in rows if r["skill"] == skill and r["area"] == area)


HALL_SAYS = (
    "de lamp in de gang {} zodra iemand binnenkomt en het donker is: "
    "’s avonds tussen Normaal en Helder, ’s nachts op Gedimd"
)


async def test_autonomous_says_i_do_with_what_it_learned(tmp_path: Path) -> None:
    """The administrator's example: light on entry in the hall, on Autonomous, says in one
    sentence when HouseMind does it, with the levels it learned per part of the day – not the
    start knowledge (60% in the evening, 20% at night)."""
    hm = await _house(tmp_path)
    _learned(hm, Step.AUTONOMOUS)
    row = _row(await readiness(hm, "nl", 3, True), "light_on_entry", "hall")
    assert row["step"] == "autonomous"
    assert row["when"] == "Ik doe " + HALL_SAYS.format("aan") + "."
    await hm.stop()


async def test_each_step_has_its_own_sentence(tmp_path: Path) -> None:
    """Per step its own sentence (E-386): the same light on Observe says 'Ik zou …', on Propose
    'Ik vraag of …' – what it would do or asks, never 'Ik doe …'."""
    hm = await _house(tmp_path)
    _learned(hm, Step.OBSERVE)
    row = _row(await readiness(hm, "nl", 3, True), "light_on_entry", "hall")
    assert row["step"] == "observe"
    assert row["when"] == "Ik zou " + HALL_SAYS.format("aandoen") + "."
    hm.learning.steps[("light_on_entry", "hall")] = Step.PROPOSE  # type: ignore[union-attr]
    # The automation still steers it: HouseMind asks nothing there, and says so (E-460).
    row = _row(await readiness(hm, "nl", 3, True), "light_on_entry", "hall")
    assert row["step"] == "observe" and row["watching"]
    assert row["when"] == "Ik zou " + HALL_SAYS.format("aandoen") + "."
    assert hm.gate is not None
    hm.gate.state.other_system.clear()
    row = _row(await readiness(hm, "nl", 3, True), "light_on_entry", "hall")
    assert row["step"] == "propose"
    assert row["when"] == "Ik vraag of ik " + HALL_SAYS.format("mag aandoen") + "."
    await hm.stop()


async def test_a_house_rule_that_takes_part_is_said(tmp_path: Path) -> None:
    """A house rule 'never turn the light in the hall on before 7:00' takes part: the
    sentence says it ('…, maar nooit voor 7:00'). A rule for another room does not."""
    hm = await _house(tmp_path)
    _learned(hm, Step.AUTONOMOUS)
    now = hm.clock.now()
    await hm.rules.add(
        hm.db, HouseRule("area", "kitchen", "never", "on", time_to=time(6)), None, now
    )
    row = _row(await readiness(hm, "nl", 3, True), "light_on_entry", "hall")
    assert "nooit" not in row["when"]
    await hm.rules.add(hm.db, HouseRule("area", "hall", "never", "on", time_to=time(7)), None, now)
    row = _row(await readiness(hm, "nl", 3, True), "light_on_entry", "hall")
    assert row["when"] == "Ik doe " + HALL_SAYS.format("aan") + ", maar nooit voor 7:00."
    await hm.stop()


async def test_what_is_not_known_yet_is_said_honestly(tmp_path: Path) -> None:
    """In the living room, with two lamps, HouseMind does not know yet which ones people use:
    the sentence says so ('welke lampen je meestal gebruikt, leer ik nog'). Once it learned
    that only the reading lamp goes on, it names that lamp and says nothing is missing."""
    hm = await _house(tmp_path)
    assert hm.learning is not None
    hm.learning.first_seen[("light_on_entry", "living")] = hm.clock.now()
    row = _row(await readiness(hm, "nl", 3, True), "light_on_entry", "living")
    assert row["when"].startswith("Ik zou de lampen in de woonkamer aandoen zodra iemand")
    assert row["when"].endswith("; welke lampen je meestal gebruikt, leer ik nog.")
    hm.learning.lights_used["living"] = {"light.living_reading"}
    row = _row(await readiness(hm, "nl", 3, True), "light_on_entry", "living")
    assert row["when"].startswith("Ik zou de leeslamp in de woonkamer aandoen zodra iemand")
    assert "leer ik nog" not in row["when"]
    await hm.stop()


async def test_expert_sees_exactly_why_involved_does_not(tmp_path: Path) -> None:
    """Expert (E-387) sees under the sentence the sensors and lamps by their names in Home
    Assistant, the light limit in lux, where it learned it and how sure it is ('14 van de 16
    keer'); for light off when leaving the wait and the dimming. Betrokken sees the sentence
    but not that line."""
    hm = await _house(tmp_path)
    _learned(hm, Step.AUTONOMOUS)
    hm.learning.first_seen[("light_off_leave", "hall")] = hm.clock.now()  # type: ignore[union-attr]
    involved = _row(await readiness(hm, "nl", 3, True), "light_on_entry", "hall")
    assert involved["when"] and "whenDetail" not in involved
    parts = await readiness(hm, "nl", 4, True)
    detail = _row(parts, "light_on_entry", "hall")["whenDetail"]
    for said in (
        "Gang beweging",
        "Gang lamp",
        "lux < 40 (Gang lichtsterkte)",
        "automatisering ‘Hall light at motion’",
        "14 van de 16 keer goed",
    ):
        assert said in detail, (said, detail)
    leave = _row(parts, "light_off_leave", "hall")
    assert leave["when"].startswith("Ik zou de lamp in de gang uitdoen als er 5 minuten niemand")
    assert "wachttijd 5 min" in leave["whenDetail"]  # a hall: people walk back and forth (E-444)
    assert "eerst 60 s op Gedimd" in leave["whenDetail"]  # E-494
    await hm.stop()


async def test_the_sentence_in_four_languages(tmp_path: Path) -> None:
    """The hall example in every language, each in its own words (E-24): no Dutch or English
    left in another language."""
    hm = await _house(tmp_path)
    _learned(hm, Step.AUTONOMOUS)
    for lang, said in (
        ("en", "I turn on the lamp in the gang as soon as someone comes in and it is dark"),
        ("de", "Ich schalte die Lampe im Gang ein, sobald jemand hereinkommt und es dunkel ist"),
        ("fr", "J’allume la lampe dans le gang dès que quelqu’un entre et qu’il fait sombre"),
    ):
        row = _row(await readiness(hm, lang, 3, True), "light_on_entry", "hall")
        assert row["when"].startswith(said), row["when"]
    await hm.stop()


async def test_every_skill_has_a_sentence_per_step_in_four_languages(tmp_path: Path) -> None:
    """Every skill of core.yaml and energy.yaml (B-290) has its own sentence for each step in
    each of the four language files – none falls back to English – and each renders without a
    gap for a room, or for the whole house for energy."""
    hm = await _house(tmp_path)
    languages = hm.languages.languages
    missing = [
        f"{code}: when.{skill}.{key}"
        for skill in sorted(hm.skill_params)
        for key in STEP_KEYS.values()
        for code in ("nl", "en", "de", "fr")
        if f"when.{skill}.{key}" not in languages[code]
    ]
    assert not missing, "\n".join(missing)
    for skill, params in sorted(hm.skill_params.items()):
        area = WHOLE_HOUSE if params.get("domain") == "energy" else "living"
        for code in ("nl", "en", "de", "fr"):
            said = [when(hm, code, skill, area, step, 4)["when"] for step in STEP_KEYS]
            for sentence in said:
                assert sentence.endswith(".") and "{" not in sentence and "when." not in sentence
            assert len(set(said)) == 3, (skill, code, said)
    await hm.stop()


def test_the_screen_shows_the_sentence_and_experts_the_details() -> None:
    """In Brein, each row shows its sentence under its name (B-289), one quiet row (E-501);
    everything about it – the whole sentence and, on Expert, the details as terms and values –
    is in its detail (E-502, B-423), also when grouped by step (E-314)."""
    card = (FRONTEND / "components" / "Readiness.svelte").read_text(encoding="utf-8")
    row = card.split("{#snippet skillRow", 1)[1].split("{/snippet}", 1)[0]
    assert "{skill.when}" in row.split('<span class="what">', 1)[1]
    detail = card.split("{#snippet skillDetail", 1)[1].split("{/snippet}", 1)[0]
    assert "{skill.when}" in detail
    assert "detailFacts(skill.whenDetail)" in detail and 'class="when-detail"' in detail
    types = (FRONTEND / "lib" / "types.ts").read_text(encoding="utf-8")
    assert "when?: string;" in types and "whenDetail?: string;" in types
