"""The weekly house report (E-91, B-33): per level, on each person's day, once a week."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.report import due, week_key
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.ha.fake import FakeHomeAssistant
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.server.logbook import logbook

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
SUNDAY = datetime(2026, 10, 18, 19, 0, tzinfo=TZ)


async def start(
    tmp_path: Path, levels: tuple[int, ...] = (2, 3, 1)
) -> tuple[HouseMind, SimulatedClock, FakeHomeAssistant]:
    clock = SimulatedClock(SUNDAY.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path))
    await hm.start(autorun=False)
    for i, level in enumerate(levels, start=1):
        await hm.db.execute(
            "INSERT INTO people(user_id, display_name, level, consents, created) VALUES(?,?,?,?,?)",
            (f"demo-user-{i}", f"Resident {i}", level, json.dumps({}), clock.now().isoformat()),
        )
    await hm._refresh_people()
    # A week of HouseMind: what it did, one undo, an answer, a learned step, a different day.
    at = (clock.now() - timedelta(days=2)).isoformat()
    for entity, n in (("light.kitchen", 5), ("cover.living_curtain", 2), ("climate.living", 1)):
        for _ in range(n):
            await hm.db.execute(
                "INSERT INTO decisions(at, source_layer, entity_id, action, data, outcome, reason,"
                " trace) VALUES(?,?,?,?,?,?,?,?)",
                (at, "reflex", entity, "x.y", "{}", "executed", "skill:light_on_entry", "[]"),
            )
    await hm.db.execute(
        "INSERT INTO corrections(at, skill, area_id, entity_id) VALUES(?,?,?,?)",
        (at, "light_on_entry", "kitchen", "light.kitchen"),
    )
    await hm.db.execute(
        "INSERT INTO proposals(at, skill, area_id, entity_id, action, data, reason, status, "
        "answered_at, answer) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            at,
            "covers_close_dark",
            "living",
            "cover.living_curtain",
            "cover.close_cover",
            "{}",
            "skill:covers_close_dark",
            "answered",
            at,
            "do",
        ),
    )
    await hm.db.execute(
        "INSERT INTO skill_steps(skill, area_id, step, score, situations, since, first_seen, "
        "history) VALUES(?,?,?,?,?,?,?,?)",
        ("light_on_entry", "kitchen", 2, 0.95, 30, at, at, "[]"),
    )
    await hm.note_day(0, "home_work", "said")
    return hm, clock, house.ha


def test_the_report_is_due_once_on_the_chosen_day() -> None:
    assert not due(SUNDAY, 6, None)  # before 19:30
    late = SUNDAY + timedelta(minutes=31)
    assert due(late, 6, None) and not due(late, 6, week_key(late.date()))
    assert not due(late, 0, None)  # this person chose Monday


async def test_each_level_gets_its_own_report(tmp_path: Path) -> None:
    hm, clock, _ = await start(tmp_path)
    data = await hm.week_data(clock.now())
    assert data["did"] == {"lighting": 5, "covers": 2, "climate": 1}
    assert data["undone"] == 1 and data["yes"] == 1 and data["days"] == 1
    assert hm.report_text(data, 1, "nl") == []  # Carefree: it just works
    short = hm.report_text(data, 2, "nl")
    assert short == ["Deze week deed ik 8 dingen zelf; 1 keer zette je iets terug."]
    involved = hm.report_text(data, 3, "nl")
    kitchen = hm.model.areas["kitchen"].name
    assert "Zelf gedaan: 8 keer (licht 5, gordijnen 2, verwarming 1)." in involved
    assert f"Geleerd: licht bij binnenkomen in de {kitchen} doe ik nu zelf." in involved
    assert "1 dag was anders dan normaal." in involved
    expert = hm.report_text(data, 4, "nl")
    assert len(expert) == len(involved) + 1 and expert[-1].startswith("Besluiten:")
    await hm.stop()


async def test_sunday_evening_the_report_arrives_once(tmp_path: Path) -> None:
    hm, clock, ha = await start(tmp_path)
    await clock.advance(31 * 60)
    hm._last_review = None
    await hm.tick()
    reports = [n for n in ha.notifications if n[1] == "Huisverslag van deze week"]
    assert {n[0] for n in reports} == {"notify.mobile_app_phone_1", "notify.mobile_app_phone_2"}
    assert "8 dingen" in reports[0][2]
    await clock.advance(20 * 60)
    hm._last_review = None
    await hm.tick()
    assert len([n for n in ha.notifications if n[1] == "Huisverslag van deze week"]) == 2
    viewer = await views.load_viewer(hm, "demo-user-2", None, trusted_local=False)
    viewer.level = 3  # Betrokken: the list of the report in the Logboek
    log = await logbook(hm, viewer, "demo-user-2", "nl")
    report = next(i for i in log["items"] if i["type"] == "report")
    assert report["week"] == week_key(SUNDAY.date())
    assert any(line.startswith("Zelf gedaan") for line in report["lines"])
    await hm.stop()
