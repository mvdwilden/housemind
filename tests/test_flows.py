"""Beslissingen in beeld (E-434 – E-439, design 52), from the user's side. Part 1: from
Geïnformeerd the newest thought on Huis is a flow of cause → effect, and from Betrokken the
Logboek tells saw → thought → did as a stream. Each test fails without the change."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from housemind.brain.proposals import Answer
from housemind.server.views import Viewer, snapshot
from tests.test_mind import _house, _mind


async def test_informed_sees_cause_and_effect(tmp_path: Path) -> None:
    """Ontzorgd sees none of it; Geïnformeerd sees what HouseMind saw and what it did or
    asked – the two blocks of the flow on Huis."""
    hm = await _house(tmp_path)
    one = await _mind(hm, 1)
    assert "cause" not in one[0] and "effect" not in one[0]
    two = await _mind(hm, 2)
    living = next(item for item in two if item["area"] == "Woonkamer")
    assert "iemand in Woonkamer" in str(living["cause"]) and living["effect"]
    asked = next(item for item in two if item["outcome"] == "proposed")
    assert asked["effect"] and not str(asked["effect"]).startswith("gevraagd:")  # the flow says it
    await hm.stop()


async def test_involved_keeps_the_whole_story(tmp_path: Path) -> None:
    """Betrokken gets what the stream in the Logboek shows: what it saw, thought and did."""
    hm = await _house(tmp_path)
    three = await _mind(hm, 3)
    living = next(item for item in three if item["area"] == "Woonkamer")
    assert living["saw"] and living["thought"] and living["did"] == living["effect"]
    await hm.stop()


async def _expert(hm: Any) -> dict[str, Any]:
    viewer = Viewer("demo-user-1", "Resident 1", 4, True, {"language": "nl"})
    return dict(await snapshot(hm, viewer))


async def test_expert_sees_each_decisions_checks_in_its_logboek_line(tmp_path: Path) -> None:
    """Expert: a decision in the Logboek folds open to the gate's six checks, in words –
    passed, where it stopped, or not reached. That was the decision trail in Brein; it, the
    stream of thoughts and the meter are gone (QA 3)."""
    hm = await _house(tmp_path)
    four = await _mind(hm, 4)
    checked = [item for item in four if item.get("checks")]
    assert checked and all(len(item["checks"]) == 6 for item in checked)
    assert [c["name"] for c in checked[0]["checks"]] == [
        "Geldig",
        "Mag",
        "Nu",
        "Stap",
        "Zinvol",
        "Gedaan",
    ]
    assert {c["state"] for i in checked for c in i["checks"]} <= {"ok", "stop", "none"}
    assert not any("1_valid" in line for i in checked for line in i.get("details", []))
    proposed = next(i for i in checked if i["outcome"] == "proposed")
    assert proposed["checks"][-1]["state"] == "none"  # asked, not done: the last not reached
    data = await _expert(hm)
    assert not {"trace", "dayFlow", "thoughts", "meter"} & set(data)
    assert "ms" in data["reaction"]  # one line in Over HouseMind instead of the meter
    await hm.stop()


async def _level(hm: Any, level: int) -> dict[str, Any]:
    viewer = Viewer("demo-user-1", "Resident 1", level, True, {"language": "nl"})
    return dict(await snapshot(hm, viewer))


async def test_carefree_sees_the_day_ring(tmp_path: Path) -> None:
    """Ontzorgd: around Vesta a dot for each thing HouseMind did today, at its time of day –
    nothing it only asked or left (E-435)."""
    hm = await _house(tmp_path)
    ring = (await _level(hm, 1))["dayRing"]
    done = await hm.db.fetchall("SELECT at FROM decisions WHERE outcome = 'executed'")
    assert ring and len(ring) == len(done) and all(0 <= at <= 1 for at in ring)
    assert "dayRooms" not in await _level(hm, 1)
    await hm.stop()


async def test_informed_sees_today_per_room(tmp_path: Path) -> None:
    """Geïnformeerd and Betrokken: the busiest rooms of today, each with a mark per thing
    HouseMind did, asked or left (E-436); Expert reads every decision in the Logboek."""
    hm = await _house(tmp_path)
    rooms = (await _level(hm, 2))["dayRooms"]
    assert rooms and len(rooms) <= 4
    names = {row["room"] for row in rooms}
    assert "Woonkamer" in names
    outcomes = {mark["outcome"] for row in rooms for mark in row["marks"]}
    assert "executed" in outcomes and "proposed" in outcomes
    assert "dayRooms" not in await _level(hm, 4)
    await hm.stop()


async def test_the_house_in_motion(tmp_path: Path) -> None:
    """From Betrokken what HouseMind did lights up as a path from its room to the device, with
    one line. The line flows for twenty seconds after it happens; the line under it stays as
    what it did last, all day (E-439)."""
    hm = await _house(tmp_path)
    assert "livePath" not in await _level(hm, 2)
    done = next(d for d in reversed(hm.status.thoughts) if d.outcome.value == "executed")
    hm.status.thoughts.append(replace(done, at=hm.clock.now()))  # what it did, just now
    live = (await _level(hm, 3))["livePath"]
    assert live["outcome"] == "executed" and live["area"] == "living"
    assert live["entity"].startswith("light.") and live["text"]
    assert live["flowFor"] == 20000  # it flows now, for twenty seconds
    await hm.clock.advance(15)
    assert (await _level(hm, 3))["livePath"]["flowFor"] == 5000  # still there after a glance
    await hm.clock.advance(6)
    still = (await _level(hm, 3))["livePath"]
    assert still["flowFor"] == 0 and still["text"] == live["text"]  # no longer flows, still said
    await hm.clock.advance(11 * 60)
    assert (await _level(hm, 3))["livePath"]["entity"] == live["entity"]  # the old ten minutes
    await hm.clock.advance(24 * 3600)
    assert (await _level(hm, 3))["livePath"] is None  # a new day: nothing done yet
    await hm.stop()


async def test_a_question_waits_as_its_own_line(tmp_path: Path) -> None:
    """A question that waits for an answer is no line that flows by: it carries its own room,
    device and time, for a dotted path for as long as it is open – also after ten minutes –
    and is gone once it is answered; the line under the network does not say it twice
    (E-439)."""
    hm = await _house(tmp_path)
    data = await _level(hm, 3)
    wait = data["proposals"][-1]
    asked = await hm.open_proposals()
    assert asked and wait["id"] == asked[-1].id
    assert wait["areaId"] == "kitchen" and wait["entity"].startswith("light.")
    assert wait["question"].endswith("?") and wait["at"].count(":") == 1
    assert data["livePath"]["outcome"] == "executed"  # the open question is not said twice
    await hm.clock.advance(10 * 60)
    assert (await _level(hm, 3))["proposals"][-1]["id"] == wait["id"]
    await hm.answer(wait["id"], Answer.NOT_NOW, "demo-user-1")
    assert (await _level(hm, 3))["proposals"] == []
    await hm.stop()


async def test_why_beside_the_moving_line(tmp_path: Path) -> None:
    """'Waarom?' beside the line under the network (design 'Het huis in beweging'): the path
    carries its decision, and Why? explains that very decision – the same room and device."""
    hm = await _house(tmp_path)
    live = (await _level(hm, 4))["livePath"]
    assert isinstance(live["id"], int)
    stored = await hm.stored_decision(live["id"])
    assert stored is not None and stored.request.entity_id == live["entity"]
    assert await hm.why_stored(live["id"], 4, "nl", plain=True)
    await hm.stop()
