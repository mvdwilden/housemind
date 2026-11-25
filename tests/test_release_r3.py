"""Release test R3 – daily life (E-144, fase 3): three weeks in the time machine.

The family house lives its standard rhythm (with the phone's alarm on workdays). The resident
(level Involved) answers what HouseMind asks, mostly yes. After three weeks:
- the rhythm of the house is learned (bedtime and wake-up time on workdays, E-88);
- HouseMind asked the evening before and then woke the house slowly (E-88);
- a house report was made every week and reached the resident (E-91);
- corrections stay below one a day per domain (S-01);
- every text people can see is real language, never a raw key (S-02).
"""

from __future__ import annotations

import random
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from housemind.brain.proposals import Answer
from housemind.core.clock import SimulatedClock
from housemind.demo.houses import load_demo_house
from housemind.demo.timemachine import standard_routine
from housemind.runtime import HouseMind, Paths
from housemind.server import views
from housemind.world.calendar import DayType

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Amsterdam")
MONDAY = datetime(2026, 10, 19, 0, 0, tzinfo=TZ)
WEEKS = 3
RAW_KEY = re.compile(
    r"\b(action|reason|explain|ask|gate|skills|steps|briefing|bedtime|report|rhythm|days|scenes)"
    r"\.[a-z_]+"
)


async def test_r3_three_weeks_of_daily_life(tmp_path: Path) -> None:
    clock = SimulatedClock(MONDAY.astimezone(UTC))
    house = load_demo_house("family_home", clock, "nl")
    hm = HouseMind(clock, house.ha, Paths([ROOT / "knowledge"], tmp_path), set(house.pet_areas))
    await hm.start(autorun=False)
    await hm.set_house_setting("consent_learning", True)
    await hm.db.execute(
        "INSERT INTO people(user_id, display_name, level, created) VALUES(?,?,?,?)",
        ("demo-user-1", "Resident", 3, clock.now().isoformat()),
    )
    await hm._refresh_people()
    routine = standard_routine(house, TZ)
    rng = random.Random(11)
    asked_wake = 0
    texts: list[str] = []
    for day in range(WEEKS * 7):
        date = (MONDAY + timedelta(days=day)).date()
        pending = sorted(routine(date, rng))
        until = (MONDAY + timedelta(days=day + 1)).astimezone(UTC)
        while clock.now() < until:
            next_tick = clock.now() + timedelta(minutes=5)
            while pending and pending[0].at <= next_tick:
                step = pending.pop(0)
                if step.at > clock.now():
                    await clock.advance((step.at - clock.now()).total_seconds())
                await house.ha.simulate(step.entity_id, step.state, step.attributes, by=step.by)
            if next_tick > clock.now():
                await clock.advance((next_tick - clock.now()).total_seconds())
            await hm.tick()
            for proposal in await hm.open_proposals():
                assert hm.explainer is not None
                if proposal.skill == "wake_light":
                    asked_wake += 1
                answer = Answer.DO if rng.random() < 0.9 else Answer.NOT_NOW
                await hm.answer(proposal.id, answer, "demo-user-1")
        local = clock.now().astimezone(TZ)
        if date.weekday() < 5 and local.time() == time(0, 0):
            viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
            snap = await views.snapshot(hm, viewer)
            texts += [snap.get("line") or "", snap.get("day") or ""]
            rhythm = snap.get("rhythm") or {}
            for day in rhythm.get("days", []):
                texts.append(day["label"])
                texts += [
                    str(m.get(k, ""))
                    for m in day["moments"]
                    for k in ("label", "value", "learning")
                ]
            texts += [str(o.get("value", o["label"])) for o in rhythm.get("other", [])]
            texts.append(rhythm.get("note") or "")
            texts += list((snap.get("report") or {}).get("lines") or [])

    # The rhythm of the house (E-88).
    wake = hm.rhythm.usual("wake", DayType.WORKDAY)
    bed = hm.rhythm.usual("bed", DayType.WORKDAY)
    assert wake is not None and time(6, 20) <= wake <= time(7, 40), wake
    assert bed is not None and time(22, 15) <= bed <= time(23, 59), bed

    # Asked the evening before, then woke the house slowly.
    assert asked_wake >= 3
    rows = await hm.db.fetchall(
        "SELECT at FROM decisions WHERE outcome = 'executed' AND entity_id = 'light.bedroom' "
        "AND source_layer = 'planner'"
    )
    mornings = {datetime.fromisoformat(r["at"]).astimezone(TZ).date() for r in rows}
    assert len(mornings) >= 3

    # Woken shortly before the usual time of that kind of day: on workdays around 6:45, on
    # free days around 8:45 – only after a yes the evening before.
    def in_window(at: datetime) -> bool:
        local = at.astimezone(TZ)
        workday = local.weekday() < 5
        early, late = (time(5, 30), time(7, 30)) if workday else (time(7, 30), time(9, 30))
        return early <= local.time() <= late

    assert all(in_window(datetime.fromisoformat(r["at"])) for r in rows), [
        r["at"] for r in rows if not in_window(datetime.fromisoformat(r["at"]))
    ]

    # A house report every week, and it reached the resident (E-91).
    reports = await hm.db.fetchall("SELECT week FROM reports")
    assert len(reports) >= WEEKS - 1
    assert any(n[1] == "Huisverslag van deze week" for n in house.ha.notifications), (
        "report not sent"
    )

    # S-01: fewer than one correction a day per domain.
    per_domain = await hm.corrections_per_domain(7)
    assert all(n / 7 < 1 for n in per_domain.values()), per_domain

    # S-02: real language everywhere people look.
    for row in await hm.db.fetchall("SELECT text_key, params FROM notifications"):
        import json

        line = hm.languages.text("nl", str(row["text_key"]), **json.loads(row["params"]))
        texts.append(line)
    texts += [n[2] for n in house.ha.notifications]
    for decision in list(hm.status.decisions)[-50:]:
        texts += hm.explain(decision, 4, "nl")
    leftovers = [t for t in texts if RAW_KEY.search(t)]
    assert not leftovers, leftovers[:5]
    await hm.stop()
