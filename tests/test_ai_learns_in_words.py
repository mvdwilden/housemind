"""Learning and explaining in words (E-415, E-418, design 50.3): with AI, what HouseMind learned
per room becomes a short story on Brein › Ritme, and the weekly report reads as plain words. The
AI only writes: the facts and every number come from the core (E-162), a text that changes a
number is not used, and without AI it all reads as now. Each test fails without the change;
what it checks is its docstring."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from housemind.ai.providers import AIResult
from housemind.knowledge.catalog import Step
from housemind.server import views
from housemind.server.logbook import logbook
from tests.test_ai import FakeAI, enable
from tests.test_ai import start as start_ai
from tests.test_report import start as start_report

LABEL = re.compile(r"\b[a-z][a-z_]*_\d+\b")


class Writer(FakeAI):
    """An AI that writes what it is asked for: the habits per room, or the weekly report."""

    def __init__(self, habits: Any = None, report: Any = None) -> None:
        super().__init__([])
        self.habits = habits
        self.report = report
        self.asked: dict[str, list[tuple[str, dict[str, Any]]]] = {"habits": [], "report": []}

    async def complete(self, system: str, user: str, model: str, max_tokens: int) -> AIResult:
        self.sent.append((system, user, model))
        data = json.loads(user.split("\n", 1)[1].rsplit("\n", 1)[0])
        for kind, mark, reply in (
            ("habits", "short story", self.habits),
            ("report", "weekly report", self.report),
        ):
            if mark in system:
                self.asked[kind].append((system, data))
                answer = reply(data) if callable(reply) else reply
                return AIResult(True, json.dumps(answer or {}), 900, 120)
        return AIResult(True, "{}", 10, 2)


async def _learned(tmp_path: Path) -> Any:
    hm, _clock, _ha = await start_ai(tmp_path)
    hm.activated, hm.gate.state.inactive = True, False
    assert hm.learning is not None
    hm.learning.steps[("light_on_entry", "hall")] = Step.AUTONOMOUS
    hm.learning.steps[("light_on_entry", "kitchen")] = Step.PROPOSE
    return hm


async def _quarter(hm: Any) -> None:
    hm._last_review = None
    await hm.tick()
    await hm.ai_jobs.idle()


def _habits(snap: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {h["room"]: h for h in snap["rhythm"]["habits"]}


async def _snap(hm: Any, level: int = 3) -> dict[str, Any]:
    viewer = await views.load_viewer(hm, "demo-user-1", None, trusted_local=False)
    viewer.level = level
    return await views.snapshot(hm, viewer)


async def test_without_ai_the_rhythm_shows_what_was_learned_per_room(tmp_path: Path) -> None:
    """Brein › Ritme had the rhythm of the day, but not what HouseMind learned per room. Now it
    has a block per room with the sentences HouseMind makes itself – without any AI."""
    hm = await _learned(tmp_path)
    habits = _habits(await _snap(hm))
    assert set(habits) == {"Gang", "Keuken"}  # only what it asks or does itself
    assert habits["Gang"]["ai"] is False and habits["Gang"]["text"] is None
    assert any("gang" in line for line in habits["Gang"]["lines"])
    await hm.stop()


async def test_with_ai_each_room_gets_a_short_story_once_a_day(tmp_path: Path) -> None:
    """With AI, once a day the AI turns those sentences into a short story per room. It gets
    neutral labels, never the names of the house, and the story comes back in plain names;
    the times and numbers stay those of the core."""
    hm = await _learned(tmp_path)

    def stories(data: dict[str, Any]) -> dict[str, Any]:
        rooms = data["rooms"]
        return {
            "rooms": {
                label: f"In {label} zorg ik voor het licht als het donker is: {nums}.".replace(
                    ": .", "."
                )
                for label, lines in rooms.items()
                # the standen of the core (E-494), kept as they are
                for nums in [
                    " en ".join(
                        re.findall(r"op (?:Gedimd|Normaal|Helder|Maximaal)", " ".join(lines))
                    )
                ]
            }
        }

    writer = Writer(habits=stories)
    await enable(hm, writer)
    await _quarter(hm)
    assert len(writer.asked["habits"]) == 1
    _system, data = writer.asked["habits"][0]
    sent = json.dumps(data, ensure_ascii=False)
    assert "Gang" not in sent and "Keuken" not in sent and "Emma" not in sent
    assert all(LABEL.fullmatch(label) for label in data["rooms"])
    habits = _habits(await _snap(hm))
    assert habits["Keuken"]["ai"] is True
    assert habits["Keuken"]["text"].startswith("In de keuken zorg ik")
    assert "op Normaal" in habits["Keuken"]["text"], habits["Keuken"]["text"]
    assert habits["Keuken"]["lines"]  # the sentences of the core stay there too
    await _quarter(hm)
    assert len(writer.asked["habits"]) == 1  # once a day
    await hm.stop()


async def test_a_story_that_changes_a_number_is_not_used(tmp_path: Path) -> None:
    """An AI that writes a time or a number the core never said – '7:15' where HouseMind
    learned nothing of the kind – is not believed: that room keeps the sentences of the core."""
    hm = await _learned(tmp_path)
    writer = Writer(
        habits=lambda data: {
            "rooms": {label: f"Om 7:15 gaat het licht in {label} aan." for label in data["rooms"]}
        }
    )
    await enable(hm, writer)
    await _quarter(hm)
    habits = _habits(await _snap(hm))
    assert writer.asked["habits"]
    assert habits["Keuken"]["ai"] is False and habits["Keuken"]["text"] is None
    await hm.stop()


async def test_the_weekly_report_in_plain_words(tmp_path: Path) -> None:
    """With AI the weekly report reads as plain words, with what stands out first; the numbers
    come from the core. It arrives a quarter of an hour later than without AI, and the Logbook
    says the words are the AI's."""
    hm, clock, ha = await start_report(tmp_path)

    def words(data: dict[str, Any]) -> dict[str, str]:
        source = " ".join(data["sentences"])
        did = re.search(r"Zelf gedaan: (\d+) keer", source)
        assert did is not None
        return {"text": f"Een rustige week: ik deed {did.group(1)} dingen voor jullie."}

    writer = Writer(report=words)
    await enable(hm, writer)
    await clock.advance(31 * 60)
    await _quarter(hm)
    assert writer.asked["report"]
    _system, data = writer.asked["report"][0]
    assert "Resident" not in json.dumps(data)
    await clock.advance(15 * 60)
    await _quarter(hm)
    reports = [n for n in ha.notifications if n[1] == "Huisverslag van deze week"]
    texts = {n[0]: n[2] for n in reports}
    assert texts["notify.mobile_app_phone_2"] == "Een rustige week: ik deed 8 dingen voor jullie."
    assert "8 dingen" in texts["notify.mobile_app_phone_1"]  # Informed: the short line of now
    viewer = await views.load_viewer(hm, "demo-user-2", None, trusted_local=False)
    log = await logbook(hm, viewer, "demo-user-2", "nl")
    report = next(i for i in log["items"] if i["type"] == "report")
    assert report["ai"] is True and report["lines"][0].startswith("Een rustige week")
    await hm.stop()


async def test_a_report_with_a_number_of_its_own_is_not_used(tmp_path: Path) -> None:
    """An AI that makes 8 into 9 is not believed: the report comes in the core's sentences."""
    hm, clock, ha = await start_report(tmp_path)
    writer = Writer(report={"text": "Ik deed deze week 9 dingen voor jullie."})
    await enable(hm, writer)
    await clock.advance(31 * 60)
    await _quarter(hm)
    await clock.advance(15 * 60)
    await _quarter(hm)
    reports = {n[0]: n[2] for n in ha.notifications if n[1] == "Huisverslag van deze week"}
    assert "9 dingen" not in reports["notify.mobile_app_phone_2"]
    assert "Zelf gedaan: 8 keer" in reports["notify.mobile_app_phone_2"]
    await hm.stop()
